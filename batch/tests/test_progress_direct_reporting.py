"""Scripts with a long loop and no ProgressTracker report their progress with report_progress."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import batch.clusterize as clusterize
import batch.create_recut_cuts as create_recut_cuts
import batch.detect_comics_candidates as detect_comics_candidates
import batch.detect_recut_crops as detect_recut_crops
import batch.ingest_hash_dedup as ingest_hash_dedup
import batch.ingest_validate_formats as ingest_validate_formats


@pytest.fixture
def capture(monkeypatch):
    calls = []

    def patch_module(module):
        monkeypatch.setattr(
            module, "report_progress",
            lambda label, unit, done, total, rate_per_min=None: calls.append((label, unit, done, total)),
        )
        return calls

    return patch_module


def _check(calls, label, unit):
    assert calls, "no progress reported"
    assert all(c[0] == label and c[1] == unit for c in calls)
    assert all(c[3] is None or c[2] <= c[3] for c in calls)
    last = calls[-1]
    assert last[3] is not None and last[2] == last[3]


def test_ingest_hash_dedup_makes_progress_calls(capture, tmp_path):
    """Proves only that hash_incoming_files calls report_progress with the right label, unit and
    counts. It does NOT prove the values are written live: the hashing loop is synchronous (no
    await), so the write task only runs after the loop ends and the bar shows only the final
    value (see docs/batches/execution-model.md, "Progress reporting", Limitations)."""
    calls = capture(ingest_hash_dedup)
    for i in range(3):
        (tmp_path / f"f{i}.jpg").write_bytes(bytes([i]))
    (tmp_path / "duplicates").mkdir()
    result = ingest_hash_dedup.hash_incoming_files(str(tmp_path))
    assert sum(len(v) for v in result.values()) == 3
    _check(calls, "ingest_hash_dedup", "files")


@pytest.mark.asyncio
async def test_ingest_validate_formats_reports_progress(capture, monkeypatch):
    calls = capture(ingest_validate_formats)
    monkeypatch.setattr(ingest_validate_formats, "get_pending_batch_images",
                        AsyncMock(return_value=[(1, "a.jpg"), (2, "b.jpg")]))
    monkeypatch.setattr(ingest_validate_formats, "apply_format_fix", AsyncMock())
    monkeypatch.setattr(ingest_validate_formats, "ImagesRepository", MagicMock())
    monkeypatch.setattr(ingest_validate_formats, "ImageExtrasRepository", MagicMock())
    await ingest_validate_formats.run(MagicMock(), "/base", uuid.uuid4())
    _check(calls, "ingest_validate_formats", "files")


@pytest.mark.asyncio
async def test_clusterize_reports_progress(capture, monkeypatch):
    calls = capture(clusterize)
    session = MagicMock(execute=AsyncMock())
    monkeypatch.setattr(clusterize, "get_images_ids",
                        AsyncMock(return_value=({}, {1: "a", 2: "b", 3: "c"})))
    monkeypatch.setattr(clusterize, "get_duplicate_pairs",
                        AsyncMock(return_value=[(1, 2, 0.01), (2, 3, 0.02)]))
    await clusterize.cluster_active_library(session)
    _check(calls, "clusterize", "pairs")


@pytest.mark.asyncio
async def test_clusterize_reports_every_stride_and_the_final_once(capture, monkeypatch):
    calls = capture(clusterize)
    monkeypatch.setattr(clusterize, "_REPORT_EVERY", 2)
    session = MagicMock(execute=AsyncMock())
    monkeypatch.setattr(clusterize, "get_images_ids",
                        AsyncMock(return_value=({}, {i: str(i) for i in range(1, 7)})))
    monkeypatch.setattr(clusterize, "get_duplicate_pairs",
                        AsyncMock(return_value=[(i, i + 1, 0.01) for i in range(1, 6)]))
    await clusterize.cluster_active_library(session)
    assert calls == [("clusterize", "pairs", 2, 5), ("clusterize", "pairs", 4, 5), ("clusterize", "pairs", 5, 5)]


@pytest.mark.asyncio
async def test_create_recut_cuts_reports_progress(capture, monkeypatch, tmp_path):
    calls = capture(create_recut_cuts)
    rows = [SimpleNamespace(cut_id=i, filename="a.jpg") for i in range(3)]
    cuts = MagicMock(
        count_ineligible_detected=AsyncMock(return_value=0), count_cuttable=AsyncMock(return_value=3),
        select_cuttable=AsyncMock(return_value=rows))
    monkeypatch.setattr(create_recut_cuts, "ImageCutsRepository", MagicMock(return_value=cuts))
    monkeypatch.setattr(create_recut_cuts, "_cut_one", AsyncMock())
    session = MagicMock(commit=AsyncMock())
    await create_recut_cuts.run(session, str(tmp_path), set(), 90)
    _check(calls, "create_recut_cuts", "images")


@pytest.mark.asyncio
async def test_detect_recut_crops_reports_progress(capture, monkeypatch, tmp_path):
    calls = capture(detect_recut_crops)
    rows = [SimpleNamespace(id=i, filename="a.jpg", content_hash="h") for i in range(3)]
    cuts = MagicMock(count_candidates=AsyncMock(return_value=3), select_candidates=AsyncMock(return_value=rows))
    processed = MagicMock(mark_done_by_id=AsyncMock())
    monkeypatch.setattr(detect_recut_crops, "ImageCutsRepository", MagicMock(return_value=cuts))
    monkeypatch.setattr(detect_recut_crops, "ImageProcessingStatusRepository", MagicMock(return_value=processed))
    monkeypatch.setattr(detect_recut_crops, "detect_margin_box", lambda path, params: None)
    session = MagicMock(commit=AsyncMock())
    await detect_recut_crops.run(session, str(tmp_path), None, 0.5, None, set(), 100)
    _check(calls, "detect_recut_crops", "images")


@pytest.mark.asyncio
async def test_detect_comics_candidates_reports_progress(capture, monkeypatch):
    calls = capture(detect_comics_candidates)
    monkeypatch.setattr(detect_comics_candidates, "PAIR_BATCH", 2)
    items = [(i, f"{i}", "s", 0.0) for i in range(4)]
    monkeypatch.setattr(detect_comics_candidates, "window_pairs",
                        lambda items, gap: [(items[0], items[i]) for i in range(1, 4)] + [(items[1], items[2])])
    session = MagicMock(execute=AsyncMock(return_value=[]))
    await detect_comics_candidates.window_distances(session, items, 10)
    _check(calls, "detect_comics_candidates", "pairs")
