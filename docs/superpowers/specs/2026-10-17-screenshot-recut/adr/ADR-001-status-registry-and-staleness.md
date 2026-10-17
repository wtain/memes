# ADR-001: Status registry, `cut_proposed` and staleness

Status: proposed
Originates from: docs/superpowers/specs/2026-10-17-screenshot-recut/README.md (D3 to D6)

## Context

`images.status` is a free `String(20)` with the values `active`, `pending` and `rejected`. Their meaning is defined in several
specs. Repositories default to `status="active"`. The cut image must be hidden until approved, must receive pixel-dependent
processing, and must carry copied derived data that is recomputed later.

## Decisions

1. **New status `cut_proposed`**, not a reuse of `pending`. `pending` belongs to ingestion (`ingest_promote`, `ingest_abort`
   operate on it); sharing it would let an ingestion run promote or abort a cut. Cost: the `--status` choice of the
   pixel-dependent jobs is extended, and queries that mean "not active" are audited.
2. **A status registry**, `docs/image-status.md`, lists every value with meaning, who sets it, who reads it and allowed
   transitions. Other documents link to it and do not redefine statuses. Adding a status requires updating the registry in the
   same change.
3. **Staleness as a boolean column** `stale` (default `false`) on each group 3 derived table, added through a shared mixin.
   A provenance column holding the source id was rejected: the source link lives in `image_cuts`, and a foreign key with
   `ON DELETE SET NULL` would make rows look fresh when the source is removed.
   Refinement (R1 design, 2026-10-17): the column is added only where staleness cannot already be derived. That is four
   tables: `ocr_texts`, `ocr_lemmas`, `image_descriptions`, `tags`. Meme profiles already derive staleness from their input
   fingerprint, and description embeddings, lemmas and feedback hang off a description row that refresh replaces.
4. **A third batch mode, "refresh stale"**, next to full and incremental. It selects images with stale rows through one shared
   helper, deletes those rows, and rewrites them with the job's normal logic. Rewritten rows are fresh by default.

## Alternatives rejected

- Timestamp backdating so that "input newer than output" rules fire: implicit, fragile, only works for jobs using that rule.
- A side table of refresh markers: keeps derived tables untouched but adds a table and a join to every selector.

## Consequences

- Batches learn one concept (stale rows) through a mixin and a helper; the rest of their logic is unchanged.
- A job missing the mode keeps the copied data and is never refreshed. The R1 inventory lists every group 3 table and its job so none is forgotten.
- Hidden-by-default behaviour of `status="active"` queries gives the visibility rule for free.
