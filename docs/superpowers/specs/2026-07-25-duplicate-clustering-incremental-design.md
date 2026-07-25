# Incremental, Threshold-Bounded Duplicate Clustering — Design

**Date:** 2026-07-25
**Status:** Draft. Prerequisite for `2026-07-24-ingestion-pipeline-design.md` — both ingestion
stage 2 (in-batch near-dup review) and stage 3 (cross-corpus near-dup review) need a scoped,
cheap way to find candidate duplicate pairs, and the current `rebuild_duplicates.py` can provide
neither: it's whole-corpus, non-incremental, and doesn't filter by distance until a later step.

---

## Motivation

`docs/adr/adr-2026-07-10-tmp-duplicates-fk-index.md` already measured the cost of the current
design in production: on `general`, 22,402 images produced **489,389,056 rows (~84 GB)** in
`tmp_duplicates`, because `create_tmp_duplicates()` does an unconditional all-pairs cross join
(`JOIN image_embeddings ie2 ON true`, no filter) and stores *every* pair regardless of distance —
including the overwhelming majority that are nowhere near duplicates. That ADR explicitly flagged
this as "a separate, larger design question... worth a follow-up ADR if it becomes a recurring pain
point." Ingestion is that recurring pain point: running the current script for every ingestion
batch would mean recomputing and storing pairwise distances for the *entire* corpus every time a
handful of new images arrive.

The user's two requirements — incremental mode, and skip pairs past a distance threshold — turn
out to be the same underlying fix, not two separate ones: replacing the cross join with an
index-assisted nearest-neighbor search naturally bounds both the number of pairs *computed* (not
just stored) and makes "only the new images" a cheap, well-defined scope.

## Current design problems (concrete, from reading the code)

- **O(n²) compute, not just O(n²) storage.** The cross join computes a distance for every pair
  before any filtering happens — a `WHERE distance < threshold` added to the existing query would
  reduce *storage* but not the underlying compute cost, since Postgres still has to evaluate every
  pair to know which to keep.
- **Non-idempotent by construction.** `DROP TABLE IF EXISTS tmp_duplicates` followed by
  `CREATE TABLE tmp_duplicates AS SELECT ...` every run — explicitly called out in CLAUDE.md as the
  one exception to "batch jobs are idempotent." There's no way to add rows incrementally to a table
  that gets dropped every time.
