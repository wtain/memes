"""P6: `python -m sync.verify` (design P6/P7 part A; interfaces.md a, e, i, k): the read-only drift and health report of one environment.

One run: lock, handshake for tool V (S0; a failure is exit 2 and no other check runs), then every check of the design (`model.CHECK_IDS`),
then the drift in the report vocabulary (`added` = what a promote would add, `overwritten` with the changed digest groups, `left_behind`,
informational) next to the check results. Written to `runs/<runid>/verify.json` (also the trend store of A1) and, stand-alone, to
`report.json` / `report.md` in a `dry` run folder (nothing in it is ever an input).

Read only everywhere: dev through the read-only URL (session set read only), prod through `sync_writer` in a read-only session (class D
digests) and `sync_reader` (class P counts). Both sides are read inside one `REPEATABLE READ, READ ONLY` snapshot. The only gate verb that
writes anything is `hash` (with `--deep-files`), which rewrites the gate's own `work/record.tsv` cache.

Exit codes: 0 clean, 4 any check with status `error`, 3 only warnings with `--fail-on warn`, 2 handshake or precondition (including a held
lock), 1 other failure.

Library entry for `sync.run`: `run_verify(args, ..., lock=False, plan_only=...)`. With `plan_only` it runs the handshake and the drift only
(the planning part: no checks, no files written) and hands the drift `Section` to `args.document_sink`.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

from sync import connect, report
from sync.config import (ENVIRONMENTS, EXIT_OK, PreconditionError, SyncError, SyncSettings, atomic_write, exclusive_lock,
                         load_settings, mark_dry, new_run_id, run_dir, state_dir)
from sync.files import diff as diff_mod
from sync.pull import state as pull_state
from sync.verify import data as data_mod
from sync.verify import files as files_mod
from sync.verify import growth, model, net
from sync.verify import runtime as rt
from sync.verify.model import CHECK_IDS, Check

TOOL = data_mod.TOOL
SECTION = data_mod.SECTION
VERIFY_FILE = "verify.json"
SCHEMA_VERSION = 1
FILES_ENTRY = "files"


class UsageProblem(PreconditionError):
    """A bad flag value (exit 2)."""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m sync.verify", description="Read-only drift and health report of one environment "
                                "(prod against dev, identity, files, schema, grants, growth, freshness). Writes nothing on either side.")
    p.add_argument("--env", required=True, choices=ENVIRONMENTS)
    p.add_argument("--json", dest="json_path", default=None, metavar="PATH", help="also write verify.json to PATH ('-' for stdout)")
    p.add_argument("--deep-files", action="store_true", help="F3: re-hash every file on prod (gate `hash`) and on dev")
    p.add_argument("--deep-net", action="store_true", help="A4: also the declared Content-Length and the rate-limit burst against the public site")
    p.add_argument("--skip", action="append", default=[], metavar="CHECK[,..]", help="skip checks by id (listed as skipped with the reason)")
    p.add_argument("--fail-on", choices=("warn",), default=None, help="exit 3 when there are only warnings")
    p.add_argument("--top", type=int, default=None, help="ids listed per category in report.md and the summary (default sync.report.top_n)")
    p.add_argument("--run-id", default=None, help="run id (sync.run passes it to its children)")
    return p


def skipped_ids(args: argparse.Namespace) -> set:
    return {t.strip() for chunk in args.skip for t in chunk.split(",") if t.strip()}


def validate_args(args: argparse.Namespace) -> None:
    bad = sorted(skipped_ids(args) - set(CHECK_IDS))
    if bad:
        raise UsageProblem(f"--skip names unknown checks: {', '.join(bad)} (known: {', '.join(CHECK_IDS)})")
    if args.top is not None and args.top < 1:
        raise UsageProblem("--top must be at least 1")
    if args.run_id is not None:
        try:
            report.check_run_id(args.run_id)
        except report.ReportError:
            raise UsageProblem("--run-id must look like YYYYMMDDTHHMMSSZ-<8 hex>") from None


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _utc(now: _dt.datetime) -> str:
    return now.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Recording:
    """Wraps the gate transport so the `handshake` reply of step H6 can be read afterwards (S4, the owner uid of F4)."""

    def __init__(self, inner: Any):
        self.inner, self.reply = inner, None

    def handshake(self) -> dict:
        self.reply = self.inner.handshake()
        return self.reply

    def __getattr__(self, name: str):
        return getattr(self.inner, name)


class Collector:
    """The checks of one run, in the order of `CHECK_IDS`; `--skip` ids are recorded as skipped, a crashing check as a check."""

    def __init__(self, skip: set):
        self.skip, self.items = skip, {}

    def add(self, *checks: Check) -> None:
        for c in checks:
            self.items[c.id] = c

    def run(self, check_id: str, severity: str, fn: Callable[[], Any]) -> None:
        if check_id in self.skip:
            self.add(model.skipped(check_id, severity, "skipped_by_flag"))
            return
        self.add(*model.guarded(check_id, severity, fn))

    def mark_skipped(self, ids: tuple, severity: str, reason: str) -> None:
        for cid in ids:
            if cid not in self.items:
                self.add(model.skipped(cid, severity, "skipped_by_flag" if cid in self.skip else reason))

    @property
    def ordered(self) -> list:
        return [self.items[c] for c in CHECK_IDS if c in self.items]


# ---------------------------------------------------------------------------------------------------------------------------------
# grants (S1, S2 need the roles' facts)

def gather_grants(settings: SyncSettings, prod: Any, reader: Any) -> dict:
    """Problem lists of the writer (the `prod` connection), the reader (when connected) and `backend_app`, plus the facts S2 reuses."""
    env = settings.env
    writer_problems = connect.evaluate_role(connect.gather_role_facts(prod, env), "writer", str(settings.require("prod.writer_role")))
    reader_problems = None
    if reader is not None:
        reader_problems = connect.evaluate_role(connect.gather_role_facts(reader, env), "reader", str(settings.require("prod.reader_role")))
    app_role = str(settings.get("prod.app_role") or f"memes_{env}_app")
    try:
        app_facts = rt.gather_app_facts(prod, app_role)
        app_problems = rt.evaluate_app(app_facts)
    except connect.DbError as exc:
        app_facts, app_problems = None, [f"role_unreadable_{exc.sqlstate or 'none'}"]
    return {"writer": writer_problems, "reader": reader_problems, "app": app_problems, "app_facts": app_facts}


# ---------------------------------------------------------------------------------------------------------------------------------
# the run

def _drift_section(entries: list, checks: list | None = None) -> report.Section:
    return report.Section(name=SECTION, tool=TOOL, entries=list(entries), checks=[
        {"id": c.id, "status": c.status, "severity": c.severity, "detail": c.detail} for c in (checks or [])])


def _drift_summary(entries: list) -> dict:
    out = {}
    for e in entries:
        out[e.name] = {"added": e.added.n, "overwritten": e.overwritten.n, "unchanged": e.unchanged.n, "left_behind": e.left_behind.n}
    return out


def _file_entry(facts: files_mod.FileFacts) -> report.Entry:
    return diff_mod.to_entry(facts.manifest, facts.diff, FILES_ENTRY)


def run_verify(args: argparse.Namespace, *, settings: SyncSettings | None = None, session_opener: Callable | None = None,
               capabilities: Callable | None = None, transport: Any = None, http: Callable | None = None, git_sha: str | None = None,
               now: _dt.datetime | None = None, base_dir: str | None = None, lock: bool = True, plan_only: bool = False,
               accounts_probe: Callable | None = None, out=None, err=None) -> int:
    """The whole command; the keyword arguments are the injection points of the tests, the command line passes none."""
    out, err = out or sys.stdout, err or sys.stderr
    try:
        validate_args(args)
        settings = settings or load_settings(args.env)
        run_id = args.run_id or new_run_id()
        folder = run_dir(settings, run_id, create=not plan_only)
        with contextlib.ExitStack() as stack:
            if lock:
                try:
                    stack.enter_context(exclusive_lock(settings))
                except SyncError as exc:
                    _failed_report(settings, args, run_id, folder, [], [{"id": "lock", "ok": False, "detail": "held"}], out)
                    raise exc
            opener = session_opener
            if opener is None:
                from sync.files.transport import SshTransport
                opener = connect.open_session
                if transport is None:
                    transport = SshTransport.from_settings(settings)
            return _locked(args, settings, run_id, folder, opener, capabilities, transport, http or net.default_http, git_sha,
                           now or _now(), base_dir, plan_only, accounts_probe, out)
    except SyncError as exc:
        print(f"sync: {exc}", file=err)
        return exc.exit_code


def _failed_report(settings, args, run_id, folder, handshake, preconditions, out) -> None:
    if getattr(args, "document_sink", None) is not None:
        return
    doc = report.Document(env=settings.env, run_id=run_id, tool=TOOL, dry=True, handshake=list(handshake), preconditions=list(preconditions))
    mark_dry(folder)
    top = args.top if args.top is not None else int(settings.get("report.top_n", report.DEFAULT_TOP_N))
    written = report.write_report(doc, folder, top_n=top, max_ids=int(settings.get("report.max_ids", report.DEFAULT_MAX_IDS)))
    out.write(written["summary"])


def _locked(args, settings: SyncSettings, run_id: str, folder: Path, opener, capabilities, transport, http, git_sha, now, base_dir,
            plan_only: bool, accounts_probe, out) -> int:
    env = settings.env
    gate = _Recording(transport) if transport is not None else None
    with opener(env, "V", "normal", settings=settings) as conns:
        dev, prod, reader = conns["dev"], conns["prod"], conns.get("prod_reader_conn")
        for conn in (dev, prod, reader):
            if conn is not None:
                conn.query("SET default_transaction_read_only = on")  # a verify cannot write, even by mistake
        out.write(connect.banner(env, settings, str(settings.get("dev.database") or "?")) + "\n")
        try:
            hs = connect.handshake(env, "V", "normal", settings=settings, dev=dev, prod=prod, prod_reader_conn=reader,
                                   capabilities=capabilities, transport=gate, git_sha=git_sha, fail_fast=True)
        except connect.HandshakeError as exc:
            _failed_report(settings, args, run_id, folder, exc.results, [], out)
            raise
        if plan_only:
            return _plan_only(args, settings, run_id, hs, dev, prod, gate, base_dir)
        return _full(args, settings, run_id, folder, hs, dev, prod, reader, gate, capabilities, http, git_sha, now, base_dir,
                     accounts_probe, out)


def _drift_files(settings, dev, gate, base_dir, deep=False, gate_reply=None):
    base = base_dir if base_dir is not None else os.environ.get("BASE_PATH")
    if gate is None or not base or not os.path.isdir(base):
        return None
    modes = settings.get("verify.files.modes", list(files_mod.DEFAULT_MODES))
    return files_mod.gather(dev, gate, str(base), deep=deep, gate=gate_reply, modes=modes)


def _plan_only(args, settings, run_id, hs, dev, prod, gate, base_dir) -> int:
    """The planning part for the combined `sync.run` report: handshake done, now the drift only."""
    facts = data_mod.gather(settings, dev, prod, list(settings.get("promote.required_tables") or []))
    entries = data_mod.drift_entries(facts.plan)
    ff = _drift_files(settings, dev, gate, base_dir, gate_reply=gate.reply if gate else None)
    if ff is not None:
        entries.append(_file_entry(ff))
    sink = getattr(args, "document_sink", None)
    if sink is not None:
        sink(report.Document(env=settings.env, run_id=run_id, tool=TOOL, dry=True, sections=[_drift_section(entries)], handshake=hs))
    return EXIT_OK


def _full(args, settings, run_id, folder, hs, dev, prod, reader, gate, capabilities, http, git_sha, now, base_dir, accounts_probe, out) -> int:
    env = settings.env
    skip = skipped_ids(args)
    cx = Collector(skip)
    cx.add(model.ok("S0", "error", f"handshake_steps={len(hs)}"))
    dev_sha = git_sha if git_sha is not None else connect._git_sha()

    # ---- data (D1 to D6) and the drift
    entries: list = []
    facts = None
    d_ids = ("D1", "D2", "D3", "D4", "D5", "D6")
    if all(c in skip for c in d_ids):
        cx.mark_skipped(d_ids, "error", "skipped_by_flag")
    else:
        try:
            facts = data_mod.gather(settings, dev, prod, list(settings.get("promote.required_tables") or []))
        except Exception as exc:
            for cid in d_ids:
                cx.run(cid, "info" if cid == "D5" else "error", lambda exc=exc: _raise(exc))
        if facts is not None:
            cx.add(*[c for c in data_mod.data_checks(facts, dev_sha, skip)])
            entries = data_mod.drift_entries(facts.plan)

    # ---- files (F1 to F4), bug reports (P2, A2)
    gate_reply = _gate_reply(gate)
    f_ids = ("F1", "F2", "F3", "F4")
    ff = None
    if all(c in skip for c in f_ids):
        cx.mark_skipped(f_ids, "error", "skipped_by_flag")
    elif gate is None:
        cx.mark_skipped(f_ids, "error", "no_gate_transport")
    else:
        try:
            ff = _drift_files(settings, dev, gate, base_dir, deep=args.deep_files, gate_reply=gate_reply)
        except Exception as exc:
            for cid in f_ids:
                cx.run(cid, "error", lambda exc=exc: _raise(exc))
        else:
            if ff is None:
                cx.mark_skipped(f_ids, "error", "dev_base_path_missing")
            else:
                for c in files_mod.files_checks(ff):
                    cx.add(c if c.id not in skip else model.skipped(c.id, c.severity, "skipped_by_flag"))
                entries.append(_file_entry(ff))
    bug = _bug_checks(cx, gate, settings, now, skip)

    # ---- class P freshness and growth (P1, A1)
    sdir = state_dir(settings)
    doc_state, status = pull_state.load_state(sdir, env)
    cx.run("P1", "warn", lambda: rt.check_p1(doc_state, status, now, float(settings.get("verify.pull_stale_days", 7))))
    classp = _growth(cx, settings, run_id, sdir, dev, prod, reader, bug, now, skip)

    # ---- disk, containment, backup (gate facts)
    _gate_facts(cx, gate, settings, now, skip, classp)

    # ---- grants, roles, connections (S1, S2, S5)
    _database_checks(cx, settings, dev, prod, reader, skip)

    # ---- the gate itself (S4) and the public site (S3, S7, A4)
    if gate_reply is not None:
        cx.run("S4", "error", lambda: rt.check_s4(gate_reply, _repo_gate_version()))
    else:
        cx.mark_skipped(("S4",), "error", "no_gate_reply")
    cx.run("S4b", "error", lambda: rt.check_s4b(accounts_probe() if accounts_probe else None))
    _net_checks(cx, settings, http, args, skip)

    checks = cx.ordered
    code = model.exit_code(checks, args.fail_on == "warn")
    doc_json = _verify_json(env, run_id, now, checks, classp["growth"], entries, ff, code, hs)
    _write_outputs(args, settings, run_id, folder, hs, entries, checks, doc_json, out)
    _print(checks, doc_json, out, args.top if args.top is not None else int(settings.get("report.top_n", report.DEFAULT_TOP_N)))
    return code


def _gate_reply(gate) -> dict | None:
    """The gate's `handshake` reply: the one the handshake step recorded, else asked for now (None when there is no gate or it fails)."""
    if gate is None:
        return None
    if gate.reply is not None:
        return gate.reply
    try:
        return gate.handshake()
    except SyncError:
        return None


