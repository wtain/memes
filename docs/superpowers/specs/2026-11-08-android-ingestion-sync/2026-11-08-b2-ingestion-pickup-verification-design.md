# B2: Ingestion pickup verification, design

Status: done
Originates from: docs/superpowers/specs/2026-11-08-android-ingestion-sync/README.md
Plan: docs/superpowers/plans/2026-11-08-b2-ingestion-pickup.md

Owner requirement: "Check that the ingestion flow would pick up the uploaded images from the uploaded images directory."
B2 proves with tests, and documents, that a file written by the B1 upload endpoint into `PATH_INGESTION_SOURCE` is picked up
by ingestion Stage 1 (`ingest_hash_dedup`) and Stage 1.5 (`ingest_validate_formats`), and that nothing the upload path
creates breaks them. No production code changes. Small calls are marked "(my call)".

## Approach

`tests/integration/test_upload_to_ingestion.py` drives the real `POST /api/uploads` router and `InboxUploadService`
in process (httpx `ASGITransport`, so the test's event loop and database session are shared) with the real `ImageRepository`
on the test database, in a `tmp_path` layout `base/` (BASE_PATH) and `base/inbox/` (PATH_INGESTION_SOURCE). It then runs
the real `ingest_hash_dedup.run()` and `ingest_validate_formats.run()`. Only `settings` (path stub) and the rate limiter are
patched. (my call) Stage 1's `run()` rather than `main()`: `main()` opens its own session on the configured database.

## What is asserted

| Claim | Test |
|---|---|
| Original names (Cyrillic, spaces, NFD input normalised to NFC) become `pending` rows with the right `content_hash`, `ingestion_batch_id`, moved into BASE_PATH; a name colliding with a different library file is renamed by ingestion and the row carries the final name | `test_uploaded_files_are_registered_pending_and_moved_to_base_path` |
| `.uploading` (with a leftover) and `duplicates/` are ignored and survive; uploads keep working afterwards | `test_staging_and_duplicates_directories_are_ignored_and_survive` |
| Same content uploaded twice, under two names, in one request or concurrently: one inbox file, ingestion sees one | `..._leaves_one_inbox_file`, `..._accepted_once`, `test_concurrent_identical_uploads...` |
| A library image of any status (active, pending, rejected, cut_proposed) makes the upload a `duplicate`, nothing is written, Stage 1 finds nothing and `popularity_absorptions` gains no row; the same bytes dropped by hand DO add one (contrast test) | `test_library_duplicate_is_refused...`, `test_contrast...` |
| A scan running during a slow publish sees nothing or the complete file, never a partial one (mutation: a non-atomic slow write makes it fail) | `test_scan_during_a_slow_upload...` |
| Sanitised names never start with a dot (so `extract_text_from_memes`'s hidden-file rule never skips an upload) and contain no separator | `test_sanitiser_never_produces_a_hidden_name` |
| Stage 1.5: uploaded JPEG/PNG are `no_op`, a WebP is converted to JPEG, rows follow | `test_validate_formats_...` |

## Findings

No bug found in B1 or ingestion. Observations, none a defect: Stage 1 checks cross-corpus duplicates against `active`
images only while upload checks every status (upload is stricter; documented). `extract_text_from_memes` and later stages
are not run (they need models); they pick pending images by registered filename and skip dot-names, both covered above.

## Not verified

A real OneDrive-synced inbox (placeholder files, sync latency), a live backend process and a real phone: project E1.
