# P1 taxonomy and data model: implementation review

Spec: `docs/superpowers/specs/2026-10-02-meme-from-text/2026-10-02-p1-taxonomy-and-data-model-design.md`
Plan: `docs/superpowers/plans/2026-10-02-p1-taxonomy-and-data-model.md`
Branch: `worktree-p1-taxonomy`, base `5a9c6b8` (local `main`). One review iteration (CLAUDE.md): every task was reviewed by a fresh reviewer, then one whole-branch review (most capable model), one fix wave and one scoped re-review.

## Verification evidence (run on the branch head before the fix wave, re-run for the fix wave where noted)

| Check | Result |
|---|---|
| `pytest tests/taxonomy` | 76 passed before the fix wave, 82 after |
| `pytest tests/ai` | 21 passed (before and after) |
| `pytest batch/tests` | 199 passed (before and after) |
| `Backend` pytest | 356 passed (before the fix wave; the wave changed no backend file) |
| Frontend `tsc -b`, `eslint src/ --max-warnings 0`, `vitest run` | exit 0 / exit 0 / 31 files, 278 tests passed (before the fix wave; the wave changed no frontend file) |
| Embedding probe (live Ollama) | 4/4 pairs passed, model `bge-large`, digest `b3d71c92805938e2c9d78e6f35e82d7cc1bd77e05b47dd2ab3016240f4021c15`, dimension 1024 |
| Corpus coverage probe (live, read-only role, `general`) | 81 usable images, report in `docs/superpowers/reviews/2026-10-02-p1-vocabulary-coverage-review.md` |

`tests/integration` and `tests/rules` were not run: nothing shared (`rules/normalize.py`, `repository/ocr_lemmas.py`, `rules/concept_tagger.py`, `batch/build_bow.py`) was touched and no table was added (CLAUDE.md "run the right test scope").

## Acceptance criteria

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| 1 | `batch/data/taxonomy/` has `taxonomy.yaml` and the four vocabularies, reviewed and approved by the user | Met | Vocabularies reviewed through the coverage gate (user chose "add 3 topics"): emotion 26, stance 15, tone 10, topic 19. `tests/taxonomy/test_shipped_taxonomy.py` lints them |
| 2 | The `taxonomy` core loads with no ML imports (verified by importing it in a clean venv built from `Backend/requirements-backend.txt`) | **Met with deviation** | Verified statically instead: `tests/taxonomy/test_core_imports.py` AST-checks that the core modules import only stdlib, `yaml` and `taxonomy.*`, and PyYAML is already pinned in `Backend/requirements-backend.txt`. No clean-venv build was made (installing into a venv is the documented venv-rot risk and the static check covers the same failure) |
| 3 | `memeprofile` and `queryprofile` schemas exist, are in `all.schema.json`, with TS, Kotlin and Python types regenerated and no CI diff | Met | `tests/taxonomy/test_profile_schemas.py` (registration, axes vs taxonomy, example validation, enum sync). Generated Pydantic classes are named `Schema` per module (repo convention, reviewer confirmed). Frontend `tsc -b` passes with the new `all.d.ts`. Pre-existing generated files were not committed as drift |
| 4 | Embedding model pulled in Ollama; name, digest and dimension recorded; `PROFILE_EMBEDDING_DIM` defined from the measured value | Met | Spec addendum "embedding probe result"; `Storage/models.py` `PROFILE_EMBEDDING_DIM = 1024`; `taxonomy.embedding_model: bge-large` in `environments/settings.yaml` |
| 5 | `similarity.json` generated, reviewed for wrong neighbours, consistency test passes | **Met with deviation** | The generated embedding neighbours were noise (opposites and unrelated keys at every usable threshold), so the user chose hand-curated pairs: 25 pairs in `similarity_overrides.yaml`, `similarity_threshold` 0.9. `tests/taxonomy/test_shipped_similarity.py` passes. See "Deviations" |
| 6 | The corpus sample report exists in `docs/superpowers/reviews/` and the vocabularies were adjusted once from it | Met | `2026-10-02-p1-vocabulary-coverage-review.md`, with the Decisions section filled in |
| 7 | P2 and P3 can start from the spec alone: table design, schemas, loader API, versioning and staleness rules complete and unambiguous | Met after the fix wave | The final review found the staleness rule ambiguous once soft-match pairs sat inside the versioned file. The fix wave moved them out; the spec addendum now states that the version covers only extraction-relevant content and lists notes for P2 and P3 |
| 8 | `tracker.md` and ADR-002 updated | Met after the fix wave | Tracker shows P1 `done`. ADR-002 had two statements contradicting the build; corrected in the fix wave |

