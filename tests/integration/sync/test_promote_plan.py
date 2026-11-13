"""P2-T4/T5 against the two-schema fixture: the dev schema plays dev, the prod schema plays prod. Digests are computed on both
sides with the real SQL and the plan categories are asserted. Only `ocrdb_test` (via `sync_pair`); the handshake is replaced by
a canned success because the fixture has no tunnel, roles or capabilities endpoint."""
import datetime as dt
import io
import json
import uuid
from pathlib import Path

import pytest

from sync import connect, report
from sync.config import SyncSettings
from sync.promote import cli, digest as dg, files_gate, plan as plan_mod, preconditions as pre_mod

VEC = "[" + ",".join(["0.5"] * 512) + "]"
VEC2 = "[" + ",".join(["0.25"] * 512) + "]"
NOW = dt.datetime(2026, 11, 13, 12, 0, 0, tzinfo=dt.timezone.utc)


def uid(n: int) -> str:
    return str(uuid.UUID(int=n))


class Side:
    """Seeding helper bound to one schema (autocommit, so every statement is visible to the digest sessions at once)."""

    def __init__(self, raw):
        self.raw = raw
        raw.autocommit = True

    def x(self, sql, args=None):
        with self.raw.cursor() as cur:
            cur.execute(sql, args)

    def image(self, n, *, filename=None, status="active", ocr="hello world", vec=VEC, tags=(("meme", "cat", "ocr"),),
              desc="a cat on a sofa", flagged=None, remarks=None, note=None, embedding=True, ocr_row=True, popularity=1):
        i = uid(n)
        fn = filename or f"img{n:04d}.jpg"
        self.x("insert into images (id, filename, content_hash, status, created_at, popularity) values (%s,%s,%s,%s,%s,%s)",
               (i, fn, f"hash{n:04d}", status, dt.datetime(2026, 1, 1) + dt.timedelta(minutes=n), popularity))
        if ocr_row:
            self.x("insert into ocr_texts (id, image_id, text, language) values (%s,%s,%s,'en')", (uid(10_000 + n), i, ocr))
        if embedding:
            self.x("insert into embeddings (id, image_id, embedding) values (%s,%s,%s::vector)", (uid(20_000 + n), i, vec))
        for k, (key, value, source) in enumerate(tags):
            self.x("insert into tags (id, image_id, key, value, source) values (%s,%s,%s,%s,%s)", (uid(30_000 + n * 10 + k), i, key, value, source))
        if desc is not None:
            self.x("insert into image_descriptions (id, image_id, prompt_key, model_used, text, created_at) "
                   "values (%s,%s,'p','m',%s,'2026-01-01')", (uid(40_000 + n), i, desc))
        if flagged is not None or remarks is not None:
            self.x("insert into image_extras (image_id, flagged, remarks) values (%s,%s,%s)", (i, flagged, remarks))
        if note is not None:
            self.x("insert into description_notes (image_id, text, updated_at) values (%s,%s,'2026-01-01')", (i, note))
        return i

    def scalar(self, sql, args=None):
        with self.raw.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchone()[0]


@pytest.fixture()
def world(sync_pair):
    sync_pair.reset()
    dev_raw, prod_raw = sync_pair.dev_conn(), sync_pair.prod_conn()
    dev_side, prod_side = Side(dev_raw), Side(prod_raw)
    # a second pair of connections for the plan: read-only dev, like the tool
    dev_c, prod_c = sync_pair.dev_conn(), sync_pair.prod_conn()
    dev_c.autocommit = prod_c.autocommit = True
    dev = connect.SyncConnection(dev_c, connect.DEV_RO, None, None, "general")
    prod = connect.SyncConnection(prod_c, connect.PROD_WRITER)
    try:
        yield type("World", (), {"dev_side": dev_side, "prod_side": prod_side, "dev": dev, "prod": prod, "pair": sync_pair})
    finally:
        for c in (dev_raw, prod_raw, dev_c, prod_c):
            c.close()


def both(world, n, **kw):
    world.dev_side.image(n, **kw)
    world.prod_side.image(n, **kw)


def settings_for(tmp_path) -> SyncSettings:
    return SyncSettings("general", {"state_dir": str(tmp_path / "state"), "dev": {"database": "ocrdb_test"},
                                    "prod": {"database": "ocrdb_test"}}, root=tmp_path)


def args_for(*argv):
    return cli.build_parser().parse_args(["--env", "general", *argv])


