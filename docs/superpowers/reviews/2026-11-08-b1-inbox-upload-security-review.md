# B1 inbox upload endpoint: security review

Reviewer axis: security of the file-upload path (commits b8e68999, e84391df, 4424172c, spec 52aa7023).
Experiments: `C:\Users\ramiz\AppData\Local\Temp\claude\4a112938bd3f2dfb\7045ec19-3c3d-4051-ba0c-572c6c5b0e67\scratchpad\b1sec\exp.py` and `exp2.py` (sanitiser unit probes, and the real router and service driven through a TestClient against a temp inbox with a fake repository; no database, no ports). "Proven" = reproduced there; "suspected" = reasoned from code only.

## Verdict

**Ship after fixes** (small). No path traversal, overwrite-by-name or half-written-file hole was found; the three anti-goals about names, atomicity and popularity hold. The fixes below are one real race (silent overwrite through `os.replace`), a hardening of the unauthenticated-write-into-the-library trust change, and a few minor items. None blocks the Android work if the owner accepts the residual risks.

## Findings

### Critical
None.

### Important

**I1. `os.replace` can silently overwrite a file that appears between `_free_name` and the replace (suspected, narrow window).**
`Backend/app/services/inbox_upload_service.py:~150-153` (`_free_name` then `os.replace`). The lock only serialises this process's uploads. The inbox is a human drop folder (OneDrive-synced, plus the owner copying files in by hand), and `ingest_hash_dedup` runs from another process. A file created at the chosen name by anyone else after `.exists()` returns False is replaced without notice (POSIX and Windows `os.replace` both replace). Impact: loss of a not-yet-ingested meme; the anti-goal "overwrite" is only guaranteed against other uploads, not against the inbox owner. Fix: publish with a no-clobber primitive: `os.link(staged, final)` (raises `FileExistsError`) then unlink staged, retrying with the next suffix in a loop; fall back to `os.rename` on Windows (fails if the target exists) when hard links are unsupported (OneDrive/FAT/exFAT may refuse links). Then `_free_name`'s exists check is only an optimisation.

**I2. Trust change: an unauthenticated caller can now write arbitrary-content "images" into the path that feeds the library (design-level, accepted by the owner for auth, but the consequences are new).**
`Backend/app/api/uploads.py` upload path to `PATH_INGESTION_SOURCE`. Before, uploads went to `BASE_PATH/incoming`, a directory nothing consumed. Now anything passing a 2-byte (`BM`) to 8-byte magic check lands in a directory that `ingest_hash_dedup` registers, then `ingest_validate_formats` opens with Pillow, then OCR, CLIP, YOLO and Ollama vision run on it. Impact: any LAN host can (a) feed crafted decoder-exploit or decompression-bomb files to Pillow/EasyOCR/Ollama, (b) pollute the library review queue, (c) fill the disk (see I3). Proven that the magic check is the only content gate: `b"BM<script>alert(1)</script>"` as `p.bmp` and GIF bytes named `gif.jpg` with `image/jpeg` were accepted and written. Mitigations, cheapest first: keep the rate limit; add a cheap structural check (`PIL.Image.open(BytesIO(data)).verify()` with `Image.MAX_IMAGE_PIXELS` set, in the worker thread) before staging, which also rejects the 2-byte BMP and mismatched types; optionally an optional shared-secret header for the Android client (the epic says no auth, so at minimum record this as an owner-accepted risk).

**I3. Disk and memory exhaustion are cheaper than before and now hit the inbox volume (partly pre-existing).**
`uploads.py` reads each part fully (`await file.read()`) before the 20 MB check, so the limit does not bound memory: one part of N GB is spooled by Starlette to disk then loaded whole into RAM (suspected; pre-existing). Within the limits a single IP can push 10 requests/min x 50 x 20 MB = about 10 GB/min onto the inbox volume (OneDrive, so also sync traffic), and nothing caps total inbox size or file count. Fix: read with a bounded loop (`await file.read(MAX_FILE_SIZE + 1)`), add a free-space and entry-count guard in `_prepare_inbox` (503 when the inbox holds more than `MAX_INBOX_ENTRIES_SCANNED` entries or free space is under a floor). The entry-count guard also makes I4 impossible.

