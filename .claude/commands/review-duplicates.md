Review one duplicate cluster and mark the lower-quality images as excluded.

## Arguments

$ARGUMENTS may contain:
- `--env metal|general|it` (default: metal)
- `--threshold 0.1` (default: 0.1)
- `--cluster_id N` (default: next unprocessed cluster from state)
- `--reset` to ignore saved state and restart from the first cluster
- `--limit N` to process N clusters in sequence (default: 1)

## How to run

Parse `$ARGUMENTS` for the flags above, then:

```bash
cd H:/workspace_sandbox/memes
.venv311/Scripts/python tools/agent_duplicates.py --env metal [flags...]
```

The script outputs JSON for one cluster to stdout and advances the state file.
If `{"done": true}` is returned, all clusters have been processed — stop.

## Decision process (per cluster)

You will receive a JSON object with `cluster_id` and `members`. Each member has:
- `id`: image UUID
- `ocr_text`: concatenated OCR content
- `already_excluded`: skip these in your reasoning
- `pairwise_distances`: cosine distances to other members (0 = identical, 1 = unrelated)
- `has_description` / `description`: Ollama LLM description

### Signal priority

1. **OCR text first**: compare the OCR text of each pair.
   - Substantially different text → meme variant (same template, different joke) → **keep both, do not exclude either**.
   - Same or very similar text (or both empty) → proceed to step 2.

2. **Embedding distance**: look at `pairwise_distances`.
   - Distance < threshold → likely true duplicate → exclude all but the best.
   - Distance ≥ threshold with similar OCR → unclear, skip and log.

3. **Description (fallback)**: if OCR is absent/ambiguous for all members, compare descriptions to break ties.

### Quality tiebreaker (which duplicate to keep)

Among true duplicates, prefer the member that:
1. Has the longest / richest `ocr_text`
2. Has `has_description: true`
3. If still tied, mark the cluster as unclear rather than guessing

Do **not** exclude the winner. Exclude all others.

### Unclear cases

If you cannot confidently classify a cluster, do not exclude anything.
Append an entry to `logs/agent_unresolved_duplicates.jsonl`:
```json
{"cluster_id": N, "member_ids": [...], "reason_skipped": "brief explanation"}
```

## Applying decisions

For each image to exclude, call:
```
PUT http://127.0.0.1:{port}/api/images/meme/{id}/mark_excluded
```
Ports: metal=8081, general=8082, it=8083.

Use the Bash tool with curl, or WebFetch if available.

## Output

After processing a cluster, print a summary:
- Cluster ID
- Decision: `variant` / `duplicate` / `unclear`
- IDs excluded (if any) and the reason
- Which member was kept and why

Then ask: **"Process next cluster? (yes / stop)"**
If `--limit N` was passed, loop automatically until N clusters are done or `done: true`.