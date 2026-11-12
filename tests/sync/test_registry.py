"""The sync registry classifies every table of Storage/models.py; a new or misplaced table fails here (project P1)."""
from sqlalchemy import Integer

from Storage.models import Base
from sync import registry
from sync.model import PROD_WRITERS, Klass, Mode, ScopeKind
from sync.render import DOC, render

TABLES = Base.metadata.tables
REG = registry.REGISTRY

# The class boundaries are pinned here on purpose, so moving a table across them is a visible, reviewed change.
PULLED = {
    "meme_saves", "meme_impressions", "search_history", "search_history_tags", "search_not_found", "meme_text_ratings",
}
EXPECTED_X = {
    "batch_runs", "process_events", "image_metrics", "image_processing_status", "processing_errors", "ocr_text_embeddings",
    "image_classifications", "tmp_duplicates", "tmp_clusters", "duplicate_decisions", "comics_candidates",
    "comics_candidate_images", "image_cuts", "trend_sources", "trends_run_results",
}
EXPECTED_D = set(TABLES) - PULLED - EXPECTED_X
# D tables outside the image scope that are append-only or a single row: upsert is enough, nothing to prune.
NO_PRUNE = {"popularity_absorptions", "statistics_snapshots"}
# D tables of an image scope that are not replaced whole per image: the core rows themselves.
NOT_REPLACED_PER_IMAGE = {"images", "image_extras"}


def _fks(table_name):
    """(column name, target table name) for every foreign key of a table."""
    return [(fk.parent.name, fk.column.table.name) for fk in TABLES[table_name].foreign_keys]


def _descends_from_images(name, seen=None):
    """True for `images` and for tables reachable from it through foreign keys (the tables that hang off an image)."""
    seen = seen or set()
    if name == "images":
        return True
    if name in seen:
        return False
    seen.add(name)
    return any(_descends_from_images(target, seen) for _, target in _fks(name) if target != name)


def _image_bounded(name):
    """True when the rows of a shipped table are limited by the promotable image set along its scope chain."""
    scope = REG[name].scope
    if scope.kind in (ScopeKind.IMAGES, ScopeKind.IMAGE_FK):
        return True
    if scope.kind is ScopeKind.VIA:
        return _image_bounded(scope.parent)
    return False


def test_every_table_has_exactly_one_entry_and_no_entry_is_stale():
    missing = sorted(set(TABLES) - set(REG))
    unknown = sorted(set(REG) - set(TABLES))
    assert not missing, f"tables with no sync entry (add them to sync/registry.py): {missing}"
    assert not unknown, f"registry entries for tables that do not exist: {unknown}"


def test_class_mode_and_writer_agree():
    for entry in REG.values():
        assert entry.prod_writer in PROD_WRITERS, entry.name
        if entry.klass is Klass.D:
            assert entry.mode in (Mode.UPSERT_BY_KEY, Mode.REPLACE_PER_IMAGE), entry.name
            assert entry.prod_writer == "sync_writer", entry.name
        elif entry.klass is Klass.P:
            assert entry.mode is Mode.MIRROR_REPLACE, entry.name
            assert entry.prod_writer == "backend_app", entry.name
        else:
            assert entry.mode is Mode.SKIP and entry.scope.kind is ScopeKind.NEVER, entry.name
            assert entry.prod_writer == "none" and entry.level is None, entry.name


def test_key_is_the_primary_key():
    for entry in REG.values():
        if entry.klass is Klass.X:
            assert entry.key == (), entry.name
        else:
            assert entry.key == tuple(c.name for c in TABLES[entry.name].primary_key.columns), entry.name


def test_nullify_columns_are_foreign_keys():
    for entry in REG.values():
        fk_columns = {column for column, _ in _fks(entry.name)}
        assert set(entry.nullify) <= fk_columns, f"{entry.name}: nullify names a column that is not a foreign key"
        for column in entry.nullify:
            col = TABLES[entry.name].c[column]
            assert col.nullable and not col.primary_key, f"{entry.name}.{column} is blanked but cannot be NULL"
            assert column != entry.scope.column, f"{entry.name}.{column} is both blanked and the scope column"


def test_class_d_foreign_keys_stay_inside_what_is_shipped():
    """A shipped table may only reference rows that are shipped; otherwise the column is blanked (nullify).

    A target row can be missing on prod when the target is not shipped at all (class X) or when it is limited to the promotable
    images while the referencing table is not (a global set cannot know which image rows were left out).
    """
    for entry in (e for e in REG.values() if e.klass is Klass.D):
        for column, target in _fks(entry.name):
            if target == entry.name:
                assert column in registry.SELF_REFERENCING.get(entry.name, ()), f"{entry.name}.{column} self-reference undeclared"
                continue
            target_entry = REG[target]
            may_be_absent = target_entry.klass is not Klass.D or (not _image_bounded(entry.name) and _image_bounded(target))
            if may_be_absent:
                assert column in entry.nullify, f"{entry.name}.{column} -> {target} may be absent on prod; add it to nullify"


def test_class_p_cascade_from_images_is_declared_as_pull_before_promote():
    """Retiring an image on prod cascades into a P table with ON DELETE CASCADE and would destroy the only copy of that data."""
    for entry in REG.values():
        cascades = any(
            fk.column.table.name == "images" and (fk.ondelete or "").upper() == "CASCADE"
            for fk in TABLES[entry.name].foreign_keys
        )
        if entry.klass is Klass.P:
            assert entry.pull_before_promote is cascades, f"{entry.name}: pull_before_promote must be {cascades}"
        else:
            assert not entry.pull_before_promote, f"{entry.name}: only class P tables can need a pull before a promote"


