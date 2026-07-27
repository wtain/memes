# Rename `excluded` → `flagged` Design

Status: done

**Date:** 2026-06-29

---

## Motivation

The `excluded` property on images is misleading. Its name suggests a terminal state ("this image has been excluded"), but its actual role is a **persistent admin selection marker**: an admin picks a subset of images, and a bulk operation is then applied to that set. The current operation is "move out of the active library"; future operations (e.g. "add selected images to a concept") will reuse the same mechanism.

Renaming to `flagged` makes the intent clear: an image is flagged by an admin for a pending bulk action. The flag itself says nothing about which action will be taken.

---

## Semantic change

| Before | After |
|--------|-------|
| `excluded: true` means "this image has been excluded from the library" | `flagged: true` means "an admin has selected this image for a pending bulk operation" |
| Field bakes in the action | Field describes the selection state only |

The schema description changes from:
> "Whether the image has been marked as excluded"

to:
> "Whether the image has been flagged by an admin for a bulk operation"

---

## Rename map

### DB / ORM (`Storage/models.py`, Alembic migration)

| Before | After |
|--------|-------|
| `image_extras.exclude` column (Boolean) | `image_extras.flagged` |
| `ImageExtras.exclude` ORM attribute | `ImageExtras.flagged` |

Migration: single Alembic revision — `ALTER TABLE image_extras RENAME COLUMN exclude TO flagged`. Instant in PostgreSQL (metadata-only). No data loss, no backfill.

### Repository (`repository/image_extras.py`, `Backend/app/repositories/image_repository.py`)

| Before | After |
|--------|-------|
| `ImageExtrasRepository.set_excluded()` | `set_flagged()` |
| `ImageRepository.get_excluded()` | `get_flagged()` |
| `ImageRepository.set_is_excluded()` | `set_flagged()` |
| `ImageRepository.get_is_excluded()` | `get_is_flagged()` |
| All `extras.exclude` column references in queries | `extras.flagged` |

### Service (`Backend/app/services/image_service.py`)

| Before | After |
|--------|-------|
| `mark_excluded()` | `mark_flagged()` |
| `unmark_excluded()` | `unmark_flagged()` |
| `get_excluded()` | `get_flagged()` |
| `get_is_excluded()` | `get_is_flagged()` |

### API routes (`Backend/app/api/images.py`) — breaking change

| Before | After |
|--------|-------|
| `PUT /api/images/meme/{id}/mark_excluded` | `mark_flagged` |
| `PUT /api/images/meme/{id}/unmark_excluded` | `unmark_flagged` |
| `GET /api/images/meme/{id}/get_excluded` | `get_flagged` |
| `GET /api/images/excluded` | `/api/images/flagged` |

All three clients (web, Android, backend) live in the same repo and deploy together. No versioning or deprecation shim is needed — the rename is atomic across the repo.

### Shared JSON schemas (source of truth)

- `shared/schemas/meme.schema.json`: property `excluded` → `flagged`; update `description`
- `shared/schemas/statisticsmemestats.schema.json`: property `excluded` → `flagged`

### Generated types (regenerate from schema — do not hand-edit)

| File | Change |
|------|--------|
| `Backend/app/types/generated/meme.py` | `Schema.excluded` → `Schema.flagged` |
| `Frontend/memes-frontend/src/types/generated/all.d.ts` | same |
| `AndroidClient/app/.../data/model/Models.kt` | `Meme.excluded`, `StatisticsMemeStats.excluded` |

Regenerate with:
```bash
./Frontend/generate-types.sh           # TypeScript
python AndroidClient/scripts/generate_dtos.py  # Kotlin
```

The Python types must also be regenerated via `datamodel-codegen` (same tooling as the last generation).

### Frontend (`Frontend/memes-frontend/src/`)

| Before | After |
|--------|-------|
| Route `/excluded` | `/flagged` |
| `ExploreExcludedPage` (file + component) | `ExploreFlaggedPage` |
| Nav label "Excluded" | "Flagged" |
| `router.tsx` route entry | updated |
| `AppLayout.tsx` nav link | updated |
| `HttpMemesApi.ts` endpoint strings | updated |

### Android (`AndroidClient/app/src/main/java/com/memebrowser/app/`)

| Before | After |
|--------|-------|
| `ui/excluded/ExcludedScreen.kt` | `ui/flagged/FlaggedScreen.kt` |
| `ui/excluded/ExcludedViewModel.kt` | `ui/flagged/FlaggedViewModel.kt` |
| `ui/NavGraph.kt` destination | updated |
| `data/api/MemeApiService.kt` endpoint strings | updated |
| `Text("Excluded")` UI label | `Text("Flagged")` |
| All package/import references | updated |

### Batch scripts (`batch/`)

| Before | After |
|--------|-------|
| `batch/move_excluded.py` | `batch/move_flagged.py` |
| `batch/cleanup_excluded.py` | `batch/cleanup_flagged.py` |
| Internal variable `excluded_path` | `flagged_path` |

> **Note:** The on-disk directory `<BASE_PATH>/excluded/` is **not renamed**. It is a data artifact that already exists in deployed environments. `move_flagged.py` continues to write to `excluded/` until a separate ops migration is scheduled.

