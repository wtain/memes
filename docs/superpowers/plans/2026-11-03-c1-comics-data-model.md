# C1 Comics Data Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store comics (named, ordered lists of images) with database-enforced rules, a repository that keeps them, and the registry entries.

**Architecture:** Two new tables (`comics`, `comics_panels`) in `Storage/models.py`, one alembic migration, a pure name-rules module and an async `ComicsRepository` in the global `repository/` layer (never commits). No endpoint, no composer, no build (C2, C3, C4). Panels leave the library later through the existing flag, move and unregister path, so there is no new image status.

**Tech Stack:** SQLAlchemy 2 async ORM, alembic, PostgreSQL, pytest (integration root with `ocrdb_test`, plus a small unit root).

**Spec:** `docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-02-c1-data-model-and-status-registry-design.md`

## Global Constraints

- One comics per image: unique index on `comics_panels.image_id`.
- `comics_panels.image_id` is `ON DELETE SET NULL`; `source_filename` and `source_content_hash` are snapshots taken when the image is added.
- `comics_panels.comics_id` is `ON DELETE CASCADE`; `comics.built_image_id` is `ON DELETE SET NULL`, unique.
- `(comics_id, position)` is unique and `DEFERRABLE INITIALLY DEFERRED`; `position >= 0` is a CHECK; positions are contiguous from 0 after every change.
- `ComicsState`: `draft`, `built`, with a CHECK constraint like `image_cuts.state`.
- Name: trimmed and NFC-normalized, 1 to 80 characters, no control characters or any of `< > : " / \ | ? *`, no trailing dot or space, not a Windows reserved device name (`CON`, `PRN`, `AUX`, `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9`, with or without an extension). Unique on `name_key` (NFC, case-folded).
- A built comics rejects every change and cannot be deleted. A draft can be renamed and deleted.
- Repositories never call `session.commit()`.
- Popularity source `comics_build` is added to `PopularitySource` and to `ck_popularity_absorptions_source`; the existing unflag logic stays limited to its three sources.
- Flag reason `comics_source` is documented only; C4 sets it.
- Never touch the live metal/general/it databases or ports. Integration tests use `ocrdb_test` (see CLAUDE.md).

## Review Focus

- Two comics whose names differ only by case or by composed vs decomposed accents (`Café`, `Café`): the second must be refused as `name_taken`. Pinned in Task 2 and Task 3.
- Deleting an image that is a draft panel: the panel row stays with `image_id` NULL and its snapshot; a second image can then be added and the missing one removed without gaps in positions. Pinned in Task 4.
- Adding an image that is flagged, not active, in another comics, or part of a live recut proposal (as source or as cut): each refused with its own code. Pinned in Task 4.
- Reorder with a list that omits, repeats or adds a panel id: refused, nothing changes. Pinned in Task 4.
- Every mutation on a built comics, including delete and `mark_built` again: refused. Pinned in Task 3 and Task 4.

---

### Task 1: Models, migration and registries

**Files:**
- Modify: `Storage/models.py` (add `ComicsState`, `Comics`, `ComicsPanel`; add `COMICS_BUILD` to `PopularitySource`, including `ALL`)
- Create: `Storage/alembic/versions/<new id>_add_comics_tables.py` (revises `b4c9e1f7a028`; confirm with `cd Storage; alembic heads` first)
- Modify: `repository/cut_copier.py` (add `"comics"`, `"comics_panels"` to `NOT_COPIED_TABLES`)
- Modify: `docs/image-derived-data.md` (two `relation` rows under "## Inventory"), `docs/image-status.md` (a Transitions bullet, a "Not image statuses" row for `comics.state`), `docs/schema.md` (source list at line 31, and the two tables), `Frontend/memes-frontend/src/utils/popularitySources.ts` (label `comics_build: "Comics built"`)
- Test: `tests/integration/test_comics_model.py`

**Interfaces:**
- Produces: `ComicsState.DRAFT`, `ComicsState.BUILT`, `ComicsState.ALL`; `Comics(id, name, name_key, state, built_image_id, built_content_hash, created_at, built_at, panels)` where `panels` is a relationship ordered by `ComicsPanel.position`; `ComicsPanel(id, comics_id, image_id, position, source_filename, source_content_hash)`; `PopularitySource.COMICS_BUILD == "comics_build"`.