def _repo_gate_version() -> str:
    from sync.files.transport import load_gate

    return str(load_gate().GATE_VERSION)


def _bug_checks(cx: Collector, gate, settings, now, skip) -> dict | None:
    if all(c in skip for c in ("P2", "A2")):
        cx.mark_skipped(("P2", "A2"), "warn", "skipped_by_flag")
        return None
    if gate is None:
        cx.mark_skipped(("P2", "A2"), "warn", "no_gate_transport")
        return None
    holder: dict = {}

    def facts():
        reply = gate.pull_list()
        if not reply.ok:
            raise _Unavailable("gate_" + (reply.reason or f"exit_{reply.code}"))
        holder["f"] = files_mod.bug_facts(reply.out, now)
        return holder["f"]

    try:
        facts()
    except _Unavailable as exc:
        cx.mark_skipped(("P2", "A2"), "warn", str(exc))
        return None
    except Exception as exc:
        cx.run("P2", "warn", lambda exc=exc: _raise(exc))
        cx.run("A2", "warn", lambda exc=exc: _raise(exc))
        return None
    bug = holder["f"]
    caps = settings.get("pull.bug_reports") or {}
    cx.run("P2", "warn", lambda: files_mod.check_p2(bug, float(settings.get("verify.pull_stale_days", 7))))
    cx.run("A2", "warn", lambda: files_mod.check_a2(bug, int(caps.get("max_files") or 200), int(caps.get("max_total_mb") or 200) * 1024 * 1024,
                                                    float(settings.get("verify.bug_reports.alarm_pct", 80))))
    return bug


