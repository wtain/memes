"""ProgressTracker: console output stays as it was, and every step is also reported to the run row."""
import uuid

import pytest

import batch.utils.progress as progress_mod
from batch.utils.progress import ProgressTracker


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(progress_mod.time, "perf_counter", c)
    return c


@pytest.fixture
def in_run():
    token = progress_mod.current_run_id.set(uuid.uuid4())
    yield
    progress_mod.current_run_id.reset(token)


@pytest.fixture
def reports(monkeypatch):
    calls = []
    monkeypatch.setattr(
        progress_mod, "report_progress", lambda *args, **kw: calls.append((args, kw)), raising=False
    )
    return calls


def test_console_output_unchanged(clock, capsys):
    t = ProgressTracker(total=10, report_every=2)
    for _ in range(4):
        clock.advance(3)
        t.mark_done()
    t.skip()
    clock.advance(3)
    t.summary()
    out = capsys.readouterr().out.splitlines()
    assert out == [
        "[2/~10] elapsed=6s  avg=3.0s/img  eta≈24s",
        "[4/~10] elapsed=12s  avg=3.0s/img  eta≈18s",
        "Done: 4/9 images in 15s (avg 3.8s/img)",
    ]


def test_mark_done_reports_done_and_effective_total(clock, reports):
    t = ProgressTracker(total=10, label="some_job", unit="images")
    clock.advance(3)
    t.mark_done()
    t.skip()
    assert reports[0][0][:4] == ("some_job", "images", 1, 10)
    assert reports[1][0][:4] == ("some_job", "images", 1, 9)


def test_rate_window_stays_bounded_and_rate_is_exact(clock, reports, in_run):
    # Regression (I1): the rate must not scan an ever-growing window per item.
    t = ProgressTracker(total=10**6, report_every=10**9, window_secs=10.0)
    for _ in range(5000):
        clock.advance(1)
        t.mark_done()
    assert len(t._window) <= 11
    assert reports[-1][0][4] == pytest.approx(60.0)


def test_no_rate_computed_outside_a_run(clock, reports):
    t = ProgressTracker(total=10, report_every=10**9, window_secs=10.0)
    for _ in range(5):
        clock.advance(1)
        t.mark_done()
    assert reports[-1][0][4] is None


def test_rate_per_min_from_window(clock, reports, in_run):
    t = ProgressTracker(total=100, unit="images")
    for _ in range(4):
        clock.advance(3)
        t.mark_done()
    assert reports[-1][0][4] == pytest.approx(20.0)


def test_no_rate_before_first_sample(clock, reports):
    t = ProgressTracker(total=10)
    t.skip()
    assert reports[0][0][3] == 9
    assert reports[0][0][4] is None


def test_summary_reports_final(clock, reports):
    t = ProgressTracker(total=5, unit="images")
    for _ in range(3):
        clock.advance(2)
        t.mark_done()
    t.skip()
    t.skip()
    reports.clear()
    t.summary()
    assert reports[-1][0][2:4] == (3, 3)


def test_default_label_and_unit(clock, reports):
    ProgressTracker(total=1).mark_done()
    assert reports[0][0][:2] == (None, "items")


def test_tracker_without_current_run_is_unaffected(clock, capsys):
    t = ProgressTracker(total=2, report_every=1)
    clock.advance(1)
    t.mark_done()
    t.skip()
    t.summary()
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "[1/~2] elapsed=1s  avg=1.0s/img  eta≈1s"