def plan_of(world, tmp_path, *argv, files=None):
    args = args_for(*argv)
    pre: list = []
    assert all(p["ok"] for p in cli.run_preconditions(settings_for(tmp_path), args, world.dev, world.prod))
    inp = cli.gather(settings_for(tmp_path), args, world.dev, world.prod, tmp_path / "run", NOW, pre)
    if files is not None:
        inp.files = files
    return plan_mod.build_plan(inp), inp, pre


def entry(p, name="images"):
    return next(e for e in p.section.entries if e.name == name)


# ---------------------------------------------------------------------------------------------------------------------------------

def test_equal_sides_are_unchanged_and_no_guard_trips(world, tmp_path):
    for n in range(1, 7):
        both(world, n)
    p, _, _ = plan_of(world, tmp_path)
    e = entry(p)
    assert (e.added.n, e.overwritten.n, e.unchanged.n, e.left_behind.n) == (0, 0, 6, 0)
    assert not p.tripped
    assert p.section.totals()["overwritten"]["count"] == 0


def test_new_image_added_and_left_behind_prod_only(world, tmp_path):
    for n in range(1, 6):
        both(world, n)
    world.dev_side.image(6)  # only on dev: added
    world.prod_side.image(7)  # only on prod: left behind
    p, _, _ = plan_of(world, tmp_path)
    e = entry(p)
    assert e.added.ids == [uid(6)] and e.left_behind.ids == [uid(7)] and e.unchanged.n == 5 and e.overwritten.n == 0
    rows = p.section.table_rows["embeddings"]
    assert rows["added_rows"] == 1 and rows["replaced_rows"] == 0 and rows["added_bytes"] > 0


@pytest.mark.parametrize("what,groups", [
    ("desc", ["desc"]), ("tag", ["tags"]), ("embedding", ["clip"]), ("ocr", ["ocr"]), ("remarks", ["core"]),
    ("note", ["note"]), ("popularity", ["core"]), ("filename", ["core"]),
    ("tag_removed", ["tags"]), ("desc_removed", ["desc"]), ("note_cleared", ["note"]), ("remarks_removed", ["core"]),
    ("tag_and_embedding", ["clip", "tags"]),
])
def test_changed_image_names_exactly_its_groups(world, tmp_path, what, groups):
    for n in range(1, 9):
        both(world, n, remarks="r" if what in ("remarks_removed",) else None, note="n" if what == "note_cleared" else None)
    d, i = world.dev_side, uid(3)
    if what == "desc":
        d.x("update image_descriptions set text = 'a dog' where image_id = %s", (i,))
    elif what == "tag":
        d.x("update tags set value = 'dog' where image_id = %s", (i,))
    elif what == "embedding":
        d.x("update embeddings set embedding = %s::vector where image_id = %s", (VEC2, i))
    elif what == "ocr":
        d.x("update ocr_texts set text = 'other text' where image_id = %s", (i,))
    elif what == "remarks":
        d.x("insert into image_extras (image_id, flagged, remarks) values (%s, false, 'new remark')", (i,))
    elif what == "note":
        d.x("insert into description_notes (image_id, text, updated_at) values (%s, 'curated', '2026-01-01')", (i,))
    elif what == "popularity":
        d.x("update images set popularity = 5 where id = %s", (i,))
    elif what == "filename":
        d.x("update images set filename = 'renamed.jpg' where id = %s", (i,))
    elif what == "tag_removed":
        d.x("delete from tags where image_id = %s", (i,))
    elif what == "desc_removed":
        d.x("delete from image_descriptions where image_id = %s", (i,))
    elif what == "note_cleared":
        d.x("delete from description_notes where image_id = %s", (i,))
    elif what == "remarks_removed":
        d.x("delete from image_extras where image_id = %s", (i,))
    elif what == "tag_and_embedding":
        d.x("update tags set value = 'dog' where image_id = %s", (i,))
        d.x("update embeddings set embedding = %s::vector where image_id = %s", (VEC2, i))
    p, _, _ = plan_of(world, tmp_path)
    e = entry(p)
    assert e.overwritten.ids == [i] and e.overwritten.groups == {i: groups}, e.overwritten.groups
    assert e.unchanged.n == 7 and e.added.n == 0
    assert set(e.overwritten.group_counts) == set(groups)


def test_a_rebuild_with_the_same_content_is_not_a_change(world, tmp_path):
    """digest_exclude: surrogate ids and created_at of ocr_texts, embeddings and tags; ingestion_batch_id of images."""
    for n in range(1, 7):
        both(world, n)
    world.dev_side.x("update tags set id = %s, created_at = now() + interval '1 day' where image_id = %s", (uid(99_999), uid(2)))
    world.dev_side.x("update embeddings set id = %s, created_at = now() + interval '2 day' where image_id = %s", (uid(99_998), uid(2)))
    world.dev_side.x("update ocr_texts set id = %s, created_at = now() + interval '3 day' where image_id = %s", (uid(99_997), uid(2)))
    p, _, _ = plan_of(world, tmp_path)
    assert entry(p).overwritten.n == 0 and entry(p).unchanged.n == 6


