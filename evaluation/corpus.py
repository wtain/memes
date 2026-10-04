"""The frozen evaluation corpus (P6 spec, decision 3)."""
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from sqlalchemy import select

from batch.utils.image_format_filter import has_unsupported_image_extension
from batch.utils.meme_profile_bundle import PATH_VISION, build_bundle, choose_path
from evaluation.gold import STRATA, CorpusImage
from repository.meme_profile_inputs import MemeProfileInputsRepository
from Storage.models import ImageExtras, TmpImageClusters

SHARES = (("ocr+description", 0.6), ("ocr only", 0.2), ("description only", 0.1), ("no usable text", 0.1))
CHUNK = 1000


@dataclass(frozen=True)
class CorpusCandidate:
    image_id: str
    content_hash: str | None
    stratum: str
    cluster_key: str


def quotas_for(size: int) -> dict[str, int]:
    quotas = {stratum: int(size * share) for stratum, share in SHARES}
    quotas["ocr+description"] += size - sum(quotas.values())
    return quotas


def stratum_of(bundle, min_text_chars: int) -> str:
    if choose_path(bundle, min_text_chars) == PATH_VISION:
        return "no usable text"
    has_description = bool(bundle.note or bundle.descriptions)
    if has_description and bundle.ocr:
        return "ocr+description"
    return "description only" if has_description else "ocr only"


def select_corpus(candidates: Sequence[CorpusCandidate], quotas: Mapping[str, int], seed: int) -> list[CorpusImage]:
    """One image per duplicate cluster (the lowest image id), then a seeded draw per stratum; a stratum that
    cannot fill its quota hands the shortfall to ocr+description."""
    representative: dict[str, CorpusCandidate] = {}
    for candidate in candidates:
        current = representative.get(candidate.cluster_key)
        if current is None or candidate.image_id < current.image_id:
            representative[candidate.cluster_key] = candidate
    pools: dict[str, list[CorpusCandidate]] = {stratum: [] for stratum in STRATA}
    for candidate in representative.values():
        pools[candidate.stratum].append(candidate)
    for pool in pools.values():
        pool.sort(key=lambda c: c.image_id)

    rng = random.Random(seed)
    chosen: dict[str, list[CorpusCandidate]] = {}
    shortfall = 0
    for stratum in STRATA:
        quota = quotas.get(stratum, 0)
        pick = rng.sample(pools[stratum], min(quota, len(pools[stratum])))
        shortfall += quota - len(pick)
        chosen[stratum] = pick
    if shortfall:
        taken = {c.image_id for c in chosen["ocr+description"]}
        rest = [c for c in pools["ocr+description"] if c.image_id not in taken]
        chosen["ocr+description"] += rng.sample(rest, min(shortfall, len(rest)))

    return [CorpusImage(c.image_id, c.content_hash, c.stratum)
            for stratum in STRATA for c in sorted(chosen[stratum], key=lambda c: c.image_id)]


async def load_candidates(session, config) -> list[CorpusCandidate]:
    """Eligible active images with their stratum and duplicate-cluster key (read-only)."""
    inputs = MemeProfileInputsRepository(session)
    flagged = {image_id for (image_id,) in (await session.execute(
        select(ImageExtras.image_id).where(ImageExtras.flagged.is_(True)))).all()}
    clusters = {image_id: cluster_id for image_id, cluster_id in (await session.execute(
        select(TmpImageClusters.image_id, TmpImageClusters.cluster_id))).all()}
    images = [image for image in await inputs.list_active_images()
              if image[0] not in flagged and not has_unsupported_image_extension(image[1])]

    candidates: list[CorpusCandidate] = []
    for start in range(0, len(images), CHUNK):
        chunk = images[start:start + CHUNK]
        raw = await inputs.load_inputs([image_id for image_id, _, _ in chunk])
        for image_id, _, content_hash in chunk:
            item = raw[image_id]
            bundle = build_bundle(item.note, item.descriptions, item.ocr_rows,
                                  ocr_confidence_min=config.ocr_confidence_min,
                                  ocr_lang_score_min=config.ocr_lang_score_min, limits=config.limits)
            cluster = clusters.get(image_id)
            candidates.append(CorpusCandidate(
                str(image_id), content_hash, stratum_of(bundle, config.min_text_chars),
                f"c{cluster}" if cluster is not None else f"s{image_id}"))
    return candidates


def write_ids_file(path: Path, image_ids: Sequence[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(f"{image_id}\n" for image_id in sorted(image_ids)))
