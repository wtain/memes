# B1 inbox upload endpoint: backend review (correctness and maintainability)

Reviewer: fresh independent backend reviewer. Scope: commits b8e68999, e84391df, 4424172c, spec 52aa7023 (Android A1 commits ignored).
Tests run: `tests/test_inbox_upload_service.py` + `tests/test_uploads_endpoints.py`, 49 passed. No database, no ports, no real inbox touched.

## Verdict

**Ship after fixes.** The design is sound and the implementation matches the approved spec almost line for line. Nothing is
Critical. Three Important items should be settled before A3 builds on the response: no per-file index in the response (A3 cannot
reliably map an entry back to the file it sent), silent weakening of the "never inflate popularity" guarantee for a large inbox
(the 5000-entry scan cap, hit exactly by the first bulk phone sync), and a library-duplicate check that misses WebP uploads whose
`content_hash` was rewritten after format conversion. Also no log line for 500/503 inbox failures.

## Requirement conformance

| # | Requirement (README / B1 spec) | Code | Proven by tests | Verdict |
|---|---|---|---|---|
| 1 | Uploads go to `PATH_INGESTION_SOURCE`, read lazily, no `INCOMING_PATH` fallback | `_prepare_inbox` reads `settings.get` per request; `image_store` cleaned | `test_does_not_fall_back_to_incoming`, unset/missing 503 tests | Met |
| 2 | Original name kept, sanitised (traversal, reserved, dotfile, length, Unicode) | `sanitize_filename` | 15 parametrised cases + Cyrillic + cap + empty stem | Met |
| 3 | Collision with different content gets numeric suffix | `_free_name` | `test_name_collision...` | Met |
| 4 | Atomic write via `.uploading/` + `os.replace`, invisible to `ingest_hash_dedup` | `_write_all` | `test_file_is_staged_outside...` runs the real `hash_incoming_files` | Met |
| 5 | Duplicate = any-status `images.content_hash` or inbox file; writes nothing | `existing_content_hashes` (no status filter, NULL never matches), `_find_in_inbox` | fake-repo tests + integration test with all 4 statuses and NULL | Met, with caveats F2, F3 |
| 6 | Same-request duplicate | inbox scan sees the first file | `test_two_identical_files_in_one_request` | Met |
| 7 | Hash equals `images.content_hash` | lowercase hexdigest of bytes = `sha256_file` | `test_hash_matches_the_batch_algorithm` only compares `hashlib` with `sha256_file`, it does not exercise the service (T1) | Met in fact, weakly proven |
| 8 | Backward-compatible response; `status` accepted or duplicate | `UploadResponse` unchanged | status asserts | Met; web page and Android screen only read `total_accepted`, `total_failed`, `original_filename`, `size_bytes`, `reason`; none reads `saved_as` or `"ok"` |
| 9 | 503 for unset, missing, unwritable inbox and DB failure; 500 for write error | router mapping | all four tested | Met; no logging (F4) |
| 10 | Process-wide lock around name choice + dup check + replace | `_publish_lock` | concurrency test | Met (test is probabilistic, T3) |
| 11 | Stale staging cleanup, 1 h | `_remove_stale_staging` | yes | Met |
| 12 | Backend image carries every new import | only `batch.utils.file_hash`, `batch/utils/__init__`, both copied in `Dockerfile.backend` and re-included in `.dockerignore` | n/a (checked by reading) | Met |
| 13 | No commit in repository, session via `get_async_db` | `existing_content_hashes` is a plain select | integration test | Met |
| 14 | Docs: `backend_api.md`, upload-endpoint spec, ingestion.md, Environments.md | all updated | n/a | Met, minor inaccuracies (F8) |
| 15 | Schema + 3 trees regenerated | description added; py + d.ts regenerated, Kotlin unchanged (generator emits no descriptions) | n/a | Met |

## Findings

### Critical
None.

### Important

**F1. No per-file index in the response; A3 cannot reliably map an entry to the file it sent.**
`Backend/app/api/uploads.py:147-163`. `uploaded[]` and `failed[]` are two separate lists; each entry carries only
`original_filename` (echoed raw and unchanged, which is good). The order inside each list follows request order, but the
interleaving of the two lists is lost. Scenario: A3 sends `[a.jpg, b.jpg, a.jpg]` (two documents with the same display name
cannot occur inside one SAF folder, but the web page and any other caller can, and a custom environment or a sub-batch retry can
repeat names), the server rejects the second `a.jpg` (bad magic) and accepts the first and third: the response has two
`uploaded` entries named `a.jpg` and one `failed` entry named `a.jpg`, and the client cannot tell which of its three files failed.
Also, OkHttp percent-encodes `"` (as `%22`) and CR/LF in the multipart filename, so for such names the echoed `original_filename`
differs from what the app holds (verify on a device; the app can pre-normalise names or key by index). Fix: add an integer
`index` (0-based position in the request) to both `UploadedFile` and `FailedFile`; additive, so old clients keep working; update
the schema, three trees, `backend_api.md`. Cheap now, painful after A3 ships. If the owner declines, document in the A3 design
that the client must send one file per distinct name per request and match by name.

