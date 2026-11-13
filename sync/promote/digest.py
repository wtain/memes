"""P2-T5: the digest SQL generator of `sync.promote` (design P2 "Plan", step 2) and the reads that use it.

Per image and per table one query returns `(image id, row count, two summed row hashes, bytes)`. Rules (design, review H6/M3):

- every digest session pins `TimeZone`, `DateStyle`, `extra_float_digits`, `bytea_output`, `IntervalStyle` and `client_encoding`
  and reads them back (`pin_session`), so the text of a row does not depend on the session or the locale;
- the row text is `ROW(c1, c2, ...)::text` over an explicit column list: the columns of `Storage.models` for the table in
  alphabetical order, minus the registry's `digest_exclude` (never `t::text`, never `*`, never `concat_ws`); a column the registry
  `nullify`s is read as NULL on both sides (prod stores NULL there); `vector` and `json` columns are cast to text;
- two sums of `hashtextextended(row_text, seed)::numeric` (seeds 0 and 1), not `bit_xor`, so identical rows do not cancel;
- a group hash is the md5 over the per-table `count:h0:h1` tokens of the group's tables in load order (a table without rows of
  the image contributes `0:0:0`).

Only `(image id, tokens)` crosses the tunnel. No row value is ever read into Python. Reads go through `SyncConnection.query`, so a
database error is already reduced to SQLSTATE, constraint and table.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable

from sync import registry
from sync.model import PROMOTABLE_IMAGES_SQL, Klass, ScopeKind

PINNED_SETTINGS = (
    ("TimeZone", "UTC"),
    ("DateStyle", "ISO, YMD"),
    ("extra_float_digits", "1"),
    ("bytea_output", "hex"),
    ("IntervalStyle", "iso_8601"),
    ("client_encoding", "UTF8"),
)

RESTRICTIONS = ("promotable", "all", "ids")
ABSORPTIONS = "popularity_absorptions"
KEY_CHUNK = 5000  # primary keys per restricted global-set query


class DigestError(ValueError):
    """A table that cannot be digested (not class D, or a global set asked for per image)."""


# ---------------------------------------------------------------------------------------------------------------------------------
# session pinning

def pin_session(conn: Any) -> list:
    """SET every pinned setting on `conn` and read it back; returns the names that did not take (empty = pinned)."""
    problems = []
    for name, value in PINNED_SETTINGS:
        conn.query(f"SET {name} = '{value}'")
        got = conn.scalar("select current_setting(%s)", (name,))
        if str(got) != value:
            problems.append(name.lower())
    return problems


# ---------------------------------------------------------------------------------------------------------------------------------
# columns

def _kind(sa_type: Any) -> str:
    import sqlalchemy as sa

    name = type(sa_type).__name__
    if name.lower() == "vector":
        return "vector"
    if type(sa_type) is sa.JSON:  # exact class: JSONB (a subclass) is normalised by Postgres and read as is
        return "json"
    return "plain"


def model_columns(table: str) -> list:
    """`(name, kind)` of the table's columns in `Storage.models`, alphabetical; kind is `vector`, `json` or `plain`."""
    from Storage.models import Base

    return sorted((c.name, _kind(c.type)) for c in Base.metadata.tables[table].columns)


def digest_exprs(table: str, columns: Iterable | None = None, alias: str = "t") -> list:
    """SQL expressions of the row text, in column order: alphabetical, minus `digest_exclude`; nullified columns read NULL."""
    entry = registry.get(table)
    excluded = {c for c, _ in entry.digest_exclude}
    out = []
    for name, kind in sorted(columns if columns is not None else model_columns(table)):
        if name in excluded:
            continue
        if name in entry.nullify:
            out.append("NULL")
            continue
        ref = f'{alias}."{name}"'
        out.append(f"{ref}::text" if kind in ("vector", "json") else ref)
    return out


def _check_image_table(table: str):
    entry = registry.get(table)
    if entry.klass is not Klass.D or entry.scope.kind in (ScopeKind.GLOBAL_SET, ScopeKind.NEVER, ScopeKind.PROD_ALL):
        raise DigestError(f"{table} is not an image-scoped class D table")
    return entry


