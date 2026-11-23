"""Unit tests for batch/utils/run_progress.py (no database: the writer is a fake)."""
import asyncio
import uuid

import pytest

from batch.utils import run_progress as rp

ALLOWED_KEYS = {"label", "unit", "done", "total", "rate_per_min", "step"}


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class RecordingWriter:
    """Records every write; optionally blocks on an event or raises."""

    def __init__(self) -> None:
        self.writes: list[tuple[uuid.UUID, dict]] = []
        self.gate: asyncio.Event | None = None
        self.entered: asyncio.Event | None = None
        self.raise_next = 0

    async def __call__(self, run_id: uuid.UUID, value: dict) -> None:
        if self.entered is not None:
            self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.raise_next:
            self.raise_next -= 1
            raise RuntimeError("database down")
        self.writes.append((run_id, value))


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(rp, "_clock", c)
    return c


@pytest.fixture
def writer(monkeypatch):
    w = RecordingWriter()
    monkeypatch.setattr(rp, "_writer", w)
    return w


@pytest.fixture
def config(monkeypatch):
    values = {"enabled": True, "interval": 10.0}
    monkeypatch.setattr(rp, "_config", lambda: (values["enabled"], values["interval"]))
    return values


@pytest.fixture
def run_id(clock, writer, config):
    rid = uuid.uuid4()
    token = rp.current_run_id.set(rid)
    rp._states.clear()
    yield rid
    rp.current_run_id.reset(token)
    rp._states.clear()


async def settle() -> None:
    """Let scheduled write tasks run to completion."""
    for _ in range(10):
        await asyncio.sleep(0)


# --- no-op cases -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_noop_without_current_run(clock, writer, config):
    rp._states.clear()
    assert rp.current_run_id.get() is None
    rp.report_progress("extract_text_from_memes", "images", 1, 10)
    rp.report_step(1, 3, "ocr")
    await settle()
    assert writer.writes == []
    assert rp._states == {}


def test_noop_without_running_loop(run_id, writer):
    rp.report_progress("extract_text_from_memes", "images", 1, 10)
    rp.report_step(1, 3, "ocr")
    assert writer.writes == []


@pytest.mark.asyncio
async def test_noop_when_disabled(run_id, writer, config):
    config["enabled"] = False
    rp.report_progress("extract_text_from_memes", "images", 1, 10)
    rp.report_step(1, 3, "ocr")
    await rp.drain_progress(run_id)
    assert writer.writes == []


# --- throttle and concurrency ------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_and_final_reports_pass_throttle(run_id, writer, clock):
    rp.report_progress("job", "images", 1, 10)
    await settle()
    clock.now += 1
    rp.report_progress("job", "images", 2, 10)
    await settle()
    clock.now += 1
    rp.report_progress("job", "images", 3, 10)
    await settle()
    assert [v["done"] for _, v in writer.writes] == [1]

    clock.now += 1
    rp.report_progress("job", "images", 10, 10)
    await settle()
    assert [v["done"] for _, v in writer.writes] == [1, 10]
    await rp.drain_progress(run_id)


@pytest.mark.asyncio
async def test_report_after_interval_writes(run_id, writer, clock):
    rp.report_progress("job", "images", 1, 100)
    await settle()
    clock.now += 10
    rp.report_progress("job", "images", 50, 100)
    await settle()
    assert [v["done"] for _, v in writer.writes] == [1, 50]
    await rp.drain_progress(run_id)


@pytest.mark.asyncio
async def test_latest_wins_while_in_flight(run_id, writer, clock):
    writer.gate = asyncio.Event()
    writer.entered = asyncio.Event()
    rp.report_progress("job", "images", 1, 100)
    await asyncio.wait_for(writer.entered.wait(), 1)
    clock.now += 10  # past the interval: reports 2 and 3 are due
    rp.report_progress("job", "images", 2, 100)
    rp.report_progress("job", "images", 3, 100)
    writer.gate.set()
    await settle()
    assert [v["done"] for _, v in writer.writes] == [1, 3]
    await rp.drain_progress(run_id)
    assert len(writer.writes) == 2


@pytest.mark.asyncio
async def test_in_flight_reports_inside_interval_wait_for_drain(run_id, writer, clock):
    writer.gate = asyncio.Event()
    writer.entered = asyncio.Event()
    rp.report_progress("job", "images", 1, 100)
    await asyncio.wait_for(writer.entered.wait(), 1)
    for done in range(2, 50):
        clock.now += 0.01  # still inside the interval
        rp.report_progress("job", "images", done, 100)
    writer.gate.set()
    await settle()
    assert [v["done"] for _, v in writer.writes] == [1]  # no follow-up write yet
    await rp.drain_progress(run_id)
    assert [v["done"] for _, v in writer.writes] == [1, 49]  # exactly one, at drain


@pytest.mark.asyncio
async def test_report_storm_with_slow_writer_stays_throttled(run_id, writer, clock, monkeypatch):
    writes: list[dict] = []

    async def slow_writer(_run_id, value):
        for _ in range(5):  # ~5 report intervals per write
            await asyncio.sleep(0)
        writes.append(value)

    monkeypatch.setattr(rp, "_writer", slow_writer)
    for done in range(1, 1001):
        clock.now += 0.001  # 1000 reports 1 ms apart, 10 s interval
        rp.report_progress("job", "images", done, 5000)
        await asyncio.sleep(0)
    await rp.drain_progress(run_id)
    assert len(writes) <= 2
    assert writes[-1]["done"] == 1000


