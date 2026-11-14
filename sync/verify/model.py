"""P6: the check model of `python -m sync.verify` (design P6/P7 part A; interfaces.md a, i).

Every check returns a `Check`: `{id, status, severity, detail, fix, skipped_reason}`.

* `status`: `ok`, `warn`, `error` or `skipped` (the outcome of this run);
* `severity`: `error`, `warn` or `info`, the class of the check from the design table (what a failure of it counts as); an
  informational finding (left behind on prod) is a status `ok` check whose detail carries the count;
* `detail`: a code-like token string of at most 80 characters (`sync.report.scrub_detail`: lower-case `[a-z0-9_.:=,-]`, so row
  content, names with spaces or paths cannot get through; anything else becomes `[redacted]`);
* `fix`: the command that repairs it, a fixed text from this code (validated, never built from data);
* `skipped_reason`: a code token, set exactly when the status is `skipped`.

Exit codes of the tool: 0 clean, 4 any `error`, 3 only warnings with `--fail-on warn`, 2 could not run, 1 other.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from sync import report
from sync.config import EXIT_OK, EXIT_VERIFY
from sync.config import EXIT_GUARD

OK, WARN, ERROR, SKIPPED = "ok", "warn", "error", "skipped"
STATUSES = (OK, WARN, ERROR, SKIPPED)
SEVERITIES = ("error", "warn", "info")

_FIX = re.compile(r"^[A-Za-z0-9_.:=,/<>();+ -]{0,200}$")
_REASON = re.compile(r"^[a-z0-9_.:=,-]{1,60}$")

# every check id of the design, in report order (S4b is the account probe of S4)
CHECK_IDS = ("S0", "D1", "D2", "D3", "D4", "D5", "D6", "F1", "F2", "F3", "F4", "P1", "P2", "A1", "A2", "A3", "A4",
             "S1", "S2", "S3", "S4", "S4b", "S5", "S6", "S7", "S8")


class CheckError(ValueError):
    """A malformed check (a bug in a check function, never data)."""


@dataclass(frozen=True)
class Check:
    id: str
    status: str
    severity: str
    detail: str = ""
    fix: str = ""
    skipped_reason: str | None = None

    def __post_init__(self):
        if self.id not in CHECK_IDS:
            raise CheckError(f"unknown check id {self.id!r}")
        if self.status not in STATUSES or self.severity not in SEVERITIES:
            raise CheckError("status or severity is not one of the known codes")
        # a long list of codes is cut at the 80 characters `report.scrub_detail` accepts (a cut code beats a `[redacted]` that hides all of them)
        text = self.detail if isinstance(self.detail, str) and report._DETAIL.match(self.detail[:80]) else self.detail
        object.__setattr__(self, "detail", report.scrub_detail(text[:80] if isinstance(text, str) else text))
        if not _FIX.match(self.fix):
            raise CheckError("fix must be a plain command text")
        if (self.status == SKIPPED) != (self.skipped_reason is not None):
            raise CheckError("skipped_reason is set exactly when the status is skipped")
        if self.skipped_reason is not None and not _REASON.match(self.skipped_reason):
            raise CheckError("skipped_reason must be a code token")

    def as_json(self) -> dict:
        return {"id": self.id, "status": self.status, "severity": self.severity, "detail": self.detail, "fix": self.fix,
                "skipped_reason": self.skipped_reason}

    @property
    def is_problem(self) -> bool:
        return self.status in (WARN, ERROR)


def ok(check_id: str, severity: str, detail: str = "ok", fix: str = "") -> Check:
    return Check(check_id, OK, severity, detail, fix)


def fail(check_id: str, severity: str, detail: str, fix: str = "") -> Check:
    """A failed check: `error` for a severity `error`, `warn` for a severity `warn` (an `info` check never fails)."""
    status = ERROR if severity == "error" else WARN
    return Check(check_id, status, severity, detail, fix)


def skipped(check_id: str, severity: str, reason: str, fix: str = "") -> Check:
    return Check(check_id, SKIPPED, severity, "", fix, reason)


def downgrade(check: Check, detail_suffix: str) -> Check:
    """An error found while a dev batch job runs is only a warning (`unstable: dev job running`)."""
    if check.status != ERROR:
        return check
    detail = (check.detail + "," + detail_suffix).strip(",")
    return Check(check.id, WARN, check.severity, detail, check.fix)


def summarize(checks: Iterable[Check]) -> dict:
    counts = {s: 0 for s in STATUSES}
    for c in checks:
        counts[c.status] += 1
    return counts


def exit_code(checks: Iterable[Check], fail_on_warn: bool = False) -> int:
    checks = list(checks)
    if any(c.status == ERROR for c in checks):
        return EXIT_VERIFY
    if fail_on_warn and any(c.status == WARN for c in checks):
        return EXIT_GUARD
    return EXIT_OK


def token(value: Any, default: str = "unknown") -> str:
    """A value reduced to a lower-case code token (for names that come from outside, e.g. a mount name)."""
    text = re.sub(r"[^a-z0-9_.:=-]", "_", str(value).lower())[:30].strip("_")
    return text or default


def guarded(check_id: str, severity: str, fn: Callable[[], Any], *, no_privilege: tuple = ("42501",)) -> list:
    """Run one check function; a check that fails to run becomes a check (never an exception): a missing privilege or an
    unreachable gate is `skipped` with its reason, anything else an `error` naming the exception type only (a message could quote a row)."""
    from sync.connect import DbError
    from sync.files.transport import GateFailure

    try:
        result = fn()
    except DbError as exc:
        if exc.sqlstate in no_privilege:
            return [skipped(check_id, severity, "no_privilege")]
        return [Check(check_id, ERROR if severity == "error" else WARN, severity, exc.code)]
    except GateFailure as exc:
        return [skipped(check_id, severity, "gate_" + token(getattr(exc, "code", "failed")))]
    except Exception as exc:  # a bug or a surprise: the type only
        return [Check(check_id, ERROR if severity == "error" else WARN, severity, "crashed:" + token(type(exc).__name__))]
    return result if isinstance(result, list) else [result]
