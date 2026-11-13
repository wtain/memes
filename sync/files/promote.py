"""P3-T5: `python -m sync.files promote` (design P3 "promote flow"; interfaces.md a, i, k).

Dry run by default: handshake (H1 dev side, H6 gate), the promotable rows from dev (read only), the gate's `list`, the diff, the
guards, and the report of interfaces.md k (`report.json`, `report.md`, stdout summary, `dry` marker). Nothing is written to prod
and no file in the run folder is an input for any other tool.

`--apply` re-plans, refuses (exit 3) when `--plan-hash` differs or a guard is tripped, then sends the batches
(tar -> sftp upload -> `receive` -> `verify` -> `commit`), resuming a batch the gate already holds, and writes
`runs/<runid>/files-promoted.json` for P2. Nothing is ever deleted on prod (decision 27): files prod has and dev does not promote
are `left_behind` in the report.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import json
import math
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from sync import connect, report
from sync.config import (ENVIRONMENTS, EXIT_FAILURE, EXIT_GUARD, EXIT_OK, EXIT_PRECONDITION, PreconditionError, SyncError,
                         SyncSettings, atomic_write, exclusive_lock, load_settings, mark_dry, new_run_id, run_dir)
from sync.files import batches as batches_mod
from sync.files import diff as diff_mod
from sync.files import manifest as manifest_mod
from sync.files.transport import GateFailure, GateReply, SshTransport, Transport

TOOL = "sync.files promote"
SECTION = "files"
ENTRY = "images"
OVER_MEMBER_CAP = "over_member_cap"
GATE_MISS = "files_gate_miss"
MB = 1024 * 1024
ASSUMED_BYTES_PER_S = 50_000_000 / 8  # the design's first-seed estimate: 50 Mbit/s
DEFAULT_BATCH_MB = 300


class UsageProblem(PreconditionError):
    """A bad flag value or an unusable local precondition (exit 2)."""


# ---------------------------------------------------------------------------------------------------------------------------------
# command line

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m sync.files", description="Files of the dev/prod sync (additions and replacements only).")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("promote", help="promote the image files of the promotable images to prod (dry run unless --apply)")
    p.add_argument("--env", required=True, choices=ENVIRONMENTS)
    p.add_argument("--apply", action="store_true", help="send the files (otherwise a dry run that writes only the report folder)")
    p.add_argument("--deep", action="store_true", help="re-read every dev file and let the gate re-hash its files before sending")
    p.add_argument("--batch-mb", type=float, default=None, help="tar batch target in MB, 150 to 500 (default sync.files.batch_mb, 300)")
    p.add_argument("--yes", action="store_true", help="with --apply: skip only the typed confirmation '<env> <prod database>'")
    p.add_argument("--plan-hash", default=None, help="with --apply: refuse (exit 3) if the re-planned hash differs from the dry run's")
    p.add_argument("--top", type=int, default=None, help="ids listed per category in report.md and the summary (default sync.report.top_n)")
    p.add_argument("--run-id", default=None, help="run id (sync.run passes it to its children)")
    p.add_argument("--json", dest="json_path", default=None, metavar="PATH", help="also write report.json to PATH ('-' for stdout)")
    return parser


# ---------------------------------------------------------------------------------------------------------------------------------
# plan

class _Recording:
    """Wraps a transport so the one gate `handshake` reply of `connect.handshake` (step H6) can be read afterwards."""

    def __init__(self, inner: Transport):
        self.inner, self.reply = inner, None

    def handshake(self) -> dict:
        self.reply = self.inner.handshake()
        return self.reply


@dataclass
class Plan:
    manifest: manifest_mod.Manifest
    diff: diff_mod.Diff
    section: report.Section
    gate: dict
    caps: dict
    blocking: list
    batch_mb: float
    preconditions: list = field(default_factory=list)

    @property
    def tripped(self) -> list:
        return [g for g in self.section.guards if g.tripped]


def _gate_caps(gate: dict) -> dict:
    caps = gate.get("caps") if isinstance(gate, dict) else None
    caps = caps if isinstance(caps, dict) else {}
    return {
        "max_member_mb": float(caps.get("max_member_mb", batches_mod.DEFAULT_MAX_MEMBER_MB)),
        "max_members": int(caps.get("max_members", batches_mod.DEFAULT_MAX_MEMBERS)),
        "max_batch_mb": float(caps.get("max_batch_mb", 600)),
        "replace_day_min": int(caps.get("replace_day_min", 200)),
        "replace_day_pct": float(caps.get("replace_day_pct", 5)),
    }


def _blocking(d: diff_mod.Diff) -> list:
    """Anomalies on prod that stop an apply before any write: a manifest name that sits on anything but a plain regular file,
    and entries the gate lists that cannot be parsed or have an invalid name (the owner cleans them up; P6 F4 reports them too)."""
    return [a for a in d.anomalies if a.in_manifest or a.kind in ("invalid", "malformed")]


def _replace_cap_limit(caps: dict, prod_regular_count: int) -> int:
    """The gate's per-day replace cap: max(replace_day_min, ceil(replace_day_pct % of the regular files in the images dir))."""
    return max(caps["replace_day_min"], int(math.ceil(caps["replace_day_pct"] * prod_regular_count / 100.0)))


