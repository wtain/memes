# [P2] Rejected description feedback must exclude the description from search, similarity and tagging

- **Priority:** P2
- **Area:** Backend / Batch
- **Source:** user decision 2026-10-01
- **Depends on:** 150, 151 (the consumers must exist first for search and tagging)

## Problem

`image_description_feedback.approved` is display-only today. A description marked rejected still feeds embeddings,
`/similar?source=description`, and `Ollama` tags.

## Decision

Rejected descriptions are excluded from search, similarity and tagging. Open design points (resolve in analysis/design):
unreviewed (no feedback) stays included; an approve after a reject re-includes it; already-built embeddings, lemmas and tags need
cleanup or query-time filtering; what happens to tags already derived from a description when it gets rejected.

## Pointers

`Backend/app/repositories/image_repository.py` (`get_similar_by_description`, `set_description_feedback`),
`repository/image_description_embeddings.py`, `repository/images.py` (`get_images_and_descriptions*`).

---

## Analysis (2026-10-01, session 12ec6cf0-9a05-40be-ad18-6802263c7ac5)

### What is already covered by task 151/152 (branch `worktree-152-notes-join-design`, not yet merged to main)

That branch (spec `2026-10-01-description-notes-search-similarity-tagging-design.md`, status done) adds the shared predicate
`description_not_rejected(description_id_col)` in `repository/image_descriptions.py` and applies it to the **new** consumers: the
`description_lemmas` search source and `source=description_all`. It explicitly leaves the rest of this task to 149. **149 builds on that
predicate, so implementation must start from that branch (or after it merges to main).**

### Remaining consumers (verified in code on main)

| Consumer | Today | Needed |
|---|---|---|
| `GET /similar?source=description` (`ImageRepository.get_similar_by_description`) | Joins every description and embedding; feedback ignored | Exclude rejected on both the source and candidate side with the predicate. Query-time, no data change |
| `build_image_description_embeddings` (`get_descriptions_without_embedding`) | Embeds every description | See question 2 |
| `build_tags_from_descriptions` (`get_images_and_descriptions_needing_tags`) | Tags every description; incremental selection looks only at description vs tag timestamps | Exclude rejected descriptions from the text fed to `ConceptTagger`, and make feedback changes trigger a retag (below) |
| Existing `Ollama` tags | Materialized rows in `image_tags` and searchable | Must stop matching once their source description is rejected |

### The hard part is tagging, not queries

Search and similarity filter at query time, so rejecting and re-approving take effect immediately and need no cleanup. Tags are
**materialized** and **unioned per image across descriptions** (task 150), so a tag row does not record which description produced it.
Consequences:

- `image_description_feedback` has only `created_at`, and the upsert in `set_description_feedback` does not update it, so there is no
  "feedback changed" timestamp for the incremental selection to compare against. Neither a reject nor an approve is visible to
  `get_images_and_descriptions_needing_tags` today.
- The batch cannot tell which of an image's `Ollama` tags came from the rejected description, so the only correct repair is to rebuild
  the image's `Ollama` tags from its non-rejected descriptions.
- Rejecting every description of an image must leave it with no `Ollama` tags (the rebuild yields nothing).

### Options for tags

- **A. Delete on change (recommended).** When feedback changes (`ImageService` feedback toggle, both `set` and `clear`), delete that
  image's `Ollama` tags in the same transaction (a plain DB delete, safe in the API process, unlike running `ConceptTagger`). The existing
  incremental rule "no `Ollama` tag -> select" then makes the next `description_tags` run rebuild them from non-rejected descriptions only,
  with no schema change. Rejects are exact immediately. Cost: after any approve/reject the image briefly loses all its `Ollama` tags
  (including ones from other, untouched descriptions) until the next pipeline run, and an image whose remaining descriptions produce no
  tags just stays without them.
