# ADR 2026-11-12: dev/prod image file sync (spike S4)

STATUS: PROPOSED (measured against a throwaway Linux container over loopback with synthetic files; the owner confirms before P3 and
the file part of P4 build on it)

Epic: `docs/superpowers/specs/2026-11-12-environment-data-sync/` (S1 classification, S2 sizes). Sibling record for the rows:
`docs/adr/adr-2026-11-12-dev-prod-sync-technique.md` (S3), whose contract this record keeps: **files are synced before rows**, so a
row is never visible without its file.

## Context

Dev is Windows 11 behind NAT with the Windows OpenSSH 9.9 client, bsdtar (`C:\Windows\System32\tar.exe`) and Python 3.11. There is no
rsync (not in `PATH`, not in Git for Windows' `usr/bin`; a WSL Ubuntu distro is installed but was not started or probed, and rsync
inside WSL would still need its own ssh keys and the slow `/mnt/h` mount), no sshd, and paramiko, asyncssh, fabric, pysftp and
cryptography are not importable in `.venv311`. Prod is a Linux VPS reachable over SSH; dev initiates everything. The userland on prod
is unknown, so the design uses only POSIX `sh`, `find`, `tar`, `sha256sum`, `mv`, `xargs` (all present in busybox-or-GNU form on any
ordinary Linux).

What has to move (S2): the active, unflagged image files, flat in `BASE_PATH` (general about 32.3k files / 3.3 GB, metal 18.1k /
1.9 GB, it 1.4k / 121 MB), identified by `images.filename` and `images.content_hash`. Subdirectories of `BASE_PATH`
(`converted_originals/`, `pre-cuts/`, `comics-sources/`, `excluded/`, ...) are dev-only and never copied. Reverse direction: prod's
flat inbox (files from `POST /api/uploads`) and the `bug_reports` folder.

`images.content_hash` is the full-file lowercase hex SHA-256 (`batch/utils/file_hash.py: sha256_file`, 64 KB chunks). Every writer uses
it (`ingest_hash_dedup`, `detect_file_duplicates` backfill, `fix_image_formats` / `image_format_fix`, `create_recut_cuts` via
`recut_cut`, comics compose). The column is nullable (the backfill fills gaps), so the manifest builder must treat a NULL as "hash the
file locally" and may not skip it. It can be reused as the transfer checksum: no second hash algorithm is needed.

## Decision (summary)

1. **Transport: batched tar over `scp` of ONE file, driven by Python.** Per batch (about 150 to 500 MB, name-sorted): build a tar
   locally with the stdlib `tarfile` (PAX format, UTF-8 names), `scp` it as `<id>.tar.part` into a staging directory on prod,
   `mv` it to `<id>.tar`, extract into a batch directory, verify with `sha256sum -c` against the manifest, then `mv -T` each file
   into `BASE_PATH`. This is the only option that was fast for large files, fast for many tiny files, correct for every file name
   tested, and needs nothing on prod beyond POSIX tools. The same pattern in reverse, using `tar` on the `ssh` stdout stream (which
   is fast on Windows), serves the pull.
2. **Not** `tar | ssh` from Windows (ssh.exe stdin is capped at about 12.5 MB/s, see Q1), **not** bsdtar `-T` (breaks on non-ASCII
   names), **not** `scp -r`/`sftp -b` per file (4 to 6 times slower on the many-files case, write partial files under the final
   name), **not** rsync (absent on dev).
3. **Manifest-driven diff:** dev builds `{filename: (size, content_hash)}` from the promotable rows; prod's state is one
   `find -printf` over ssh (0.2 s for 32,000 files); diff in memory gives missing, size-changed and orphan sets in about 3 ms.
   Same-size content changes are caught by dev's own "last pushed" ledger (a changed `content_hash` since the last run) and by a
   periodic `--deep` remote hash audit.
4. **Atomicity:** staging directory and `BASE_PATH` on the same filesystem; the only operation that makes a file visible is
   `mv -T` (rename(2)) after the `sha256sum -c` of its batch passed. Rows are written afterwards (P2). Orphans are moved to a dated
   quarantine directory, not deleted, and only after the row transaction that retired them has committed.
5. **Access:** a dedicated non-root key-only user on prod with a forced-command gate (verbs allow-listed), dev connects with a repo-
   external `~/.ssh/config` entry. No ControlMaster on Windows (confirmed broken); batches mean few connections anyway.