**F2. The 5000-entry inbox scan cap silently disables the inbox duplicate check, and the scan is repeated per file.**
`inbox_upload_service.py:121-137,163`. `_find_in_inbox` returns `None` ("not a duplicate") once 5000 entries were visited, with only
a server warning. The headline use of this epic is a first bulk sync of a whole album into an inbox that nobody ingests until
later, so an inbox above 5000 files is the expected case, not an edge. Past the cap, a re-sent renamed file or a copy with another
name is `accepted`, and `ingest_hash_dedup.dedupe_in_batch` then moves it to `duplicates/` and records a popularity absorption
(see `ingest_hash_dedup.py` docstring and `record_popularity`), which is exactly the inflation the owner wanted blocked. Also
each file of a 50-file request rescans the whole directory under the global lock (50 x N `DirEntry` checks, a hash per same-size
file), serialising all uploads. Fix: per request, under the lock, build one `{size: [names]}` index with a single `scandir`
(no cap, or a cap that fails the request with 503 instead of silently accepting), keep a per-request `{digest: name}` cache of
files already published by this request and a size-keyed cache of inbox digests, and update the index as files are added.
If a cap is kept, fail closed (503) or report it in the response, never "not a duplicate".

**F3. Library duplicate check misses images whose stored hash is of a converted file (WebP, the allowed type).**
`image_repository.py` `existing_content_hashes`; `batch/utils/image_format_fix.py:138` + `image_format_apply.py:55` rewrite
`images.content_hash` with the hash of the converted JPEG (original goes to `converted_originals/`). A phone WebP that was ingested
earlier, uploaded again (renamed file, reinstalled app, tracker wiped on album change), hashes to the original WebP bytes, matches
no row, and is `accepted`; it then flows through ingestion as a new image and shows up as a near-duplicate review item, and
neither `dedupe_cross_corpus` (same hash comparison) nor this endpoint stops it. Popularity is not inflated by the hash path, but
the "sync is idempotent" property of the README (renamed file answered `duplicate`) does not hold for converted formats. The
same blind spot applies to images with `content_hash` NULL (never backfilled). Fix options: (a) accept and document the gap in
`backend_api.md` and the A3 design (WebP/extension-fixed files may come back as `accepted` after a tracker wipe); (b) keep the
original hash on conversion (needs a migration, owner's call). At minimum document it.

**F4. No logging on the 500/503 inbox paths.**
`uploads.py:140-145`, `inbox_upload_service.py:91-105,171-173`. `InboxUnavailableError` and `InboxWriteError` are raised
`from exc`, then the router raises `HTTPException(... ) from None`; nothing logs the cause (Starlette does not log HTTPExceptions).
The operator sees a bare 500/503 in the access log with no `errno` (disk full vs OneDrive lock vs ACL deny, which S1 flagged as
the real risks). Only the DB failure path logs. Fix: `logger.exception`/`logger.warning(..., exc_info=True)` where the
`OSError` is caught (path value may be logged server-side; only the response body must not carry it, as now).

### Minor

**F5. Sync filesystem calls on the event loop.** `inbox_upload_service.py:186-187`: `_prepare_inbox()` (is_dir, mkdir, os.access)
and `_remove_stale_staging()` (scandir, stat, unlink) run on the loop before the first `to_thread`. On a OneDrive path these can
stall every other request. Move both into the same `to_thread` call as `_write_all` (or one helper).

**F6. `os.replace` can overwrite.** `inbox_upload_service.py:168-169`. `_free_name` checks existence, then `os.replace`. The lock
covers only this process; `ingest_abort` moves files back into the inbox and a person may copy files in, so a file created
between the check and the replace is overwritten silently. Use `os.link` + unlink or `os.rename` (fails on Windows if the target
exists; on POSIX use `os.link`) and retry with the next suffix. Rare, but an overwrite is the anti-goal.

**F7. Poison file blocks the whole sync.** `inbox_upload_service.py:171-173`. Any per-file `OSError` fails the whole request with a
500 that does not name the file; a retry hits the same file again. Plausible trigger: stem cap is 200 bytes, but the inbox path
(`<BASE_PATH>\inbox`, under OneDrive) + 200 ASCII chars can exceed the Windows 260-character path limit. A3 would need to
bisect. Consider a lower stem cap (about 120 bytes) or document the behaviour in the A3 design. Also the owner-approved rule
("no fourth outcome") is respected; this is only about the cap value and the error not naming the file.

**F8. Doc inaccuracies.** (a) `backend_api.md` states the 429 but not the limit itself (10 requests per minute per IP, `rate_limit.py:27`), which
is what bounds A3's sync speed (batch of 1 = 10 files a minute; a 429 stops the sync per the README). (b) `docs/superpowers/specs/
upload-endpoint.md` keeps the old text below a "superseded" banner; acceptable, but the "Architecture" section still names
`save_incoming`. (c) `backend_api.md` does not state that `saved_as` of a `duplicate` can equal an unrelated inbox file's name.
(d) the 500 text "files written earlier in the same request stay and come back as duplicate" is true only until
`ingest_hash_dedup` moves them (then they come back as duplicate via the library, also true once the row is registered; in the
window between move and commit they come back `accepted`, see Q2).

**F9. Small code smells.** `uploads.py:58` `status: str = Field("accepted", ...)` default is misleading (the field is required in
the schema); make it required. `status` strings are free `str` constants (`ACCEPTED`, `DUPLICATE`), a `Literal`/`StrEnum` would
make the router model and the service agree. `InboxUploadService` is a one-method class over module functions with a 24-line
`_write_all` that mixes naming, duplicate policy and I/O; split `_publish(candidate, digest)` out so the lock section is
readable and testable. Two DI functions (`get_upload_image_repository`, `get_inbox_upload_service`) are one more than neighbours
need (`get_recut_repository`, `get_history_repo` pattern is a single provider); acceptable.
`sanitize_filename` line 81 is a nested conditional expression that is hard to read; split into an if/else.

**F10. Session held for the whole request.** The `AsyncSession` from `get_async_db` stays open (idle in transaction after the
select) while up to 50 files are written to a possibly slow OneDrive disk. Not a correctness bug; pool pressure is low at
`max 10 req/min`. Closing the read early (`await session.commit()` is forbidden in repositories, so do it in the dependency) is
optional.

## What is good

- The spec is followed closely; every "my call" is implemented as written and documented.
- Layering is right: validation in the router, policy in the service, one read-only query in the repository that never commits.
- The staging-directory design is verified against the real `hash_incoming_files` in a test, and against the actual
  `ingest_hash_dedup` behaviour: `.uploading` and `duplicates` are directories and skipped; sanitised names never start with a dot, so
  `extract_text_from_memes` hidden-file skipping is irrelevant; Cyrillic and spaces are fine for `os.listdir`/`move_without_overwrite`.
- Hash is lowercase hex of the same bytes, `IN` never matches NULL, no status filter; integration test covers all four statuses.
- Error bodies are generic (no path, no exception text); the DB failure fails closed (503) instead of skipping the check.
- `uploaded[]` order equals request order and `content_types` stays aligned with candidates; `original_filename` is echoed unchanged.
- The response is backward compatible for both existing callers (web `UploadPage`, Android `UploadScreen`): they show
  `total_accepted` and names only.
- Dockerfile.backend and .dockerignore already cover `batch/utils/file_hash.py`; no dead `INCOMING_PATH` code remains in code or CI
  (only historical docs and a negative test).
- Tests use `tmp_path`, a fake repository, and real file I/O; the atomicity test spies on `os.replace` instead of mirroring the
  implementation.

## Test gaps

- T1: `test_hash_matches_the_batch_algorithm` never calls the service; assert instead that `existing_content_hashes` was called with
  `sha256_file(<the written file>)`.
- T2: no test for the scan cap (F2), nor for an inbox of more than one same-size file, nor for a duplicate when the file is
  larger than one read chunk.
- T3: the concurrency test (6 threads, one `TestClient`) can pass without the lock because the race window is tiny; make it
  deterministic by patching `_free_name` to `time.sleep` or by holding `_publish_lock` and asserting the second request blocks.
- T4: no test that a mid-request write error keeps files written earlier and leaves no staging file for the failing one (spec
  states this behaviour).
- T5: no test for case/Unicode collision (`A.png` vs `a.png`, NFD vs NFC), no test for `.JPG` with an uppercase extension through
  the endpoint, none for a stem that becomes empty after the byte cap, none for `Path.suffix` vs `sanitize_filename` ext
  mismatch (names like `a.png.` are rejected by the router, which is fine, but untested).
- T6: `test_unwritable_inbox_is_a_503` tests only "`.uploading` is a file"; `os.access(W_OK)` is unreliable on Windows, so the
  real unwritable case is exercised nowhere (E1 should).
- T7: response-order test with a mixed valid/invalid/duplicate batch and two files sharing a name (documents F1).
- T8: nothing in `tests/integration/` proves a file written by the endpoint is picked up by `ingest_hash_dedup.run` end to end
  (that is B2; the unit test only calls `hash_incoming_files`).

## Questions for the owner

1. F1: add an `index` field to `uploaded[]` and `failed[]` now (recommended), or keep name-based matching and constrain A3?
2. Race between `ingest_hash_dedup` and the library check: after `ingest_hash_dedup` moves a file out of the inbox but before
   its `Image` row is committed, a re-upload of that content is `accepted` (not in the inbox, not yet in `images`), so a later run
   records a popularity absorption. The window is small (one transaction); acceptable?
3. F3: accept that converted WebP files can come back `accepted`, or should conversion keep a record of the original hash?
4. F2: is failing with 503 when the inbox holds more than N entries acceptable, or must a large inbox keep working (then build the
   index without a cap)?
5. Rate limit: 10 requests per minute per IP bounds a sync at 10 x batch size; is that intended for A3, or should the limiter be
   raised/exempted for the sync path?
