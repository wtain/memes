"""P6: the growth trend store of check A1/A2 (design P6 "A1 growth alarm"; interfaces.md a `verify.json`, b `sync.verify.growth.*`).

The store is the set of earlier `<state dir>/runs/*/verify.json` files (the last 30 of this environment). Each carries
`growth.metrics`: a flat `{metric name: number}` map (`meme_impressions.rows`, `meme_impressions.bytes`, `bug_reports.files`, ...).
A daily growth rate is the change between two consecutive samples divided by the days between them (at least one hour, so two runs a
minute apart cannot blow a rate up). The alarm is the current rate (previous sample to now) above `factor` times the median of the
rates of the trailing `window_days`, and above a floor (`min_daily_rows` / `min_daily_bytes`) so that a quiet table that doubles from
ten rows is not an alarm. Absolute ceilings (`hard_rows`, `hard_bytes`) are errors whatever the history says. Fewer than `min_samples`
earlier samples: the trend is `building`, never an alarm.

Pure: standard library only, no database, no network (the caller passes the current values).
"""
from __future__ import annotations

import datetime as _dt
import json
import statistics
from pathlib import Path

HISTORY_LIMIT = 30
MIN_DAYS = 1.0 / 24.0
DEFAULTS = {"factor": 5, "window_days": 7, "min_samples": 3, "min_daily_rows": 1000, "min_daily_bytes": 50 * 1024 * 1024,
            "hard_rows": 5_000_000, "hard_bytes": 2 * 1024 ** 3}
BUILDING, GROWTH_OK, GROWTH_WARN, GROWTH_ERROR = "building", "ok", "warn", "error"


def config(settings_get) -> dict:
    """The thresholds: tracked `sync.verify.growth.*` over the defaults. `settings_get(path, default)` reads the settings."""
    out = {}
    for key, default in DEFAULTS.items():
        value = settings_get(f"verify.growth.{key}", default)
        out[key] = value if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else default
    return out


def parse_time(text) -> _dt.datetime | None:
    if not isinstance(text, str):
        return None
    try:
        return _dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def load_history(state_dir: Path, limit: int = HISTORY_LIMIT, exclude_run: str | None = None) -> list:
    """`[{run_id, at, metrics}]` of the last `limit` readable verify.json files, oldest first. A missing, unreadable or foreign file is
    left out (a history that cannot be read means a trend that is still `building`, the safe direction)."""
    out = []
    runs = Path(state_dir) / "runs"
    if not runs.is_dir():
        return out
    for path in runs.glob("*/verify.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict) or doc.get("run_id") == exclude_run:
            continue
        at = parse_time(doc.get("ran_at"))
        metrics = (doc.get("growth") or {}).get("metrics") if isinstance(doc.get("growth"), dict) else None
        if at is None or not isinstance(metrics, dict):
            continue
        clean = {k: v for k, v in metrics.items() if isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool)}
        facts = doc["growth"].get("facts") if isinstance(doc["growth"].get("facts"), dict) else {}
        out.append({"run_id": str(doc.get("run_id")), "at": at, "metrics": clean, "facts": facts})
    out.sort(key=lambda s: (s["at"], s["run_id"]))
    return out[-limit:]


def _rates(series: list) -> list:
    """`[(end time, daily rate)]` between consecutive `(time, value)` samples."""
    rates = []
    for (t1, v1), (t2, v2) in zip(series, series[1:]):
        days = max((t2 - t1).total_seconds() / 86400.0, MIN_DAYS)
        rates.append((t2, (v2 - v1) / days))
    return rates


def _kind(metric: str) -> str:
    return "bytes" if metric.endswith(".bytes") else "rows"


def evaluate(metric: str, history: list, now: _dt.datetime, value: float, cfg: dict) -> dict:
    """One metric: `{value, daily, baseline_daily, status}`; status is building / ok / warn / error."""
    series = [(s["at"], s["metrics"][metric]) for s in history if metric in s["metrics"]]
    kind = _kind(metric)
    hard = cfg["hard_bytes"] if kind == "bytes" else cfg["hard_rows"]
    floor = cfg["min_daily_bytes"] if kind == "bytes" else cfg["min_daily_rows"]
    out = {"value": value, "daily": None, "baseline_daily": None, "status": GROWTH_OK}
    if series:
        out["daily"] = round(_rates(series + [(now, value)])[-1][1], 2)
    if value > hard:
        out["status"] = GROWTH_ERROR
        return out
    if len(series) < cfg["min_samples"]:
        out["status"] = BUILDING
        return out
    window_start = now - _dt.timedelta(days=cfg["window_days"])
    past = [r for t, r in _rates(series) if t >= window_start]
    if past:
        out["baseline_daily"] = round(statistics.median(past), 2)
        base = max(out["baseline_daily"], 0.0)
        if out["daily"] is not None and out["daily"] > floor and out["daily"] > cfg["factor"] * max(base, 1e-9):
            out["status"] = GROWTH_WARN
    return out


def trend(history: list, now: _dt.datetime, current: dict, cfg: dict, facts: dict | None = None) -> dict:
    """`growth{}` of verify.json: `{samples, metrics: {name: value}, trend: {name: evaluate(...)}, facts}`; `facts` are plain numbers the
    next run compares with (backup size, class P rows) that are not growth metrics themselves."""
    return {"samples": len(history), "metrics": {k: v for k, v in sorted(current.items())},
            "trend": {k: evaluate(k, history, now, v, cfg) for k, v in sorted(current.items())}, "facts": dict(facts or {})}