def make_plan(settings: SyncSettings, args: argparse.Namespace, dev: Any, transport: _Recording, base_dir: str) -> Plan:
    """Everything up to and including the report model; reads dev (read only) and the gate's `list`; writes nothing."""
    gate = transport.reply or {}
    caps = _gate_caps(gate)
    batch_mb = float(args.batch_mb if args.batch_mb is not None else settings.get("files.batch_mb", DEFAULT_BATCH_MB))
    if not (batches_mod.MIN_BATCH_MB <= batch_mb <= batches_mod.MAX_BATCH_MB):
        raise UsageProblem(f"--batch-mb must be between {batches_mod.MIN_BATCH_MB} and {batches_mod.MAX_BATCH_MB}")
    target_mb = min(batch_mb, caps["max_batch_mb"])

    rows = manifest_mod.load_promotable_rows(dev.raw)
    mf = manifest_mod.build_manifest(rows, base_dir)
    cap_bytes = int(caps["max_member_mb"] * MB)
    for name in [n for n, e in mf.entries.items() if e.size > cap_bytes]:  # excluded up front, listed as skipped_with_reason
        e = mf.entries.pop(name)
        mf.left_out.append(manifest_mod.LeftOut(e.id, e.filename, OVER_MEMBER_CAP, OVER_MEMBER_CAP))

    remote = diff_mod.parse_list(transport.inner.list())
    d = diff_mod.diff_manifest(mf, remote)
    before = len(mf.entries)
    manifest_mod.verify_hashes(mf, None if args.deep else [e.filename for e in d.to_send])  # every packed file is read once here
    if len(mf.entries) != before:
        d = diff_mod.diff_manifest(mf, remote)

    guards = diff_mod.guards(mf, d.prod_regular_count)
    limit = _replace_cap_limit(caps, d.prod_regular_count)
    guards.append(report.Guard("gate_replace_cap", d.replaced_count > limit, d.replaced_count, limit))
    blocking = _blocking(d)
    guards.append(report.Guard("prod_anomaly_blocking", bool(blocking), len(blocking), 0))

    try:
        planned = batches_mod.plan_batches(settings.env, d.to_send, target_mb, max_members=caps["max_members"],
                                           max_member_mb=caps["max_member_mb"], target_bytes=int(target_mb * MB))
    except batches_mod.PlanError as exc:
        raise UsageProblem(f"cannot plan batches: {exc}") from None
    wire = sum(e.size for e in d.to_send)
    estimate = {"duration_s": round(wire / ASSUMED_BYTES_PER_S + 3 * len(planned), 1), "wire_bytes": wire}
    flags = {"deep": True} if args.deep else {}
    section = report.Section(name=SECTION, tool=TOOL, entries=[diff_mod.to_entry(mf, d, ENTRY)], guards=guards, flags=flags,
                             manifest_hash=mf.manifest_sha256 or None, estimate=estimate)
    free = (gate.get("free") or {}).get("files") or {}
    pre = [
        {"id": "gate_caps", "ok": True, "detail": f"member_mb={caps['max_member_mb']:g},members={caps['max_members']},batch_mb={caps['max_batch_mb']:g}"},
        {"id": "batches", "ok": True, "detail": f"count={len(planned)},target_mb={target_mb:g}"},
        {"id": "prod_free_space", "ok": True, "detail": f"free_pct={free.get('free_pct', 'unknown')}".lower()},
    ]
    return Plan(mf, d, section, gate, caps, blocking, target_mb, pre)


def _apply_flags(args: argparse.Namespace) -> list:
    flags = []
    if args.deep:
        flags.append("--deep")
    if args.batch_mb is not None:
        flags += ["--batch-mb", f"{args.batch_mb:g}"]
    return flags


# ---------------------------------------------------------------------------------------------------------------------------------
# apply

class _Failure(Exception):
    def __init__(self, code: str, exit_code: int):
        super().__init__(code)
        self.code, self.exit_code = code, exit_code


