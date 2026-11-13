"""P2-T5: the digest SQL generator (no database): exact column lists, determinism, the registry's digest_exclude and nullify,
the image expression per scope, pinned session settings."""
import pytest

from sync import registry
from sync.model import Klass
from sync.promote import digest as dg


class FakeConn:
    def __init__(self, settings=None):
        self.settings = dict(settings or {})
        self.statements = []

    def query(self, sql, args=None):
        self.statements.append((sql, args))
        if sql.startswith("SET "):
            name, value = sql[4:].split(" = ")
            self.settings[name.lower()] = value.strip("'")
        return []

    def scalar(self, sql, args=None):
        assert sql == "select current_setting(%s)"
        return self.settings.get(args[0].lower())


def row_list(table):
    sql = dg.image_digest_sql(table, "all")
    start = sql.index("ROW(") + 4
    return sql[start:sql.index(")::text AS row_text")]


def test_tags_columns_are_alphabetical_without_the_excluded_surrogate_id_and_created_at():
    assert row_list("tags") == 't."image_id", t."key", t."source", t."stale", t."value"'


def test_vectors_are_cast_to_text_and_json_too():
    assert row_list("embeddings") == 't."embedding"::text, t."image_id"'  # id and created_at are excluded
    assert 't."bbox"::text' in row_list("ocr_texts")  # json (not jsonb)


def test_images_exclude_the_blanked_batch_column_and_keep_popularity():
    cols = row_list("images")
    assert "ingestion_batch_id" not in cols and 't."popularity"' in cols and 't."filename"' in cols
    assert cols.index('t."content_hash"') < cols.index('t."created_at"') < cols.index('t."filename"')


def test_nullified_columns_are_read_as_null_in_position():
    exprs = dg.digest_exprs("popularity_absorptions")
    names = [n for n, _ in dg.model_columns("popularity_absorptions")]
    assert len(exprs) == len(names) and names == sorted(names)
    assert exprs[names.index("batch_run_id")] == "NULL"
    assert exprs[names.index("created_at")] == 't."created_at"'


@pytest.mark.parametrize("table", dg.image_scoped_tables())
def test_every_image_table_uses_an_explicit_sorted_column_list(table):
    exclude = {c for c, _ in registry.get(table).digest_exclude}
    expect = sorted(n for n, _ in dg.model_columns(table) if n not in exclude)
    got = [e.split(".")[1].split("::")[0].strip('"') for e in dg.digest_exprs(table)]
    assert got == expect
    sql = dg.image_digest_sql(table, "promotable")
    for forbidden in ("t::text", "concat_ws", "SELECT *", "t.*)::text", ", *"):
        assert forbidden not in sql
    assert "hashtextextended(r.row_text, 0)" in sql and "hashtextextended(r.row_text, 1)" in sql
    assert "ORDER BY 1" in sql and sql == dg.image_digest_sql(table, "promotable")  # deterministic


def test_stale_flags_approved_and_popularity_are_never_excluded():
    for table in dg.image_scoped_tables():
        excluded = {c for c, _ in registry.get(table).digest_exclude}
        assert not excluded & {"stale", "approved", "popularity"}
    assert 't."approved"' in row_list("image_description_feedback")


def test_image_expression_per_scope():
    assert dg.image_expr("images") == ('t."id"', "")
    assert dg.image_expr("tags") == ('t."image_id"', "")
    expr, join = dg.image_expr("description_note_embeddings")
    assert expr == 'p."image_id"' and join == ' JOIN "description_notes" AS p ON p."image_id" = t."description_note_id"'
    expr, join = dg.image_expr("image_description_embeddings")
    assert expr == 'p."image_id"' and 'p."id" = t."image_description_id"' in join


def test_restrictions():
    promotable = dg.image_digest_sql("tags", "promotable")
    assert " JOIN images ON images.id = t.\"image_id\" WHERE images.status = 'active' AND NOT EXISTS" in promotable
    assert "WHERE" not in dg.image_digest_sql("tags", "all")
    assert dg.image_digest_sql("tags", "ids").endswith('WHERE t."image_id" = ANY(%s::uuid[]) GROUP BY 1 ORDER BY 1')
    with pytest.raises(dg.DigestError):
        dg.image_digest_sql("tags", "nothing")


def test_global_and_class_checks():
    with pytest.raises(dg.DigestError):
        dg.image_digest_sql("concepts")  # a global set has no image
    with pytest.raises(dg.DigestError):
        dg.image_digest_sql("meme_saves")  # class P
    with pytest.raises(dg.DigestError):
        dg.global_digest_sql("tags")
    sql = dg.global_digest_sql("popularity_absorptions")
    assert "(t.\"reversed_by_id\" IS NOT NULL) AS closed" in sql and "md5(r.row_text)" in sql
    assert "closed" not in dg.global_digest_sql("concepts")
    assert dg.global_tables() == ["concepts", "popularity_absorptions", "concept_image_sets", "concept_text_sets", "concept_images",
                                  "concept_texts"]


def test_the_two_table_sets_cover_class_d_exactly():
    assert sorted(dg.image_scoped_tables() + dg.global_tables()) == registry.names(Klass.D)
    assert dg.image_scoped_tables()[0] == "images"


def test_pin_session_sets_and_reads_back():
    conn = FakeConn()
    assert dg.pin_session(conn) == []
    assert [s for s, _ in conn.statements if s.startswith("SET ")] == [f"SET {n} = '{v}'" for n, v in dg.PINNED_SETTINGS]
    stubborn = FakeConn()
    stubborn.query = lambda sql, args=None: []  # a server that ignores SET
    stubborn.scalar = lambda sql, args=None: "other"
    assert dg.pin_session(stubborn) == [n.lower() for n, _ in dg.PINNED_SETTINGS]


def test_group_hashes_are_stable_and_see_missing_tables():
    a = dg.TableDigest(2, "1", "2", 10)
    same = dg.TableDigest(2, "1", "2", 999)  # bytes are a measurement, not part of the hash
    other = dg.TableDigest(2, "1", "3")
    groups = dg.groups_for(["images", "image_extras", "tags"])
    assert groups == {"core": ["images", "image_extras"], "tags": ["tags"]}
    base = dg.image_group_hashes({"images": a}, groups)
    assert base == dg.image_group_hashes({"images": same}, groups)
    assert base["core"] != dg.image_group_hashes({"images": other}, groups)["core"]
    assert base["core"] != dg.image_group_hashes({"images": a, "image_extras": a}, groups)["core"]  # a row appeared
    assert base["tags"] == dg.image_group_hashes({}, groups)["tags"]  # a missing table is the empty digest


def test_table_order_is_load_order_then_name():
    assert dg.table_order(["tags", "images", "image_descriptions", "image_description_embeddings", "embeddings"]) == [
        "images", "embeddings", "image_descriptions", "tags", "image_description_embeddings"]


def test_fetch_parses_rows_and_dev_images_are_ordered():
    class Conn:
        def query(self, sql, args=None):
            if "FROM images WHERE" in sql:
                return [("b", "f2", None, "2026-01-02"), ("a", "f1", "h", "2026-01-02"), ("c", "f3", "h", None), ("d", "f4", "h", "2026-01-01")]
            return [("00000000-0000-0000-0000-000000000001", 2, "5", "-7", 100)]

    assert [r.id for r in dg.fetch_dev_images(Conn())] == ["d", "a", "b", "c"]
    got = dg.fetch_image_digests(Conn(), ["tags"], "all")
    assert got == {"00000000-0000-0000-0000-000000000001": {"tags": dg.TableDigest(2, "5", "-7", 100)}}
