"""Which images need a (new) meme profile, and by which path (P2 spec, decision 4). Pure functions."""
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from batch.utils.meme_profile_bundle import PATH_TEXT, PATH_VISION, Bundle, choose_path
from repository.meme_profiles import ProfileMeta


def failure_context(fingerprint: str, prompt_version: str, model: str) -> str:
    """The context a failure is recorded in; stored as a prefix of the failure message."""
    return f"[{fingerprint[:16]}|{prompt_version}|{model}]"


def recorded_context(message: str | None) -> str | None:
    """The leading bracket group of a stored failure message, or None (no message, legacy format)."""
    if message is None or not message.startswith("["):
        return None
    end = message.find("]")
    if end == -1:
        return None
    return message[:end + 1]


@dataclass(frozen=True)
class Candidate:
    image_id: uuid.UUID
    filename: str
    bundle: Bundle
    fingerprint: str


@dataclass(frozen=True)
class WorkItem:
    image_id: uuid.UUID
    filename: str
    bundle: Bundle
    fingerprint: str
    path: str
    model: str


@dataclass(frozen=True)
class SelectionResult:
    work: list[WorkItem]
    stale_ids: frozenset
    needs_review_ids: frozenset
    skipped_failed: int


def select_work(candidates: list[Candidate], metas: dict[uuid.UUID, ProfileMeta],
                failures: Mapping[uuid.UUID, str | None],
                *, taxonomy_version: int, prompt_version: str, text_model: str, vision_model: str,
                min_text_chars: int, retry_failed: bool) -> SelectionResult:
    """An image is selected when it has no profile or its profile is stale (taxonomy version, prompt version,
    the model that would now run for its path, or the input fingerprint differs). Human-verified profiles are
    never selected: when stale they are reported as needing review, and a human-verified profile is stale only when
    its taxonomy version differs. A failed image is skipped only while the context recorded with its failure
    (fingerprint, prompt version, model) equals the current one, unless retry_failed. Text-path work is ordered before vision-path work so Ollama swaps models at most once."""
    text_work: list[WorkItem] = []
    vision_work: list[WorkItem] = []
    stale: set = set()
    needs_review: set = set()
    skipped_failed = 0

    for candidate in candidates:
        path = choose_path(candidate.bundle, min_text_chars)
        model = text_model if path == PATH_TEXT else vision_model
        meta = metas.get(candidate.image_id)
        if meta is not None:
            if meta.human_verified:
                # A person's correction does not depend on the prompt, the model or the extraction inputs:
                # it only goes stale when the vocabulary it was made against changes (P5c spec, decision 2).
                is_stale = meta.taxonomy_version != taxonomy_version
            else:
                is_stale = (
                    meta.taxonomy_version != taxonomy_version
                    or meta.extractor_prompt_version != prompt_version
                    or meta.extractor_model != model
                    or meta.inputs_fingerprint != candidate.fingerprint
                )
            if not is_stale:
                continue
            stale.add(candidate.image_id)
            if meta.human_verified:
                needs_review.add(candidate.image_id)
                continue
        if candidate.image_id in failures and not retry_failed:
            current = failure_context(candidate.fingerprint, prompt_version, model)
            if recorded_context(failures[candidate.image_id]) == current:
                skipped_failed += 1
                continue
        item = WorkItem(candidate.image_id, candidate.filename, candidate.bundle, candidate.fingerprint, path, model)
        (text_work if path == PATH_TEXT else vision_work).append(item)

    return SelectionResult(text_work + vision_work, frozenset(stale), frozenset(needs_review), skipped_failed)
