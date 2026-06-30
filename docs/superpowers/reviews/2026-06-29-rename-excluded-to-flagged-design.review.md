# Review: rename-excluded-to-flagged-design

**Spec:** `docs/superpowers/specs/2026-06-29-rename-excluded-to-flagged-design.md`  
**Date:** 2026-06-30  
**Tests:** 161 backend passed · 102 frontend passed (16 test files)

---

## Summary

Large atomic rename of `excluded` → `flagged` across 41+ files spanning DB, ORM, repositories, services, API routes, generated types, shared schemas, frontend, Android, batch, and docs. Three issues were found and fixed during review; everything else was clean.

---

## Logic Changes

### DB / ORM
- `Storage/models.py`: `ImageExtras.exclude` → `ImageExtras.flagged` — correct column rename.
- New Alembic migration `2026_06_30_rename_exclude_to_flagged.py` uses `op.alter_column` with `new_column_name` for both upgrade/downgrade — correct reversible migration pattern.

### Repository layer
- `repository/image_extras.py`: `set_excluded()` → `set_flagged()`, all field refs updated.
- `Backend/app/repositories/image_repository.py`: All `.exclude` → `.flagged`, method names `get_excluded`/`set_is_excluded`/`get_is_excluded` → `get_flagged`/`set_flagged`/`get_is_flagged`. Stale inline comment updated.
- `Backend/app/repositories/diagnostics_repository.py`: `ImageExtras.exclude == true()` → `ImageExtras.flagged == true()`, label `"excluded"` → `"flagged"`.

### Service layer
- `image_service.py`: `get_flagged`, `mark_flagged`, `unmark_flagged`, `get_is_flagged` — all method names and field refs updated.
- `recommendations_service.py` (**runtime bug fixed during review**): `excluded=r.exclude` → `flagged=r.flagged`. This was a missed rename that would have crashed with `AttributeError` on the ORM object and a Pydantic validation error from the unknown `excluded` kwarg.

### API routes
- `/images/excluded` → `/images/flagged`
- `mark_excluded` / `unmark_excluded` / `get_excluded` → `mark_flagged` / `unmark_flagged` / `get_flagged`

### Batch
- `move_excluded.py` → `move_flagged.py` (old file deleted), `cleanup_excluded.py` → `cleanup_flagged.py` (old file deleted).
- `move_flagged.py` continues writing to `excluded/` directory on disk — correct per spec (on-disk migration is out of scope).
- `detect_file_duplicates.py`: `set_excluded` → `set_flagged`, metric key `"excluded"` → `"flagged"`.
- `tools/agent_duplicates.py`: `"already_excluded"` → `"already_flagged"`.

### Frontend
- `MemesApi.ts` / `HttpMemesApi.ts`: all methods renamed, `/flagged` URL, mark/unmark/get_flagged endpoints.
- `ExploreFlaggedPage.tsx` created, `ExploreExcludedPage.tsx` deleted; router and AppLayout updated to `/flagged`.
- `MemeCard`, `MemeDetails`, `MemesList`, `StatisticsPage`, `mockApi`, `all.d.ts`: `excluded` → `flagged` throughout.

### Android
- `Models.kt`, `MemeApiService.kt`, `MemeRepository.kt`, `NavGraph.kt`, `SearchScreen.kt`, `MemeDetailScreen.kt`, `MemeDetailViewModel.kt`, `StatisticsScreen.kt` updated.
- `FlaggedScreen.kt` + `FlaggedViewModel.kt` created; `ui/excluded/` folder deleted.
- Unit tests (`MemeDetailViewModelTest.kt`, `Fakes.kt`) and instrumented tests (`SearchScreenTest.kt`, `MemeDetailScreenTest.kt`, `AndroidFakes.kt`) updated.

### Docs
- `backend_api.md`: all route names, URL paths, and field descriptions updated.
- `CLAUDE.md`: maintenance table `move_excluded` → `move_flagged`.
- `Backend/Readme.md` (**missed by agents, fixed during review**): updated route table, endpoint names, and `MemeSearchResponse` example JSON.
- Shared schemas: `meme.schema.json` and `statisticsmemestats.schema.json` updated.

---

## Code Quality

**Consistency:** Complete — no functional `excluded` names remain in src. Remaining occurrences:
- `originalFileName="excluded.jpg"` in test fixture — intentional filename string
- `flagged_path = os.path.join(base_path, "excluded")` in `move_flagged.py` — intentional on-disk dir name per spec
- `intentionally excluded from the standard pytest run` in `test_rebuild_duplicates.py` header — English word, not feature reference

**Spec conformance:** Full. All 12 spec file groups covered; on-disk directory intentionally not renamed per spec.

**Test coverage:** Test class names, test method names, and test docstrings all updated to say "flagged". Assertions updated throughout.

---

## Test Results

```
Backend/tests/ + tests/rules/    161 passed
Frontend vitest run              102 passed (16 test files)
TypeScript                       0 errors
```

---

## Fixed During Review

| Issue | File | Action |
|-------|------|--------|
| `excluded=r.exclude` missed rename — runtime `AttributeError` + Pydantic error | `Backend/app/services/recommendations_service.py:38` | Fixed to `flagged=r.flagged` |
| `Backend/Readme.md` fully missed by all agents | `Backend/Readme.md` | Updated route table, endpoint rows, and JSON example |
| `ConceptDetails.test.tsx` mock override used stale `getImageIsExcluded` key | `Frontend/…/ConceptDetails.test.tsx:39` | Fixed to `getImageIsFlagged` (caught by `tsc -b`) |
| Stale docstrings in test methods — said "excluded" while method names said "flagged" | `Backend/tests/test_images_endpoints.py` (20 occurrences) | Fixed all docstrings to say "flagged" |

## Not Fixed / Intentional

| Item | Reason |
|------|--------|
| `move_flagged.py` writes to `excluded/` directory | Spec explicitly defers on-disk directory rename to a separate ops migration |