## Deviations from the plan and spec (all decided or confirmed)

1. **Embedding model provisional via Ollama** (user decision, before planning): English model served by Ollama, called over HTTP; P6 may overturn it.
2. **Vocabulary changes:** topic gained `music`, `religion`, `horror` after the corpus probe (user decision). Nothing else changed.
3. **Similarity is curated, not auto-generated** (user decision after the controller showed the noise): 25 pairs, threshold 0.9, taxonomy version 2.
4. **Overrides live in their own file** (`batch/data/taxonomy/similarity_overrides.yaml`), outside `content_hash` and the lock, with `overrides_hash` stored and checked in `similarity.json` (controller ruling R8, from the final review): tuning soft-match pairs never stales meme profiles.
5. **Generated model naming:** classes are `Schema` per module, not `MemeProfile` (generator behaviour, repo convention).
6. **Execution order:** Task 7 ran before Task 6 (ruling R4; independent) and Task 8's shipped-similarity test was created after its artifact (ruling R6).

## What was fixed

- Final review, Important: overrides inside the versioned file (moved out), ADR-002 contradictions (corrected), Docker image cannot load the taxonomy (recorded as a P3 note, not fixed, by ruling R9).
- Final review, Minor: enum-sync test added, generator docstring corrected; the other minors recorded as P2/P3 notes (ruling R10).

## What was not fixed (explained)

- `Dockerfile.backend` does not copy `taxonomy/` or `batch/data/taxonomy/`. Deliberate: the Docker smoke test cannot run in this environment and P3 is the first backend importer. Recorded in the spec addendum, "Notes for P2 and P3".
- Deferred per-task minors (all judged acceptable by the final review): probe edge-case handling and test gaps (Task 1), unwrapped YAML parse errors, shallow-frozen dataclass and cache tests (Task 2), falsy axis values accepted, `tax or load()` truthiness (Task 3), lint cannot see duplicate keys because the loader rejects them, label style and overlapping definitions (Task 4), probe guard is a substring check and report noise (Task 5), weak null assertions (Task 7), corrupt lock file raises a raw `JSONDecodeError` (Task 6), `check_artifact` robustness (Task 8).

## What was intentionally ignored

- Unused vocabulary keys in the coverage sample (12 emotions, 9 stances, 3 topics, 4 tones): a single probe on 81 images cannot show a key is unneeded. Any further vocabulary change belongs to P6's gold set.
- `amusement` (86%) and `relate` (46%) dominance: a ranking-time concern (frequency weighting in P4), not a vocabulary one.
- Appendix A of the spec still lists topic with 16 keys: it is the historical draft; the addendum records 19.

## Review status of the fix wave

The scoped re-review of the fix wave (commits `333c832`, `85aa27d`, `a304c1f`, range `8f2fedf..a304c1f`) verdicted every finding ADDRESSED with no new Critical or Important breakage. Checks it made: `content_hash` still excludes `similarity_overrides.yaml` and the lock logic is unchanged; the loader rejects a `similarity_overrides` key left in `taxonomy.yaml`; `overrides_hash` is LF-normalised, `""` when the file is absent, and used by `build_artifact` and `check_artifact`; the 25 pairs (14/3/5/3) moved verbatim and no vocabulary file changed; lock and artifact agree on version 2 and `content_hash`; no stub env file or secret is in the diff. One theoretical edge it noted (an artifact without an `overrides_hash` key is flagged as drifted against a taxonomy without an overrides file) is harmless and strict by design.

The controller re-ran the post-fix suites on the final head: `tests/taxonomy` 82 passed, `tests/ai` 21 passed, `batch/tests` 199 passed. The fix wave changed no backend or frontend file, so the Backend (356 passed) and frontend (`tsc -b`, eslint, 278 vitest tests) results above stand.

**Verdict:** ready to merge. Open items for the merge decision: none blocking. The `Dockerfile.backend` gap is a recorded P3 task, not a P1 defect.
