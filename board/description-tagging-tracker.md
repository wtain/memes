# Description and tagging work tracker

Created 2026-10-01 from the audit in docs/superpowers/specs/drafts/2026-10-01-description-tagging-audit-and-pipeline-notes.md.
Reference doc (written): docs/data-flow.md. Each task is taken in its own session. Move task files through board/ as they
progress (see docs/process/process.md).

Decisions (user, 2026-10-01): description tagging uses ConceptTagger; notes join (not override) in search, similarity and
tagging; rejected description feedback is excluded from search, similarity and tagging.

| # | Task | Priority | Path | Depends on |
|---|---|---|---|---|
| - | Docs: data-flow.md, CLAUDE.md batch list, ARCHITECTURE.md | - | done 2026-10-01 | - |
| 147 | Run-track and register description jobs | P2 | bounded | - |
| 148 | `--status pending` for description jobs | P2 | bounded + analysis | 147 |
| 150 | Description tagging via ConceptTagger + retag fix | P1 | bounded/architectural | - |
| 151 | Lemma index for Ollama descriptions (search) | P2 | analysis then design | 152 (design together) |
| 152 | Design: notes join search/similarity/tagging | P1 | architectural | - |
| 149 | Rejected feedback excluded everywhere | P2 | design | 150, 151 |
| 153 | Pipeline orchestration with human gates | P1 | architectural | 147, 148 |
| 154 | Question: description embeddings in duplicates | P3 | analysis | - |

Suggested order: 152 and 150 (decisions shape the rest) -> 147 -> 151 -> 149 -> 148 -> 153; 154 any time.

Doc upkeep: when a task changes a producer or consumer, update docs/data-flow.md in the same change.