## Q1: transfer mechanisms (measured)

Test bed: container `s4-sim-prod` (Alpine, current OpenSSH sshd, GNU tar, rsync), reached at 127.0.0.1:52222 from Windows OpenSSH 9.9.
2,000 synthetic JPEG-like files (30 to 400 KB, random bytes, 430.7 MB) and 5,000 tiny files (0.8 to 2 KB, 7.0 MB). Wall time
includes everything on both sides; each row is one run unless noted (a repeat of tar, scp -r and sftp gave 34.7/10.1/10.6 s, so run
noise is about 5 percent). Loopback numbers measure CPU and per-file protocol cost, not the network.

| Mechanism (dev Windows to prod Linux) | 2,000 files, 430 MB | 5,000 tiny files | Verdict |
|---|---|---|---|
| (a1) `scp` once per file | 11 files/s (200 files = 18.7 s) | 11 files/s | one ssh handshake per file (about 60 ms plus scp start); unusable |
| (a2) `scp -r` of the folder | 9.9 s = 43 MB/s | 15.3 s = 330 files/s | per-file round trips dominate small files |
| (b) `sftp -b` batch file, `put` per file | 17.2 s, repeat 10.6 s | 30.5 s = 164 files/s | per-file ack; fully resumable only with `reput` |
| (c1) bsdtar piped into `ssh "tar -x"` | 33.9 s = **12.7 MB/s** | 1.1 s = 4,450 files/s | the `ssh.exe` stdin pump is the limit, see below |
| (c2) same with `gzip` / `ssh -C` | 38.1 s / 54.7 s | not run | compression only slows incompressible data |
| (d) rsync | not available on dev | | reference run inside the container only |
| (e) pure-Python SFTP (paramiko) | not importable | | on paper only, see below |
| **Batch: Python tar, `scp` one file, remote extract + sha256 verify** | **3.9 s = 112 MB/s** | **1.2 s = 5,700 files/s** | chosen |

Batch breakdown (2,000 big files): local tar 0.68 s, `scp` of the 430 MB tar 2.1 s (206 MB/s, one large file), remote extract
0.69 s, remote `sha256sum -c` 0.40 s. Tiny files: 0.58 / 0.15 / 0.26 / 0.24 s.

**Surprise 1: `ssh.exe` stdin is slow.** Anything piped into Windows `ssh.exe` tops out near 12.5 MB/s, whether the source is a pipe
from bsdtar, a `cmd` pipe, or a file redirect (`ssh host "cat > f" < file`: 34.7 s for 430 MB; `... | ssh host "cat > /dev/null"`:
33.6 s), while `scp` of the same bytes as one file takes 2.1 s. The reverse direction is not affected: `ssh host "tar -cf -" | tar -xf -`
pulled the 2,000 files in 2.8 s (154 MB/s). At 100 Mbit/s (12.5 MB/s) the cap would not matter, but at 1 Gbit/s it would.
**Surprise 2: bsdtar `-T listfile` fails on non-ASCII names** (list read in the ANSI code page: "Couldn't visit directory" for 7 of 18
test names, only 11 of 18 arrived). Python `tarfile` (PAX, `encoding="utf-8"`) transferred all 18.

File name test (18 names: space, double space, Cyrillic, Spanish accents, emoji, CJK, an NFC and an NFD "café" as separate files, and
`& ; $ \` ' % # [ ] { } ~ ( )` and a leading `-`), compared as raw bytes after the transfer:

| Method | Names arrived intact |
|---|---|
| `scp -r`, `scp` per file, `sftp -b` with quoted names | 18 of 18 |
| bsdtar `-T` list file | 11 of 18 (all non-ASCII lost) |
| Python `tarfile` + `scp` + remote extract | 18 of 18, sha256 matched for all 18 |

`CASE.JPG` and `case.jpg` cannot coexist on NTFS, so the test had 18 files. Two caveats for P3: names are never put on a remote command
line (they travel as NUL-separated stdin or inside the tar), and prod must be case-sensitive-aware: two promotable rows whose names
differ only by case are fine on Linux but cannot be checked out on dev, so the manifest builder should report such pairs.
Metadata: `scp`/`sftp` give remote mtime = transfer time and mode 0644; tar keeps the mtime (Python tarfile sets what we give it,
mode forced to 0644, uid/gid 0). No line-ending translation anywhere (bytes `61 0d 0a 62 0a 63 0d 0a 1a` round-tripped). mtime is
not used by the design, so nothing depends on it.

