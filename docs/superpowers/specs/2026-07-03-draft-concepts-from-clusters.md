# draft_concepts_from_clusters: Turn Top Lemma Clusters into Draft Concept Entries

**Date:** 2026-07-03
**Status:** Proposed
**Scope:** new `batch/draft_concepts_from_clusters.py`

---

## Summary

`build_lemma_clusters` (see `2026-07-01-build-lemma-clusters.md`) produces a YAML report of semantically-grouped unmatched OCR lemmas, ranked by `total_frequency` per language. Reviewing and hand-writing a concept entry for each promising cluster is manual, repetitive work. This batch takes the top N clusters for one language of one such report and appends draft concept entries to `batch/data/tagging/concepts.<env>.yaml`, plus matching tag declarations to `batch/data/tagging/tags.<env>.yaml`, so a human reviews/edits/renames the drafts in place (via `git diff`) rather than starting from a blank page.

---

## Background and Motivation

The new concept-tagging design (`rules/concept_tagger.py`, see `batch/rules_engine.md`) loads two files per profile:

- `concepts.<profile>.yaml` — concept blocks (`words:`, optional `fuzzy:`, `votes: {tag_key:tag_value: weight}`)
- `tags.<profile>.yaml` — the registry of *declared* tags; a vote in `concepts.yaml` referencing an undeclared tag is a **load error**

Manually reviewing the `general` environment's `ru` cluster output already produced 12 hand-curated concept entries (`sleep`, `family`, `purchase`, `singing`, `death`, `russian`, `memory`, `medics`, `scream`, `showing`, `tea`, `ending` — see uncommitted changes to both files at the time of writing). This confirms the target format and naming style: short English mnemonic keys, a single `тема:<canonical-word>` vote per new concept, and content that may deviate from the raw cluster (a human adds/removes words with judgment a script can't replicate). This batch automates producing the *first draft* of that same shape — narrowing the manual work from "write a concept from scratch" to "edit/prune a proposed one."

---

## Design Decisions

