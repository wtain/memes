"""
Optional ingestion step between Tier B discovery and Tier B review: settle ("auto-keep") Tier B
candidate pairs whose OCR text is clearly different, so the reviewer only sees plausible duplicates.

    python -m batch.ingest_text_filter --env general                       # dry run: report only
    python -m batch.ingest_text_filter --env general --apply --plan-hash H # apply what the dry run showed
    python -m batch.ingest_text_filter --env general --undo --run-id R     # reopen a run's pairs

`main()` (what /admin/batches calls) is always the dry run. Apply and undo are shell only.
Rule, constants and rationale: docs/superpowers/specs/2026-11-26-ingestion-tier-b-text-filter-design.md.
Only the judged pair is settled (no image is rejected or moved); settled pairs carry the run id in
tmp_duplicates.tier_b_auto_kept_run, so a run can be undone.
"""
import argparse
import asyncio
import hashlib
import json
import random
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from batch.clusterize import PROXIMITY_THRESHOLD as TIER_A_THRESHOLD
from batch.run_tracking import finish_existing_run, record_stats, tracked_run
from batch.utils.run_progress import report_progress
from Backend.app.repositories.ingestion_repository import IngestionRepository
from config.settings import load_env, settings
from repository import ingestion_text_filter as repo
from repository.batch_runs import BatchRunRepository
from repository.ingestion_text_filter import ELIGIBLE, FAR_WEAK_TEXT, SETTLED_BUCKETS, FilterParams, PlannedPair
from Storage.db import AsyncSessionLocal
from Storage.models import Image

KIND = "ingest_text_filter"
REPORT_ROOT = Path(__file__).resolve().parents[1] / "Storage" / "ingestion-text-filter"
_OCR_SNIPPET_CHARS = 300
_NEAR_MISS_OVERLAP = (0.2, 0.3)   # the overlap band just above the line, sampled to show where it falls


class PlanMismatch(Exception):
    """The open pair set (or the parameters) differ from what the dry run reported."""


@dataclass(frozen=True)
class Plan:
    batch_id: uuid.UUID
    params: FilterParams
    pairs: list[PlannedPair]
    eligible_ids: list[uuid.UUID]
    hash: str

    def counts(self) -> dict[str, int]:
        out = {b: 0 for b in repo.BUCKETS}
        for p in self.pairs:
            out[p.bucket] += 1
        return out


def plan_hash(pair_ids: list[uuid.UUID], params: FilterParams) -> str:
    """sha256 of the sorted eligible pair ids and the three rule values."""
    h = hashlib.sha256()
    h.update(f"{params.min_lemmas}|{params.max_overlap}|{params.veto_distance}"
             f"|{params.far_min_distance}|{params.far_max_overlap}".encode())
    for pair_id in sorted(str(i) for i in pair_ids):
        h.update(b"|" + pair_id.encode())
    return h.hexdigest()


def _params() -> FilterParams:
    cfg = settings.INGESTION_TEXT_FILTER
    return FilterParams(
        min_lemmas=cfg.MIN_LEMMAS, max_overlap=cfg.MAX_OVERLAP, veto_distance=cfg.VETO_DISTANCE,
        far_min_distance=cfg.FAR_MIN_DISTANCE, far_max_overlap=cfg.FAR_MAX_OVERLAP)


async def build_plan(session) -> Plan:
    """Classify every open Tier B pair of the active ingestion run. Read only."""
    active = await BatchRunRepository(session).get_active_run(kind="ingestion")
    if active is None:
        raise RuntimeError("No ingestion run is currently in progress -- nothing to filter.")
    params = _params()
    band = (TIER_A_THRESHOLD, settings.DUPLICATES.THRESHOLD)
    pairs = await repo.plan_pairs(session, active.run_id, band, params)
    eligible_ids = [p.pair_id for p in pairs if p.bucket in SETTLED_BUCKETS]
    report_progress(KIND, "pairs", len(pairs), len(pairs))
    return Plan(active.run_id, params, pairs, eligible_ids, plan_hash(eligible_ids, params))


async def _describe(session, pairs: list[PlannedPair]) -> dict:
    """filename and OCR snippet per image id, for the sample sheet only."""
    ids = {i for p in pairs for i in (p.image_id1, p.image_id2)}
    if not ids:
        return {}
    names = dict((await session.execute(select(Image.id, Image.filename).where(Image.id.in_(ids)))).all())
    texts = await IngestionRepository(session).get_ocr_texts(
        ids, settings.OCR.CONFIDENCE_MIN, settings.OCR.LANG_SCORE_MIN)
    return {i: (names.get(i, "?"), (texts.get(i) or "")[:_OCR_SNIPPET_CHARS].replace("\n", " ")) for i in ids}


def _fmt(value, spec=".3f") -> str:
    return "n/a" if value is None else format(value, spec)


def _sheet(title: str, pairs: list[PlannedPair], info: dict) -> str:
    lines = [f"## {title} ({len(pairs)})", ""]
    for n, p in enumerate(pairs, 1):
        (fa, ta), (fb, tb) = info[p.image_id1], info[p.image_id2]
        lines += [
            f"### {n}. clip {p.distance:.3f} · overlap {_fmt(p.overlap)} · text distance {_fmt(p.text_distance)}",
            f"- {fa}: {ta}", f"- {fb}: {tb}", "",
        ]
    return "\n".join(lines)


