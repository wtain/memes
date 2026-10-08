# P5: Web UI (searcher experience) — Design

Status: done
Plan: docs/superpowers/plans/2026-10-08-p5-web-ui.md
Originates from: docs/superpowers/specs/2026-10-02-meme-from-text/tracker.md (project P5, narrowed to the searcher UI on 2026-10-08)
Follow-ups: none yet (P5b rating widget and P5c curator tools are separate specs, see Out of scope)

Epic: `docs/superpowers/specs/2026-10-02-meme-from-text/README.md`. Inherited contracts: the P4 endpoint `POST /api/meme-from-text/search` and its handoff notes (`2026-10-07-p4-retrieval-fusion-and-api-design.md`, addendum), the generated types in `Frontend/memes-frontend/src/types/generated/all.d.ts`, the P1 taxonomy (`batch/data/taxonomy/`), and the existing frontend conventions (React 19, React Router 7, Tailwind 4, `MemesApi` injection, vitest with Testing Library).

## Problem

The retrieval pipeline works and meets its thresholds (nDCG@10 0.526, hit@10 1.000 on the gold set), but nobody can use it: there is no UI. The tracker row P5 bundled three separable parts: the searcher experience (US-1 to US-4, US-6), the rating widget (US-5), and the curator tools (meme-profile editor, coverage view; US-8 and US-9). Only the first is needed for the epic's milestone M3 "Usable", and only the first can be built without new backend storage. This spec covers the searcher experience. It adds one small backend endpoint (the vocabulary) because the chips editor and the facet labels need it.

## Findings that shape the design (verified 2026-10-08)

