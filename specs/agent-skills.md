# Agent Skills Spec

Two Claude Code slash-command skills for semi-automated curation of the meme database.
Both skills are designed for a human-in-the-loop workflow: the agent does the heavy lifting,
a human reviews and confirms before anything irreversible happens.

---

## Shared Principles

- **Read path**: direct async DB queries (AsyncSessionLocal + ORM), not HTTP.
  Rationale: decisions require embeddings + OCR + descriptions simultaneously; assembling
  that over HTTP would require multiple round-trips and new endpoints.
- **Write path**: HTTP API for DB mutations (`mark_excluded`); direct file edits for YAML rules.
- **Environment**: both skills accept `--env metal|general|it` (maps to env file + backend port).
- **One unit at a time**: duplicate skill processes one cluster per invocation; tag skill
  processes one batch of untagged images per invocation. Both support iterating over all
  remaining work in a single session.

---

## Skill 1: `/review-duplicates`

### Purpose

For each duplicate cluster: decide which images are true duplicates (exclude the worse ones)
vs. meme variants (same template, different caption → keep both).

### Invocation

```
/review-duplicates [--env metal|general|it] [--threshold 0.1] [--limit N_clusters]
```

Default: `--env metal`, `--threshold 0.1` (matches current backend default), `--limit` unbounded
(iterate all clusters).

### Data sources (read from DB)

Per cluster:
- `tmp_clusters`: `cluster_id`, `image_id` membership
- `embeddings`: CLIP 512-dim vector per image (for pairwise cosine distance via pgvector `<=>`)
- `ocr_texts`: all OCR blocks for each image (joined and concatenated)
- `ollama_description`: LLM description (fallback signal, used only when OCR + embeddings
  are ambiguous)
- `image_extras`: current `exclude` flag (skip already-excluded images)

### Decision logic (per cluster)

Signal priority:
1. **OCR text** (primary): if two images have substantially different OCR content, they are
   meme variants — keep both regardless of embedding distance.
2. **Embedding cosine distance** (secondary): low distance (< threshold) + similar/no OCR
   confirms true duplicate.
3. **Ollama description** (tertiary, fallback): used when OCR is absent or ambiguous
   (e.g., both images have no text). Description similarity helps distinguish variant from duplicate.

Outcomes per pair:
- **True duplicate**: keep one, mark the rest excluded.
- **Meme variant**: keep all — do not mark excluded.
- **Unclear**: skip (log as unresolved for human review).

### Quality tiebreaker (which duplicate to keep)

Prefer the member with:
1. Longer / richer OCR text (proxy for resolution/legibility)
2. An Ollama description present
3. If still tied, mark as unclear rather than guessing

### Write path

- Call `PUT /api/images/meme/{id}/mark_excluded` for losers.
- Human can review and undo exclusions in the web UI.
- Print a per-cluster summary: decision, reasoning, which IDs were excluded.
- Clusters where the decision is unclear are written to `logs/agent_unresolved_duplicates.jsonl`
  (one JSON object per line: `{cluster_id, member_ids, reason_skipped}`).

### Session state

Progress is stored in `.agent_state/duplicates_{env}.json`:
```json
{"last_processed_cluster_id": 42, "processed_count": 17, "started_at": "2026-06-25T..."}
```
On resume, the skill reads this file and continues from the next unprocessed cluster.
Stateless re-run (ignore saved state): `--reset` flag.

### Analysis script

`tools/agent_duplicates.py --env metal [--threshold 0.1] [--cluster_id N]`

Outputs JSON:
```json
{
  "cluster_id": 42,
  "members": [
    {
      "id": "abc",
      "ocr_text": "...",
      "has_description": true,
      "description": "...",
      "pairwise_distances": {"def": 0.031, "ghi": 0.089},
      "already_excluded": false
    }
  ]
}
```

Pairwise distances computed in-DB with a single query per cluster using pgvector `<=>`.

---

## Skill 2: `/suggest-tags`

### Purpose

For a batch of untagged images: propose new entries for the YAML rules files so that the
batch pipeline (`build_tags_from_ocr`, `build_tags_from_descriptions`) picks them up on rerun.
The agent suggests rules; a human reviews the YAML diff and approves.

### Invocation

```
/suggest-tags [--env metal|general|it] [--limit 50] [--mode words|concepts|both]
```

Default: `--env metal`, `--limit 50`, `--mode both`.

### Data sources (read from DB + files)

- `ocr_texts`: primary text signal for untagged images
- `ollama_description`: secondary signal (richer semantic content)
- `GET /api/images/{id}/similar`: fetch N nearest neighbors per image; use their existing
  tags as a consistency signal — if K of N neighbors share a tag, it's a candidate for the
  current image too.
- `batch/data/tagging/concepts.{env}.yaml`: existing concept rules (avoid duplicating)
- `batch/data/tagging/tags.{env}.yaml`: existing tag vocabulary

### Modes

| Mode | Output |
|------|--------|
| `words` | New trigger words added to existing concept entries |
| `concepts` | Entirely new concept blocks (name + words + votes) |
| `both` | Both of the above |

**`words` mode** (add to existing concept): agent identifies OCR/description tokens that
clearly match an existing concept but aren't listed as trigger words yet.
Example: concept `metallica` exists; agent finds images with "james hetfield" in OCR that
aren't matched — proposes adding `"james hetfield"` to `metallica.words`.

**`concepts` mode** (new concept block): agent identifies recurring patterns across the batch
that don't map to any existing concept and warrant a new entry.

### Tag inheritance from similar images

For each untagged image, call `GET /api/images/{id}/similar?limit=N` (nearest neighbors).
Default N = 10 (current endpoint default). Collect tags of neighbors. If a tag appears in
≥ 60% of returned neighbors, treat it as a strong candidate signal. The agent uses this to
validate or strengthen a rule suggestion, not as a standalone write — the goal is always a
rule, not a one-off tag.

The 60% threshold is a starting point; agent may note confidence level in output.

**Required API change**: add `limit` query parameter to `GET /api/images/{image_id}/similar`
(default 10, matching current hardcoded behavior).

### Write path

The agent **edits YAML files directly** and prints the diff. Human reviews with `git diff`
before committing. No separate staging file — the edit IS the proposal.

Batch rerun after human approval:
```
python batch/build_tags_from_ocr.py
python batch/build_tags_from_descriptions.py  # if descriptions used as trigger source
```

### tags.yaml vs concepts.yaml routing

- **`concepts.{env}.yaml`**: named entities and topics where a trigger word maps to multiple
  tag dimensions simultaneously (bands, politicians, meme formats, themes). Use for anything
  with a `votes` map producing ≥1 tag key:value pair.
- **`tags.{env}.yaml`**: atomic tag vocabulary — the tag key:value exists, optionally with a
  numeric match threshold. Use for simple membership (e.g., `animal:cat: {}`).

### Analysis script

`tools/agent_untagged.py --env metal [--limit 50]`

Outputs JSON:
```json
[
  {
    "id": "abc",
    "ocr_text": "metallica master of puppets",
    "description": "A meme featuring Metallica album artwork..."
  }
]
```

---

## Required changes to existing code

- `GET /api/images/{image_id}/similar`: add `limit: int = 10` query parameter (default preserves
  current behavior). Thread through to `image_repository.get_similar(limit=limit)`.
- Duplicates API response: expose `cluster_id` in `MemeSearchResponse` items — it is already
  fetched in `get_duplicates_clustered` but dropped before reaching the client. Needed for
  web UI grouping (separate from agent work, but a natural companion change).