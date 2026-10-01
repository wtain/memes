# Description and tagging work tracker

Created 2026-10-01 from the audit in docs/superpowers/specs/drafts/2026-10-01-description-tagging-audit-and-pipeline-notes.md.
Reference doc (written): docs/data-flow.md. Each task is taken in its own session. Move task files through board/ as they
progress (see docs/process/process.md).

Decisions (user, 2026-10-01): description tagging uses ConceptTagger; notes join (not override) in search, similarity and
tagging; rejected description feedback is excluded from search, similarity and tagging.

Status is the board stage the task file is in (todo, analysis, design, implementation, verification, done). Session is the
Claude Code session id; resume with `claude --resume <id>`. Add a new row's session id when a session takes the task.

| # | Task | Priority | Path | Depends on | Status | Session |
|---|---|---|---|---|---|---|
| - | Docs: data-flow.md, CLAUDE.md batch list, ARCHITECTURE.md | - | done 2026-10-01 | - | done | - |
| 147 | Run-track and register description jobs | P2 | bounded | - | done | 12ec6cf0-9a05-40be-ad18-6802263c7ac5 |
| 148 | `--status pending` for description jobs | P2 | bounded + analysis | 147 | done (won't-do) | 12ec6cf0-9a05-40be-ad18-6802263c7ac5 |
| 150 | Description tagging via ConceptTagger + retag fix | P1 | bounded/architectural | - | done 2026-10-01 (rolled out to metal, general) | 1178696a-3821-4aac-97b2-d52c07e4406c |
| 151 | Lemma index for Ollama descriptions (search) | P2 | analysis then design | 152 (design together) | done 2026-10-01 (merged 2a96a8c, rolled out) | 1178696a-3821-4aac-97b2-d52c07e4406c |
| 152 | Design: notes join search/similarity/tagging | P1 | architectural | - | done 2026-10-01 (merged 2a96a8c, rolled out) | 1178696a-3821-4aac-97b2-d52c07e4406c |
| 149 | Rejected feedback excluded everywhere | P2 | design | 150, 151 | verification | 12ec6cf0-9a05-40be-ad18-6802263c7ac5 |
| 153 | Pipeline orchestration with human gates (design approved, spec 2026-10-01-description-tagging-pipeline-driver-design.md) | P1 | architectural | 147 | done | 12ec6cf0-9a05-40be-ad18-6802263c7ac5 |
| 155 | Implement `description_pipeline` driver | P1 | bounded | 147 | done | 12ec6cf0-9a05-40be-ad18-6802263c7ac5 |
| 154 | Question: description embeddings in duplicates | P3 | analysis | - | analysis | 12ec6cf0-9a05-40be-ad18-6802263c7ac5 |
| 156 | Search ranking (results are an unranked membership filter today) | P3 | analysis then design | 151, 152 | done 2026-10-02 (merged a103e36; also fixed the pagination cursor skipping a row per page) | 1178696a-3821-4aac-97b2-d52c07e4406c |
| 157 | Measure `description_all` similarity on a real corpus; HNSW per-vector fallback if slow | P3 | analysis | 151, 152 | done 2026-10-01 (0.5 s on 32k vectors, no fallback needed) | 1178696a-3821-4aac-97b2-d52c07e4406c |

Suggested order: 152 and 150 (decisions shape the rest) -> 147 -> 151 -> 149 -> 148 (likely won't-do) -> 155 (closes 153); 154 any time.

Doc upkeep: when a task changes a producer or consumer, update docs/data-flow.md in the same change.
