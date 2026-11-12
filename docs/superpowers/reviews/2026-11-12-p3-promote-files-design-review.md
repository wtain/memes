# Review of the P3 design: promote files, dev to prod

Status: done

Design under review: `docs/superpowers/specs/2026-11-12-environment-data-sync/2026-11-12-p3-promote-files-design.md` (draft), read together with the epic README, the S4 ADR, P2, P4, P5, P6/P7 and S2. Ground truth read: `Backend/app/services/image_store.py`, `Backend/app/api/images.py` (get_image), `Backend/app/services/cache.py`, `Backend/app/api/bug_reports.py`, `Backend/app/services/inbox_upload_service.py`, `batch/utils/image_format_fix.py`, `Dockerfile.backend`. One throwaway Python check on tar hostile members (below). No repo file other than this report was written.

Counts: 1 critical, 5 high, 8 medium, 4 low.

---

## Critical

### C1. The admitted `scp -t` / SFTP is an unrestricted file channel that bypasses every verb, and the backend follows symlinks (stolen key reads the backend container's secrets)

Design sections: "Gate verbs" last paragraph ("the gate admits `scp -t` for that directory only", "SFTP is not path-restricted (S4)... confinement is unix permissions of the memesync user: it owns STAGING and RETIRED, **may write BASE_PATH**"); ADR Q6.

Scenario (needs only the dev ssh key, no database credential):
1. With SFTP as `memesync` the holder can `put`, `rename`, `rm`, `ln -s` anywhere `memesync` can write, including BASE_PATH directly. `receive`, `verify`, `commit` and the quarantine are not on this path at all; the verbs are advisory.
2. `get_image_path()` is `_IMAGES_DIR / filename` with no containment check, and `get_image` returns `FileResponse(path)`, which follows symlinks. The holder does `ln -s /proc/self/environ <existing-name>.jpg` over the file of any existing image (rename over it). `GET /api/images/{id}` then serves the environment of the backend process: `DATABASE_URL` of `backend_app` with its password, the proxy headers setting, anything in `env_file`. The route is public (decision 6). Any file readable by uid 1000 inside the container (the image contains `/app/environments/*.yaml`, the app code) can be read the same way.
3. Same channel: replace any image with other content (defacement under a one-year `immutable` cache), delete the corpus, or fill the disk.

Why it is critical: the S4 point "the gate gives no shell" is true but beside the point; sftp-server as the file owner of the serving directory is stronger than most shells for this threat. It also invalidates the 2 %, `--allow-replace` and quarantine protections, which are all client side.

Suggested change (any one of these, in order of preference):
- **Split the privileges by account.** `memeupload`: sshd `Match User` with `ForceCommand internal-sftp -d /staging -u 077` and `ChrootDirectory` (root-owned dir with one writable subdirectory `incoming/`), no shell, no verbs; it can only write staging. `memesync` (verbs only, no sftp subsystem, `restrict`, `command=`) runs `verify/commit/retire/list/hash`, and is the only account allowed to write BASE_PATH, which is mounted **read-only into the backend container**. S4 rejected chroot because it loses remote hashing; two accounts keep both.
- Backend hardening regardless of transport (small code change, P5a): in `get_image` and friends resolve the path and require `resolved.is_relative_to(_IMAGES_DIR.resolve())` and a regular file (or open with `O_NOFOLLOW`). This closes the symlink class for good, also against a bad file written by any other tool.
- If one account stays: remove write on BASE_PATH from `memesync` and let `commit` run through a root-owned helper via a single `sudoers` rule (`memesync ALL=(memefiles) NOPASSWD: /usr/local/sbin/memesync-commit`), the gate being the only entry.

---

## High

### H1. `receive` extraction is unspecified and name validation alone does not make tar safe

Design sections: Gate verbs (`receive`), Tests item 4.