**Interrupted runs (process killed with `taskkill /F /T`, then re-run):**

| Mechanism | State after the kill | Re-run |
|---|---|---|
| `scp -r` (2,000 files) | 858 complete; in this run no partial file, but a single 300 MB `scp` killed after 0.6 s left a **191 MB file under its final name** | redoes everything: 9.7 s |
| `sftp -b` (2,000 files) | 1,392 complete; single 300 MB `put` left **201 MB under the final name** | redoes everything (10.6 s); `reput` resumed the 300 MB file in 0.6 s, but only for a file known to be partial |
| bsdtar piped into ssh tar | 928 complete, **1 truncated file under the final name** | redoes everything: 33.6 s |
| batch (scp of the tar) | `<id>.tar.part` of 261 MB in staging, nothing in `BASE_PATH` | re-uploads that batch only (2.1 s); finished batches are skipped by the diff |
| batch commit killed (promote prototype, two kills at 2.5 s and 5.5 s) | 713 and 2,100 files live, **0 wrong-size files under a final name**; one run left `*.tar`, `*.sums` and an extracted batch directory in staging | next run diffs again and uploads only the 4,450 missing files; 7.3 s |
| rsync (reference, inside the container, throttled and killed) | 297 complete, no temp file visible, no partial under final name | transferred only the missing 1,703; a no-op run printed 0 lines |

So `scp` and `sftp` write straight into the destination name and are unsafe as the last step; rsync and the staging-then-rename
batch are safe. rsync would be the better tool if it existed on dev; the batch method reproduces its important properties (temp name,
rename, skip what is there) in about 120 lines of Python (`scratchpad/s4/promote.py`).

**pure-Python SFTP (paramiko, asyncssh):** not importable, and nothing may be installed into `.venv311` for this spike. On paper it
would remove the ssh.exe stdin cap, give per-file `posix-rename` and a single long session, but it adds a dependency chain
(`cryptography`, `bcrypt`, `PyNaCl`) to the venv that this project's notes call fragile, SFTP has no hash verb (verification would
still need a remote shell), and per-file SFTP round trips cost what `sftp -b` already shows (about 164 files/s). Not recommended unless
the tar batch proves insufficient.

## Q2: atomicity and "row never visible without its file"

Contract for the promote (P3), in this order, each step idempotent:

1. **Manifest.** From the promotable rows: `filename`, `size` (stat of the dev file), `content_hash` (the column; NULL means hash the
   file; a mismatch between the column and the dev file is a dev-side error that aborts the run before anything is sent).
2. **Upload** batches to `STAGING/<runid>_<n>.tar.part`, `mv` to `.tar` (so a complete upload is recognisable), extract to a batch
   directory.
3. **Verify** the batch: `sha256sum -c --quiet` over a sums file built from the manifest (covers size). Any failure aborts the whole
   batch before a single rename.
4. **Commit** each verified file with `mv -T staging/x BASE_PATH/x` (rename(2), atomic when both are on one filesystem; the prototype
   confirmed both dirs report the same device, and P3 must assert `stat -c %d` equality at start). The backend sees the old file or the
   new complete file, never a partial one. For a changed file this atomically replaces it.
5. **Final check** after all batches: a fresh remote listing must contain every manifest name with the manifest size. Only then does P2
   insert or update rows. If any step fails, no rows are written and the next run resumes from the diff.
6. **Retire** (after P2 has deleted or flagged the rows on prod, in a later step): orphans are moved to `RETIRED/<date>/`, purged after
   14 days by an allow-listed gate verb. Order is the mirror image of adding: rows first, files last.

Orphan detection: the remote listing covers flat regular non-hidden files in `BASE_PATH` only (`find -maxdepth 1 -type f ! -name '.*'`);
names not in the manifest are orphans. Prototype: hidden entries and dot-directories on prod were ignored; five dropped manifest
entries were quarantined; a manifest containing only 18 of 7,013 files was **refused** by the guard (more than 2 percent of prod would
be retired; `--force-orphans` required). That guard is the protection against an empty or truncated manifest, which would otherwise
quarantine the whole corpus. Cost of a quarantine instead of `rm`: disk for 14 days of retirements.

