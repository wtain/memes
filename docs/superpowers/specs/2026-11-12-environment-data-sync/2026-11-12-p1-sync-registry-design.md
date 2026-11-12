# P1: sync registry (design)

Status: draft
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

- Goals: a typed, importable registry covering all 46 tables (25 D, 6 P, 15 X); derived load, delete and grant views; guard tests that catch
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

## Entry shape

```python
@dataclass(frozen=True)
class TableSync:
    name: str
    klass: Literal["D", "P", "X"]          # S1 classes; R is gone (statistics is D, see decisions log)
    mode: Mode                              # how it moves (below)
    scope: Scope                            # which rows move
    key: tuple[str, ...]                    # natural key used for upsert / compare (the primary key unless noted)
    digest_group: str | None                # per-image digest bucket (D, per-image modes only)
    prod_writer: Literal["none", "backend_app", "sync_writer"]  # who may write it on prod (S5 roles)
    nullify: tuple[str, ...] = ()           # FK columns set NULL when the target is not shipped (see below)
    notes: str = ""                         # one line, e.g. the retire rule
```

`Mode`: `upsert_by_key` (small sets: concepts, statistics, absorptions, comics), `replace_per_image` (delete and insert all rows of a
changed image, digest-driven, S3), `mirror_replace` (P tables on pull: replace the dev copy), `skip` (class X).

`Scope`: `images` (the promotable set: `status = 'active'` and not flagged), `image_fk(column)` (rows whose `image_id` column is in
the promotable set), `via(parent_table, column)` (rows reachable through a parent that is itself scoped, e.g. `description_lemmas`
via `image_descriptions.id`), `global_set` (no image relation: concepts, statistics, absorptions, comics), `prod_all` (class P, the
whole prod table), `never`.

## The registry (all 46 tables)

Level is the load order (parents first); the delete order is the reverse. Promotable = active and unflagged.

### Class D (dev-origin, shipped)

| Table | Level | Mode | Scope | Digest group | Notes |
|---|---|---|---|---|---|
| `images` | 0 | upsert_by_key | images | `core` | `ingestion_batch_id` nullified; `popularity` promoted as a value |
| `image_extras` | 1 | upsert_by_key | image_fk | `core` | only `remarks`; flagged images are not promoted, so `flagged` is always false on prod |
| `ocr_texts` | 1 | replace_per_image | image_fk | `ocr` | includes the `stale` flag |
| `ocr_lemmas` | 1 | replace_per_image | image_fk | `ocr` | includes `stale` |
| `embeddings` | 1 | replace_per_image | image_fk | `clip` | 512-d vector |
| `tags` | 1 | replace_per_image | image_fk | `tags` | all sources; includes `stale` |
| `image_descriptions` | 1 | replace_per_image | image_fk | `desc` | parent of the next four |
| `image_description_embeddings` | 2 | replace_per_image | via(image_descriptions) | `desc` | 1024-d |
| `description_lemmas` | 2 | replace_per_image | via(image_descriptions) | `desc` | |
| `image_description_feedback` | 2 | replace_per_image | via(image_descriptions) | `desc` | must be in the digest (S3 backlog) |
| `meme_profiles` | 1 | replace_per_image | image_fk | `profile` | includes human-verified rows |
| `meme_profile_values` | 2 | replace_per_image | via(meme_profiles) | `profile` | |
| `meme_profile_situation_embeddings` | 2 | replace_per_image | via(meme_profiles) | `profile` | 1024-d, carries model name and digest |
| `description_notes` | 1 | replace_per_image | image_fk | `note` | |
| `description_note_lemmas` | 1 | replace_per_image | image_fk | `note` | |
| `description_note_embeddings` | 2 | replace_per_image | via(description_notes) | `note` | |
| `comics` | 1 | upsert_by_key | global_set | none | `built_image_id` nullified when the built image is not shipped; key `name_key` |
| `comics_panels` | 2 | upsert_by_key | via(comics) | none | `image_id` nullified when the panel image is not shipped (unkept panels are flagged `comics_source`); snapshot columns stay |
| `concepts` | 0 | upsert_by_key | global_set | none | integer ids, replaced as a set (one origin, S1 constraint 6) |
| `concept_image_sets`, `concept_images` | 1, 2 | upsert_by_key | global_set | none | |
| `concept_text_sets`, `concept_texts` | 1, 2 | upsert_by_key | global_set | none | empty today; kept for parity |
| `popularity_absorptions` | 0 | upsert_by_key | global_set | none | decisions log 1; no FK to images; self-FKs `reverses_id`/`reversed_by_id` are loaded in two passes (rows first with the two columns NULL, then updated) |
| `statistics_snapshots` | 0 | upsert_by_key | global_set | none | decisions log 4; the prod route never recomputes |

### Class P (prod-origin, pulled)

| Table | Mode | Scope | prod_writer | Notes |
|---|---|---|---|---|
| `meme_saves`, `meme_impressions`, `search_history`, `search_history_tags`, `search_not_found` | mirror_replace | prod_all | backend_app | no FK to images (`search_history_tags` has an FK to `search_history` only); `search_history_tags` loads after `search_history` |
| `meme_text_ratings` | mirror_replace | prod_all | backend_app | FK to images CASCADE: the pull filters to images that exist on dev, and the promote must never delete a prod image before the pull has run (rule `pull_before_promote`, S3) |

