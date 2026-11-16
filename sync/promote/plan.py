"""P2-T5: the plan of `sync.promote` (design P2 "Plan"): the manifest diff of dev against prod, the global sets, the guards and the
promote ("data") section of the report. Pure: it works on digest maps and id lists and touches no database, file or network.

Image categories (all from per-image digests, `sync.promote.digest`):

- `added`: promotable on dev, absent on prod;
- `overwritten`: on both and at least one digest group differs (the report names the changed groups per image);
- `unchanged`: on both and equal;
- `skipped_with_reason`: would be written but is held back: `missing_required_table` (a new image without a row in a required
  table), the files gate (`files_gate_miss`, `missing_file`, `name_problem`, `hash_mismatch`, `over_member_cap`: its file is not
  verified on prod) and `capped_by_limit` (over `--limit-images`);
- `left_behind`: on prod, not promotable on dev (informational, never a guard; decision 27).

Global sets (`concepts*`, `popularity_absorptions`) are upsert-only: missing on prod = added, differing = overwritten (absorptions:
only a row that dev has closed and prod still has open), prod-only = left behind.

Guards (id, value, limit, flag that lifts it). `Guard.tripped` is the raw condition and does not depend on a lift flag, and no lift
flag is part of the plan hash, so a dry run's hash stays valid for an apply that lifts a guard; `effective_blocking` applies the lifts.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

from sync import registry, report
from sync.model import Klass, ScopeKind
from sync.promote import digest as dg
from sync.promote.files_gate import FilesReport, classify

TOOL = "sync.promote"
SECTION = "data"
ENTRY = "images"

MASS_CHANGE_FRACTION = 0.25
SHRINK_MIN_ROWS = 100
SHRINK_FRACTION = 0.02
COVERAGE_TABLES = ("ocr_lemmas", "embeddings", "tags", "image_descriptions", "meme_profiles")
COVERAGE_FRACTION = 0.01
ADVICE_FRACTION = 0.25
ASSUMED_BYTES_PER_S = 50_000_000 / 8  # the design's first-seed estimate: 50 Mbit/s
DEFAULT_BATCH_SIZE = 500
DEFAULT_REQUIRED = ("embeddings", "ocr_texts")

LIFT_MASS = "--allow-mass-change"
LIFT_SHRINK = "--allow-shrink"
LIFT_MODEL = "--allow-model-change"
MODEL_TABLES = ("meme_profiles", "meme_profile_situation_embeddings")


class PlanError(ValueError):
    """An unusable plan input (a flag naming a table that cannot be promoted)."""


# ---------------------------------------------------------------------------------------------------------------------------------
# --only expansion

def _fk_parents(table: str) -> set:
    from Storage.models import Base

    return {fk.column.table.name for fk in Base.metadata.tables[table].foreign_keys}


def resolve_only(only: Iterable[str] | None) -> tuple:
    """`(image tables, global tables)` to compare, in load order. No `--only`: every class D table. With it: the named tables, their
    parents (an image table needs `images`; a `via` table its parent; a global table the global tables it references) and, for a
    named table that is the parent of others (e.g. `image_descriptions`), its children, because replacing a parent's rows
    cascades into them."""
    image_all, global_all = dg.image_scoped_tables(), dg.global_tables()
    if only is None:
        return image_all, global_all
    names = list(dict.fromkeys(only))
    known = set(registry.names(Klass.D))
    bad = [n for n in names if n not in known]
    if bad:
        raise PlanError(f"--only names tables that are not class D: {', '.join(sorted(bad))}")
    img = {n for n in names if n in image_all}
    glb = {n for n in names if n in global_all}
    if img:
        img.add("images")
    changed = True
    while changed:
        changed = False
        for t in list(img):
            scope = registry.get(t).scope
            if scope.kind is ScopeKind.VIA and scope.parent not in img:
                img.add(scope.parent)
                changed = True
        for t in image_all:
            scope = registry.get(t).scope
            if scope.kind is ScopeKind.VIA and scope.parent in img and t not in img and scope.parent in names:
                img.add(t)
                changed = True
    changed = True
    while changed:
        changed = False
        for t in list(glb):
            for parent in _fk_parents(t) & set(global_all):
                if parent not in glb:
                    glb.add(parent)
                    changed = True
    return dg.table_order(img), dg.table_order(glb)


# ---------------------------------------------------------------------------------------------------------------------------------
# input and result

@dataclass
class PlanInput:
    env: str
    dev_images: list  # DevImage, ordered by created_at, id
    prod_ids: set
    dev_digests: dict  # image id -> table -> TableDigest (promotable images; at least the compared and the required tables)
    prod_digests: dict  # image id -> table -> TableDigest (every prod image; the compared tables)
    tables: list  # image-scoped tables compared (load order); empty: the image part is not planned
    required: list = field(default_factory=lambda: list(DEFAULT_REQUIRED))
    manifest_hash: str | None = None
    first_seed: bool = False
    files: FilesReport | None = None  # a valid files report, or None (nothing is classified)
    limit_images: int | None = None
    global_tables: list = field(default_factory=list)
    dev_globals: dict = field(default_factory=dict)  # table -> {key: (hash, closed)}
    prod_globals: dict = field(default_factory=dict)
    only: list | None = None
    batch_size: int = DEFAULT_BATCH_SIZE
    model_change: bool = False  # the `model_pair_change` precondition found a different model pair or taxonomy version on dev


@dataclass
class Plan:
    section: report.Section
    new: list = field(default_factory=list)  # planned adds, ordered by created_at, id
    changed: dict = field(default_factory=dict)  # planned overwrites: id -> changed groups, in the same order
    unchanged: list = field(default_factory=list)
    left_behind: list = field(default_factory=list)
    skipped: dict = field(default_factory=dict)  # reason -> ids
    globals: dict = field(default_factory=dict)  # table -> {"added": [...], "overwritten": [...], "unchanged": n, "left_behind": [...]}
    advice: list = field(default_factory=list)  # tables whose planned rows exceed 25 % of the table: rebuild the indexes afterwards

    @property
    def guards(self) -> list:
        return self.section.guards

    @property
    def tripped(self) -> list:
        return [g for g in self.section.guards if g.tripped]

    @property
    def write_order(self) -> list:
        """Image ids of this plan in apply order (`created_at, id`): adds and overwrites interleaved as dev orders them."""
        return list(self._order)

    _order: list = field(default_factory=list, repr=False)


def _count(per_table: dict, table: str) -> int:
    return per_table.get(table, dg.EMPTY).count


def _bytes(per_table: dict, tables: Iterable[str]) -> int:
    return sum(per_table.get(t, dg.EMPTY).bytes for t in tables)


# ---------------------------------------------------------------------------------------------------------------------------------
# plan

def build_plan(inp: PlanInput) -> Plan:
    tables = dg.table_order(inp.tables)
    group_tables = dg.groups_for(tables)
    dev_by_id = {r.id: r for r in inp.dev_images}
    dev_ids = list(dev_by_id)  # created_at, id order
    dev_set = set(dev_ids)
    prod_ids = set(inp.prod_ids)
    plan_images = bool(tables)

    entries = []
    guards = []
    skipped: dict = {}
    new_planned: list = []
    changed_planned: dict = {}
    unchanged: list = []
    left_behind = sorted(prod_ids - dev_set) if plan_images else []
    table_rows: dict = {}
    advice: list = []
    order: list = []
    added_bytes = over_bytes = 0

    if plan_images:
        existing = [i for i in dev_ids if i in prod_ids]
        new_all = [i for i in dev_ids if i not in prod_ids]

        changed_all: dict = {}
        for i in existing:
            dh = dg.image_group_hashes(inp.dev_digests.get(i, {}), group_tables)
            ph = dg.image_group_hashes(inp.prod_digests.get(i, {}), group_tables)
            diff = [g for g in report.GROUPS if g in group_tables and dh[g] != ph[g]]
            if diff:
                changed_all[i] = diff
        unchanged = [i for i in existing if i not in changed_all]

        # completeness floor (M8): a new image needs a row in every required table; a changed image is never held back by it
        floor_miss = [i for i in new_all if any(_count(inp.dev_digests.get(i, {}), t) == 0 for t in inp.required)]
        if floor_miss:
            skipped["missing_required_table"] = floor_miss
        floor_set = set(floor_miss)

        # files gate: what would be written must have its file verified on prod (report of P3), same id, filename and content hash
        candidates = [i for i in dev_ids if (i in changed_all or i not in prod_ids) and i not in floor_set]
        gate_set: set = set()
        if inp.files is not None and candidates:
            for reason, ids in classify(inp.files, [dev_by_id[i] for i in candidates]).items():
                skipped.setdefault(reason, []).extend(ids)
                gate_set.update(ids)

        writable = [i for i in candidates if i not in gate_set]
        if inp.limit_images is not None and len(writable) > inp.limit_images:
            skipped["capped_by_limit"] = writable[inp.limit_images:]
            writable = writable[:inp.limit_images]
        wset = set(writable)
        new_planned = [i for i in new_all if i in wset]
        changed_planned = {i: changed_all[i] for i in dev_ids if i in changed_all and i in wset}
        order = [i for i in dev_ids if i in wset]

        for t in tables:
            added_rows = sum(_count(inp.dev_digests.get(i, {}), t) for i in new_planned)
            written_rows = sum(_count(inp.dev_digests.get(i, {}), t) for i in changed_planned)
            replaced_rows = sum(_count(inp.prod_digests.get(i, {}), t) for i in changed_planned)
            table_rows[t] = {
                "added_rows": added_rows, "overwritten_rows": written_rows, "replaced_rows": replaced_rows,
                "unchanged_rows": sum(_count(inp.dev_digests.get(i, {}), t) for i in unchanged),
                "added_bytes": sum(inp.dev_digests.get(i, {}).get(t, dg.EMPTY).bytes for i in new_planned),
                "overwritten_bytes": sum(inp.dev_digests.get(i, {}).get(t, dg.EMPTY).bytes for i in changed_planned),
            }
            total = sum(_count(d, t) for d in inp.dev_digests.values())
            if total and added_rows + written_rows > ADVICE_FRACTION * total:
                advice.append(t)
        added_bytes = sum(_bytes(inp.dev_digests.get(i, {}), tables) for i in new_planned)
        over_bytes = sum(_bytes(inp.dev_digests.get(i, {}), tables) for i in changed_planned)

        guards += _image_guards(inp, tables, existing, dev_ids, prod_ids, len(changed_planned))
        # only when a profile table is compared: the affected rows are overwrites by digest, the flag is the owner's acknowledgement
        # M2: a new image written without its required derived tables would be served by prod without OCR or an embedding; an `--only`
        # that leaves a required table out is refused (exit 3, no lift) while the plan adds images. Nothing is written for it.
        missing_required = [t for t in inp.required if t not in tables]
        guards.append(report.Guard("only_excludes_required_tables", bool(new_planned and missing_required), len(new_planned), 0))
        guards.append(report.Guard("model_change", inp.model_change and any(t in tables for t in MODEL_TABLES), int(inp.model_change), 0,
                                   LIFT_MODEL))

        entries.append(report.Entry(
            name=ENTRY, kind="images",
            added=report.IdSet(ids=new_planned, bytes=added_bytes),
            overwritten=report.Overwritten(ids=list(changed_planned), groups=changed_planned, bytes=over_bytes),
            unchanged=report.IdSet(ids=unchanged),
            skipped={r: report.IdSet(ids=ids) for r, ids in sorted(skipped.items())},
            left_behind=report.LeftBehind(ids=left_behind),
        ))

    glob: dict = {}
    if inp.limit_images is None:  # a capped run never touches the global sets
        for t in dg.table_order(inp.global_tables):
            plan_t = _plan_global(t, inp.dev_globals.get(t, {}), inp.prod_globals.get(t, {}))
            glob[t] = plan_t
            entries.append(report.Entry(
                name=t, kind="rows",
                added=report.IdSet(ids=plan_t["added"]),
                overwritten=report.Overwritten(ids=plan_t["overwritten"]),
                unchanged=report.IdSet(ids=[], count=plan_t["unchanged"]),
                left_behind=report.LeftBehind(ids=plan_t["left_behind"]),
            ))

    wire = added_bytes + over_bytes
    batches = math.ceil(len(order) / max(1, inp.batch_size))
    estimate = {"duration_s": round(wire / ASSUMED_BYTES_PER_S + 2 * batches, 1), "wire_bytes": wire}
    flags: dict = {}
    if inp.first_seed:
        flags["first_seed"] = True
    for t in sorted(inp.only or []):
        flags[f"only:{t}"] = True
    if inp.limit_images is not None:
        flags["limit_images"] = inp.limit_images
    section = report.Section(name=SECTION, tool=TOOL, entries=entries, guards=guards, flags=flags,
                             manifest_hash=inp.manifest_hash, estimate=estimate, table_rows=table_rows)
    return Plan(section=section, new=new_planned, changed=changed_planned, unchanged=unchanged, left_behind=left_behind,
                skipped=skipped, globals=glob, advice=advice, _order=order)


def _plan_global(table: str, dev: dict, prod: dict) -> dict:
    added = [k for k in dev if k not in prod]
    left = [k for k in prod if k not in dev]
    both = [k for k in dev if k in prod]
    if table == dg.ABSORPTIONS:  # insert-or-close: only a row dev has closed and prod still has open is an overwrite
        over = [k for k in both if dev[k][1] and not prod[k][1]]
    else:
        over = [k for k in both if dev[k][0] != prod[k][0]]
    return {"added": added, "overwritten": over, "unchanged": len(both) - len(over), "left_behind": left}


def _image_guards(inp: PlanInput, tables: list, existing: list, dev_ids: list, prod_ids: set, changed_n: int) -> list:
    guards = []
    n_dev, n_prod = len(dev_ids), len(prod_ids)
    guards.append(report.Guard("zero_images", n_dev == 0, n_dev, 1))
    guards.append(report.Guard("dev_emptied", n_prod > 0 and n_dev * 2 < n_prod, n_dev, (n_prod + 1) // 2))
    mass_limit = int(MASS_CHANGE_FRACTION * n_prod)
    guards.append(report.Guard("mass_change", (not inp.first_seed) and n_prod > 0 and changed_n > MASS_CHANGE_FRACTION * n_prod,
                               changed_n, mass_limit, LIFT_MASS))
    for t in tables:
        dev_total = sum(_count(inp.dev_digests.get(i, {}), t) for i in existing)
        prod_total = sum(_count(inp.prod_digests.get(i, {}), t) for i in existing)
        tolerance = max(SHRINK_MIN_ROWS, math.ceil(SHRINK_FRACTION * prod_total))
        shrink = prod_total - dev_total
        guards.append(report.Guard(f"row_regression:{t}", shrink > tolerance, shrink, tolerance, LIFT_SHRINK))
    n = len(existing)
    for t in COVERAGE_TABLES:
        if t not in tables:
            continue
        dev_with = sum(1 for i in existing if _count(inp.dev_digests.get(i, {}), t) > 0)
        prod_with = sum(1 for i in existing if _count(inp.prod_digests.get(i, {}), t) > 0)
        lost = sum(1 for i in existing if _count(inp.dev_digests.get(i, {}), t) == 0 and _count(inp.prod_digests.get(i, {}), t) > 0)
        worst = max(lost, prod_with - dev_with)
        limit = round(COVERAGE_FRACTION * n, 6)
        guards.append(report.Guard(f"coverage_regression:{t}", n > 0 and worst > limit, worst, limit, LIFT_SHRINK))
    return guards


# ---------------------------------------------------------------------------------------------------------------------------------
# lifts

def effective_blocking(plan: Plan, allow_mass_change: int | None = None, allow_shrink: Iterable[str] = (),
                       allow_model_change: bool = False) -> list:
    """Ids of the tripped guards that no lift flag removes. `--allow-mass-change N` lifts only when N equals the planned changed
    count exactly (a wrong N lifts nothing); `--allow-shrink TABLE` lifts the row and coverage regression guards of that table.
    `--allow-model-change` lifts the model-change guard. The zero-image, dev-emptied and id-overlap guards have no lift."""
    shrink = set(allow_shrink)
    blocking = []
    for g in plan.section.guards:
        if not g.tripped:
            continue
        base, _, table = g.id.partition(":")
        if base == "mass_change" and allow_mass_change is not None and allow_mass_change == g.value:
            continue
        if base == "model_change" and allow_model_change:
            continue
        if base in ("row_regression", "coverage_regression") and table in shrink:
            continue
        blocking.append(g.id)
    return blocking


def lift_problems(plan: Plan, allow_mass_change: int | None, allow_shrink: Iterable[str], allow_model_change: bool = False) -> list:
    """Codes for lift flags that do nothing useful: a wrong N, or a table that has no guard in this plan."""
    problems = []
    by_id = {g.id: g for g in plan.section.guards}
    if allow_mass_change is not None:
        g = by_id.get("mass_change")
        if g is None or not g.tripped:
            problems.append("allow_mass_change_not_needed")
        elif g.value != allow_mass_change:
            problems.append("allow_mass_change_wrong_n")
    if allow_model_change:
        g = by_id.get("model_change")
        if g is None or not g.tripped:
            problems.append("allow_model_change_not_needed")
    for t in allow_shrink:
        if f"row_regression:{t}" not in by_id:
            problems.append(f"allow_shrink_unknown_table:{t}")
    return problems
