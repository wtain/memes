"""The dev/prod sync registry: one entry per table of Storage/models.py (epic environment-data-sync, project P1).

Says which stage owns each table, whether and how it is synced, in which order, and who may write it on prod. The promote
tool (P2), the pull tool (P4), the prod roles script (P5) and the drift report (P6) read this instead of keeping table lists of
their own. tests/sync/ fails when a table is missing, misclassified or out of order.

Design: docs/superpowers/specs/2026-11-12-environment-data-sync/2026-11-12-p1-sync-registry-design.md
Classes and rationale: .../2026-11-12-s1-data-classification.md and the decisions log in .../README.md

Add a table in a migration -> add its entry here (the test tells you), then `python -m sync.render --write`.
Standard library only.
"""
from sync.model import (
    GLOBAL_SET,
    IMAGES,
    NEVER,
    PROD_ALL,
    Klass,
    Mode,
    TableSync,
    image_fk,
    via,
)

D, P, X = Klass.D, Klass.P, Klass.X
UPSERT, REPLACE, MIRROR, SKIP = Mode.UPSERT_BY_KEY, Mode.REPLACE_PER_IMAGE, Mode.MIRROR_REPLACE, Mode.SKIP


def _d(name, mode, scope, key, level, digest=None, nullify=(), setval=False, prune=False, notes=""):
    return TableSync(
        name, D, mode, scope, tuple(key), level, digest, "sync_writer", tuple(nullify), setval, prune, False, notes
    )


def _p(name, key, level, setval=False, pull_first=False, notes=""):
    return TableSync(
        name, P, MIRROR, PROD_ALL, tuple(key), level, None, "backend_app", (), setval, False, pull_first, notes
    )


def _x(name, notes):
    return TableSync(name, X, SKIP, NEVER, (), None, None, "none", (), False, False, False, notes)


_ENTRIES = (
    # ---- class D: dev-origin, shipped ------------------------------------------------------------------------------
    _d("images", UPSERT, IMAGES, ("id",), 0, "core", nullify=("ingestion_batch_id",),
       notes="active and unflagged only; popularity promoted as a value; ingestion_batch_id blanked"),
    _d("image_extras", UPSERT, image_fk(), ("image_id",), 1, "core",
       notes="remarks only: flagged images are not shipped, so flagged is always false on prod"),
    _d("ocr_texts", REPLACE, image_fk(), ("id",), 1, "ocr", notes="includes the stale flag"),
    _d("ocr_lemmas", REPLACE, image_fk(), ("image_id", "lemma"), 1, "ocr", notes="includes the stale flag"),
    _d("embeddings", REPLACE, image_fk(), ("id",), 1, "clip", notes="CLIP 512-d"),
    _d("tags", REPLACE, image_fk(), ("id",), 1, "tags", notes="all sources; includes the stale flag"),
    _d("image_descriptions", REPLACE, image_fk(), ("id",), 1, "desc", notes="parent of the description tables"),
    _d("image_description_embeddings", REPLACE, via("image_descriptions", "image_description_id"),
       ("image_description_id",), 2, "desc", notes="1024-d"),
    _d("description_lemmas", REPLACE, via("image_descriptions", "image_description_id"),
       ("image_description_id", "lemma"), 2, "desc"),
    _d("image_description_feedback", REPLACE, via("image_descriptions", "image_description_id"),
       ("image_description_id",), 2, "desc", notes="owner curation; must stay in the digest"),
    _d("meme_profiles", REPLACE, image_fk(), ("image_id",), 1, "profile", notes="includes human-verified profiles"),
    _d("meme_profile_values", REPLACE, via("meme_profiles", "image_id"), ("image_id", "axis", "value_key"), 2, "profile"),
    _d("meme_profile_situation_embeddings", REPLACE, via("meme_profiles", "image_id"), ("image_id",), 2, "profile",
       notes="1024-d, bound to a model name and digest"),
    _d("description_notes", REPLACE, image_fk(), ("image_id",), 1, "note", notes="owner curation"),
    _d("description_note_lemmas", REPLACE, image_fk(), ("image_id", "lemma"), 1, "note"),
    _d("description_note_embeddings", REPLACE, via("description_notes", "description_note_id"),
       ("description_note_id",), 2, "note", notes="1024-d"),
    _d("concepts", UPSERT, GLOBAL_SET, ("id",), 0, setval=True, prune=True, notes="integer ids: one origin, replaced as a set"),
    _d("concept_image_sets", UPSERT, GLOBAL_SET, ("id",), 1, setval=True, prune=True),
    _d("concept_images", UPSERT, GLOBAL_SET, ("id",), 2, setval=True, prune=True),
    _d("concept_text_sets", UPSERT, GLOBAL_SET, ("id",), 1, setval=True, prune=True, notes="empty today; kept for parity"),
    _d("concept_texts", UPSERT, GLOBAL_SET, ("id",), 2, setval=True, prune=True, notes="empty today; kept for parity"),
    _d("popularity_absorptions", UPSERT, GLOBAL_SET, ("id",), 0, nullify=("batch_run_id",),
       notes="append-only, no FK to images; load in created_at order (absorb row, then its reverse row, then UPDATE the absorb "
             "row's reversed_by_id): the shape check and the open-loser unique index forbid a NULL-first load"),
    # ---- class P: prod-origin reader activity, pulled ---------------------------------------------------------------
    _p("meme_saves", ("id",), 0),
    _p("meme_impressions", ("id",), 0, setval=True, notes="bigint id: the dev sequence moves past max(id) after a pull"),
    _p("search_history", ("id",), 0),
    _p("search_history_tags", ("id",), 1, notes="FK to search_history"),
    _p("search_not_found", ("id",), 0),
    _p("meme_text_ratings", ("id",), 0, pull_first=True,
       notes="FK to images with CASCADE: pull before any promote retires an image; skip ratings of images missing on dev"),
    # ---- class X: dev-only, never synced ----------------------------------------------------------------------------
    _x("batch_runs", "run tracking"),
    _x("process_events", "business metrics of dev review flows"),
    _x("image_metrics", "OCR timing scratch"),
    _x("image_processing_status", "pipeline skip markers; prod runs no pipelines"),
    _x("processing_errors", "no writers"),
    _x("ocr_text_embeddings", "duplicate matching only"),
    _x("image_classifications", "text-heavy classifier, review UI only"),
    _x("tmp_duplicates", "rebuildable candidate pairs"),
    _x("tmp_clusters", "rebuildable clusters"),
    _x("duplicate_decisions", "consumed by clusterize only; back up on dev"),
    _x("comics", "not shipped (decision 22): a built comics reaches prod as an ordinary image; comics routes are dev-only"),
    _x("comics_panels", "not shipped (decision 22)"),
    _x("statistics_snapshots", "statistics page dropped on prod (decision 25)"),
    _x("comics_candidates", "review suggestions"),
    _x("comics_candidate_images", "review suggestions"),
    _x("image_cuts", "recut proposals, admin only"),
    _x("trend_sources", "trends_batch stays on dev; hidden on prod"),
    _x("trends_run_results", "trends_batch stays on dev; hidden on prod"),
)