- The existing `/search` page is a short keyword box with tag facets driven by `GET /api/images`; its state lives in the URL (`useSearchParams`). The message-search flow shares almost nothing with it (long pasted text, an editable reading, explanations, degraded states, a different endpoint).
- The backend reads only the first 600 characters of a message (`parse.max_input_chars`); the public schema accepts up to 8,000. FR-1 forbids silent truncation, so the UI limit must be 600.
- The generated types already contain `MemeFromTextRequest`, `MemeFromTextResponse`, `MemeFromTextResult`, `AxisExplanation`, `QueryProfile` and `Facet`. The generator also emitted `QueryProfile1` (the same shape, for the request's `profile` field).
- Nothing serves the vocabulary to the browser. `taxonomy.yaml` carries per-axis `type`, `max_values` or `max_chars`, and per value a `key`, a `definition` and `labels` for en, ru and es.
- A response never fails because a model failed: `degraded`/`fallback` describe the state, `meta.available: false` marks an environment without a parser (`it`). Fields whose value is null are omitted. Edited profiles must be a full query profile and skip the parse.
- Only about 5% of the `general` library has meme profiles; results will be thin until the backlog run (board task 158).
- The frontend already has vitest, Testing Library, user-event, jsdom and a `makeMockApi` helper; existing components to reuse: `MemeCard`, `MemeDetailsModal`, `MultiSelectFacet`, `Modal`.

## Decisions

### 1. Placement and scope

A new page at `/reply` with its own navigation entry ("Find a meme for a message"); the existing `/search` is untouched. Scope: US-1 to US-4 and US-6 (find, explanations, correct the reading, narrow by axis, keep working when the model is down), plus the vocabulary endpoint. Out of scope: see the list below.

### 2. Layout

Top: a message box with a visible label, a character counter (limit 600) and a Find button. Below it: the reading panel ("How I read your message"). Then two columns: facets and filters on the left, results on the right; under 768 px the facets collapse into a "Filters" disclosure above the results and everything stacks. Whitespace-only input does nothing and shows no error; at the limit the counter turns red and the button stays enabled. Find (or Ctrl+Enter) runs the full pipeline.

### 3. The reading panel

Shows the detected language, the situation phrase as an editable line (at most 160 characters, the taxonomy's `max_chars`), and one chip group per controlled axis (emotion, stance, topic, tone). A chip is a value with an × button; a group's "+" opens a keyboard-operable picker listing the axis vocabulary (labels from the vocabulary endpoint, definition as tooltip). Groups cap at the axis `max_values` (emotion 3, stance 2, topic 3, tone 2). A chip added by the user is sent with `source: "human"` and confidence 1. Removing every chip from an axis sends the axis empty, which excludes it from ranking.

Any edit marks the panel "edited", updates the edited profile immediately and re-runs retrieval after a 400 ms debounce, sending the message plus the full edited `profile` (so the server skips the parse). "Reset to original reading" clears the edit and re-runs with the original profile. Quick consecutive edits collapse into one request.

### 4. States and wording

- **reading** (first search): "Reading your message…" then skeleton cards. **refining** (edit or filter change): results stay visible with a subtle busy state.
- **degraded** (`degraded: true`): a banner "Limited understanding" explaining that a simpler match was used (wording from `fallback`: model unavailable, timed out, unusable answer, temporarily skipped, or embedding unavailable); the reading panel then says the message could not be read structurally and stays editable (an edit re-searches with the edited profile).
- **unavailable** (`meta.available: false`, or the endpoint missing): "Message search isn't available in this environment".
- **zero results**: "No memes fit", the active filters listed and individually removable.
- **error** (422 or 500): a plain retry message; it never echoes the user's text; the chips are kept.

### 5. Result cards

`MemeCard` plus a "Why this meme" disclosure showing, per axis: a score bar with a numeric value, the matched value in the meme's own words (for example "emotion: resignation, matched your annoyance"), and a "neutral" marker where the meme has no value on a queried axis; the meme's `situationText` and `useWhen` below. A result reached only by word matching (`axisPath: false`, degraded responses only) says so. A badge "+N similar" shows when duplicates were collapsed into the card (`clusterSize > 1`). Clicking the image opens the existing details modal. Explanations come from the response (the same scores used for ranking), never from a second model call (US-2 AC3).

### 6. Facets, filters, paging

The response's facets (per axis, over the filtered, duplicate-collapsed set) feed the existing multi-select facet widget, one group per axis. A selection becomes a hard filter in the request (OR within an axis, AND across axes) and re-runs retrieval with the current profile (the edited one if present, otherwise the original), never the parse. The first version offers axis filters only: the API's tag filters stay supported but get no UI. "Show more" appends the next page with `nextCursor` and the same request; there is no infinite scroll, so pages cannot repeat or skip; focus moves to the first new card. A response whose first item shows the list restarted (a stale cursor) replaces the list instead of appending.

### 7. State: one hook

`useMessageSearch` (a reducer) owns: `text`, `submitted` (the exact text last searched), `originalProfile`, `editedProfile`, `filters`, `pages` (items, `nextCursor`, `hasNext`, facets, meta) and `status` (`idle`, `reading`, `refining`, `ready`, `degraded`, `unavailable`, `error`). Find sends the text only and stores the returned profile as `originalProfile`. Every other request sends the text plus the profile in use. Each request has an `AbortController`; a response that arrives after a newer request began is dropped. The message stays out of the URL (it is long and private). `sessionStorage` keeps the text, the edited profile and the filters so Back and refresh restore the box and the chips (restoring re-runs the search, usually served from the 120 s server cache); every storage access is wrapped in try/catch and the page works without it. No language selector in the first version (the parser detects language reliably).

### 8. API layer

`MemesApi` gains `searchMemeFromText(request, signal?)` returning the generated `MemeFromTextResponse` and `getTaxonomy()`. `HttpMemesApi` implements both with `fetch`: a 422 becomes a typed validation error, a 500 a typed generic failure whose message never contains the text, an `AbortError` is swallowed. `QueryProfile1` is aliased to `QueryProfile` in one place; no hand-written response types.

### 9. Vocabulary endpoint (the only backend change)

`GET /api/meme-from-text/taxonomy`, read-only: `{version, axes: [{name, type, maxValues?, maxChars?, values?: [{key, definition, labels: {en, ru, es}}]}]}`. New shared schema `taxonomyresponse.schema.json` with regenerated TypeScript, Kotlin and Python types; a router function in `Backend/app/api/meme_from_text.py` (no repository: it reads the taxonomy the pipeline already loads); `backend_api.md` section; tests. The UI fetches it once per page load, builds the picker and facet labels from it, and refetches when a search response's `profile.taxonomyVersion` differs from the stored version. If the fetch fails, chips still render from their keys and only the "+" picker is disabled; search never depends on it. The UI shows English labels (the app is English) with the definition as a tooltip.

### 10. Accessibility and responsiveness (NFR-10)

A real `<textarea>` with a label; the counter is a polite live region that announces only when crossing the limit. Banners use `role="status"` (errors `role="alert"`); a single hidden status line announces "N memes found" when results change. Chips are a list with labelled remove buttons (`Remove emotion: annoyance`); the picker is a disclosure with a listbox operable by arrow keys, Enter and Escape; after removing a chip, focus moves to the next chip or to that axis's "+" button. "Why this meme" is a button with `aria-expanded`; score bars carry a text value and meaning never rests on colour alone. Facets are native labelled checkboxes in `fieldset`/`legend` groups. Skeletons respect `prefers-reduced-motion`; touch targets are at least 44 px.

## Out of scope

The rating widget (US-5, P5b; needs a database table, an endpoint and a gold-set export), the meme-profile editor and the admin coverage view (US-8, US-9, P5c; need new backend endpoints), a UI for tag filters, a language selector, saved or shareable searches, the Android client, and any change to the existing `/search` page.

## Testing

- Reducer and hook, with fake timers: the Find, chip-edit, filter and reset flows send the right requests and never drop the profile in use; debounce collapses edits; a superseded response is dropped and the abort signal fires; Show more appends with the stored cursor and a restarted list replaces; `sessionStorage` restore and a throwing storage.
- API client, with `fetch` mocked: request body, 422 mapping, 500 mapping, abort handling; no error contains the message text.
- Components against `makeMockApi`: reading panel (remove, add from picker, `max_values` cap, 160-character situation line, emptied axis sent empty, reset), result card (explanation toggle, neutral and duplicate badges, lexical-only wording), degraded and unavailable notices, zero-result state with removable filters, error state, facets re-run with the same profile.
- Accessibility assertions with Testing Library roles and ARIA (labelled controls, live regions, focus after removal, `aria-expanded`), no new test dependency.
- Backend: the taxonomy endpoint's payload shape and version, labels for all three languages, the generated model validating it, router test, `backend_api.md` in sync.
- Gates before commit: `tsc -b`, `eslint src/` with 0 warnings, `vitest run`, regenerate the type trees and verify there is no diff; the Backend root and the retrieval roots stay green.
- Manual check by the controller: after the user restarts a backend, open the page in the browser against the real parser and verify the states end to end.

## Acceptance criteria

1. `GET /api/meme-from-text/taxonomy` exists with a shared schema, regenerated type trees (TypeScript, Kotlin, Python), `backend_api.md` and tests; the `/reply` route and nav entry exist.
2. A pasted message (EN, RU or ES) returns ranked results with the reading panel, per-result explanations, duplicate badges and facets; empty input does nothing; over-length input is prevented at 600 characters with a visible counter; "Show more" pages without repeats or skips.
3. Editing chips, the situation line or filters re-runs retrieval only (the request carries the profile), debounced, with reset to the original reading; an emptied axis is excluded from ranking.
4. The degraded, unavailable, zero-result and error states exist as specified; with Ollama unreachable a search still returns results and shows the limited-understanding notice; no error message or log line contains the user's text.
5. The accessibility and responsive requirements of decision 10 hold (verified by tests and a manual pass).
6. `tsc -b`, `eslint src/` (0 warnings), `vitest run` and the backend roots pass; generated types have no diff.
7. The addendum records the manual verification, the tracker, `CLAUDE.md` (if the frontend section needs it) and the review report are updated.

## Risks

- Thin results until the profile backlog runs (about 5% of the library is profiled): the page works, the "No memes fit" state is honest, and the backlog run (board task 158) must precede any rollout.
- The first search after an idle period can take seconds while models load; the backend warm-up reduces this and the UI shows the reading state.
- The running servers need a restart to expose the new endpoints; the page degrades to the unavailable state until then.
- An edited profile with an unknown key is a 422; the picker offers only vocabulary keys, and a 422 keeps the chips with a retry message.
- A stale cursor restarts the list; handled by replacing instead of appending.

## Plan outline (for writing-plans)

1. Backend: the taxonomy endpoint, schema, generated types, `backend_api.md`, tests.
2. API layer: interface methods, `HttpMemesApi`, typed errors, `QueryProfile1` alias, tests.
3. State: the reducer and `useMessageSearch`, `sessionStorage`, tests.
4. Components: message box with counter, reading panel with chips and the picker, tests.
5. Components: result card with the explanation, the notices, the facets and filters, "Show more", tests.
6. The page, route, nav entry and responsive layout.
7. Controller step: a real-browser check against a restarted backend, accessibility pass, docs, review report and tracker.

## Addendum: result (2026-10-09)

Implemented on branch `p5-web-ui` (six plan tasks, their fix rounds, one whole-branch review with a final fix wave and a small focus follow-up, then a live check). `/reply` works end to end against the real backend.

### What was built

- **Backend:** `GET /api/meme-from-text/taxonomy` (schema `taxonomyresponse.schema.json`, `Backend/app/services/meme_from_text_taxonomy.py`, regenerated TypeScript, Kotlin and Python types, `backend_api.md`). The Kotlin generator now emits `List<JsonObject>` for arrays of inline objects (it emitted `List<Any>`, which kotlinx.serialization cannot serialise; P4's `AxisExplanation.matched` already had it on `main`).
- **Frontend:** `src/reply/` (types, pure helpers, reducer, `useMessageSearch`, `useTaxonomy`, storage), `src/components/reply/` (`MessageBox`, `ValuePicker`, `ChipGroup`, `ReadingPanel`, `ResultCard`, `ExplanationPanel`, `AxisFacets`, `ActiveFilters`, `StatusBanner`), `src/pages/ReplyPage.tsx`, the `/reply` route and a "Reply" nav link; two new `MemesApi` methods.
- Gates on the final commit: `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run` (443 tests, 165 of them new) and `vite build` all pass; Backend root 394 tests.

### Live verification (2026-10-09, real `general` database via the read-only role, real Ollama, a minimal backend on a free port without the scheduler)

- An English message returned a parsed reading with vocabulary labels (Frustration / Complain / Work), 20 results, facets, and "Why this meme" explanations in about 1.2 s end to end.
- Russian and Spanish messages were detected (`ru`, `es`) and parsed to the same English reading; 20 results each.
- Removing a chip re-ran retrieval only: the server accepted the edited profile (HTTP 200) and reported `parseMs: 0`, `embedMs: 0`.
- Show more replayed the same request body plus the cursor: HTTP 200, 40 unique cards, `cacheHit: true`, focus stayed inside the results, the hidden count line read "40 memes shown, more available".
- A facet filter re-ran with the same reading and no parse (`parseMs: 0`), showed the active-filter chip and "16 memes found".
- With Ollama unreachable the page showed "Limited understanding ... the language model is not reachable; the similarity model is not reachable", explained that the message could not be read structurally, and kept the reading panel editable.
- **Not verified:** the narrow-window layout (the browser tool could not change the viewport; the disclosure is tested by roles and attributes only), screen-reader announcements, the `it` unavailable state and the real 404 of a not-yet-restarted backend (both covered by tests only), and the Gradle compile of `Models.kt` (the wrapper cannot download its distribution here; the change is verified by regeneration, and `Models.kt` already used `JsonObject` fields under `@Serializable`).

### Deviations from the spec text (decided during planning and review)

1. "Show more" replays the exact request body of the list being paged (`lastRequest`) plus the cursor: the server ties a cursor to the whole request fingerprint, so rebuilding the request with the returned profile would restart the list.
2. A degraded original reading is never sent back as `profile` (it is empty and would be treated as an edited profile that ranks nothing).
3. `AxisFacets` is a new component (taxonomy labels, native checkboxes, selected values kept with count 0) instead of reusing `MultiSelectFacet`.
4. Abort handling lives in the hook; the API client rethrows `AbortError`.
5. sessionStorage also keeps the original reading and the submitted text, so a restore is one retrieval-only request.
6. The nav link reads "Reply" (spec decision 1 said "Find a meme for a message"; the page title carries that text).
7. A failed refine keeps the results, facets and filters on screen, and "Try again" re-runs with the current edits and filters (it falls back to a text-only search after a validation error); Show more is hidden while the status is `error`.
8. A 404 or 405 on the search call is shown as "unavailable" (a backend that has not been restarted), not as an error.
9. A "Show more" response whose degraded state differs from the list's, or whose first item equals the first item shown, replaces the list instead of appending; appended items are de-duplicated (the server cache expires after 120 s healthy, 10 s degraded, and the cursor is positional).
10. Beyond the spec: a visible hint at the 600-character limit, busy styling and restore skeletons, focus targets after self-removing controls and after Show more, one always-mounted polite live region for the banner texts, count wording "N memes shown, more available".

### Handoff notes for P5b (rating) and P5c (curator tools)

- Rating (US-5) needs a database table with a migration, an endpoint that stores query hash, parse version, meme id and rank, and an export into the gold-set format; the result card already has a place for a control next to "Why this meme".
- The meme-profile editor (US-9) and the coverage view (US-8) need new backend endpoints (profile read and edit with human-verified marking and vector refresh, coverage counts); the taxonomy endpoint and the chips components (`ChipGroup`, `ValuePicker`) are reusable for the editor.
- Known gaps: the existing `MemeCard` image is not keyboard-focusable, so after "Show more" focus lands on the first button inside the new card; two polite live regions (banner and result count) can announce together; on `general` only about 5% of the library has profiles until the backlog run (board task 158).
