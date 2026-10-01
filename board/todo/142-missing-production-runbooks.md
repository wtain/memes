# [P1] Missing production runbooks (batch crash recovery, stuck ingestion run, DB restore drill)

- **Priority:** P1
- **Area:** Runbooks/process
- **GitHub issue:** https://github.com/wtain/memes/issues/142
- **Source:** docs/2026-08-13-ORR.md

## Problem

`docs/runbooks/` contains exactly one runbook (`ingestion-pipeline.md`, a how-to for a specific
batch flow — not an incident/recovery guide). There is no documented procedure for: a batch job
crashing mid-run, an ingestion run getting stuck, restoring the DB from backup, or general
on-call "what do I do when X breaks."

## Why it matters

Once running unattended on a VPS, someone (possibly not the original developer) needs a
documented first response instead of reading source code under pressure.

## Pointers

- `docs/runbooks/` (only file present)
- `docs/audit/audit-2026-06-25.md` flagged the same absence of a "production runbook" ~7 weeks
  ago

## Scope

Write runbooks for the most likely failure modes. Content/scope of each runbook TBD — this only
tracks that they're needed.
