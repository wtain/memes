# Android album sync into the ingestion inbox: project tracker

Status: approved
Originates from: docs/superpowers/specs/2026-11-08-android-ingestion-sync/README.md

Created 2026-11-08. Each project has its own design spec (written when the project is picked up, named
`YYYY-MM-DD-<name>-design.md` and linked below), then an implementation plan, then implementation. Project status uses the spec
lifecycle values (`draft`, `approved`, `planned`, `implementation`, `done`), with `not started` before a spec exists. Spikes and
deep dives use `open`, `in progress`, `done` or `dropped`.

## Working rules

- **Worktree:** `.claude/worktrees/android-ingestion-sync-epic`, branch `worktree-android-ingestion-sync-epic`. Another epic may be
  running in parallel: sync from local `main` before starting a project (`git merge --ff-only main` in the worktree).
- **Merging to `main` and every database migration need the owner's explicit confirmation**, per project. Nothing is merged or
  migrated otherwise. No migration is expected in this epic.
- **Ambiguities** are brainstormed with the owner, not guessed.
- **Development mode** is chosen per project by size and impact: inline for small, contained changes; subagent-driven for
  multi-task or cross-area work.
- **Review:** one review iteration per project by a fresh subagent whose prompt names the area and axis, saved to
  `docs/superpowers/reviews/`. Axes: backend (security of file handling, correctness against ingestion, maintainability);
  Android (readability, code smells, lifecycle and concurrency, maintainability); security review for the upload path.
- **Ports:** never bind the metal, general or it ports; use a free port for any temporary backend and the `ocrdb_test` database.
- **Live databases:** read-only role only, never handed the main `DATABASE_URL`.

## Projects

| ID | Project | Delivers | Depends on | Status | Spec / plan |
|---|---|---|---|---|---|
| A0 | Epic charter and tracker | This folder | none | done (approved 2026-11-08) | `README.md`, `tracker.md` |
| B1 | Inbox upload endpoint | `POST /api/uploads` writes to `PATH_INGESTION_SOURCE`: filename sanitising and preservation, collision suffix, atomic staging write, per-file outcome (`accepted`, `duplicate`, `rejected`), `INCOMING_PATH` retired, shared schema and generated Kotlin/TS/Python types, `backend_api.md`, tests | S1 | not started | |
| B2 | Ingestion pickup verification | Integration test and doc update proving a file written by the endpoint is picked up by `ingest_hash_dedup` and flows through the tiers; update `docs/batches/use-cases/ingestion.md` and the CLAUDE.md batch notes | B1 | not started | |
| A1 | Android tracker data layer | Local database with per-environment rows (file identity, state `pending`/`uploaded`/`rejected`, reason), per-environment album setting and batch size, repository, unit tests | S2 | not started | |
| A2 | Album selection and scan | Folder picker with persisted permission, per-environment album, Y count and refresh (auto and manual), change-album confirmation that wipes the tracker | A1 | not started | |
| A3 | Sync engine | Batched upload (1 to 50), mark uploaded per successful batch, stop, error handling, mapping of the per-file outcomes into the tracker | A1, B1 | not started | |
| A4 | Sync screen | "X of Y", Sync now / Stop, batch-size setting, refresh button, rejected list, change-album dialog, navigation entry; fits or replaces `UploadScreen` | A2, A3 | not started | |
| E1 | End-to-end check and rollout | Device run against a scratch backend on a free port, rollout note, the owner's go-ahead for applying the backend change to the always-on environments | B2, A4 | not started | |

## Spikes and deep dives

| ID | Question | Status | Outcome |
|---|---|---|---|
| S1 | What feeds the inbox and `INCOMING_PATH` today (per environment, from the `.env` files read-only and the code): is anything else using `incoming/`, are `PATH_INGESTION_SOURCE` and `BASE_PATH` on one volume, can the backend process write to the inbox, does the Docker image see it? Does `ingest_hash_dedup` race with a file being written (confirm the staging-subdirectory design)? | in progress | |
| S2 | Can the Storage Access Framework folder picker select a Samsung Gallery album folder on Android 10 to 14, keep the permission across reboots and list thousands of files fast? What is the stable identity of a file (document URI, MediaStore id, name plus size)? Is MediaStore bucket access a better fit? | in progress | |
| D1 | Popularity on re-upload: block files already in the library at upload time or let `ingest_hash_dedup` count them | done | Block (owner, 2026-11-08): `duplicate`, nothing written |
| D2 | What does the tracker do when a file in the album is later edited, renamed or deleted on the phone (same identity, changed size/mtime)? Proposed: not re-uploaded; deleted files drop out of Y but stay counted in X | open | Decide in the A1 design |

## Order

```
A0 ──► S1 ──► B1 ──► B2 ──┐
  └──► S2 ──► A1 ──► A2 ──┤
                └──► A3 ──┴──► A4 ──► E1
```

S1 and S2 run in parallel. The backend track (S1, B1, B2) and the Android track (S2, A1, A2) are independent until A3, which needs
the B1 response shape; the B1 design fixes that shape first so A3 can start against it.
