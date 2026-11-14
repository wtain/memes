"""P6 data checks D1 to D6 and the drift section (design P6 "Checks"; interfaces.md k).

The drift is the promote plan of dev against prod over the whole promotable set, computed by P2's own modules (`sync.promote.digest`,
`sync.promote.plan.build_plan`) with no files gate and no cap: `added` = what a promote would add, `overwritten` = differs (with the
changed digest groups), `left_behind` = on prod and not promotable on dev (informational, decision 27). Both sides are read inside one
`REPEATABLE READ, READ ONLY` snapshot each (P2 review H5), after the digest session settings were pinned and read back.

Reading is split from judging: `gather` runs the queries (read only), the `check_*` functions judge plain facts (unit tested with fakes).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sync import registry
from sync.config import PreconditionError, SyncSettings
from sync.model import Klass
from sync.promote import cli as promote_cli
from sync.promote import digest as dg
from sync.promote import plan as plan_mod
from sync.promote import preconditions as pre_mod
from sync.verify import model
from sync.verify.model import Check, downgrade, fail, ok

SECTION = "verify"
TOOL = "sync.verify"
UNSTABLE = "unstable:dev_job_running"

FIX_PROMOTE = "python -m sync.run --env <env>"
FIX_RELEASE = "release runbook: migrate prod, set memes.revision"
FIX_X = "owner: remove the class X rows on prod"


@dataclass
class DataFacts:
    plan: Any  # sync.promote.plan.Plan
    dev_counts: dict = field(default_factory=dict)  # table -> rows of the promotable images on dev
    prod_counts: dict = field(default_factory=dict)  # table -> rows of the same images on prod
    left_behind_rows: dict = field(default_factory=dict)  # table -> prod rows of the images left behind
    schema_dev: dict = field(default_factory=dict)
    schema_prod: dict = field(default_factory=dict)
    x_rows: dict = field(default_factory=dict)  # class X table -> approximate live rows on prod
    running_batches: int = 0


# ---------------------------------------------------------------------------------------------------------------------------------
# reading

_X_SQL = ("select relname, coalesce(n_live_tup, 0)::bigint from pg_stat_user_tables "
          "where schemaname = current_schema() and relname = any(%s) order by 1")


def schema_facts(conn: Any) -> dict:
    """alembic heads, server major, extension versions, `memes.revision` of one side (catalog reads, no row content)."""
    return {
        "alembic": sorted(r[0] for r in conn.query("select version_num from alembic_version")),
        "major": int(conn.scalar("select current_setting('server_version_num')::int")) // 10000,
        "extensions": [list(map(str, r)) for r in conn.query(pre_mod._EXTENSIONS_SQL, (list(pre_mod.EXTENSIONS),))],
        "revision": conn.scalar("select nullif(current_setting('memes.revision', true), '')"),
    }


def gather(settings: SyncSettings, dev: Any, prod: Any, required: list | None = None) -> DataFacts:
    """Read dev and prod (read only, one snapshot each) and build the drift plan. Raises PreconditionError when a session cannot be pinned."""
    for side, conn in (("dev", dev), ("prod", prod)):
        problems = dg.pin_session(conn)
        if problems:
            raise PreconditionError(f"session settings not pinned on {side}")
    tables, global_tables = plan_mod.resolve_only(None)
    required = list(required) if required else list(plan_mod.DEFAULT_REQUIRED)
    fetch = dg.table_order(set(tables) | set(required))
    with promote_cli.snapshot(dev), promote_cli.snapshot(prod):
        dev_images = dg.fetch_dev_images(dev)
        prod_ids = dg.fetch_prod_image_ids(prod)
        dev_digests = dg.fetch_image_digests(dev, fetch, "promotable")
        prod_digests = dg.fetch_image_digests(prod, tables, "all")
        dev_globals = {t: dg.fetch_global_hashes(dev, t) for t in global_tables}
        prod_globals = {t: dg.fetch_global_hashes(prod, t) for t in global_tables}
        schema_dev, schema_prod = schema_facts(dev), schema_facts(prod)
        running = int(dev.scalar(pre_mod.RUNNING_BATCHES_SQL) or 0)
        x_names = registry.names(Klass.X)
        x_rows = {str(r[0]): int(r[1]) for r in prod.query(_X_SQL, (x_names,))}
    inp = plan_mod.PlanInput(env=settings.env, dev_images=dev_images, prod_ids=prod_ids, dev_digests=dev_digests, prod_digests=prod_digests,
                             tables=tables, required=required, global_tables=global_tables, dev_globals=dev_globals,
                             prod_globals=prod_globals)
    plan = plan_mod.build_plan(inp)
    dev_ids = [i.id for i in dev_images]
    dev_counts, prod_counts, left_rows = {}, {}, {}
    for t in tables:
        dev_counts[t] = sum(dev_digests.get(i, {}).get(t, dg.EMPTY).count for i in dev_ids)
        prod_counts[t] = sum(prod_digests.get(i, {}).get(t, dg.EMPTY).count for i in dev_ids)
        left_rows[t] = sum(prod_digests.get(i, {}).get(t, dg.EMPTY).count for i in plan.left_behind)
    return DataFacts(plan=plan, dev_counts=dev_counts, prod_counts=prod_counts, left_behind_rows=left_rows, schema_dev=schema_dev,
                     schema_prod=schema_prod, x_rows={t: x_rows.get(t, 0) for t in x_names}, running_batches=running)


# ---------------------------------------------------------------------------------------------------------------------------------
# judging (pure)

def check_d1(dev: dict, prod: dict, git_sha: str | None) -> Check:
    problems = []
    if dev["alembic"] != prod["alembic"]:
        problems.append("alembic_differs")
    if dev["major"] != prod["major"]:
        problems.append("major_differs")
    if dev["extensions"] != prod["extensions"]:
        problems.append("ext_differs")
    revision = (prod.get("revision") or "").strip().lower()
    if not git_sha:
        problems.append("dev_git_sha_unknown")
    elif not revision:
        problems.append("revision_unset")
    elif revision != git_sha.strip().lower():
        problems.append("revision_mismatch")
    if problems:
        return fail("D1", "error", ",".join(problems), FIX_RELEASE)
    return ok("D1", "error", "equal")


def _ids(entry, category: str) -> int:
    return getattr(entry, category).n


def _images_entry(plan):
    return next((e for e in plan.section.entries if e.name == plan_mod.ENTRY), None)


def check_d2(plan, unstable: bool = False) -> Check:
    """New or changed images are errors; left behind is informational (decision 27); a new image held back by the completeness floor
    is not a difference a promote could repair, so it is only counted."""
    e = _images_entry(plan)
    if e is None:
        return model.skipped("D2", "error", "no_image_tables")
    new, changed, left = e.added.n, e.overwritten.n, e.left_behind.n
    floor = e.skipped["missing_required_table"].n if "missing_required_table" in e.skipped else 0
    detail = f"new={new},changed={changed},left_behind={left}" + (f",floor_skipped={floor}" if floor else "")
    if new or changed:
        c = fail("D2", "error", detail, FIX_PROMOTE)
        return downgrade(c, UNSTABLE) if unstable else c
    return ok("D2", "error", detail)


def check_d3(dev_counts: dict, prod_counts: dict, unstable: bool = False) -> Check:
    """Row counts per shipped table over the promotable set (dev's rows of the promotable images against prod's rows of the same images)."""
    differing = sorted(t for t in dev_counts if dev_counts[t] != prod_counts.get(t, 0))
    if differing:
        c = fail("D3", "error", f"tables={len(differing)}," + ",".join(differing[:3]), FIX_PROMOTE)
        return downgrade(c, UNSTABLE) if unstable else c
    return ok("D3", "error", f"tables={len(dev_counts)},rows={sum(dev_counts.values())}")


def check_d4(plan, unstable: bool = False) -> Check:
    """Global sets: a dev member missing on prod or differing is an error; members only prod has are left behind (informational)."""
    missing = differing = left = 0
    for name, g in plan.globals.items():
        missing += len(g["added"])
        differing += len(g["overwritten"])
        left += len(g["left_behind"])
    if not plan.globals:
        return model.skipped("D4", "error", "no_global_tables")
    detail = f"missing={missing},differing={differing},left_behind={left}"
    if missing or differing:
        c = fail("D4", "error", detail, FIX_PROMOTE)
        return downgrade(c, UNSTABLE) if unstable else c
    return ok("D4", "error", detail)


def check_d5(left_behind_images: int, left_behind_rows: dict) -> Check:
    """Rows on prod that dev does not have: the rows of the images left behind (informational, never an error)."""
    rows = sum(left_behind_rows.values())
    tables = sum(1 for n in left_behind_rows.values() if n)
    return ok("D5", "info", f"left_behind_images={left_behind_images},rows={rows},tables={tables}")


def check_d6(x_rows: dict) -> Check:
    """Class X tables on prod must hold no rows (they are never shipped; rows mean scratch data leaked). Counts are the catalog's live
    row estimates: neither prod role may SELECT a class X table (H3)."""
    bad = sorted(t for t, n in x_rows.items() if n > 0)
    if bad:
        return fail("D6", "error", f"tables={len(bad)}," + ",".join(bad[:3]), FIX_X)
    return ok("D6", "error", f"tables={len(x_rows)},rows=0")


def data_checks(facts: DataFacts, git_sha: str | None, skip: set) -> list:
    """D1 to D6 for one run (`skip` holds the ids of `--skip`; the caller records those as skipped)."""
    unstable = facts.running_batches > 0
    out = []
    for cid, fn in (("D1", lambda: check_d1(facts.schema_dev, facts.schema_prod, git_sha)),
                    ("D2", lambda: check_d2(facts.plan, unstable)),
                    ("D3", lambda: check_d3(facts.dev_counts, facts.prod_counts, unstable)),
                    ("D4", lambda: check_d4(facts.plan, unstable)),
                    ("D5", lambda: check_d5(len(facts.plan.left_behind), facts.left_behind_rows)),
                    ("D6", lambda: check_d6(facts.x_rows))):
        if cid in skip:
            out.append(model.skipped(cid, "info" if cid == "D5" else "error", "skipped_by_flag"))
        else:
            out += model.guarded(cid, "info" if cid == "D5" else "error", fn)
    return out


def drift_entries(plan) -> list:
    """The `images` and global-set entries of the drift (shared objects of the plan: do not mutate)."""
    return list(plan.section.entries)
