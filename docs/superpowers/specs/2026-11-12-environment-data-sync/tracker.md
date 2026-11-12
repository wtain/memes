# Dev/prod data sync: project tracker

Status: draft (reconciled 2026-11-12; interface names in `interfaces.md`; decisions 26 and 27 applied 2026-11-12)
Originates from: docs/superpowers/specs/2026-11-12-environment-data-sync/README.md

Created 2026-11-12. Each project has its own design spec (written when the project is picked up, named
`YYYY-MM-DD-<id>-<name>-design.md` and linked below), then an implementation plan, then implementation. Project status uses the spec
lifecycle values (`draft`, `approved`, `planned`, `implementation`, `done`), with `not started` before a spec exists. Spikes use
`open`, `in progress`, `done` or `dropped`.

## Working rules

- **Worktree:** `.claude/worktrees/env-data-sync`, branch `worktree-env-data-sync`, created from local `main` (7da0ff82).
- **Merging to `main` and every database migration need the owner's explicit confirmation**, per project.
- **Ambiguities** are brainstormed with the owner, not guessed.
- **Live databases:** read-only role (`DATABASE_URL_READONLY`) only, run by the controller; never hand a subagent the main
  `DATABASE_URL`. Prod credentials are never written to the repo.
- **Ports:** never bind the metal, general or it ports; use a free port and `ocrdb_test` for any test backend.
- **Review:** one review iteration per project by a fresh subagent, saved to `docs/superpowers/reviews/`.

## Spikes

| ID | Spike | Question answered | Status | Output |
|---|---|---|---|---|
| S1 | Data classification | Which table and disk asset has which origin, and is it needed on prod? | done (draft; all open questions answered 2026-11-12, see the decisions log in README.md) | `2026-11-12-s1-data-classification.md` |
| S2 | Size measurement | Row counts and bytes per table, disk of BASE_PATH, so the prod disk can be sized and the trimming gain is known | done (2026-11-12; read-only role, run by the controller) | `2026-11-12-s2-size-measurement.md` |
| S3 | Sync technique | Per table: `COPY` with upsert over an SSH tunnel vs `pg_dump` subset vs logical replication; index drop/rebuild vs keep, measured; incremental change detection; how to carry deletions | done (2026-11-12; ADR status PROPOSED until the owner confirms; throwaway containers, synthetic data, no real SSH or network) | `docs/adr/adr-2026-11-12-dev-prod-sync-technique.md` |
| S4 | File transfer | rsync (or equivalent) from Windows over SSH; manifest by `content_hash`; resumability; row/file consistency | done (2026-11-12; ADR status PROPOSED; loopback container, synthetic files, no real network or VPS) | `docs/adr/adr-2026-11-12-dev-prod-file-sync.md` |
| S5 | Prod topology and roles | Prod DB roles (sync writer, backend, read-only), which backend features are switched off on prod, how prod is configured per env | done (2026-11-12; read-only code analysis, owner questions answered 2026-11-12) | `2026-11-12-s5-prod-topology-and-roles.md` |

## Projects (designs revised after review 2026-11-12; names fixed in `interfaces.md`)