class _Unavailable(Exception):
    """A gate fact that is not there (reason code in the message): the dependent check is skipped, not failed."""


def _raise(exc: BaseException):
    raise exc


def _growth(cx: Collector, settings, run_id, sdir, dev, prod, reader, bug, now, skip) -> dict:
    """A1 and the numbers the next run compares with. Returns `{growth, prod_counts, dev_counts, rows}`."""
    prod_counts = dev_counts = sizes = None
    errors = []
    try:
        sizes = rt.table_bytes(prod, rt.GROWTH_TABLES)
        dev_counts = rt.class_p_counts(dev)
        if reader is not None:
            prod_counts = rt.class_p_counts(reader)
    except Exception as exc:  # reported through A1 below
        errors.append(exc)
    metrics = rt.growth_metrics(prod_counts, sizes, bug)
    history = growth.load_history(sdir, exclude_run=run_id)
    rows = sum(prod_counts.values()) if prod_counts else None
    trend = growth.trend(history, now, metrics, growth.config(settings.get), {"classp_rows": rows})
    if errors:
        cx.run("A1", "warn", lambda: _raise(errors[0]))
    else:
        cx.run("A1", "warn", lambda: rt.check_a1(trend))
    return {"growth": trend, "prod_counts": prod_counts, "dev_counts": dev_counts, "rows": rows, "history": history}