### Minor

**M1. Inbox duplicate scan silently stops at 5000 entries and then reports "not a duplicate"** (`_find_in_inbox`, `MAX_INBOX_ENTRIES_SCANNED`). An attacker (or a large backlog) that fills the inbox with more than 5000 entries disables the inbox half of the popularity-inflation guard; identical files are then written, and `ingest_hash_dedup` drops them but records them as popularity absorptions. Also `duplicates` and `.uploading` count toward the cap. Fix: fail closed (503, "inbox too large") instead of returning None.

**M2. Lock held across disk hashing, and pool starvation.** `_find_in_inbox` hashes every same-size file (up to 20 MB each) while holding `_publish_lock`, inside `asyncio.to_thread` threads. The event loop is not blocked (verified by reading), but waiting requests each occupy a thread of the default executor (about cpu+4 threads) which is shared with any other `to_thread` user. With an inbox of same-size files, uploads serialise and can stall. OneDrive cloud-only placeholders pass `is_file(follow_symlinks=False)` and hashing them would force a download while the lock is held (suspected, Windows-specific). Fix: hash outside the lock, take the lock only for name choice and publish (re-check cheaply after); skip files with `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS`.

**M3. Bidirectional and invisible characters survive sanitising** (proven): `"\u202egpj.jpg"` is stored as-is (so it displays as `jpg.jpg`-style spoof in the review UI and in `images.filename`), as are `\u200b` and `\u2028`. `unicodedata.category` filter only removes `Cc`. Not a traversal or execution issue (the extension is always a validated image extension). Fix: also drop categories `Cf`, `Zl`, `Zp` (and `Cs`, `Co`).

**M4. Declared MIME, extension and content are not cross-checked** (proven: GIF bytes saved as `.jpg`). Downstream `ingest_validate_formats` renames extension/content mismatches, so this is cosmetic, but a stored `.jpg` that is not a JPEG can confuse any consumer trusting the extension. Fixed by the I2 Pillow check (compare `Image.format` to the extension).

**M5. `inbox duplicate` returns `saved_as` = name of an existing inbox file** (`_write_all`, `StoredFile(candidate, existing, DUPLICATE)`). That name came from some earlier uploader or from a hand-dropped file. Exposure is small: the caller must already hold the identical bytes. Consider returning the sanitised caller name for both duplicate kinds (the Android tracker does not need the existing name).

**M6. Inbox path is used as configured with no sanity check** (`_prepare_inbox`). A relative `PATH_INGESTION_SOURCE` resolves against the backend cwd; a value equal to `BASE_PATH` or one of its ancestors is accepted and uploads would then write into the live library directory (names cannot overwrite, but they bypass registration). Fix: require `inbox.is_absolute()`, and refuse when it equals or contains `BASE_PATH`.

**M7. Stale-staging cleanup is by age only and per request** (`_remove_stale_staging`). Not exploitable (`follow_symlinks=False`, regular files only, within `.uploading`), but if `.uploading` is itself replaced by a symlink/junction, `mkdir(exist_ok=True)` accepts it and uploads follow it. Needs local filesystem access, so low. Optional fix: reject `staging.is_symlink()`.

**M8. Errors outside `OSError` are not mapped** (for example a `UnicodeEncodeError` from `_cap_utf8` on a lone surrogate). I tried to reach it with raw multipart bytes (`\xed\xa0\x80`, `\xff\xfe`): Starlette decodes headers as latin-1, so it is **not reachable** over HTTP today (proven: accepted as mojibake names). Informational only; a broad `except Exception` mapping to `InboxWriteError` in `_write_all` would keep it that way if the decoder changes.

## Things checked and found fine

