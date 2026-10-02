"""Which images need a (new) meme profile, and by which path (P2 spec, decision 4). Pure functions."""
import uuid
from dataclasses import dataclass

from batch.utils.meme_profile_bundle import PATH_TEXT, PATH_VISION, Bundle, choose_path
from repository.meme_profiles import ProfileMeta


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


def select_work(candidates: list[Candidate], metas: dict[uuid.UUID, ProfileMeta], failed_ids,
                *, taxonomy_version: int, prompt_version: str, text_model: str, vision_model: str,
                min_text_chars: int, retry_failed: bool) -> SelectionResult:
    """An image is selected when it has no profile or its profile is stale (taxonomy version, prompt version,
    the model that would now run for its path, or the input fingerprint differs). Human-verified profiles are
    never selected: when stale they are reported as needing review. Failed images are skipped unless
    retry_failed. Text-path work is ordered before vision-path work so Ollama swaps models at most once."""
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
        if candidate.image_id in failed_ids and not retry_failed:
            skipped_failed += 1
            continue
        item = WorkItem(candidate.image_id, candidate.filename, candidate.bundle, candidate.fingerprint, path, model)
        (text_work if path == PATH_TEXT else vision_work).append(item)

    return SelectionResult(text_work + vision_work, frozenset(stale), frozenset(needs_review), skipped_failed)