"`extracts into STAGING/<id>/`, refuses names with `/`, `..` or a leading dot" validates names only. I tested the stdlib behaviour with a hostile PAX archive (members in this order: symlink `a.jpg -> <absolute path outside>`, then a regular file also called `a.jpg`, a hardlink `h.jpg -> a.jpg`, a FIFO `f.jpg`), all four names pass a "no slash, no dot-dot, no leading dot" check:
- `tarfile` with the permissive filter wrote `PWNED` **through the symlink into the outside file**; `h.jpg` was created; the FIFO type is accepted by the format.
- `filter="data"` refused the symlink and the FIFO but still extracted `a.jpg` (regular) and `h.jpg` (hardlink).
GNU tar by default removes an existing entry before creating a regular file, so the same-name trick likely fails there, but which tar the gate uses (GNU, busybox) is not decided, and the behaviours differ. Other cases a name check does not stop: a hardlink member whose link target is an absolute path or a file in BASE_PATH (then `commit`'s `mv` publishes a link to an arbitrary readable file; `verify` passes because the sums file comes from the same client), a FIFO or other special file (`sha256sum -c` on a FIFO blocks the gate forever; the backend would block a worker when serving it), sparse or multi-GB members, a duplicate name inside the archive, more members than the sums list.

Suggested change: the gate validates and extracts with a small, reviewed Python 3 script (stdlib `tarfile`, stream mode; Python 3 exists on every mainstream VPS image) instead of shell tar: accept only `isreg()` members, name regex `^[^/\x00-\x1f.][^/\x00-\x1f]{0,254}$` plus byte length <= 255, no duplicate names, per-member and total size caps, member count cap, no PAX extended-header tricks (`path` overrides are fine because validation runs on the final name), write each file with `O_CREAT|O_EXCL|O_NOFOLLOW` mode 0644 into a freshly created empty batch directory. `commit` must iterate over the **validated list** (not `*`), and refuse if the batch directory contains anything else. Hostile-input tests must include exactly these members. Or remove the problem by dropping tar (see "Simplification assessment").

### H2. The P2 gate is by image id; a dev change between the P3 report and the P2 apply leaves a row without its file

Design sections: "The report P2 consumes", P2 "Files gate", 24 h freshness.

P2 treats an image as shippable if its **id** is in the newest report (up to 24 h old). Dev pipelines run continuously. Sequence: P3 promotes `x.webp.jpg` and writes the report; `fix_image_formats` (or a rename path) then converts or renames the file on dev, changing `images.filename` and/or `content_hash` for the same id (`image_format_fix` returns `new_filename`/`new_content_hash` and the row is updated); P2 reads the current row, finds the id in the report, and commits a row whose `filename` is not on prod. That is exactly the anti-goal "row visible without its file". The report already carries `filename` and `content_hash`; nothing says P2 compares them.

Suggested change: P2 must compare, per image, the report's `(filename, content_hash)` with the dev row it is about to write and skip (and report) on any difference; the report should be bound to a manifest (`manifest_sha256` is there) and the driver should run P3 and P2 back to back so the window is minutes, not hours. Consider a default `--max-report-age` of 2 h.

### H3. "Changed bytes are refused unless `--allow-replace`" collides with how dev really changes files, and the refusal behaviour is undefined

Design section: "Changed files".

`fix_image_formats` / `ingest_validate_formats` change files **in place under the same image id**: WebP content in `x.jpg` is converted to `x.jpg` again (same name, new bytes, new `content_hash`), or a mislabeled file is renamed (new filename, same bytes and hash). The design says "the intended route for changed bytes is a new id", but nothing on dev creates a new id for a conversion. Result: every such image trips `ledger-changed`; the design does not say whether the run then aborts, skips only those files, or exits 0 with a warning. The likely outcomes are bad either way: abort blocks all promotion until the owner passes a flag; skip leaves prod permanently stale and, because P2 digests include `content_hash`/`filename`, P2 keeps planning those images as `changed` and never commits them (H2's gate skips them), so the dry run never goes clean and P6 D2 stays red.
Also, for the rename case the old name becomes an orphan that is **not** in P2's retire list (it is not a retired image), so `retire` demands `--force-orphans` for each of them: the "may be a stray file" escape hatch becomes the normal route.
The immutable-cache argument is real only when the bytes change visually. A WebP to JPEG conversion of the same picture is visually identical; a browser holding the old WebP-in-.jpg still renders it (browsers sniff).

Suggested change: decide the class explicitly. Default: replace is allowed for changed files, counted as its own line in the dry run, and `--apply` shows the number; the cache concern is documented as accepted for conversions. If the owner wants the strict rule, then the refusal must be per file (skip + warn + exit 0 + list), those images must be excluded from the report so P2 skips them cleanly, and P2's plan must carry "superseded filenames" (old filename of a changed image) so `retire` can quarantine rename orphans without `--force-orphans`. A cache-busting alternative with a frontend change is an `?v=<content_hash>` on image URLs; not needed for the first version.

### H4. Every guard (2 %, empty manifest, replace, name rules) is client-side; the gate itself enforces nothing about volume

Design sections: Guards, Gate verbs.

The 2 % rule and `--allow-replace` are code in the dev tool. A stolen key (or a bug in the tool) calls `retire` with all 32,000 names or `commit` with a batch that replaces everything; the gate accepts. Quarantine for 14 days is the only net, and `purge` age is computed by the gate clock, fine, but a mass `retire` hides the whole corpus from the public site immediately.

Suggested change: put the volume limits in the gate: `retire` refuses more than `max(50, 2 %)` of its own current listing per call and a daily cap recorded in a state file; `commit` refuses to replace an existing name unless the batch carries a marker file that the gate counts against a daily replace cap; `receive` caps bytes and members. Add a `restore` verb (move back from `RETIRED/<date>`), otherwise the 14-day safety is a manual root job.

### H5. `retire` trusts the wrong evidence, and "rows first, files last" can be violated

Design section: `retire` flow, step 1.

"Refuse unless P2 has retired the corresponding rows: the tool lists orphans and checks them against the **P2 plan's** retire list." A plan is what P2 intended, not what it committed; P2 commits in batches of 500 and stops on the first failed batch. Running `sync.files retire` after a partial P2 retire quarantines files whose rows still exist on prod. A second hole: "orphan = file on prod not in the current manifest". If an image is flagged on dev after P2 ran (manifest changes between P2 and retire), its file is an orphan whose prod row still exists; only the `--force-orphans` prompt stands between that and a public 404 ("Image file missing").
The "orphans not in the list need `--force-orphans`" flag is also used for the 2 % guard, so one flag lifts two different protections.

Suggested change: P2 writes `runs/<id>/retired.json` (ids and filenames, **after** the delete transaction commits); `retire` quarantines only names in that file, and only if a read-only query on the prod database (the tunnel is open anyway in the driver) confirms no `images` row references the filename. Orphans outside that file are only reported (the F2 warn of P6). Use two flags: `--force-orphans` (quarantine unlisted strays, still requires the DB check) and `--allow-mass-retire` (the 2 % guard), matching P2's flag name.

---

## Medium

### M1. Staging can fill the disk that holds the database

`scp -t` into STAGING has no size cap; the design states STAGING and BASE_PATH share a filesystem, and P5 puts Postgres in a container on the same VPS (probably the same disk). The sweep (6 h) runs only inside `handshake`, which the client controls. Suggested: `ulimit -f` in the gate before `exec scp`/sftp, a cap in `receive`, a minimum-free check in the gate for every verb that writes, and a decision (owner question 3) whether Postgres lives on a separate volume.

### M2. The `scp -t <path>` admission is pattern-matched

ADR: "limited to `scp -t /data/staging/*`". In `sh` `case`, `*` matches `/` and `..`, so `scp -t /data/staging/../memes` passes. The same applies if the admission is a regex. Canonicalise (`realpath -m`) and compare the prefix, or remove the admission by moving the upload to a chrooted sftp account (C1).

### M3. One bad row aborts the whole promote, and one failed commit suppresses the whole report

Step 1 aborts on any content_hash/file mismatch or missing file; step 7 writes the report only if **every** manifest name is on prod. A single dev data error (a file under repair, a stale hash after a manual edit) stops promotion of 32,000 unrelated images, and a transient failure on one batch means P2 gets no report at all, so even the already-verified images are skipped. Suggested: exclude the offending images from the manifest with a clear problem list (threshold e.g. more than 0.5 % aborts, as a tripwire), and write a report of what is verified, marked `complete: false` when something was left out. P2 already skips images that are not listed.

### M4. The dev ledger is the wrong place for the "what is on prod" state

It lives in `Storage/sync-state/<env>/` in the repository tree (a worktree is a different directory, `git clean -x` deletes it), it is not tied to a prod instance (a prod rebuilt from a snapshot or restore would be trusted), and an empty ledger has no defined meaning: all files unchanged (silent same-size drift until `--deep`) or all changed (the H3 refusal fires for the whole corpus on the first run after a lost ledger). Suggested: let the gate keep `name sha256 size` for everything `commit` places (a plain file in a gate-owned directory outside BASE_PATH, appended in the commit, rebuilt by `hash` on `--deep`), and `list` return it. The diff then compares dev's `content_hash` with prod's own record; the dev ledger disappears, resume works from any workstation, and `--deep` verifies record against disk.

### M5. `list` output format breaks on names with tab or newline; `-printf` and NUL handling are not POSIX

`-printf '%P\t%s\n'` is GNU `find`; busybox lacks it (the ADR admits `ls -l` as fallback and the design drops it). Linux allows tab and newline in a name, and "NUL-separated names on stdin" cannot be read in POSIX `sh` with `read` (it needs `xargs -0`, `tr`, or bash). Suggested: state the minimum userland (GNU coreutils, findutils, bash or python3), emit `%s\t%P\0`, and make the gate Python or bash explicitly. Names with newline or tab are rejected at the manifest builder with a clear error.

### M6. Environment selection and concurrency are missing from the verbs

There are three BASE_PATHs and one STAGING; no verb takes an environment; the P5 text says one gate. Two parallel runs (or a double start) interleave `commit` and `handshake`'s sweep. Suggested: `--env` argument validated against a fixed list in the gate (paths from a root-owned config), one `flock` per env in the gate for `receive/commit/retire/purge`, and a dev-side lock file also for the direct commands (today only `sync.run` has one). Batch ids and staging directories include the env.

### M7. Ownership between the backend container and `memesync` is left to P5, but the contract is P3's

Backend runs as uid 1000 (`Dockerfile.backend`); files must be readable by it (mode 0644, forced by the tar mode, but the new `RETIRED` and extraction umask are not stated), directories traversable, and BASE_PATH read-only in the backend (it never writes images on prod; recut cuts and uploads are dev only). Bug-report files are written by uid 1000 and P4's `delete-verified` must delete them as `memesync`: needs a shared group and setgid directory. State the uid/gid/mode table in this design, and add the check to `handshake` (writes and reads a probe, compares uids).

### M8. File name limits differ between NTFS and ext4

NTFS allows 255 UTF-16 units per component; ext4 allows 255 **bytes**. A 100-character Cyrillic or CJK name (200 to 300 bytes) is a valid dev file but fails to extract on prod (ENAMETOOLONG), and the final check then fails forever. `inbox_upload_service` caps new uploads (`_cap_utf8`), older corpus files may not be. Add to the manifest builder: reject or report names over 255 UTF-8 bytes, lone surrogates (cannot be encoded as UTF-8), and bytes outside valid Unicode. The S2 finding (0 missing files) says nothing about this.

---

## Low

- **L1. Windows aliases.** Win32 strips trailing dots and spaces and is case-insensitive: a DB name `a.jpg.` resolves to the dev file `a.jpg`, so two rows can read the same dev file and be named differently on prod; a DB name that differs in case from the on-disk name works on dev and would be a 404 on prod if the tar used the on-disk name (the design uses the DB name, which is right; say so). Manifest builder: use the DB name for the tar entry, reject trailing dot/space and reserved device names, and check `os.path.basename(name) == name`.
- **L2. `mv -T` and `--`.** Say that every `mv`/`sha256sum` call uses `--` and `-T`, since leading `-` names are in the S4 name test; `mv -T` onto an existing directory is refused, which is wanted.
- **L3. Quarantine collisions.** `RETIRED/<date>/` receiving the same name twice in one day overwrites; use `mv -n` with a suffix. `purge` should be limited to directories matching `^[0-9]{4}-[0-9]{2}-[0-9]{2}$`.
- **L4. "Batch ids derived from the manifest slice reuse a half-sent `.tar.part`".** `scp` does not resume; it restarts the file. The sentence suggests a resume feature that does not exist. If the manifest changes between runs, name-sorted slices shift, which is fine only if the id is a hash of the slice contents (say so).

---

## What the dev key can do if stolen (exact list, as designed)

Assuming the design as written (single key, forced-command gate admitting verbs plus `scp -t`/sftp, `memesync` owns STAGING and RETIRED and may write BASE_PATH):

| Capability | Possible | Notes |
|---|---|---|
| Read | everything `memesync` can read through sftp: the whole corpus (public anyway), RETIRED, STAGING, `memesync`'s home including `authorized_keys`, any world-readable file on the VPS (`/etc/passwd`, other users' world-readable configs, compose files if readable). Via C1 symlink: the backend process environment and files inside the container | no database data is reachable by the file key unless a DB credential also exists |
| Write | any name in BASE_PATH (replace image content, plant symlinks, FIFOs), STAGING (disk fill), RETIRED | defacement with a one-year immutable cache |
| Delete | any file in BASE_PATH via sftp `rm`, or via `retire` of all names; `purge` only affects entries older than 14 days (gate clock) | recoverable only if quarantined through `retire` and a restore tool exists (H4) |
| Exfiltrate | corpus (public), the backend env (C1), anything readable | |
| Pivot | `restrict` should block port-forwarding and agent forwarding, but P5 says the DB tunnel uses `memesync` too; if the same key permits `-L` to Postgres, the key plus the `sync_writer` password (which lives in the same dev shell environment) writes every class D table. If a second key does the tunnel, say so | **Owner question 1** |
| Not possible | shell, writing the database with this key alone, touching other users' files, changing sshd or the gate (root-owned) | |

A compromised dev workstation also holds `PROD_SYNC_DATABASE_URL` in its environment and the tunnel key, so it can anyway alter all class D rows and replace the whole shipped corpus. The file key is not the weakest link of the set, but C1 shows it can reach more than D data: the backend's own database credentials and thus the class P tables (reader activity, the only copy). That is what must be closed.

---

## The end-to-end file path (paste-ready explanation)

Is the file promote out of scope? No. P3 is it. The designs are correct but scattered: P2 says "never copies files", the S4 ADR says "P3 ... module", the tracker row names it, the README (the file the owner opens first) names no project and says nothing about files, and `sync.run` appears only in the P6/P7 document. Add this block near the top of the README and as "How the files get to prod" at the head of P3:

```
HOW THE IMAGE FILES REACH PROD (nothing is copied by hand; scp is used only inside the tool)

Who/when: you, on the dev workstation, by hand. `python -m sync.run --env general --apply`
runs: pull -> FILES (this is P3) -> data (P2) -> retire -> verify. Or P3 alone:
`python -m sync.files promote --env general --apply`. Dry run is the default.

 dev workstation                       ssh/scp over the internet                      prod VPS (memesync gate)
 ---------------                       --------------------------                     -------------------------
 1 read active, unflagged rows
   -> manifest {name,size,sha256}
 2 handshake + list  ------------------------------------------------------------>  names+sizes of BASE_PATH
 3 diff in memory: missing / changed / orphans (guards: 2 %, empty manifest)
 4 per batch (150-500 MB):
   pack tar -> scp ---------------------------------------------------------------> STAGING/<id>.tar.part
   receive / verify (sha256 of every file against the manifest) ------------------>  extract, check, nothing visible yet
   commit ---------------------------------------------------------------------->  rename(2) into BASE_PATH (file appears whole)
 5 final listing check, write files-promoted.json   (the report)
 6 P2 reads the report, then writes the database ROWS (only for verified files)
 7 later, rows of retired images are deleted by P2; then `files retire` moves their
   files to RETIRED/<date>/ (kept 14 days, never rm)
Rule: file first, row second when adding; row first, file last when removing.
A killed run is repaired by running it again; first seed of general: about 11 min at 50 Mbit/s.
```

---

## Simplification assessment

Is tar over scp through a gate justified? On S4's numbers, only partly, and it buys the largest attack surface of the design.

- What tar buys: speed for the first seed. 2,000 files / 430 MB: batch 3.9 s versus `sftp -b` 10.6 to 17.2 s; 5,000 tiny files: 1.2 s versus 30.5 s. At 50 Mbit/s the wire is 9 minutes for general; the difference between tar and sftp is about 2 minutes of CPU and per-file acks on a ~11-minute seed. A routine run moves tens to a few hundred files (daily increment 200 files, 20 MB): sftp at 164 files/s is about 1.2 s plus handshake, indistinguishable from tar. The ssh.exe stdin cap (12.5 MB/s) does not apply to sftp or scp.
- What tar costs: the `receive` extraction in the gate (H1), the batch planner, the `.tar.part` / `.tar` lifecycle, double disk use in staging (tar plus extracted copy), and the determinism bookkeeping.
- Atomicity does not come from tar. It comes from staging plus `rename(2)` after a hash check, which works for individually uploaded files.

What I would simplify: upload files individually with `sftp -b` (quoted names, one `put` per file into `incoming/<runid>/`, many per connection, pipelined) through a chrooted `internal-sftp` account that can only reach `incoming/` (C1), then the gate verbs `verify` (sha256 of each file against the sums list, which also checks the directory contains exactly those files and only regular files) and `commit` (rename, record hash). Drop `receive`, drop the tar builder, keep batches only as commit chunks (so resume and progress stay). Keep tar for the **pull** direction (P4 `pull-tar`), where it is the fast direction on Windows, the content is generated by the prod side, and the client does the validation (client-side stream extraction with the `data` filter, regular files only). If the owner prefers to keep tar for the seed speed, do it with the Python validator of H1 and do the first seed with a manual one-off.

Other simplifications: remove the dev-side ledger (M4); one flag name per protection (H5); report with `complete: false` instead of all-or-nothing (M3).

---

## Questions only the owner can answer

1. **Threat model for a stolen dev workstation.** The workstation also holds the DB sync credentials and the tunnel key. Do you want the file key to be limited to staging (two accounts, my C1 option 1), or is "whoever has the dev machine can change prod" an accepted trade-off, in which case C1 reduces to the backend symlink hardening?
2. **Same key for the database tunnel and the file gate?** (decides whether the pivot row above exists.)
3. **Disk layout on the VPS:** are Postgres and the image files on one filesystem? Is a separate volume for the database acceptable?
4. **Changed files (H3):** is it acceptable that a browser keeps the old bytes of an image whose file was converted in place (same picture)? If not, do you want the `?v=<hash>` cache-busting route in the frontend as a P5 item?
5. **Quarantine:** 14 days correct? Who restores, and should the tool have a `restore` verb (my suggestion)?
6. **Transport simplification:** keep tar for the seed speed, or switch to per-file sftp into a chrooted staging account (my recommendation)?
7. **Who runs it and when:** confirm that the owner runs `sync.run` by hand from the workstation, and that no scheduled file promote on dev is wanted (the README says manual; the P3 design is silent).

## Questions an implementer would otherwise have to guess

- What is `PROMOTABLE_IMAGES_SQL`'s output exactly, and does the manifest trust `content_hash` or re-hash every file on every run (3.3 GB read on general; cold NTFS roughly 20 to 60 s)? The design says NULL is hashed and a mismatch aborts; is the mismatch check done for all rows or only NULLs?
- Which `tar`, `sha256sum`, `find`, shell and Python the gate may assume on the VPS (M5); where the gate's config (paths per env) lives and who owns it.
- Behaviour when `changed` files exist and `--allow-replace` is absent: abort, skip, or exit 0 with warning (H3). Exit codes for each guard.
- Exact uid/gid/mode of BASE_PATH, STAGING, RETIRED, the hashes file (M7).
- Whether the report is written when `--batch-mb` limited the run, and what `--limit`-like partial runs mean for it (`complete` flag, M3).
- Whether `retire` runs from `sync.run` only with `--retire`, and what it does when P2 skipped retire (P2 `--retire` is optional; the manifest-based orphan list would then show every retired file as an orphan, and the 2 % guard decides).
- How the first seed interacts with batches: P2 `--first-seed` needs the files; is it enough that the whole corpus is in the report, or must P3 report "complete" for the seed?
- Maximum member count and byte size per batch; how the sums file is encoded (names with backslash or leading space in `sha256sum -c` format, which uses escapes).

---

## What I tried to break and could not

- **Atomic publication of a partial file.** The design (`.part`, rename inside the same filesystem, `mv -T`, handshake device check) holds on paper and matches the S4 kill tests (0 wrong-size files under a final name). I could not construct a partially visible file from a kill at any step.
- **Empty or truncated manifest.** The two guards (manifest below half of prod's listing, empty manifest) plus the orphan percentage stop it before any verb runs. The weakness is only that they are client-side (H4).
- **Case and normalisation duplicates.** NFC/NFD pairs and case pairs are rejected by the manifest builder; on NTFS case pairs cannot exist anyway, NFC/NFD can. Names with `&;$\`'`, leading `-`, spaces, Cyrillic, emoji travelled intact in S4 and never touch a command line (stdin, tar). I found no argument-injection route through names provided the gate does not `eval`, interpolate names into `sh -c`, or `read` without `-r`; the Python validator suggested in H1 removes the class.
- **Hidden files and subdirectories.** `list` is `-maxdepth 1 -type f ! -name '.*'`, `.comics-build-*` and `.uploading` entries on dev are never promotable (extract skips them); `ingest` and recut/comics files are flat in BASE_PATH. I found no path by which a subdirectory file is promoted.
- **Recut cuts.** A cut is a new image row with a new id and a new filename, so promotion treats it as new; the pre-cuts copy goes to `BASE_PATH/pre-cuts/` (a subdirectory, never promoted). No conflict with the "bytes under an id must not change" rule, except through fix_image_formats (H3).
- **Interplay of adding order.** Add: files first, report, rows last. I could not produce a row without a file from a crash of P3 or P2 alone; the only routes are H2 (dev changes between report and rows) and H5 (retire evidence). The reverse (file without row) is by design harmless (orphan) and visible to P6 F2.
- **Disk-full during commit.** `mv` inside one filesystem needs no new blocks; the staging double-copy is the exposure, covered by the handshake free-space check and M1.
- **Tar absolute names, `..`, devices.** A name validator refuses these; the unprivileged `memesync` cannot create device nodes (FIFOs it can, see H1).