REGISTRY: dict[str, TableSync] = {entry.name: entry for entry in _ENTRIES}
if len(REGISTRY) != len(_ENTRIES):
    raise RuntimeError("duplicate registry entry")

# The one declared exception to "a table loads after the tables it references": a self-referencing foreign key.
SELF_REFERENCING = {"popularity_absorptions": ("reverses_id", "reversed_by_id")}


def get(name: str) -> TableSync:
    return REGISTRY[name]


def names(klass: Klass | None = None) -> list[str]:
    return sorted(e.name for e in REGISTRY.values() if klass is None or e.klass == klass)


def shipped_tables() -> list[str]:
    return names(D)


def pulled_tables() -> list[str]:
    return names(P)


def skipped_tables() -> list[str]:
    return names(X)


def load_order(klass: Klass) -> list[list[str]]:
    """Tables of a class grouped by level, parents first; tables within a level are independent of each other."""
    levels: dict[int, list[str]] = {}
    for entry in REGISTRY.values():
        if entry.klass == klass:
            levels.setdefault(entry.level, []).append(entry.name)
    return [sorted(levels[level]) for level in sorted(levels)]


def delete_order(klass: Klass) -> list[list[str]]:
    """Reverse of load_order: children before parents."""
    return list(reversed(load_order(klass)))


def digest_groups() -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for entry in REGISTRY.values():
        if entry.digest_group:
            groups.setdefault(entry.digest_group, []).append(entry.name)
    return {group: sorted(tables) for group, tables in sorted(groups.items())}


def prod_write_set(role: str) -> list[str]:
    """Tables a role may write on prod (input of the P5 grants and of the S5 role test)."""
    return sorted(e.name for e in REGISTRY.values() if e.prod_writer == role)


def nullify_columns(name: str) -> tuple[str, ...]:
    return REGISTRY[name].nullify


def setval_tables() -> list[str]:
    return sorted(e.name for e in REGISTRY.values() if e.setval)
