Cluster an environment's unmatched OCR lemmas and draft the top N clusters into new concept/tag entries for human review — chains `build_lemma_clusters` and `draft_concepts_from_clusters`, committing the raw draft before review and again after.

## Arguments

$ARGUMENTS may contain:
- `--env metal|general|it` (default: metal)
- `--language ru|en|es` (default: ru)
- `--top N` (default: 10) — number of new concepts to draft
- `--min-cluster-size N` (default: 2) — HDBSCAN `min_cluster_size` for `build_lemma_clusters`
- `--cluster-selection-method eom|leaf` (default: leaf) — `leaf` reliably avoids the single oversized "catch-all" cluster that `eom` produces on dense, high-volume lemma sets (confirmed empirically on a `general`/`ru` sample: `eom` merged 154 generic words into one blob at the top of the ranking; `leaf` on the same data instead gave 16 clusters, none bigger than 6 members. On the full ~25k-lemma `ru` set, `leaf` produced 1750 clean clusters with no oversized blob at all). Only pass `eom` if you specifically want fewer, larger, more stable clusters.
- `--ollama-model NAME` (default: qwen2) — must already be pulled (`ollama list`; `ollama pull <name>` if missing)

## Prerequisites

Check these before running anything, and stop with a clear message if any are missing:

- `batch/output/bow.unmatched.<env>.json` must already exist — it's produced by `build_bow` (with `RULES_FILE` set) upstream of this command. If it's missing, tell the user to run `build_bow` for `<env>` first.
- `batch/data/tagging/concepts.<env>.yaml` and `batch/data/tagging/tags.<env>.yaml` must already exist — `draft_concepts_from_clusters` only appends to existing files, it never scaffolds a new profile.
- Ollama must be reachable locally with `<ollama-model>` pulled.

## Step 1: Cluster the unmatched lemmas

Parse `$ARGUMENTS`, then read `DATABASE_URL` and `BASE_PATH` from `environments/.env.<env>` (each environment has its own DB config):

```bash
cd H:/workspace_sandbox/memes
DATABASE_URL=$(grep '^DATABASE_URL=' environments/.env.<env> | cut -d= -f2-)
BASE_PATH=$(grep '^BASE_PATH=' environments/.env.<env> | cut -d= -f2-)
```

`DATABASE_URL` is only needed to satisfy an import-time check in `Storage/db.py` — no live DB connection is actually made unless `LOOKUP_CONCEPTS=true`, which this command never sets. It just needs to be a syntactically valid Postgres URL, so reading it from the env file (even if that DB isn't currently reachable) is always safe.

`--env <env>` is required — `build_lemma_clusters.py` calls `config.settings.load_env(args.env)` as the first thing in `main()`, and raises `RuntimeError: No environment selected` if neither `--env` nor `APP_ENV` is set, before any of the CLI flags below are read. Every other setting is passed as an explicit CLI flag, which overrides the tracked-config default for that key once the environment is selected:

```bash
DATABASE_URL="$DATABASE_URL" \
BASE_PATH="$BASE_PATH" \
.venv311/Scripts/python -m batch.build_lemma_clusters \
  --env <env> \
  --bow-unmatched-file batch/output/bow.unmatched.<env>.json \
  --cluster-output-file batch/output/lemma_clusters.<env>.<language>.yaml \
  --language <language> \
  --text-embed-model sbert \
  --ollama-enabled \
  --ollama-model <ollama-model> \
  --min-cluster-size <min-cluster-size> \
  --cluster-selection-method <cluster-selection-method>
```

A full unmatched set is often tens of thousands of lemmas — each one is embedded individually, so this can take several minutes. Run it in the background and wait for completion rather than assuming a short timeout; don't reduce the input size to make it faster, the whole point is to cluster the real backlog.

## Step 2: Draft the top N concepts

```bash
.venv311/Scripts/python -m batch.draft_concepts_from_clusters \
  --cluster-file batch/output/lemma_clusters.<env>.<language>.yaml \
  --env <env> \
  --language <language> \
  --top <top>
```

This appends new concept blocks to `concepts.<env>.yaml` and the matching `тема:<word>` tag declarations to `tags.<env>.yaml`. Clusters whose top lemma is already covered by an existing concept are skipped automatically and backfilled from the next-ranked cluster — read the printed summary (which concepts were added, which clusters were skipped and why) rather than assuming exactly `<top>` were added.

## Step 3: Commit the raw draft — before human review

Commit the drafted entries exactly as generated, before anyone edits them:

```bash
git add batch/data/tagging/concepts.<env>.yaml batch/data/tagging/tags.<env>.yaml
git commit -m "chore: draft <top> concepts from <env>/<language> lemma clusters (auto-generated, pending review)"
```

Committing the unreviewed draft first matters for two reasons: it gives the human a clean baseline to diff their own edits against afterward, and it means the generated draft survives even if the review session gets interrupted before anyone looks at it.

If `git status` shows no changes to these two files (every top-ranked cluster was already covered by an existing concept), say so and stop here — there's nothing to review.

## Step 4: Hand off for human review

Print a summary table — for each concept added: its key, `тема` vote, member count, and the `ollama_concept` name that suggested it (note when Ollama's name looks off; it sometimes hallucinates on garbled/transliterated lemmas, which is normal and exactly what review is for). Then stop and say plainly:

> Drafted N concepts into `concepts.<env>.yaml` / `tags.<env>.yaml`, committed as `<short sha>`. Please review and edit the entries directly — prune bad members, rename a concept key, fix a `тема` value, or discard an entry entirely. Let me know when you're done.

Do not proceed to Step 5 until the human says they're done reviewing — don't guess based on elapsed time or assume silence means approval.

## Step 5: Commit the reviewed result — after human review

Once the human confirms they're done:

```bash
git diff --stat -- batch/data/tagging/concepts.<env>.yaml batch/data/tagging/tags.<env>.yaml
```

If there are changes, commit them:

```bash
git add batch/data/tagging/concepts.<env>.yaml batch/data/tagging/tags.<env>.yaml
git commit -m "chore: review pass on drafted <env>/<language> lemma concepts"
```

If there are no changes (the human approved the draft as-is), say so — don't create an empty commit.

## Output

After Step 5, report:
- The concepts that survived review (may be fewer than were drafted, if the human discarded any)
- Both commit SHAs (the draft commit and the review commit — or note that the review commit was skipped because nothing changed)
- A reminder that these concepts aren't live in the tagging pipeline until `build_tags_from_ocr` / `build_tags_from_descriptions` are rerun — that's a separate, deliberate step, not part of this command