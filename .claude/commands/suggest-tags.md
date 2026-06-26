Analyze a batch of untagged images and propose new YAML rule entries for the tagging pipeline.

## Arguments

$ARGUMENTS may contain:
- `--env metal|general|it` (default: metal)
- `--limit N` (default: 50) — number of untagged images to analyze
- `--mode words|concepts|both` (default: both)

## How to run

Parse `$ARGUMENTS`, then fetch the untagged batch:

```bash
cd H:/workspace_sandbox/memes
.venv311/Scripts/python tools/agent_untagged.py --env metal --limit 50
```

This outputs a JSON array: `[{id, filename, ocr_text, description}, ...]`

## Context to load before reasoning

Read these files to understand existing rules and avoid duplicates:
- `batch/data/tagging/concepts.{env}.yaml` — existing concept rules
- `batch/data/tagging/tags.{env}.yaml` — existing tag vocabulary

## Tag inheritance from similar images

For images where OCR/description alone is ambiguous, fetch similar images to see their tags:
```
GET http://127.0.0.1:{port}/api/images/{id}/similar?limit=10
```
Ports: metal=8081, general=8082, it=8083.

Each neighbor in the response includes a `cosineDistance` field (0 = identical, 1 = unrelated).
Use distance-weighted voting rather than a flat frequency threshold:

- Distance < 0.05 → weight 1.0 (near-identical image, strong signal)
- Distance 0.05–0.15 → weight 0.5 (visually similar)
- Distance > 0.15 → weight 0.2 (loosely related, weak signal)

Sum the weights for each tag across all neighbors. If the weighted sum ≥ 1.5 (equivalent to
~2 strong neighbors or ~3 moderate ones), treat it as a candidate signal. Note the total weight
and the closest neighbor distance in your output so confidence is visible.

Use this to validate or strengthen a rule suggestion — the goal is always a rule, not a one-off tag.

## Reasoning

Work through the batch and identify patterns:

### `words` mode — add trigger words to existing concepts
Look for OCR/description tokens that clearly belong to an existing concept but aren't listed
as trigger words. Example: concept `metallica` exists; images contain "james hetfield" in OCR
but that phrase isn't in `metallica.words` → propose adding it.

### `concepts` mode — propose new concept blocks
Identify recurring named entities, topics, or meme formats across the batch that have no
existing concept. Only propose concepts with clear, recurring signal (seen in ≥3 images
or strongly implied by similar-image tag inheritance).

### `both` mode — do both of the above

## YAML format reference

**concepts.{env}.yaml** — use for named entities where one trigger maps to multiple tags:
```yaml
my_new_concept:
  words:
    - trigger phrase one
    - trigger phrase two
  votes:
    category:value: 1.0
    other_category:other_value: 1.0
```

**tags.{env}.yaml** — use for simple atomic tag membership (no multi-tag votes needed):
```yaml
tags:
  category:value: {}
```

## Applying suggestions

Edit the YAML files directly using the Edit tool. Make minimal targeted changes.
Do not reorganize or reformat existing content.

After editing, print a summary of every change made:
- Which file was edited
- What was added and why (which images triggered the suggestion)
- Confidence level (high / medium) and the evidence

The human will review with `git diff` and approve before rerunning the batch.

## Batch rerun (for human reference, do not run automatically)

```bash
cd H:/workspace_sandbox/memes
set DATABASE_URL=...  # from environments/.env.{env}
.venv311/Scripts/python batch/build_tags_from_ocr.py
.venv311/Scripts/python batch/build_tags_from_descriptions.py
```