### Class X (never synced)

`batch_runs`, `process_events`, `image_metrics`, `image_processing_status`, `processing_errors`, `ocr_text_embeddings`,
`image_classifications`, `tmp_duplicates`, `tmp_clusters`, `duplicate_decisions`, `comics_candidates`, `comics_candidate_images`,
`image_cuts`, `trend_sources`, `trends_run_results` (mode `skip`, scope `never`, prod_writer `none`). Each carries a one-word
reason in `notes` (scratch, rebuildable, admin-only, hidden-on-prod).

`alembic_version` is not an ORM table and is outside the registry; the schema gate compares it separately (S3).

## Derived views (pure functions)

- `load_order()` / `delete_order()`: tables of a class grouped by level. The level of each table is declared, and a test proves it
  agrees with the foreign-key topology (below), so a wrong level fails instead of failing on prod with an FK error.
- `shipped_tables()`, `pulled_tables()`, `skipped_tables()`.
- `digest_groups()`: group name to tables, so P2 builds one digest per image and per group, and a regenerated description or a new
  tag is seen as a change in exactly one group.
- `prod_write_set(role)`: tables a role may write on prod. `backend_app` = the six P tables; `sync_writer` = all D tables; this is what P5
  turns into `GRANT` statements and what the S5 role test checks.
- `nullify_columns(table)`: the FK columns to blank when their target is not shipped.

## Guard tests (all run without a database; `tests/sync/`)

1. **Complete:** every table in `Base.metadata` has exactly one registry entry and every entry names a real table.
2. **No orphan classes:** class matches mode (D: `upsert_by_key` or `replace_per_image`; P: `mirror_replace`; X: `skip`).
3. **FK direction:** a class D table's foreign keys point only to D tables or are in `nullify` (so `images.ingestion_batch_id` to the X table
   `batch_runs` and `comics_panels.image_id` are explicit exceptions, nothing else); a class P table's foreign keys point only to `images`
   or to another P table.
4. **Level is a topological order:** for every shipped FK child, `level(child) > level(parent)`; the popularity self-FK is the one declared exception.
5. **Scope reachable:** every `image_fk` column and every `via` parent/column exists and has an FK path to `images`.
6. **Digest coverage:** every class D table with `replace_per_image` is in exactly one digest group; the group list contains the `stale`
   columns' tables (`ocr_texts`, `ocr_lemmas`, `tags`) and `image_description_feedback`.
7. **Write sets:** `prod_write_set("backend_app")` equals the six P tables and contains no D or X table; no table appears in two
   roles' write sets; X tables are in no write set.
8. **Imports:** `sync/` imports only the standard library (AST test).
9. **Doc in step:** a short generated page `docs/sync-registry.md` (table, class, mode) equals what the registry prints; the test fails
   with the command to regenerate it. This replaces keeping the S1 tables by hand once P1 is done; S1 then links to the page.

A new table in a later migration fails test 1 until it is classified, which is the whole point.

## Special cases the registry encodes (and P2/P4 implement)

- **Not shipped but referenced:** `comics.built_image_id`, `comics_panels.image_id` and `images.ingestion_batch_id` are nullified, never
  dropped from the row.
- **Ratings:** the only P table with a cascade from `images`; `pull_before_promote` is a registry rule P2 checks (it refuses to retire an image on prod
  unless a pull ran in the same session).
- **Absorptions:** ids are preserved and the table is append-only, so an upsert by `id` is idempotent; the two-pass load handles the
  self-FKs.
- **Integer sequences:** after an upsert of integer-keyed D tables (`concepts*`, `popularity_absorptions`), P2 runs `setval` to
  `max(id)`; the registry lists which tables need it (`key` of integer type).
- **Per-environment asymmetry:** tables that are legitimately empty on `it` (descriptions, tags, profiles, concepts) are not special-cased; the
  tools treat an empty shipped table as normal.

## Work breakdown

| Task | Delivers |
|---|---|
| T1 | `sync/model.py` dataclasses and enums |
| T2 | `sync/registry.py` with all 46 entries and the derived views |
| T3 | `tests/sync/` guard tests 1 to 9 |
| T4 | `docs/sync-registry.md` generator and the link from S1 and `docs/image-derived-data.md` |
| T5 | CLAUDE.md pointer ("a new table needs a registry entry"), tracker update |

Size: small, one day of work. Mode: inline (no subagents needed), then one fresh-subagent review focused on test strength: try to
make each guard fail by mutating the registry.

## Verification

`pytest tests/sync/` (no DB) and `pytest tests/docs/`; the registry test run is added to CI next to the docs guards. The full
`tests/integration/` root is not affected (no shared normalization or matching code is touched).

## Choices made by me (to confirm)

- Package name and location (`sync/`).
- Statistics as `D` and `R` removed; trends as `X`.
- Comics shipped as a global set rather than per image; their snapshots are kept even if the panel image is not shipped.
- One digest group per derived family (`core`, `ocr`, `clip`, `tags`, `desc`, `profile`, `note`) rather than one per table.
- Vector dimensions are not stored in the registry; the precondition check in P2 reads them from the schema.

## Open for the owner

None blocking. The choices above are for review.
