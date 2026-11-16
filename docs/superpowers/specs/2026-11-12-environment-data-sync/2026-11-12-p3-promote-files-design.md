# P3: promote files, dev to prod (design)

Status: implementation (built on branch worktree-env-data-sync, unmerged, no real VPS, see tracker.md "Built state" 2026-11-16; text of the design: revised after design review 2026-11-12; reconciled with `interfaces.md` 2026-11-12; revised again for decisions 26 (dry-run report) and 27 (no removals for now) on 2026-11-12)
Interfaces: state files, settings, accounts, directories, gate verbs, flags and exit codes are defined in `interfaces.md`; this document owns the gate program and the file flows, not the names.
Originates from: docs/superpowers/specs/2026-11-12-environment-data-sync/tracker.md
Depends on: P1 (registry, built), S4 (`docs/adr/adr-2026-11-12-dev-prod-file-sync.md`), decisions log in `README.md` (decisions 17 to 21 shaped the first revision; 26 and 27 the second).
Pairs with: P2 (consumes `files-promoted.json`), P4 (shares the transport and the gate), P5 (provisions the accounts, directories, mounts and the backend change).
Review: `docs/superpowers/reviews/2026-11-12-p3-promote-files-design-review.md` (disposition at the end of this file).

## Intent

Put the image files of the promotable images on prod's disk, verified and atomically visible, before P2 writes their rows. The dry run is a **report** of the files that would be added and the files that would be overwritten (decision 26, `interfaces.md` k).
**Nothing is removed from prod, for now (decision 27):** files that dev no longer promotes stay in the serving directory and are only reported as left behind; the quarantine, restore and purge machinery is kept as design text in the last section. Resumable, safe to kill at any point,
and unable to touch anything on prod except the directories it owns, even with a stolen key.

## HOW THE FILES GET TO PROD (plain language)

Nothing is copied by hand. You run one command on the dev workstation, by hand, when you decide to roll out (no schedule, nothing runs by itself):
`python -m sync.run --env general --apply` does pull, then FILES (this project), then data (P2), then verify. The files step alone is
`python -m sync.files promote --env general --apply`. Without `--apply` it is a dry run that changes nothing and writes a **report** (what would be added, what would be overwritten, what is left behind, how long and how many bytes, and the exact command to apply); you read it first. Each environment (it, metal, general)
has its own stack on the VPS, its own images folder and its own keys, so the same command with `--env metal` talks to a different place.

```
 dev workstation (your PC)                       internet (ssh)                         VPS, stack of ONE environment
 -------------------------                       --------------                         -----------------------------
 0 open the ssh tunnel from the ssh alias, read credentials from environments/.env.<env>.sync,
   IDENTITY HANDSHAKE: the gate and the database both say "I am general"; the tool compares with --env, else stops
 1 read the active, unflagged rows (dev, read only) -> manifest {id, name, size, sha256}
   problem files (bad names, missing, wrong hash) are left out and listed, not fatal
 2 ask the gate (verb account) for the listing:  ----- list ----------------------->  names, sizes, sha256 the gate recorded when it placed them
 3 compare in memory: missing / changed / unverified / orphans (orphans only reported)   (guards: empty manifest, 50 % shrink)
   DRY RUN STOPS HERE: report.json + report.md (added, overwritten, unchanged, skipped with reason, left behind, estimate, plan hash)
 4 per batch (150 to 500 MB), tar built on dev:
   upload the tar  (UPLOAD account, sftp only, ----->  incoming/<batch>.tar.part, then renamed .tar. This account is jailed
   chrooted, no shell, cannot see the images)           in that folder: it can reach nothing else
   receive  (verb account, gate verb)  ----------->  gate COPIES the tar out of the jail, extracts it with its own strict
                                                         Python checker (regular files only, names checked, size caps)
                                                         into a private batch folder. Nothing is visible to readers yet.
   verify   (every file's sha256 vs manifest) ---->  one mismatch throws the whole batch away
   commit   ------------------------------------->  rename(2) of each file into the images folder: a file appears whole or not at all
 5 fresh listing check, then write the REPORT files-promoted.json (every image whose file is verified on prod)
 6 P2 reads the report and writes the database ROWS, only for images whose id, filename, size and hash all match the report
 7 (nothing is removed for now, decision 27: a file dev no longer promotes stays on prod and is reported as left behind)
Rule: file first, row second when adding. (Row first, file last when removing applies once removals are built.)
A killed run is repaired by running it again. First seed of general: about 11 min at 50 Mbit/s.
```