@dataclass
class Outcome:
    added: int = 0
    replaced: int = 0
    batches_done: int = 0
    failure: _Failure | None = None
    dropped: list = field(default_factory=list)  # LeftOut found while packing
    images: list = field(default_factory=list)  # ManifestEntry verified on prod by the final listing
    missed: list = field(default_factory=list)  # ManifestEntry not on prod after the run
    final_listing: bool = False


def _reply_failure(reply: GateReply, outcome: Outcome, what: str) -> _Failure:
    code = reply.reason or f"{what}_exit_{reply.code}"
    # a refusal before this batch published anything (cap, free space) is a guard (3) only while nothing was committed yet
    exit_code = EXIT_GUARD if reply.code == 3 and outcome.batches_done == 0 else EXIT_FAILURE
    return _Failure(f"{what}:{code}", exit_code)


def _build_tar(batch: batches_mod.Batch, directory: str) -> str:
    path = os.path.join(directory, f"{batch.id}.tar")
    try:
        with open(path, "wb") as out:
            batches_mod.write_tar(batch, out)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(path)
        raise
    return path


_PACK_REASON = {"not_regular_file": "missing_file", "unreadable": "missing_file",
                "changed_during_read": "hash_mismatch", "content_hash_differs": "hash_mismatch"}


def _send_batch(env: str, batch: batches_mod.Batch | None, transport: Transport, outcome: Outcome, mf: manifest_mod.Manifest) -> None:
    """receive -> (upload when the gate has neither the batch nor the tar) -> verify -> commit. Raises _Failure."""
    with tempfile.TemporaryDirectory(prefix="sync-files-") as tmp:
        while batch is not None and batch.entries:
            reply = transport.receive(batch.id)  # first: the gate may hold this batch (killed run) or its tar already
            if reply.ok or reply.reason == "batch_exists":
                break
            if reply.reason != "no_such_upload":
                raise _reply_failure(reply, outcome, "receive")
            try:
                path = _build_tar(batch, tmp)
            except batches_mod.PackError as exc:  # a dev file moved under us: drop it, rebuild the batch without it
                entry = next(e for e in batch.entries if e.filename == exc.name)
                outcome.dropped.append(manifest_mod.LeftOut(entry.id, entry.filename, _PACK_REASON.get(exc.rule, "hash_mismatch"), exc.rule))
                mf.entries.pop(entry.filename, None)
                rest = [e for e in batch.entries if e.filename != exc.name]
                batch = batches_mod.Batch(batches_mod.batch_id(env, rest), tuple(rest), sum(e.size for e in rest)) if rest else None
                continue
            transport.upload(batch.id, path)
            with contextlib.suppress(OSError):
                os.unlink(path)
            reply = transport.receive(batch.id)
            if not reply.ok and reply.reason != "batch_exists":
                raise _reply_failure(reply, outcome, "receive")
            break
        if batch is None or not batch.entries:
            return
        verified = transport.verify(batch.id, batches_mod.sums_payload(batch))
        if not verified.ok:
            raise _reply_failure(verified, outcome, "verify")
        committed = transport.commit(batch.id)
        if not committed.ok:
            raise _reply_failure(committed, outcome, "commit")
        try:
            data = committed.json()
            replaced, published = int(data.get("replaced", 0)), int(data.get("committed", 0))
        except (GateFailure, ValueError, TypeError):
            replaced, published = 0, 0
        outcome.replaced += replaced
        outcome.added += max(0, published - replaced)
        outcome.batches_done += 1


def apply_plan(settings: SyncSettings, args: argparse.Namespace, plan: Plan, transport: Transport) -> Outcome:
    outcome = Outcome()
    mf, d = plan.manifest, plan.diff
    try:
        sweep = transport.sweep()  # the gate clears debris older than six hours; a resumable batch younger than that is kept
        if not sweep.ok:
            raise _reply_failure(sweep, outcome, "sweep")
        if args.deep:  # adopt what prod already holds: the gate re-hashes its files, then the diff is taken again
            hashed = transport.hash_all()
            if not hashed.ok:
                raise _reply_failure(hashed, outcome, "hash")
            d = diff_mod.diff_manifest(mf, diff_mod.parse_list(transport.list()))
        planned = batches_mod.plan_batches(settings.env, d.to_send, plan.batch_mb, max_members=plan.caps["max_members"],
                                           max_member_mb=plan.caps["max_member_mb"], target_bytes=int(plan.batch_mb * MB))
        for batch in planned:
            _send_batch(settings.env, batch, transport, outcome, mf)
    except _Failure as exc:
        outcome.failure = exc
    except GateFailure as exc:
        outcome.failure = _Failure(f"transport:{exc.code}", EXIT_PRECONDITION if outcome.batches_done == 0 else EXIT_FAILURE)
    except batches_mod.PlanError as exc:
        outcome.failure = _Failure("plan", EXIT_FAILURE)
    # the final check: only what the gate lists with the manifest hash and size is reported as on prod
    try:
        final = diff_mod.diff_manifest(mf, diff_mod.parse_list(transport.list()))
        outcome.final_listing = True
        outcome.images = list(final.unchanged)
        outcome.missed = (list(final.added) + [e for e, _ in final.size_changed] + [e for e, _ in final.hash_changed]
                          + list(final.unverified))
    except GateFailure as exc:
        if outcome.failure is None:
            outcome.failure = _Failure(f"final_list:{exc.code}", EXIT_FAILURE)
        outcome.missed = list(mf.entries.values())
    return outcome


