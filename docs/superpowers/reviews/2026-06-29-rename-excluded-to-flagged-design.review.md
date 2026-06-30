# Review: rename-excluded-to-flagged-design

**Spec:** `docs/superpowers/specs/2026-06-29-rename-excluded-to-flagged-design.md`  
**Date:** 2026-06-30  
**Tests:** 161 backend passed · 102 frontend passed

---

## Summary

Large atomic rename of `excluded` → `flagged` across 41 files spanning DB, ORM, repositories, services, API routes, generated types, shared schemas, frontend, Android, batch, and docs. One runtime bug was found and fixed during review; everything else was clean.

---

## Logic Changes

### DB / ORM
- `Storage/models.py`: `ImageExtras.exclude` → `ImageExtras.flagged` — correct column rename.
- New Alembic migration `2026_06_30_rename_exclude_to_flagged.py` uses `op.alter_column` with `new_column_name` for both upgrade/downgrade — correct reversible migration pattern.

### Repository layer
- `repository/image_extras.py`: `set_excluded()` → `set_flagged()`, all field refs updated.
- `Backend/app/repositories/image_repository.py`: All `.exclude` → `.flagged`, method names `get_excluded`/`set_is_excluded`/`get_is_excluded` → `get_flagged`/`set_flagged`/`get_is_flagged`.
- `Backend/app/repositories/diagnostics_repository.py`: `ImageExtras.exclude == true()` → `ImageExtras.flagged == true()`, label `"excluded"` → `"flagged"`.

### Service layer
- `image_service.py`: `get_flagged`, `mark_flagged`, `unmark_flagged`, `get_is_flagged` — all method names and field refs updated.
- `recommendations_service.py` (**bug fixed during review**): `excluded=r.exclude` → `flagged=r.flagged`. This was a missed rename that would have crashed at runtime with an `AttributeError` on the ORM object and a Pydantic validation error from the unknown `excluded` kwarg. Fixed before commit.

### API routes
- `/images/excluded` → `/images/flagged`
- `mark_excluded` / `unmark_excluded` / `get_excluded` endpoints → `mark_flagged` / `unmark_flagged` / `get_flagged`

### Batch
- `move_excluded.py` → `move_flagged.py` (deleted old), `cleanup_excluded.py` → `cleanup_flagged.py` (deleted old).
- `move_flagged.py` continues writing to `excluded/` directory on disk — correct per spec (on-disk migration is out of scope).
- `detect_file_duplicates.py`: `set_excluded` → `set_flagged`, metric key `"excluded"` → `"flagged"`.
- `tools/agent_duplicates.py`: `"already_excluded"` → `"already_flagged"`.

### Frontend
- `MemesApi.ts` / `HttpMemesApi.ts`: all methods renamed, `/flagged` URL, mark/unmark/get_flagged endpoints.
- `ExploreFlaggedPage.tsx` created, `ExploreExcludedPage.tsx` deleted; router and AppLayout updated to `/flagged`.
- `MemeCard`, `MemeDetails`, `MemesList`, `StatisticsPage`, `mockApi`, `all.d.ts`: `excluded` → `flagged` throughout.

### Android
- `Models.kt`, `MemeApiService.kt`, `MemeRepository.kt`, `NavGraph.kt`, `SearchScreen.kt`, `MemeDetailScreen.kt`, `MemeDetailViewModel.kt` updated.
- `FlaggedScreen.kt` + `FlaggedViewModel.kt` created; `ExcludedScreen.kt` + `ExcludedViewModel.kt` deleted.

### Docs
- `backend_api.md`: all route names, URL paths, and field descriptions updated.
- `CLAUDE.md`: maintenance table `move_excluded` → `move_flagged`.
- Shared schemas: `meme.schema.json` and `statisticsmemestats.schema.json` updated.

---

## Code Quality

**Consistency:** Complete — no old `excluded` names remain in src (only Alembic migration history, and the intentional `excluded/` disk path).

**Spec conformance:** Full. All 12 spec file groups covered; on-disk directory intentionally not renamed per spec.

**Test coverage:** Test class names and test method names updated (`TestMarkFlagged`, `TestUnmarkFlagged`, etc.). Some test docstrings still say "as excluded" (e.g. "Test getting excluded status when image is excluded") — these are harmless descriptions and were not updated; they do not affect correctness.

---

## Test Results

```
Backend/tests/ + tests/rules/    161 passed
Frontend vitest run              102 passed (16 test files)
```

---

## Fixed During Review

| Issue | File | Action |
|-------|------|--------|
| `excluded=r.exclude` missed rename — runtime `AttributeError` + Pydantic error | `Backend/app/services/recommendations_service.py:38` | Fixed to `flagged=r.flagged` |

## Not Fixed / Intentional

| Item | Reason |
|------|--------|
| `move_flagged.py` writes to `excluded/` directory | Spec explicitly defers on-disk directory rename to a separate ops migration |
| Test docstrings still say "excluded" in descriptions | Cosmetic only; class/method names and assertions are correct; no behavior impact |