Changed file (same `filename`, new `content_hash`; `fix_image_formats` converts WebP content in place and may keep the name, or
renames): size-changed files are found by the size diff (2 of 4 mutated files in the prototype), same-size content changes only by a
hash comparison. Therefore dev keeps a ledger `{filename: content_hash}` of what the last successful run pushed, and a manifest hash
different from the ledger forces a re-send even at equal size; the remote `--deep` audit (hash every file) is the backstop, run after any
`fix_image_formats` or recut job, and monthly otherwise (prototype: 2 same-size changes detected, 0.54 s for 7,013 files). A renamed
file is simply a new name plus an orphan. Between the file replace and P2's row update the prod row carries the old `content_hash`
for a few seconds; the hash is not used for serving, but P2 must update it in the same transaction as the rest of the row.

## Q3: manifest and the cost of learning prod's state (32,000 synthetic files, 6.9 GB, container)

| Operation (over ssh, warm cache) | Time | Output |
|---|---|---|
| `find -maxdepth 1 -type f -printf '%P\t%s\n'` | **0.18 s** | 0.60 MB, 32,000 lines |
| `ls -l` | 0.20 s | 1.82 MB (needs parsing; fine for busybox without `-printf`) |
| `stat -c '%n\t%s' *` | 0.21 s | 0.60 MB (fails at ARG_MAX on a larger folder; avoid) |
| `sha256sum` of all files, one process | 5.3 s for 6.9 GB = 1.3 GB/s | |
| same, `xargs -P4` | 1.4 s = 4.8 GB/s | |
| diff of 32,500 manifest vs 31,850 remote entries (Python dicts) | **2.7 ms** | 800 missing, 200 changed, 150 orphan |
| local scandir + stat, 2,000 files; local Python sha256, 430 MB | 3 ms; 0.34 s = 1.27 GB/s | |

The hash timings are warm page cache on a fast CPU and say little about a VPS. Extrapolation for general (3.3 GB): at a cold-disk 100
to 500 MB/s the full remote hash is 7 to 33 s, on a 50 MB/s disk 66 s; metal (1.9 GB) and it (121 MB) scale linearly. That is cheap
enough for a deep audit but it also reads the whole disk, so it is not run on every promote. The routine run costs one listing plus
one diff: well under a second plus ssh start (about 60 ms per connection, 119 ms first).

Manifest format (JSON lines, or TSV when handed to the remote shell): `filename<TAB>size<TAB>sha256`. Real size for general:
about 32k lines, 3.6 MB as JSON, compresses to 0.3 percent as text, so it can travel over stdin to the remote commit script even at
the slow ssh.exe stdin rate (0.3 s). File names are the join key and are unique on prod (flat directory); the manifest builder must
reject duplicates differing only by case or Unicode normalisation (NFC vs NFD names were distinct files in the test).

## Q4: reverse direction (prod to dev), file part of P4

Dev collisions: the uploads route stores a new file under `sanitize_filename`, takes the next free `stem_1`, `stem_2`... suffix
(`_candidate_names` in `Backend/app/services/inbox_upload_service.py`), refuses content that is already in the library or inbox
(status `duplicate`), publishes through a `.uploading` staging directory with a no-clobber link/rename. Bug reports are named
`<UTC timestamp>_<uuid><ext>` (`Backend/app/api/bug_reports.py`) and cannot collide in practice; they land in `BUG_REPORTS_PATH` or
`BASE_PATH/bug_reports`.

Prototype `pull.py` (100-file batches): list prod's flat non-hidden regular files (hidden files and the `.uploading` directory are
skipped, so a half-written upload is never read); remote `sha256sum`; pull the batch with `ssh host "xargs -0 tar -cf -"` read by
Python `tarfile` in stream mode; compare each file's sha256 with prod's; publish into a dev inbox with the same naming rule
(same name and same sha = already received, same name and different sha = next `_n` suffix); record `name@sha -> final name` in a
ledger; only then delete on prod with a per-file re-check (`sha256sum` on prod must still equal the received hash, so a file
replaced meanwhile survives). Results: 284 inbox files (including Cyrillic and emoji names, a hidden file and a `.uploading/half.part`)
were pulled in 2.6 s; the collision `collide_diff.jpg` (different content) became `collide_diff_1.jpg`, `collide_same.jpg` (same
content) was not duplicated yet was deleted on prod; hidden entries stayed untouched; 40 bug reports took 0.5 s. A simulated crash
after the first batch was published but before the delete left all 284 files on prod; the re-run pulled only the 184 not in the
ledger and deleted all 284 (ledger hits are deleted without re-download). A hash mismatch aborts without publishing or deleting.
Remaining dev-side steps are unchanged: the pulled inbox files go through `ingest_hash_dedup`, which dedups by `content_hash`
against `images` anyway.