def _promoted_document(env: str, run_id: str, plan: Plan, outcome: Outcome, finished_at: str) -> dict:
    left_out = list(plan.manifest.left_out) + list(outcome.dropped)
    entries = [{"id": lo.id, "filename": lo.filename, "reason": lo.label} for lo in left_out]
    entries += [{"id": e.id, "filename": e.filename, "reason": GATE_MISS} for e in outcome.missed]
    entries.sort(key=lambda x: (x["filename"].encode("utf-8", "surrogatepass"), x["id"]))
    images = sorted(outcome.images, key=lambda e: e.filename.encode("utf-8", "surrogatepass"))
    return {
        "run_id": run_id, "env": env, "finished_at": finished_at, "gate_version": str(plan.gate.get("version", "")),
        "complete": outcome.failure is None and outcome.final_listing and not entries,
        "images": [{"id": e.id, "filename": e.filename, "size": e.size, "content_hash": e.sha256} for e in images],
        "replaced": outcome.replaced,
        "left_out": entries,
        "manifest_sha256": plan.manifest.manifest_sha256,
    }


def _result(plan: Plan, outcome: Outcome) -> dict:
    skipped: dict = {}
    for lo in list(plan.manifest.left_out) + list(outcome.dropped):
        skipped.setdefault(lo.reason, []).append(lo.id)
    if outcome.missed:
        skipped.setdefault(GATE_MISS, []).extend(e.id for e in outcome.missed)
    return {SECTION: {ENTRY: report.ResultEntry(added=outcome.added, overwritten=outcome.replaced, skipped=skipped)}}


# ---------------------------------------------------------------------------------------------------------------------------------
# the command

def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _emit(doc: report.Document, folder: Path, args: argparse.Namespace, settings: SyncSettings, out) -> None:
    top = args.top if args.top is not None else int(settings.get("report.top_n", report.DEFAULT_TOP_N))
    max_ids = int(settings.get("report.max_ids", report.DEFAULT_MAX_IDS))
    json_copy = args.json_path if args.json_path not in (None, "-") else None
    written = report.write_report(doc, folder, top_n=top, max_ids=max_ids, json_copy=json_copy)
    if args.json_path == "-":
        out.write(report.render_json(doc, max_ids))
    else:
        out.write(written["summary"])


def _failed_report(settings, args, run_id, folder, handshake=(), preconditions=(), out=None) -> None:
    doc = report.Document(env=settings.env, run_id=run_id, tool=TOOL, dry=not args.apply, handshake=list(handshake),
                          preconditions=list(preconditions))
    _emit(doc, folder, args, settings, out or sys.stdout)


def run_promote(args: argparse.Namespace, *, settings: SyncSettings | None = None, session_opener: Callable | None = None,
                transport_factory: Callable[[SyncSettings], Transport] | None = None, base_dir: str | None = None, lock: bool = True,
                out=None, err=None, ask: Callable[[str], str] | None = None) -> int:
    """The whole command; the keyword arguments are the injection points of the tests, the command line passes none."""
    out, err = out or sys.stdout, err or sys.stderr
    try:
        settings = settings or load_settings(args.env)
        run_id = args.run_id or new_run_id()
        try:
            report.check_run_id(run_id)
        except report.ReportError:
            raise UsageProblem("--run-id must look like YYYYMMDDTHHMMSSZ-<8 hex>") from None
        if args.plan_hash is not None and not args.apply:
            raise UsageProblem("--plan-hash needs --apply")
        if args.plan_hash is not None and not report._HEX.match(args.plan_hash):
            raise UsageProblem("--plan-hash must be the hex hash printed by the dry run")
        if args.top is not None and args.top < 1:
            raise UsageProblem("--top must be at least 1")
        folder = run_dir(settings, run_id)
        with contextlib.ExitStack() as stack:
            if lock:
                try:
                    stack.enter_context(exclusive_lock(settings))
                except SyncError as exc:
                    _failed_report(settings, args, run_id, folder, preconditions=[{"id": "lock", "ok": False, "detail": "held"}], out=out)
                    raise exc
            return _locked(args, settings, run_id, folder, session_opener or connect.open_session,
                           transport_factory or (lambda s: SshTransport.from_settings(s)), base_dir, out, ask or input)
    except SyncError as exc:
        print(f"sync: {exc}", file=err)
        return exc.exit_code