def image_scoped_tables() -> list:
    """Class D tables that hang off an image, in load order (parents first), then by name."""
    return sorted((e.name for e in registry.REGISTRY.values()
                   if e.klass is Klass.D and e.scope.kind in (ScopeKind.IMAGES, ScopeKind.IMAGE_FK, ScopeKind.VIA)),
                  key=lambda t: (registry.get(t).level, t))


def global_tables() -> list:
    return sorted((e.name for e in registry.REGISTRY.values() if e.klass is Klass.D and e.scope.kind is ScopeKind.GLOBAL_SET),
                  key=lambda t: (registry.get(t).level, t))


def image_expr(table: str) -> tuple:
    """`(SQL of the owning image id, extra JOIN clause)` for an image-scoped table (alias `t`; parent alias `p`)."""
    entry = _check_image_table(table)
    scope = entry.scope
    if scope.kind is ScopeKind.IMAGES:
        return 't."id"', ""
    if scope.kind is ScopeKind.IMAGE_FK:
        return f't."{scope.column}"', ""
    parent = registry.get(scope.parent)
    if parent.scope.kind is not ScopeKind.IMAGE_FK:
        raise DigestError(f"{table}: the parent {parent.name} must be scoped by an image column")
    return f'p."{parent.scope.column}"', f' JOIN "{parent.name}" AS p ON p."{parent.key[0]}" = t."{scope.column}"'


def image_digest_sql(table: str, restrict: str = "promotable", columns: Iterable | None = None) -> str:
    """The per-image digest query of one table. `restrict`: `promotable` (dev: join `images` with the promotable predicate),
    `all` (prod: every image) or `ids` (one `%s` parameter: a list of image uuids)."""
    if restrict not in RESTRICTIONS:
        raise DigestError(f"unknown restriction {restrict!r}")
    img, join = image_expr(table)
    exprs = ", ".join(digest_exprs(table, columns))
    sql = (
        f'SELECT {img}::text AS image_id, count(*)::bigint AS n, '
        f'sum(hashtextextended(r.row_text, 0)::numeric)::text AS h0, '
        f'sum(hashtextextended(r.row_text, 1)::numeric)::text AS h1, '
        f'coalesce(sum(pg_column_size(t.*)), 0)::bigint AS bytes '
        f'FROM "{table}" AS t{join} CROSS JOIN LATERAL (SELECT ROW({exprs})::text AS row_text) AS r'
    )
    if restrict == "promotable":
        sql += f" JOIN images ON images.id = {img} WHERE {PROMOTABLE_IMAGES_SQL}"
    elif restrict == "ids":
        sql += f" WHERE {img} = ANY(%s::uuid[])"
    return sql + " GROUP BY 1 ORDER BY 1"


def key_sql_type(table: str) -> str:
    """The postgres type name of a single-key global set's primary key (`int4` or `uuid`), for `= ANY(%s::<type>[])`."""
    from Storage.models import Base

    entry = registry.get(table)
    name = type(Base.metadata.tables[table].columns[entry.key[0]].type).__name__.lower()
    return "uuid" if name == "uuid" else "int4"


def global_digest_sql(table: str, columns: Iterable | None = None, keys: bool = False) -> str:
    """Per-row hash of a global set, by primary key. Absorptions also return whether the row is closed (`reversed_by_id` set).
    With `keys` the query takes one `%s` parameter, a list of primary keys, and returns only those rows."""
    entry = registry.get(table)
    if entry.klass is not Klass.D or entry.scope.kind is not ScopeKind.GLOBAL_SET or len(entry.key) != 1:
        raise DigestError(f"{table} is not a single-key global set")
    key = entry.key[0]
    exprs = ", ".join(digest_exprs(table, columns))
    closed = ', (t."reversed_by_id" IS NOT NULL) AS closed' if table == ABSORPTIONS else ""
    where = f' WHERE t."{key}" = ANY(%s::{key_sql_type(table)}[])' if keys else ""
    return (f'SELECT t."{key}" AS k, md5(r.row_text) AS h{closed} FROM "{table}" AS t '
            f'CROSS JOIN LATERAL (SELECT ROW({exprs})::text AS row_text) AS r{where} ORDER BY 1')