def test_duplicate_rows_are_counted_not_cancelled(world, tmp_path):
    for n in range(1, 7):
        both(world, n)
    for side in (world.dev_side,):  # two identical tag rows on dev, one on prod: identical rows must not cancel
        side.x("insert into tags (id, image_id, key, value, source) values (%s,%s,'meme','cat','ocr')", (uid(77_777), uid(4)))
    p, _, _ = plan_of(world, tmp_path)
    assert entry(p).overwritten.groups == {uid(4): ["tags"]}


def test_flagged_image_is_not_promotable_and_stays_left_behind(world, tmp_path):
    for n in range(1, 7):
        both(world, n)
    world.dev_side.x("insert into image_extras (image_id, flagged) values (%s, true)", (uid(2),))
    world.dev_side.x("update images set status = 'rejected' where id = %s", (uid(3),))
    p, _, _ = plan_of(world, tmp_path)
    e = entry(p)
    assert e.left_behind.ids == [uid(2), uid(3)] and e.unchanged.n == 4 and e.overwritten.n == 0
    assert not p.tripped  # left behind is informational


def test_new_image_without_required_table_is_skipped_but_changed_is_not_held_back(world, tmp_path):
    for n in range(1, 8):
        both(world, n)
    world.dev_side.image(8, embedding=False)  # new, no embeddings
    world.dev_side.image(9, ocr_row=False)  # new, no ocr_texts
    world.dev_side.x("delete from ocr_texts where image_id = %s", (uid(2),))  # existing, loses a required table
    p, _, _ = plan_of(world, tmp_path)
    e = entry(p)
    assert e.skipped["missing_required_table"].ids == [uid(8), uid(9)] and e.added.n == 0
    assert e.overwritten.groups == {uid(2): ["ocr"]}


def test_limit_images_caps_and_skips_the_global_sets(world, tmp_path):
    for n in range(1, 5):
        both(world, n)
    for n in range(5, 9):
        world.dev_side.image(n)
    world.dev_side.x("insert into concepts (id, name) values (1, 'c')")
    p, _, _ = plan_of(world, tmp_path, "--limit-images", "2")
    e = entry(p)
    assert e.added.ids == [uid(5), uid(6)] and e.skipped["capped_by_limit"].ids == [uid(7), uid(8)]
    assert [x.name for x in p.section.entries] == ["images"]
    p2, _, _ = plan_of(world, tmp_path)
    assert entry(p2, "concepts").added.ids == [1]


def test_only_restricts_tables_and_adds_parents(world, tmp_path):
    for n in range(1, 7):
        both(world, n)
    world.dev_side.x("update tags set value = 'dog' where image_id = %s", (uid(2),))
    world.dev_side.x("update embeddings set embedding = %s::vector where image_id = %s", (VEC2, uid(3)))
    p, inp, _ = plan_of(world, tmp_path, "--only", "tags")
    assert inp.tables == ["images", "tags"]
    assert entry(p).overwritten.groups == {uid(2): ["tags"]}  # the embedding change is not in scope
    assert set(p.section.table_rows) == {"images", "tags"}
    assert [e.name for e in p.section.entries] == ["images"]  # no global set selected
    assert p.section.flags == {"only:tags": True}


def test_files_report_gates_new_and_changed_images(world, tmp_path):
    for n in range(1, 7):
        both(world, n)
    world.dev_side.image(7)  # new, file on prod
    world.dev_side.image(8)  # new, file not on prod
    world.dev_side.image(9)  # new, left out by P3 (missing file)
    world.dev_side.x("update tags set value = 'dog' where image_id = %s", (uid(2),))  # changed, file on prod
    world.dev_side.x("update tags set value = 'dog' where image_id = %s", (uid(3),))  # changed, but not in the report
    files = files_gate.FilesReport("20261113T110000Z-aaaaaaaa", "general", "2026-11-13T11:30:00Z", False, "x" * 64,
                                   images={uid(n): (f"img{n:04d}.jpg", f"hash{n:04d}") for n in (1, 2, 4, 5, 6, 7)},
                                   left_out={uid(9): "missing_file"})
    p, _, _ = plan_of(world, tmp_path, files=files)
    e = entry(p)
    assert e.added.ids == [uid(7)]
    assert list(e.overwritten.groups) == [uid(2)]
    assert e.skipped["files_gate_miss"].ids == [uid(3), uid(8)]
    assert e.skipped["missing_file"].ids == [uid(9)]
    # the combined dry run of sync.run: the data section moves ids whose file the files section would add to waiting_for_files
    report.mark_waiting_for_files(e, [uid(7)])
    t = p.section.totals()
    assert t["waiting_for_files"]["count"] == 1 and t["expected_added"]["count"] == 1 and t["added"]["count"] == 0


