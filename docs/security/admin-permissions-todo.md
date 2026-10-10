# Admin Functionality Needing Permission Controls

Living checklist of backend functionality that has no authorization model today and will need one
before it's safe to expose beyond a trusted network. Not a spec — no status/lifecycle, just append
to it as new admin-only functionality ships without permission checks.

## Current state (as of the query log and gold-set jobs, 2026-10-11)

There is no authentication or authorization anywhere in this backend today. Anyone who can reach a
backend process's port can call any endpoint, including the ones below.

The first version of this list (2026-07-28) named five endpoints. It has been rebuilt from the code:
every route in every router under `Backend/app/api/` (57 routes, all mounted under `/api` by the
`include_router` lines in `Backend/app/main.py`, plus the root `GET /health`) is classified in the
three tables below, and every frontend page that calls an admin or personal-data route is named
(checked against `Frontend/memes-frontend/src/app/router.tsx` and `AppLayout.tsx`). Re-do that
inventory (`rg -n "@router\.(get|put|post|delete|patch)" Backend/app/api`) whenever a router changes.

`environments/.env.*` (gitignored, per-environment) can configure LAN-facing origins
(`GENERAL.FRONTEND_ORIGIN` / `ALTERNATIVE_FRONTEND_ORIGIN` in `Backend/app/main.py`'s CORS setup) —
meaning a given environment's backend is not guaranteed to be reachable from localhost only. Origin
restriction is a browser-enforced CORS check, not a server-side authorization boundary: it does not
stop a direct HTTP client (curl, another service on the LAN, etc.) from calling these endpoints
regardless of `Origin`.

## Interim mitigation (accepted for now, not a substitute for real controls)

Two layers, neither of which is real access control on its own:

1. **Nothing in the frontend hides these endpoints.** The pages `/admin`, `/admin/profiles`,
   `/ingestion` and `/upload` have links in the header navigation, and the curator panel is on every
   `MemePage`. Treat the UI as a convenience, not a barrier.
2. **The batch-trigger allow-list is fixed, server-side code** (`batch/registry.py`, reading
   `environments/batch_registry.yaml`, 32 entries today) — a caller can only ever trigger a
   registered script, with no way to supply an arbitrary module path or extra CLI arguments. This
   bounds *what* an unauthenticated caller could do, but does not stop them from doing it.

Anyone with network access to a backend port (which may include the LAN, depending on
per-environment config) can trigger any registered batch job, read run history/status for all of
them, edit meme profiles and notes, flag and promote images, upload files, and read the pasted
meme-from-text messages in `search_history` and `meme_text_ratings`. This is accepted as a known
gap for now, not resolved.

## Endpoints needing permission controls once a model exists

Page names below are the frontend routes (`router.tsx`). The batch admin page is at `/admin` (the
"Admin" link; older docs call it `/admin/batches`).

### Admin only

| Route | Why | Called from |
|---|---|---|
| `POST /api/admin/batches/{batch_name}/run` | Starts a batch job against live data. The strongest guard needed | `/admin` |
| `GET /api/admin/batches/names` | Lists the registered jobs | `/admin` |
| `GET /api/admin/batches/runs` | Run history for all jobs | `/admin` |
| `GET /api/admin/batches/runs/{run_id}` | Run status and error detail | `/admin` |
| `GET /api/admin/profile-coverage` | Profile coverage counts and last runs of the profile jobs | `/admin/profiles` |
| `GET /api/admin/profile-coverage/images` | Keyset-paged image lists per coverage category | `/admin/profiles` |
| `PUT /api/meme-from-text/profiles/{image_id}` | Curator write: saves a profile as human-verified, embeds the situation | curator panel on `/memes/:id` |
| `POST /api/meme-from-text/profiles/{image_id}/confirm` | Curator write: marks a model profile verified | curator panel on `/memes/:id` |
| `DELETE /api/meme-from-text/profiles/{image_id}` | Curator write: deletes the profile and its failure row | curator panel on `/memes/:id` |
| `PUT /api/images/{image_id}/description-note` | Anyone can overwrite any image's note | `/memes/:id` |
| `DELETE /api/images/{image_id}/description-note` | Anyone can clear any image's note | `/memes/:id` |
| `PUT /api/images/{image_id}/descriptions/{prompt_key}/approve` | Description feedback; changes which descriptions feed search and tags | `/memes/:id` |
| `PUT /api/images/{image_id}/descriptions/{prompt_key}/reject` | Same, and deletes the image's Ollama tags | `/memes/:id` |
| `PUT /api/images/meme/{image_id}/mark_flagged` | Flags an image; `move_flagged` later moves the file out of the library | meme cards and `/memes/:id` |
| `PUT /api/images/meme/{image_id}/unmark_flagged` | Reverses a flag | meme cards and `/memes/:id` |
| `POST /api/images/duplicates/clusters/{cluster_id}/dismiss` | Records duplicate decisions that hide candidate pairs | `/duplicates` |
| `POST /api/images/duplicates/pairs/undo-dismiss` | Reverses those decisions | `/duplicates` |
| `GET /api/ingestion/run` | State of the intake run | `/ingestion` |
| `GET /api/ingestion/pending` | Lists images not yet in the library | `/ingestion` |
| `GET /api/ingestion/clusters/{tier}` | Review queue, with OCR text per member | `/ingestion` |
| `GET /api/ingestion/review/tier_b` | Tier B review queue | `/ingestion` |
| `POST /api/ingestion/clusters/{tier}/resolve` | Reject or keep decisions; rejected images leave the library path | `/ingestion` |
| `POST /api/ingestion/images/{image_id}/undo-reject` | Reverses a rejection | `/ingestion` |
| `POST /api/uploads` | Writes image files into the intake directory (rate-limited per IP, which is not authorization) | `/upload` |

### Personal data: restrict to the owner or admins

| Route | Why | Called from |
|---|---|---|
| `GET /api/history` | Search history, which now also holds the meme-from-text messages the owner pasted (`?kind=meme_text`, text, parse, timings). Default `kind=search` is the old behaviour. Anyone can read all of it | no page today |
| `PUT /api/meme-from-text/ratings` | Writes a rating and the message text that goes with it | `/reply` |
| `GET /api/meme-from-text/ratings` | Returns the stored ratings for a message | `/reply` |
| `DELETE /api/meme-from-text/ratings` | Removes one rating | `/reply` |
| `DELETE /api/meme-from-text/ratings/text` | "Forget text": clears the stored text and the matching `search_history` rows. Anyone can erase them, or probe hashes | `/reply` |

Also note that `POST /api/meme-from-text/search` (public, below) writes one `search_history` row per
fresh message unless `search.meme_from_text.query_log.enabled` is false, and `GET /api/images?q=`
writes a `search` row; the personal data is created by routes that look like plain reads.

### Public or low risk

| Route | Why it can stay open |
|---|---|
| `GET /health`, `GET /api/diagnostics/health` | Liveness |
| `GET /api/diagnostics/statistics` | Corpus counts. `?live=true` recomputes and stores the snapshot, so an open endpoint can be used to add load (rate-limit when auth arrives) |
| `GET /api/images`, `/{image_id}/similar`, `/{image_id}/descriptions`, `/meme/{image_id}`, `/meme/{image_id}/get_flagged` | Search and browse |
| `GET /api/images/{image_id}` | Image bytes |
| `GET /api/images/flagged`, `/untagged`, `/no-ocr`, `/duplicates`, `/duplicates/clusters/{cluster_id}/similarity`, `/duplicates/decisions` | Review lists (reads only; their write counterparts are above) |
| `GET /api/concepts`, `/concepts/top-images`, `/concepts/for-image`, `/concepts/{concept_id}` | Read-only concept browsing (no concept management endpoint exists) |
| `GET /api/trends/dates`, `/dates/{run_date}/runs`, `/dates/{run_date}/runs/latest`, `/runs/{run_id}`, `/history` | Read-only trends (sources are managed by a script, not an endpoint) |
| `GET /api/recommendations` | Read-only |
| `POST /api/meme-from-text/search`, `GET /api/meme-from-text/taxonomy`, `GET /api/meme-from-text/profiles/{image_id}` | Retrieval and vocabulary; the profile read feeds the curator panel and exposes nothing the page does not show. Search writes the query log (see above) |
| `POST /api/bug-reports` | Anonymous log upload from the Android client: `.txt`/`.log` only, 10 MB cap, rate-limited per IP. It is a write, so revisit it with the admin routes |

## Second routes to the same data

Closing the HTTP routes above does not close these:

- **`DATABASE_URL_READONLY`** (role `ocr_readonly`, in each `environments/.env.<environment>`): a
  `SELECT` grant on all tables, so it reads `search_history` (pasted messages) and
  `meme_text_ratings` directly. Hand it out accordingly (see the section below).
- **Database dumps in `Storage/backups/`**: full copies of every table, including both of the above.
  Untracked, but on the developer's disk and potentially copied elsewhere.
- **`evaluation/work/ratings-export/`** (git-ignored): `export_ratings` rewrites `current/` daily on
  `general`; `candidate-queries.json` there holds message text. `judgments-*.json` carry hashes and
  grades only.

## Open items

- **Retention for `search_history`.** Nothing expires rows. The only controls are the manual
  "Forget text" action (one message at a time, meme-from-text rows only) and the
  `search.meme_from_text.query_log.enabled` switch, which stops new meme-from-text rows but keeps old
  ones. Decide a retention period or a prune job.
- **Hide meme-from-text rows from `/api/history` for non-owners?** Today `kind=meme_text` and
  `kind=all` are open to every caller. Decide whether they should be owner- or admin-only while
  `kind=search` stays open, or whether the whole route moves to the personal-data tier.
- **No UI reads the query log yet.** Decide who may see it before one is built.

(Extend this list as future admin-only functionality ships without its own permission model.)

## When a permission model exists

Revisit this file: replace "interim mitigation" above with the actual model (roles, tokens,
whatever gets chosen), and check off / remove entries above as they get real guards. Until then,
treat every endpoint under `/api/admin/*` as unauthenticated by default — don't assume a new
`/api/admin/*` endpoint is safe just because it's "just for admins" in name.

## Related: read-only DB credentials for agent/subagent use (2026-08-06)

A separate but adjacent gap: coding-agent subagents given live database access for verification
work had no restriction stopping them from running destructive statements — see CLAUDE.md's "Live
database access for agents" section for the incident (`EXPLAIN ANALYZE` on a `DELETE` executed for
real against the `general`/`it` databases) and the fix (a genuine `ocr_readonly` Postgres role,
`DATABASE_URL_READONLY` in each `environments/.env.<environment>`). This is a different threat model
than the endpoint list above — it's about what a coding assistant can do with credentials it's
handed, not what an unauthenticated HTTP caller can reach — but the same underlying principle
applies: don't assume a capability is safe to hand out just because the instructions say "read
only."
