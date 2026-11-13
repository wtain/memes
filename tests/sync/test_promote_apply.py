"""P2-T6 (no database): the SQL the apply builds, committed.json, setval filtering, the result block and the static guards on the
apply module. The behaviour against real tables is in tests/integration/sync/test_promote_apply.py."""
import json
import re
from pathlib import Path

import pytest

from sync import registry, report
from sync.model import Klass
from sync.promote import apply as A
from sync.promote import cli, digest as dg

I1 = "00000000-0000-0000-0000-000000000001"
I2 = "00000000-0000-0000-0000-000000000002"


def test_column_lists_are_alphabetical_and_identical_for_source_target_and_digest():
    for table in dg.image_scoped_tables():
        cols = A.column_names(table)
        assert cols == sorted(cols)
        in_sql = A.copy_in_sql(table)
        assert in_sql == f'COPY "{table}" (' + ", ".join(f'"{c}"' for c in cols) + ") FROM STDIN (FORMAT binary)"
        select = A.select_sql(table, [I1])
        assert select.count(" AS t") == 1 and select.startswith("SELECT ")
        exprs = select[len("SELECT "):select.index(" FROM ")].split(", ")
        assert len(exprs) == len(cols)
        for expr, col in zip(exprs, cols):
            assert expr == ("NULL" if col in registry.get(table).nullify else f't."{col}"')


def test_the_copy_out_is_a_filtered_binary_select_over_the_owning_image():
    sql = A.copy_out_sql("tags", [I1, I2])
    assert sql.startswith("COPY (SELECT ") and sql.endswith("TO STDOUT (FORMAT binary)")
    assert f"t.\"image_id\" = ANY('{{{I1},{I2}}}'::uuid[])" in sql
    via = A.copy_out_sql("image_description_embeddings", [I1])
    assert 'JOIN "image_descriptions" AS p ON p."id" = t."image_description_id"' in via and 'p."image_id" = ANY(' in via
    assert 'NULL' in A.select_sql("images", [I1])  # ingestion_batch_id is nullified


def test_image_ids_are_checked_before_they_reach_sql():
    for bad in ("x", "1; drop table images", "ABCDEF00-0000-0000-0000-000000000001", I1[:-1], "'"):
        with pytest.raises(A.ApplyError):
            A.uuid_array([bad])
    assert A.uuid_array([I1]) == "'{" + I1 + "}'::uuid[]"


def test_delete_sql_targets_derived_tables_only_and_never_images():
    assert A.delete_sql("tags", [I1]) == f"DELETE FROM \"tags\" WHERE \"image_id\" = ANY('{{{I1}}}'::uuid[])"
    via = A.delete_sql("description_lemmas", [I1])
    assert via.startswith('DELETE FROM "description_lemmas" AS t USING "image_descriptions" AS p WHERE p."id" = t."image_description_id"')
    for table in dg.image_scoped_tables():
        if table == "images":
            with pytest.raises(A.ApplyError):
                A.delete_sql(table, [I1])
        else:
            assert not re.search(r'DELETE FROM\s+"?images"?\b', A.delete_sql(table, [I1]))
    with pytest.raises(A.ApplyError):
        A.delete_sql("concepts", [I1])  # a global set is never deleted from


def test_images_is_upserted_by_key_with_every_other_column_updated():
    sql = A.images_upsert_sql()
    cols = A.column_names("images")
    assert sql.startswith("INSERT INTO images (" + ", ".join(f'"{c}"' for c in cols) + ") VALUES %s ON CONFLICT (\"id\") DO UPDATE SET ")
    updates = sql.split("DO UPDATE SET ")[1]
    assert '"id" = EXCLUDED."id"' not in updates
    assert all(f'"{c}" = EXCLUDED."{c}"' in updates for c in cols if c != "id")
    assert "%s" in A.images_select_sql() and "NULL" in A.images_select_sql()


def test_load_and_delete_order_follow_the_registry():
    tables = dg.image_scoped_tables()
    load, delete = A.load_sequence(tables), A.delete_sequence(tables)
    assert sorted(load) == sorted(tables) and delete == list(reversed(delete[::-1]))
    for child, parent in (("image_descriptions", "images"), ("image_description_embeddings", "image_descriptions"),
                          ("meme_profile_values", "meme_profiles"), ("description_note_embeddings", "description_notes")):
        assert load.index(parent) < load.index(child)
        assert delete.index(child) < delete.index(parent)
    assert load[0] == "images" and delete[-1] == "images"
    assert A.load_sequence(["tags", "images"]) == ["images", "tags"]


