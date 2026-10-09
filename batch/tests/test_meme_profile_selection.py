import uuid

from batch.utils.meme_profile_bundle import PATH_TEXT, PATH_VISION, Bundle
from batch.utils.meme_profile_selection import Candidate, failure_context, recorded_context, select_work
from repository.meme_profiles import ProfileMeta

LONG = "x" * 60
COMMON = dict(taxonomy_version=2, prompt_version="p2-now", text_model="text-model", vision_model="vision-model",
              min_text_chars=40, retry_failed=False)


def _candidate(text=LONG, fingerprint="fp-1"):
    return Candidate(uuid.uuid4(), "a.jpg", Bundle(None, (("k", text),) if text else (), None), fingerprint)


def _meta(candidate, **overrides):
    values = dict(taxonomy_version=2, extractor_model="text-model", extractor_prompt_version="p2-now",
                  inputs_fingerprint=candidate.fingerprint, human_verified=False)
    values.update(overrides)
    return ProfileMeta(**values)


def _select(candidates, metas=None, failed=None, **overrides):
    params = dict(COMMON)
    params.update(overrides)
    return select_work(candidates, metas or {}, failed or {}, **params)


def test_an_image_without_a_profile_is_selected_with_its_path_and_model():
    text_image, vision_image = _candidate(), _candidate(text="")

    result = _select([text_image, vision_image])

    assert [(w.image_id, w.path, w.model) for w in result.work] == [
        (text_image.image_id, PATH_TEXT, "text-model"), (vision_image.image_id, PATH_VISION, "vision-model")]
    assert result.stale_ids == frozenset()


def test_text_path_work_comes_before_vision_path_work_whatever_the_input_order():
    vision_first, text_second, vision_third, text_fourth = _candidate(text=""), _candidate(), _candidate(text=""), _candidate()

    result = _select([vision_first, text_second, vision_third, text_fourth])

    assert [w.image_id for w in result.work] == [
        text_second.image_id, text_fourth.image_id, vision_first.image_id, vision_third.image_id]


def test_a_current_profile_is_not_selected():
    candidate = _candidate()

    result = _select([candidate], {candidate.image_id: _meta(candidate)})

    assert result.work == [] and result.stale_ids == frozenset()


def test_each_staleness_rule_selects_the_image_and_marks_it_stale():
    cases = {
        "taxonomy version": dict(taxonomy_version=1),
        "prompt version": dict(extractor_prompt_version="p2-old"),
        "model": dict(extractor_model="old-model"),
        "fingerprint": dict(inputs_fingerprint="changed"),
    }
    for name, overrides in cases.items():
        candidate = _candidate()
        result = _select([candidate], {candidate.image_id: _meta(candidate, **overrides)})
        assert [w.image_id for w in result.work] == [candidate.image_id], name
        assert result.stale_ids == frozenset({candidate.image_id}), name


def test_the_expected_model_depends_on_the_path():
    vision_candidate = _candidate(text="")
    meta = _meta(vision_candidate, extractor_model="text-model")  # was extracted with the text model

    result = _select([vision_candidate], {vision_candidate.image_id: meta})

    assert [w.model for w in result.work] == ["vision-model"]


def test_a_stale_human_verified_profile_is_never_selected_and_needs_review():
    candidate = _candidate()
    meta = _meta(candidate, taxonomy_version=1, human_verified=True)

    result = _select([candidate], {candidate.image_id: meta})

    assert result.work == []
    assert result.stale_ids == frozenset({candidate.image_id})
    assert result.needs_review_ids == frozenset({candidate.image_id})


def test_a_current_human_verified_profile_is_neither_stale_nor_needing_review():
    candidate = _candidate()

    result = _select([candidate], {candidate.image_id: _meta(candidate, human_verified=True)})

    assert result.work == [] and result.stale_ids == frozenset() and result.needs_review_ids == frozenset()


def _failure(candidate, fingerprint=None, prompt_version="p2-now", model="text-model"):
    context = failure_context(fingerprint or candidate.fingerprint, prompt_version, model)
    return {candidate.image_id: f"{context} unusable answer"}


def test_a_failure_with_a_matching_context_is_skipped_unless_retrying():
    candidate = _candidate()
    failures = _failure(candidate)

    skipped = _select([candidate], failed=failures)
    retried = _select([candidate], failed=failures, retry_failed=True)

    assert skipped.work == [] and skipped.skipped_failed == 1
    assert [w.image_id for w in retried.work] == [candidate.image_id] and retried.skipped_failed == 0


def test_a_failure_recorded_in_a_different_context_is_selected_again():
    candidate = _candidate()
    cases = {
        "fingerprint": _failure(candidate, fingerprint="other-fingerprint-value"),
        "prompt version": _failure(candidate, prompt_version="p2-old"),
        "model": _failure(candidate, model="old-model"),
    }
    for name, failures in cases.items():
        result = _select([candidate], failed=failures)
        assert [w.image_id for w in result.work] == [candidate.image_id], name
        assert result.skipped_failed == 0, name


def test_a_legacy_failure_message_without_a_context_is_selected_again():
    candidate = _candidate()

    for message in ("unusable answer", None):
        result = _select([candidate], failed={candidate.image_id: message})
        assert [w.image_id for w in result.work] == [candidate.image_id]


def test_failure_context_format():
    assert failure_context("0123456789abcdef0123", "p2-now", "m") == "[0123456789abcdef|p2-now|m]"


def test_recorded_context_edge_cases():
    assert recorded_context(None) is None
    assert recorded_context("no bracket [x]") is None
    assert recorded_context("[never closed") is None
    assert recorded_context("[a|b|c] some [other] text") == "[a|b|c]"


def test_a_human_verified_profile_is_not_stale_when_only_prompt_model_or_inputs_changed():
    candidate = _candidate()
    for override in (dict(extractor_prompt_version="p2-old"), dict(extractor_model="human"),
                     dict(inputs_fingerprint="other")):
        result = _select([candidate], {candidate.image_id: _meta(candidate, human_verified=True, **override)})
        assert result.work == [] and result.stale_ids == frozenset() and result.needs_review_ids == frozenset(), override


def test_a_human_verified_profile_from_an_older_taxonomy_needs_review_and_is_never_selected():
    candidate = _candidate()
    result = _select([candidate], {candidate.image_id: _meta(candidate, human_verified=True, taxonomy_version=1,
                                                              extractor_model="human", inputs_fingerprint="human")})
    assert result.work == []
    assert result.stale_ids == frozenset({candidate.image_id})
    assert result.needs_review_ids == frozenset({candidate.image_id})
