# Batch jobs: documentation index

Status: draft (verified against the code on 2026-10-11)

Everything that enriches the library offline (OCR, embeddings, lemmas, tags, descriptions, meme profiles,
duplicate clusters, trends, statistics) is a batch job under `batch/`. The FastAPI backend only serves what
these jobs pre-computed. This folder is the map of those jobs.

| Document | Read it when you want to know |
|---|---|
| [inventory.md](inventory.md) | Every script: what it does, how to run it, whether it is run-tracked, admin-triggerable or scheduled, per environment |
| [execution-model.md](execution-model.md) | How a job actually runs: CLI vs `run_wrapper` vs scheduler vs `/admin/batches`, run tracking, locks, chaining |
| [use-cases/ingestion.md](use-cases/ingestion.md) | Bringing new images in (inbox to active), with the stage machine and the review tiers |
| [use-cases/ocr-lemmas-tags.md](use-cases/ocr-lemmas-tags.md) | OCR, lemmatisation (smart search), rule-based tagging, concept tagging |
| [use-cases/descriptions-and-profiles.md](use-cases/descriptions-and-profiles.md) | Ollama descriptions, notes, description embeddings, meme-from-text profiles, `description_pipeline` |
| [use-cases/duplicates-and-clusters.md](use-cases/duplicates-and-clusters.md) | Near-duplicate candidates, clustering, the text-heavy classifier and OCR-text probe |
| [use-cases/maintenance-and-cleanup.md](use-cases/maintenance-and-cleanup.md) | Flag/move/unregister, format fixes, resets, backfills |
| [use-cases/concept-discovery.md](use-cases/concept-discovery.md) | Human-in-the-loop vocabulary growth (`build_bow` to drafted concepts) |
| [use-cases/trends-and-statistics.md](use-cases/trends-and-statistics.md) | The two jobs scheduled on every environment |
| [use-cases/screenshot-recut.md](use-cases/screenshot-recut.md) | Screenshot recut: detecting badly cropped memes, cutting, review, and removing the sources (`recut_pipeline`) |
| [use-cases/ratings-and-gold-set.md](use-cases/ratings-and-gold-set.md) | The `/reply` rating widget and query log, the two extra `general` jobs `export_ratings` and `import_judgments`, and how ratings reach the gold set |
| [use-cases/comics-build.md](use-cases/comics-build.md) | Building a multi-image comics into one meme, the flagged panel sources and the jobs to run after a build |
| [use-cases/comics-candidates.md](use-cases/comics-candidates.md) | Suggested comics: the `detect_comics_candidates` job and the signals it uses |
| [diagrams/](diagrams/README.md) | Graphviz execution-order diagrams (ingestion, mark-move cleanup, duplicates, tagging, descriptions, concept discovery, ratings and gold set, start paths) |
| [gaps-and-findings.md](gaps-and-findings.md) | Things found while writing this: doc drift, undocumented behaviour, risks |

Related, older documents (still authoritative for the topics they cover):
`docs/data-flow.md` (which signal feeds which consumer), `docs/runbooks/ingestion-pipeline.md` (operator steps),
`batch/rules_engine.md` (tagging engine), `CLAUDE.md` section "Batch pipeline" (long per-script notes),
`docs/superpowers/specs/` (design specs; linked from the use-case pages).

## The 60-second picture

```mermaid
flowchart LR
    subgraph IN[Intake]
        INBOX[(inbox dir)] --> ING[Ingestion<br/>hash dedup, formats,<br/>embeddings, OCR, review]
    end
    ING --> ACTIVE[(active library)]
    ACTIVE --> SIG[Signals<br/>OCR, CLIP, lemmas]
    SIG --> TAG[Tags<br/>OCR / Note / Ollama /<br/>CONCEPT / YOLO]
    SIG --> DUP[Duplicates<br/>tmp_duplicates, clusters]
    ACTIVE --> DESC[Descriptions<br/>Ollama, notes]
    DESC --> PROF[Meme profiles<br/>+ situation embeddings]
    DESC --> TAG
    TAG --> API[(FastAPI)]
    SIG --> API
    DUP --> API
    PROF --> API
    TREND[trends_batch] --> API
    STAT[build_statistics] --> API
```

## Ways a job gets started

```mermaid
flowchart TD
    CLI["Shell: python -m batch.X --env E"] --> MAIN
    ADM["/admin/batches (POST /api/admin/batches/{name}/run)"] --> WRAP
    SCH["Backend lifespan scheduler (scheduler.jobs)"] --> WRAP
    WRAP["python -m batch.run_wrapper<br/>--script --env --trigger [--run-id]"] --> MAIN["module.main(trigger, run_id)"]
    MAIN --> TRK["batch_runs row<br/>(kind, trigger, status, stats)"]
    WRAP -. "after any success except build_statistics" .-> STAT["refresh statistics snapshot (best effort)"]
```

Only scripts listed in `environments/batch_registry.yaml` can be started by the wrapper (and therefore from the
admin UI or the scheduler). Everything else is shell-only.