- **Traversal** (proven in `exp.py`/`exp2.py`): `../../x.jpg`, `..\..\x.jpg`, `C:\a\b.jpg`, `\\srv\share\a.jpg`, `/.jpg`, `a\x00b.jpg` all reduce to a basename (`x.jpg`, `b.jpg`, `a.jpg`, `ab.jpg`, `upload-<uuid8>`). The result never contains a separator.
- **NTFS alternate streams**: `a.jpg:stream.png` becomes `a.jpg_stream.png`; `x.jpg:Zone.Identifier.png` becomes `x.jpg_Zone.Identifier.png`. `:` is always replaced.
- **Hidden/staging names**: `.uploading.jpg` becomes `uploading.jpg`; `duplicates.jpg` is a file name with an extension and cannot equal the `duplicates` or `.uploading` directories; leading dots and spaces are stripped, and all-dots names get `upload-<uuid8>`.
- **Reserved device names**: `CON.txt.jpg`, `con.jpg`, `COM1.jpg`, `NUL .jpg` are prefixed `_`. I also probed this Windows 11 host in the scratch directory: `COM1.jpg`, `COM¹.jpg`, `LPT².jpg`, `CONIN$.jpg` write as ordinary files (extension makes them non-device), so the unhandled superscript digits are harmless in practice.
- **Length**: 300-char ASCII and 150-char Cyrillic stems are capped at 200 UTF-8 bytes on a character boundary; names that sanitise to empty fall back to a random name.
- **Case-insensitive collisions**: `_free_name` uses `.exists()`, which is case-insensitive on NTFS, so `A.JPG`/`a.jpg` both route to the suffix path (extension is lowercased anyway).
- **Within-process races**: name choice, inbox duplicate check and replace are all under one `threading.Lock`, in a worker thread; the staged file is in the same volume, so `os.replace` is atomic and the inbox never lists a partial file (staging empty after my runs; the directory is skipped by `hash_incoming_files`, which uses `os.path.isdir`).
- **Staging**: random uuid4 names, removed on failure and on duplicate; stale cleanup uses `follow_symlinks=False`, only unlinks regular files in `.uploading`.
- **SQL**: `existing_content_hashes` uses a bound `IN` list built from server-computed SHA-256 hex digests (never client strings), at most 50 values, read-only, no commit. No injection surface.
- **Error bodies**: 503/500 `detail` strings are fixed texts (`from None`, no path or exception text); original DB errors go only to the server log. The 422/429 behaviour is unchanged. CORS and the rate limiter are untouched by the diff (rate-limit key is `request.client.host`; `X-Forwarded-For` is only honoured by uvicorn from `127.0.0.1`, so LAN clients cannot spoof it).
- **Removal of `INCOMING_PATH`**: no remaining code reader found (only docs/historical specs mention it); the web UploadPage and Android callers use the same response shape.
- **Duplicate oracle**: the response confirms that an exact-bytes file exists in the library (any status, including rejected) or in the inbox. The caller must already hold the exact file, and gains no name, id or status of the library image, so exposure is a one-bit membership test.
- **Popularity**: duplicate path writes nothing and records nothing.

## Residual risks the owner should accept knowingly

1. No authentication: any host that can reach the backend can add files to the ingestion inbox, bounded only by 10 requests per minute per IP (I2, I3).
2. The single-process lock: a second uvicorn worker would break the in-process serialisation (collisions of suffix choice, then I1 overwrite).
3. Behind a reverse proxy or the Vite dev proxy on localhost, all clients share one rate-limit bucket (and the limiter dict grows per key and never evicts; pre-existing).
4. Duplicate detection against `pending` images from an in-flight ingestion run relies on `images.content_hash`; a file that `ingest_hash_dedup` has moved out of the inbox but not yet committed is invisible to both checks for that moment (documented limitation of Stage 1, not new).
5. Content is only checked by magic bytes; decoder-level attacks are deferred to Pillow and the ML stack downstream (I2).
6. OneDrive behaviour (reparse points, placeholders, `os.replace` on synced folders) is untested here; the spec leaves it to E1.