| ID | Project | Delivers | Depends on | Status | Spec / plan |
|---|---|---|---|---|---|
| P0 | Epic charter, tracker and interface contract | This folder | none | draft (reconciled 2026-11-12) | `README.md`, `tracker.md`, `interfaces.md` |
| P1 | Sync registry | The S1 classification as code (46 tables: 22 D, 6 P, 18 X) plus guard tests | S1 | implementation (built); follow-ups listed in P2 T1 (`digest_exclude`, `image_extras` replace mode, absorptions note). Verified: `pytest tests/sync` 19 passed, `tests/docs` 15 passed, 18 mutations caught. Not verified: CI run | `2026-11-12-p1-sync-registry-design.md`, review `docs/superpowers/reviews/2026-11-12-p1-sync-registry-review.md` |
| SC | Shared modules `sync/config.py`, `sync/tunnel.py`, `sync/connect.py` (+ `sync.doctor`), `sync/report.py` | Settings and `.env.<env>.sync` loader, tool-owned tunnel, identity handshake H1 to H10, connection factories, the report model and writers (`interfaces.md` k) | P1; P5 values (ports, aliases, roles) for the settings only | draft; **owner P2** (tasks P2-T2 and P2-T3), consumed by P3, P4, P6, P7 | `interfaces.md` c and e |
| P2 | Promote data dev to prod | Dry-run-first command with a report (decision 26): dev-origin tables for the active corpus, ids preserved, handshake, digests, guards, `committed.json`; additions and overwrites only (removals deferred, decision 27) | P1, SC, S3 | draft (revised after review 2026-11-12) | `2026-11-12-p2-promote-data-design.md` |
| P3 | Promote files dev to prod | Manifest, gate program `deploy/prod/memesync-gate.py`, batched verified upload, dry-run report (retire/restore/purge deferred, decision 27) | P1, SC, S4 | draft (revised after review 2026-11-12) | `2026-11-12-p3-promote-files-design.md` |
| P4 | Pull reader activity prod to dev | Mirror of the six class P tables, ratings mode and archive, bug-report pull, `pull_state.json` | P1, SC, S3, P3 gate | draft (revised after review 2026-11-12) | `2026-11-12-p4-pull-reader-activity-design.md` |
| P5 | Prod provisioning and hardening | Per-environment stacks, roles, grants, backend stage mechanism, capabilities, web and Android hiding, backups; sub-deliveries P5a backend stage, P5b web, P5c Android, P5d VPS stacks and roles | S2, S5, P1 | draft (revised after review 2026-11-12) | `2026-11-12-p5-prod-provisioning-and-hardening-design.md` |
| P6 | Verification and drift report | Read-only compare of dev-origin data between stages, health, grants, growth alarms | P2, P3, P5 | draft (revised after review 2026-11-12) | `2026-11-12-p6-p7-verification-and-operations-design.md` (Part A) |
| P7 | Runbooks, driver `sync.run`, docs, acceptance run | Runbook per direction, CLAUDE.md and docs updates, acceptance run on a throw-away VPS | all | draft (revised after review 2026-11-12) | `2026-11-12-p6-p7-verification-and-operations-design.md` (Part B) |

## Recommended build order (additions only; removals later, see "Deferred: removals")

