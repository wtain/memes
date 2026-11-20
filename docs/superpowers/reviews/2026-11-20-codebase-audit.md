# Codebase audit, 2026-11-20

Six read-only subagents each audited one dimension: security, algorithms, synchronisation, duplication, code smells, maintainability. None touched `.env` files or any database.

**Caveats**
- Findings come from reading code and targeted greps. Nothing was executed, and no EXPLAIN or timing numbers back the performance findings.
- Coverage was partial in places (the security agent did not read every router; the duplication agent ran no mechanical similarity scan). Treat the list as the high-value subset, not an exhaustive one.
- I (the coordinator) have not independently re-verified each finding. Re-check a finding before acting on it, especially the Critical/High ones.
- Severity is the agents' own judgement. Effort is a t-shirt size: XS under 1h, S about half a day, M 1-3 days, L about a week, XL more.
- No Critical findings were reported.

## Top priorities

| # | Finding | Why |
|---|---|---|
| 1 | SYN-1, SYN-2, SYN-7, SYN-8 | File-vs-DB ordering can leave active rows with missing files, and `unregister_deleted_images` then cascade-deletes them. SYN-2 is an XS fix. |
| 2 | SYN-3 | A failed admin-triggered batch can block that job kind permanently (409). |
| 3 | ALG-4, ALG-14, ALG-15 | IN lists can exceed asyncpg's 32,767-parameter cap at the corpus sizes the repo quotes. This is a failure risk, not only a slowdown. |
| 4 | ALG-7 | The tags table has no usable index for smart search. |
| 5 | MNT-1, MNT-2, MNT-3 | Most unit-test roots never run in CI, and the path filters skip code the backend imports. |
| 6 | SEC-1, SEC-2 | No auth on dev services bound to 0.0.0.0, and no rate limit on the LLM-backed public search. |
| 7 | DUP-1 | Hand-written router models duplicate the generated ones. CLAUDE.md already records a silent field drop caused by this. |
| 8 | ALG-1, ALG-2, ALG-16, ALG-9 | Brute-force similarity queries, unbatched embedding inference, no thumbnails. |

## Overlaps between agents

- SEC-4 = SYN-19 (rate limiter buckets never evicted).
- SMELL-1 ≈ SYN-10 (repositories commit, and savers commit on exception).
- SMELL-20 relates to SMELL-1 (two repository roots).
- DUP-20 relates to SYN-10 (the Saver commit-on-error pattern).
- DUP-1 and DUP-2 relate to SMELL-2.
- MNT-8 relates to the `≈` gotcha in CLAUDE.md.

## Findings

### Security (SEC)