def _gate_facts(cx: Collector, gate, settings, now, skip, classp) -> None:
    def facts_json(verb):
        reply = getattr(gate, verb)()
        if not reply.ok:
            raise _Unavailable("gate_" + (reply.reason or f"exit_{reply.code}"))
        try:
            return reply.json()
        except Exception:
            raise _Unavailable("facts_unusable") from None

    def run(cid, severity, verb, judge):
        if cid in skip:
            cx.add(model.skipped(cid, severity, "skipped_by_flag"))
        elif gate is None:
            cx.add(model.skipped(cid, severity, "no_gate_transport"))
        else:
            try:
                data = facts_json(verb)
            except _Unavailable as exc:
                cx.add(model.skipped(cid, severity, str(exc)))
                return
            except Exception as exc:
                cx.run(cid, severity, lambda exc=exc: _raise(exc))
                return
            cx.run(cid, severity, lambda: judge(data))

    run("A3", "error", "disk", lambda d: rt.check_a3(d, float(settings.get("verify.disk.warn_free_pct", 20)),
                                                     float(settings.get("verify.disk.error_free_pct", 10))))
    run("S8", "error", "containment", rt.check_s8)

    def backup(data):
        prev = (classp["history"][-1]["facts"] if classp["history"] else {}) or {}
        prev_bk = {"size": prev.get("backup_size"), "rows": prev.get("classp_rows")}
        c = rt.check_s6(data, now, float(settings.get("verify.backup_max_age_h", 36)), prev_bk if prev_bk["size"] else None, classp["rows"])
        if isinstance(data.get("size"), int):
            classp["growth"]["facts"]["backup_size"] = data["size"]
        return c

    if "S6" in skip:
        cx.add(model.skipped("S6", "warn", "skipped_by_flag"))
    elif gate is None:
        cx.add(model.skipped("S6", "warn", "no_gate_transport"))
    else:
        reply = gate.fetch_backup_meta()
        if not reply.ok:
            cx.add(model.skipped("S6", "warn", "no_backup" if reply.reason in ("no_backup", "") else "gate_" + reply.reason))
        else:
            try:
                data = reply.json()
            except Exception:
                cx.add(model.skipped("S6", "warn", "facts_unusable"))
            else:
                cx.run("S6", "warn", lambda: backup(data))


