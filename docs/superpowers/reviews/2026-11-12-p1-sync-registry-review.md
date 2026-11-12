# P1 sync registry: review

Status: done (static review by a fresh subagent; the reviewer had read-only tools, so it ran no mutations; the controller ran them,
see "Verification after fixes")
Spec: docs/superpowers/specs/2026-11-12-environment-data-sync/2026-11-12-p1-sync-registry-design.md
Axes: strength of the guard tests, correctness against `Storage/models.py` and the decisions log, maintainability.
Counts reported: 0 critical, 3 high, 4 medium, 6 low. One review iteration, per the project rules.

Ground truth the reviewer confirmed: 46 tables, 25 D / 6 P / 15 X; class, scope, level, key, digest group, nullify and prod writer of every
entry match the decisions log; keys equal the real primary keys; `setval` follows integer primary keys (`BigInteger` included);
levels agree with the foreign-key topology; the two deviations from the design (UUID absorptions need no `setval`, comics keyed by
primary key) are correct. `test_the_rendered_page_is_current` cannot fail on a clean checkout on Windows or Linux (universal newlines on read, LF on write).

## High

| # | Finding | Disposition |
|---|---|---|
| H1 | The absorptions "second pass" in the registry note cannot run: `ck_popularity_absorptions_shape` requires `reverses_id` on a `reverse` row, and `uq_popularity_open_loser` allows one open absorb row per loser | **Fixed.** Verified in `Storage/models.py:327-341`. Note and design now say: load in `created_at` order (absorb row, its reverse row, then UPDATE the absorb row's `reversed_by_id`). A real-database test belongs to P2 |
| H2 | The D versus X boundary is not pinned (demoting `comics` or promoting `tmp_duplicates` would pass) | **Fixed.** `EXPECTED_X`, `EXPECTED_D` and `PULLED` are hard-coded in the test (`test_class_boundaries_are_pinned`) |
| H3 | CI path filters skip the guard when only `sync/**`, `tests/sync/**` or the page change; the step also sits after steps that can fail | **Fixed.** Paths added to both filters; the step has `if: always()` |

## Medium

| # | Finding | Disposition |
|---|---|---|
| M1 | Upsert never deletes: a deleted concept, comics or panel lingers on prod; switching `tags` to upsert passes the tests | **Fixed.** New `prune` field (concepts*, comics, comics_panels) and a test that image-scoped children are `replace_per_image` and every global set states whether it prunes. Unique keys of comics are noted for P2 (transient unique violations on rename-swaps need an ordered or deferred apply) |
| M2 | The class-P cascade rule is only a note | **Fixed.** New `pull_before_promote` field, and a test that a P table has it exactly when it has a CASCADE foreign key to `images` |
| M3 | Holes in the class-D foreign-key test (VIA chains under a global set; over-strict for a global table referencing comics) | **Fixed.** The test now computes "image-bounded" along the scope chain |
| M4 | `nullify` does not check nullability | **Fixed.** Asserts the column is nullable, not a primary key and not the scope column |

## Low

| # | Finding | Disposition |
|---|---|---|
| L1 | Non-promotable data ships by design: draft comics, panel snapshots of non-shipped images, `flagged_reason` in `image_extras`, loser filenames in absorptions; a `built` comics with a blanked `built_image_id` is inconsistent | **Documented, owner call** (design, "Accepted data exposure"): either scope comics to built ones or accept |
| L2 | The promotable predicate is prose only | **Fixed.** `PROMOTABLE_IMAGES_SQL` in `sync/model.py` |
| L3 | `nullify` mixes "always blank" and "blank when absent" | **Documented** in the design; derivable from the target class |
| L4 | The import test globs only `sync/*.py` and would forbid DB drivers in later tools | **Fixed.** The rule now covers the registry core (`model.py`, `registry.py`, `render.py`) only |
| L5 | `PROD_WRITERS` unused; mutable dict and `assert` stripped under `-O`; only imported models are seen | **Fixed** (writers are checked against `PROD_WRITERS`; the duplicate check raises). The "only imported models" point stays: `Storage.models` is the single source of truth (CLAUDE.md) |
| L6 | Spec and docs drift; T4 links missing | **Fixed** (design updated; links added to the S1 spec and `docs/image-derived-data.md`); `.gitattributes` pins the page to LF |

## Verification after fixes

`pytest tests/sync` 19 passed; `pytest tests/docs` 15 passed. Mutation run by the controller (`scratchpad/p1_mutations.py`): 18 deliberate
registry mistakes, each fails at least one named test; baseline has no failures.