def test_plan_hash_is_deterministic_and_includes_changed_groups(world, tmp_path):
    for n in range(1, 7):
        both(world, n)
    world.dev_side.x("update tags set value = 'dog' where image_id = %s", (uid(2),))
    h1 = plan_of(world, tmp_path)[0].section.plan_hash()
    h2 = plan_of(world, tmp_path)[0].section.plan_hash()
    assert h1 == h2
    world.dev_side.x("update embeddings set embedding = %s::vector where image_id = %s", (VEC2, uid(2)))  # same image, one more group
    assert plan_of(world, tmp_path)[0].section.plan_hash() != h1


def test_digests_do_not_depend_on_the_session_settings(world, tmp_path):
    both(world, 1, tags=(("meme", "cat", "ocr"), ("meme", "dog", "ocr")))
    base = dg.fetch_image_digests(world.dev, dg.image_scoped_tables(), "promotable")
    odd = world.pair.dev_conn()
    odd.autocommit = True
    try:
        with odd.cursor() as cur:
            cur.execute("SET TimeZone = 'Asia/Tokyo'")
            cur.execute("SET DateStyle = 'German, DMY'")
            cur.execute("SET extra_float_digits = -3")
            cur.execute("SET bytea_output = 'escape'")
        conn = connect.SyncConnection(odd, connect.DEV_RO)
        assert dg.pin_session(conn) == []
        assert dg.fetch_image_digests(conn, dg.image_scoped_tables(), "promotable") == base
    finally:
        odd.close()


def test_regression_guards_trip_on_a_half_rebuilt_dev(world, tmp_path):
    for n in range(1, 41):
        both(world, n, tags=(("meme", "cat", "ocr"),) * 1)
    # dev lost every tag of 12 of 40 images (a table emptied for 30 % of the images)
    for n in range(1, 13):
        world.dev_side.x("delete from tags where image_id = %s", (uid(n),))
    p, _, _ = plan_of(world, tmp_path)
    tripped = {g.id for g in p.tripped}
    assert "coverage_regression:tags" in tripped
    assert "row_regression:tags" not in tripped  # 12 rows are below max(100, 2 %)
    assert "mass_change" in tripped  # 12 of 40 images changed: above 25 %
    assert plan_mod.effective_blocking(p) == sorted(tripped, key=[g.id for g in p.guards].index)
    assert plan_mod.effective_blocking(p, allow_mass_change=12, allow_shrink=["tags"]) == []
    assert plan_mod.effective_blocking(p, allow_mass_change=11, allow_shrink=["tags"]) == ["mass_change"]


def test_dev_emptied_and_zero_image_guards(world, tmp_path):
    for n in range(1, 7):
        world.prod_side.image(n)
    world.dev_side.image(1)
    world.dev_side.image(2)
    p, _, _ = plan_of(world, tmp_path)
    assert {g.id for g in p.tripped} == {"dev_emptied"}
    world.dev_side.x("delete from images")
    p, _, _ = plan_of(world, tmp_path)
    assert {"zero_images", "dev_emptied"} <= {g.id for g in p.tripped}
    assert plan_mod.effective_blocking(p, 0, [t for t in dg.image_scoped_tables()]) == ["zero_images", "dev_emptied"]


