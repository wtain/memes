import pytest

from batch.utils.meme_profile_bundle import Bundle
from evaluation.corpus import CorpusCandidate, quotas_for, select_corpus, stratum_of, write_ids_file
from evaluation.gold import STRATA


def _candidate(index, stratum, cluster=None, content_hash="h"):
    image_id = f"{index:08d}-0000-0000-0000-000000000000"
    return CorpusCandidate(image_id, content_hash, stratum, cluster or f"s{image_id}")


def test_quotas_follow_the_shares_and_always_sum_to_the_size():
    assert quotas_for(1500) == {"ocr+description": 900, "ocr only": 300, "description only": 150, "no usable text": 150}
    for size in (10, 99, 1234):
        assert sum(quotas_for(size).values()) == size
    assert list(quotas_for(100)) == list(STRATA)


def test_stratum_of_a_bundle():
    long_text = "x" * 60
    assert stratum_of(Bundle(None, (("k", long_text),), long_text), 40) == "ocr+description"
    assert stratum_of(Bundle(None, (), long_text), 40) == "ocr only"
    assert stratum_of(Bundle(None, (("k", long_text),), None), 40) == "description only"
    assert stratum_of(Bundle(None, (), "short"), 40) == "no usable text"
    assert stratum_of(Bundle("a note " * 10, (), None), 40) == "description only"


def test_selection_takes_the_quota_per_stratum_deterministically():
    candidates = ([_candidate(i, "ocr+description") for i in range(100)]
                  + [_candidate(1000 + i, "ocr only") for i in range(50)]
                  + [_candidate(2000 + i, "description only") for i in range(20)]
                  + [_candidate(3000 + i, "no usable text") for i in range(20)])
    quotas = {"ocr+description": 12, "ocr only": 4, "description only": 2, "no usable text": 2}

    first = select_corpus(candidates, quotas, seed=7)
    again = select_corpus(candidates, quotas, seed=7)
    other = select_corpus(candidates, quotas, seed=8)

    assert first == again and first != other
    counts = {s: sum(1 for image in first if image.stratum == s) for s in STRATA}
    assert counts == quotas
    ids = [image.image_id for image in first]
    assert len(ids) == len(set(ids))


def test_shortfall_in_a_small_stratum_is_redistributed_to_ocr_and_description():
    candidates = ([_candidate(i, "ocr+description") for i in range(50)]
                  + [_candidate(1000 + i, "no usable text") for i in range(3)])
    quotas = {"ocr+description": 10, "ocr only": 0, "description only": 0, "no usable text": 6}

    selected = select_corpus(candidates, quotas, seed=1)

    counts = {s: sum(1 for image in selected if image.stratum == s) for s in STRATA}
    assert counts["no usable text"] == 3 and counts["ocr+description"] == 13 and len(selected) == 16


def test_only_the_lowest_image_id_of_a_duplicate_cluster_is_eligible():
    a, b, c = _candidate(1, "ocr only", cluster="c1"), _candidate(2, "ocr only", cluster="c1"), _candidate(3, "ocr only")
    quotas = {"ocr+description": 0, "ocr only": 10, "description only": 0, "no usable text": 0}

    selected = select_corpus([b, a, c], quotas, seed=1)

    assert sorted(image.image_id for image in selected) == sorted([a.image_id, c.image_id])


def test_content_hash_and_stratum_are_carried_into_the_corpus_images():
    selected = select_corpus([_candidate(1, "ocr only", content_hash="abc")],
                             {"ocr+description": 0, "ocr only": 5, "description only": 0, "no usable text": 0}, seed=1)

    assert selected[0].content_hash == "abc" and selected[0].stratum == "ocr only"


def test_write_ids_file_writes_one_uuid_per_line(tmp_path):
    path = tmp_path / "corpus.ids"

    write_ids_file(path, ["b", "a"])

    assert path.read_text(encoding="utf-8") == "a\nb\n"
