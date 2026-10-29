# R7 Operations and Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `recut_pipeline` driver job, a reproducible precision report on the labelled sample, a live approve-rate-by-band endpoint and table on the review page, the runbook, and the recorded scheduling decision.

**Architecture:** `recut_pipeline` reuses `Step`/`run_steps` from `description_pipeline` and calls three children in process (`create_recut_cuts`, `build_image_embeddings` for `cut_proposed`, `refresh_recut_cuts`). `evaluation/recut_report.py` is a pure module over the gold files, wired into the evaluation CLI as `recut-report`. `GET /api/recut/stats` is one grouped query in `RecutRepository.band_stats`; the review page shows it through a small hook and a `ReviewStats` component.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async, pytest (unit with mocked children, integration against `ocrdb_test`), shared JSON schemas with generated TS/Kotlin/Python types, React + Vite + vitest + eslint (React Compiler rules).

**Spec:** `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-29-r7-operations-and-evaluation-design.md`

## Global Constraints

- `recut_pipeline` steps, in order and with no `needs`: `cuts` = `create_recut_cuts.main(trigger=t, limit=settings.get("recut.max_cuts_per_run"))`; `cut_embeddings` = `build_image_embeddings.main(trigger=t, incremental=True, target_status="cut_proposed")` (the parameter is `target_status`, not `status`); `refresh` = `refresh_recut_cuts.main(trigger=t)`. Kind and registry name `recut_pipeline`, module `batch.recut_pipeline`, manual-trigger only, not in `scheduler.jobs`. The driver raises when any step failed.
- Bands (identical in the report and the endpoint): `0.6-0.7` (everything below 0.7; the detector's floor is 0.6), `0.7-0.8`, `0.8-0.9`, `0.9-1.0`, `1.0` (confidence >= 1.0), and `unscored` (null confidence). Lower edge inclusive, upper exclusive, except `1.0`.
- The report counts only the shipped detector's proposals: round 1 items whose key `by` contains `A` or `C` (band `1.0`), round 2 items whose key `band` starts with `margin` (banded by their `confidence`). Round 2 `inner` items, skipped (`s`) items and items without a judgment are excluded and counted. Grade `2` = as is, `1` = slightly off, `0` = wrong. Recall is printed as "not measurable".
- Stats endpoint: `GET /api/recut/stats`, rows in state `proposed`/`approved`/`rejected`, grouped by band; per band `label`, `lower`, `upper` (null for `unscored`), `waiting` (= proposed), `approved`, `rejected`, `approveRate` (`approved / (approved + rejected)` or null); `total` is the same shape with label `total`; `bands` lists the six bands in a fixed order, including empty ones. No migration, no change to `Storage/models.py`.
- Frontend: types from the generated `all.d.ts`; none hand-written. `eslint src/` 0 warnings; StrictMode and React Compiler rules (effects fetch with a cancel guard). A failed stats request hides the table and never blocks reviewing.
- `evaluation/recut_report.py` is a pure module (stdlib only); it joins the `PURE` list in `tests/evaluation/test_pure_imports.py`.
- Test roots run separately: `Backend/` from its own directory; `tests/integration/` with `DATABASE_URL=postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test`; `batch/tests/`, `tests/evaluation/`, `tests/rules/`, `tests/docs/` each on their own. Python is `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe` (the venv lives in the main checkout). The shared test database can produce random failures when another process uses it at the same time; a failing file that passes in isolation is rerun, not "fixed".
- Many repo files use CRLF; preserve line endings (use the Edit tool for existing files). Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Never run a migration or a batch against a live metal/general/IT database; R7 runs nothing live.

## Review Focus

- One failing step does not stop the others and the driver still fails; a busy child is `skipped_busy`. Pinned in Task 1.
- Band edges: confidence exactly 0.6, 0.7, 0.8, 0.9 and 1.0 land in the band whose lower edge they are; null confidence is `unscored`; `detected`/`failed` rows are not counted. Pinned in Task 3.
- `approveRate` is null while nothing is decided, in the band and in the total. Pinned in Tasks 3 and 4.
- The report excludes inner-rectangle, skipped and ungraded items, says recall is not measurable, and its real-file totals are pinned (124 graded, 118 / 5 / 1). Pinned in Task 2.
- A failing stats request leaves reviewing working; stats refresh after each decision. Pinned in Task 4.

---

### Task 1: The `recut_pipeline` driver

**Files:**
- Create: `batch/recut_pipeline.py`
- Modify: `environments/batch_registry.yaml`, `environments/settings.yaml`, `docs/batches/inventory.md`, `CLAUDE.md`
- Test: `batch/tests/test_recut_pipeline.py`

**Interfaces:**
- Consumes: `batch.description_pipeline` `Step`, `run_steps(steps, trigger) -> dict[str, dict]`, `FAILED`, `OK`, `SKIPPED_BUSY`; `batch.create_recut_cuts.main(trigger, run_id, limit, retry_failed)`; `batch.build_image_embeddings.main(trigger, run_id, incremental, target_status)`; `batch.refresh_recut_cuts.main(trigger, run_id)`; `batch.run_tracking` `tracked_run`, `finish_existing_run`, `record_stats`.
- Produces: `batch.recut_pipeline.STEPS`, `async main(trigger="manual", run_id=None)`, kind `recut_pipeline`.

- [ ] **Step 1: Write the failing tests**

`batch/tests/test_recut_pipeline.py`:

```python
"""
Unit tests for batch/recut_pipeline.py -- the recut pipeline driver. No real DB; every child job's main() is mocked,
matching batch/tests/test_refresh_recut_cuts.py.
"""
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

import batch.recut_pipeline as module
from batch.description_pipeline import FAILED, OK, SKIPPED_BUSY, run_steps
from batch.registry import BatchRegistry
from repository.batch_runs import BatchAlreadyRunningError

CHILDREN = ["create_recut_cuts", "build_image_embeddings", "refresh_recut_cuts"]


def _patched(mocks, settings_value=None):
    stack = ExitStack()
    for name, mock in mocks.items():
        stack.enter_context(patch.object(getattr(module, name), "main", mock))
    stack.enter_context(patch.object(module.settings, "get", return_value=settings_value))
    return stack


def test_order_and_independence_match_the_design():
    assert [(s.name, s.needs) for s in module.STEPS] == [("cuts", ()), ("cut_embeddings", ()), ("refresh", ())]


@pytest.mark.asyncio
async def test_steps_call_the_children_with_the_documented_arguments():
    mocks = {name: AsyncMock() for name in CHILDREN}
    with _patched(mocks, settings_value=250):
        results = await run_steps(module.STEPS, trigger="manual")

    mocks["create_recut_cuts"].assert_awaited_once_with(trigger="manual", limit=250)
    mocks["build_image_embeddings"].assert_awaited_once_with(
        trigger="manual", incremental=True, target_status="cut_proposed")
    mocks["refresh_recut_cuts"].assert_awaited_once_with(trigger="manual")
    assert {name: r["status"] for name, r in results.items()} == {"cuts": OK, "cut_embeddings": OK, "refresh": OK}


@pytest.mark.asyncio
async def test_an_unset_cut_cap_means_no_limit():
    mocks = {name: AsyncMock() for name in CHILDREN}
    with _patched(mocks, settings_value=None):
        await run_steps(module.STEPS, trigger="manual")

    mocks["create_recut_cuts"].assert_awaited_once_with(trigger="manual", limit=None)


@pytest.mark.asyncio
async def test_a_failed_step_does_not_stop_the_others():
    mocks = {name: AsyncMock() for name in CHILDREN}
    mocks["create_recut_cuts"].side_effect = RuntimeError("20 failures in a row")
    with _patched(mocks):
        results = await run_steps(module.STEPS, trigger="manual")

    assert results["cuts"]["status"] == FAILED
    assert results["cut_embeddings"]["status"] == OK and results["refresh"]["status"] == OK
    mocks["build_image_embeddings"].assert_awaited_once()
    mocks["refresh_recut_cuts"].assert_awaited_once()


@pytest.mark.asyncio
async def test_a_child_that_is_already_running_is_skipped_not_failed():
    mocks = {name: AsyncMock() for name in CHILDREN}
    mocks["build_image_embeddings"].side_effect = BatchAlreadyRunningError("build_image_embeddings")
    with _patched(mocks):
        results = await run_steps(module.STEPS, trigger="manual")

    assert results["cut_embeddings"]["status"] == SKIPPED_BUSY


@pytest.mark.asyncio
async def test_the_driver_fails_when_a_step_failed_and_records_the_step_results():
    recorded = {}

    async def record_stats(run_id, stats):
        recorded.update(stats)

    mocks = {name: AsyncMock() for name in CHILDREN}
    mocks["refresh_recut_cuts"].side_effect = RuntimeError("boom")
    with _patched(mocks), patch.object(module, "record_stats", record_stats):
        with pytest.raises(RuntimeError, match="steps failed: refresh"):
            await module._run("run-id", "manual")

    assert recorded["steps"]["refresh"]["status"] == FAILED and recorded["steps"]["cuts"]["status"] == OK


def test_the_job_is_registered_as_admin_triggerable():
    entry = BatchRegistry().get("recut_pipeline")
    assert entry == {"module": "batch.recut_pipeline", "kind": "recut_pipeline"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest batch/tests/test_recut_pipeline.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'batch.recut_pipeline'`.

- [ ] **Step 3: Implement**

`batch/recut_pipeline.py`:

```python
"""
Recut pipeline driver (screenshot recut R7): turns the `detected` proposals into cuts that are ready to review, with one
admin-triggerable job instead of three hand-run ones, one of them shell-only. Detection (`detect_recut_crops`) stays a
separate manual step on purpose: it decides how large the review queue gets. Manual-trigger only; not scheduled (the
decision and its trigger are in docs/batches/inventory.md). See
docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-29-r7-operations-and-evaluation-design.md.

Same model as description_pipeline and refresh_recut_cuts: every child is called in process with run_id=None, so it
self-tracks under its own kind; an already-running child is skipped, not failed. The steps have no dependencies on each
other -- each selects only what is still waiting, so a failed `cuts` step (20 failures in a row) still lets the embeddings
and the refresh serve the cuts that exist -- and resuming after a failure is triggering again.
"""
import argparse
import asyncio
import uuid

from batch import build_image_embeddings, create_recut_cuts, refresh_recut_cuts
from batch.description_pipeline import FAILED, Step, run_steps
from batch.run_tracking import finish_existing_run, record_stats, tracked_run
from config.settings import load_env, settings


async def _cuts(trigger: str) -> None:
    await create_recut_cuts.main(trigger=trigger, limit=settings.get("recut.max_cuts_per_run"))


STEPS = [
    Step("cuts", _cuts),
    Step("cut_embeddings", lambda t: build_image_embeddings.main(
        trigger=t, incremental=True, target_status="cut_proposed")),
    Step("refresh", lambda t: refresh_recut_cuts.main(trigger=t)),
]


async def _run(run_id: uuid.UUID, trigger: str) -> None:
    results = await run_steps(STEPS, trigger)
    await record_stats(run_id, {"steps": results})
    failed = [name for name, result in results.items() if result["status"] == FAILED]
    if failed:
        raise RuntimeError(f"recut_pipeline: steps failed: {', '.join(failed)}")


async def main(trigger: str = "manual", run_id: uuid.UUID | None = None) -> None:
    if run_id is not None:
        async with finish_existing_run(run_id):
            await _run(run_id, trigger)
    else:
        async with tracked_run(kind="recut_pipeline", trigger=trigger) as new_run_id:
            await _run(new_run_id, trigger)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["metal", "general", "it"], default=None)
    args = parser.parse_args()
    load_env(args.env)
    asyncio.run(main())  # trigger defaults to "manual" -- unchanged direct-CLI behavior
```

`environments/batch_registry.yaml`: after the `refresh_recut_cuts` entry add

```yaml
recut_pipeline:
  module: batch.recut_pipeline
  kind: recut_pipeline
```

`environments/settings.yaml`, in the `recut:` block after `jpeg_quality`:

```yaml
  # max_cuts_per_run: 500       # optional cap on proposals cut per recut_pipeline run (passed to create_recut_cuts as --limit); unset = unlimited
```

- [ ] **Step 4: Run to verify it passes**

Run: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest batch/tests/test_recut_pipeline.py -q`
Expected: all pass. If `BatchRegistry()` needs a `base_dir` for the real `environments/`, check how `batch/registry.py` resolves its default and adjust the last test to the real directory, not a temp one.

- [ ] **Step 5: Docs**

- `docs/batches/inventory.md`: add a row for `recut_pipeline` next to the other recut rows (columns as the neighbours): "Screenshot recut: turns `detected` proposals into reviewable cuts: `create_recut_cuts`, `build_image_embeddings --status cut_proposed`, `refresh_recut_cuts` (R7)", kind `recut_pipeline`, admin yes, scheduled no, notes "Detection stays a separate manual step. `recut.max_cuts_per_run` caps the cut step. Not scheduled by decision (R7): review, not detection, is the bottleneck; revisit once the first pass is reviewed (then `detect_recut_crops` + `recut_pipeline` weekly on `general` is a settings change)." Append "Scheduling: see `recut_pipeline`." to the `detect_recut_crops` row notes. Update the counts in the summary lines (the registry now holds 36 entries; "Admin-triggerable, manual only" 32) and add `recut_pipeline` to the parenthesised registration list ("`recut_pipeline` 2026-10-29").
- `CLAUDE.md`: after the `refresh_recut_cuts` entry add a `recut_pipeline` entry in the same style: what it runs (the three steps), that detection is separate, run-tracked, admin-triggerable, manual only and not scheduled, `recut.max_cuts_per_run`, the spec path.

- [ ] **Step 6: Run the scopes and commit**

Run: `... -m pytest batch/tests/ -q` (all pass), `... -m pytest tests/docs/ -q` (12 pass), and `DATABASE_URL=... ... -m pytest tests/integration/ -q` (all pass).

```bash
git add batch/recut_pipeline.py batch/tests/test_recut_pipeline.py environments docs/batches/inventory.md CLAUDE.md
git commit -m "feat(recut): R7 recut_pipeline driver

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The gold precision report

**Files:**
- Create: `evaluation/recut_report.py`
- Modify: `evaluation/__main__.py`, `tests/evaluation/test_pure_imports.py`, `evaluation/gold/recut/README.md`
- Test: `tests/evaluation/test_recut_report.py`

**Interfaces:**
- Produces: `evaluation.recut_report` with `BAND_LABELS: tuple[str, ...]` (`("0.6-0.7", "0.7-0.8", "0.8-0.9", "0.9-1.0", "1.0")`), `band_of(confidence: float) -> str`, `wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]`, `build_report(round1_judgments, round1_key, round2_judgments, round2_key) -> dict`, `load_report(directory: Path) -> dict`, `format_report(report: dict) -> str`; CLI command `recut-report` (no database).
- `build_report` returns `{"bands": {label: {"graded", "as_is", "slightly_off", "wrong"}}, "total": {...same}, "excluded": {"inner": int, "skipped": int, "ungraded": int}, "recall": {"measurable": False, "judged_no_proposal": int, "needed_a_cut": int, "skipped_no_proposal": int}}`.

- [ ] **Step 1: Write the failing tests**

`tests/evaluation/test_recut_report.py`:

```python
import json
from pathlib import Path

import pytest

from evaluation import __main__ as cli
from evaluation.recut_report import BAND_LABELS, band_of, build_report, format_report, load_report, wilson

REAL_GOLD = Path(__file__).resolve().parents[2] / "evaluation" / "gold" / "recut"


@pytest.mark.parametrize("confidence, label", [
    (0.6, "0.6-0.7"), (0.69, "0.6-0.7"), (0.7, "0.7-0.8"), (0.8, "0.8-0.9"), (0.9, "0.9-1.0"),
    (0.99, "0.9-1.0"), (1.0, "1.0"), (0.55, "0.6-0.7")])
def test_band_edges(confidence, label):
    assert band_of(confidence) == label


def test_the_bands_are_the_documented_five():
    assert BAND_LABELS == ("0.6-0.7", "0.7-0.8", "0.8-0.9", "0.9-1.0", "1.0")


def test_wilson_interval_for_known_values():
    low, high = wilson(118, 124)
    assert low == pytest.approx(0.8977, abs=0.001) and high == pytest.approx(0.9814, abs=0.001)
    assert wilson(0, 0) == (0.0, 0.0)


def _fixture():
    round1_key = {"i0": {"id": "a", "box": [0, 0, 1, 1], "by": ["A"]}, "i1": {"id": "b", "box": [0, 0, 1, 1], "by": ["C"]},
                  "i2": {"id": "c", "box": [0, 0, 1, 1], "by": ["B"]}, "i3": {"id": "d", "box": [0, 0, 1, 1], "by": ["A"]},
                  "i4": {"id": "e", "box": [0, 0, 1, 1], "by": ["none"]}, "i5": {"id": "f", "box": [0, 0, 1, 1], "by": ["none"]},
                  "i6": {"id": "g", "box": [0, 0, 1, 1], "by": ["none"]}}
    round1 = {"i0": "2", "i1": "1", "i2": "2", "i3": "s", "i4": "n", "i5": "y", "i6": "s"}
    round2_key = {"i0": {"id": "h", "box": [0, 0, 1, 1], "band": "margin 0.6-0.7", "confidence": 0.65},
                  "i1": {"id": "i", "box": [0, 0, 1, 1], "band": "margin 0.7-0.8", "confidence": 0.75},
                  "i2": {"id": "j", "box": [0, 0, 1, 1], "band": "inner", "confidence": 0.4},
                  "i3": {"id": "k", "box": [0, 0, 1, 1], "band": "margin 0.9-1.0", "confidence": 0.95}}
    round2 = {"i0": "2", "i1": "0", "i2": "2", "i3": "2"}
    return round1, round1_key, round2, round2_key


def test_build_report_counts_only_the_shipped_detectors_proposals():
    report = build_report(*_fixture())

    assert report["bands"]["1.0"] == {"graded": 2, "as_is": 1, "slightly_off": 1, "wrong": 0}
    assert report["bands"]["0.6-0.7"] == {"graded": 1, "as_is": 1, "slightly_off": 0, "wrong": 0}
    assert report["bands"]["0.7-0.8"] == {"graded": 1, "as_is": 0, "slightly_off": 0, "wrong": 1}
    assert report["bands"]["0.9-1.0"] == {"graded": 1, "as_is": 1, "slightly_off": 0, "wrong": 0}
    assert report["bands"]["0.8-0.9"] == {"graded": 0, "as_is": 0, "slightly_off": 0, "wrong": 0}
    assert report["total"] == {"graded": 5, "as_is": 3, "slightly_off": 1, "wrong": 1}
    assert report["excluded"] == {"inner": 1, "skipped": 1, "ungraded": 0}


def test_recall_is_not_measurable_and_the_evidence_is_given():
    report = build_report(*_fixture())

    assert report["recall"] == {"measurable": False, "judged_no_proposal": 2, "needed_a_cut": 1,
                                "skipped_no_proposal": 1}


def test_an_item_without_a_judgment_is_counted_as_ungraded():
    round1, round1_key, round2, round2_key = _fixture()
    del round1["i0"]

    report = build_report(round1, round1_key, round2, round2_key)

    assert report["excluded"]["ungraded"] == 1
    assert report["bands"]["1.0"]["graded"] == 1


def test_format_report_says_recall_is_not_measurable_and_names_the_detector():
    text = format_report(build_report(*_fixture()))

    assert "not measurable" in text
    assert "detector A" in text and "margin_trim" in text
    for label in BAND_LABELS:
        assert label in text


def test_the_real_gold_files_give_the_documented_totals():
    report = load_report(REAL_GOLD)

    assert report["total"] == {"graded": 124, "as_is": 118, "slightly_off": 5, "wrong": 1}
    assert report["recall"]["measurable"] is False
    assert report["recall"]["needed_a_cut"] == 0


def test_the_cli_prints_the_report_without_a_database(capsys):
    assert cli.main(["--gold-dir", str(REAL_GOLD.parent), "recut-report"]) == 0

    assert "not measurable" in capsys.readouterr().out


def test_the_cli_reports_missing_gold_files(tmp_path, capsys):
    assert cli.main(["--gold-dir", str(tmp_path), "recut-report"]) != 0
```

(If the real files give other round-1/round-2 splits than the plan's 124/118/5/1, stop and read the files before touching the expected numbers: they were computed by hand in the design. A `s` or missing grade inside the margin items explains a difference; the totals line in the test must then be updated together with the spec.)

- [ ] **Step 2: Run to verify it fails**

Run: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe -m pytest tests/evaluation/test_recut_report.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'evaluation.recut_report'`.

- [ ] **Step 3: Implement**

`evaluation/recut_report.py` (stdlib only):

```python
"""Precision of the shipped recut detector on the labelled sample (screenshot recut R7, FR-16). Pure: reads the gold files
under evaluation/gold/recut/ and counts; no database, no images.

The sample was judged on the R2 spike's detector A. The shipped detector (`margin_trim` v1) is A's margin part, so only
margin proposals count: round 1 items proposed by A or C (all at confidence 1.0), round 2 items of a margin band. Round 2
inner-rectangle items are excluded (the shipped detector does not propose them). A different detector version needs a new
labelled round. Grades: 2 use as is, 1 slightly off, 0 wrong, s skipped.

BAND_LABELS mirrors the bands of GET /api/recut/stats (Backend/app/repositories/recut_repository.py), so gold and live
numbers compare band for band."""
import json
from math import sqrt
from pathlib import Path

BAND_LABELS = ("0.6-0.7", "0.7-0.8", "0.8-0.9", "0.9-1.0", "1.0")
_FILES = ("spike-round1-judgments.json", "spike-round1-key.json", "spike-round2-judgments.json", "spike-round2-key.json")


def band_of(confidence: float) -> str:
    """Lower edge inclusive, upper exclusive; 1.0 is its own band; anything under 0.7 is the first band (the detector's
    floor is 0.6)."""
    if confidence >= 1.0:
        return "1.0"
    if confidence >= 0.9:
        return "0.9-1.0"
    if confidence >= 0.8:
        return "0.8-0.9"
    if confidence >= 0.7:
        return "0.7-0.8"
    return "0.6-0.7"


def wilson(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return (centre - margin, centre + margin)


def _empty() -> dict:
    return {"graded": 0, "as_is": 0, "slightly_off": 0, "wrong": 0}


def _count(counts: dict, grade: str) -> None:
    counts["graded"] += 1
    counts[{"2": "as_is", "1": "slightly_off", "0": "wrong"}[grade]] += 1


def build_report(round1_judgments: dict, round1_key: dict, round2_judgments: dict, round2_key: dict) -> dict:
    bands = {label: _empty() for label in BAND_LABELS}
    excluded = {"inner": 0, "skipped": 0, "ungraded": 0}
    recall = {"measurable": False, "judged_no_proposal": 0, "needed_a_cut": 0, "skipped_no_proposal": 0}

    def take(grade, label):
        if grade is None:
            excluded["ungraded"] += 1
        elif grade == "s":
            excluded["skipped"] += 1
        else:
            _count(bands[label], grade)

    for item, entry in round1_key.items():
        by = entry.get("by", [])
        grade = round1_judgments.get(item)
        if "A" in by or "C" in by:
            take(grade, "1.0")
        elif "none" in by:
            if grade in ("y", "n"):
                recall["judged_no_proposal"] += 1
                recall["needed_a_cut"] += grade == "y"
            elif grade == "s":
                recall["skipped_no_proposal"] += 1
    for item, entry in round2_key.items():
        if not str(entry.get("band", "")).startswith("margin"):
            excluded["inner"] += 1
            continue
        take(round2_judgments.get(item), band_of(entry["confidence"]))

    total = _empty()
    for counts in bands.values():
        for field in total:
            total[field] += counts[field]
    return {"bands": bands, "total": total, "excluded": excluded, "recall": recall}


def load_report(directory: Path) -> dict:
    directory = Path(directory)
    missing = [name for name in _FILES if not (directory / name).exists()]
    if missing:
        raise FileNotFoundError(f"missing gold files in {directory}: {', '.join(missing)}")
    r1j, r1k, r2j, r2k = (json.loads((directory / name).read_text(encoding="utf-8")) for name in _FILES)
    return build_report(r1j, r1k, r2j, r2k)


def _row(label: str, counts: dict) -> str:
    graded = counts["graded"]
    if graded == 0:
        return f"{label:<10}{0:>8}{'-':>26}{'-':>30}{0:>7}"
    as_is = f"{counts['as_is'] / graded:.1%} ({counts['as_is']}/{graded})"
    ok = counts["as_is"] + counts["slightly_off"]
    low, high = wilson(ok, graded)
    either = f"{ok / graded:.1%} ({ok}/{graded}) [{low:.1%}-{high:.1%}]"
    return f"{label:<10}{graded:>8}{as_is:>26}{either:>30}{counts['wrong']:>7}"


def format_report(report: dict) -> str:
    lines = [
        "Recut detection precision on the labelled sample",
        "Sample: the R2 spike's detector A, whose margin part is the shipped margin_trim v1. A different detector version",
        "needs a new labelled round.",
        "",
        f"{'band':<10}{'graded':>8}{'as is':>26}{'as is or slightly off [95% CI]':>30}{'wrong':>7}",
    ]
    lines += [_row(label, report["bands"][label]) for label in BAND_LABELS]
    lines.append(_row("all", report["total"]))
    excluded = report["excluded"]
    lines += [
        "",
        f"Excluded: {excluded['inner']} inner-rectangle items (not proposed by the shipped detector), "
        f"{excluded['skipped']} skipped, {excluded['ungraded']} without a judgment.",
        "",
        "Recall: not measurable. "
        f"{report['recall']['judged_no_proposal']} no-proposal items were judged ({report['recall']['needed_a_cut']} needed a "
        f"cut) and {report['recall']['skipped_no_proposal']} were skipped; too few to say anything.",
    ]
    return "\n".join(lines)
```

In `evaluation/__main__.py`: import `from evaluation.recut_report import format_report as format_recut_report, load_report as load_recut_report`; add next to `cmd_list_baselines`:

```python
def cmd_recut_report(args, gold: Path) -> int:
    try:
        report = load_recut_report(gold / "recut")
    except FileNotFoundError as error:
        return _error(str(error))
    print(format_recut_report(report))
    return 0
```

register `commands.add_parser("recut-report")` next to `list-baselines`, and add `"recut-report": cmd_recut_report` to the `handlers` dict in `main()`.

`tests/evaluation/test_pure_imports.py`: add `"recut_report.py"` to `PURE`. `evaluation/gold/recut/README.md`: add "Precision report: `python -m evaluation recut-report` (no database needed; see the R7 design)."

- [ ] **Step 4: Run to verify it passes**

Run: `... -m pytest tests/evaluation/ -q` (all pass, including the pinned real-file run), then `... -m evaluation recut-report` from the worktree root and read the printed table once.

- [ ] **Step 5: Commit**

```bash
git add evaluation tests/evaluation
git commit -m "feat(recut): R7 gold precision report

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The stats endpoint

**Files:**
- Create: `shared/schemas/recutstatsband.schema.json`, `shared/schemas/recutstats.schema.json`
- Modify: `shared/schemas/all.schema.json`, `Backend/app/repositories/recut_repository.py`, `Backend/app/api/recut.py`, `backend_api.md`; regenerate TS/Kotlin/Python types
- Test: `tests/integration/test_recut_stats.py`, `Backend/tests/test_recut_endpoints.py`

**Interfaces:**
- Produces: TS types `RecutStats`, `RecutStatsBand`; Python `Backend.app.types.generated.recutstats.Schema`; `RecutRepository.band_stats() -> dict`; `GET /api/recut/stats`.
- Wire shape: `{"bands": [band...], "total": band}`, band = `{"label": str, "lower": float|None, "upper": float|None, "waiting": int, "approved": int, "rejected": int, "approveRate": float|None}`.

- [ ] **Step 1: Write the schemas and regenerate**

`recutstatsband.schema.json` (draft-07, `$id`, `title: RecutStatsBand`, `additionalProperties: false`): `label` string; `lower` and `upper` `["number","null"]`; `waiting`, `approved`, `rejected` integer; `approveRate` `["number","null"]`; all required. `recutstats.schema.json` (`title: RecutStats`): `bands` array of `{"$ref": "./recutstatsband.schema.json"}`, `total` `{"$ref": "./recutstatsband.schema.json"}`; both required (mirror how `recutproposalpage.schema.json` refs `./recutproposal.schema.json`). In `all.schema.json` add `"RecutStats": { "$ref": "recutstats.schema.json" },` and `"RecutStatsBand": { "$ref": "recutstatsband.schema.json" },` after `RecutDecision`.

Regenerate as in R5a: from `Frontend/`: `bash generate-types.sh` (`pnpm install --frozen-lockfile` in `Frontend/memes-frontend/` first if there is no `node_modules`); from the repo root: `H:\workspace_sandbox\memes\.venv311\Scripts\python.exe AndroidClient/scripts/generate_dtos.py`; from `Backend/`: `H:\workspace_sandbox\memes\.venv311\Scripts\datamodel-codegen.exe --input ../shared/schemas/all.schema.json --input-file-type jsonschema --output app/types/generated/ --target-python-version 3.11 --use-standard-collections --use-schema-description --use-field-description --use-default-kwarg --use-subclass-enum --strict-nullable --output-model-type pydantic_v2.BaseModel`. The generator rewrites a `# timestamp:` line in every module: copy `Backend/app/types/generated/__init__.py` aside, `git checkout -- Backend/app/types/generated`, copy it back, so only `__init__.py` and the new `recutstats*.py` modules differ. Check `git status --short` shows only the schemas, `all.d.ts`, `Models.kt`, `__init__.py` and the new modules. Commit this step on its own:

```bash
git add shared/schemas Frontend/memes-frontend/src/types/generated AndroidClient Backend/app/types/generated
git commit -m "feat(recut): R7 stats schema and generated types

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 2: Write the failing tests**

`tests/integration/test_recut_stats.py`:

```python
"""RecutRepository.band_stats: the live approve rate by confidence band (screenshot recut R7). Requires a live PostgreSQL
instance. The test database is shared, so assertions compare the change a seeded set makes against a baseline taken first."""
import uuid

import pytest

from Backend.app.repositories.recut_repository import RecutRepository
from Storage.models import CutState, ImageCut

LABELS = ["0.6-0.7", "0.7-0.8", "0.8-0.9", "0.9-1.0", "1.0", "unscored"]


async def _row(session, confidence, state):
    session.add(ImageCut(source_image_id=None, cut_image_id=None, crop_x=1, crop_y=1, crop_w=5, crop_h=5,
                         detector="margin_trim", detector_version=str(uuid.uuid4())[:8], confidence=confidence,
                         state=state))
    await session.flush()


def _by_label(stats):
    return {band["label"]: band for band in stats["bands"]}


@pytest.mark.asyncio(loop_scope="session")
async def test_the_bands_come_in_a_fixed_order_with_their_edges(db_session):
    stats = await RecutRepository(db_session).band_stats()

    assert [b["label"] for b in stats["bands"]] == LABELS
    assert [(b["lower"], b["upper"]) for b in stats["bands"]] == [
        (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0), (1.0, 1.0), (None, None)]
    assert stats["total"]["label"] == "total"


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("confidence, label", [
    (0.6, "0.6-0.7"), (0.699, "0.6-0.7"), (0.7, "0.7-0.8"), (0.8, "0.8-0.9"), (0.9, "0.9-1.0"), (1.0, "1.0"),
    (None, "unscored")])
async def test_a_confidence_lands_in_the_band_whose_lower_edge_it_reaches(db_session, confidence, label):
    repo = RecutRepository(db_session)
    before = _by_label(await repo.band_stats())[label]["waiting"]

    await _row(db_session, confidence, CutState.PROPOSED)

    assert _by_label(await repo.band_stats())[label]["waiting"] == before + 1


@pytest.mark.asyncio(loop_scope="session")
async def test_counts_per_state_and_the_approve_rate(db_session):
    repo = RecutRepository(db_session)
    before = _by_label(await repo.band_stats())["0.8-0.9"]
    for state in (CutState.APPROVED, CutState.APPROVED, CutState.APPROVED, CutState.REJECTED, CutState.PROPOSED):
        await _row(db_session, 0.85, state)

    band = _by_label(await repo.band_stats())["0.8-0.9"]

    assert band["approved"] == before["approved"] + 3
    assert band["rejected"] == before["rejected"] + 1
    assert band["waiting"] == before["waiting"] + 1
    decided = band["approved"] + band["rejected"]
    assert band["approveRate"] == pytest.approx(band["approved"] / decided)


@pytest.mark.asyncio(loop_scope="session")
async def test_detected_and_failed_rows_are_not_counted(db_session):
    repo = RecutRepository(db_session)
    before = (await repo.band_stats())["total"]
    await _row(db_session, 0.95, CutState.DETECTED)
    await _row(db_session, 0.95, CutState.FAILED)

    after = (await repo.band_stats())["total"]

    assert (after["waiting"], after["approved"], after["rejected"]) == (
        before["waiting"], before["approved"], before["rejected"])


@pytest.mark.asyncio(loop_scope="session")
async def test_the_total_is_the_sum_of_the_bands_and_a_rate_is_null_without_decisions(db_session):
    repo = RecutRepository(db_session)
    await _row(db_session, 0.75, CutState.PROPOSED)

    stats = await repo.band_stats()

    for field in ("waiting", "approved", "rejected"):
        assert stats["total"][field] == sum(b[field] for b in stats["bands"])
    for band in [*stats["bands"], stats["total"]]:
        decided = band["approved"] + band["rejected"]
        assert band["approveRate"] == (None if decided == 0 else band["approved"] / decided)
```

Append to `Backend/tests/test_recut_endpoints.py` (add `AsyncMock` `repo.band_stats` in the existing `client_and_repo` fixture: `repo.band_stats = AsyncMock(return_value=STATS)`):

```python
BAND = {"label": "0.9-1.0", "lower": 0.9, "upper": 1.0, "waiting": 3, "approved": 7, "rejected": 1, "approveRate": 0.875}
STATS = {"bands": [BAND], "total": {**BAND, "label": "total", "lower": None, "upper": None}}


def test_stats_returns_the_bands_and_the_total(client_and_repo):
    client, repo = client_and_repo
    response = client.get("/api/recut/stats")
    assert response.status_code == 200 and response.json() == STATS
    repo.band_stats.assert_awaited_once_with()
```

(Define `STATS` above the fixture so the fixture can use it.)

- [ ] **Step 3: Run to verify they fail**

Run: `DATABASE_URL=... H:\...\python.exe -m pytest tests/integration/test_recut_stats.py -q` (AttributeError: no `band_stats`) and, from `Backend/`, `... -m pytest tests/test_recut_endpoints.py -q` (404 on `/api/recut/stats`).

- [ ] **Step 4: Implement**

`Backend/app/repositories/recut_repository.py`: add imports `case` to the sqlalchemy import, and at module level

```python
# Bands of the live approve-rate table. Mirrors evaluation/recut_report.py (BAND_LABELS), so gold and live numbers compare
# band for band. Lower edge inclusive; anything under 0.7 is the first band (the detector's floor is 0.6); 1.0 is its own.
_BANDS = (("0.6-0.7", 0.6, 0.7), ("0.7-0.8", 0.7, 0.8), ("0.8-0.9", 0.8, 0.9), ("0.9-1.0", 0.9, 1.0),
          ("1.0", 1.0, 1.0), ("unscored", None, None))
_DECIDABLE = (CutState.PROPOSED, CutState.APPROVED, CutState.REJECTED)


def _band_expression():
    return case(
        (ImageCut.confidence.is_(None), "unscored"),
        (ImageCut.confidence >= 1.0, "1.0"),
        (ImageCut.confidence >= 0.9, "0.9-1.0"),
        (ImageCut.confidence >= 0.8, "0.8-0.9"),
        (ImageCut.confidence >= 0.7, "0.7-0.8"),
        else_="0.6-0.7",
    )


def _stats_band(label: str, lower, upper, counts: dict) -> dict:
    approved, rejected = counts.get(CutState.APPROVED, 0), counts.get(CutState.REJECTED, 0)
    decided = approved + rejected
    return {"label": label, "lower": lower, "upper": upper, "waiting": counts.get(CutState.PROPOSED, 0),
            "approved": approved, "rejected": rejected, "approveRate": None if decided == 0 else approved / decided}
```

and on `RecutRepository`:

```python
    async def band_stats(self) -> dict:
        """Waiting / approved / rejected counts and the approve rate per confidence band, computed at read time (NFR-5).
        'waiting' counts every proposed row; the review queue additionally needs both images, so the two can differ by
        orphaned rows."""
        band = _band_expression().label("band")
        rows = (await self.session.execute(
            select(band, ImageCut.state, func.count()).where(ImageCut.state.in_(_DECIDABLE)).group_by(band, ImageCut.state)
        )).all()
        per_band: dict[str, dict] = {}
        for label, state, count in rows:
            per_band.setdefault(label, {})[state] = count
        totals: dict[str, int] = {}
        for counts in per_band.values():
            for state, count in counts.items():
                totals[state] = totals.get(state, 0) + count
        return {"bands": [_stats_band(label, lower, upper, per_band.get(label, {})) for label, lower, upper in _BANDS],
                "total": _stats_band("total", None, None, totals)}
```

`Backend/app/api/recut.py`: import `from Backend.app.types.generated.recutstats import Schema as RecutStats` and, before the `/proposals/{proposal_id}` routes:

```python
@router.get("/stats", response_model=RecutStats)
async def get_stats(repo: RecutRepository = Depends(get_recut_repository)):
    return await repo.band_stats()
```

`backend_api.md`, "Recut review" section: add "**Review statistics**" - `GET /api/recut/stats`, response `RecutStats`: `bands` (six, fixed order: `0.6-0.7`, `0.7-0.8`, `0.8-0.9`, `0.9-1.0`, `1.0`, `unscored`; each `label`, `lower`, `upper` (null for `unscored`), `waiting`, `approved`, `rejected`, `approveRate`) and `total` (same shape, label `total`). `approveRate` is `approved / (approved + rejected)`, null while nothing is decided. Counts proposals in state `proposed`, `approved` and `rejected` by detector confidence, computed per request.

- [ ] **Step 5: Run to verify it passes, then the roots**

Run the two commands from Step 3 (pass), then `DATABASE_URL=... ... -m pytest tests/integration/ -q` (all pass) and from `Backend/` `... -m pytest -q` (all pass except the known unrelated `test_forces_utf8_output_encoding_in_child_env`, report it by name).

- [ ] **Step 6: Commit**

```bash
git add Backend/app/repositories/recut_repository.py Backend/app/api/recut.py Backend/tests/test_recut_endpoints.py tests/integration/test_recut_stats.py backend_api.md
git commit -m "feat(recut): R7 live approve rate by confidence band

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The "Review so far" table on the review page

**Files:**
- Create: `Frontend/memes-frontend/src/components/recut/ReviewStats.tsx`, `src/recut/useRecutStats.ts`
- Modify: `src/api/MemesApi.ts`, `src/api/http/HttpMemesApi.ts`, `src/test/mockApi.ts`, `src/recut/types.ts`, `src/recut/fixtures.ts`, `src/pages/RecutReviewPage.tsx`
- Test: `src/components/recut/ReviewStats.test.tsx`, `src/pages/RecutReviewPage.test.tsx`, `src/api/http/HttpMemesApi.recut.test.ts`

**Interfaces:**
- Consumes: generated `RecutStats`, `RecutStatsBand` (Task 3); `CuratorRequestError`.
- Produces: `MemesApi.getRecutStats(): Promise<RecutStats>`; `makeStats(overrides?)` fixture; `useRecutStats(memesApi, refreshKey: number | null): RecutStats | null`; `<ReviewStats stats={RecutStats} />`.

- [ ] **Step 1: Write the failing tests**

Fixture first (`src/recut/fixtures.ts`, plus the type re-exports in `types.ts`):

```ts
export function makeStats(overrides: Partial<RecutStats> = {}): RecutStats {
  return {
    bands: [
      { label: "0.9-1.0", lower: 0.9, upper: 1, waiting: 3, approved: 7, rejected: 1, approveRate: 0.875 },
      { label: "0.8-0.9", lower: 0.8, upper: 0.9, waiting: 2, approved: 0, rejected: 0, approveRate: null },
      { label: "unscored", lower: null, upper: null, waiting: 0, approved: 0, rejected: 0, approveRate: null },
    ],
    total: { label: "total", lower: null, upper: null, waiting: 5, approved: 7, rejected: 1, approveRate: 0.875 },
    ...overrides,
  }
}
```

`src/components/recut/ReviewStats.test.tsx`:

```tsx
import { render, screen, within } from "@testing-library/react"
import ReviewStats from "./ReviewStats"
import { makeStats } from "../../recut/fixtures"

describe("ReviewStats", () => {
  it("shows a row per band that has proposals, with the approve rate as a percentage", () => {
    render(<ReviewStats stats={makeStats()} />)

    const row = screen.getByRole("row", { name: /0\.9-1\.0/ })
    expect(within(row).getByText("88%")).toBeInTheDocument()
    expect(within(row).getByText("3")).toBeInTheDocument()
  })

  it("shows a dash instead of a rate while nothing in the band is decided", () => {
    render(<ReviewStats stats={makeStats()} />)

    expect(within(screen.getByRole("row", { name: /0\.8-0\.9/ })).getByText("-")).toBeInTheDocument()
  })

  it("leaves out bands without any proposal", () => {
    render(<ReviewStats stats={makeStats()} />)

    expect(screen.queryByRole("row", { name: /unscored/ })).not.toBeInTheDocument()
  })

  it("shows the total row and explains how the rate differs from the gold numbers", () => {
    render(<ReviewStats stats={makeStats()} />)

    expect(screen.getByRole("row", { name: /total/i })).toBeInTheDocument()
    expect(screen.getByText(/stricter than/i)).toBeInTheDocument()
  })
})
```

Add to `RecutReviewPage.test.tsx` (the existing `setup` builds the mock API with `makeMockApi`, whose new default is `getRecutStats: vi.fn().mockResolvedValue(makeStats())`):

```tsx
  it("shows the review statistics under the page", async () => {
    setup()
    expect(await screen.findByText("Review so far")).toBeInTheDocument()
    expect(await screen.findByRole("row", { name: /0\.9-1\.0/ })).toBeInTheDocument()
  })

  it("still lets the owner review when the statistics cannot be loaded", async () => {
    setup({ getRecutStats: vi.fn().mockRejectedValue(new CuratorRequestError(500)) })

    expect(await screen.findByText("1 of 3")).toBeInTheDocument()
    expect(screen.queryByText("Review so far")).not.toBeInTheDocument()
  })

  it("reloads the statistics after a decision", async () => {
    const user = userEvent.setup()
    const stats = vi.fn().mockResolvedValue(makeStats())
    const api = setup({ getRecutStats: stats })
    api.approveRecutProposal = vi.fn().mockResolvedValue(makeDecision("a", "approved"))
    await screen.findByText("1 of 3")
    await waitFor(() => expect(stats).toHaveBeenCalledTimes(1))

    await user.click(screen.getByRole("button", { name: "Approve" }))

    await waitFor(() => expect(stats).toHaveBeenCalledTimes(2))
  })
```

(import `makeStats` from `../recut/fixtures`.) Append to `HttpMemesApi.recut.test.ts`:

```ts
  it("gets the review statistics", async () => {
    const body = makeStats()
    const fetchMock = stubFetch({ ok: true, status: 200, body })

    expect(await api.getRecutStats()).toEqual(body)
    expect(fetchMock.mock.calls[0][0]).toBe("http://backend/api/recut/stats")
  })

  it("rejects with the HTTP status when the statistics fail", async () => {
    stubFetch({ ok: false, status: 500 })

    await expect(api.getRecutStats()).rejects.toMatchObject({ name: "CuratorRequestError", status: 500 })
  })
```

- [ ] **Step 2: Run to verify it fails**

Run from `Frontend/memes-frontend/`: `pnpm exec vitest run src/components/recut src/pages/RecutReviewPage.test.tsx src/api/http/HttpMemesApi.recut.test.ts`
Expected: failures (no `ReviewStats`, `getRecutStats` undefined, `makeStats` missing); the R4/R5a tests still pass.

- [ ] **Step 3: Implement**

- `types.ts`: re-export `RecutStats` and `RecutStatsBand` from `../types/generated/all`.
- `MemesApi.ts`: import `RecutStats`; add `getRecutStats(): Promise<RecutStats>`. `HttpMemesApi.ts`: add

```ts
  async getRecutStats(): Promise<RecutStats> {
    const response = await fetch(`${this.baseUrl}/api/recut/stats`, { headers: { "Accept": "application/json" } })
    if (!response.ok) throw new CuratorRequestError(response.status)
    return response.json()
  }
```

- `test/mockApi.ts`: `getRecutStats: vi.fn().mockResolvedValue(makeStats()),` (import `makeStats`).
- `src/recut/useRecutStats.ts`:

```ts
import { useEffect, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { RecutStats } from "./types"

/** The review statistics. `refreshKey` null means "not yet" (nothing is requested); a new key requests them again.
 * A failed request leaves no statistics: the table is optional and must never block reviewing. */
export function useRecutStats(memesApi: MemesApi, refreshKey: number | null): RecutStats | null {
  const [stats, setStats] = useState<RecutStats | null>(null)

  useEffect(() => {
    if (refreshKey === null) return
    let cancelled = false
    memesApi.getRecutStats().then(
      (loaded) => { if (!cancelled) setStats(loaded) },
      () => { if (!cancelled) setStats(null) },
    )
    return () => { cancelled = true }
  }, [memesApi, refreshKey])

  return stats
}
```

- `src/components/recut/ReviewStats.tsx`: a `<section>` with a heading "Review so far", a table (`Band`, `Waiting`, `Approved`, `Rejected`, `Approve rate`), one row per band with `waiting + approved + rejected > 0`, then the `total` row; the rate is `Math.round(rate * 100) + "%"` or `-`; below it a note: "Approve rate is stricter than the gold 'as is or slightly off' precision: a slightly-off box can still be rejected, so the two are not comparable one to one." Row accessible names come from the cells' text, so `getByRole("row", { name: /0\.9-1\.0/ })` works.
- `RecutReviewPage.tsx`: `const stats = useRecutStats(memesApi, listStatus === "ready" ? total : null)` and, after `{body}`, `{stats !== null && <div className="mt-6"><ReviewStats stats={stats} /></div>}`. The key is `total`, which drops by one after each decision and changes on a reload, so the effect re-requests the statistics.

- [ ] **Step 4: Run to verify it passes, then the full gate**

Run the Step 2 command (pass), then from `Frontend/memes-frontend/`: `pnpm exec vitest run`, `pnpm exec tsc -b`, `pnpm exec eslint src/ --max-warnings 0` (all green), and from `Frontend/`: `bash generate-types.sh` then `git diff --stat Frontend/memes-frontend/src/types/generated/` (no change relative to Task 3's commit).

- [ ] **Step 5: Commit**

```bash
git add Frontend/memes-frontend/src
git commit -m "feat(recut): R7 review statistics table on the review page

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Runbook, status docs and full verification

**Files:**
- Create: `docs/batches/use-cases/screenshot-recut.md`
- Modify: `docs/batches/README.md`, `docs/image-status.md`, `docs/superpowers/specs/2026-10-17-screenshot-recut/README.md`, `docs/superpowers/specs/2026-10-17-screenshot-recut/2026-10-29-r7-operations-and-evaluation-design.md`, `docs/superpowers/specs/2026-10-17-screenshot-recut/tracker.md`

- [ ] **Step 1: Write the runbook**

`docs/batches/use-cases/screenshot-recut.md`, in the style of `duplicates-and-clusters.md` (read it first for headings and the mermaid conventions): a title and intro (what the recut does, one paragraph, link to the epic README); a mermaid flowchart of the path: `detect_recut_crops --limit` (manual) -> `recut_pipeline` (subgraph with `create_recut_cuts`, `build_image_embeddings --status cut_proposed`, `refresh_recut_cuts`) -> `/admin/recut` review (approve / reject) -> flagged -> `move_flagged` -> `unregister_deleted_images` -> `remove_singletons`; a table "What runs where" (job, admin-triggerable, shell-only flags, tracked kind); sections "Reviewing" (queue order, keys A / R / arrows, the "Review so far" table and what its approve rate means next to the gold precision, `python -m evaluation recut-report`), "After a failed step" (trigger the driver again; every step selects only what is waiting; `create_recut_cuts --retry-failed` for failed proposals), "Duplicates" (a source/cut pair is never a candidate, the transitive-cluster limit from the R5b spec), "Scheduling" (not scheduled, why, and the revisit trigger), and "First rollout" (the five items from the R7 spec, section 4: migrations in order against the current alembic head, confirmed backup and one environment at a time with the read-only role, a first `detect_recut_crops --limit 500` on `general` then `recut_pipeline`, the overlay check in a real browser, the R3c refresh on the GPU; each step needs the owner's explicit go-ahead).

Check the facts you write against the code and `docs/batches/inventory.md`; do not invent flags.

- [ ] **Step 2: Link and status edits**

- `docs/batches/README.md`: add a row to the use-case table: `[use-cases/screenshot-recut.md](use-cases/screenshot-recut.md)` | "Screenshot recut: detecting badly cropped memes, cutting, review, and removing the sources (`recut_pipeline`)".
- `docs/image-status.md`: in the `cut_proposed` row, "Read by" also names the review statistics (`GET /api/recut/stats`).
- Epic `README.md`: set the epic status to reflect R7 done (read the file for its status line and project list; R6 and M2 remain).
- R7 spec: `Status: draft` becomes `Status: done`, and add `Plan: docs/superpowers/plans/2026-10-29-r7-operations-and-evaluation.md` under the status line (it is `planned` between the plan and the execution; execution sets `implementation`, the merge sets `done`; in this task set `implementation`). Tracker: the R7 row gets the plan path and `implementation`.

- [ ] **Step 3: Full verification**

Run each root separately: `DATABASE_URL=... python.exe -m pytest tests/integration/ -q`; `python.exe -m pytest batch/tests/ -q`; `python.exe -m pytest tests/evaluation/ -q`; `python.exe -m pytest tests/rules/ -q`; `python.exe -m pytest tests/docs/ -q`; from `Backend/`: `python.exe -m pytest -q` (known unrelated failure `test_forces_utf8_output_encoding_in_child_env`, report it by name); from `Frontend/memes-frontend/`: `pnpm exec vitest run`, `pnpm exec tsc -b`, `pnpm exec eslint src/ --max-warnings 0`; from `Frontend/`: `bash generate-types.sh` then `git diff --stat Frontend/memes-frontend/src/types/generated/` (no change).
Smoke (test URL, no server port): `DATABASE_URL=... BASE_PATH=/tmp python.exe -c "from Backend.app.main import app; print([p for p in app.openapi()['paths'] if 'recut' in p])"`; expected: the list, detail, approve, reject and `/api/recut/stats` paths. Also `python.exe -m evaluation recut-report` prints the table.
Expected: all green except the one known Backend failure.

- [ ] **Step 4: Commit**

```bash
git add docs
git commit -m "docs(recut): R7 runbook and implementation status

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```
