# Epic: Meme from text

Status: draft
Originates from: user request, 2026-10-02 (brainstorming session)
Follow-ups: none yet (project specs will be listed here and in `tracker.md`)

Given a piece of text, find a meme (or several) that fit it. The user opens the web UI, enters text, and gets ranked memes.
The text is turned into a structured profile along fixed "meaning axes" (situation, emotion, ...), and memes carry the same axes,
so matching and filtering happen per axis instead of on one opaque vector.

This folder is the epic. It holds the target state (requirements, user stories), the decomposition and its mapping onto the existing
system, the epic's ADRs and a project tracker. Design specs for individual projects are written later, one per project, and linked
from `tracker.md`.

## Files

| File | Purpose |
|---|---|
| `README.md` | This charter: vision, scope, decisions made, glossary |
| `requirements.md` | Functional and non-functional requirements (IDs `FR-*`, `NFR-*`) and open questions (`OQ-*`) |
| `user-stories.md` | User stories with acceptance criteria (IDs `US-*`) |
| `decomposition.md` | Capabilities, mapping to what exists (reuse / extend / new), gaps, agent tool catalogue |
| `adr/ADR-001-flow-style.md` | Deterministic pipeline vs LLM agent |
| `adr/ADR-002-axis-taxonomy-and-representation.md` | Which axes, how values and vectors are stored |
| `adr/ADR-003-retrieval-and-fusion.md` | How per-axis signals are combined into one ranking |
| `tracker.md` | Projects, dependencies, order, status |

## Vision

A person receives a message ("the boss wants the report by Friday evening") and wants to answer with a meme. They paste or type the
message. The system works out what is going on and what kind of reaction fits, and returns memes whose meaning fits that
situation and reaction, even when the meme shares no words with the message.

## Decisions already made (user, 2026-10-02)

| # | Decision | Consequence |
|---|---|---|
| D1 | Core scenario is **reply to a message**: the input is a message or situation, the output is a meme to send back or react with | Matching is pragmatic ("when would you use this meme"), not lexical or purely visual. Query and meme sides are not symmetric (see Glossary) |
| D2 | Query-time LLM is **local Ollama only** | No hosted API, no per-query cost, user text never leaves the machine. Latency budget must tolerate a local model (a few seconds) |
| D3 | Axes are a **small fixed taxonomy** (about 4-6 axes) | One schema on both sides, so filtering and facets are well defined. A new axis is a schema change, not a data change |
| D4 | First release: **EN + RU + ES input, web UI only** | Same languages as OCR. Android and API-only clients are later. Queries in any of the three languages must match memes regardless of the meme's language |
| D5 | Approach **B**: deterministic pipeline with an LLM used only for structured parsing (query) and extraction (offline, meme side) | See ADR-001. Pipeline stages are designed as tools so an agent layer (P7) can wrap them later without a rewrite |

## Scope

In scope:
- Query understanding: text to a structured axis profile.
- Meme-side axis extraction, offline, for the active library.
- Retrieval, filtering and ranking across axes, with a per-result explanation of why it matched.
- A web UI for entering text, seeing results, and filtering by axis.
- An evaluation harness and a gold set, so quality can be measured and tuned.
- Operating the pipeline: batch jobs, admin triggering, run tracking, statistics, consistent with existing conventions.

Out of scope for the epic's first release (candidates for later epics):
- Android client and a public/auth'd API for third parties.
- Generating new memes or captioning images.
- Conversation context (multi-message threads) and follow-up refinement dialogue. The profile is built from one input text.
- Personalisation, usage feedback loops that retrain anything (click logging may be added by P6 for evaluation only).
- Memes whose meaning needs real-world knowledge the local model lacks (very recent events). Tracked as a known limitation.
- A hosted-LLM option. Revisit only through a new ADR.

## Principles

1. **The existing lexical search stays.** Meme-from-text is a new entry point, not a replacement for `/api/images?q=`.
   Where the lexical signal helps (quoted phrases, rare words that appear in the meme's text), it is one input to fusion.
2. **Same schema on both sides.** The query profile and the meme profile use the same axes and the same vocabularies.
3. **Offline does the heavy work.** Anything computable per meme is computed in a batch job (project convention). Query time does one
   LLM parse, then cheap retrieval.
4. **Everything is explainable and measurable.** Every result can say which axes matched, and every change is checked against a gold set.
5. **Respect the existing signals.** Description rejections and human notes keep their meaning (rejected descriptions never feed axes).
6. **Project conventions apply**: repository pattern, `backend_api.md` stays authoritative, settings in `environments/settings*.yaml`,
   schemas in `shared/schemas/`, batch jobs run-tracked and admin-triggerable, specs carry a status line.

## Glossary

- **Axis**: one named dimension of meaning (for example `emotion`). Defined in the taxonomy.
- **Taxonomy**: the set of axes, their value types and vocabularies. Versioned.
- **Profile**: a set of axis values for one text or one meme. Axis values may be empty.
- **Query profile**: what the input message is about and what reaction is wanted.
- **Meme profile**: what a meme conveys and when it is usable.
- **Asymmetry**: the message says what is happening (`situation`), the meme says what you answer (`stance`, `emotion`). A good match is
  "this meme is a good reply to this message", not "this meme is similar to this message". The taxonomy must encode both directions.
- **Fusion**: combining per-axis and lexical scores into one ranked list.
- **Gold set**: human-judged (text, acceptable memes) pairs used to measure quality.
- **Tool**: a single-purpose, typed, side-effect-free function of the pipeline (parse, search one axis, filter, fuse, explain).
