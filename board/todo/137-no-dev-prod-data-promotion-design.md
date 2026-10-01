# [P0] No dev→prod data promotion design (which tables sync, direction, partial sync)

- **Priority:** P0
- **Area:** Data lifecycle
- **GitHub issue:** https://github.com/wtain/memes/issues/137
- **Source:** docs/2026-08-13-ORR.md

## Problem

The intended model is: dev environments (metal/general/it) prepare/enrich data offline (OCR,
embeddings, dedup clustering, tagging), and a curated subset syncs to a production DB serving
the public app. No such sync process, tooling, or documentation exists today — three dev DBs
run in isolation with no path to a prod DB at all.

Also flagging: a code check shows embeddings are **not** purely offline/dev-only as might be
assumed — `Backend/app/repositories/image_repository.py` and
`Backend/app/repositories/concept_repository.py` use embeddings at request time (concept-based
search, similarity). Any design here needs to validate which tables are genuinely dev-only
(e.g. `tmp_duplicates`, `tmp_clusters` per `docs/schema.md`) vs. required at prod runtime,
rather than assuming.

## Why it matters

Foundational — without this, there's no way to actually populate a production database with
curated content, and no repeatable way to push new batches of enriched/deduped images live.

## Pointers

- `docs/schema.md` — full table inventory, several `tmp_*` tables that look dev-intermediate
- `CLAUDE.md`'s batch pipeline section — enrichment is entirely offline/manual today, no
  "publish" step

## Scope

Design which tables/rows sync, sync direction and cadence, and how schema migrations stay
coordinated between dev DBs and prod DB. Design/requirements TBD — this only tracks that the
work is needed.