- [ ] **Step 1: Write the failing test** `tests/integration/test_comics_model.py` (uses the `db_session` fixture; create `Image` rows directly). Tests, each asserting `IntegrityError` or the stated state:
  - `test_name_key_is_unique`: two `Comics` with equal `name_key` fail on flush.
  - `test_one_comics_per_image`: two `ComicsPanel` with the same non-null `image_id` fail; two panels with NULL `image_id` do not.
  - `test_position_unique_but_deferrable`: duplicate `(comics_id, position)` fails after `SET CONSTRAINTS ALL IMMEDIATE`; swapping two positions in one transaction succeeds after the same statement.
  - `test_position_must_not_be_negative`; `test_state_check_rejects_other_values`.
  - `test_deleting_an_image_nulls_the_panel_and_keeps_the_snapshot`: delete the `Image`, expire, the panel has `image_id is None` and its `source_filename` unchanged.
  - `test_deleting_a_comics_cascades_to_its_panels`; `test_deleting_the_built_image_nulls_built_image_id`.
  - `test_popularity_source_comics_build_is_accepted`: insert a `PopularityAbsorption` with `source="comics_build"` (copy the field values from an existing test in `tests/integration/test_popularity_repository.py`).

- [ ] **Step 2: Run** `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/test_comics_model.py -v`. Expected: FAIL on import of `Comics`.

- [ ] **Step 3: Implement the models** in `Storage/models.py` next to `ImageCut`, following its style (CHECK via `_in_list`, `Index(..., unique=True)`, `UniqueConstraint(..., deferrable=True, initially="DEFERRED")`). `Comics.panels = relationship(..., order_by=ComicsPanel.position, cascade="all, delete-orphan", passive_deletes=True)`. `created_at` uses `server_default=func.now()`, timezone-aware like `ImageCut`.

- [ ] **Step 4: Write the migration** `<id>_add_comics_tables.py` modeled on `c8e2f5a1d093_popularity_adjustment_source.py`: create both tables with every constraint from Global Constraints, then replace `ck_popularity_absorptions_source` with the nine existing sources plus `comics_build`. Downgrade refuses when any row has source `comics_build`, then drops the tables and restores the old check. Verify the migration against a scratch database that is not any of the three live ones: ask the user for one, or run `pg_dump --schema-only` into a throwaway database on port 5432 named `ocrdb_comics_migration_check` and drop it afterward; run `alembic upgrade head`, `alembic downgrade -1`, `alembic upgrade head` there, and `alembic heads` must print one head. If a scratch database is not possible, say so in the report instead of skipping silently.

- [ ] **Step 5: Registries.** Add `comics` and `comics_panels` to `NOT_COPIED_TABLES`; add the two `relation` rows to `docs/image-derived-data.md` ("Bookkeeping of the comics epic, see its README; never copied to a cut"); add to `docs/image-status.md` Transitions: "An `active` image that becomes a comics panel leaves the library by the duplicates path (flag with reason `comics_source`, move, unregister); it gets no status of its own (multi-image comics C1)" and a `comics.state` row (`draft`, `built`) in "Not image statuses"; add `comics_build` to `docs/schema.md`'s source list; add the frontend label. Add `comics_build` to the `popularitySources.test.ts` label test.

- [ ] **Step 6: Run** the new test, then `pytest tests/docs/ -v` (run from the repo root; it must show the inventory and cut-copier guards passing), then `cd Frontend/memes-frontend && vitest run src/utils/popularitySources.test.ts`. Expected: all PASS.

- [ ] **Step 7: Commit** `feat(comics): C1 tables, migration and registries`.

---

### Task 2: Name rules

**Files:**
- Create: `repository/comics_name.py`
- Create: `tests/comics/__init__.py` (empty, create with Bash), `tests/comics/test_comics_name.py`

**Interfaces:**
- Produces: `normalize_name(raw: str) -> tuple[str, str]` returning `(name, name_key)`; raises `ComicsNameError(reason: str)` (subclass of `ValueError`) with `reason` one of `empty`, `too_long`, `forbidden_character`, `trailing_dot_or_space`, `reserved_name`. `name_key` is the NFC-normalized name case-folded (`str.casefold()`).

- [ ] **Step 1: Write the failing tests** in `tests/comics/test_comics_name.py`:
  - `"  Мой комикс  "` returns `("Мой комикс", "мой комикс")`.
  - NFC: the decomposed `"Café"` and the composed `"Café"` return the same name and key.
  - `"Straße"` and `"STRASSE"` give the same `name_key` (casefold).
  - Parametrized refusals: `""` and `"   "` -> `empty`; 81 characters -> `too_long`, 80 passes; each of `< > : " / \ | ? *` and `"a\tb"` -> `forbidden_character`; `"abc."` and a name that ends in a space after normalization cannot occur (trimmed), so test `"abc. "` -> `trailing_dot_or_space`; `"con"`, `"NUL.txt"`, `"com1"`, `"LPT9"` -> `reserved_name`; `"console"` and `"com10"` pass.