def test_committed_json_is_written_after_the_first_batch_and_resumed(tmp_path):
    log = A.CommittedLog(tmp_path, "20261113T120000Z-00000001", "general", ["images", "tags"])
    assert not (tmp_path / "committed.json").exists() and log.next_n == 1
    log.append(1, [I1], {I1: {"core": "a"}}, [], "2026-11-13T12:00:00Z")
    doc = json.loads((tmp_path / "committed.json").read_text())
    assert doc == {"run_id": "20261113T120000Z-00000001", "env": "general", "tables": ["images", "tags"],
                   "batches": [{"n": 1, "committed_at": "2026-11-13T12:00:00Z", "ids": [I1], "digests": {I1: {"core": "a"}}, "renamed": []}],
                   "globals": {"concepts": False, "absorptions": False}}
    again = A.CommittedLog(tmp_path, "20261113T120000Z-00000001", "general", ["images", "tags"])
    assert again.next_n == 2 and again.committed_ids == [I1]
    for run_id, env in (("20261113T120000Z-00000002", "general"), ("20261113T120000Z-00000001", "metal")):
        with pytest.raises(A.ApplyError, match="committed_json_foreign"):
            A.CommittedLog(tmp_path, run_id, env, [])
    (tmp_path / "committed.json").write_text("{not json")
    with pytest.raises(A.ApplyError, match="committed_json_unreadable"):
        A.CommittedLog(tmp_path, "20261113T120000Z-00000001", "general", [])
    assert not list(tmp_path.glob("*.tmp")) and not list(tmp_path.glob(".*.tmp"))


class FakeProd:
    """A prod connection that answers the setval queries and records everything."""

    def __init__(self, sequences, max_ids, state):
        self.log, self.sequences, self.max_ids, self.state = [], sequences, max_ids, state

    def scalar(self, sql, args=None):
        self.log.append(sql)
        if "pg_get_serial_sequence" in sql:
            return self.sequences.get(args[0].strip('"'))
        if sql.startswith("SELECT max("):
            return self.max_ids.get(re.search(r'FROM "(\w+)"', sql).group(1))

    def query(self, sql, args=None):
        self.log.append(sql)
        if "last_value" in sql:
            return [self.state[sql.split(" FROM ")[1]]]
        return []


def test_setval_never_lowers_and_ignores_class_p_tables():
    prod = FakeProd({"concepts": "public.concepts_id_seq", "concept_images": "public.concept_images_id_seq", "meme_impressions": "s"},
                    {"concepts": 9, "concept_images": 2, "meme_impressions": 99},
                    {"public.concepts_id_seq": (4, True), "public.concept_images_id_seq": (50, True)})
    done = A.setval_tables(prod)
    assert done == {"concepts": 9}  # concept_images: 2 < 51, left alone
    assert any(q.startswith("SELECT setval") for q in prod.log)
    assert not any("meme_impressions" in q for q in prod.log)  # class P: the registry lists it, a promote never touches it
    assert "meme_impressions" in registry.setval_tables() and registry.get("meme_impressions").klass is Klass.P
    fresh = FakeProd({"concepts": "public.concepts_id_seq"}, {"concepts": 1}, {"public.concepts_id_seq": (1, False)})
    assert A.setval_tables(fresh, ["concepts"]) == {"concepts": 1}  # a never-called sequence would hand out 1 again


def test_the_result_block_carries_counts_reasons_and_failed_ids_only():
    outcome = A.ApplyOutcome(added=[I1], overwritten=[], skipped={"files_gate_miss": [I2]}, failed=[], failure=None)
    sec = report.Section(name="data", tool="sync.promote", entries=[report.Entry(name="images", kind="images", added=report.IdSet(ids=[I1, I2]))])
    p = type("P", (), {"section": sec})()
    res = cli.result_of(p, outcome)
    entry = res["data"]["images"]
    assert (entry.added, entry.overwritten, entry.skipped) == (1, 0, {"files_gate_miss": [I2]})
    assert report.plan_vs_result(sec, {"images": entry}) == []
    failed = cli.result_of(p, A.ApplyOutcome(failed=[I2], failure="db_error:23505", skipped={"digest_mismatch": [I1]}))["data"]["images"]
    doc = report.Document(env="general", run_id="20261113T120000Z-00000001", tool="sync.promote", dry=False, sections=[sec],
                          result={"data": {"images": failed}})
    js = report.document_json(doc)["result"]["data"]["images"]
    assert js["failed"] == {"count": 1, "ids": [I2], "failure": "db_error:23505"}
    assert js["skipped_with_reason"]["digest_mismatch"]["ids"] == [I1]
    assert "FAILED batch of 1 images rolled back (db_error:23505)" in report.render_markdown(doc)
    with pytest.raises(report.ReportError):
        report.ResultEntry(skipped={"made_up_reason": [I1]})


def test_a_global_only_plan_has_an_empty_images_result():
    sec = report.Section(name="data", tool="sync.promote", entries=[report.Entry(name="concepts", kind="rows")])
    assert cli.result_of(type("P", (), {"section": sec})(), A.ApplyOutcome()) == {"data": {}}


def test_the_apply_module_cannot_say_truncate_drop_ddl_or_delete_from_images():
    src = Path(A.__file__).read_text(encoding="utf-8")
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    code = re.sub(r'""".*?"""', "", code, flags=re.S)  # the docstrings talk about what is not done
    for word in ("TRUNCATE", "DROP ", "ALTER ", "CREATE ", "GRANT", "REVOKE", "VACUUM"):
        assert word not in code, word
    assert not re.search(r'DELETE FROM\s+(\\?"?)images', code)
    assert not re.search(r"meme_text_ratings|alembic_version|batch_runs", code)


def test_apply_time_reason_codes_are_report_codes():
    for code in (A.DEFERRED, A.MISMATCH, "files_gate_miss"):
        assert code in report.REASON_CODES
