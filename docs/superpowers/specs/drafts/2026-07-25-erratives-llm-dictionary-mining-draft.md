# Erratives: LLM-Assisted Dictionary Mining — Placeholder

Status: Draft / placeholder — not researched in depth, not ready for brainstorming or
implementation. Captured now so the idea isn't lost; revisit as a standalone spec later,
likely after the phonetic-normalization work
(see the sibling "smart search: phonetic erratives normalization" design, once written)
has shipped and its real remaining gap is visible.

## Idea

Use a local LLM (this project already integrates Ollama — `ollama.model: qwen2`,
`ollama.enabled: true` in `environments/settings.yaml`) to mine the corpus's own OCR
vocabulary for likely erratives (deliberate internet-slang misspellings) and propose
candidate `errative → canonical` pairs for human review — as a **dictionary-building
aid**, not a runtime/query-time dependency. This is a complement to phonetic
normalization, not a replacement: phonetic rules handle the generalizable, systematic
substitutions (е↔и, а↔о, etc.); an LLM-assisted dictionary is more suited to catching
irregular, idiomatic, or culturally-specific erratives that don't reduce to a clean
phonetic rule (memeified brand names, deliberately garbled famous phrases, etc.).

## Why this, and why not live/inline rewriting

Rewriting OCR text through an LLM at query time or per-image at index time was
considered and set aside for now: real per-image LLM cost and latency across a
corpus of tens of thousands of images, plus non-determinism/hallucination risk (an LLM
"correcting" text can change actual meaning, not just spelling). Mining candidates
**offline, once, for a human to approve** avoids all of that — the LLM's output becomes
a small, static, versioned dictionary file, not a live dependency.

## Why this might not need new infrastructure

This project already has a workflow that's structurally almost identical to what
erratives-mining would need: `batch/build_lemma_clusters.py` embeds unmatched OCR
vocabulary (via `build_bow.py`'s `bow.unmatched.<env>.json` output) and clusters it
(HDBSCAN, per-language), optionally naming each cluster via Ollama;
`batch/draft_concepts_from_clusters.py` then drafts new concept/tag entries from the
top clusters for human review, committing the draft for review via the
`/draft-lemma-concepts` Claude Code command
(`.claude/commands/draft-lemma-concepts.md`). An erratives-mining pass would likely
follow the same shape: cluster candidate misspelled/unusual lemmas, ask Ollama whether
a cluster member looks like a known errative and what its canonical form would be, draft
candidate dictionary entries for a human to accept/reject — reusing the clustering and
review-workflow machinery rather than building new plumbing from scratch.

## Open questions for whenever this is picked up

- Does this feed the *same* substitution list the phonetic-normalization work produces,
  as one combined dictionary, or a separate LLM-curated list layered on top of the
  phonetic rules?
- Model choice: reuse the already-configured `qwen2`, or evaluate whether a different
  local model handles Russian internet slang recognition better?
- Prompt design and validation: how do we measure whether the LLM's proposed
  canonical form is actually correct, beyond a human eyeballing each entry?
- Precision/scale tradeoff: how many candidate pairs is "enough" before diminishing
  returns set in, given erratives likely follow a long-tail distribution (a small
  set of very common ones, a large tail of rare/one-off ones not worth curating)?
- Is corpus-mined discovery (only surfaces erratives already present in this specific
  corpus) sufficient, or is there value in a general-purpose Russian erratives seed list
  independent of what this corpus happens to contain?
