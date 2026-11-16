# P1: sync registry (design)

Status: implementation (built, unmerged; follow-ups listed under P2 T1; text reconciled to the built state 2026-11-12)
Plan: none (small project, built inline; review in docs/superpowers/reviews/2026-11-12-p1-sync-registry-review.md)
Originates from: docs/superpowers/specs/2026-11-12-environment-data-sync/tracker.md
Depends on: S1 (`2026-11-12-s1-data-classification.md`), S3 (`docs/adr/adr-2026-11-12-dev-prod-sync-technique.md`), S5
(`2026-11-12-s5-prod-topology-and-roles.md`), decisions log in `README.md`.

## Intent

One place, as code, that says for every table of `Storage/models.py` which stage owns it, whether and how it is synced, in which
order, and who may write it on prod. The promote tool (P2), the pull tool (P4), the prod roles script and the drift report (P6) all
read this registry instead of keeping their own table lists. A test makes a new table fail the build until someone classified it,
in the spirit of `tests/docs/test_image_derived_data_inventory.py`.

P1 delivers the registry, its derived views and the guard tests. It moves no data and opens no database connection.

## Goals / non-goals

- Goals: a typed, importable registry covering all 46 tables (22 D, 6 P, 18 X); derived load, delete and grant views; guard tests that catch
  unclassified tables, wrong classes, bad foreign-key direction and an unreachable scope; a short doc page generated from, or
  checked against, the registry.
- Non-goals: any SQL execution, the sync commands themselves (P2/P3/P4), the roles SQL file (P5; P1 only exposes the write sets it
  needs), schema changes, a migration.
- Anti-goals: a registry that can drift from `Base.metadata` unnoticed; a second hand-kept list of tables anywhere else.

## Where it lives

A new top-level package `sync/` next to `retrieval/` and `parse/`: `sync/__init__.py`, `sync/registry.py` (data and derived views),
`sync/model.py` (the dataclasses). Runtime imports are the standard library only (an AST test enforces it, like `retrieval/`), so
the sync tools and the prod migration image can import it without SQLAlchemy. Only the tests import `Storage.models`.
My call: `sync/` rather than `Storage/` because it is tooling, not schema; the name `sync` is free in the repo (to be checked at
implementation, rename to `envsync/` if it shadows anything).

## Entry shape (as built, `sync/model.py`)

```python
@dataclass(frozen=True)
class TableSync:
    name: str
    klass: Klass                       # D, P, X
    mode: Mode                         # how it moves (below)
    scope: Scope                       # which rows move
    key: tuple[str, ...]               # the primary key; empty for class X
    level: int | None                  # load order, parents first; None for class X
    digest_group: str | None = None    # per-image digest bucket (D, replace_per_image and the image core only)
    prod_writer: str = "none"          # "none" | "backend_app" | "sync_writer" (logical roles; concrete names in interfaces.md)
    nullify: tuple[str, ...] = ()      # FK columns blanked because the target is class X
    setval: bool = False               # integer primary key: the sequence is moved past max(id) after a load
    prune: bool = False                # global set: rows deleted on dev are deleted on prod too (only concepts*)
    pull_before_promote: bool = False  # class P with a CASCADE from images (meme_text_ratings)
    notes: str = ""
```

`Mode`: `UPSERT_BY_KEY` (`images`, `image_extras` at build time, `concepts*`, `popularity_absorptions`), `REPLACE_PER_IMAGE` (delete and insert all rows of a changed image,
digest-driven), `MIRROR_REPLACE` (class P on pull), `SKIP` (class X). `Scope` is a dataclass `(kind, column, parent)`: `images` (the promotable set: status `active` and not flagged,
`PROMOTABLE_IMAGES_SQL` in `sync/model.py`), `image_fk(column)`, `via(parent, column)`, `global_set` (concepts, absorptions), `prod_all` (class P), `never`. There is no
`Scope.where` and no comics rule: comics are class X.

## The registry (all 46 tables = 22 D, 6 P, 18 X; `docs/sync-registry.md` is the generated, authoritative listing)

Level is the load order (parents first); the delete order is the reverse. Promotable = active and unflagged.

### Class D (dev-origin, shipped, 22 tables)

