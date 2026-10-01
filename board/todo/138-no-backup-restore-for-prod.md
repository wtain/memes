# [P0] No backup automation or restore procedure for production DB/images

- **Priority:** P0
- **Area:** Backups/DR
- **GitHub issue:** https://github.com/wtain/memes/issues/138
- **Source:** docs/2026-08-13-ORR.md

## Problem

`Storage/backups/` is a local, gitignored, manually-managed dump location with no documented
schedule, retention, or restore procedure. Image files live on plain filesystem with no
redundancy. There is no tested restore drill for either.

## Why it matters

Once real/public data exists on a VPS, an untested or nonexistent backup strategy means an
outage or disk failure can mean permanent data loss.

## Pointers

- `Storage/backups/` (referenced in `Storage/.gitignore`)
- `docs/audit/audit-2026-06-25.md` — flagged the same gap ~7 weeks ago (Tier 3, still open)

## Scope

Design + implement automated backups (DB dump + image files) for the production deployment,
with a documented and tested restore procedure. Design/requirements TBD — this only tracks
that the work is needed.
