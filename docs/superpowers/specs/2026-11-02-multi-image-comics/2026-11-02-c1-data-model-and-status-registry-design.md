# C1: Data model and registry for comics

Status: draft
Originates from: docs/superpowers/specs/2026-11-02-multi-image-comics/README.md (tracker project C1)
Follow-ups: none yet

## Intent

A comics is a named, ordered list of existing images that the owner wants to turn into one tiled image. C1 delivers the stored
shape of that idea, the rules the database itself enforces, the data-access functions that keep those rules, and the entries in the
status and derived-data registries. It delivers no endpoint, page, composer or build (those are C3, C2 and C4).

## Decisions by the owner (brainstorming, 2026-11-02)

- A comics is persistent, named and searchable, and can exist with fewer than two images; "build" needs at least two (enforced by C4).
- Building is final: no undo, no reversal of a built comics.
- After a build the panel rows are **deleted**, the way duplicates are today: the panels are flagged, their files are moved to
  `comics-sources/<comics name>/`, and `unregister_deleted_images` removes the rows and their derived data. There is **no new
  image status** for retired panels.
- The built image inherits the panels' popularity through the existing absorption mechanism, as a duplicate keeper does.
- An image belongs to **at most one** comics at a time.

## Data model

Both tables are new, migration-managed, and defined only in `Storage/models.py`. A string-constant class `ComicsState`
(`draft`, `built`) sits next to `CutState` and the column carries a CHECK constraint, like `image_cuts.state`.

**`comics`**

| Column | Notes |
|---|---|
| `id` | UUID primary key |
| `name` | the identifier the owner sees; also the directory name in `comics-sources/`; see Name rules |
| `state` | `draft` or `built` |
| `built_image_id` | FK to `images`, `ON DELETE SET NULL`, unique; set at build, NULL while a draft |
| `built_content_hash` | audit anchor for the built image, NULL while a draft |
| `created_at`, `built_at` | timestamps |

`name_key` (not null) holds the name NFC-normalized and case-folded, computed in code; a unique index on `name_key` makes names
case-insensitively unique the way a Windows directory name is, independent of the database collation. There is no other unique
constraint.

**`comics_panels`**

| Column | Notes |
|---|---|
| `id` | UUID primary key |
| `comics_id` | FK to `comics`, `ON DELETE CASCADE` |
| `image_id` | FK to `images`, `ON DELETE SET NULL`, nullable |
| `position` | integer, 0-based order in the tiling; the order the owner sets |
| `source_filename`, `source_content_hash` | snapshot of the image at the time it is added, kept when the image row is later deleted |

Constraints: unique index on `image_id` (one comics per image; PostgreSQL treats NULLs as distinct, so deleted panels do not
collide), and unique `(comics_id, position)` declared `DEFERRABLE INITIALLY DEFERRED` so a reorder can swap positions inside one
transaction. `position >= 0` is a CHECK.

Why SET NULL and a snapshot: after a build the panel images are deleted, but the comics keeps a record of what it was made from
(provenance, and the file names to find the moved sources in `comics-sources/`). While a comics is still a draft, an image that
disappears through another route (flagged as a duplicate and unregistered, say) leaves a panel row with a NULL `image_id`; the
UI shows it as missing and the owner removes it, and build refuses a comics with a missing panel (C4).

## Name rules (my call, D5)

The name is trimmed and NFC-normalized before it is stored; 1 to 80 characters after trimming; Cyrillic and other Unicode letters are allowed; it must not contain control
characters or any of `< > : " / \ | ? *`; it must not end in a dot or space and must not be a Windows reserved device name
(`CON`, `PRN`, `AUX`, `NUL`, `COM1` to `COM9`, `LPT1` to `LPT9`, with or without an extension). The check is one pure function in
a shared module, so the API (C3) and the build (C4) use the same rule. The name of a `built` comics is frozen, because its
directory exists; a draft can be renamed.

## Data access

A repository in the global `repository/` layer (async, never commits, returns ORM rows or plain values), covering what the
invariants need and nothing the API layers add on top:

- create a comics (validated name); rename a draft; get one with its panels in order; search by name (case-insensitive substring,
  paged);
- add an image to a draft: refuses when the image already belongs to a comics (the error names that comics so C3's picker can offer
  "move"), when the image is not `active`, or when it is flagged; appends at the next position; stores the snapshot;
- move an image from one draft to another (the "move" the picker offers); remove a panel; reorder (a full ordered list of panel
  ids must match the current set exactly); delete an empty or abandoned draft;
- positions stay contiguous from 0 after every change.

Every function above that changes a comics or its panels raises when the comics is `built`. A built comics cannot be deleted (its
name stays reserved, matching the directory that exists); a draft can.

`mark_built(comics, built_image_id, built_content_hash)` is a repository function that C4 calls inside its own transaction. It
requires the comics to be a draft with at least 2 panels, none of them missing (NULL `image_id`), and an existing `active`
`built_image_id`; it sets `state`, `built_image_id`, `built_content_hash` and `built_at`. It does not touch the panel rows:
they keep their `image_id` until the panels' rows are deleted by the existing flag-move-unregister path, which SET NULL then
turns into the snapshot-only record. C4 decides when to call it.

"Move an image between drafts" is one function in one transaction: both comics must be drafts, the panel row is updated in place
(`comics_id`, new last position; the snapshot is kept), and the source's positions are made contiguous again.

An image that has a live recut proposal, as source or as cut (`image_cuts` states detected, proposed, approved), cannot be added.
The opposite direction (a recut starting on an image that is already a panel) is a guard in recut code and is tracked as D7.

"Searchable" means the comics list's own search by name. Comics names are not indexed into the main smart search.

## Registries

- `docs/image-status.md`: no new status row. A short entry in "Transitions" records that a panel leaves the library through the
  duplicates path (flag, move, unregister) and a row in "Not image statuses" for `comics.state`.
- `docs/image-derived-data.md`: `comics` and `comics_panels` are `relation` tables (bookkeeping, not derived data; copied to nothing).
  `tests/docs/test_image_derived_data_inventory.py` fails otherwise.
- `PopularitySource` gains `comics_build`, with the matching migration of `ck_popularity_absorptions_source`. C1 only adds the
  value, so C4 can record the edges; the "unflag reverses these sources" logic stays limited to its existing three sources, so a
  comics edge is never reversed by an unflag.
- Flag reason `comics_source` for the panels (`image_extras.flagged_reason` is a free `String(50)`); C4 sets it.

## Migrations

One alembic migration creates both tables and replaces `ck_popularity_absorptions_source`. It chains from the head that is current
when the work is started; the branch is rebased or merged onto `main` before the final check and the repository must have a single
head again (parallel branches add migrations too).

## Out of scope

Endpoints and schemas (C3); composing the image, moving files, registering the built image, recording popularity edges, the
derived-data pipeline for the built image (C2, C4, D1); `move_flagged` routing to `comics-sources/` (C4, D6).

## Tests

Integration tests against `ocrdb_test` for each constraint (case-insensitive name uniqueness, one comics per image, deferred
position swap, SET NULL on image delete leaves the snapshot, CASCADE on comics delete), the repository rules above, the name
validator unit tests (no DB), and the existing `tests/docs/` guards. Run the whole `tests/integration/` root, per CLAUDE.md,
because a migration touches a shared check constraint.

## Left to the builder

Table and index names beyond those given, migration file layout, search-by-name implementation (a `LIKE` is enough at this size),
and the exact shape of the repository's error types.
