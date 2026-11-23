import time
from collections import deque

from batch.utils.run_progress import current_run_id, report_progress


class ProgressTracker:
    def __init__(
        self,
        total: int,
        report_every: int = 10,
        report_interval_secs: float = 0.0,
        window_secs: float = 120.0,
        label: str | None = None,
        unit: str = "items",
    ):
        self._label = label
        self._unit = unit
        self._total = total
        self._skip_count = 0
        self._done = 0
        self._report_every = report_every
        self._report_interval_secs = report_interval_secs
        self._window_secs = window_secs
        self._start_time = time.perf_counter()
        self._last_report_time = self._start_time
        self._window: deque[float] = deque()

    def _rate_per_min(self, now: float) -> float | None:
        """Items per minute from the same windowed average as the console line (read only)."""
        if current_run_id.get() is None:
            return None  # no run row to report to: shell runs pay nothing
        # O(1) amortised: stale entries are popped from the left (the console print applies the
        # same cutoff, so pruning earlier never changes its average).
        cutoff = now - self._window_secs
        while self._window and self._window[0] < cutoff:
            self._window.popleft()
        if len(self._window) >= 2:
            avg = (self._window[-1] - self._window[0]) / (len(self._window) - 1)
        elif self._done > 0:
            avg = (now - self._start_time) / self._done
        else:
            return None
        return 60.0 / avg if avg > 0 else None

    def _report(self, now: float) -> None:
        report_progress(
            self._label, self._unit, self._done, self._effective_total(), self._rate_per_min(now)
        )

    def skip(self) -> None:
        self._skip_count += 1
        self._report(time.perf_counter())

    def mark_done(self) -> None:
        self._done += 1
        now = time.perf_counter()
        self._window.append(now)
        self._report(now)
        count_trigger = self._done % self._report_every == 0
        time_gate_ok = (
            self._report_interval_secs == 0
            or (now - self._last_report_time) >= self._report_interval_secs
        )
        if count_trigger and time_gate_ok:
            self._print_progress(now)
            self._last_report_time = now

    def _effective_total(self) -> int:
        return self._total - self._skip_count

    def _print_progress(self, now: float) -> None:
        effective = self._effective_total()
        elapsed = now - self._start_time

        cutoff = now - self._window_secs
        while self._window and self._window[0] < cutoff:
            self._window.popleft()

        if len(self._window) >= 2:
            window_elapsed = self._window[-1] - self._window[0]
            avg = window_elapsed / (len(self._window) - 1)
        elif self._done > 0:
            avg = elapsed / self._done
        else:
            avg = 0.0

        remaining = max(0, effective - self._done)
        eta = avg * remaining

        print(
            f"[{self._done}/~{effective}] "
            f"elapsed={_fmt(elapsed)}  "
            f"avg={avg:.1f}s/img  "
            f"eta≈{_fmt(eta)}"
        )

    def summary(self) -> None:
        effective = self._effective_total()
        now = time.perf_counter()
        elapsed = now - self._start_time
        self._report(now)
        if self._done > 0:
            avg = elapsed / self._done
            print(f"Done: {self._done}/{effective} images in {_fmt(elapsed)} (avg {avg:.1f}s/img)")
        else:
            print(f"Done: 0/{effective} images in {_fmt(elapsed)}")


def _fmt(seconds: float) -> str:
    s = int(seconds)
    if s >= 60:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s}s"