"""P4-T6 hook: the bug-report file step of `sync.pull`. NOT BUILT YET: this stage reports `skipped`.

What T6 plugs into (the two functions below are the only call sites in `sync/pull/cli.py`):

- `plan_lists(...)` returns the report `lists` of the pull section (`to_fetch`, `to_delete_on_prod_after_verified_pull`, both ids that carry no
  text: the report model accepts uuid/hex ids only, so T6 lists the `<timestamp>_<uuid>` names by their uuid part, or extends
  `report.Section.lists` for names) and the flags that change the plan; a dry run calls only this.
- `run(...)` is called after the dev commit and the first `pull_state.json` write, only with `--apply` and without `--skip-files`; it returns the
  `bug_reports` block of the state (`status` done | partial | skipped, `pulled`, `deleted_on_prod`, `failed`) and cli writes the state again
  (stage 2). A failure here never undoes the table pull and never blocks the state; it makes the exit code 1 at the end (design "Failure
  behaviour"), which T6 signals by returning `failed > 0` (cli maps that to exit 1).
- The gate transport (`sync.files.transport`, verbs `pull-list`, `pull-tar`, `delete-verified`, already in deploy/prod/memesync-gate.py) is passed
  in as `transport`; when T6 supplies one, `cli.handshake_steps` also runs H6.
"""
from __future__ import annotations

from typing import Any

from sync.pull import state


def plan_lists(settings: Any, transport: Any = None) -> dict:
    """Report lists of the dry run: nothing is fetched in this stage."""
    return {}


def run(settings: Any, transport: Any, state_dir: Any, run_id: str) -> dict:
    """The bug-report step of an apply. T6 replaces this body; until then the state says `skipped`."""
    return state.skipped_bug_reports()
