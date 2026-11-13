"""P2-T4: the files gate of `sync.promote` (design P2 "Apply" step 2; interfaces.md a, `files-promoted.json`).

P3 writes `runs/<runid>/files-promoted.json` after a files apply. P2 accepts it only when it is from a non-dry run folder, belongs
to this environment, is younger than `--max-report-age` (default 2 h), and its `manifest_sha256` equals the one P2 recomputes from
the promotable rows now (a `fix_image_formats` run between report and apply changes the rows and invalidates the report).
`classify` then says, per image that would be written, whether its file is on prod: the report entry must carry the same
`(id, filename, content_hash)` as the dev row. Pure functions plus one directory scan; no database, no network.
"""
from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from sync import report
from sync.config import is_dry

FILE_NAME = "files-promoted.json"
DEFAULT_MAX_AGE_S = 2 * 3600
FUTURE_SLACK_S = 300

# statuses of `check`
VALID, ABSENT, MALFORMED = "valid", "absent", "malformed"
ENV_MISMATCH, STALE, MANIFEST_MISMATCH = "env_mismatch", "stale", "manifest_mismatch"

GATE_MISS = "files_gate_miss"


@dataclass
class FilesReport:
    run_id: str
    env: str
    finished_at: str
    complete: bool
    manifest_sha256: str
    images: dict = field(default_factory=dict)  # id -> (filename, content_hash)
    left_out: dict = field(default_factory=dict)  # id -> report reason code
    path: Path | None = None


def parse(data: Any, path: Path | None = None) -> FilesReport | None:
    """A `FilesReport` from the decoded JSON, or None when the shape is wrong."""
    try:
        images = {str(i["id"]): (str(i["filename"]), i.get("content_hash")) for i in data["images"]}
        left_out = {str(i["id"]): reason_code(str(i.get("reason", ""))) for i in data.get("left_out", [])}
        return FilesReport(str(data["run_id"]), str(data["env"]), str(data["finished_at"]), bool(data.get("complete")),
                           str(data["manifest_sha256"]), images, left_out, path)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def reason_code(raw: str) -> str:
    """Map a `files-promoted.json` `left_out[].reason` to a report reason code."""
    if raw.startswith("name_"):
        return "name_problem"
    return raw if raw in report.REASON_CODES else GATE_MISS


def _finished(report_: FilesReport) -> _dt.datetime | None:
    try:
        return _dt.datetime.strptime(report_.finished_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return None


def candidates(runs_dir: Path, own_folder: Path | None = None) -> list:
    """Non-dry run folders holding the file, this run's own folder first, then the newest by folder name (run ids sort by time)."""
    found = []
    if own_folder is not None and (Path(own_folder) / FILE_NAME).is_file() and not is_dry(Path(own_folder)):
        found.append(Path(own_folder) / FILE_NAME)
    if runs_dir.is_dir():
        for folder in sorted((p for p in runs_dir.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True):
            path = folder / FILE_NAME
            if path.is_file() and not is_dry(folder) and path not in found:
                found.append(path)
    return found


def load_newest(runs_dir: Path, own_folder: Path | None = None) -> tuple:
    """`(FilesReport or None, status)`: the first candidate that parses, else `absent` / `malformed`."""
    paths = candidates(runs_dir, own_folder)
    if not paths:
        return None, ABSENT
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        parsed = parse(data, path)
        if parsed is not None:
            return parsed, VALID
    return None, MALFORMED


def check(files_report: FilesReport | None, env: str, now: _dt.datetime, max_age_s: float, current_manifest_sha: str,
          status: str = VALID) -> str:
    """`valid` or the reason it cannot be used."""
    if files_report is None:
        return status if status != VALID else ABSENT
    if files_report.env != env:
        return ENV_MISMATCH
    finished = _finished(files_report)
    if finished is None:
        return MALFORMED
    age = (now - finished).total_seconds()
    if age > max_age_s or age < -FUTURE_SLACK_S:
        return STALE
    if files_report.manifest_sha256 != current_manifest_sha:
        return MANIFEST_MISMATCH
    return VALID


def classify(files_report: FilesReport, dev_rows: Iterable) -> dict:
    """reason code -> ids, for the given dev rows (objects with `id`, `filename`, `content_hash`) that cannot be written because
    their file is not verified on prod. An image whose dev row has no content hash is compared by filename only."""
    out: dict = {}
    for row in dev_rows:
        entry = files_report.images.get(row.id)
        if entry is not None:
            filename, content_hash = entry
            if filename == row.filename and (row.content_hash is None or content_hash == row.content_hash):
                continue
            out.setdefault(GATE_MISS, []).append(row.id)
        else:
            out.setdefault(reason_code(files_report.left_out.get(row.id, GATE_MISS)), []).append(row.id)
    return out