- [ ] **Step 2: Run** `pytest tests/comics/ -v`. Expected: FAIL, module missing.

- [ ] **Step 3: Implement** `normalize_name` in `repository/comics_name.py`: trim, NFC, then check in the order empty, length, forbidden characters, trailing dot, reserved name (the part before the first dot, case-insensitive).

- [ ] **Step 4: Run** `pytest tests/comics/ -v`. Expected: PASS.

- [ ] **Step 5: Commit** `feat(comics): comics name rules`.

---

### Task 3: Comics lifecycle in the repository

**Files:**
- Create: `repository/comics.py`
- Test: `tests/integration/test_comics_repository.py`

**Interfaces:**
- Consumes: Task 1 models; `normalize_name` from Task 2.
- Produces: `ComicsError(Exception)` with `code: str` and `detail: dict`; codes used by the whole repository: `name_invalid` (detail `reason`), `name_taken`, `not_found`, `built`, `image_in_comics` (detail `comics_id`, `comics_name`), `image_not_addable` (detail `reason`: `not_found`, `not_active`, `flagged`, `live_cut`), `reorder_mismatch`, `not_buildable` (detail `reason`: `too_few_panels`, `panel_missing`, `image_not_active`). Class `ComicsRepository(session)` with:
  - `async create(name: str) -> Comics` (state `draft`);
  - `async rename(comics_id: uuid.UUID, name: str) -> Comics` (draft only);
  - `async get(comics_id: uuid.UUID) -> Comics` (panels loaded in order; `not_found`);
  - `async search(query: str | None, limit: int = 50, offset: int = 0) -> list[Comics]` (case-insensitive substring on `name`, ordered by `created_at desc, id`; `None` or empty lists all);
  - `async delete(comics_id: uuid.UUID) -> None` (draft only; cascades panels);
  - `async mark_built(comics_id: uuid.UUID, built_image_id: uuid.UUID, built_content_hash: str) -> Comics`: requires draft, at least 2 panels, none with NULL `image_id`, and an existing `active` built image; sets `state`, `built_image_id`, `built_content_hash`, `built_at = now`; leaves the panel rows alone.

- [ ] **Step 1: Write the failing tests** (`db_session`, `Image` rows created inline):
  - `test_create_normalizes_and_stores_the_key`; `test_create_with_an_invalid_name_raises_name_invalid`.
  - `test_names_differing_by_case_or_accents_are_taken` (`"Café"` composed then `"CAFÉ"` -> `name_taken`).
  - `test_rename_a_draft`; `test_rename_to_a_taken_name_raises_name_taken`; `test_renaming_to_its_own_name_with_other_case_is_allowed`.
  - `test_get_unknown_raises_not_found`.
  - `test_search_is_case_insensitive_substring_and_paged`.
  - `test_delete_a_draft_removes_its_panels` (create panel rows directly with the model, since `add_image` arrives in Task 4).
  - `test_mark_built_sets_state_and_leaves_panels` with two panel images and an active built image; then `test_mark_built_refuses` parametrized: one panel -> `not_buildable/too_few_panels`; a panel with NULL image -> `panel_missing`; built image with status `pending` -> `image_not_active`; already built -> `built`.
  - `test_a_built_comics_cannot_be_renamed_or_deleted` (both `built`).

- [ ] **Step 2: Run** `DATABASE_URL=... pytest tests/integration/test_comics_repository.py -v`. Expected: FAIL, import error.

- [ ] **Step 3: Implement** the class in `repository/comics.py` (module docstring in the style of `repository/image_cuts.py`: never commits). Convert `IntegrityError` on `name_key` into `name_taken` by checking for an existing key before the insert (flush inside a savepoint is not needed); use `select ... where Comics.name_key == key and Comics.id != comics_id` for rename.

- [ ] **Step 4: Run** the file again, then `pytest tests/docs/ -v`. Expected: PASS.

- [ ] **Step 5: Commit** `feat(comics): comics repository lifecycle`.

---

### Task 4: Panel operations in the repository

**Files:**
- Modify: `repository/comics.py`
- Test: `tests/integration/test_comics_panels.py`