def test_global_sets_upsert_only(world, tmp_path):
    for n in range(1, 4):
        both(world, n)
    d, pr = world.dev_side, world.prod_side
    d.x("insert into concepts (id, name) values (1, 'same'), (2, 'changed-dev'), (3, 'dev-only')")
    pr.x("insert into concepts (id, name) values (1, 'same'), (2, 'changed-prod'), (4, 'prod-only')")
    run = uuid.uuid4()
    d.x("insert into batch_runs (run_id, kind, trigger, status) values (%s, 'k', 'manual', 'completed')", (str(run),))
    ab = uid(500)
    rv = uid(501)
    only_dev = uid(502)
    for side, closed in ((d, True), (pr, False)):
        side.x("insert into popularity_absorptions (id, kind, source, loser_image_id, winner_image_id, weight, dedupe_key, created_at) "
               "values (%s,'absorb','cluster_review',%s,%s,1,'k1','2026-01-01T00:00:00Z')", (ab, uid(2), uid(1)))
    d.x("insert into popularity_absorptions (id, kind, source, loser_image_id, winner_image_id, weight, reverses_id, reason, created_at, batch_run_id) "
        "values (%s,'reverse','cluster_review',%s,%s,1,%s,'unflag','2026-01-02T00:00:00Z',%s)", (rv, uid(2), uid(1), ab, str(run)))
    d.x("update popularity_absorptions set reversed_by_id = %s where id = %s", (rv, ab))
    d.x("insert into popularity_absorptions (id, kind, source, loser_image_id, winner_image_id, weight, dedupe_key, created_at, batch_run_id) "
        "values (%s,'absorb','cluster_review',%s,%s,1,'k2','2026-01-03T00:00:00Z',%s)", (only_dev, uid(3), uid(1), str(run)))
    pr.x("insert into popularity_absorptions (id, kind, source, loser_image_id, winner_image_id, weight, dedupe_key, created_at) "
         "values (%s,'absorb','cluster_review',%s,%s,1,'k3','2026-01-04T00:00:00Z')", (uid(503), uid(3), uid(1)))
    p, _, _ = plan_of(world, tmp_path)
    c = entry(p, "concepts")
    assert (c.added.ids, c.overwritten.ids, c.unchanged.n, c.left_behind.ids) == ([3], [2], 1, [4])
    a = entry(p, "popularity_absorptions")
    assert a.added.ids == sorted([rv, only_dev])  # the reverse row and the dev-only absorb (batch_run_id is blanked, not compared)
    assert a.overwritten.ids == [ab]  # closed on dev, open on prod: insert-or-close
    assert a.left_behind.ids == [uid(503)]
    assert a.unchanged.n == 0


def test_a_blanked_column_does_not_make_an_absorption_differ(world, tmp_path):
    both(world, 1)
    run = str(uuid.uuid4())
    world.dev_side.x("insert into batch_runs (run_id, kind, trigger, status) values (%s, 'k', 'manual', 'completed')", (run,))
    for side, br in ((world.dev_side, run), (world.prod_side, None)):
        side.x("insert into popularity_absorptions (id, kind, source, loser_image_id, winner_image_id, weight, dedupe_key, batch_run_id, created_at) "
               "values (%s,'absorb','cluster_review',%s,%s,1,'k',%s,'2026-01-01T00:00:00Z')", (uid(700), uid(1), uid(1), br))
    p, _, _ = plan_of(world, tmp_path)
    a = entry(p, "popularity_absorptions")
    assert a.unchanged.n == 1 and a.added.n == 0 and a.overwritten.n == 0


# --- phase 0 ---------------------------------------------------------------------------------------------------------------------

def test_preconditions_pass_on_two_equal_schemas_and_catch_drift(world, tmp_path):
    s = settings_for(tmp_path)
    pre = cli.run_preconditions(s, args_for(), world.dev, world.prod)
    assert [p["id"] for p in pre] == ["required_tables", "session_settings", "schema_parity", "column_parity", "model_parity",
                                      "model_pair_change", "running_batches", "prod_lock"]
    assert all(p["ok"] for p in pre), pre
    pre_mod.release_prod_lock(world.prod)

    world.prod_side.x("alter table image_extras add column sneaky text")
    try:
        r = pre_mod.column_parity(world.dev, world.prod)
        assert not r["ok"] and "image_extras" in r["detail"]
    finally:
        world.prod_side.x("alter table image_extras drop column sneaky")


def test_running_batches_block_unless_lifted_and_ingestion_is_exempt(world):
    d = world.dev_side
    d.x("insert into batch_runs (run_id, kind, trigger, status) values (%s, 'ingestion', 'manual', 'started')", (str(uuid.uuid4()),))
    assert pre_mod.running_batches(world.dev)["ok"]
    d.x("insert into batch_runs (run_id, kind, trigger, status) values (%s, 'build_tags_from_ocr', 'manual', 'started')", (str(uuid.uuid4()),))
    r = pre_mod.running_batches(world.dev)
    assert not r["ok"] and r["detail"] == "running=1"
    r = pre_mod.running_batches(world.dev, ignore=True)
    assert r["ok"] and r["detail"] == "ignored=1"


def seed_profiles(world, count=2, dev_model="m1", prod_model="m1", dev_tax=3, prod_tax=3, plain=0):
    """Images 1..count with a profile and a situation embedding; `plain` more images without any, so a model change stays
    under the mass-change limit."""
    for n in range(1, count + plain + 1):
        both(world, n)
    for side, model, tax in ((world.dev_side, dev_model, dev_tax), (world.prod_side, prod_model, prod_tax)):
        for n in range(1, count + 1):
            side.x("insert into meme_profiles (image_id, taxonomy_version, extractor_model, extractor_prompt_version, inputs_fingerprint) "
                   "values (%s, %s, 'x', 'v', 'f')", (uid(n), tax))
            side.x("insert into meme_profile_situation_embeddings (image_id, model_name, model_digest) values (%s, %s, 'd')", (uid(n), model))