def _database_checks(cx: Collector, settings, dev, prod, reader, skip) -> None:
    grants = None
    if "S1" in skip and "S2" in skip:
        cx.mark_skipped(("S1", "S2"), "error", "skipped_by_flag")
    else:
        try:
            grants = gather_grants(settings, prod, reader)
        except Exception as exc:
            cx.run("S1", "error", lambda exc=exc: _raise(exc))
            cx.run("S2", "error", lambda exc=exc: _raise(exc))
    if grants is not None:
        cx.run("S1", "error", lambda: rt.check_s1(grants["writer"], grants["reader"], grants["app"]))
        cx.run("S2", "error", lambda: rt.check_s2(rt.gather_s2(prod, grants["app_facts"])))

    def s5():
        used, limit = rt.gather_connections(prod)
        seeding = prod.scalar("select current_setting('memes.state', true)") == "seeding"
        return rt.check_s5(used, limit, rt.gather_indexes(dev), rt.gather_indexes(prod), bool(seeding))

    cx.run("S5", "error", s5)


def _net_checks(cx: Collector, settings, http, args, skip) -> None:
    base = str(settings.get("prod.api_url") or "")
    ids = ("S3", "S7", "A4")
    if not base:
        cx.mark_skipped(ids, "error", "api_url_missing")
        return
    caps_holder: dict = {}

    def s3():
        health, caps = net.gather_s3(http, base)
        caps_holder["caps"] = caps
        return net.check_s3(health, caps, settings.env)

    cx.run("S3", "error", s3)

    def s7():
        features = (caps_holder.get("caps") or {}).get("features") or {}
        skip_routes = ("ratings",) if features.get("ratings") is True else ()
        return net.check_s7(net.gather_s7(http, base, skip_routes))

    cx.run("S7", "error", s7)
    cap_mb = int((settings.get("pull.bug_reports") or {}).get("max_file_mb") or 10)
    cx.run("A4", "error", lambda: net.check_a4(net.gather_a4(http, base, deep=args.deep_net, bug_cap_bytes=cap_mb * 1024 * 1024)))


