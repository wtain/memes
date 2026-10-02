# ADR-002: Axis taxonomy and how profiles are represented

Status: proposed (draft axes for discussion, not final)
Date: 2026-10-02
Epic: docs/superpowers/specs/2026-10-02-meme-from-text/README.md

## Context

Decision D3: a small fixed taxonomy of about 4-6 axes, shared by query and meme sides. The scenario is replying to a message (D1), so
the schema must express an asymmetry: the message describes a situation, the meme expresses a reaction and a use-context.
Inputs of three languages (D4) must land in one shared representation.

Two things need deciding: which axes, and how an axis value is stored and searched (vocabulary vs free text, one vector per axis vs
one combined vector, embedding model).

## Decision 1: draft axes

| Axis | Query side means | Meme side means | Value type (proposed) |
|---|---|---|---|
| `situation` | What is happening in the message ("deadline moved up", "someone cancelled plans") | The situation in which the meme is a fitting reply ("when plans get cancelled last minute") | Free text, one short English phrase, embedded |
| `emotion` | The feeling the sender wants to convey back (or the feeling the message provokes) | The emotion the meme expresses | Controlled vocabulary, multi-valued (about 20-30 values), each with a short definition |
| `stance` | The kind of reply wanted (agree, refuse, mock, console, celebrate, deflect, ...) | The communicative act the meme performs | Controlled vocabulary, single or two values (about 10-15 values) |
| `topic` | The domain of the message (work, family, money, tech, school, relationships, news...) | The domain the meme belongs to | Controlled vocabulary, multi-valued, can map from existing tag categories |
| `tone` | Register wanted (wholesome, sarcastic, absurd, dark, deadpan, crude) | The meme's register | Controlled vocabulary, multi-valued. Carries the content-sensitivity values (OQ-8) |

Rationale: `situation` and `stance` together carry the "reply" meaning. `emotion` and `tone` are the most reliable for a small local
model to classify, and the most useful as filters. `topic` is the coarse pre-filter and bridges to existing tags. Visual subject and
template identity (a particular character or format) stay in the existing tags and CLIP signals and are not an axis.

Open for the user: confirm the five axes, and whether `tone` should be split from content-sensitivity (OQ-8). Vocabularies are drafted
in P1 and reviewed by the user, using existing tags and the existing corpus to ground them.

## Decision 2: representation (proposed)

- **Controlled-vocabulary axes** (`emotion`, `stance`, `topic`, `tone`): store the vocabulary key(s) plus a confidence per value.
  The vocabulary lives in tracked YAML files shared across all environments (P1 spec, decision 2; only ranking weights and
  thresholds are per environment), with an
  English label and definition per key and optional localised labels (RU, ES) for the UI. Keys are language-neutral.
  - Filtering and facets are exact on keys, and no embedding is needed for a hard filter.
  - Soft matching uses a precomputed similarity between vocabulary keys (for example "resignation" is near "defeat"), currently
    hand-curated near-synonym pairs in `batch/data/taxonomy/similarity_overrides.yaml` with embedding neighbours switched off,
    because definition embeddings proved noisy (see the P1 spec addendum); the generated `similarity.json` is what the backend
    reads. Near-miss values still score above zero.
- **Free-text axis** (`situation`): store an English phrase per meme, plus its embedding. Query side: the LLM outputs an English
  phrase for the situation regardless of input language (the LLM also acts as the translator), embedded in the same space.
- **Always store** a short English "use when" sentence per meme, as the human-readable source for explanation and for re-extraction.
- **Vectors**: one vector per (image, axis) for axes where soft matching is needed (`situation` for sure, the others via the
  vocabulary key similarity instead of per-image vectors). This avoids n x axes vectors per image where a lookup table is enough.
- **Embedding model** (provisional, decided in P1 on 2026-10-02): one English text-embedding model **served by the local Ollama**
  and called over HTTP, for meme situation phrases, vocabulary definitions and query situation phrases. Reason: the backend forbids
  ML libraries (torch, sentence-transformers), so it cannot embed text itself, and Ollama is already the only allowed query-time
  model host (D2). Because the LLM normalises the query into English profile values, an English model suffices and no multilingual
  space is needed. These vectors live in new tables and are never mixed with the existing sentence-transformers `bge-large-en-v1.5`
  vectors. `model_name` and `model_digest` are stored per row so a switch is a re-embed. P6 compares this choice with a
  multilingual model on the gold set and may overturn it (OQ-4). The result must be one decision, not per-axis models. Details:
  `2026-10-02-p1-taxonomy-and-data-model-design.md`, decision 6.
- **Versioning**: a profile row stores `taxonomy_version`, `extractor_model`, `extractor_prompt_version`, `extracted_at`,
  `human_verified`. Changing the vocabulary or prompt marks rows stale, which re-extracts them (except `human_verified` rows,
  which are flagged for review instead).
- **Schema**: profile JSON schema under `shared/schemas/` (generated types for TS, Kotlin, Python per the repo's generation docs).
  Tables live in `Storage/models.py` with an Alembic migration. A normalised table per axis value is preferred over one JSON
  column when the axis is filterable or faceted (facet counts must be cheap).

## Alternatives considered

- **Free text for every axis**: most flexible, but facets and hard filters become fuzzy, and extraction noise would multiply.
- **One combined profile vector per meme**: simplest index, but loses per-axis weights, per-axis filtering and explanations, which
  are the epic's point.
- **Axes mined from the corpus (clustering)**: reuses lemma-cluster tooling, but gives unstable axes and no "reply" semantics. May
  be used offline to propose vocabulary entries (as concept discovery does), reviewed by a human.
- **Multilingual labels as the stored key**: rejected, keys stay language-neutral and localisation is a presentation concern.

## Consequences

- P1 delivers: the confirmed axes, the vocabularies as YAML, the profile JSON schema, the table design and the versioning rules,
  and the embedding-model decision supported by a small measured comparison.
- P2's extraction prompt outputs JSON conforming to the schema, validated before storage, and uses the vocabulary keys as an
  enumerated choice list.
- P3's parse prompt uses the same vocabulary list and the same schema (with the roles of the axes flipped as in the table above).
- Adding or renaming an axis later is a taxonomy version bump and a re-extraction, not a code rewrite, but it is not free.
  Keep the axis count small.

## Revisit when

- The gold set shows an axis is unreliable on either side (drop it, or merge it with another).
- Per-language results diverge, which would point to the embedding-model choice or the translation step.