| ID | Finding | Sev | Effort | Location |
|---|---|---|---|---|
| SEC-1 | No authentication on dev stage; servers bind 0.0.0.0 and expose batch triggers, uploads, writes | High | M | Backend/app/stage_policy.py, run commands |
| SEC-2 | No rate limit on the LLM-backed `POST /api/meme-from-text/search` (public on prod) | Medium | S | api/meme_from_text.py, stage_policy.py:115 |
| SEC-3 | No rate limit on expensive search/similar endpoints | Low | S | api/images.py:144-175 |
| SEC-4 | Upload limiter is in-memory, per worker, with unbounded buckets | Low | S | services/rate_limit.py:6-26 |
| SEC-5 | Upload validation: confirm stored name never uses client name; set Pillow pixel limit | Low | XS | api/uploads.py:117-163 |
| SEC-6 | `Access-Control-Allow-Origin: *` on image responses, including prod | Low | XS | api/images.py:~504 |
| SEC-7 | Dev CORS with credentials + no auth enables browser-driven CSRF on localhost APIs | Low | XS | main.py:222-231 |
| SEC-8 | Android allows cleartext traffic app-wide | Low | S | AndroidManifest.xml:18 |
| SEC-12 | Cursor decoding: confirm every malformed payload gives 422, not 500 | Low | XS | retrieval/cursor.py:17-30 |
| SEC-13 | Prod Postgres published on host loopback (acceptable; keep guard test) | Low | XS | deploy/prod/compose.yaml:~48 |
| SEC-14 | `--forwarded-allow-ips` trusts the whole /24, so any container on the edge net can spoof client IP | Low | XS | deploy/prod/compose.yaml |
| SEC-16 | Caddy access log keeps the path, so image ids appear in logs (conflicts with CLAUDE.md privacy rule) | Low | S | deploy/prod/edge/Caddyfile:11-90 |
| SEC-17 | No CSP or Permissions-Policy; HSTS lacks includeSubDomains | Low | XS | deploy/prod/edge/Caddyfile |
| SEC-18 | `target="_blank"` links lack `rel="noopener noreferrer"` | Low | XS | App.tsx:11,14; MemeActions.tsx:105 |
| SEC-19 | Run `pip-audit` / `pnpm audit` in CI; pins not checked against advisories | Low | S | Backend/requirements-backend.txt |
| SEC-20 | Review `memesync-gate.py` parsing of `SSH_ORIGINAL_COMMAND` in depth (prod remote-exec surface) | Low | M | deploy/prod/memesync-gate.py |

No injection, traversal, command injection or unsafe deserialization was found (SEC-9, 10, 11). Positive controls noted: SEC-15 (pinned digest, non-root, hardened compose), SEC-18 (no `dangerouslySetInnerHTML`), SEC-20 (restricted ssh keys), SEC-21 (log leakage controls).

### Algorithms and performance (ALG)

| ID | Finding | Sev | Effort | Location |
|---|---|---|---|---|
| ALG-1 | `description_all` similarity is a brute-force cross join, no HNSW | High | M | repositories/image_repository.py:350 |
| ALG-2 | Per-description similarity scans all same-prompt descriptions, no ANN | High | M | image_repository.py:292 |
| ALG-3 | Ingestion `list_clusters` recomputes the whole tier per page; O(blobs x E) edge bucketing | High | M | services/ingestion_service.py:167-262 |
| ALG-4 | Text search passes its match set as an IN list (bind-limit risk), reused 3 times | High | M | image_repository.py:93,126-140 |
| ALG-5 | Facet counts recomputed on every page including cursor pages | Medium | S | image_repository.py:129-139 |
| ALG-6 | Ranked search loads, scores and sorts all matches in Python per page | Medium | M | image_repository.py:175-212 |
| ALG-7 | Tag lookups (`upper(value)`, `%`) have no usable index | High | S | repository/ocr_lemmas.py:78,110-120; Storage/models.py:612 |
| ALG-8 | Smart search runs many sequential queries per token | Medium | M | repository/ocr_lemmas.py:50-60,250-294 |
| ALG-9 | No thumbnails; grid loads full-size originals | High | L | MemeCard.tsx; api/images.py:482-512 |
| ALG-10 | Every image request opens a DB transaction and does blocking FS calls on the event loop | Medium | S | api/images.py:482-510; services/image_store.py:30-75 |
| ALG-11 | Per-row awaits (N+1) in business-metrics recording | Medium | S | services/library_metrics.py, ingestion_metrics.py, business_metrics_service.py |
| ALG-12 | `delete_decisions` issues one DELETE per pair | Low | XS | repositories/duplicate_decisions_repository.py:30-37 |
| ALG-13 | `images.ingestion_batch_id` has no index | Medium | XS | Storage/models.py:68 |
| ALG-14 | Ingestion OCR/text-heavy lookups use IN lists that can pass the bind limit | Medium | XS | repositories/ingestion_repository.py:88-118 |
| ALG-15 | `get_text_heavy_images_needing_embedding` builds an unbounded IN list; uses NOT IN | Medium | XS | repository/ocr_text_embeddings.py:25-47 |
| ALG-16 | Batch embedding jobs run inference one item at a time | High | M | batch/build_image_embeddings.py:83-92; build_ocr_text_embeddings.py:44-52; ai/clip.py, ai/sbert.py |
| ALG-17 | `select_images_to_embed` uses NOT IN (subquery) and loads all rows | Low | XS | batch/build_image_embeddings.py:33-37 |
| ALG-18 | Clusterize writes one ORM object per member and rebuilds from scratch | Low | S | batch/clusterize.py:75-140 |
| ALG-19 | UnionFind merges member sets without comparing sizes; recursive find | Low | XS | graph/uf.py:54 |
| ALG-20 | `tmp_clusters` keyset paging has no composite index | Low | XS | image_repository.py:786-840 |
| ALG-21 | Recommendations hash and sort the whole images table per page; includes non-active rows | Medium | S | repositories/recommendations_repository.py:25-46 |
| ALG-22 | Trending and flagged lists lack supporting indexes | Low | XS | Storage/models.py:59-100 |
| ALG-23 | Dead O(n²) cross-join duplicate query | Low | XS | image_repository.py:636-700 |
| ALG-24 | Tag saving builds one ORM object per tag; single commit for the whole corpus | Low | S | repository/tags.py:55-72 |
| ALG-25 | No `React.memo` in the grid; modal open re-renders every row | Low | S | MemesList.tsx:127-136; MemeCard.tsx |