| Question | Decision |
|----------|----------|
| Target files | The new-design pair only: `concepts.<env>.yaml` + `tags.<env>.yaml`. The legacy `batch/data/rules.<env>.json` (old `rules/engine.py` format) is untouched. |
| Input | A `build_lemma_clusters` output YAML (explicit `--cluster-file` path — no fixed naming convention exists for that output today) |
| Scope of "top N" | One language at a time (`--language`, required). No cross-language merging — matches how `build_lemma_clusters` output and review already happen per language. |
| Ranking | Cluster file's own order (already `total_frequency`-descending per `build_lemma_clusters`). Singletons are never eligible — only entries under `languages.<language>.clusters`. |
| Concept key | Slugified `ollama_concept` (lowercase, non-alphanumeric → `_`, collapsed/stripped). Falls back to the cluster's top lemma if `ollama_concept` is null/empty/all-punctuation. Matches the observed manual-curation style (English mnemonic keys like `sleep`, `purchase`). |
| Concept key collisions | If the derived key already exists as a concept key (in the file, or already generated earlier in the same run), disambiguate with a numeric suffix (`_2`, `_3`, ...) rather than skipping — the *content* may still be new even if the name collides. |
| "Already covered" check | A cluster is skipped (not just renamed) if its **top lemma** (the cluster's highest-frequency member — already first in `members` insertion order per `build_lemma_clusters`) already appears in the `words:` list of *any* existing concept. Checking only the top lemma (not every member) avoids false positives, since word reuse across unrelated concepts (homonyms) is normal and expected in this codebase. |
| Backfill on skip | Skipped clusters don't count toward N — keep walking down the ranked list until N *new* concepts are added or the cluster list is exhausted. |
| Default vote | Each new concept gets exactly one vote: `тема:<top_lemma>: 1.0`. Choosing a *different* tag category, or additional votes, is left to human review — the script never invents a tag taxonomy beyond the existing generic `тема` convention already used for the large majority of entries in both files. |
| Tag registration | If `тема:<top_lemma>` is not already declared in `tags.<env>.yaml`, append `  тема:<top_lemma>: {}` to it — required, or the new concept's vote would fail to load (see Background). If already declared, skip (no duplicate key). |
| Write strategy | Plain text append to both files (not a full YAML re-dump). Existing content/comments/ordering are untouched — matches the project's existing `suggest-tags` command convention ("Make minimal targeted changes. Do not reorganize or reformat existing content."). Parsing (`yaml.safe_load`) is only used for the collision/declared-tag checks. |
| Review mechanism | None built in beyond the files themselves — `git diff` on the two modified files is the review step, consistent with `suggest-tags.md`'s existing "human reviews with `git diff` and approves before rerunning the batch" convention. No `--dry-run` flag (would duplicate what `git diff` already gives for free). |
| New dependencies | None. Reuses `yaml.safe_load` (already used by `build_bow.py`, `build_lemma_clusters.py`, `rules/concept_tagger.py`). |

---

## Non-Goals

- Writing to the legacy `batch/data/rules.<env>.json` / `rules/engine.py` format.
- Choosing a tag category other than the generic `тема` convention, or proposing multiple votes per concept — that judgment is left to human review, per the spec's own "Human Review Workflow" precedent in `2026-07-01-build-lemma-clusters.md`.
- Consuming or writing the `decision:` field annotation workflow described in that same spec — this batch is a separate, more direct "draft now, edit in place" path, not the "annotate then apply" path.
- Curating cluster membership (dropping noise words, adding related words not in the cluster) — a human does this after the draft lands, as already demonstrated by the manual entries in Background.
- Cross-language merging of top-N selection.
- Modifying `golden.<profile>.yaml` or running `eval_rules.py` — out of scope for drafting.

---

## New Components

### `batch/draft_concepts_from_clusters.py`

Main entry point. CLI via `argparse` (not env vars — this is an ad hoc review-prep tool, not a pipeline stage run from `.env` files).

**CLI:**

```bash
python -m batch.draft_concepts_from_clusters \
  --cluster-file batch/output/lemma_clusters.general.ru.leaf.yaml \
  --env general \
  --language ru \
  --top 10
```

| Argument | Required | Default | Description |
|---|---|---|---|
| `--cluster-file` | yes | — | Path to a `build_lemma_clusters` output YAML |
| `--env` | yes | — | `general` / `metal` / `it` — resolves to `batch/data/tagging/{tags,concepts}.<env>.yaml` |
| `--language` | yes | — | Which `languages.<language>` block in the cluster file to draw from |
| `--top` | no | `10` | Number of *new* concepts to add (post-collision-filtering) |

**Algorithm:**

1. Load the cluster file (`yaml.safe_load`). Fail immediately with a clear error if the file is missing or the requested `--language` key is absent from `languages`.
2. Load `concepts.<env>.yaml` and `tags.<env>.yaml`. Fail immediately with a clear error if either file is missing (both must already exist — this batch only appends).
3. Build `existing_words: set[str]` — every entry in every existing concept's `words:` list, plus every `word` field from every `fuzzy:` entry (case-as-is, no re-lemmatization needed since cluster members are already lemmas). Both count as "already covered" — a lemma matched only via `fuzzy:` is still handled by an existing concept.
4. Build `existing_keys: set[str]` — every top-level key in `concepts.yaml`.
5. Build `declared_tags: set[str]` — every key under `tags.yaml`'s `tags:` mapping.
6. Walk `languages.<language>.clusters` in order. For each cluster:
   - `top_lemma = next(iter(cluster["members"]))` (first key = highest frequency, per `build_lemma_clusters`'s insertion-order guarantee).
   - If `top_lemma in existing_words` → skip (record as "already covered by an existing concept"), continue to the next cluster without counting it toward N.
   - Otherwise, accept the cluster. Derive `key = slugify(cluster["ollama_concept"]) or slugify(top_lemma)`; if `key in existing_keys` (including keys already generated earlier this run), disambiguate: try `key_2`, `key_3`, ... until free.
   - Record `(cluster, key, top_lemma)` as accepted; add `key` to `existing_keys`.
   - Stop once `--top` clusters are accepted, or the cluster list is exhausted (report the shortfall if fewer than N were available).
7. For each accepted `(cluster, key, top_lemma)`, in acceptance order:
   - Append to `concepts.<env>.yaml`:
     ```yaml
     # Ollama suggested: "<ollama_concept>" (freq=<total_frequency>, size=<size>, from build_lemma_clusters)
     <key>:
       words:
       - <member_1>
       - <member_2>
       ...
       votes:
         тема:<top_lemma>: 1.0
     ```
     (If `ollama_concept` is null, the comment reads `# from build_lemma_clusters (no Ollama name)`.)
   - If `тема:<top_lemma>` not in `declared_tags`: append `  тема:<top_lemma>: {}` to `tags.<env>.yaml`'s `tags:` mapping (indented to match existing entries) and add it to `declared_tags` so a later cluster in the same run sharing the same top lemma doesn't double-declare it.
8. Print a summary:
   - For each concept added: key, `тема` vote value, member count, source `ollama_concept` name.
   - For each cluster skipped: which lemma already existed and (if determinable) which concept it's already under.
   - Final tally: `Added N concepts (M clusters skipped as already covered) to concepts.<env>.yaml and tags.<env>.yaml`. If fewer than the requested `--top` were available, say so explicitly.

**Slugification:**

```python
import re

def slugify(name: str | None) -> str:
    if not name:
        return ""
    text = name.strip().strip('"').strip("'").lower()
    text = re.sub(r'[^a-z0-9Ѐ-ӿ]+', '_', text)  # keep latin, digits, Cyrillic
    text = re.sub(r'_+', '_', text).strip('_')
    return text
```

Examples: `"Local Dialect Words"` → `local_dialect_words`; `Memes` → `memes`; `null`/`""` → `""` (triggers top-lemma fallback).

**File append mechanics:** both files are opened, read as raw text, and new content is written with `open(path, "a", encoding="utf-8")`. Since neither file currently ends with a trailing newline (confirmed for both `tags.general.yaml` and `concepts.general.yaml`), each append starts with `"\n"` before its own content to avoid gluing onto the last existing line.

---

## Output Example

Given the `ru` cluster file's top cluster (`#1`, `ollama_concept: "Sleeping"`, `total_frequency: 640`, members starting with `спать`), and assuming `спать` is not yet in any existing concept's `words:`:

**Appended to `concepts.general.yaml`:**
```yaml
# Ollama suggested: "Sleeping" (freq=640, size=29, from build_lemma_clusters)
sleeping:
  words:
  - спать
  - сон
  - спа
  - уснуть
  - выспаться
  - засыпать
  - заснуть
  - сонный
  - переспать
  - поспать
  - папить
  - спиться
  - проспать
  - еспать
  - спид
  - сонно
  - высыпать
  - поспа
  - выспався
  - высыпаться
  - эеспать
  - сыпать
  - постельный
  - сыпаться
  - спатьба
  - бессонный
  - отоспаться
  - уняться
  - посыпать
  votes:
    тема:спать: 1.0
```

**Appended to `tags.general.yaml`:**
```yaml
  тема:спать: {}
```

(If `спать` had already been covered — as it in fact currently is, by the manually-added `sleep` concept — this cluster would instead be skipped and the next-ranked cluster considered.)

---

## Error Handling and Edge Cases

| Case | Behaviour |
|---|---|
| `--cluster-file` missing | Fail immediately with a clear error |
| `--language` not present in cluster file's `languages` mapping | Fail immediately with a clear error (unlike `build_lemma_clusters`, there's no sensible "skip" here — the caller asked for one specific language) |
| `concepts.<env>.yaml` or `tags.<env>.yaml` missing | Fail immediately with a clear error — this batch only appends to existing files, it does not scaffold new profiles |
| Fewer than `--top` non-colliding clusters available | Add as many as possible, report the shortfall in the summary (not an error) |
| `ollama_concept` is `null` | Fall back to the top lemma for the key; comment notes "no Ollama name" |
| Derived key collides with an existing or just-generated key | Disambiguate with a numeric suffix (`_2`, `_3`, ...) |
| `тема:<top_lemma>` already declared in `tags.yaml` | Don't re-declare; just use it in the new concept's vote |
| A cluster's top lemma already appears in some existing concept's `words:` | Skip the cluster entirely (doesn't count toward N), log which lemma/why |
| Cluster's `members` dict is empty | Not possible per `build_lemma_clusters`'s contract (every cluster has ≥ `min_cluster_size` members); no special-case needed |
| `--top` is `0` or negative | Nothing added, no error — the summary reports "0 concepts added" |

---

## Testing

Unit tests (no DB, no I/O beyond temp files), following the existing `tests/batch/` conventions:

- `slugify()`: quoted/unquoted names, multi-word names, Cyrillic names, `None`/empty → `""`.
- Collision detection: cluster whose top lemma is already covered → skipped; cluster whose top lemma is new → accepted.
- Top-N selection with skip-and-backfill: verify N accepted clusters are returned even when earlier-ranked ones are skipped, and that the count is honest when fewer than N are available.
- Key disambiguation: two accepted clusters that slugify to the same key get `_2` suffix on the second.
- End-to-end file append: run against temp copies of a small `concepts.yaml`/`tags.yaml`/cluster-file fixture, then `yaml.safe_load` the results to confirm they still parse and contain exactly the expected new keys — plus a byte-level check that pre-existing content is untouched (matches the "don't reformat" requirement).

---

## Reuse and Refactoring

No existing utilities are extracted or changed. This script is self-contained; it reads `build_lemma_clusters`' output format (already stable) and writes in the format already established by `rules/concept_tagger.py`'s loader and the existing hand-written `concepts.general.yaml`/`tags.general.yaml` content. No changes to `rules/concept_tagger.py`, `build_lemma_clusters.py`, or the legacy rules engine.