def test_model_parity_needs_one_consistent_dev_set_and_a_difference_from_prod_is_only_reported(world):
    seed_profiles(world)
    assert pre_mod.model_parity(world.dev, world.prod)["ok"]
    assert pre_mod.model_pair_change(world.dev, world.prod) == {"id": "model_pair_change", "ok": True,
                                                                "detail": "profile_embedding=same,taxonomy=same"}
    world.prod_side.x("update meme_profile_situation_embeddings set model_name = 'm0'")
    assert pre_mod.model_parity(world.dev, world.prod)["ok"]  # no longer a precondition failure
    assert pre_mod.model_pair_change(world.dev, world.prod)["detail"] == "profile_embedding=changed,taxonomy=same"
    world.prod_side.x("update meme_profiles set taxonomy_version = 2")
    assert pre_mod.model_pair_change(world.dev, world.prod)["detail"] == "profile_embedding=changed,taxonomy=changed"
    world.prod_side.x("update meme_profile_situation_embeddings set model_name = 'm1'")
    world.dev_side.x("update meme_profile_situation_embeddings set model_name = 'm2' where image_id = %s", (uid(2),))
    assert "dev_model_pairs=2" in pre_mod.model_parity(world.dev, world.prod)["detail"]
    world.dev_side.x("update meme_profile_situation_embeddings set model_name = 'm1'")
    world.dev_side.x("update meme_profiles set taxonomy_version = 4 where image_id = %s", (uid(2),))
    assert "dev_taxonomy_versions=2" in pre_mod.model_parity(world.dev, world.prod)["detail"]


def test_a_rebuild_timestamp_of_a_derived_table_is_not_a_change(world, tmp_path):
    """digest_exclude of the 2026-11-13 audit: created_at of ocr_lemmas and of the embedding tables, extracted_at and updated_at
    of meme_profiles."""
    seed_profiles(world, 3)
    d = world.dev_side
    d.x("insert into ocr_lemmas (image_id, lemma) values (%s, 'cat')", (uid(1),))
    world.prod_side.x("insert into ocr_lemmas (image_id, lemma) values (%s, 'cat')", (uid(1),))
    d.x("update ocr_lemmas set created_at = now() + interval '1 day'")
    d.x("update meme_profiles set extracted_at = now() + interval '1 day', updated_at = now() + interval '2 day'")
    d.x("update meme_profile_situation_embeddings set created_at = now() + interval '3 day'")
    p, _, _ = plan_of(world, tmp_path)
    assert entry(p).overwritten.n == 0 and entry(p).unchanged.n == 3
    d.x("update meme_profiles set human_verified = true where image_id = %s", (uid(1),))  # content: still a change
    p, _, _ = plan_of(world, tmp_path)
    assert entry(p).overwritten.ids == [uid(1)]


def test_prod_lock_is_exclusive(world, sync_pair):
    first = pre_mod.try_prod_lock(world.prod)
    assert first["ok"]
    other_raw = sync_pair.prod_conn()
    other_raw.autocommit = True
    try:
        other = connect.SyncConnection(other_raw, connect.PROD_WRITER)
        assert not pre_mod.try_prod_lock(other)["ok"]
    finally:
        other_raw.close()
    pre_mod.release_prod_lock(world.prod)
    again = sync_pair.prod_conn()
    again.autocommit = True
    try:
        assert pre_mod.try_prod_lock(connect.SyncConnection(again, connect.PROD_WRITER))["ok"]
    finally:
        again.close()


# --- the command end to end (handshake replaced by a canned success) -------------------------------------------------------------

@pytest.fixture()
def command(world, tmp_path, monkeypatch):
    monkeypatch.setattr(connect, "handshake", lambda *a, **k: [{"step": "H1", "ok": True, "detail": "ok"}])
    settings = settings_for(tmp_path)

    class Opener:
        def __init__(self, env, tool, mode="normal", settings=None):
            self.mode = mode

        def __enter__(self):
            Opener.mode_seen = self.mode
            return {"dev": world.dev, "prod": world.prod, "prod_reader_conn": None}

        def __exit__(self, *exc):
            return None

    def run(*argv, answer=None, run_id):
        out, err = io.StringIO(), io.StringIO()
        code = cli.run_promote(args_for(*argv, "--run-id", run_id), settings=settings, session_opener=Opener, now=NOW, out=out, err=err,
                               ask=(lambda prompt: answer) if answer is not None else (lambda prompt: "general ocrdb_test"))
        return code, out.getvalue(), err.getvalue(), tmp_path / "state" / "runs" / run_id

    return run