# ---------------------------------------------------------------------------------------------------------------------------------
# output

def _verify_json(env, run_id, now, checks: list, growth_doc: dict, entries: list, ff, code: int, hs: list) -> dict:
    counts = model.summarize(checks)
    anomalies = [{"path": p, "reason": r} for p, r in (ff.anomalies[:files_mod.ANOMALY_LIMIT] if ff is not None else [])]
    return {"schema_version": SCHEMA_VERSION, "env": env, "ran_at": _utc(now), "run_id": run_id,
            "checks": [c.as_json() for c in checks], "growth": growth_doc,
            "summary": {**counts, "exit_code": code, "drift": _drift_summary(entries)},
            "anomalies": anomalies, "handshake": [{"step": h["step"], "ok": h["ok"], "detail": report.scrub_detail(h.get("detail", ""))} for h in hs]}


def _write_outputs(args, settings, run_id, folder: Path, hs, entries, checks, doc_json: dict, out) -> None:
    text = json.dumps(doc_json, indent=2, ensure_ascii=True, sort_keys=False) + "\n"
    atomic_write(folder / VERIFY_FILE, text)
    if args.json_path == "-":
        out.write(text)
    elif args.json_path:
        atomic_write(Path(args.json_path), text)
    if getattr(args, "document_sink", None) is not None:
        args.document_sink(report.Document(env=settings.env, run_id=run_id, tool=TOOL, dry=False, handshake=hs,
                                           sections=[_drift_section(entries, checks)]))
        return
    doc = report.Document(env=settings.env, run_id=run_id, tool=TOOL, dry=True, handshake=hs, sections=[_drift_section(entries, checks)])
    top = args.top if args.top is not None else int(settings.get("report.top_n", report.DEFAULT_TOP_N))
    mark_dry(folder)
    report.write_report(doc, folder, top_n=top, max_ids=int(settings.get("report.max_ids", report.DEFAULT_MAX_IDS)))


def _print(checks: list, doc_json: dict, out, top: int) -> None:
    for c in checks:
        mark = {"ok": "ok  ", "warn": "WARN", "error": "FAIL", "skipped": "skip"}[c.status]
        out.write(f"[{mark}] {c.id:<3} {c.detail or c.skipped_reason or ''}" + (f"   fix: {c.fix}" if c.is_problem and c.fix else "") + "\n")
    drift = doc_json["summary"]["drift"]
    for name, d in drift.items():
        out.write(f"drift {name}: would add {d['added']}, overwrite {d['overwritten']}, unchanged {d['unchanged']}, "
                  f"left behind on prod (removals deferred) {d['left_behind']}\n")
    for a in doc_json["anomalies"][:top]:
        out.write(f"anomaly {a['reason']}: {a['path']}\n")
    if len(doc_json["anomalies"]) > top:
        out.write(f"... {len(doc_json['anomalies']) - top} more anomalies in verify.json\n")
    s = doc_json["summary"]
    out.write(f"verify {doc_json['run_id']}: {s['ok']} ok, {s['warn']} warn, {s['error']} error, {s['skipped']} skipped; exit {s['exit_code']}\n")


def main(argv: list | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_verify(args)