## Q5: bandwidth for the first seed

Arithmetic with 5 percent protocol overhead (tar blocks, ssh framing); checked only at 10 Mbit/s with `scp -l` (20 MB took 16.8 s =
9.5 Mbit/s effective, so the shaped rate is honoured; the 50 and 100 Mbit/s samples were too short to measure and are not claimed).
JPEG compression was not measured on real memes (forbidden); on synthetic random bytes zlib -6 gave ratio **1.0003** and
`scp -C` slowed the 430 MB copy from 9.9 s to 19.4 s. Random data is incompressible by construction, real JPEG/PNG would gain a
few percent at most (PNG with big flat areas maybe more), so the honest statement is: compression is not worth it by default and a
real corpus could differ slightly; `ssh -C` is off.

| Seed | Size | 10 Mbit/s | 50 Mbit/s | 100 Mbit/s |
|---|---|---|---|---|
| general | 3.3 GB, 32.3k files | about 46 min | about 9.2 min | about 4.6 min |
| metal | 1.9 GB, 18.1k files | about 27 min | about 5.3 min | about 2.7 min |
| it | 121 MB, 1.4k files | about 1.7 min | 20 s | 10 s |
| Daily increment (200 new files, about 20 MB) | | 17 s | 3.4 s | 1.7 s + about 2 s fixed |

Add the CPU side measured on loopback: building tar 0.7 s, extracting 0.7 s, verifying 0.4 s per 430 MB, and about 1.25 ms per
file for the shell rename loop (5,600 files took about 7 s; 32k files about 40 s), so general adds roughly 1.5 minutes of local and
remote work on top of the wire time. The seed is resumable batch by batch, so a 46-minute seed on a bad link survives interruptions;
the 10-minute tool kill on dev does not apply to a detached run but would to a naive background call (see Windows gotchas).

## Q6: credentials, host key, unattended runs

- **ControlMaster is not supported by Windows OpenSSH 9.9** (confirmed: `ControlMaster=yes` and `auto` both print `getsockname failed:
  Not a socket` and exit 127). Do not rely on connection multiplexing. A connection costs about 60 ms here (real WAN: a few round
  trips more), and the batch design needs only about 5 connections per batch plus 2 per run, so use a plain connection per step
  with `ServerAliveInterval 15`; keep a single long session only for the S3 database tunnel (separate key, `ssh -N -L`).
- **Config entry** in the user's `~/.ssh/config` (never in the repo): `Host memes-prod`, `HostName`, `User`, `IdentityFile` pointing
  at a dedicated ed25519 key without agent use (`IdentitiesOnly yes`), `BatchMode yes` so an unattended run fails instead of
  prompting, `StrictHostKeyChecking yes` with the host key pinned in `known_hosts` after a first manual, fingerprint-checked
  connection (the test used `accept-new` only because the host is a throwaway). Windows: key file permissions must be limited to
  the user (OpenSSH refuses a key readable by others); a passphrase-less key is only acceptable with the restrictions below,
  otherwise the Windows `ssh-agent` service holds the unlocked key.
- **Prod user:** a dedicated non-root `memesync` owning only `STAGING`, `RETIRED` and (write access to) `BASE_PATH`, `inbox`,
  `bug_reports`; password locked, key-only. Verified in the container: password auth off, root login off, `AllowUsers`.
- **Forced-command gate** (`scratchpad/s4/gate.sh`, `restrict,command="..."` in `authorized_keys`): tested outcomes were `list`
  allowed (0), `rm -rf /data/memes` denied (126), interactive shell denied (126), port forwarding disabled by `restrict`, `scp -O`
  limited to `scp -t /data/staging/*` (but the container lacked an `scp` binary, so the allowed path failed with "scp: not found";
  a real prod needs `openssh-clients`, which is a reason to prefer the default SFTP mode below). Modern `scp` uses the SFTP subsystem;
  under a forced command sshd hands the gate `SSH_ORIGINAL_COMMAND=internal-sftp`, which the gate maps to `sftp-server`; that works
  (the upload succeeded) but is **not path-restricted**, so confinement rests on unix permissions of the sync user, not on the gate.
  The gate must therefore expose verbs instead of a shell: `list`, `commit <batch>`, `retire`, `pull-list`, `pull-tar`,
  `delete-verified`, each taking NUL-separated names on stdin. A chroot with `internal-sftp` was not tested; it would remove the
  shell and with it the remote sha256 verification, so it is not suitable.
