# Board index

All tasks by stage (`todo -> analysis -> design -> implementation -> verification -> done`, see docs/process/process.md).
A task's stage is the directory its file is in. This index is maintained by hand: update it in the same change that moves a task file.
Topic trackers with sessions and dependencies: [description-tagging-tracker.md](description-tagging-tracker.md).

## todo

| # | Priority | Task |
|---|---|---|
| 135 | P0 | [No authentication/authorization on any API endpoint](todo/135-no-auth-authz-on-api.md) |
| 136 | P0 | [No VPS deployment automation — CI publishes images but nothing deploys them](todo/136-no-vps-deployment-automation.md) |
| 137 | P0 | [No dev→prod data promotion design (which tables sync, direction, partial sync)](todo/137-no-dev-prod-data-promotion-design.md) |
| 138 | P0 | [No backup automation or restore procedure for production DB/images](todo/138-no-backup-restore-for-prod.md) |
| 139 | P1 | [In-memory rate limiter doesn't work under the production gunicorn multi-worker setup](todo/139-rate-limiter-broken-under-multiworker.md) |
| 140 | P1 | [No secrets management process for VPS (.env.example, rotation, no-dev-defaults-in-prod)](todo/140-secrets-management-for-vps.md) |
| 141 | P1 | [SETUP.md's Docker Quick Start (docker-compose up -d) does not work — no root compose file](todo/141-docker-quick-start-broken.md) |
| 142 | P1 | [Missing production runbooks (batch crash recovery, stuck ingestion run, DB restore drill)](todo/142-missing-production-runbooks.md) |
| 143 | P1 | [No documented process for applying Alembic migrations safely against a live prod DB](todo/143-migration-process-against-live-prod-db.md) |
| 144 | P2 | [environments/Environments.md still has TODO placeholders for DB build and migration steps](todo/144-environments-md-todo-placeholders.md) |
| 145 | P2 | [No process supervision / restart policy design for the VPS-hosted backend](todo/145-no-process-supervision-restart-policy.md) |
| 146 | P2 | [No monitoring/alerting for the production service (uptime, log aggregation)](todo/146-no-monitoring-alerting.md) |
| 154 | P3 | [Question: should description embeddings take part in duplicate detection?](todo/154-description-embeddings-in-duplicate-detection-question.md) |

## analysis

| # | Priority | Task |
|---|---|---|
| 156 | P3 | [Search has no ranking: results are a membership filter ordered by created_at](analysis/156-search-result-ranking.md) |

## design

_none_

## implementation

_none_

## verification

| # | Priority | Task |
|---|---|---|
| 149 | P2 | [Rejected description feedback must exclude the description from search, similarity and tagging](verification/149-description-feedback-not-consumed.md) |

## done

| # | Priority | Task |
|---|---|---|
| 147 | P2 | [build_image_descriptions and build_image_description_embeddings are not run-tracked or admin-triggerable](done/147-description-jobs-not-run-tracked.md) |
| 148 | P2 | [Description jobs cannot cover an in-flight ingestion batch (no --status pending) — won't-do](done/148-description-jobs-no-pending-status-ingestion.md) |
| 150 | P1 | [Description tagging should use ConceptTagger, and retag when a description changes](done/150-description-tagging-use-concept-tagger.md) |
| 151 | P2 | [Ollama descriptions have no lemma index, so smart search cannot match them](done/151-ollama-descriptions-not-searchable.md) |
| 152 | P1 | [Design: human notes join (not override) Ollama descriptions in search, similarity and tagging](done/152-description-notes-join-search-similarity-tagging-design.md) |
| 153 | P1 | [Pipeline: scheduled chain of description and tagging jobs, with human gates](done/153-description-tagging-pipeline-orchestration.md) |
| 155 | P1 | [Implement the description_pipeline driver](done/155-description-pipeline-driver.md) |
| 157 | P3 | [Measure `source=description_all` on a real corpus (unindexed cross join)](done/157-description-all-performance.md) |
