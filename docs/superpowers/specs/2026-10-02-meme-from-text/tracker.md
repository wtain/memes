# Meme from text: project tracker

Status: draft
Originates from: docs/superpowers/specs/2026-10-02-meme-from-text/README.md

Created 2026-10-02. Each project is a meaningful part of the feature, with its own design spec (written when the project is
picked up, named `YYYY-MM-DD-<name>-design.md` and linked below), then an implementation plan, then implementation. Projects
together deliver "meme from text". Project status uses the spec lifecycle values (`draft`, `approved`, `planned`,
`implementation`, `done`), with `not started` before a spec exists. Board tasks (`board/todo`, see `docs/process/process.md`)
are created per project when it starts, and listed in the Tasks column.

Epic-level ADRs (all `proposed`): ADR-001 flow style, ADR-002 taxonomy and representation, ADR-003 retrieval and fusion.

## Projects

| ID | Project | Delivers | Depends on | Reqs | Status | Spec / plan | Tasks |
|---|---|---|---|---|---|---|---|
| P1 | Taxonomy and data model | Confirmed axes, vocabularies (YAML), profile JSON schema and generated types, tables and migration design, versioning rules, embedding-model decision (measured) | none (ADR-002 input) | FR-12, OQ-1, OQ-2, OQ-4, OQ-8 | done | `2026-10-02-p1-taxonomy-and-data-model-design.md`, plan `docs/superpowers/plans/2026-10-02-p1-taxonomy-and-data-model.md` | - |
| P6 | Evaluation and gold set | Gold-set format and first 150-300 cases (EN/RU/ES), runner, metrics, baseline store, per-language report, optional rating capture | P1 (axis definitions) for case metadata, can start labelling earlier | FR-18, FR-24, NFR-6, OQ-5 | not started | - | - |
| P2 | Meme profile extraction | Offline job: image + OCR + description + note to a validated profile, axis values and vectors stored, incremental, run-tracked, admin-triggerable, human override, coverage stats, measured throughput and model choice | P1 | FR-7..FR-12, FR-23, NFR-5, NFR-8, OQ-3, OQ-7 | draft | `2026-10-03-p2-meme-profile-extraction-design.md` | - |
| P3 | Query understanding | Parse tool: text to query profile with local LLM, schema validation, retry, timeout, cache, language detection, degraded mode, tool interface | P1; P6 for prompt evaluation | FR-2..FR-4, FR-22, NFR-3, NFR-4, NFR-9 | not started | - | - |
| P4 | Retrieval, fusion and API | Candidate generation, per-axis scoring, fusion, filters, facets, diversity, cursor, explanations, endpoint, `backend_api.md`, schemas and generated types, tool interfaces | P1, P2 (data), P3 (parse) | FR-6, FR-13..FR-17, FR-19..FR-22, NFR-1, OQ-6 | not started | - | - |
| P5 | Web UI | Text box, loading and degraded states, profile chips editor, results with explanations, axis facets and filters, rating widget, meme-profile editor on `MemePage`, coverage view in admin | P4 (endpoint), P2 for the editor | FR-1, FR-5, FR-6, FR-11, FR-16, FR-17, FR-23 | not started | - | - |
| P7 | Agent layer (optional) | Decision memo, then (if justified) an agent wrapping the P3/P4 tools: query rewriting, clarifying questions, LLM re-rank | P3, P4, P6 results | OQ-9 | not started, gated | - | - |

## Order

```
P1 ──► P2 ─────┐
 │             ├──► P4 ──► P5 ──► (P7?)
 ├──► P3 ──────┘
 └──► P6 (starts with labelling, runs alongside P2, P3 and P4, and gates tuning in each)
```

Suggested sequence of sessions: P1, then P6 gold set (labelling is human time and can run in parallel with P2), P2 in parallel
with P3, then P4, P5. P7 is decided after P4 and P6 show where the pipeline fails.

## Milestones

| Milestone | Definition of done | Projects |
|---|---|---|
| M0 Epic approved | User approves this epic and the three ADRs (or changes them) | - |
| M1 Data ready | At least one environment (suggest `general`) has profiles for the active library, with the coverage view | P1, P2 |
| M2 Baseline | Gold set exists and the baseline pipeline (parse + retrieve + fuse) has stored metrics | P3, P4, P6 |
| M3 Usable | Web UI from US-1 to US-4 works end to end on one environment, meeting NFR-1 and NFR-4 | P4, P5 |
| M4 Rolled out | Rolled out to metal, general and it (or `it` explicitly excluded per NFR-7) | all but P7 |
| M5 Agent decision | P7 memo accepted or the layer dropped | P7 |

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Local model quality is too low for Russian or Spanish parsing or for meme-meaning extraction | Poor axes, poor results | P6 measures per language before P2 runs at scale. Compare candidate models on a sample first. Fallback to embedding-only path |
| Extraction throughput on local Ollama makes the backlog take days (25k images) | M1 slips | Per-run cap and resumable slices as in `description_pipeline`. Measure images per hour in P2 before committing. Consider extracting from text and existing descriptions first, image only where needed |
| Taxonomy is wrong and re-extraction is needed | Rework | Versioning (FR-12), keep the axis count small, validate vocabularies on a sample before the full run |
| Vision model describes content but not intent ("when to use it") | Weak `situation` and `stance` | Give the extractor OCR text and existing descriptions, evaluate against the gold set, human override for key memes |
| Gold set is too small or biased to the author's taste | Metrics mislead | Cover all languages, include hard and ambiguous cases, record the rater, grow it from ratings (FR-18) |
| Always-occupied ports and the always-on environments (CLAUDE.md) | Cannot bind ports for testing | Test against mocked DB and the dedicated test DB. Follow `environments/Environments.md` |
| Agent scope creep | Complexity before evidence | P7 is gated by evidence (OQ-9) |

## Next step

P1 is done. P2, P3 and P6 can start. Board tasks for them have not been created.