def test_dry_run_writes_the_report_and_exits_zero_even_when_a_guard_would_trip(world, command):
    for n in range(1, 41):
        both(world, n)
    for n in range(1, 13):
        world.dev_side.x("delete from tags where image_id = %s", (uid(n),))
    before = world.prod_side.scalar("select count(*) from tags")
    code, out, err, folder = command(run_id="20261113T120000Z-00000001")
    assert code == 0, (out, err)
    assert (folder / "dry").exists() and (folder / "report.json").exists() and (folder / "report.md").exists()
    doc = json.loads((folder / "report.json").read_text())
    assert doc["dry"] is True and doc["plan_hash"] and "--apply --plan-hash " + doc["plan_hash"] in doc["apply_command"]
    sec = doc["sections"][0]
    assert sec["name"] == "data" and sec["entries"][0]["overwritten"]["count"] == 12
    assert any(g["id"] == "mass_change" and g["tripped"] for g in sec["guards"])
    assert "an apply would refuse (exit 3)" in out and "guard mass_change would trip" in out
    assert world.prod_side.scalar("select count(*) from tags") == before
    assert "tags" in sec["table_rows"]


def write_files_report(tmp_path, count, name="20261113T115000Z-0000000a", finished="2026-11-13T11:50:00Z"):
    from sync.files.manifest import manifest_sha256

    rows = [(uid(n), f"img{n:04d}.jpg", f"hash{n:04d}") for n in range(1, count + 1)]
    folder = tmp_path / "state" / "runs" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "files-promoted.json").write_text(json.dumps({
        "run_id": folder.name, "env": "general", "finished_at": finished, "complete": True,
        "images": [{"id": i, "filename": f, "size": 1, "content_hash": h} for i, f, h in rows], "left_out": [],
        "manifest_sha256": manifest_sha256(rows)}))
    return folder


def test_apply_refusals_happen_before_anything_is_written(world, command, tmp_path):
    for n in range(1, 41):
        both(world, n)
    for n in range(1, 13):
        world.dev_side.x("delete from tags where image_id = %s", (uid(n),))
    code, out, err, folder = command(run_id="20261113T120000Z-00000002")
    plan_hash = json.loads((folder / "report.json").read_text())["plan_hash"]
    lifts = ["--allow-mass-change", "12", "--allow-shrink", "tags"]
    # no files report: a precondition (exit 2), before any guard
    code, out, err, _ = command("--apply", "--yes", *lifts, run_id="20261113T120000Z-00000004")
    assert code == 2 and "files_report" in err
    write_files_report(tmp_path, 40)
    # a tripped guard without its lift: exit 3 (the hash of the dry run is still the hash of the plan: lifts are not hashed)
    code, out, err, _ = command("--apply", "--yes", run_id="20261113T120000Z-00000003")
    assert code == 3 and "mass_change" in err
    # the dry run's hash was made without a files report; with one the skipped sets differ only if images are held back: none here
    code, out, err, _ = command("--apply", "--yes", "--plan-hash", plan_hash, *lifts, run_id="20261113T120000Z-00000010")
    assert code == 1 and "not built yet" in err  # past every refusal; T6 does the copy
    # a wrong N lifts nothing
    code, out, err, _ = command("--apply", "--yes", "--allow-mass-change", "11", "--allow-shrink", "tags", run_id="20261113T120000Z-00000011")
    assert code == 3 and "mass_change" in err
    assert world.prod_side.scalar("select count(*) from tags") == 40


def test_apply_with_a_valid_files_report_and_matching_hash_stops_at_the_unbuilt_apply(world, command, tmp_path):
    for n in range(1, 7):
        both(world, n)
    world.dev_side.x("update tags set value = 'dog' where image_id = %s", (uid(2),))
    write_files_report(tmp_path, 6)
    code, out, err, run = command(run_id="20261113T120000Z-00000005")
    assert code == 0, (out, err)
    doc = json.loads((run / "report.json").read_text())
    assert next(p for p in doc["preconditions"] if p["id"] == "files_report")["detail"] == "valid"
    # wrong hash -> 3 (and nothing else is attempted)
    code, out, err, _ = command("--apply", "--yes", "--plan-hash", "0" * 64, run_id="20261113T120000Z-00000006")
    assert code == 3 and "plan changed" in err
    # right hash -> the copy is not built (T6): exit 1, prod untouched
    before = world.prod_side.scalar("select value from tags where image_id = %s", (uid(2),))
    code, out, err, _ = command("--apply", "--plan-hash", doc["plan_hash"], run_id="20261113T120000Z-00000007")
    assert code == 1 and "not built yet" in err
    assert world.prod_side.scalar("select value from tags where image_id = %s", (uid(2),)) == before
    # a typed confirmation that does not match -> 3 before the stop
    code, out, err, _ = command("--apply", "--plan-hash", doc["plan_hash"], answer="general nope", run_id="20261113T120000Z-00000008")
    assert code == 3 and "typed confirmation" in err
    # the apply report is a real (non-dry) folder with no marker
    assert not (tmp_path / "state" / "runs" / "20261113T120000Z-00000007" / "dry").exists()


