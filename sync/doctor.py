"""`python -m sync.doctor --env E [--json]`: the identity handshake (interfaces.md e) and nothing else; writes nothing.

Runs every step with no early stop and prints one line per step (or the JSON list). Exit 0 when all steps pass, 2 when any
fails or a precondition (lock, config, tunnel, connection) is missing. The gate step H6 reports `skipped:no_transport` until
the gate transport (P3-T4) exists.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Callable

from sync import connect
from sync.config import ENVIRONMENTS, EXIT_OK, EXIT_PRECONDITION, SyncError, exclusive_lock, load_settings


def run_doctor(env: str, *, settings=None, session_opener: Callable | None = None, capabilities=None, transport=None,
               git_sha: str | None = None, lock: bool = True) -> tuple[list[dict], str]:
    """Return (handshake results, banner). Injection points exist for the tests; the command line passes none."""
    settings = settings or load_settings(env)
    opener = session_opener or connect.open_session
    import contextlib

    with contextlib.ExitStack() as stack:
        if lock:
            stack.enter_context(exclusive_lock(settings))
        conns = stack.enter_context(opener(env, "D", settings=settings))
        dev_db = str(settings.get("dev.database") or "?")
        text = connect.banner(env, settings, dev_db)
        results = connect.handshake(env, "D", "normal", settings=settings, dev=conns["dev"], prod=conns["prod"],
                                    prod_reader_conn=conns["prod_reader_conn"], capabilities=capabilities,
                                    transport=transport, git_sha=git_sha, fail_fast=False)
    return results, text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sync.doctor", description=__doc__.splitlines()[0])
    parser.add_argument("--env", required=True, choices=ENVIRONMENTS)
    parser.add_argument("--json", action="store_true", help="print the handshake results as JSON")
    args = parser.parse_args(argv)
    try:
        results, text = run_doctor(args.env)
    except SyncError as exc:
        print(f"sync: {exc}", file=sys.stderr)
        return exc.exit_code
    ok = all(r["ok"] for r in results)
    if args.json:
        print(json.dumps({"env": args.env, "ok": ok, "handshake": results}, indent=2))
    else:
        print(text)
        for r in results:
            print(f"{r['step']:<4} {'ok' if r['ok'] else 'FAILED':<7} {r['detail']}")
    return EXIT_OK if ok else EXIT_PRECONDITION


if __name__ == "__main__":
    sys.exit(main())