| Table | Level | Mode | Scope | Digest group | Notes |
|---|---|---|---|---|---|
| `images` | 0 | upsert_by_key | images | `core` | `ingestion_batch_id` nullified; `popularity` promoted as a value |
| `image_extras` | 1 | upsert_by_key (built); `replace_per_image` after P2-T1 | image_fk | `core` | only `remarks`; flagged images are not promoted, so `flagged` is always false on prod |
| `ocr_texts`, `ocr_lemmas` | 1 | replace_per_image | image_fk | `ocr` | include the `stale` flag |
| `embeddings` | 1 | replace_per_image | image_fk | `clip` | 512-d vector |
| `tags` | 1 | replace_per_image | image_fk | `tags` | all sources; includes `stale` |
| `image_descriptions` | 1 | replace_per_image | image_fk | `desc` | parent of the next three |
| `image_description_embeddings`, `description_lemmas`, `image_description_feedback` | 2 | replace_per_image | via(image_descriptions) | `desc` | feedback must be in the digest |
| `meme_profiles` | 1 | replace_per_image | image_fk | `profile` | includes human-verified rows |
| `meme_profile_values`, `meme_profile_situation_embeddings` | 2 | replace_per_image | via(meme_profiles) | `profile` | the embeddings carry model name and digest |
| `description_notes`, `description_note_lemmas` | 1 | replace_per_image | image_fk | `note` | |
| `description_note_embeddings` | 2 | replace_per_image | via(description_notes) | `note` | |
| `concepts` | 0 | upsert_by_key | global_set | none | integer ids, replaced as a set (`setval`, `prune`) |
| `concept_image_sets`, `concept_text_sets` | 1 | upsert_by_key | global_set | none | `setval`, `prune`; the text set is empty today |
| `concept_images`, `concept_texts` | 2 | upsert_by_key | global_set | none | `setval`, `prune` |
| `popularity_absorptions` | 0 | upsert_by_key | global_set | none | decisions log 1; no FK to images; `batch_run_id` nullified; self-FKs: absorb row, its reverse row, then UPDATE of `reversed_by_id`. P2 applies it as **insert-or-close** (follow-up 3 below) |

There are no comics tables and no `statistics_snapshots` here: they are class X (decisions 22 and 25). There is no digest group `comics`. `prune` is set only on the five `concepts*` tables.

### Class P (prod-origin, pulled, 6 tables)

`meme_saves`, `meme_impressions` (`setval`), `search_history`, `search_history_tags` (level 1, FK to `search_history`), `search_not_found`, `meme_text_ratings` (mode `mirror_replace`, scope
`prod_all`, prod writer `backend_app`; `pull_before_promote`: FK to images CASCADE, so the pull runs before a promote may retire an image and skips ratings of images missing on dev).

### Class X (never synced, 18 tables)

`batch_runs`, `process_events`, `image_metrics`, `image_processing_status`, `processing_errors`, `ocr_text_embeddings`, `image_classifications`, `tmp_duplicates`, `tmp_clusters`,
`duplicate_decisions`, `comics`, `comics_panels`, `statistics_snapshots`, `comics_candidates`, `comics_candidate_images`, `image_cuts`, `trend_sources`, `trends_run_results`
(mode `skip`, scope `never`, prod writer `none`). Each carries a one-line reason in `notes`.

`alembic_version` is not an ORM table and is outside the registry; the schema gate compares it separately (S3).

## Derived views (pure functions, `sync/registry.py`)

- `load_order(klass)` / `delete_order(klass)`: tables grouped by level (a test proves the declared level agrees with the foreign-key topology).
- `shipped_tables()`, `pulled_tables()`, `skipped_tables()`, `names(klass)`.
- `digest_groups()`: group name to tables.
- `prod_write_set(role)`: `backend_app` = the six P tables; `sync_writer` = the 22 D tables. P5 turns this into `GRANT` statements; the single exception (a column-level
  `SELECT` on three columns of `meme_text_ratings` for the ratings guard) is a P5 grant outside the write set (`interfaces.md`, section h).
- `nullify_columns(table)`, `setval_tables()`, `SELF_REFERENCING`.

## Guard tests (all run without a database; `tests/sync/`, 19 tests)

