# [DRAFT] Multi-Model Concept Embeddings

Status: draft
Originates from: docs/superpowers/specs/2026-07-01-build-lemma-clusters.md

**Date:** 2026-07-02
**Scope:** TBD — likely `Storage/models.py`, `repository/concepts.py`, `batch/build_concept_embeddings.py`, and any `LOOKUP_CONCEPTS`-style consumers

---

## Intent

Extend concept-embedding storage so that a concept can have embeddings from more than one embedding model (e.g. CLIP and `sbert`), instead of a single implicit CLIP-only vector per concept.

## Rationale

`build_lemma_clusters` (`2026-07-01-build-lemma-clusters.md`) settled on `sbert` (`paraphrase-multilingual-MiniLM-L12-v2`) as its default text embedding model after empirically finding that CLIP's text encoder gives little to no meaningful similarity separation for Russian and Spanish lemmas — the exact case that batch exists to catch (e.g. Russian erative variants showed a ~0.00 within/across-group gap under CLIP).

Today, DB concept embeddings (`Storage/models.py` `Concept.embedding`, populated by `batch/build_concept_embeddings.py` via `ai/clip.py`'s `ClipModel`) live only in CLIP's vector space. `build_lemma_clusters`'s optional `LOOKUP_CONCEPTS` feature (comparing a cluster centroid to the nearest DB concept) is therefore restricted to `TEXT_EMBED_MODEL=clip` — an `sbert`-embedded centroid isn't comparable to a CLIP-embedded concept vector at all (different model, not just different dimensionality).

`LOOKUP_CONCEPTS` is currently disabled by default and CLIP-only, so this isn't an immediate blocker. But if `sbert` remains the better choice for non-Latin-script text generally (which the empirical results suggest), other future consumers may hit the same mismatch. This spec is a placeholder to track that eventual need — not an active requirement yet.

## Non-Goals (for this draft)

- No schema design, migration plan, or API changes are proposed here.
- No decision yet on approach (separate table vs. model-discriminant column vs. something else).
- Not a prerequisite for anything currently planned — only for `LOOKUP_CONCEPTS` support under `sbert`, which is explicitly out of scope for `build_lemma_clusters` for now.

## Follow-up

Flesh out this spec (or supersede it) if/when a concrete consumer needs cross-model concept-embedding comparison — e.g. if `LOOKUP_CONCEPTS` under `sbert` becomes a real requirement.