Two prod accounts per environment, because uploading and publishing are different powers: `memeup-<env>` can only drop tars into a jailed folder; `memesync-<env>` can run the gate verbs
and nothing else (no sftp, no shell; its separate tunnel key only forwards to that environment's Postgres). Neither has a database password. The backend container sees the
images folder read-only, so even a bad file can only be read, never written, through the website.

## Goals / non-goals / anti-goals

- Goals: a manifest from the promotable rows; a cheap incremental diff against what prod's own record says it holds; a dry-run report of added and overwritten files that the apply reproduces (plan hash); batched upload with verification; an atomic
  publish per file (`rename(2)`); a `files-promoted.json` P2 can trust field by field; one transport abstraction shared with P4, testable without ssh.
- Non-goals: database rows (P2), the pull of bug reports (P4), creating prod users or directories (P5), a Windows rsync, compression, any removal of files from prod (deferred, decision 27).
- Anti-goals: a row visible without its file; a partial file under its final name; a file copied from a directory; an empty or truncated manifest
  overwriting or hiding anything on prod; any key that gives a shell, a write path into the images folder other than the validated one, or a link/special file in it.

## Components

```
sync/files/manifest.py   promotable rows -> {filename: (id, size, sha256)} + problem list; name rules (below)
sync/files/diff.py       manifest x remote listing/record -> missing / changed / unverified / orphan sets (pure)
sync/files/batches.py    plans batches (150-500 MB, name-sorted, id = hash of env + slice), builds the tar (stdlib tarfile, PAX, UTF-8 names)
sync/files/transport.py  Transport protocol: SshTransport (Windows OpenSSH ssh.exe + sftp, ssh alias from config), LocalTransport (tests)
sync/files/promote.py    the command (below); the report section is built with sync/report.py (SC, interfaces.md k)
deploy/prod/memesync-gate.py   forced command of the verb key; Python 3 stdlib only; one reviewed file; the environment is its first argument (verbs: `interfaces.md` g)
deploy/prod/gate.conf.example  template of `/etc/memes/gate.conf` (root-owned): per environment BASE_PATH, work dir, retired dir, caps
```

There is no dev-side ledger any more: what prod holds is recorded by the gate (review M4). `sync/files/*` may use the standard library and
`psycopg2` (dev read only for the promotable rows; no prod database access in the built tool, the prod row check belongs to the deferred retire). Registry core stays stdlib-only (P1 test).
Location of `deploy/prod/` is a choice to check at implementation.

## Connection, identity, accounts (decisions 17, 18, 21)

- Config: the shared modules of work item SC (`sync/config.py`, `sync/tunnel.py`, `sync/connect.py`, built by P2-T2/T3). Ssh aliases are tracked settings `sync.prod.gate_alias` (`memes-prod-<env>-sync`, verbs) and
  `sync.prod.upload_alias` (`memes-prod-<env>-up`, sftp upload); the tunnel alias is `sync.prod.ssh_alias`. ssh keys live in the user's ssh config, never in the repo and never in an agent session.
  Secrets are only in `environments/.env.<env>.sync` (database passwords). No `PROD_*` variables are read. BatchMode, `ExitOnForwardFailure`, full path to `sync.ssh_bin` and `sync.sftp_bin`.
- Identity handshake first (`interfaces.md` e, step H6 for the gate): `handshake` returns `environment`, the gate version and device ids; the database steps (name, role,
  `memes.environment`, capabilities) are `sync/connect.py`. Any mismatch with `--env` exits 2 and nothing else runs.
- Prod accounts per environment: `memeup-<env>` (sshd `Match User`: `ChrootDirectory` `/srv/memes/<env>/staging`, `ForceCommand internal-sftp -u 007`,
  `AllowTcpForwarding no`, `X11Forwarding no`, `PermitTTY no`; the only writable place is `incoming/`) and `memesync-<env>` holding two keys: the verb key
  `restrict,command="/usr/bin/python3 /opt/memes/memesync-gate.py <env>"` (no sftp subsystem; the environment is fixed by the key line, not by the client) and the tunnel key
  `restrict,port-forwarding,permitopen="127.0.0.1:<that env's Postgres port>",command="/bin/false"`; a file key never reaches Postgres and the tunnel key never runs a verb
  (answers review question 2). There is no shared `memesync` user and no `memesync-gate.sh`.
- Directory contract (P5 creates it, `handshake` probes it; table in `interfaces.md` d): BASE_PATH is `/srv/memes/<env>/images`, owner `memesync-<env>`, directories 2755, files 0644; the backend
  container (uid 1000) reads through "other" and mounts it `:ro`. `/srv/memes/<env>/work` (batches, record, flock) and `/srv/memes/<env>/retired` are owned by
  `memesync-<env>`, mode 0700, on the **same device as BASE_PATH** (handshake exits 3 otherwise). `staging/incoming` is owned `memeup-<env>:memesync-<env>` mode 2770
  (the verb account reads and deletes, the upload account writes). `BUG_REPORTS_PATH` is outside BASE_PATH (P4/P5: shared group, setgid) so the read-only
  mount does not break bug reports.

## Commands

`python -m sync.files promote --env {metal,general,it} [--apply] [--plan-hash H] [--deep] [--batch-mb N] [--top N]`

Dry run is the default and produces the report (below). Exit codes and shared flags: `interfaces.md` (section i); `--run-id` and `--json PATH` as everywhere. The state-directory `lock` file serialises direct commands (`sync.run` takes the same lock and passes it down).
The `retire` and `restore` subcommands and the flags `--force-orphans`, `--allow-mass-retire N`, `--retired`, `--max-retired-age`, `--date`, `--names-file` are deferred (decision 27; design text in the last section).

### Gate verbs (the entire remote surface of the verb account)

The verbs marked L in `interfaces.md` g run under `flock` of the environment (receive, commit, sweep, hash, delete-verified): two runs never interleave. Names arrive NUL-separated
on stdin and are parsed by the Python gate, never by a shell; every filesystem call is made with a validated final name relative to a directory fd.
Minimum userland on the VPS: Python 3.9 or newer and nothing else (no tar, find or sha256sum is called).

| Verb | Does | Notes |
|---|---|---|
| `handshake` | prints environment, gate version, device ids, free disk, uid/mode probe result | exit 3 if work dir and BASE_PATH are on different devices, free space below the floor (10 %), or the backend uid cannot read a probe file |
| `list` | prints `type TAB size TAB sha256 TAB nlink TAB uid TAB mode TAB name NUL` for every entry directly in BASE_PATH (hash from the gate's record `work/record.tsv`; empty if unrecorded) | flat only; `type` `f` is a regular file, anything else (link, directory, fifo, hidden, ...) is listed with its type: promote and retire only use `f` with `nlink 1` and report the rest as anomalies (P6 check F4); names with tab, newline or non-UTF-8 bytes are `invalid` entries, never silently dropped |
| `hash [--names]` | recomputes sha256 of the named files (or all) and rewrites their record entries | used by `--deep`; the way the record is rebuilt after a restore of prod |
| `receive <id>` | copies `incoming/<id>.tar` into the work dir (the upload account can no longer change it), extracts with the strict extractor below into a new empty `work/<id>/`, writes the validated name list | caps below; refuses if `work/<id>` exists |
| `verify <id>` | checks sha256 and size of every extracted file against the sums sent on stdin (names escaped in a private, NUL-delimited format), and that the directory holds exactly the validated list | any difference deletes the batch and exits 1 |
| `commit <id>` | for each name of the validated list: `os.replace` from `work/<id>/` into BASE_PATH, append `name sha256 size` to the record under the lock, then removes the batch | the only operation that publishes; iterates the validated list, never a glob; counts replaced names (cap below) |
| `sweep` | deletes `work/*` batches and `incoming/*` older than 6 h | also run by a P5 systemd timer, so staging is bounded without the client |

The other verbs of the same file follow the same rules (flock where marked, name validation, no shell): `pull-list`, `pull-tar`, `delete-verified` (P4, bug reports only), `fetch-backup`, `disk`, `containment` (P5; the last two print files written by a root timer, the gate never touches docker). The full verb table with owner designs is `interfaces.md` g.

**Strict extractor (review H1).** `receive` opens the copied tar in stream mode and accepts a member only if all hold: `isreg()` (no symlink, hardlink,
device, FIFO, directory, sparse); the final name (after PAX `path` overrides) matches `^[^/\\\x00-\x1f.][^/\\\x00-\x1f]{0,254}$` and is at most 255 UTF-8 bytes, not
a duplicate within the archive, not a reserved Windows name or trailing dot/space; size at most `max_member_mb` (default 200); member count at most
`max_members` (default 5000); total at most `max_batch_mb` (default 600). The file is written with `os.open(..., O_CREAT|O_EXCL|O_WRONLY|O_NOFOLLOW, 0o644)` into the empty batch
directory by name only; any violation aborts the whole batch before `commit` and exits 1. The same Python checker is unit-tested with the hostile archive of the review
(symlink to an outside path, same name regular file after it, hardlink, FIFO, absolute and `..` names, duplicate names, oversize).

**Volume limits in the gate (review H4).** `commit` counts names it replaces (name already in BASE_PATH with a different hash; counter in `work/counters.json`) and refuses beyond `max(200, 5 %)` per day (new names are not capped:
a seed is all new). The `retire`/`restore` caps (`max(50, 2 %)` per call, `max(100, 5 %)` per day) are deferred with those verbs. A legitimate larger run is lifted by the owner on the VPS by editing `/etc/memes/gate.conf` for that day (a root-owned file), never by a client flag, so a
stolen key cannot lift it. The client reads the caps in `handshake` and its dry run says in advance when a cap will trip.

## `promote` flow

1. **Promotable set** from dev through `PROMOTABLE_IMAGES_SQL` (read only, columns id, filename, content_hash). Resolve each file in dev `BASE_PATH` (flat; subdirectories and
   hidden names are never promoted). The tar entry uses the **database filename**, not the on-disk spelling (a case difference on NTFS is a warning). Rows with a problem are
   **left out and listed** (review M3, M8, L1): file missing; `content_hash` column differing from the file (checked for every file that is packed this run, plus NULL hashes, which are
   hashed; unchanged files are not re-read, `--deep` re-reads all); name rules: no control characters (tab, newline), no trailing dot or space, no leading dot, no Windows reserved
   device name (CON, PRN, AUX, NUL, COM1 to 9, LPT1 to 9, with or without extension), at most 255 UTF-8 bytes, valid Unicode (no lone surrogates), and no two names equal after
   NFC plus case folding (NTFS dev versus ext4 prod). If left-out images exceed 0.5 % of the promotable set the run stops (exit 3, a dev data problem).
2. **Remote state:** `handshake`, then `list` (names, sizes, recorded hashes). The diff is in memory.
3. **Diff:** `missing` (not on prod), `changed` (recorded hash differs from the manifest hash, or the size differs), `unverified` (on prod, no recorded hash, size equal: not trusted,
   re-sent unless `--deep` adopts it by hashing on the gate), `orphans` (on prod, not in the manifest: reported as `left_behind`, nothing is done about them, decision 27). Changed bytes are **always promoted** (decision 20): dev is the truth and
   files are fixed before they become active; the dry run and the report carry them as `replaced`, a warning count. The one-year immutable cache of the image endpoint is accepted;
   a `?v=<content_hash>` cache-bust on image URLs is a backlog note, not part of this project.
4. **Guards (client side, repeated in the gate where it counts):** a manifest smaller than half of prod's listing, or an empty manifest: stop (exit 3 on apply; the dry run reports the guard as tripped), no lifting flag. The orphan-count guard and `--force-orphans` are deferred: orphans are
   only reported, never an error.
5. **Dry run: the report (decision 26, `interfaces.md` k)** is written (`report.json`, `report.md`, stdout summary) and the command stops. Files section content:
   - `added`: files missing on prod: count, bytes, complete image-id list;
   - `overwritten`: files whose bytes change under an existing name: `hash_changed` and `size_changed` counts, bytes, id lists (the `replaced` warning count of the report below); plus the `unverified` files that would be re-sent (probably unchanged);
   - `unchanged`: files already on prod with the manifest hash;
   - `skipped_with_reason`: the problem images (`missing_file`, `name_problem` with the rule, `hash_mismatch`, `normalisation_collision`), counts and ids, and the 0.5 % tripwire decision;
   - `left_behind`: orphans (files on prod not in the manifest; names, since they have no id) and non-regular entries from `list`, informational ("left behind on prod, removals deferred");
   - the guards and the gate caps in force (`handshake`), batches (number, MB), free space on prod, the estimated duration and bytes on the wire, the plan hash and the exact apply command.
   A `sync.run` dry run embeds this as its `files` section. The apply recomputes the plan and refuses (exit 3) when `--plan-hash` differs.
6. **Apply, per batch** (name-sorted; the batch id is a hash of environment plus slice contents, so a changed manifest yields different ids, an unchanged one reproduces them):
   build the tar locally; upload with `sftp -b` to `incoming/<id>.tar.part` and rename to `.tar` (an interrupted upload restarts that file, it does not resume); `receive`;
   `verify` with sums from the manifest; `commit`. A failed verify discards the batch and stops the run (exit 1), nothing visible. A batch is not started if it does not fit in the
   free space.
7. **Final check and report.** A fresh `list` must show every packed name with the manifest hash. The **report** is then written, always, even when some images were left out or
   a batch failed earlier (then `complete: false`); it contains only images verified on prod.
8. A killed run is repaired by the next one: `sweep` clears debris and the diff skips what is committed. Worst case: one batch is re-sent.

### The report P2 consumes

`<state dir>/runs/<runid>/files-promoted.json`, same name and place as before, new fields marked:

```json
{ "run_id": "...", "env": "general", "finished_at": "2026-11-12T20:00:00Z", "gate_version": "2",
  "complete": true,                                   // NEW: false when anything was left out or failed
  "images": [ {"id": "<uuid>", "filename": "...", "size": 123, "content_hash": "<sha256>"} ],
  "replaced": 12,                                     // NEW: files whose bytes changed under an existing name (warning, not a block)
  "left_out": [ {"id": "<uuid>", "filename": "...", "reason": "name_trailing_dot"} ],   // NEW
  "manifest_sha256": "<hash of the sorted manifest>" }
```

`files-promoted.json` is a state file for P2, not the dry-run report; it is written by an apply only. Contract with P2 (review H2): P2 treats an image as shippable only if the dev row it is about to write has the **same id, filename, size (file) and content_hash** as the report entry; any
difference (for example `fix_image_formats` converted or renamed the file after the report: it changes the filename and the hash of the same id) skips and reports that image, nothing
is committed for it. The report must be newer than P2's `--max-report-age` (default **2 h**; the driver runs P3 and P2 back to back). P2's `--first-seed` requires
`complete: true`. State directory: `sync.state_dir`, default `Storage/sync-state/<env>/` (git-ignored). `manifest_sha256` is computed by `sync/files/manifest.py: manifest_sha256()` over the sorted `id\tfilename\tcontent_hash` lines of the promotable set as read (left-out images included); P2 imports the same function. There is no dev-side ledger.

## Changed files, case and renames

A file with the same name and a new hash (a WebP converted in place, a recut) is replaced atomically by `commit`; between that moment and P2's row update the prod row still carries
the old `content_hash`, which is not used for serving. A rename (same bytes or not, new filename under the same id) is a new name to promote plus an old name that stays on prod as a left-behind file (reported as such, decision 27; once removals are built it becomes a `superseded`
entry in P2's `retired.json` that `retire` quarantines). P2's digest includes `filename` and `content_hash`, so the next dry run is clean once both sides agree.

## Failure behaviour

| Event | Result |
|---|---|
| ssh drops during upload | `.tar.part` stays; the next run restarts that file; `sweep` clears old ones |
| kill between `receive` and `commit` | work-dir debris, swept after 6 h; no file visible |
| kill during `commit` | some files visible and recorded one by one, no rows yet; the next run finds the rest `missing` |
| `verify` fails or the extractor rejects a member | batch discarded, run exits 1, nothing visible |
| Prod disk below the floor | `handshake` and `receive` refuse; a batch that does not fit is not started |
| Dev file changes during the run | the sums come from the hash taken when the batch was packed; `verify` fails, batch discarded |
| Left-out problem images | run continues, report `complete: false`, P2 skips them |
| Wrong environment on the other end | identity handshake exits 2 before anything is sent |

## What a stolen key can do (after the fix; replaces the review's table)

| Key | Read | Write | Delete | Other |
|---|---|---|---|---|
| Upload key (`memeup-<env>`) | only `incoming/` of one environment | tars into `incoming/` (disk fill bounded by the free-space floor, `sweep`, and a quota P5 sets on that folder) | its own tars | no shell, no forwarding, cannot see or touch the images folder, RETIRED, the record, any database |
| Verb key (`memesync-<env>`) | listing and recorded hashes of that environment | regular, name-validated files into that environment's images folder through `receive`/`commit`, only batches that pass the extractor; replaces capped per day | nothing (no delete verb exists while removals are deferred; a `commit` can replace a file within the daily cap) | no shell, no sftp, no other environment (fixed by the key), no links or special files, no path outside the base; **residual:** within the caps it can still change what the public sees (a day's worth of replaced images), which is the same power the tool legitimately has |
| Tunnel key (`memesync-<env>`) | nothing by itself | nothing | nothing | forwards only to that environment's Postgres port; needs the sync database password (in `.env.<env>.sync` on the same workstation) to do anything |

Read of secrets through the website is closed twice: the gate cannot create a symlink, and the backend refuses image paths outside its base and serves from a read-only mount (P5 change
below). A compromised workstation holds all three keys and the database password and can still change class D data of every environment; that is the accepted owner-level trust
(decision 21 keeps the files out of agent sessions); the split limits a stolen *single* key or a bug in the tool.

## Interface notes for other designs (changes to mirror)

- **P2:** files gate compares id + filename + content_hash (size is checked by P3) with the report; `--max-report-age` default 2 h; `--first-seed` needs `complete: true`; no `retired.json` is written for now (decision 27); P2 builds the shared connection modules and `sync/report.py`.
- **P4:** the gate is `memesync-gate.py` (Python, environment as first argument); P4's verbs go through the same flock and name rules; the upload/pull split is not used for pulls (pulls stay on the verb key).
- **P5:** two accounts per environment plus a tunnel key, chroot and `Match User` stanzas, the directory/uid/mode table (`interfaces.md` d), `sweep` timer, quota on `incoming/`, **BASE_PATH mounted
  read-only in the backend container, `BUG_REPORTS_PATH` outside it, and a backend change: `get_image_path`/`get_image` (and `image_exists`) resolve the path and require it to be a
  regular file inside the resolved images directory (`is_relative_to`; no symlink escape)**; `/etc/memes/gate.conf` root-owned; `fetch-backup`, `disk` and `containment` follow the same rules.
- **Backend (P5a):** the containment check applies on every stage, tested with a symlink pointing outside.

## Tests

1. **Unit:** manifest builder (NFC/NFD, case duplicates, Cyrillic, emoji, NULL hash, missing file, tab/newline, trailing dot, reserved names, 255-byte limit, lone surrogate, 0.5 % tripwire), diff (all
   sets), batch id determinism, tar names, guards, report writing, `files-promoted.json` field checks, name validation, **report checks (decision 26):** completeness (added, overwritten with hash/size split, unchanged, skipped with reason, left behind, guards, estimate, plan hash, apply command), reproducibility (same inputs give the same plan hash; a manifest change between dry run and apply makes `--plan-hash` exit 3), ids and counts only (a sentinel in a filename never reaches `report.json`, `report.md` or stdout except as the name of a left-behind file without an id, which is the one place names appear).
2. **Gate tests** (the gate run as a subprocess against temp directories, Linux CI and Git Bash locally): every verb; the strict extractor with the hostile archive (symlink then same-name
   file, hardlink, FIFO, absolute, `..`, duplicates, oversize, too many members); a tar changed after upload (copy semantics); caps for replace; flock; hidden file ignored; leading `-` and spaces in names; the record rebuilt by `hash`.
3. **LocalTransport integration:** full promote, idempotent re-run, interrupted batch, verify failure, replaced files counted not refused, rename case (old name reported as left behind), orphans reported not an error, truncated manifest refused,
   `complete: false` with left-out images, identity mismatch.
4. **Docker ssh test** (marker `docker`, local only, throwaway container on 127.0.0.1:52222, throwaway keys): two accounts, the chroot, the upload key unable to list or write outside, the verb key
   unable to run sftp, a real promote.
5. **Not covered:** real WAN, real VPS, antivirus on dev, concurrent reads while renames happen (S4 "not verified").

## Work breakdown

| Task | Delivers | Depends |
|---|---|---|
| T1 | `manifest.py` (name rules, problem list), `diff.py` with unit tests | P1 |
| T2 | `batches.py` (planner, tar, hash-based batch ids) with unit tests | T1 |
| T3 | `deploy/prod/memesync-gate.py` (verbs of `interfaces.md` g without the deferred `retire`, `restore`, `purge`), `gate.conf.example`, strict extractor, caps, record and their hostile-input tests | none |
| T4 | `transport.py` (Local and Ssh with sftp upload, ssh aliases from `sync.prod.*`), gate handshake step for `sync/connect.py` | T3, P2-T3 (SC) |
| T5 | `promote` (dry-run report via `sync/report.py`, apply, `files-promoted.json` with `complete`, `replaced`, `left_out`) | T1, T2, T4 |
| T6 | **Deferred (decision 27, not built):** `retire`, `restore`, `purge` using P2's `retired.json` and the prod row check | T5, P2-T9 result format |
| T7 | Docker ssh test with two accounts and chroot, Windows OpenSSH gotchas | T5 |
| T8 | Docs (`docs/sync/files.md` including the VPS cap-lift step; the restore runbook waits for removals), `.gitignore` entry, CLAUDE.md pointer | all |

Size: medium to large. Mode: subagent-driven (T1, T3 in parallel; T2, T4 after; T5 and T7 sequential). One review after T7 by a fresh subagent, axes **security of the gate and the extractor**
(path traversal, links, caps, privilege split, flock) and **correctness against the S4 contract and the P2 report/result contract**. Live prod: never touched in development.

## Decisions taken (owner) and calls made by me

- Owner: tar batches kept, incremental by comparison (decision 19), SFTP only for the jailed upload; changed bytes always promoted (decision 20); credentials only in `.env.<env>.sync`
  (decisions 18, 21); manual use through `sync.run`, no schedule; per-environment stack, keys and gate arguments (decision 17).
- Review simplification question: per-file sftp instead of tar was recommended; the owner kept tar. What keeps it safe: the gate never calls tar (own validator/extractor), the tar is
  copied out of the jail before it is read, the upload account is write-only into a chroot, the extractor creates regular files only with O_EXCL|O_NOFOLLOW under caps, `commit` walks the
  validated list, and the backend cannot follow a link or write. If the extractor ever proves too costly, per-file sftp into `incoming/<id>/` with the same `verify`/`commit` is a drop-in.
- Mine, to confirm: Python 3 stdlib gate; exit codes; default caps (200 MB member, 5000 members, 600 MB batch, replace `max(200, 5 %)` per day, 10 % free floor);
  report freshness 2 h; the 0.5 % tripwire; the gate record replacing the dev ledger; cap lifts by editing gate.conf on the VPS; orphans are reported only and the orphan guard is dropped with the removals.

## Risks and what is not settled

- Gate and chroot behaviour depends on the real sshd (`ChrootDirectory`, `internal-sftp -u`, `restrict`), known only once the VPS exists (P5); the Docker test approximates it.
- Cold-disk timings of `--deep` and the first seed on a real network: measured only on loopback.
- Whether Postgres shares the images filesystem on the VPS is a P5 decision; the free-space floor and the quota are the mitigation either way.

## Deferred phase: removals (not built, decision 27)

Everything below is design text kept for later: it is not in the commands, the gate verbs, the tests or the "to build" lists above. It was written when the retire was in scope and has not been re-reviewed since. Verbs `retire`, `restore`, `purge` are listed as deferred in `interfaces.md` g; their caps are `max(50, 2 %)` per call and `max(100, 5 %)` per day.

Commands (deferred):
`python -m sync.files retire  --env ... [--apply] [--force-orphans] [--allow-mass-retire N] [--retired PATH] [--max-retired-age D]`
`python -m sync.files restore --env ... --date YYYY-MM-DD [--names-file F] [--apply]`
`--force-orphans` lifts only the "unlisted stray file" rule of retire; the 2 % guard has its own flag `--allow-mass-retire N` (N equals the planned count; same meaning as in P2).

Gate verbs (deferred): `retire` (moves the named files to `RETIRED/<date>/`, never over an existing entry: a numeric suffix; drops them from the record; per-call and per-day volume cap; refuses anything that is not a regular file directly in BASE_PATH),
`restore <date>` (moves the named files back if the name is absent; records them; capped like retire; never overwrites), `purge` (deletes `RETIRED/<date>/` directories matching `^[0-9]{4}-[0-9]{2}-[0-9]{2}$` and older than 14 days, gate clock). The orphan-count guard of `promote` (orphans above `max(50, 2 %)` of the prod listing stop the run unless `--force-orphans`) belongs here too.
Stolen verb key, when built: quarantine of up to the daily cap, never `rm`; `restore` and the 14-day window recover it.

### `retire` flow (after P2 has committed the retirement of rows)

Evidence is what P2 **committed**, not what it planned (review H5). P2 writes `<state dir>/runs/<runid>/retired.json` after each committed retire batch and completes it at the end of the retire phase
(fields in `interfaces.md` a: `retired[] {id, filename}`, `superseded[] {id, old_filename, new_filename}`, `blocked[]`). `superseded` lists the old filenames of images whose filename changed in a committed update
(the rename case of `fix_image_formats`; P2 takes them from `committed.json`). **`retired.json` is the only input**: P3 reads this run's file under `sync.run`, else the newest non-dry file of the environment not older
than `--max-retired-age` (default 24 h) unless `--retired PATH` names one. `retire-result.json`, `data-result.json` and `plan.json` are never read. Blocked ids are not candidates.

1. Candidates: names in `retired` and `superseded`, minus any name that is still in the current manifest.
2. **Row check:** through the tunnel, a read-only query on the prod database as `sync_writer` opened `default_transaction_read_only` (`sync_reader` has no `SELECT` on `images.filename`): any candidate filename still referenced by an `images` row is removed
   from the list and reported (rows first, files last cannot be violated by a partial P2 commit).
3. Orphans (on prod, not in the manifest, not in the result) are only reported, as P6's F2 warn. `--force-orphans` quarantines them too, still behind the row check.
4. Guards: client `max(50, 2 %)`, lifted by `--allow-mass-retire N`; the gate enforces its own caps (above).
5. The `retire` verb quarantines; `purge` runs at the end for days older than 14. `restore` is the way back (owner-run, or by P6 runbook).
If P2 ran without `--retire` there is no `retired.json` for the run and `retire` refuses (exit 2); the manifest-based orphan list is never an authority to delete.

Tests that go with it: caps for retire and restore, restore, purge pattern, rename case with retire. Task T6.

## Review disposition

| Finding | Disposition | Where or reason |
|---|---|---|
| C1 stolen key via scp/SFTP, symlink read | adopted | Two accounts, chroot, no sftp for the verb account, no scp admission; backend read-only mount plus path containment (P5); stolen-key table redone |
| H1 tar extraction unsafe | adopted | Strict Python extractor, copy before read, `commit` over validated list, hostile tests |
| H2 gate by id only | adopted | Report contract: id, filename, size, hash all matched by P2; 2 h age; back-to-back driver |
| H3 refuse changed bytes | adopted (owner decision 20) | Always promoted, `replaced` warning count, `superseded` for renames, cache-bust only a backlog note |
| H4 guards only client side | adopted (retire/restore caps deferred, decision 27) | Gate caps (retire, restore, replace, receive), `restore` verb, VPS-side lift |
| H5 retire evidence | adopted; **deferred (decision 27)** | `retired.json` (P2) after each committed retire batch, prod row check, two flags |
| Decision 26 (2026-11-12) | applied | Dry run is the report (promote flow step 5, `interfaces.md` k): added, overwritten (hash/size), unchanged, skipped with reason, left behind, guards, estimate, plan hash, apply command; `--plan-hash` on apply; report tests; `sync/report.py` |
| Decision 27 (2026-11-12) | applied | `retire`, `restore`, `purge`, quarantine, `retired.json` reading, `retired-files.json`, the orphan guard, `--force-orphans`, retire/restore caps and task T6 moved to "Deferred phase: removals"; orphans only reported; H5 (retire evidence) and H4 retire/restore caps deferred |
| Reconciliation 2026-11-12 | applied | `interfaces.md` adopted: connection through SC and `sync.prod.*` settings (no `PROD_SSH_HOST`), accounts `memesync-<env>`/`memeup-<env>`, `retired.json` replaces `retire-result.json`, `list` returns entry types, `--allow-mass-retire N`, directory paths of P5, verbs table shared with P4/P5 |
| M1 staging fills disk | adopted | Free floor, caps, `sweep` timer, quota on `incoming/`; Postgres volume left to P5 |
| M2 `scp -t` pattern admission | adopted | Admission removed; chrooted sftp account |
| M3 one bad row aborts all / all-or-nothing report | adapted | Problem images left out, 0.5 % tripwire, `complete` flag, report always written |
| M4 dev ledger | adopted | Gate record `record.tsv`, returned by `list`, rebuilt by `hash`; `unverified` state |
| M5 list format, POSIX userland | adopted | Python gate, NUL records, tab/newline names rejected in the manifest |
| M6 environment and concurrency | adopted | Environment fixed by key, flock per environment, dev lock file, env in batch ids, identity handshake |
| M7 ownership contract | adopted | uid/gid/mode table, probe in `handshake` |
| M8 name length NTFS vs ext4 | adopted | 255 UTF-8 bytes, lone surrogates, normalisation collisions |
| Simplification question (tar vs sftp) | owner kept tar | Section "Decisions taken"; hardening above |
| Owner questions 1 to 7 | answered | 1 two accounts, 2 separate tunnel key, 3 P5, 4 cache accepted, 5 14 days plus `restore` (deferred, decision 27), 6 tar kept, 7 manual via `sync.run` |

## Deferred (low findings)

- L1 Windows aliases: handled in the manifest builder (rules above); only the implementation detail of the case-warning remains.
- L2 `--`/`-T` for `mv` and `sha256sum`: moot, the Python gate makes no shell calls; the leading `-` name stays in the tests.
- L3 quarantine collisions and purge pattern: handled (numeric suffix, date regex); the suffix format is an implementation detail.
- L4 resume wording: corrected, upload restarts, ids are hashes of the slice contents.
- `?v=<content_hash>` cache-busting on image URLs (decision 20): backlog.
- Open for the owner: none blocking; the default caps are for review.