Complete (one entry per table, none stale); class/mode/writer agree; key is the primary key; nullify columns are foreign keys; class D foreign keys stay inside what is shipped; the
class P cascade is declared as `pull_before_promote`; class P foreign keys point only to images or class P; level is a topological order within a class; scope is reachable from the
promotable images; a table with a direct image foreign key is scoped by it unless blanked; image-scoped children are replaced whole and global sets say whether they prune; digest groups
cover every replaced table and every stale flag; class boundaries are pinned (22/6/18); prod write sets; `setval` exactly for integer primary keys; load and delete order mirror each other;
the rendered page is current; the registry core imports only the standard library and `sync`.

## Special cases the registry encodes (and P2/P4 implement)

- **Nullified columns:** `images.ingestion_batch_id` and `popularity_absorptions.batch_run_id` (targets are class X) are always blanked on prod.
- **Ratings:** the only P table with a cascade from `images` (`pull_before_promote`). P2 refuses to retire an image unless a fresh pull ran (pull state, 1 h) and its ratings guard passes.
- **Absorptions:** UUID ids preserved, append-only on dev; the ordered load handles the self-FKs; no `setval`.
- **Integer sequences:** after a load of integer-keyed tables (`concepts*`, and `meme_impressions` after a pull), P2/P4 run `setval` to `max(id)` (coalesce form).
- **Pruning:** upsert never deletes, so the `concepts*` global sets carry `prune`; a test requires image-scoped children to be `replace_per_image` and every global set to say whether it prunes.
- **Promotable predicate:** `PROMOTABLE_IMAGES_SQL`; P2 and P4 use it instead of re-implementing it.
- **Per-environment asymmetry:** tables legitimately empty on `it` (descriptions, tags, profiles, concepts) are not special-cased.

## Follow-ups owned by P2 task T1 (registry amendments; none is built yet, P1 stays `implementation` until they are)

1. `digest_exclude: tuple[str, ...] = ()` on `TableSync`: columns left out of the digest, each with a stated reason (unreferenced surrogate ids and `created_at` of `ocr_texts`, `embeddings`,
   `tags`; `images.ingestion_batch_id`; `description_notes.lemmas_built_at` and `embedding_built_at`). A test asserts the excluded columns exist and that stale flags,
   `image_description_feedback.approved` and `popularity` are not excluded. `docs/sync-registry.md` is regenerated.
2. `image_extras` changes from `upsert_by_key` to `replace_per_image` (`interfaces.md`, resolved call 4: it is the per-image remarks row, P2 deletes it with the other rows of a
   changed image, and a removed remark must leave prod). `images` stays the only image-scoped upsert; the mutation list is extended.
3. The `popularity_absorptions` note says "insert-or-close" (a row open on prod and closed on dev is closed, not skipped), replacing "append-only upsert by id".
4. The mutation test list follows these three changes.

## Work breakdown

| Task | Delivers |
|---|---|
| T1 | `sync/model.py` dataclasses and enums |
| T2 | `sync/registry.py` with all 46 entries (22 D, 6 P, 18 X) and the derived views |
| T3 | `tests/sync/` guard tests 1 to 9 |
| T4 | `docs/sync-registry.md` generator and the link from S1 and `docs/image-derived-data.md` |
| T5 | CLAUDE.md pointer ("a new table needs a registry entry"), tracker update |

Size: small, one day of work. Mode: inline (no subagents needed), then one fresh-subagent review focused on test strength: try to
make each guard fail by mutating the registry.

## Accepted data exposure (owner call)

By design the following reach prod although they describe non-promotable things: `image_extras.flagged_reason` (the flag itself is always false on prod) and loser filenames and
hashes inside `popularity_absorptions` (decision 1; owner question in the tracker). Comics and statistics are not shipped (decisions 22 and 25), so draft comics and panel
snapshots no longer reach prod.

## Verification

`pytest tests/sync/` (19 tests, no DB; 46 tables: 22 D / 6 P / 18 X) and `pytest tests/docs/`; the controller also ran 18 deliberate registry mistakes against the tests and each failed a named test; the registry test run is added to CI next to the docs guards. The full
`tests/integration/` root is not affected (no shared normalization or matching code is touched).

## Choices made by me (to confirm)

