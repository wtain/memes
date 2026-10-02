# ADR-001: Flow style for meme-from-text: deterministic pipeline vs LLM agent

Status: proposed (recommended option B, awaiting user confirmation)
Date: 2026-10-02
Epic: docs/superpowers/specs/2026-10-02-meme-from-text/README.md

## Context

The feature turns a message into a ranked list of memes. A local Ollama model is the only allowed query-time LLM (decision D2).
The existing system is batch-heavy: expensive work is precomputed and the API reads results. The new flow needs one step that is
inherently language-understanding (what is this message about, what reaction fits) and several steps that are not (vector
search, filtering, ranking).

Forces:
- Local models are slower and less reliable at multi-step tool use and structured output than hosted ones, and quality is weaker
  for Russian and Spanish.
- Query latency matters (NFR-1), and results must be reproducible for evaluation (NFR-3).
- The feature must degrade gracefully when the model is unavailable (NFR-4).
- The user wants an agent to be considered, and the epic should not close that door.

## Options

### A. Agent-first
An LLM agent (for example LangChain or a hand-rolled loop) receives the text and a set of tools and decides which to call, how
many times, and when to stop.
- For: flexible, can refine queries, can ask clarifying questions, easy to add new tools.
- Against: several sequential local LLM calls per query (latency multiplies), non-deterministic paths, hard to evaluate and
  regress-test, weak tool-use reliability from small local models, failure modes are open-ended.

### B. Deterministic pipeline, LLM only for parsing and offline extraction (recommended)
Fixed stages: parse (one LLM call, structured JSON) → per-axis retrieval → filter → fuse → explain. The meme-side axes come from
an offline LLM extraction job. Stages are implemented as tools with typed interfaces (decomposition.md section 4).
- For: predictable latency (one LLM call), deterministic after parse, cacheable, testable per stage, natural fallback (skip the
  parse), the same tool functions can later be exposed to an agent.
- Against: no adaptive behaviour, a bad parse is a bad result unless the user edits the profile (FR-5), needs the taxonomy and
  extraction job built first.

### C. Embeddings only, no axes
Embed the text, search existing description or note vectors.
- For: cheapest, nearly all components exist.
- Against: fails the stated goal (axes and axis filters), conflates "what the meme shows" with "when you would use it", English-only
  vectors today, no explanation.

## Decision (proposed)

Choose **B**. Design every stage as a tool (FR-22) and implement the endpoint by composing those tools, so that an agent layer (P7)
is an additive wrapper, not a rewrite. Keep C as the fallback path inside B (when parsing fails or Ollama is down), which costs
almost nothing because the pieces overlap.

An agent is not adopted now. It becomes a candidate (P7, OQ-9) only for failure classes the gold set shows the pipeline cannot
fix, for example: ambiguous input that needs a clarifying question, multi-intent text that needs two searches, or re-ranking the
top N by direct LLM judgement. Each candidate use is evaluated against the baseline and adopted only if it improves quality
within the latency budget.

## Consequences

- P3 and P4 produce tool-shaped modules with JSON-schema'd inputs and outputs (shared with generated types where they cross the API).
- The LLM is called once per query, at most twice with the retry (FR-4), plus never at fusion time.
- Parse results are cached (key: normalised text + parser version + taxonomy version) and stored with results of evaluation runs.
- Choosing LangChain or any framework is deferred to P7. P3 uses the existing `ai/` Ollama client directly, with no framework
  dependency, which also keeps `Backend/requirements-backend.txt` small.
- Reversal cost is low: moving to A means adding an orchestration layer over the existing tools.

## Revisit when

- The gold set shows a large class of failures that a second or third LLM step would fix.
- A faster or better local model makes multi-step agent runs viable within the latency budget.
- The user decides to allow a hosted model (this would also reopen decision D2).