**Interfaces:**
- Consumes: Task 3 `ComicsRepository`, `ComicsError`.
- Produces on `ComicsRepository`:
  - `async add_image(comics_id: uuid.UUID, image_id: uuid.UUID) -> ComicsPanel`: draft only; appends at the next position; stores `source_filename` and `source_content_hash` from the image; refuses with `image_not_addable` (`not_found`, `not_active`, `flagged` when `image_extras.flagged` is true, `live_cut` when an `image_cuts` row in `CutState.LIVE` has the image as `source_image_id` or `cut_image_id`) or `image_in_comics` (naming the other comics, also when it is this one);
  - `async move_image(image_id: uuid.UUID, to_comics_id: uuid.UUID) -> ComicsPanel`: both comics must be drafts; the panel row is updated in place to the target's next position, snapshot kept, and the source comics' positions are renumbered contiguously; `not_found` when the image is in no comics;
  - `async remove_panel(panel_id: uuid.UUID) -> None`: draft only; renumbers; the image itself is untouched;
  - `async reorder(comics_id: uuid.UUID, panel_ids: list[uuid.UUID]) -> None`: draft only; `panel_ids` must equal the current set exactly (no omission, repeat or addition) or `reorder_mismatch`; assigns positions 0..n-1 in that order (deferred unique constraint lets this happen in one flush).

- [ ] **Step 1: Write the failing tests** (`db_session`; images via `Image(...)`, flags via `ImageExtras(flagged=True)`, cuts via `ImageCut`):
  - `test_add_appends_with_contiguous_positions_and_snapshot`.
  - Parametrized `test_add_refuses`: `pending` image -> `image_not_addable/not_active`; flagged -> `flagged`; live cut as source -> `live_cut`; live cut as cut image -> `live_cut`; a `rejected`-state cut does not block; unknown id -> `not_found`.
  - `test_add_an_image_already_in_a_comics_names_that_comics` (same comics and another comics).
  - `test_move_image_between_drafts_renumbers_the_source` (3 panels, move the first; source positions become 0,1; target appends; snapshot kept); `test_move_refuses_when_either_comics_is_built`.
  - `test_remove_panel_renumbers`; `test_remove_a_missing_panel_works_after_its_image_is_deleted` (delete the `Image`, panel has NULL image, remove it, then add another image without a gap).
  - Parametrized `test_reorder_refuses_a_wrong_set`: omitted id, repeated id, an id of another comics -> `reorder_mismatch` and positions unchanged; `test_reorder_applies_the_order`.
  - `test_every_panel_operation_refuses_on_a_built_comics`: `add_image`, `move_image` (to and from), `remove_panel`, `reorder` all raise `built`.

- [ ] **Step 2: Run** `DATABASE_URL=... pytest tests/integration/test_comics_panels.py -v`. Expected: FAIL, methods missing.

- [ ] **Step 3: Implement** the four methods. Renumbering helper shared by `move_image` and `remove_panel` (private `_compact(comics_id)`): re-read panels ordered by position and assign 0..n-1; flush once at the end.

- [ ] **Step 4: Run** the test file, then the whole integration root: `DATABASE_URL="postgresql+asyncpg://ocr:ocr@localhost:5432/ocrdb_test" pytest tests/integration/ -v` (CLAUDE.md requires the whole root because the migration touches a shared check constraint), `pytest tests/docs/ -v`, `pytest tests/comics/ -v` and `cd Backend && pytest` (each its own command, never combined). Expected: all PASS; report any pre-existing failure with its output rather than hiding it.

- [ ] **Step 5: Commit** `feat(comics): comics panel operations`.

---

### Task 5: Close-out

**Files:**
- Modify: `docs/superpowers/specs/2026-11-02-multi-image-comics/tracker.md` (C1 status `done`, spec link to the plan), `docs/superpowers/specs/2026-11-02-multi-image-comics/2026-11-02-c1-data-model-and-status-registry-design.md` (Status `done`, `Plan:` line)
- Create: `docs/superpowers/reviews/2026-11-03-c1-comics-data-model-review.md`

- [ ] **Step 1:** One review pass of the whole branch diff against the spec (logic, duplication, tests, every requirement addressed), saved as the review file with a final summary: what was fixed, what was not fixed and why, what was ignored and why (CLAUDE.md workflow). Fix findings with one agent, one iteration.
- [ ] **Step 2:** Update the status lines and tracker. Commit `docs(comics): C1 done`.
