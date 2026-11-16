# `sync.files promote`: promote image files, dev to prod

Design: `docs/superpowers/specs/2026-11-12-environment-data-sync/2026-11-12-p3-promote-files-design.md`. Flags and exit codes: [commands.md](commands.md). Security of the gate: [security.md](security.md).

Sends the image files of the promotable images (active, unflagged) to the VPS gate: additions and replacements only. Run it before `sync.promote`.

## Flow

1. **Manifest** from the promotable dev rows: id, database filename, size, sha256 (`images.content_hash`; a NULL hash is computed). Problem files are left out and listed, not fatal: file missing, `content_hash`
   differs from the file, a name the VPS filesystem cannot hold (control characters, leading dot, trailing dot or space, Windows reserved names, more than 255 bytes, invalid Unicode, or two names equal after
   NFC plus case folding). If more than 0.5 % of the promotable set is left out, the run stops (exit 3: a dev data problem).
2. **Handshake** (H1; the gate `handshake` verb for H6) then `list` of the files prod's gate records (type, size, recorded sha256, link count, owner, mode, name).
3. **Diff** in memory: `missing`, `changed` (hash or size differs; dev's current bytes always win), `unverified` (on prod without a recorded hash; re-sent unless `--deep` adopts it by hashing on the gate),
   and orphans (on prod, not in the manifest: `left_behind`, never an error). Guards: empty manifest, manifest below half of prod's files, the left-out tripwire, the gate's per-day replace cap, a blocking
   non-regular entry on prod.
4. **Dry run stops here** with the report (counts and bytes, estimate, plan hash).
5. **Apply:** per batch of about `--batch-mb` (150 to 500, default 300) MB: build a tar on dev; upload it as `incoming/<id>.tar.part` through the sftp-only upload account and rename it; the gate
   `receive`s (copies it out of the jail, strict extraction into a private batch directory), `verify` checks every file's sha256 and size against the manifest (one mismatch deletes the whole batch), `commit`
   renames each file into the images directory (`os.replace`) and records its hash. A fresh `list` then checks the result.
6. **Result:** `files-promoted.json` in the apply's run folder lists every image whose file is verified on prod, with `complete`, the left-out images and `manifest_sha256`. `sync.promote` accepts it only if it
   comes from a non-dry run of this environment, is younger than 2 hours and its manifest hash equals the rows it recomputes.

## Resume and repair

Running the command again repairs a killed run: missing files are sent again, committed ones are not. Staging batches older than 6 hours are swept by the gate (`sweep`) and by a VPS timer. `--deep` re-reads every
dev file and has the gate re-hash its files first (slow; after a restore of prod or a doubt about the record).

## Limits and lifting them

The gate refuses beyond 200 MB per file, 5000 files and 600 MB per batch, below 10 % free space on the files volume, and more than `max(200, 5 %)` replaced names per day. A legitimate larger run is allowed by editing
`/etc/memes/gate.conf` on the VPS for that day (never by a client flag), then reverting. The dry run says in advance when a cap will trip.

## Not built

`retire`, `restore` and `purge` (and any removal of files from prod) are deferred (decision 27). A restore runbook for files therefore does not exist yet; the VPS files directory is not backed up (it is rebuilt by this command).