- **B. Timestamp and batch-only.** Add `updated_at` to feedback (migration), extend the incremental selection to `feedback.updated_at >
  latest tag`. No temporary loss, but a rejected description's tags stay searchable until the next batch, which violates "rejected is
  excluded from search" for that window, and clearing feedback (a row delete) leaves no timestamp at all.

### Embeddings

Embeddings are per description and do not depend on approval. Filtering at query time (the predicate) makes reject and re-approve instant
and avoids deleting vectors that would have to be recomputed on re-approval. Recommended: keep generating them for every description and
filter only at query time. Skipping rejected descriptions in the job would save little and would need re-embedding on approve.

### Resolved by the existing decisions

- Unreviewed (no feedback row) stays included; "rejected" is `approved IS FALSE` (shared predicate).
- Approve after reject re-includes: query-time consumers, immediately; tags, on the next run (option A).
- Notes have no feedback and are unaffected.

### Questions for the user

1. Tags on feedback change: option A (delete the image's `Ollama` tags immediately, rebuild on the next pipeline run) or B (timestamp, batch-only)?
2. Embeddings: query-time filtering only (recommended), or also skip rejected descriptions in the embedding job?
3. Should the feedback change also trigger anything beyond tag deletion (for example nothing for lemmas, which are filtered at query time already)?

### Decisions (user, 2026-10-01)

1. Tags: delete-on-change (option A). Embeddings: no skipping, filter at query time only.

## Design (bounded, no separate spec needed)

Scope, all behind the shared predicate `description_not_rejected` from the 151/152 branch:

1. `ImageRepository.get_similar_by_description`: apply the predicate to both `source_desc` and `cand_desc`. Tests: a rejected source or candidate
   description is ignored; approve/unreviewed still match; re-approval restores.
2. `build_tags_from_descriptions` input: `get_images_and_descriptions_needing_tags` (and the full-run reader `get_images_and_descriptions`) exclude rejected
   descriptions, so an image whose descriptions are all rejected gets no `Ollama` tags. Tests in `tests/integration/test_images_repository_description_tagging.py`.
3. `ImageService._toggle_description_feedback`: after any set or clear, delete that image's `Ollama` tags in the same transaction
   (`repository/tags.py`'s `delete_tags_for_images("Ollama", [image_id])`, a repository call; no commit inside the repository). The next
   `description_tags` run rebuilds them (incremental rule: no `Ollama` tag -> selected).
4. No change to `build_image_description_embeddings`, lemmas or `source=description_all` (already filtered).
5. Docs in the same change: `backend_api.md` (feedback endpoints: effect on tags, `source=description`), `docs/data-flow.md` (feedback row now
   consumed; the temporary tag gap), `CLAUDE.md` batch entry for `build_tags_from_descriptions`.

Known behavior to document: after any feedback change the image has no `Ollama` tags until the next pipeline run (`description_tags` step).

Acceptance: reject a description -> its words stop matching in `source=description` and (after the next tag run) in `Ollama` tags; approve or clear
-> they return; rejecting every description leaves no `Ollama` tags; `tests/integration/` whole root passes.

**Blocked on** the 151/152 work being merged to main (the predicate lives only on `worktree-152-notes-join-design`). Start implementation after
that merge, or on top of its commit `4198086`+ if the other session agrees.

## Implementation (2026-10-01)

Implemented in worktree `.claude/worktrees/149-feedback-exclusion` (branch `worktree-149-feedback-exclusion`, based on main 9222ba6), staged but not committed.
Delivered exactly the design above, plus one consistency fix: `has_description_embedding` also ignores rejected descriptions, so `source=description`
404s when all of the source image's descriptions are rejected (same as `description_all`). Added `DESCRIPTION_TAG_SOURCE` to `repository/tags.py`.
Verified: whole `tests/integration/` root (416), `Backend/tests` (351), `batch/tests` (197). Docs updated: `backend_api.md`, `docs/data-flow.md`, `CLAUDE.md`.
Not done: a live check against a real database through the UI (reject a description, confirm tags drop and return after a `description_tags` run).