async def write_dry_run(session, run_id: uuid.UUID, plan: Plan, out_dir: Path) -> dict:
    """Write report.json and sample.md under out_dir; returns the run stats (counts only, no text)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = plan.counts()
    rng = random.Random(plan.hash)   # reproducible sample for one plan
    size = settings.INGESTION_TEXT_FILTER.SAMPLE_SIZE
    eligible = [p for p in plan.pairs if p.bucket == ELIGIBLE]
    far = [p for p in plan.pairs if p.bucket == FAR_WEAK_TEXT]
    near = [p for p in plan.pairs if p.bucket == "overlap_high"
            and p.overlap is not None and _NEAR_MISS_OVERLAP[0] <= p.overlap < _NEAR_MISS_OVERLAP[1]]
    picked_eligible = rng.sample(eligible, min(size, len(eligible)))
    picked_near = rng.sample(near, min(size, len(near)))
    picked_far = rng.sample(far, min(size, len(far)))
    info = await _describe(session, picked_eligible + picked_far + picked_near)

    report = {
        "run_id": str(run_id), "ingestion_batch_id": str(plan.batch_id), "plan_hash": plan.hash,
        "params": {"min_lemmas": plan.params.min_lemmas, "max_overlap": plan.params.max_overlap,
                   "veto_distance": plan.params.veto_distance,
                   "far_min_distance": plan.params.far_min_distance, "far_max_overlap": plan.params.far_max_overlap},
        "counts": counts,
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    sheet = "\n".join([
        "# Ingestion text filter: dry run sample (contains OCR text; keep local)", "",
        _sheet("Would be auto-kept (random sample)", picked_eligible, info),
        _sheet("Far band: would be auto-kept although the text is weak or missing (random sample)", picked_far, info),
        _sheet("Stay for review: overlap 0.2 to 0.3 (random sample, shows where the line falls)", picked_near, info),
    ])
    (out_dir / "sample.md").write_text(sheet, encoding="utf-8")
    return {
        "pairs_open": len(plan.pairs), "pairs_eligible": counts["eligible"], "pairs_vetoed": counts["vetoed"],
        "pairs_no_text": counts["no_text"], "pairs_far_weak_text": counts[FAR_WEAK_TEXT], "pairs_applied": 0,
    }


async def apply_plan(session, run_id: uuid.UUID, expected_hash: str) -> int:
    """Recompute the plan and apply it only if it hashes to what the dry run reported. Never commits."""
    plan = await build_plan(session)
    if plan.hash != expected_hash:
        raise PlanMismatch(
            "The open Tier B pairs (or the rule values) changed since the dry run: "
            f"expected plan hash {expected_hash}, now {plan.hash}. Run a new dry run and read its report.")
    return await repo.apply_pairs(session, run_id, plan.eligible_ids)


async def undo(session, undo_run_id: uuid.UUID) -> int:
    return await repo.undo_run(session, undo_run_id)


async def _dry_run(run_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as session:
        plan = await build_plan(session)
        stats = await write_dry_run(session, run_id, plan, REPORT_ROOT / str(run_id))
    await record_stats(run_id, stats)
    print(f"Dry run {run_id}: {stats['pairs_eligible'] + stats['pairs_far_weak_text']} of {stats['pairs_open']} "
          f"open pairs would be auto-kept ({stats['pairs_far_weak_text']} of them in the far band). "
          f"Plan hash: {plan.hash}. Report: {REPORT_ROOT / str(run_id)}")


async def _apply(run_id: uuid.UUID, expected_hash: str) -> None:
    async with AsyncSessionLocal() as session:
        applied = await apply_plan(session, run_id, expected_hash)
        await session.commit()
    await record_stats(run_id, {"pairs_applied": applied})
    print(f"Run {run_id}: auto-kept {applied} pair(s). Undo with --undo --run-id {run_id}")


async def _undo(undo_run_id: uuid.UUID, run_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as session:
        reopened = await undo(session, undo_run_id)
        await session.commit()
    await record_stats(run_id, {"pairs_reopened": reopened})
    print(f"Reopened {reopened} pair(s) auto-kept by run {undo_run_id}")


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    """The admin-trigger entry: always a dry run (the contract passes no parameters)."""
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _dry_run(run_id)
    else:
        async with tracked_run(kind=KIND, trigger=trigger) as new_run_id:
            await _dry_run(new_run_id)


async def _cli(args) -> None:
    if args.apply:
        async with tracked_run(kind=KIND, trigger="manual") as run_id:
            await _apply(run_id, args.plan_hash)
    elif args.undo:
        async with tracked_run(kind=KIND, trigger="manual") as run_id:
            await _undo(args.run_id, run_id)
    else:
        await main()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="apply the plan a dry run reported (needs --plan-hash)")
    mode.add_argument("--undo", action="store_true", help="reopen the pairs a run auto-kept (needs --run-id)")
    parser.add_argument("--plan-hash", help="the hash printed by the dry run")
    parser.add_argument("--run-id", type=uuid.UUID, help="the run whose pairs --undo reopens")
    parsed = parser.parse_args()
    if parsed.apply and not parsed.plan_hash:
        parser.error("--apply needs --plan-hash")
    if parsed.undo and not parsed.run_id:
        parser.error("--undo needs --run-id")
    load_env(parsed.env)
    try:
        asyncio.run(_cli(parsed))
    except PlanMismatch as e:
        raise SystemExit(f"REFUSED: {e}")