### Synchronisation and concurrency (SYN)

| ID | Finding | Sev | Effort | Location |
|---|---|---|---|---|
| SYN-1 | `unregister_deleted_images` deletes (cascading) any row whose file looks missing; no mount or fraction guard | High | S | batch/tasks/SourceTasks.py:13-29; batch/unregister_deleted_images.py |
| SYN-2 | `promote_images` has no status guard; races with review reject | High | XS | repositories/ingestion_repository.py:285-293 |
| SYN-3 | Admin-triggered runs can stay `started` forever, giving a permanent 409 | High | S | services/admin_batch_service.py:31-48; batch_subprocess.py:130-153 |
| SYN-4 | Scheduler orphan recovery force-fails a running job, then starts a second copy | Medium | M | scheduler.py:63-85 |
| SYN-5 | `get_async_db` commits after the response is sent | Medium | M | Storage/db.py:43-53 |
| SYN-6 | `useWindowedPagination` applies stale responses after a reset | Medium | S | hooks/useWindowedPagination.ts:79-207 |
| SYN-7 | `fix_image_formats` renames the file before the DB row is updated | Medium | M | batch/utils/image_format_fix.py:99-141 |
| SYN-8 | `move_flagged` moves from a snapshot with no row lock; DB lags | Medium | M | batch/move_flagged.py:30-61 |
| SYN-9 | Note edit and lemma rebuild do delete-then-insert without ON CONFLICT | Medium | S | repository/description_note_lemmas.py:68-85 |
| SYN-10 | Full rebuilds delete and commit first; savers commit on exception | Medium | M | repository/ocr_lemmas.py:318; repository/tags.py:15-66 |
| SYN-11 | Reject/undo-reject lack locks and do blocking file moves in async handlers | Medium | S | repositories/ingestion_repository.py:295-367; services/image_store.py:109-123 |
| SYN-12 | `move_without_overwrite` checks existence, then moves (TOCTOU) | Low | XS | batch/utils/safe_move.py:23-40 |
| SYN-13 | Meme-from-text pipeline: no single-flight; cache and breaker are per process | Low | M | retrieval/pipeline.py:118-243 |
| SYN-14 | Upload duplicate check has a window against `ingest_hash_dedup` | Low | M | services/inbox_upload_service.py:41-45,376-391 |
| SYN-15 | Recut approve's pre-cuts copy runs after commit and races `move_flagged` | Low | S | services/recut_decision_service.py:53-55 |
| SYN-16 | Sync promote's running-batches check is a point-in-time read, not a lock | Low | M | sync/promote/preconditions.py:154-165 |
| SYN-17 | `warm_task.cancel()` is never awaited | Low | XS | main.py:170-172 |
| SYN-18 | Admin page and recut `loadMore` responses can land out of order | Low | XS | AdminBatchesPage.tsx; recut/useRecutQueue.ts |
| SYN-19 | In-memory limiter buckets never evicted (same as SEC-4) | Low | XS | services/rate_limit.py:15-24 |
| SYN-20 | Live statistics requests stampede the heavy query | Low | S | api/diagnostics.py:146-170 |