@pytest.mark.asyncio
async def test_writer_exception_is_swallowed(run_id, writer, clock, caplog):
    writer.raise_next = 1
    rp.report_progress("job", "images", 1, 100)  # must not raise
    await settle()
    assert writer.writes == []
    assert "database down" not in caplog.text  # logged without data
    clock.now += 10
    rp.report_progress("job", "images", 2, 100)
    await settle()
    assert [v["done"] for _, v in writer.writes] == [2]
    await rp.drain_progress(run_id)


# --- value shape -------------------------------------------------------------------------


def test_unknown_unit_becomes_items():
    assert rp.build_value("job", "memes", 1, 2, None, None)["unit"] == "items"
    for unit in rp.PROGRESS_UNITS:
        assert rp.build_value("job", unit, 1, 2, None, None)["unit"] == unit
    assert rp.PROGRESS_UNITS == frozenset(
        {"images", "pairs", "descriptions", "profiles", "files", "items"}
    )


def test_done_clamped_to_total():
    v = rp.build_value("job", "images", 15, 10, None, None)
    assert v["done"] == 10 and v["total"] == 10


def test_total_none_kept():
    v = rp.build_value("job", "images", 15, None, None, None)
    assert v["done"] == 15 and v["total"] is None


def test_total_zero_kept():
    v = rp.build_value("job", "images", 0, 0, None, None)
    assert v["done"] == 0 and v["total"] == 0


def test_negative_numbers_become_zero():
    v = rp.build_value("job", "images", -3, -1, -2.5, None)
    assert v["done"] == 0 and v["total"] == 0 and v["rate_per_min"] == 0


def test_label_none_falls_back():
    assert rp.build_value(None, "images", 1, 2, None, None)["label"] == "batch"


@pytest.mark.asyncio
async def test_step_bypasses_throttle_and_merges_with_progress(run_id, writer, clock):
    rp.report_progress("job", "images", 5, 100, 12.34)
    await settle()
    clock.now += 1  # inside the throttle interval
    rp.report_step(2, 8, "extract_text_from_memes")
    await settle()
    assert len(writer.writes) == 2
    merged = writer.writes[1][1]
    assert merged["step"] == {"index": 2, "count": 8, "label": "extract_text_from_memes"}
    assert merged["done"] == 5 and merged["total"] == 100 and merged["label"] == "job"
    await rp.drain_progress(run_id)


@pytest.mark.asyncio
async def test_step_only_value(run_id, writer):
    rp.report_step(1, 8, "ingest_hash_dedup")
    await settle()
    (_, value), = writer.writes
    assert value["label"] == "ingest_hash_dedup"
    assert value["unit"] == "items"
    assert value["done"] is None and value["total"] is None
    assert value["step"] == {"index": 1, "count": 8, "label": "ingest_hash_dedup"}
    await rp.drain_progress(run_id)


@pytest.mark.asyncio
async def test_drain_delivers_last_value_and_clears_state(run_id, writer, clock):
    rp.report_progress("job", "images", 1, 100)
    await settle()
    clock.now += 1
    rp.report_progress("job", "images", 7, 100)  # throttled, not yet written
    await settle()
    assert [v["done"] for _, v in writer.writes] == [1]
    await rp.drain_progress(run_id)
    assert [v["done"] for _, v in writer.writes] == [1, 7]
    assert run_id not in rp._states


@pytest.mark.asyncio
async def test_drain_awaits_in_flight_write(run_id, writer):
    writer.gate = asyncio.Event()
    writer.entered = asyncio.Event()
    rp.report_progress("job", "images", 1, 100)
    await asyncio.wait_for(writer.entered.wait(), 1)
    asyncio.get_running_loop().call_later(0.01, writer.gate.set)
    await rp.drain_progress(run_id)
    assert [v["done"] for _, v in writer.writes] == [1]
    assert run_id not in rp._states


@pytest.mark.asyncio
async def test_drain_timeout_cancels_and_never_raises(run_id, writer):
    writer.gate = asyncio.Event()  # never released
    writer.entered = asyncio.Event()
    rp.report_progress("job", "images", 1, 100)
    await asyncio.wait_for(writer.entered.wait(), 1)
    await rp.drain_progress(run_id, timeout=0.01)
    assert writer.writes == []
    assert run_id not in rp._states
    assert rp._tasks == set()


@pytest.mark.asyncio
async def test_drain_unknown_run_is_noop(clock, writer, config):
    rp._states.clear()
    await rp.drain_progress(uuid.uuid4())
    assert writer.writes == []


@pytest.mark.asyncio
async def test_label_and_values_contain_only_allowed_keys(run_id, writer):
    rp.report_progress("job", "images", 1, 100, 3.0)
    rp.report_step(1, 2, "step")
    await rp.drain_progress(run_id)
    assert writer.writes
    for _, value in writer.writes:
        assert set(value) <= ALLOWED_KEYS
        if "step" in value:
            assert set(value["step"]) == {"index", "count", "label"}


def test_settings_block_present():
    from config.settings import settings

    assert settings.BATCH_PROGRESS.ENABLED is True
    assert settings.BATCH_PROGRESS.MIN_INTERVAL_SECONDS == 10