# ---------------------------------------------------------------------------------------------------------------------------------
# digests

@dataclass(frozen=True)
class TableDigest:
    count: int
    h0: str
    h1: str
    bytes: int = 0

    @property
    def token(self) -> str:
        return f"{self.count}:{self.h0}:{self.h1}"


EMPTY = TableDigest(0, "0", "0", 0)


def table_order(tables: Iterable[str]) -> list:
    return sorted(tables, key=lambda t: (registry.get(t).level, t))


def groups_for(tables: Iterable[str]) -> dict:
    """digest group -> its tables among `tables`, in load order (groups without a selected table are left out)."""
    chosen = set(tables)
    return {g: table_order(t for t in ts if t in chosen) for g, ts in registry.digest_groups().items()
            if any(t in chosen for t in ts)}


def group_hash(per_table: dict, tables: Iterable[str]) -> str:
    text = "|".join(f"{t}={per_table.get(t, EMPTY).token}" for t in tables)
    return hashlib.md5(text.encode("ascii")).hexdigest()


def image_group_hashes(per_table: dict, group_tables: dict) -> dict:
    """`per_table`: table -> TableDigest of one image; returns group -> hash."""
    return {g: group_hash(per_table, ts) for g, ts in group_tables.items()}


def fetch_image_digests(conn: Any, tables: Iterable[str], restrict: str, ids: list | None = None) -> dict:
    """image id (text) -> table -> TableDigest, for `tables` on one side. The connection must be pinned (`pin_session`)."""
    out: dict = {}
    for table in table_order(tables):
        sql = image_digest_sql(table, restrict)
        params = (list(ids),) if restrict == "ids" else None
        for image_id, n, h0, h1, nbytes in conn.query(sql, params):
            out.setdefault(str(image_id), {})[table] = TableDigest(int(n), str(h0), str(h1), int(nbytes))
    return out


def fetch_global_hashes(conn: Any, table: str, keys: list | None = None) -> dict:
    """primary key -> (row hash, closed flag or None) of a global set on one side; `keys` restricts it to those primary keys."""
    out: dict = {}
    if keys is None:
        batches = [None]
    else:
        batches = [keys[i:i + KEY_CHUNK] for i in range(0, len(keys), KEY_CHUNK)]
    for chunk in batches:
        rows = conn.query(global_digest_sql(table), None) if chunk is None else conn.query(global_digest_sql(table, keys=True), (chunk,))
        for row in rows:
            out[row[0]] = (str(row[1]), bool(row[2]) if len(row) > 2 else None)
    return out


# ---------------------------------------------------------------------------------------------------------------------------------
# plain reads of dev and prod (ids only; no row content)

DEV_IMAGES_SQL = (
    "SELECT images.id::text, images.filename, images.content_hash, images.created_at::text FROM images WHERE "
    + PROMOTABLE_IMAGES_SQL
)
PROD_IMAGE_IDS_SQL = "SELECT id::text FROM images"


@dataclass(frozen=True)
class DevImage:
    id: str
    filename: str
    content_hash: str | None
    created_at: str | None

    @property
    def order_key(self) -> tuple:
        return (self.created_at is None, self.created_at or "", self.id)


def fetch_dev_images(dev: Any) -> list:
    """The promotable images of dev (`PROMOTABLE_IMAGES_SQL`), ordered by `created_at, id`."""
    rows = [DevImage(str(i), f, h, c) for i, f, h, c in dev.query(DEV_IMAGES_SQL)]
    return sorted(rows, key=lambda r: r.order_key)


def fetch_prod_image_ids(prod: Any) -> set:
    return {str(r[0]) for r in prod.query(PROD_IMAGE_IDS_SQL)}