def test_same_model_pair_trips_no_guard_and_a_changed_pair_needs_the_flag(world, command, tmp_path):
    seed_profiles(world, 4, plain=16)
    code, out, err, folder = command(run_id="20261113T120000Z-00000021")
    doc = json.loads((folder / "report.json").read_text())
    g = next(g for g in doc["sections"][0]["guards"] if g["id"] == "model_change")
    assert code == 0 and not g["tripped"]
    assert next(p for p in doc["preconditions"] if p["id"] == "model_pair_change") == {
        "id": "model_pair_change", "ok": True, "detail": "profile_embedding=same,taxonomy=same"}

    world.dev_side.x("update meme_profile_situation_embeddings set model_name = 'm2'")  # dev upgraded its model
    write_files_report(tmp_path, 20)
    code, out, err, folder = command(run_id="20261113T120000Z-00000022")
    assert code == 0, (out, err)  # a dry run exits zero and says what an apply would do
    doc = json.loads((folder / "report.json").read_text())
    g = next(g for g in doc["sections"][0]["guards"] if g["id"] == "model_change")
    assert g["tripped"] and g["lifted_by"] == "--allow-model-change"
    assert next(p for p in doc["preconditions"] if p["id"] == "model_pair_change")["detail"] == "profile_embedding=changed,taxonomy=same"
    assert all(p["ok"] for p in doc["preconditions"])
    assert doc["sections"][0]["entries"][0]["overwritten"]["count"] == 4  # the digest, not the guard, marks the rows
    assert "model_change" in out and "an apply would refuse (exit 3)" in out
    plan_hash = doc["plan_hash"]
    before = world.prod_side.scalar("select count(*) from meme_profile_situation_embeddings where model_name = 'm1'")

    code, out, err, _ = command("--apply", "--yes", "--plan-hash", plan_hash, run_id="20261113T120000Z-00000023")
    assert code == 3 and "model_change" in err
    # the flag is not part of the hash: the same hash passes the guard and stops only at the unbuilt copy
    code, out, err, _ = command("--apply", "--yes", "--plan-hash", plan_hash, "--allow-model-change", run_id="20261113T120000Z-00000024")
    assert code == 1 and "not built yet" in err
    assert world.prod_side.scalar("select count(*) from meme_profile_situation_embeddings where model_name = 'm1'") == before


def test_the_apply_command_of_a_dry_run_carries_the_model_lift(world, command):
    seed_profiles(world, 2, dev_model="m2", plain=8)
    code, out, err, folder = command("--allow-model-change", run_id="20261113T120000Z-00000025")
    assert code == 0, (out, err)
    assert "--allow-model-change" in json.loads((folder / "report.json").read_text())["apply_command"]
    assert "an apply would refuse" not in out


def test_mixed_models_among_the_promoted_rows_are_a_precondition_failure(world, command):
    seed_profiles(world, 3)
    world.dev_side.x("update meme_profile_situation_embeddings set model_name = 'm2' where image_id = %s", (uid(3),))
    code, out, err, folder = command(run_id="20261113T120000Z-00000026")
    assert code == 2 and "model_parity" in err
    doc = json.loads((folder / "report.json").read_text())
    bad = [p for p in doc["preconditions"] if not p["ok"]]
    assert [p["id"] for p in bad] == ["model_parity"] and "dev_model_pairs=2" in bad[0]["detail"]
    code, out, err, _ = command("--allow-model-change", run_id="20261113T120000Z-00000027")
    assert code == 2  # no flag lifts it


def test_the_dry_run_holds_the_prod_lock_and_releases_it(world, command, sync_pair):
    both(world, 1)
    code, *_ = command(run_id="20261113T120000Z-00000009")
    assert code == 0
    other = sync_pair.prod_conn()
    other.autocommit = True
    try:
        assert pre_mod.try_prod_lock(connect.SyncConnection(other, connect.PROD_WRITER))["ok"]
    finally:
        other.close()