def _locked(args, settings: SyncSettings, run_id: str, folder: Path, opener, transport_factory, base_dir, out, ask=input) -> int:
    env = settings.env
    with opener(env, "F", settings=settings) as conns:
        dev = conns["dev"]
        transport = _Recording(transport_factory(settings))
        out.write(f"dev {env} ({settings.get('dev.database') or '?'} @ localhost, read-only) -> prod files via gate alias "
                  f"{settings.get('prod.gate_alias')} and upload alias {settings.get('prod.upload_alias')}\n")
        try:
            hs = connect.handshake(env, "F", settings=settings, dev=dev, transport=transport, fail_fast=True)
        except connect.HandshakeError as exc:
            _failed_report(settings, args, run_id, folder, handshake=exc.results, out=out)
            raise
        base = base_dir if base_dir is not None else os.environ.get("BASE_PATH")
        if not base or not os.path.isdir(base):
            _failed_report(settings, args, run_id, folder, handshake=hs, preconditions=[{"id": "dev_base_path", "ok": False, "detail": "missing"}], out=out)
            raise UsageProblem("the dev BASE_PATH is not set or not a directory")
        plan = make_plan(settings, args, dev, transport, str(base))
        plan_hash = plan.section.plan_hash()
        if not args.apply:
            doc = report.Document(env=env, run_id=run_id, tool=TOOL, dry=True, sections=[plan.section], handshake=hs,
                                  preconditions=plan.preconditions)
            doc.apply_command = report.apply_command(TOOL, env, doc.plan_hash, _apply_flags(args))
            mark_dry(folder)
            _emit(doc, folder, args, settings, out)
            return EXIT_OK

        doc = report.Document(env=env, run_id=run_id, tool=TOOL, dry=False, sections=[plan.section], handshake=hs,
                              preconditions=plan.preconditions)
        refusal = None
        if args.plan_hash is not None and args.plan_hash.lower() != plan_hash:
            refusal = f"the plan changed since the dry run (planned hash {plan_hash}, expected {args.plan_hash.lower()})"
        elif plan.tripped:
            refusal = "guard tripped before any write: " + ", ".join(g.id for g in plan.tripped)
        if refusal:
            _emit(doc, folder, args, settings, out)
            raise _Refusal(refusal)

        if not args.yes:
            expected = f"{env} {settings.get('prod.database') or ''}".strip()
            try:
                typed = ask(f"Type '{expected}' to send the files to prod: ").strip()
            except EOFError:
                typed = ""
            if typed != expected:
                raise _Refusal("typed confirmation did not match; nothing was sent")

        outcome = apply_plan(settings, args, plan, transport.inner)
        finished = _now()
        promoted = _promoted_document(env, run_id, plan, outcome, finished)
        atomic_write(folder / "files-promoted.json", json.dumps(promoted, indent=2, ensure_ascii=True) + "\n")
        doc.result = _result(plan, outcome)
        _emit(doc, folder, args, settings, out)
        if args.json_path != "-":
            out.write(f"applied: added {outcome.added}, overwritten {outcome.replaced}, left out {len(promoted['left_out'])}, "
                      f"on prod {len(promoted['images'])}, complete {str(promoted['complete']).lower()}\n")
        if outcome.failure is not None:
            raise _RunFailure(outcome.failure)
        return EXIT_OK


class _Refusal(SyncError):
    exit_code = EXIT_GUARD


class _RunFailure(SyncError):
    def __init__(self, failure: _Failure):
        super().__init__(f"the run stopped: {failure.code}; files-promoted.json lists what is verified on prod; run the same command again to resume")
        self.exit_code = failure.exit_code


def main(argv: list | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "promote":  # retire and restore are deferred (decision 27)
        parser.error("unknown command")
    if args.plan_hash is not None and not args.apply:
        parser.error("--plan-hash needs --apply")
    return run_promote(args)