- **No secrets in the repo:** key, host, port and paths come from the user's ssh config and an untracked env file.

## Extrapolation summary (general / metal / it)

| | general | metal | it |
|---|---|---|---|
| Files, bytes | 32.3k, 3.3 GB | 18.1k, 1.9 GB | 1.4k, 121 MB |
| Seed at 50 Mbit/s plus local work | about 11 min | about 6.3 min | about 25 s |
| Routine no-change run (manifest + listing + diff) | under 1 s plus ssh start | same | same |
| Full remote hash audit | 7 to 66 s | 4 to 38 s | 1 to 3 s |

## Design outline

### P3: promote files (dev to prod)

1. Resolve the promotable set (active, unflagged, with file present on dev); stat and hash gaps; abort on any row without a file on dev.
2. Remote handshake: one `list` call returns names and sizes of `BASE_PATH`; assert staging and `BASE_PATH` share a device; sweep
   staging entries older than 6 hours (the kill test left batch debris; sweeping on start is required).
3. Diff into missing, size-changed, ledger-changed (and `--deep` hash-changed) and orphan sets; print counts; `--dry-run` stops here.
4. Send missing and changed in batches (about 150 to 500 MB), commit as above; after each batch update the ledger file atomically.
   Failure of a batch: stop, report, rows untouched. Re-run: the diff skips what is committed.
5. Final listing check, then hand control to P2 for the rows. After P2 reports retired rows, run the retire step (quarantine, 2 percent
   guard, 14-day purge). Output: per-run summary with counts and timings, written to the run record.
6. Resume semantics: safe to kill at any point; worst case re-uploads one batch.

### P4 (files part): pull inbox and bug reports

Run before the promote (S3's order: pull first). Same as `pull.py`: list, hash on prod, stream a tar over ssh stdout, verify,
no-clobber publish with the upload route's suffix rule, ledger, delete on prod only after verification and with a hash re-check.
Bug reports go to `BUG_REPORTS_PATH` unchanged (names are unique). Ledger plus the directories are the whole state; a crash between
publish and delete is repaired by the next run.

## What was NOT verified

- No real network: loopback to a local container, no latency, loss, MTU or throttling; the bandwidth table is arithmetic, with one
  `scp -l` check at 10 Mbit/s. Per-file round trips (sftp, scp -r) would be slower on a real WAN; the batch method is less sensitive.
- No real VPS: prod's OS, filesystem, `tar` flavour (busybox vs GNU), disk speed (hash and rename timings are warm-cache), `sha256sum`
  availability and whether the backend runs in a container (then host paths and permissions differ) are unknown.
- Synthetic files only (random bytes), 2,000 to 32,000 files; no real meme names beyond 18 hand-made names; NTFS case-insensitivity
  and long paths (above 260 characters) not exercised; names with control characters excluded by Windows itself.
- Compression on real JPEG/PNG data; antivirus effects on dev; behaviour while the real backend serves reads during renames
  (rename(2) is atomic, but nothing was read concurrently).
- The forced-command gate was a sketch; sshd on prod may be configured differently (`AllowUsers`, `Subsystem`, `restrict` support).
- WSL rsync, paramiko and other client libraries: not tried (see above). The rsync reference ran inside the container, not from dev.
- Windows gotchas hit: Git Bash puts Git's `ssh.exe` ahead of Windows OpenSSH in `PATH` (use the full path or the PowerShell tool),
  and `tar` there is GNU tar (use `C:\Windows\System32\tar.exe` if bsdtar is meant).

## Consequences

- P3 is a small, dependency-free Python module plus a restricted prod user and gate script; no rsync, no paramiko.
- The first seed is dominated by wire time; incremental promotes are seconds.
- Prod needs disk for `STAGING` (one batch) and `RETIRED` (14 days of retirements) on the same filesystem as `BASE_PATH`.
- The remote shell rename loop (about 1.25 ms per file) is acceptable; a Python helper on prod would be faster but is not assumed.
- Open for the owner: retention for quarantined files, whether the `--deep` audit runs on a schedule, and whether prod's backend runs
  on the host or in a container (affects the sync user's permissions on `BASE_PATH`).