- **Doubled storage from symmetric pairs.** The cross join includes both `(A, B)` and `(B, A)` (and
  `(A, A)` self-pairs, filtered out later by `clusterize.py`'s `image_id1 != image_id2`). Cosine
  distance is symmetric — storing both directions doubles storage for zero additional information.
- **Indexes rebuilt from scratch every run** (on a table that, per the ADR, has taken 7+ hours to
  rebuild once at production scale) — a direct consequence of the DROP/CREATE approach, not
  something incremental inserts into a stable table would need.

---

## Design

### Replace the cross join with an HNSW-assisted per-image KNN lookup

`embeddings` already has an HNSW index (`ix_embeddings_embedding_hnsw_cosine`,
`vector_cosine_ops`). A `LATERAL` join using `ORDER BY ... <=> ... LIMIT k` per probe image uses
that index for approximate nearest-neighbor search instead of a full scan — turning the cost from
O(n²) to roughly O(n·k):

```sql
INSERT INTO tmp_duplicates (image_id1, image_id2, distance, match_source)
SELECT
    LEAST(probe.image_id, nn.image_id)    AS image_id1,
    GREATEST(probe.image_id, nn.image_id) AS image_id2,
    nn.distance,
    nn.match_source
FROM (:probe_set) AS probe(image_id, embedding)  -- see scoping below
CROSS JOIN LATERAL (
    SELECT
        e2.image_id,
        probe.embedding <=> e2.embedding AS distance,
        CASE WHEN i2.status = 'active' THEN 'cross_corpus' ELSE 'in_batch' END AS match_source
    FROM embeddings e2
    JOIN images i2 ON i2.id = e2.image_id
    WHERE e2.image_id != probe.image_id
      AND (:corpus_filter)                 -- see scoping below
    ORDER BY probe.embedding <=> e2.embedding
    LIMIT :k
) nn
WHERE nn.distance < :threshold
ON CONFLICT (image_id1, image_id2) DO NOTHING;
```

`LEAST`/`GREATEST` on the two UUIDs normalizes each pair to a single stored direction, which is
what makes `ON CONFLICT (image_id1, image_id2) DO NOTHING` both meaningful (a real uniqueness
constraint, not an accidental dedup of identical rows) and safe to re-run. `match_source` is a
small addition (see Schema below) added specifically for ingestion's merged review queues — see
`2026-07-24-ingestion-pipeline-design.md`'s decision to merge in-batch and cross-corpus review into
one queue per tier rather than four separate ones; the active-library rebuild caller ignores it
(always `cross_corpus`, since its probe and corpus are both `status = 'active'`).

### Scoping (`probe_set` / `corpus_filter`) — this is what unifies rebuild and both ingestion tiers

The query shape above takes two independent scopes: which images are doing the searching (probe
set) and which images they're allowed to match against (corpus filter). Two callers, three scope
combinations (ingestion's two tiers share the *same* scoping — they differ only in `:threshold`):

| Caller | Probe set | Corpus filter | `:threshold` |
|---|---|---|---|
| **Active-library incremental rebuild** (replaces today's default `rebuild_duplicates.py` run) | Images with no existing `tmp_duplicates` row yet (`NOT EXISTS (SELECT 1 FROM tmp_duplicates WHERE image_id1 = e.image_id OR image_id2 = e.image_id)`) | All images (`status = 'active'`, per the visibility design) | configured default (candidate cutoff) |
| **Ingestion Tier A** (pre-OCR, strong similarity) | `ingestion_batch_id = :batch_id AND status = 'pending'` | `status = 'active' OR (status = 'pending' AND ingestion_batch_id = :batch_id)` — i.e. "the active corpus, plus this image's own batch siblings" | tight (`~0.05`) |
| **Ingestion Tier B** (post-OCR-prepass, loose similarity) | Same probe set as Tier A | Same corpus filter as Tier A | loose (`0.05`–`0.3`) |

A single corpus filter covering both "the active library" and "this image's own batch" is what
makes the merged review queue possible at the query level, not just the UI level — one KNN pass per
probe image already finds both kinds of match, tagged via `match_source` rather than requiring two
separate queries whose results get merged in application code.

This directly replaces what the ingestion draft had originally proposed as two independent
mechanisms — a "scoped cross join" for in-batch dedup and a "similarity search, like the existing
endpoint" for cross-corpus dedup — with one parameterized primitive, called twice (once per tier,
different threshold) rather than four times. Worth calling out as the main payoff of building this
prerequisite rather than letting ingestion special-case its own dedup query.

**Why probing only from the new/pending side is still correct:** when a new image is inserted, an
*existing* active image's true nearest-neighbor set could now include that new image — but since
edges are undirected (union-find in `clusterize.py` doesn't care which side "found" a pair) and the
new image *does* probe outward into the full corpus, the pair gets discovered from the new side
regardless of whether the existing image would also have found it by searching. No need to
re-probe from the existing side.

### Schema change: `tmp_duplicates` becomes a real, persistent table

Currently `create_tmp_duplicates()` defines the table via `CREATE TABLE ... AS SELECT` inside the
batch script itself. Move table creation to an Alembic migration (indexes and FK constraints
created once, not re-added every run) and add the uniqueness constraint the `ON CONFLICT` clause
needs:

```python
op.create_table(
    'tmp_duplicates',
    sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
    sa.Column('image_id1', UUID(as_uuid=True), sa.ForeignKey('images.id', ondelete='CASCADE'), nullable=False),
    sa.Column('image_id2', UUID(as_uuid=True), sa.ForeignKey('images.id', ondelete='CASCADE'), nullable=False),
    sa.Column('distance', sa.Float, nullable=False),
    sa.Column('match_source', sa.String(20), nullable=True),  # 'in_batch' | 'cross_corpus'; null for active-library rebuild rows
    sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
)
op.create_unique_constraint('uq_tmp_duplicates_pair', 'tmp_duplicates', ['image_id1', 'image_id2'])
op.create_index('idx_tmp_duplicates_image_id1', 'tmp_duplicates', ['image_id1'])
op.create_index('idx_tmp_duplicates_image_id2', 'tmp_duplicates', ['image_id2'])
op.create_index('idx_tmp_duplicates_distance', 'tmp_duplicates', ['distance'])
```

This also incidentally fixes the ADR's underlying complaint: the FK-index gotcha was only painful
*because* the table got dropped and recreated (indexes had to be re-added in code every time,
correctly, or cascade deletes stall for hours). A table created once via migration doesn't have
that failure mode — indexes just exist.

### Existing production data (`general`'s ~84 GB table)

Not worth migrating row-by-row into the new shape — `tmp_duplicates` is a derived cache table, not
source data. Simplest and safest path: drop it, apply the migration (fresh, correctly-indexed
table), and run one `--full` rebuild using the new KNN approach — which, being index-assisted
instead of a full cross join, should be dramatically cheaper than the original build, even at full
corpus scope. Explicit go/no-go check before dropping: confirm nothing currently depends on
`tmp_duplicates` content surviving a rebuild (it doesn't — `clusterize.py` fully reconstructs
`tmp_clusters` from it every time already).

### `rebuild_duplicates.py` CLI

```
python -m batch.rebuild_duplicates --env metal              # incremental (default)
python -m batch.rebuild_duplicates --env metal --full        # wipe + rebuild from scratch
python -m batch.rebuild_duplicates --env metal --k 30 --threshold 0.3
```

- `--incremental` is the default going forward (a behavior change from today's only mode, which
  was effectively always "full"); `--full` truncates `tmp_duplicates` and re-probes every active
  image, for cases like changing `k`/`threshold` and wanting the whole table to reflect the new
  values, or periodic audit.
- `k` and `threshold` become tracked config (`settings.duplicates.k`, `settings.duplicates.threshold`
  in `environments/settings.yaml`, per CLAUDE.md's Configuration section) rather than hardcoded
  constants — `clusterize.py`'s `PROXIMITY_THRESHOLD = 0.05` and this new `threshold` (proposed
  default `0.3`) are deliberately two different numbers at two different stages: this one is a
  *candidate* cutoff (cheap, loose, decides what's worth storing at all), `clusterize.py`'s is the
  *confirmed-cluster* cutoff (tighter, decides what's shown as "these are probably duplicates").
  Both should be named distinctly in config to avoid the two thresholds being confused for each
  other during review.
- `k` (candidates considered per probe image, proposed default `20`) is a genuine open tuning
  question — too low risks silently truncating a genuine cluster of more than `k` near-identical
  images (rare but possible for a heavily-reposted meme); too high erodes the compute savings this
  whole design is for. Needs empirical validation against a real corpus, not just a guessed default.

### `clusterize.py`

No logic change needed — it already reads `tmp_duplicates` and applies its own
`PROXIMITY_THRESHOLD`. Two notes: (1) since `tmp_duplicates` pairs are now stored in normalized
`LEAST`/`GREATEST` order, its `image_id1 != image_id2` self-pair filter becomes redundant but
harmless (self-pairs can no longer exist — `probe.image_id != nn.image_id` is already enforced
upstream); leave the filter in place as a harmless belt-and-suspenders check rather than removing
it as part of this change. (2) `clusterize.py` still does a full rebuild of `tmp_clusters` from
whatever's in `tmp_duplicates` each run — that's fine (`tmp_clusters` is small, cluster-membership
rows, not pairwise-distance rows) and out of scope for this spec.

**Ingestion's merged review queues need clusters too, but not `clusterize.py`'s.** Grouping a
tier's pairs into connected components (so a reviewer sees "these 3 images — 2 new, 1 already in
the library — are one cluster" rather than a flat pair list) is a union-find over `tmp_duplicates`
rows, same as `clusterize.py` — but running the *global* `clusterize.py` for every ingestion review
page load would needlessly recompute clusters for the entire active library. Ingestion instead
needs a small, batch-scoped variant: union-find restricted to rows where `image_id1` or `image_id2`
belongs to the current batch (`ingestion_batch_id = :batch_id AND status = 'pending'`), which
transitively pulls in any connected active images without touching unrelated parts of the corpus
graph. Left as an implementation detail for the ingestion spec/build, not this one — the primitive
query (rows touching the batch) is straightforward given the schema above.

---

## Migration / rollout

1. Alembic migration: drop the script-managed `tmp_duplicates`, recreate via proper `op.create_table`
   with the unique constraint and indexes above.
2. Add `settings.duplicates.k` / `settings.duplicates.threshold` to `environments/settings.yaml`
   (+ per-environment overrides if `general`'s larger corpus warrants different tuning).
3. Rewrite `rebuild_duplicates.py`: `--incremental` (default) / `--full`, `--k`, `--threshold` args;
   `LATERAL` KNN query per the scoping table above (active-library case only — ingestion's Tier
   A/B scopes are consumed by the ingestion pipeline itself, calling the same underlying query
   builder with different `probe_set`/`corpus_filter`/`threshold` parameters, not by this script's
   CLI).
4. Update `tests/integration/test_rebuild_duplicates.py` for the new incremental/idempotent
   behavior (re-running with no new images should insert zero rows, not error).
5. One-time: drop `general`'s existing oversized table, run a `--full` rebuild under the new
   design (following the ADR's operational lessons if it's still large — detached process,
   `maintenance_work_mem`, progress monitoring via `pg_stat_progress_create_index` equivalents).
6. Update `CLAUDE.md`: the "Exception: `rebuild_duplicates` drops its table each run" line in the
   batch pipeline section and Key Invariants both need updating — this exception no longer applies
   once incremental mode is the default.

## Open questions

- Exact default for `k` — proposed `20`, needs empirical check against real duplicate cluster sizes
  in an existing environment (e.g. `general`, which already has known heavily-reposted memes).
- Whether `general`'s pre-existing `tmp_duplicates` needs to be dropped by an agent/operator as a
  manual one-time step, or scripted as part of the migration itself (leaning manual + documented,
  since a migration silently dropping 84 GB of data is the kind of thing that should be a visible,
  reviewed action, not implicit in `alembic upgrade head`).

## Out of scope

- Changing `clusterize.py`'s clustering algorithm (union-find) or its own threshold constant.
- A UI for tuning `k`/`threshold` — config-file tuning only, consistent with how `clusterize.py`'s
  existing constant is managed today.
