"""P4-T2: reading the six class P tables, on prod (the sync_reader snapshot) and on dev, as hashed rows.

Every function takes an open connection (`SyncConnection`, `.query` / `.scalar`) and issues SELECTs only. The prod snapshot is one
`REPEATABLE READ, READ ONLY` transaction (`snapshot`), so the tables, the image-id list, the ratings fingerprint and the later
`COPY (SELECT ...)` streams of the apply are one consistent snapshot. A row is represented by its primary key and `md5(row(...)::text)` over
the table's columns in alphabetical order, with the free-text columns cut by `left()` at the text cap; no row text is returned to Python
(except the ratings archive of the apply, which reads dev rows as `to_jsonb`).
"""
from __future__ import annotations

import contextlib
import re
from typing import Any, Iterator

from sync.promote import digest as dg
from sync.pull import plan as plan_mod

_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")
SNAPSHOT_TIME_SQL = "to_char(now() at time zone 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')"


class SnapshotError(ValueError):
    """A table name that is not one of the six (a programming error, never a data problem)."""


def ident(name: str) -> str:
    if not _NAME.match(name):
        raise SnapshotError("bad_identifier")
    return f'"{name}"'


def table_ident(table: str) -> str:
    if table not in plan_mod.EXPECTED_TABLES:
        raise SnapshotError("not_a_class_p_table")
    return ident(table)


@contextlib.contextmanager
def snapshot(conn: Any) -> Iterator[None]:
    """One consistent read-only snapshot on a connection (`REPEATABLE READ READ ONLY`); the connection is in autocommit mode."""
    conn.query("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
    try:
        yield
    finally:
        conn.query("COMMIT")


def snapshot_facts(conn: Any) -> tuple:
    """`(pg_current_snapshot() text, transaction start time as UTC ISO text)`; the first statement of the snapshot."""
    row = conn.query(f"select pg_current_snapshot()::text, {SNAPSHOT_TIME_SQL}")[0]
    return row[0], row[1]


def columns(table: str) -> list:
    """The table's columns in alphabetical order: the one order of every COPY list and every row hash."""
    table_ident(table)
    return [name for name, _ in dg.model_columns(table)]


def column_expr(table: str, column: str, cap: int) -> str:
    ref = ident(column)
    return f"left({ref}, {int(cap)})" if column in plan_mod.TEXT_COLUMNS.get(table, ()) else ref


def select_list(table: str, cap: int) -> str:
    """The columns of a COPY or SELECT stream, free text cut at the cap."""
    return ", ".join(column_expr(table, c, cap) for c in columns(table))


def row_expr(table: str, cap: int) -> str:
    return "row(" + select_list(table, cap) + ")::text"


def key_expr(table: str) -> str:
    return '"id"' if table in plan_mod.INT_ID_TABLES else '"id"::text'


def count_sql(table: str) -> str:
    return f"select count(*) from {table_ident(table)}"


def hash_sql(table: str, cap: int) -> str:
    return f"select k, md5(r), octet_length(r) from (select {key_expr(table)} as k, {row_expr(table, cap)} as r from {table_ident(table)}) s"


def truncated_sql(table: str, cap: int) -> str | None:
    cols = plan_mod.TEXT_COLUMNS.get(table)
    if not cols:
        return None
    terms = " + ".join(f"coalesce((length({ident(c)}) > {int(cap)})::int, 0)" for c in cols)
    return f"select coalesce(sum({terms}), 0) from {table_ident(table)}"


def digest_sql(table: str, cap: int) -> str:
    """Count and one aggregate hash of the whole table (collation-free order): the in-transaction check of the apply."""
    return ("select count(*), md5(coalesce(string_agg(k || ':' || h, ',' order by k collate \"C\"), '')) from "
            f"(select {key_expr(table)}::text as k, md5({row_expr(table, cap)}) as h from {table_ident(table)}) s")


def read_table(conn: Any, table: str, cap: int, ceiling: int | None = None, with_truncated: bool = False) -> plan_mod.TableRead:
    """Count and hashed rows of one table. Over `ceiling` rows only the count is read (`read` False)."""
    count = int(conn.scalar(count_sql(table)))
    truncated = 0
    if with_truncated:
        sql = truncated_sql(table, cap)
        truncated = int(conn.scalar(sql)) if sql else 0
    if ceiling is not None and count > ceiling:
        return plan_mod.TableRead(count, {}, False, truncated)
    rows = {r[0]: (r[1], int(r[2])) for r in conn.query(hash_sql(table, cap))}
    return plan_mod.TableRead(count, rows, True, truncated)


def read_ratings(conn: Any, cap: int) -> dict:
    """`(image_id, query_hash) -> RatingRow(id, hash, bytes)` of the ratings table."""
    expr = row_expr(plan_mod.RATINGS, cap)
    sql = (f"select id::text, image_id::text, query_hash, md5(r), octet_length(r) from "
           f"(select id, image_id, query_hash, {expr} as r from {table_ident(plan_mod.RATINGS)}) s")
    return {(r[1], r[2]): plan_mod.RatingRow(r[0], r[3], int(r[4])) for r in conn.query(sql)}


def read_image_ids(conn: Any) -> frozenset:
    return frozenset(r[0] for r in conn.query("select id::text from images"))


def ratings_fingerprint(conn: Any, mode: str) -> dict:
    """`{mode, prod_count, max_updated_at, keys_sha256}` from the snapshot (no text), also in `skip` mode."""
    table = table_ident(plan_mod.RATINGS)
    count, newest = conn.query(f"select count(*), to_char(max(updated_at), 'YYYY-MM-DD\"T\"HH24:MI:SS.US') from {table}")[0]
    lines = [plan_mod.key_line(r[0], r[1]) for r in conn.query(f"select image_id::text, query_hash from {table}")]
    return {"mode": mode, "prod_count": int(count), "max_updated_at": newest, "keys_sha256": plan_mod.keys_sha256(lines)}


def table_digest(conn: Any, table: str, cap: int) -> tuple:
    row = conn.query(digest_sql(table, cap))[0]
    return int(row[0]), row[1]


# ---------------------------------------------------------------------------------------------------------------------------------
# column compatibility (design "Preconditions")

COLUMNS_SQL = (
    "select table_name, column_name, data_type, udt_name from information_schema.columns "
    "where table_schema = current_schema() and table_name = any(%s) and (table_name <> 'images' or column_name = 'id') order by 1, 2"
)


def column_signature(conn: Any) -> list:
    """`(table, column, data_type, udt_name)` of the six tables and `images.id`; the reader sees only the columns it may select."""
    return [tuple(r) for r in conn.query(COLUMNS_SQL, ([*plan_mod.EXPECTED_TABLES, "images"],))]