1. **T0** (P2): test fixture for two databases or schemas in `ocrdb_test` (also used by P4, P6, P7).
2. **P2-T1**: registry amendment (`digest_exclude`, `image_extras` replace, absorptions note); finishes P1.
3. **Shared connection modules** (SC: P2-T2, P2-T3): `sync/config.py`, `sync/tunnel.py`, `sync/connect.py`, `sync/report.py`, `sync.doctor`, tested with a loopback fake.
4. **P3 manifest and gate (additions)**: P3-T1 to T4 (manifest and diff, batches, the gate program with the strict extractor and without retire/restore/purge, transport), in parallel with P5a.
5. **P2 promote (additions)**: T4 to T8, T10, T11 (the retire task T9 is deferred); then **P4 pull** (T1 to T7, in parallel with P2; P4-T6 bug-report verbs after the gate exists), then P3-T5 (promote command with its report).
6. **P5a** (backend stage mechanism, capabilities, containment) alongside steps 4 and 5, then **P5d** (**P5d-T1/T2** role generator and container verification before P2's role checks are final; **P5d-T3 to T8** compose, Postgres, host users and sshd, backups, release job, once the gate and the roles are settled); **P5b/P5c** after P5a-T4.
7. **P6** (needs P2 digests, P3 transport, P5 roles), then **P7** (driver, runbooks, docs), then the acceptance run on a throw-away VPS, then the real prod (`it`, `metal`, `general`).
8. **Removals later** (not scheduled): see the next section.

## Deferred: removals (decision 27, 2026-11-12)

Nothing is removed from prod for now. Deferred exactly as follows; the design text of each item is kept in a section "Deferred phase: removals (not built, decision 27)" of P2 and P3 (or marked deferred in `interfaces.md`):

- **P2:** the retire phase: `--retire`, `--allow-mass-retire N`, `--max-pull-age`, `retired.json`, the pull precondition, the ratings guard (`meme_text_ratings` column `SELECT`, cascade protection), the mass-retire and combined guards, `VACUUM` after a retire, task T9 and its integration tests.
- **P2 globals:** prune of global sets (`concepts*` replaced as a set; the registry's `prune` field stays in code and P2 ignores it).
- **P3:** `retire`, `restore`, `purge` (commands and gate verbs), quarantine to `RETIRED/<date>/`, `retired-files.json`, the prod row check, the orphan-count guard and `--force-orphans`, the retire/restore gate caps, task T6.
- **Contract:** `interfaces.md` rows for `retired.json`, `retired-files.json`, the gate verbs, the flags in "Nonexistent for now", resolved calls 1 to 3; `retired` directory is provisioned but unused.
- **P4 and P6/P7:** the freshness rule of `pull_state.json` for a retire; the driver's retire and `files-retire` steps; the acceptance drill "a rating made on prod after the snapshot stops the retire"; the `restore` recovery runbook; D2/D5/F2 are informational meanwhile.
- **What must be built later:** all of the above, plus: the report's `removed` category and shrinking `left_behind`, D2/D5/F2 as errors/warnings again, the owner decisions "keep, restore or drop a kept dev rating when its image returns" (item 1 below) and the gate-cap acceptance (item 9), a re-review of the deferred text against the then-current code.
- **Consequences until then:** an image flagged, cut, rejected or deleted on dev stays visible on prod (row and file); a renamed image leaves its old file behind; pull-before-promote stays in `sync.run` but is not a precondition; left-behind rows and files are reported, never an error.

## Open owner items (deduplicated; each changes what is built or blocks)

1. **Ratings:** confirm `sync.pull.ratings_mode: skip` as the default (switch to `mirror` with the ratings routes) and the local archive `ratings_archive.jsonl` for rating text that a pull would delete or overwrite (kept and archived, never restored to prod). Also: keep, restore or drop a kept dev rating when its image returns (P2 review M6).
2. **Absorptions** carry `loser_filename` and `loser_content_hash` of rejected (non-promotable) losers to a public database: accept (default) or blank those two columns for non-promotable losers (conditional transform in the digest).
3. **First-seed resume:** confirm that an interrupted first seed is resumed by running the same command (no wipe mode).
4. **Acceptance environment:** a throw-away VPS, or a local Linux VM first (chroot and sshd behaviour are only fully real on a VPS or a Linux VM).
5. **Hostnames and domain** (`sync.prod.api_url`, Caddy hosts, CORS origins).
6. **Abuse layer:** a CDN or WAF, or the Caddy rate-limit plugin, on top of the app-level limits.
7. **Disk:** XFS project quotas or provider block volumes (otherwise free-space alarms stand in); VPS size (4 GB floor, 8 GB recommended) and provider.
8. **Off-VPS backup target** beyond the dev pull of the newest class P dump.
9. **Gate default caps** (200 MB member, 5000 members, 600 MB batch, 10 % free floor and replace `max(200, 5 %)` per day; the retire/restore caps wait for the removals): accept, and confirm that a larger run lifts them by editing `/etc/memes/gate.conf` on the VPS, never by a client flag.
10. **Dev database names** per environment for `sync.dev.database` (only needed to fill the settings).

## Backlog / unscheduled (real items only; resolved ones were removed in the 2026-11-12 reconciliation)

- P5d-T2: verify on a PostgreSQL 18 container that `MAINTAIN` allows `REINDEX`, that `ON DELETE CASCADE` into `meme_text_ratings` works without a `DELETE` grant for `sync_writer`, the unprivileged index-rebuild path, extension creation rights, PG18 data directory layout and `pg_hba` order, before the roles are final.
- Impression pull by watermark (append-only) with a periodic full mirror, once `meme_impressions` approaches millions of rows (S3 Q7).
- `?v=<content_hash>` cache-busting on image URLs (decision 20).
- Restoring a rating from `ratings_archive.jsonl` is a manual step (no tool yet).
- Windows `ssh.exe` stdin is capped near 12.5 MB/s for pipes; ControlMaster does not work on Windows OpenSSH (S4). WSL rsync, a pure-Python SFTP client and real-network bandwidth were not evaluated.
- Prod `maintenance_work_mem` of 256 MB to 1 GB for HNSW builds (149 s at 64 MB vs 30 s, 43k x 1024d); plain `REINDEX` of an HNSW index blocks readers (rebuild in a window or concurrently).
- Embedding tables carry no model name or digest (only the meme-profile ones do): CLIP/SBERT parity is asserted by the revision check (`memes.revision`), not by data.
- Not verified in S3: a real SSH tunnel and Windows OpenSSH behaviour, network latency, realistic lemma text for the GIN cost, backend latency during a promote (covered by the P7 acceptance run).
- Ratings keyed by `(query_hash, image_id)` move to the cut on a recut approve on dev; a later pull must not resurrect the old pair (P4 ratings rules; revisit when the ratings routes return).
- Per-environment asymmetries: `it` has no tagging vocabulary, profiles or tagging jobs; tagging YAML only for metal and general.
- Tracked evaluation data (`evaluation/gold`) is keyed by dev image ids: dev-only, but the pull of ratings feeds it.
- Follow-ups named by the P5 review: sanitising `tools/discovery_report` output (P5a-T8) and consumers of pulled text; listed in P7's docs task.
