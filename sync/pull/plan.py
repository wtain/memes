"""P4-T1: the plan of `sync.pull` (design P4 "Mirror semantics", "Ratings", "Guards"). Pure: it works on hashed row maps and id sets and
touches no database, file or network (an AST test keeps it that way).

Per class P table, the prod snapshot is compared with the dev table (row key -> md5 of the row text, free text cut at the text cap):

- `added`: prod has the row, dev does not (inserted on dev);
- `overwritten`: the row exists on both sides and differs (replaced), plus the dev-only rows that the mirror discards (dev-made activity is
  disposable; counted separately as `dev_only_discarded` in the entry's `counts`);
- `unchanged`: equal rows.

The ratings table is the exception (design "Ratings"): in mode `skip` it is not touched (reported as skipped with reason `ratings_mode_skip`);
in mode `mirror` `partition_ratings` splits it into insert, update, delete (only a key a previous pull received from prod and that prod no
longer has), kept-retired (the image is absent from prod's `images`), kept-dev-made (never received from prod) and skipped-unknown (the
image is absent on dev).

Guards (id, value, limit, lifting flag). `Guard.tripped` is the raw condition and does not depend on a lift flag, and no lift flag is part of
the plan hash, so a dry run's hash stays valid for an apply that lifts a guard; `effective_blocking` applies the lifts.

Plan hash (owner decision A, 2026-11-14): the section is `structural`, so its hash covers only the tables in scope, the ratings mode, the
flags (`skip_files`, `bug_reports`, `text_cap`) and each guard's id and tripped state. It does not cover prod row ids, row digests or the
bug-report lists: readers write to prod every minute, so a content-bound hash could never survive the time between a dry run and its apply.
The ids and counts stay in `report.json` / `report.md`, which the owner reads.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Iterable

from sync import registry, report
from sync.model import Klass

TOOL = "sync.pull"
SECTION = "pull"
RATINGS = "meme_text_ratings"
MODES = ("skip", "mirror")

# The six class P tables (the registry is checked against this list by `check_registry`).
EXPECTED_TABLES = ("meme_impressions", "meme_saves", "meme_text_ratings", "search_history", "search_history_tags", "search_not_found")
INT_ID_TABLES = ("meme_impressions",)  # bigint ids; every other class P table has uuid ids
APPEND_ONLY = ("meme_impressions", "meme_saves", "search_not_found")  # prod never prunes them: any shrink is a refusal
LOSSY = ("search_history", "search_history_tags", RATINGS)  # "Forget text" can remove rows on prod: a drop over 50 % is a refusal
FK_FREE = ("meme_impressions", "meme_saves", "search_not_found")  # TRUNCATE is allowed above the threshold
SHRINK_FRACTION = 0.5

# Free-text columns, cut by `left()` in the source SELECT (anonymous readers write them; design "Untrusted text"). Every unbounded text
# column of the six tables must be listed; tests/sync/test_pull_plan.py compares this with Storage.models.
TEXT_COLUMNS = {
    "search_history": ("query", "situation"),
    "search_history_tags": ("category", "value"),
    RATINGS: ("query_text", "fallback", "parser_version"),
}

DEFAULT_TEXT_CAP = 2000
DEFAULT_GROWTH_FACTOR = 5
DEFAULT_MIN_GROWTH_ROWS = 100_000
DEFAULT_MAX_ROWS = 5_000_000
DEFAULT_TRUNCATE_THRESHOLD = 200_000
DEFAULT_MAX_DEV_DISCARD_ROWS = 1000  # dev-only rows a pull may discard in one table before --allow-dev-discard is needed
DEFAULT_MAX_DEV_DISCARD_RATIO = 0.25  # ... or this share of the table's dev rows
ROWS_PER_S = 33_000  # the design's figure: a full mirror is under 3 s per 100k rows
BYTES_PER_S = 40_000_000
UNCHANGED_IDS_LIMIT = report.UNCHANGED_IDS_LIMIT

LIFT_EMPTY = "--allow-empty-source"
LIFT_SHRINK = "--allow-shrink"
LIFT_GROWTH = "--allow-growth"
LIFT_DEV_DISCARD = "--allow-dev-discard"


def mirror_tables() -> list:
    """The five simple tables in dev copy order (parents first); `meme_text_ratings` has its own path."""
    return [t for level in registry.load_order(Klass.P) for t in level if t != RATINGS]


def check_registry() -> list:
    """Problems between the registry's class P tables and this module's constants (empty = consistent)."""
    problems = []
    if sorted(registry.pulled_tables()) != sorted(EXPECTED_TABLES):
        problems.append("registry_pulled_tables_differ")
    for t in registry.pulled_tables():
        if registry.get(t).mode.value != "mirror_replace":
            problems.append(f"{t}_not_mirror")
    if registry.get(RATINGS).setval or not registry.get("meme_impressions").setval:
        problems.append("setval_flags_differ")
    return problems


def key_line(image_id: str, query_hash: str) -> str:
    return f"{image_id}|{query_hash}"


def key_hash(image_id: str, query_hash: str) -> str:
    """The ledger entry of a rating key: sha256 of `image_id|query_hash` (no text)."""
    return hashlib.sha256(key_line(image_id, query_hash).encode("utf-8")).hexdigest()


def keys_sha256(lines: Iterable[str]) -> str:
    """The fingerprint of a key set: sha256 of the sorted `image_id|query_hash` lines joined by newlines."""
    return hashlib.sha256("\n".join(sorted(lines)).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------------------------------------------------------------
# input

@dataclass
class Limits:
    max_growth_factor: float = DEFAULT_GROWTH_FACTOR
    min_growth_rows: int = DEFAULT_MIN_GROWTH_ROWS
    max_rows_per_pull: int = DEFAULT_MAX_ROWS
    truncate_threshold: int = DEFAULT_TRUNCATE_THRESHOLD
    max_dev_discard_rows: int = DEFAULT_MAX_DEV_DISCARD_ROWS
    max_dev_discard_ratio: float = DEFAULT_MAX_DEV_DISCARD_RATIO
    text_cap: int = DEFAULT_TEXT_CAP


@dataclass
class TableRead:
    """One table of one side: the row count, and (when read) the hashed rows `key -> (hash, bytes)`. `read` is False for a table over the row
    ceiling: only the count is known and the plan has no row categories for it."""

    count: int
    rows: dict = field(default_factory=dict)
    read: bool = True
    truncated: int = 0  # values the text cap cuts (prod side only)


@dataclass(frozen=True)
class RatingRow:
    id: str
    hash: str
    bytes: int


@dataclass
class PlanInput:
    env: str
    mode: str  # skip | mirror
    prod: dict  # table -> TableRead (all six; the ratings entry holds only the count in skip mode)
    dev: dict  # table -> TableRead
    prod_ratings: dict = field(default_factory=dict)  # (image_id, query_hash) -> RatingRow; filled in mirror mode
    dev_ratings: dict = field(default_factory=dict)
    ledger: frozenset = frozenset()  # key hashes received from prod by earlier pulls
    prod_images: frozenset = frozenset()  # ids of prod's images (the snapshot's `select id from images`)
    dev_images: frozenset = frozenset()
    previous: dict | None = None  # the previous pull_state.json (None: a first pull)
    limits: Limits = field(default_factory=Limits)
    skip_files: bool = False


@dataclass
class RatingsPartition:
    insert: list = field(default_factory=list)  # keys (image_id, query_hash)
    update: list = field(default_factory=list)
    delete: list = field(default_factory=list)  # dev rows removed: the key is in the ledger and prod no longer has it
    unchanged: list = field(default_factory=list)
    kept_retired: list = field(default_factory=list)  # dev-only keys whose image is absent from prod's images
    kept_dev_made: list = field(default_factory=list)  # dev-only keys never received from prod
    skipped_unknown: list = field(default_factory=list)  # prod keys whose image is absent on dev

    @property
    def archived(self) -> list:
        """Dev rows that the apply removes or overwrites: each is appended to ratings_archive.jsonl first."""
        return self.update + self.delete


def partition_ratings(prod: dict, dev: dict, ledger: Iterable[str], prod_images: Iterable[str], dev_images: Iterable[str]) -> RatingsPartition:
    """The ledger-guarded mirror of the ratings table. `prod` and `dev` map `(image_id, query_hash)` to a `RatingRow` (id and row hash)."""
    ledger = set(ledger)
    prod_images, dev_images = set(prod_images), set(dev_images)
    part = RatingsPartition()
    for key in sorted(prod):
        image_id = key[0]
        if image_id not in dev_images:
            part.skipped_unknown.append(key)
        elif key not in dev:
            part.insert.append(key)
        elif dev[key].hash != prod[key].hash:
            part.update.append(key)
        else:
            part.unchanged.append(key)
    for key in sorted(dev):
        if key in prod:
            continue
        if key[0] not in prod_images:
            part.kept_retired.append(key)
        elif key_hash(*key) in ledger:
            part.delete.append(key)
        else:
            part.kept_dev_made.append(key)
    return part


# ---------------------------------------------------------------------------------------------------------------------------------
# plan

@dataclass
class Plan:
    section: report.Section
    mode: str
    first_pull: bool
    partition: RatingsPartition | None = None
    prod_counts: dict = field(default_factory=dict)  # table -> prod row count of this snapshot
    dev_counts: dict = field(default_factory=dict)
    truncated_values: int = 0

    @property
    def guards(self) -> list:
        return self.section.guards

    @property
    def tripped(self) -> list:
        return [g for g in self.section.guards if g.tripped]

    def entry(self, name: str) -> report.Entry:
        return next(e for e in self.section.entries if e.name == name)


def plan_table(name: str, prod: TableRead, dev: TableRead, truncate_threshold: int) -> report.Entry:
    """One simple table: added, overwritten (differing plus dev-only discarded), unchanged."""
    counts = {"prod": prod.count, "dev": dev.count, "truncated_values": prod.truncated,
              "truncate": int(name in FK_FREE and dev.count > truncate_threshold)}
    if not (prod.read and dev.read):
        counts["not_read"] = 1  # over the row ceiling: only the counts are known (guard row_ceiling)
        return report.Entry(name, "pulled", pulled=True, counts=counts)
    added = [k for k in prod.rows if k not in dev.rows]
    both = [k for k in prod.rows if k in dev.rows]
    differing = [k for k in both if prod.rows[k][0] != dev.rows[k][0]]
    equal = [k for k in both if prod.rows[k][0] == dev.rows[k][0]]
    discarded = [k for k in dev.rows if k not in prod.rows]
    counts.update({"differing": len(differing), "dev_only_discarded": len(discarded)})
    over_bytes = sum(prod.rows[k][1] for k in differing) + sum(dev.rows[k][1] for k in discarded)
    return report.Entry(
        name, "pulled", pulled=True, counts=counts,
        added=report.IdSet(ids=added, bytes=sum(prod.rows[k][1] for k in added)),
        overwritten=report.Overwritten(ids=differing + discarded, bytes=over_bytes),
        unchanged=report.IdSet(ids=equal if len(equal) < UNCHANGED_IDS_LIMIT else [], bytes=0, count=len(equal)))


def plan_ratings(inp: PlanInput) -> tuple:
    """`(entry, partition)`; the partition is None in `skip` mode."""
    prod, dev = inp.prod[RATINGS], inp.dev[RATINGS]
    counts = {"prod": prod.count, "dev": dev.count, "truncated_values": prod.truncated}
    if inp.mode == "skip":
        return report.Entry(RATINGS, "pulled", pulled=True, counts=counts,
                            skipped={"ratings_mode_skip": report.IdSet(ids=[], count=prod.count)}), None
    part = partition_ratings(inp.prod_ratings, inp.dev_ratings, inp.ledger, inp.prod_images, inp.dev_images)
    pr, dr = inp.prod_ratings, inp.dev_ratings
    counts.update({"inserted": len(part.insert), "updated": len(part.update), "deleted": len(part.delete),
                   "kept_retired": len(part.kept_retired), "kept_dev_made": len(part.kept_dev_made),
                   "skipped_unknown": len(part.skipped_unknown), "archived": len(part.archived)})
    entry = report.Entry(
        RATINGS, "pulled", pulled=True, counts=counts,
        added=report.IdSet(ids=[pr[k].id for k in part.insert], bytes=sum(pr[k].bytes for k in part.insert)),
        overwritten=report.Overwritten(ids=[pr[k].id for k in part.update] + [dr[k].id for k in part.delete],
                                       bytes=sum(pr[k].bytes for k in part.update) + sum(dr[k].bytes for k in part.delete)),
        unchanged=report.IdSet(ids=[pr[k].id for k in part.unchanged] if len(part.unchanged) < UNCHANGED_IDS_LIMIT else [],
                               count=len(part.unchanged)),
        skipped={"skipped_unknown_image": report.IdSet(ids=[pr[k].id for k in part.skipped_unknown],
                                                       bytes=sum(pr[k].bytes for k in part.skipped_unknown))} if part.skipped_unknown else {},
        left_behind=report.LeftBehind(ids=[dr[k].id for k in part.kept_retired + part.kept_dev_made],
                                      bytes=sum(dr[k].bytes for k in part.kept_retired + part.kept_dev_made)))
    return entry, part


def previous_count(previous: dict | None, table: str) -> int | None:
    try:
        value = previous["tables"][table]["prod"] if previous else None
    except (KeyError, TypeError):
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def build_guards(inp: PlanInput) -> list:
    lim, mode = inp.limits, inp.mode
    prod_total = sum(r.count for r in inp.prod.values())
    dev_total = sum(r.count for t, r in inp.dev.items() if t != RATINGS or mode == "mirror")
    guards = [report.Guard("empty_source", prod_total == 0 and dev_total > 0, dev_total, 0, LIFT_EMPTY)]
    for t in sorted(inp.prod):
        now, prev = inp.prod[t].count, previous_count(inp.previous, t)
        if t == RATINGS and mode != "mirror":
            guards.append(report.Guard(f"shrink:{t}", False, now, prev))
        elif prev is None:
            guards.append(report.Guard(f"shrink:{t}", False, now, None, LIFT_SHRINK))
        elif t in APPEND_ONLY:
            guards.append(report.Guard(f"shrink:{t}", now < prev, now, prev, LIFT_SHRINK))
        else:
            floor = math.ceil(prev * SHRINK_FRACTION)
            guards.append(report.Guard(f"shrink:{t}", now < floor, now, floor, LIFT_SHRINK))
    for t in sorted(inp.prod):
        now, prev = inp.prod[t].count, previous_count(inp.previous, t)
        if prev is None:
            guards.append(report.Guard(f"growth:{t}", False, now, None, LIFT_GROWTH))
        else:
            limit = int(prev * lim.max_growth_factor)
            guards.append(report.Guard(f"growth:{t}", now > limit and now - prev >= lim.min_growth_rows, now, limit, LIFT_GROWTH))
    # review finding M4: the mirror deletes dev-only rows; a pull that would discard many of them, or any on a first pull (no
    # pull_state.json), is refused until the owner lifts it. The value is the discarded row count (a count in the guard, never in the hash:
    # only the tripped state is hashed).
    for t in mirror_tables():
        dev = inp.dev[t]
        discarded = len(dev.rows.keys() - inp.prod[t].rows.keys()) if (dev.read and inp.prod[t].read) else 0
        over = discarded > lim.max_dev_discard_rows or (dev.count > 0 and discarded > lim.max_dev_discard_ratio * dev.count)
        first = inp.previous is None and discarded > 0
        guards.append(report.Guard(f"dev_only_discarded:{t}", over or first, discarded, lim.max_dev_discard_rows, LIFT_DEV_DISCARD))
    for t in sorted(inp.prod):
        now = inp.prod[t].count
        guards.append(report.Guard(f"row_ceiling:{t}", now > lim.max_rows_per_pull, now, lim.max_rows_per_pull, LIFT_GROWTH))
    return guards


def estimate(entries: list, mode: str) -> dict:
    rows = nbytes = 0
    for e in entries:
        if e.name == RATINGS and mode == "skip":
            continue
        rows += e.counts.get("prod", 0)
        nbytes += e.added.bytes + e.overwritten.bytes + e.unchanged.bytes
    return {"duration_s": round(rows / ROWS_PER_S + nbytes / BYTES_PER_S, 1), "wire_bytes": nbytes}


def build_plan(inp: PlanInput) -> Plan:
    if inp.mode not in MODES:
        raise ValueError("ratings mode must be skip or mirror")
    entries = [plan_table(t, inp.prod[t], inp.dev[t], inp.limits.truncate_threshold) for t in mirror_tables()]
    ratings_entry, part = plan_ratings(inp)
    entries.append(ratings_entry)
    guards = build_guards(inp)
    flags = {"ratings_mode": inp.mode, "skip_files": bool(inp.skip_files), "text_cap": inp.limits.text_cap}
    flags["bug_reports"] = "off" if inp.skip_files else "on"  # the cli sets "off" too when it has no gate transport
    section = report.Section(SECTION, TOOL, entries=entries, guards=guards, flags=flags, estimate=estimate(entries, inp.mode),
                             structural=True)
    return Plan(section, inp.mode, inp.previous is None, part,
                {t: r.count for t, r in inp.prod.items()}, {t: r.count for t, r in inp.dev.items()},
                sum(r.truncated for r in inp.prod.values()))


# ---------------------------------------------------------------------------------------------------------------------------------
# lifts

def effective_blocking(plan: Plan, allow_empty_source: bool = False, allow_shrink: Iterable[str] = (), allow_growth: bool = False,
                       allow_dev_discard: bool = False) -> list:
    """Ids of the tripped guards that no lift flag removes."""
    shrink = set(allow_shrink)
    blocking = []
    for g in plan.section.guards:
        if not g.tripped:
            continue
        base, _, table = g.id.partition(":")
        if base == "empty_source" and allow_empty_source:
            continue
        if base == "shrink" and table in shrink:
            continue
        if base in ("growth", "row_ceiling") and allow_growth:
            continue
        if base == "dev_only_discarded" and allow_dev_discard:
            continue
        blocking.append(g.id)
    return blocking


def lift_problems(plan: Plan, allow_empty_source: bool, allow_shrink: Iterable[str], allow_growth: bool, allow_dev_discard: bool = False) -> list:
    """Codes for lift flags that do nothing in this plan."""
    by_id = {g.id: g for g in plan.section.guards}
    problems = []
    if allow_empty_source and not by_id["empty_source"].tripped:
        problems.append("allow_empty_source_not_needed")
    if allow_growth and not any(g.tripped for g in plan.section.guards if g.id.partition(":")[0] in ("growth", "row_ceiling")):
        problems.append("allow_growth_not_needed")
    if allow_dev_discard and not any(g.tripped for g in plan.section.guards if g.id.partition(":")[0] == "dev_only_discarded"):
        problems.append("allow_dev_discard_not_needed")
    for t in allow_shrink:
        g = by_id.get(f"shrink:{t}")
        if g is None or not g.tripped:
            problems.append(f"allow_shrink_not_needed:{t}")
    return problems
