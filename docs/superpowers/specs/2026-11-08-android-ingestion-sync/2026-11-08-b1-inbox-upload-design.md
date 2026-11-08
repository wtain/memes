# B1: Inbox upload endpoint, design

Status: done
Originates from: docs/superpowers/specs/2026-11-08-android-ingestion-sync/README.md
Plan: docs/superpowers/plans/2026-11-08-b1-inbox-upload.md

`POST /api/uploads` writes accepted files into the ingestion inbox (`PATH_INGESTION_SOURCE`) instead of `BASE_PATH/incoming`, keeps
the sanitised original file name, writes atomically, and answers `duplicate` for content the library or the inbox already holds.
The epic README is the approved design; this document fixes the details. Small calls are marked "(my call)".

## Contract

Request, limits and per-file validation (MIME type, extension, 20 MB, magic bytes, 50 files, per-IP rate limit) are unchanged.

Response `200` keeps `UploadResponse { uploaded[], failed[], total_accepted, total_failed }`.

- `uploaded[]` entry fields are unchanged; `status` is now `"accepted"` (file written to the inbox) or `"duplicate"` (nothing
  written). It was always `"ok"` before. Clients must not rely on `"ok"`.
- `saved_as`: the name the file has in the inbox. For a `duplicate` of a file already in the inbox it is that existing file's name;
  for a duplicate of a library image it is the sanitised original name (nothing exists in the inbox under it).
- `failed[]` keeps rejections with `reason` (same texts as before).
- `total_accepted` = `len(uploaded)`, i.e. accepted plus duplicate (my call): an existing client shows duplicates as uploaded, which
  is what the Android tracker wants (`accepted` and `duplicate` both mean "done"). `total_failed` = `len(failed)`.

Errors: `503` when `PATH_INGESTION_SOURCE` is unset, is not an existing directory, or its staging directory cannot be created
(message names the cause, never the path value); `503` when the duplicate lookup in the database fails (a silent skip would let
re-uploads inflate popularity, which the owner decided to prevent); `500` for a write error on a single file (the whole request
fails; files already written by it are `duplicate` on retry, so a retry is safe). Existing `422` and `429` stay.

## Destination, lazily resolved

The inbox path is read at request time with `settings.get("PATH_INGESTION_SOURCE")`; nothing is read at import, so the backend
starts without it and only uploads fail (503). No fallback to `incoming/`. `INCOMING_PATH` and `save_incoming` are removed.
The inbox must already exist (the service does not create the configured directory, to avoid creating a wrongly configured tree
(my call)). Writability is checked cheaply per request by creating `<inbox>/.uploading/`.

## File name sanitising

Applied to the client name by `sanitize_filename(raw, ext)`; `ext` is the already validated lowercase extension.

1. Unicode NFC (keeps Cyrillic names intact).
2. Basename only: cut at the last `/` or `\`.
3. Remove control characters (including NUL); replace `<>:"/\|?*` with `_`.
4. Drop the extension, trim leading dots (no hidden files) and trailing dots and spaces from the stem.
5. A stem whose part before the first dot is a Windows reserved device name (CON, PRN, AUX, NUL, COM1-9, LPT1-9, any case)
   gets a `_` prefix.
6. Cap the stem at 200 UTF-8 bytes, cut on a character boundary (my call: safe for 255-byte limits and leaves room for a suffix).
7. Empty stem: `upload-<uuid8>`.
8. Result is `stem + ext` with the extension lowercased (my call).

## Staging and atomic replace

Each non-duplicate file is written to `<inbox>/.uploading/<uuid4>`, then `os.replace`d into the inbox under its final name.
`ingest_hash_dedup` skips directories, so `.uploading` is invisible to it and a file appears only complete. Failed requests remove
their own staging files. Per request, staging files older than 1 hour are deleted (best effort).

Final name choice, the inbox duplicate check and `os.replace` run under one process-wide `threading.Lock`, in a worker thread, so
two concurrent requests cannot pick the same name. If the name is taken by different content the name gets a numeric suffix:
`stem_1.ext`, `stem_2.ext`, ... (same shape as `safe_move.available_filename`, implemented backend-local because the backend image
carries only `batch/utils/{__init__,recut_cut,file_hash,...}`; my call). Limit: one backend process per inbox (a second worker
process would not share the lock; E1 notes it).

## Duplicate check

SHA-256 hex of the bytes, the same algorithm as `batch.utils.file_hash.sha256_file` and `images.content_hash` (a test compares them).

1. Library: one query per request, `ImageRepository.existing_content_hashes(hashes) -> set[str]`, `content_hash IN (...)`, no status
   filter (active, pending, rejected, cut_proposed); NULL never matches. Read only, never commits, no migration.
2. Inbox: under the lock, files in the inbox with the same size as the upload are hashed (regular files only, never directories or
   symlinks, an unreadable file is skipped, at most 5000 directory entries scanned). A file of another size cannot match, so the
   scan is cheap.

A duplicate writes nothing: a library duplicate is never staged; an inbox duplicate removes its staging file.
A duplicate also covers a second identical file in the same request.

## Layout

- `Backend/app/api/uploads.py`: validation, DI, response models (hand-written inline, as before).
- `Backend/app/services/inbox_upload_service.py`: sanitising, inbox resolve, staging, lock, duplicate logic.
- `Backend/app/repositories/image_repository.py`: `existing_content_hashes`.
- `Backend/app/services/image_store.py`: `INCOMING_PATH` / `save_incoming` removed.
- `shared/schemas/uploadedfile.schema.json`: `status` gets a description naming the values (stays `string`, so generated types stay
  source compatible; my call); three trees regenerated.

## Tests

Sanitising units (traversal, separators, reserved names, dotfile, Cyrillic, long names, forbidden characters, fallback); endpoint
tests with a `tmp_path` inbox: accepted, original name kept, collision suffix, duplicate in inbox, duplicate in library (fake
repository), no write on duplicate, atomicity (staging used, inbox listing never holds a partial file, `hash_incoming_files` sees
only the final file), concurrent same-name requests, 503 for unset, missing and unwritable inbox, 503 on database failure, 500 on
a write error with cleanup, stale staging cleanup, unchanged rejection reasons, hash equality with `sha256_file`. A real database
test of `existing_content_hashes` in `tests/integration/` against `ocrdb_test`.

## Docs

`backend_api.md`, `docs/superpowers/specs/upload-endpoint.md` (superseded sections), `docs/batches/use-cases/ingestion.md`,
`environments/Environments.md`, other mentions of `INCOMING_PATH`.

## Not covered

OneDrive behaviour on `os.replace`, and whether the real backend process can write to the inbox: E1 checks.