def test_class_p_foreign_keys_point_only_to_images_or_class_p():
    for entry in (e for e in REG.values() if e.klass is Klass.P):
        for column, target in _fks(entry.name):
            assert target == "images" or REG[target].klass is Klass.P, f"{entry.name}.{column} -> {target}"


def test_level_is_a_topological_order_within_a_class():
    for entry in (e for e in REG.values() if e.klass is not Klass.X):
        for column, target in _fks(entry.name):
            parent = REG[target]
            if target == entry.name or parent.klass is not entry.klass:
                continue
            assert entry.level > parent.level, f"{entry.name}.{column} loads before its parent {target}"


def test_scope_is_reachable_from_the_promotable_images():
    for entry in (e for e in REG.values() if e.klass is not Klass.X):
        scope = entry.scope
        fks = dict(_fks(entry.name))
        if scope.kind is ScopeKind.IMAGES:
            assert entry.name == "images"
        elif scope.kind is ScopeKind.IMAGE_FK:
            assert fks.get(scope.column) == "images", f"{entry.name}: {scope.column} is not an FK to images"
        elif scope.kind is ScopeKind.VIA:
            assert fks.get(scope.column) == scope.parent, f"{entry.name}: {scope.column} is not an FK to {scope.parent}"
            assert REG[scope.parent].scope.kind in (ScopeKind.IMAGE_FK, ScopeKind.VIA, ScopeKind.GLOBAL_SET), entry.name
            assert REG[scope.parent].level < entry.level, entry.name
        elif scope.kind is ScopeKind.GLOBAL_SET:
            assert entry.klass is Klass.D, entry.name
        else:
            assert scope.kind is ScopeKind.PROD_ALL and entry.klass is Klass.P, entry.name


def test_a_table_with_a_direct_image_foreign_key_is_scoped_by_it_unless_blanked():
    for entry in (e for e in REG.values() if e.klass is Klass.D and e.name != "images"):
        for column, target in _fks(entry.name):
            if target == "images" and column not in entry.nullify:
                assert entry.scope.kind is ScopeKind.IMAGE_FK and entry.scope.column == column, entry.name


def test_image_scoped_children_are_replaced_whole_and_global_sets_say_whether_they_prune():
    for entry in (e for e in REG.values() if e.klass is Klass.D):
        if _image_bounded(entry.name) and entry.name not in NOT_REPLACED_PER_IMAGE:
            assert entry.mode is Mode.REPLACE_PER_IMAGE, f"{entry.name}: upsert never deletes, so stale rows would linger on prod"
        if not _image_bounded(entry.name):
            assert entry.prune is (entry.name not in NO_PRUNE), f"{entry.name}: prune must be {entry.name not in NO_PRUNE}"
        else:
            assert not entry.prune, entry.name


def test_digest_groups_cover_every_replaced_table_and_every_stale_flag():
    for entry in REG.values():
        if entry.mode is Mode.REPLACE_PER_IMAGE:
            assert entry.digest_group, f"{entry.name}: replaced per image but in no digest group"
        if entry.digest_group:
            assert entry.klass is Klass.D, entry.name
        if entry.klass is Klass.D and "stale" in TABLES[entry.name].columns:
            assert entry.digest_group, f"{entry.name} has a stale flag, so a change of it must show in the digest"
    assert registry.get("image_description_feedback").digest_group  # owner curation of a description (S3 backlog)


def test_class_boundaries_are_pinned():
    assert {e.name for e in REG.values() if e.klass is Klass.X} == EXPECTED_X
    assert {e.name for e in REG.values() if e.klass is Klass.P} == PULLED
    assert {e.name for e in REG.values() if e.klass is Klass.D} == EXPECTED_D


def test_prod_write_sets():
    assert set(registry.prod_write_set("backend_app")) == PULLED, "the prod backend may write exactly the reader-activity tables"
    assert set(registry.prod_write_set("sync_writer")) == set(registry.shipped_tables())
    assert not set(registry.prod_write_set("backend_app")) & set(registry.prod_write_set("sync_writer"))
    assert set(registry.prod_write_set("none")) == set(registry.skipped_tables())
    assert set(registry.pulled_tables()) == PULLED


def test_setval_is_set_exactly_for_integer_primary_keys():
    for entry in (e for e in REG.values() if e.klass is not Klass.X):
        integer_pk = any(isinstance(c.type, Integer) for c in TABLES[entry.name].primary_key.columns)
        assert entry.setval is integer_pk, f"{entry.name}: setval must be {integer_pk}"


def test_load_and_delete_order_are_complete_and_mirror_each_other():
    for klass in (Klass.D, Klass.P):
        load = registry.load_order(klass)
        flat = [name for level in load for name in level]
        assert sorted(flat) == registry.names(klass) and len(flat) == len(set(flat))
        assert registry.delete_order(klass) == list(reversed(load))


def test_the_rendered_page_is_current():
    current = DOC.read_text(encoding="utf-8") if DOC.exists() else ""
    assert current == render(), "docs/sync-registry.md is stale: run `python -m sync.render --write`"