- Package name and location (`sync/`).
- `R` removed; trends, comics and statistics as `X` (decisions 22 and 25 reversed the earlier D classification of comics and statistics).
- One digest group per derived family (`core`, `ocr`, `clip`, `tags`, `desc`, `profile`, `note`) rather than one per table.
- Vector dimensions are not stored in the registry; the precondition check in P2 reads them from the schema.

## Open for the owner

None blocking. The choices above are for review.

## Revision notes (2026-11-12 reconciliation)

| Item | Action |
|---|---|
| Counts and classes | updated to the built registry: 46 tables = 22 D / 6 P / 18 X; comics, comics_panels, statistics_snapshots are class X; no comics rules, digest group or `Scope.where`; `prune` only on `concepts*` |
| Entry shape | text matches `sync/model.py` (enums, `level`, `setval`, `prune`, `pull_before_promote`) |
| Amendments | `digest_exclude`, `image_extras` replace mode and the absorptions note listed as follow-ups of P2-T1; cross-document names in `interfaces.md` |

## Revision notes (2026-11-13 digest exclusion audit)

Owner decision: a rebuild with identical content must not flip a digest group. Every timestamp column of a class D digest table was audited against `Storage/models.py`. A column is excluded when a rebuild changes it without a content change and nothing on prod reads it as evidence; it is kept when it is content, a curation event, or staleness evidence. Guards in `tests/sync/test_registry.py`: the pinned exclusion set; every timestamp column of a digest table is either excluded or listed in `TIMESTAMP_KEPT` with a reason; an excluded column must be a timestamp or one of four named surrogate or run columns; stale flags, `human_verified`, `approved`, `popularity`, `description_notes.updated_at` and `image_descriptions.created_at` are never excluded. Mutation-tested: removing an exclusion fails the pin and the audit guard, and excluding `popularity` or `human_verified` fails the content guards.

| table.column | Decision | Reason |
|---|---|---|
| `ocr_texts.id`, `embeddings.id`, `tags.id` | exclude (existing) | unreferenced surrogate ids |
| `ocr_texts.created_at`, `embeddings.created_at`, `tags.created_at` | exclude (existing) | rebuild timestamp |
| `images.ingestion_batch_id` | exclude (existing) | blanked on prod, run bookkeeping |
| `description_notes.lemmas_built_at`, `.embedding_built_at` | exclude (existing) | bookkeeping of the lemma and embedding jobs |
| `ocr_lemmas.created_at` | exclude (new) | rebuild timestamp; no staleness logic reads it (`--incremental` tests presence) |
| `image_description_embeddings.created_at` | exclude (new) | rebuild timestamp of a derived vector, keyed by the description id |
| `description_lemmas.created_at` | exclude (new) | rebuild timestamp of a derived index |
| `description_note_embeddings.created_at` | exclude (new) | rebuild timestamp of a derived vector |
| `description_note_lemmas.created_at` | exclude (new) | rebuild timestamp of a derived index |
| `meme_profile_situation_embeddings.created_at` | exclude (new) | rebuild timestamp; the model name and digest stay in the digest |
| `meme_profiles.extracted_at` | exclude (new) | written together with `updated_at` on every extraction; staleness is derived from taxonomy version, prompt version, model and input fingerprint, which stay in the digest |
| `meme_profiles.updated_at` | exclude (new; the P2 "check before excluding" is done) | `repository/meme_profiles.py` sets it with `extracted_at` on every write, so keeping it would keep every identical rebuild a change; a human save or confirm also changes `human_verified`, values or `taxonomy_version`, which stay in the digest. Cost: prod can show an older "updated" time than dev for a re-confirmed profile |
| `images.created_at` | keep | registration time, a rebuild never rewrites it |
| `image_descriptions.created_at` | keep | the table's id is referenced by three child tables and changes on every regeneration, so excluding the timestamp gains nothing; it also orders descriptions (`ORDER BY created_at, id`) and is the evidence of `build_tags_from_descriptions` staleness |
| `image_description_feedback.created_at` | keep | owner curation event, not a rebuild |
| `description_notes.updated_at` | keep | owner edit time, staleness evidence of `build_tags_from_notes` |
| `popularity_absorptions.created_at` | keep (global set, no digest group) | load order and content of the log |