### Diagnostics (`Backend/app/repositories/diagnostics_repository.py`, `Backend/app/api/diagnostics.py`)

- `diagnostics_repository.py`: query labels `ImageExtras.exclude == true()` count as `"excluded"` → label becomes `"flagged"`; ORM attribute reference updated
- `diagnostics.py`: response field name follows from repository label — update if exposed in the stats response shape

### Batch scripts — additional file

- `batch/detect_file_duplicates.py`: calls `extras_repo.set_excluded(dup_id, True)` and `metrics.increment("excluded")` → `set_flagged()` / `"flagged"`

### Tools

- `tools/agent_duplicates.py`: uses dict key `"already_excluded"` → `"already_flagged"`

### Backend tests (`Backend/tests/`)

- `test_images_endpoints.py`: test class names (`TestMarkExcluded`, `TestUnmarkExcluded`, `TestGetExcluded`, `TestExcludedFlagHydration`), endpoint strings (`mark_excluded`, `unmark_excluded`, `get_excluded`), field assertions (`data["items"][0]["excluded"]`), `excluded=` kwargs in `Meme()` constructor calls — all updated to `flagged` equivalents
- `test_recommendations_endpoints.py`: any `excluded=` field references updated

### Integration tests (`tests/integration/`)

- `test_rebuild_duplicates.py`: any `excluded`/`exclude` references updated

### Frontend — additional files

- `src/components/MemeCard.tsx`: local state `isExcluded` → `isFlagged`; reads `meme.excluded` → `meme.flagged`
- `src/components/MemeDetails.tsx`: any `excluded` field/endpoint references
- `src/pages/StatisticsPage.tsx`: `excluded` count field in statistics display
- `src/api/http/HttpMemesApi.ts`: endpoint strings for `mark_excluded`, `unmark_excluded`, `get_excluded`, `/excluded`
- `src/test/mockApi.ts`: mock data `excluded` field
- `src/components/MemeCard.test.tsx`, `ConceptDetails.test.tsx`: test field references

### Docs

- `backend_api.md`: all route names, field descriptions, and examples
- `CLAUDE.md`: batch pipeline section entries (`move_excluded` → `move_flagged`, `cleanup_excluded` → `cleanup_flagged`)

---

## Migration strategy

1. **DB migration** — Alembic revision renames `exclude` → `flagged`. Since all clients live in the same repo and deploy together, the DB migration and application code changes land atomically in the same deployment.
2. **Update schemas** — edit the two JSON schema files.
3. **Regenerate types** — run the two generator scripts; commit generated output.
4. **Update all application code** — backend, frontend, Android in a single PR (atomic, since all clients deploy together).
5. **Update docs** — `backend_api.md`, `CLAUDE.md`.

No data migration is required. All existing `true/false` values are preserved by the column rename.

---

## Out of scope

- Renaming the `excluded/` filesystem directory on disk — deferred to a separate ops task per environment
- Changing the bulk operation logic itself — this spec only renames; behavior is unchanged
- Adding new bulk operations (concept creation, etc.) — future work

---

## Files touched (summary)

| Layer | Files |
|-------|-------|
| DB | New Alembic revision file |
| ORM | `Storage/models.py` |
| Repository | `repository/image_extras.py`, `Backend/app/repositories/image_repository.py`, `Backend/app/repositories/diagnostics_repository.py` |
| Service | `Backend/app/services/image_service.py` |
| Router | `Backend/app/api/images.py`, `Backend/app/api/diagnostics.py` |
| Schemas | `shared/schemas/meme.schema.json`, `shared/schemas/statisticsmemestats.schema.json` |
| Generated (Python) | `Backend/app/types/generated/meme.py` |
| Generated (TS) | `Frontend/memes-frontend/src/types/generated/all.d.ts` |
| Generated (Kotlin) | `AndroidClient/app/src/main/java/com/memebrowser/app/data/model/Models.kt` |
| Frontend | `src/pages/ExploreExcludedPage.tsx` → `ExploreFlaggedPage.tsx`, `src/app/router.tsx`, `src/app/AppLayout.tsx`, `src/api/http/HttpMemesApi.ts`, `src/components/MemeCard.tsx`, `src/components/MemeDetails.tsx`, `src/pages/StatisticsPage.tsx`, `src/test/mockApi.ts`, `src/components/MemeCard.test.tsx`, `src/components/ConceptDetails.test.tsx` |
| Android | `ui/excluded/` → `ui/flagged/` (2 files), `ui/NavGraph.kt`, `data/api/MemeApiService.kt`, `StatisticsScreen.kt`, `SearchScreen.kt`, `MemeDetailScreen.kt`, `MemeDetailViewModel.kt`, test files |
| Batch | `batch/move_excluded.py` → `batch/move_flagged.py`, `batch/cleanup_excluded.py` → `batch/cleanup_flagged.py`, `batch/detect_file_duplicates.py` |
| Tools | `tools/agent_duplicates.py` |
| Tests | `Backend/tests/test_images_endpoints.py`, `Backend/tests/test_recommendations_endpoints.py`, `tests/integration/test_rebuild_duplicates.py` |
| Docs | `backend_api.md`, `CLAUDE.md` |