### Duplication (DUP)

| ID | Finding | Sev | Effort | Location |
|---|---|---|---|---|
| DUP-1 | Hand-written router models duplicate generated Pydantic models | High | M | api/ingestion.py, admin.py, history.py, uploads.py, images.py |
| DUP-2 | Two different classes named `RunStatusResponse` | Medium | S | api/admin.py:23; api/ingestion.py:18 |
| DUP-3 | Base64-JSON pagination cursor re-implemented 5 times | Medium | S | api/history.py, image_service.py, recommendations_service.py, ingestion_service.py, retrieval/cursor.py |
| DUP-4 | argparse and `--env` boilerplate in about 50 batch scripts; `--status` choice lists differ | Medium | M | batch/*.py |
| DUP-5 | `finish_existing_run` / `tracked_run` branching copied into about 38 batch mains | High | M | batch/build_ocr_lemmas.py:132 and others |
| DUP-6 | Streaming sha256 of a file implemented 6 times | Medium | S | batch/utils/file_hash.py, sync/*, evaluation/gold.py |
| DUP-7 | Chunk helper and chunk-size constants repeated | Low | XS | sync/promote/apply.py, verify.py, sync/pull/apply.py, repository/* |
| DUP-8 | Read-only DB guard repeated across tools; evaluation's is a weaker substring test | Medium | S | tools/*, evaluation/__main__.py:61, sync/connect.py |
| DUP-9 | Image extension/format allow-lists defined in 4 places | Medium | S | api/uploads.py, inbox_upload_service.py, batch/utils/image_format_*.py |
| DUP-10 | Duplicate-probe SQL fragments split between rebuild_duplicates and ingest_find_duplicates | Medium | M | batch/rebuild_duplicates.py:69-125; batch/ingest_find_duplicates.py:59-123 |
| DUP-11 | Duplicate-threshold constants spread over three modules and settings | Medium | S | batch/clusterize.py, rebuild_duplicates.py |
| DUP-12 | Four sha256 plan-hash constructions in sync | Low | S | sync/files/manifest.py, pull/plan.py, promote/digest.py, report.py |
| DUP-13 | Content hashing for in-memory bytes not shared with `sha256_file` (dedup must agree) | Low | XS | services/inbox_upload_service.py:381 |
| DUP-14 | Urllib Ollama JSON-POST client duplicated in parse/ and retrieval/ | Medium | S | parse/client.py:48; retrieval/embed.py:65 |
| DUP-15 | Legacy and current lemmatisation logic split | Low | M | rules/engine.py, rules/normalize.py |
| DUP-16 | Commit-every-N loops and constants re-implemented per batch script | Low | XS | batch/classify_text_heavy.py:19 and others |
| DUP-17 | Run-status response built by hand in two routers | Low | S | api/admin.py:23-37; api/ingestion.py:18-27 |
| DUP-18 | Frontend format helpers defined locally in pages | Low | XS | UploadPage.tsx:9; AdminBatchesPage.tsx:33 |
| DUP-19 | Several frontend fetches bypass `HttpMemesApi` and its error mapping | Low | M | telemetry/pageview.ts, features/capabilities.ts and others |
| DUP-20 | Per-script `Saver` pattern with unconditional commit in `__aexit__` | Low | L | batch/build_ocr_lemmas.py and others |

### Code smells (SMELL)

| ID | Finding | Sev | Effort | Location |
|---|---|---|---|---|
| SMELL-1 | About 11 `session.commit()` calls inside repositories despite the "never commit" rule | High | M | repository/concepts.py:85, tags.py, ocr_lemmas.py:377 and others |
| SMELL-2 | Router builds repositories and uses `AsyncSessionLocal` as a type hint | Medium | M | api/images.py:125-129,484 |
| SMELL-3 | God modules `image_service.py` (833 lines) and `image_repository.py` (900 lines) | Medium | L | Backend/app/services, repositories |
| SMELL-4 | Giant functions (100-250 lines) | Medium | M | sync/roles.py:224, build_meme_profiles.py:72, diagnostics_repository.py:29 and others |
| SMELL-5 | Parameter lists of 12-16 | Medium | M | sync/pull/cli.py:365, sync/verify/cli.py:252, sync/run/cli.py:508 and others |
| SMELL-6 | Silent `except Exception: pass` in routers and observability | Medium | S | api/images.py:120,479,508; observability/metrics.py |
| SMELL-7 | Bare `print()` in library code (26 occurrences) | Low | XS | repository/concepts.py:83 and others |
| SMELL-8 | Commented-out code and misspelled file `image_procesing_status.py` | Low | XS | repository/image_procesing_status.py:20-29 |
| SMELL-9 | Deprecated naive `datetime.utcnow()` | Low | S | repository/image_procesing_status.py:23 and others |
| SMELL-10 | Dead experimental scripts in `batch/experimental/`, with a bare `except:` and a committed `.pt` file | Medium | S | batch/experimental/* |
| SMELL-11 | Long `main()` mixing parsing, orchestration and logic | Low | M | batch/detect_file_duplicates.py:43, tools/agent_duplicates.py:62, evaluation/__main__.py |
| SMELL-12 | CLI layers hold orchestration logic (500-800-line files in sync/) | Medium | M | sync/report.py, promote/apply.py, run/cli.py |
| SMELL-13 | `memesync-gate.py` is a 1,239-line single script (security-relevant) | Medium | M | deploy/prod/memesync-gate.py |
| SMELL-14 | Oversized frontend API class and pages | Medium | M | HttpMemesApi.ts (930 lines), IngestionReviewPage.tsx |
| SMELL-15 | Android `Models.kt` hand-maintained beside generated DTOs; large screens | Low | M | AndroidClient/.../Models.kt |
| SMELL-16 | `Storage/models.py` is a 1,162-line single file | Low | L | Storage/models.py |
| SMELL-17 | Test files over 500 lines | Low | M | Frontend/.../*.test.tsx |
| SMELL-18 | Timing-based sleeps in subprocess tests (flaky under load) | Medium | S | Backend/tests/test_batch_subprocess.py |
| SMELL-19 | Business/presentation logic in repository layer | Low | M | ingestion_repository.py:134, repository/discovery_report.py |
| SMELL-20 | `repository/` and `Backend/app/repositories/` overlap in responsibility | Medium | L | both directories |

### Maintainability (MNT)

| ID | Finding | Sev | Effort | Location |
|---|---|---|---|---|
| MNT-1 | Most unit-test roots never run in CI (rules, parse, retrieval, taxonomy, evaluation, comics, ai, batch/tests) | High | S | .github/workflows/*.yml |
| MNT-2 | `backend-tests.yml` path filters omit config, parse, retrieval, taxonomy, shared, Dockerfile | High | XS | backend-tests.yml:4-30 |
| MNT-3 | `integration-tests.yml` path filter is a narrow whitelist | Medium | XS | integration-tests.yml:6-35 |
| MNT-4 | `batch/requirements.txt` is a stale UTF-16 duplicate | High | XS | batch/requirements.txt |
| MNT-5 | Root `requirements.txt` is a raw pip freeze with conflicting OpenCV packages | High | M | requirements.txt:82-84; Makefile |
| MNT-6 | paddleocr/numpy gotcha in CLAUDE.md looks stale; paddle residue (`imgaug`, OpenCV ceilings) remains | Medium | S | CLAUDE.md; requirements.txt |
| MNT-7 | Two `pytest.ini` roots with conflicting asyncio modes; no single test entry point | Medium | M | pytest.ini, Backend/pytest.ini |
| MNT-8 | `ProgressTracker` "≈" crash on Windows still present | Medium | XS | batch/utils/progress.py:59 |
| MNT-9 | CLAUDE.md is 1,025 lines and duplicates `docs/batches/inventory.md` | Medium | M | CLAUDE.md |
| MNT-10 | Batch inventory/registry hand-maintained in 3 places with no guard test | Medium | M | CLAUDE.md, docs/batches/inventory.md, environments/batch_registry.yaml |
| MNT-11 | Many migrations have no-op `downgrade()`; baseline irreversible | Low | M | Storage/alembic/versions |
| MNT-12 | No CI check for a single alembic head or for migration/model drift | Medium | S | integration-tests.yml |
| MNT-13 | Large untracked `.venv311.broken-backup-2026-09-26/` in repo root | Medium | XS | repo root |
| MNT-14 | Merged agent worktrees remain under `.claude/worktrees/` | Low | XS | .claude/worktrees/* |
| MNT-15 | Nine settings YAMLs with ad hoc key presence per environment; no schema | Medium | M | environments/settings*.yaml |
| MNT-16 | Frontend deps: loose ranges, likely dead Tailwind v3-era config, vitest/Vite mismatch | Low | XS | package.json |
| MNT-17 | Makefile minimal and PowerShell-only | Low | S | Makefile |
| MNT-18 | Overlapping onboarding docs; SETUP.md contradicts CLAUDE.md; ARCHITECTURE.md outdated (1536-dim) | Medium | M | SETUP.md, ARCHITECTURE.md |
| MNT-19 | Python 3.11 target vs two venvs and a machine-specific junction venv; no CI fresh-install check | Low | S | CLAUDE.md |
| MNT-20 | Relative `data/...` config path bug still latent in other scripts | Medium | S | CLAUDE.md gotcha 1 |
| MNT-21 | `backend_api.md` (1,990 lines) manually maintained; no generated check against OpenAPI | Low | S | backend_api.md |
| MNT-22 | Generated-code freshness checked in CI only for the frontend | Medium | S | .github/workflows/frontend-ci.yml |
| MNT-23 | Dependabot resolves the ML stack as one graph; no `pip check` in CI | Low | XS | .github/dependabot.yml |
| MNT-24 | `torch` CPU/CUDA pins bumped in lockstep by hand | Low | XS | requirements.txt:152; requirements-cuda.txt |
| MNT-25 | Windows-only operator commands (no cross-platform wrappers) | Low | S | CLAUDE.md, Makefile |

## Totals

| Dimension | Findings | High | Medium | Low |
|---|---|---|---|---|
| Security | 16 | 1 | 1 | 14 |
| Algorithms | 25 | 7 | 8 | 10 |
| Synchronisation | 20 | 3 | 8 | 9 |
| Duplication | 20 | 2 | 8 | 10 |
| Code smells | 20 | 1 | 10 | 9 |
| Maintainability | 25 | 4 | 11 | 10 |
| **Total** | **126** | **18** | **46** | **62** |

## Not covered or not verified

- Android sync engine internals beyond the coordinator.
- Routers the security and concurrency agents did not open.
- Whether the `.venv311.broken-backup` size or the `.claude/worktrees` copies affect tooling (not measured).
- The `memesync-gate.py` parsing logic (flagged for a dedicated review, SEC-20).
