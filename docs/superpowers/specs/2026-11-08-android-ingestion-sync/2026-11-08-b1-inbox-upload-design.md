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

- `uploaded[]` entry fields are unchanged except for the additive `index` (see Review outcome); `status` is now `"accepted"` (file written to the inbox) or `"duplicate"` (nothing
  written). It was always `"ok"` before. Clients must not rely on `"ok"`.
- `saved_as`: the name the file has in the inbox for `accepted`. For a `duplicate` (library or inbox) it is the sanitised original
  name, never the name of an existing inbox file (review SR-M5; nothing is written under it).
- `failed[]` keeps rejections with `reason` (same texts as before).
- `total_accepted` = `len(uploaded)`, i.e. accepted plus duplicate (my call): an existing client shows duplicates as uploaded, which
  is what the Android tracker wants (`accepted` and `duplicate` both mean "done"). `total_failed` = `len(failed)`.

Errors: `503` when `PATH_INGESTION_SOURCE` is unset, relative, not an existing directory, equal to or containing `BASE_PATH`, or its
staging directory cannot be created or is a symlink/junction/file (a subdirectory `BASE_PATH/inbox` stays valid), or the inbox holds
more than 50,000 entries (message names the cause, never the path value); `507` when the inbox volume would be left with under
1 GiB free; `503` when the duplicate lookup in the database fails (a silent skip would let
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
3. Remove control, format, surrogate, private-use and line/paragraph-separator characters (categories Cc, Cf, Cs, Co, Zl, Zp; this
   drops NUL, the right-to-left override and zero-width characters; Cyrillic is kept); replace `<>:"/\|?*` with `_`.
4. Drop the extension, trim leading dots (no hidden files) and trailing dots and spaces from the stem.
5. A stem whose part before the first dot is a Windows reserved device name (CON, PRN, AUX, NUL, COM1-9, LPT1-9, any case)
   gets a `_` prefix.
6. Cap the stem at 200 UTF-8 bytes, cut on a character boundary (my call: safe for 255-byte limits and leaves room for a suffix).
7. Empty stem: `upload-<uuid8>`.
8. Result is `stem + ext` with the extension lowercased (my call).

## Staging and no-clobber publish

Each non-duplicate file is written to `<inbox>/.uploading/<uuid4>`, then published into the inbox under its final name with a
no-clobber primitive: `os.link(staged, final)` followed by unlinking the staged file (verified to work on a local NTFS volume; it
raises `FileExistsError` when the name exists, case-insensitively on NTFS). If hard links are unsupported (an `OSError` other than
`FileExistsError`, for example on a FAT or cloud-filtered volume) the fallback is `os.rename` on Windows (atomic, fails when the target
exists) and an exclusive-create copy elsewhere. On `FileExistsError` the next candidate name is tried (`stem_1.ext`, `stem_2.ext`, ...,
then `upload-<uuid8>`). `ingest_hash_dedup` skips directories, so `.uploading` is invisible to it and a file appears only complete
(the exclusive-create copy fallback is the one exception: the target is briefly visible while it is written). Failed requests remove
their own staging files. Per request, staging files older than 1 hour are deleted (best effort).

The no-clobber primitive is what guarantees "never overwrite", against any writer (a person, `ingest_abort`, another process). The
process-wide `threading.Lock` only covers the step that publishes (and a bounded registry of recently published content, which makes
two concurrent requests with the same new bytes write one file), so concurrent requests do not pick the same name. The same suffix
shape as `safe_move.available_filename`, implemented backend-local because the backend image carries only a few `batch/utils`
modules (my call). The `.uploading` path must be a plain directory (a symlink or NTFS junction is refused with 503).

Blocking filesystem work (inbox checks, stale cleanup, listing, hashing, writing, Pillow) runs in worker threads.

## Content gate

Before anything is staged, each file is opened with Pillow in a worker thread (`validate_image_content`): `Image.open`, the detected
format must belong to the extension's family (JPEG for `.jpg`/`.jpeg`, PNG, GIF, WEBP, BMP, TIFF for `.tif`/`.tiff`), width x height
must not exceed `MAX_IMAGE_PIXELS` (100,000,000), then `verify()`. A failure is reported in `failed[]` with a reason. A format that does
not match the extension is **rejected, not corrected** (owner's call): the file is not a valid upload under that name, and silently
renaming would hide a client bug. The cheap magic-byte check stays in front of it.

## Duplicate check

SHA-256 hex of the bytes, the same algorithm as `batch.utils.file_hash.sha256_file` and `images.content_hash` (a test calls the service
and compares).

1. Library: one query per request, `ImageRepository.existing_content_hashes(hashes) -> set[str]`, `content_hash IN (...)`, no status
   filter (active, pending, rejected, cut_proposed); NULL never matches. Read only, never commits, no migration. Limit: the hash of a
   converted file (WebP to JPEG by the format fix) is of the converted bytes, and older rows can have NULL, so a re-uploaded phone WebP of
   such an image is not a duplicate here and goes through normal ingestion (hash and CLIP review). Hashing is unchanged.
2. Inbox: one `os.scandir` of the inbox per request builds a size to paths index (regular files only, never directories or symlinks);
   only files of the same size as an upload are hashed, lazily, and each digest is cached for the request. Published files are added to
   the index, so a second identical file in the same request is a duplicate. The hashing runs outside the global lock. More than
   50,000 entries fail the request with 503 ("inbox is full, run ingestion"): failing closed, because "not a duplicate" would let a
   re-sent file reach ingestion and inflate popularity. An unreadable file is skipped.

A duplicate writes nothing: a library duplicate is never staged; an inbox duplicate is detected before staging.

## Layout

- `Backend/app/api/uploads.py`: validation, DI, response models (hand-written inline, as before).
- `Backend/app/services/inbox_upload_service.py`: sanitising, inbox resolve, staging, lock, duplicate logic.
- `Backend/app/repositories/image_repository.py`: `existing_content_hashes`.
- `Backend/app/services/image_store.py`: `INCOMING_PATH` / `save_incoming` removed.
- `shared/schemas/uploadedfile.schema.json`: `status` gets a description naming the values (stays `string`, so generated types stay
  source compatible; my call); three trees regenerated.

## Tests (as built in the first pass; the review tests are listed under Review outcome)

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

OneDrive behaviour of hard links and renames in the inbox, and whether the real backend process can write to the inbox: E1 checks.

## Review outcome

Two reviews of the first pass: `docs/superpowers/reviews/2026-11-08-b1-inbox-upload-security-review.md` (SR) and
`docs/superpowers/reviews/2026-11-08-b1-inbox-upload-review.md` (CR). One fix iteration, then closed. The sections above describe the
fixed behaviour. Owner's calls given for the fix: a format/extension mismatch is rejected; `saved_as` of a duplicate is the sanitised
original name.

| Finding | Outcome |
|---------|---------|
| SR-I1, CR-F6 silent overwrite by `os.replace` | Fixed. No-clobber publish (link, then NTFS rename or exclusive-create fallback), retry on `FileExistsError`. Tests: a foreign file appears between the check and the publish (fails with a plain `os.replace`), a wide-window concurrency test and a same-content concurrency test (all three fail with the lock removed and `os.replace` restored), primitive tests including the fallbacks. CR-T2/T3 closed. |
| SR-I2, SR-M4 content gate | Fixed. Pillow open, family check against the extension, pixel cap 100,000,000, `verify()`, in a worker thread. Pillow is in `Backend/requirements-backend.txt` (12.0.0). Tests: `BM<script>` as `.bmp`, GIF bytes named `.jpg` (rejected), a real image of each of the 8 extensions, the pixel cap, a real 400-megapixel PNG. The unauthenticated-write risk itself stays an owner-accepted risk (epic README). |
| SR-I3 memory and disk | Fixed. Reads in 1 MiB chunks, stops at `MAX_FILE_SIZE + 1` (test counts the bytes read); free-space guard (`MIN_FREE_BYTES` = 1 GiB after the request's bytes, 507). There is no cap on the entry count other than the 50,000 scan limit (503). Limits unchanged (50 files, 20 MB, 10 requests per minute). Starlette still spools the multipart body to a temporary file before the endpoint runs; that is not bounded here. |
| SR-M1, CR-F2 inbox scan | Fixed. One listing per request, lazy same-size hashing with a per-request cache, cap 50,000 and fail closed with 503. Tests for the single scandir, hash counts and the cap. |
| SR-M2 hash under the global lock | Fixed. Hashing is outside the lock (test asserts it); the lock covers only publish and the recent-content registry. |
| SR-M3 invisible characters | Fixed. Categories Cf, Zl, Zp (plus Cs, Co) dropped after NFC; Cyrillic kept. |
| SR-M5 `saved_as` of a duplicate | Fixed (owner's call): the sanitised original name for library and inbox duplicates. |
| SR-M6 inbox path sanity | Fixed. Must be absolute, must not equal or contain `BASE_PATH`; `BASE_PATH/inbox` stays valid. |
| SR-M7 `.uploading` symlink | Fixed. A symlink, NTFS junction or non-directory is a 503. (A OneDrive placeholder reparse point is deliberately not matched.) |
| SR-M8 non-`OSError` errors | Skipped: not reachable over HTTP (Starlette decodes headers as latin-1, proven by the reviewer); informational. |
| CR-F1 per-file index | Fixed. Additive `index` on every `uploaded[]` and `failed[]` entry, optional in the schema (Kotlin `Int? = null`, last constructor parameter, source compatible), three trees regenerated, `backend_api.md` updated. `original_filename` stays exactly as received; OkHttp may percent-encode quotes and line breaks, documented. |
| CR-F3 converted-hash blind spot | Documented (this spec, `backend_api.md`, `ingestion.md`); hashing is unchanged. |
| CR-F4 logging | Fixed. The 500/503 inbox paths log the exception (class and message, never file content); client bodies carry no paths. |
| CR-F5 sync I/O on the loop | Fixed. `prepare_inbox` and the stale cleanup run in `asyncio.to_thread`. |
| CR-F7 poison file | Partly fixed: a failing write logs the sanitised name (server side only). The 200-byte stem cap is unchanged. |
| CR-F8 doc inaccuracies | Fixed. The 10 requests per minute per IP limit and "retry a 429 after the window" are in `backend_api.md` (A3 must handle 429). |
| CR-F9 smells | Fixed the cheap ones: `status` is required (no misleading default), `sanitize_filename` branches written out, `_write_all` split into `_write_one`, `_publish`, `_publish_once` and `_InboxIndex`. `status` stays a plain string (schema compatibility). |
| CR-F10 session held during writes | Skipped: pool pressure is low at 10 requests per minute; closing the session early needs a dependency change outside this scope. |
| CR-T1 to T8 | T1 (hash test calls the service), T2 (scan cap, multi same-size files, chunk reads), T3 (deterministic race), T4 (mid-request write error keeps earlier files and leaves no staging file), T5 (case-insensitive and NFD collisions, skipped on case-sensitive volumes), T7 (index with shared names) added. T6 (real unwritable inbox, `os.access` is unreliable on Windows) and T8 (endpoint to `ingest_hash_dedup`, B2) left. |

Not verified here: hard links and renames inside the real OneDrive-synced inbox, the real backend account's rights there, and the
behaviour of a OneDrive placeholder (cloud-only file) in the same-size hash step (E1).
