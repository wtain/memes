# Dependency management notes

This documents how Dependabot actually behaves in this repo, and a real drift bug it
surfaced. Written 2026-07-21 after merging PRs #64–80 and hitting a `pip install`
failure caused by the drift below. See `.github/dependabot.yml` for the live config
and `CLAUDE.md`'s "Python environments" section for the requirements-file inventory.

## How Dependabot's pip ecosystem actually discovers files

A single `dependabot.yml` entry with `package-ecosystem: pip` and a `directory:` does
**not** only track `requirements.txt`. Per `dependabot-core`'s Python file fetcher
(`python/lib/dependabot/python/shared_file_fetcher.rb`, method `req_txt_and_in_files`),
it lists every file in that directory ending in `.txt` or `.in`, and keeps the ones
whose filename contains `requirements` (or, failing that, whose content looks like a
requirements file). There is no `dependabot.yml` key to list individual files, and
none is needed — this is why the root `pip` entry already produces PRs against
`requirements.txt`, `requirements-dev.txt`, and `requirements-cuda.txt` without any
extra config (confirmed empirically: PR history includes root-level `torch` bumps,
which only exists in `requirements-cuda.txt`, and a `pathspec` bump, a transitive pin
that only appears via `requirements-dev.txt`/`requirements.txt`'s `black`/`autoflake`).

**The catch:** all requirements files Dependabot finds in one directory are resolved
together as a single dependency graph. If bumping package X to the latest version
would require changes elsewhere that conflict with something else pinned in that same
directory, Dependabot can silently skip creating a PR for X — there's no visible error,
just no PR. This is exactly what happened here.

## The bug: two `requirements-backend.txt` files had drifted apart

Before this fix, there were **two** files with the same name and the same nominal
purpose (FastAPI server deps only, no ML stack):

| File | Directory | Used by | Dependabot entry | Last real bump |
|---|---|---|---|---|
| `requirements-backend.txt` (root, now deleted) | `/` | `Dockerfile.backend` (production image) | `/` pip entry, combined with the giant `requirements.txt` ML/batch stack | `fastapi==0.128.0` — stuck, never bumped |
| `Backend/requirements-backend.txt` | `/Backend` | CI (`backend-tests.yml`, `backend-coverage.yml`, `integration-tests.yml`) | `/Backend` pip entry, isolated | `fastapi==0.139.2` — actively bumped |

Root's copy sat in the same directory as `requirements.txt` (150+ pinned packages:
OCR, CLIP, PaddleOCR, OpenCV, PyTorch, etc.), `requirements-dev.txt`, and
`requirements-cuda.txt`. Per the mechanism above, Dependabot resolves all of those
together — and something in that combined graph was blocking `fastapi` (and likely
other backend packages) from bumping in that directory, even though the *exact same*
package bumped fine in the isolated `/Backend` directory.

**Net effect: CI was testing against different backend dependency versions than what
`Dockerfile.backend` actually shipped to production.** This wasn't hypothetical — it
was already flagged as a risk in `docs/audit/audit-2026-06-25.md` line 239
("unclear whether it is manually maintained or generated, and whether it stays in
sync with `requirements.txt`") but never resolved.

It also caused a concrete, immediate failure this session: PR #72 bumped `selenium` to
4.46.0 (root, in `requirements.txt`), which requires `certifi>=2026.2.25`. Root
`requirements.txt` was still pinned to `certifi==2026.1.4`, so `pip install -r
requirements.txt` broke immediately after merging — a real symptom of the same
"combined graph, partial bump" failure mode. Fixed by bumping `certifi` to `2026.2.25`
(commit `6dc2b7f`).

## The fix

- Deleted the root `requirements-backend.txt`.
- `Dockerfile.backend` now `COPY`s `Backend/requirements-backend.txt` instead — one
  file, one Dependabot entry, CI and production always match.
- Regenerated `Backend/requirements-backend.txt` from a clean-venv `pip freeze`
  (not a hand merge) because `Dockerfile.backend` builds wheels with
  `pip wheel --no-deps`: the file must contain the **full closure** (direct +
  transitive pins), not just the top-level packages. A hand merge of the two old
  files would have produced a broken image — bumping `pydantic` to `2.13.4` requires
  `pydantic-core==2.46.4` exactly (verified via PyPI's JSON API), and the newer
  `fastapi==0.139.2` pulls in `starlette==1.3.1` (a major-version jump from the old
  `0.50.0`) plus two packages neither old file had pinned: `annotated-doc` and
  `packaging`. Verified by actually building `Dockerfile.backend` locally with the
  new file — wheels build and install cleanly with `--no-deps`.
- Updated `CLAUDE.md`'s requirements-file table and `Readme.md`'s Python
  dependencies section to point at `Backend/requirements-backend.txt` as the single
  source of truth, and to note it must be regenerated via `pip freeze` in a clean
  venv, not hand-edited.
- Added comments to `.github/dependabot.yml` explaining the auto-discovery/combined-
  graph mechanics above, so the next person doesn't have to reverse-engineer
  `dependabot-core` again.

## Follow-up fix (separate session, same day): production image couldn't boot at all

While smoke-testing the `Dockerfile.backend` rebuild above, `python -c "import
app.main"` inside the built image failed with `ModuleNotFoundError: No module named
'config'`. `Storage/config.py` imports `config.settings`, which in turn reads
`environments/settings.yaml` at import time — but `Dockerfile.backend` never `COPY`d
the `config/` package or the `environments/` directory. Confirmed via `git show
HEAD~1:Dockerfile.backend` that this predated every change in this session.

Fixed with tests first (`tests/docker/test_backend_image_boots.py`, requires a local
Docker daemon, runs in CI as a step in `Backend Docker Build`):

1. Wrote two failing tests that build the real `Dockerfile.backend` and actually run
   the container — `test_image_imports_app_main` and
   `test_container_boots_and_stays_running`. Confirmed both failed with the exact
   `ModuleNotFoundError: No module named 'config'` traceback above.
2. Added `COPY config ./config` and `COPY environments ./environments` to
   `Dockerfile.backend`. Added a `.dockerignore` (there wasn't one) excluding
   `.env`/`.env.*`/`environments/.env*` so copying `environments/` can never leak the
   gitignored secrets that live alongside the tracked `settings*.yaml` files there.
3. Re-ran the tests — new failure: `ModuleNotFoundError: No module named 'Backend'`.
   `Backend/app/main.py`'s own internal imports are absolute
   (`from Backend.app.api.diagnostics import ...`, matching how it's run locally —
   `uvicorn Backend.app.main:app` from the repo root, per `CLAUDE.md`), but the
   Dockerfile flattened `Backend/app` to `./app` and pointed gunicorn at `app.main:app`
   — a layout that never matched the code's own import convention, in production only.
   Fixed by preserving `Backend` as a package (`COPY Backend/__init__.py
   ./Backend/__init__.py` + `COPY Backend/app ./Backend/app`) and changing the
   gunicorn `CMD` to `Backend.app.main:app`.
4. Re-ran the tests — `BASE_PATH environment variable is required but not set`. This
   one's not a Dockerfile bug: `BASE_PATH` is a legitimately required runtime secret
   (per `CLAUDE.md`'s Configuration section). Added it to the test's dummy env
   alongside `DATABASE_URL`/`APP_ENV`.
5. Both tests pass. Verified `test_image_imports_app_main` itself against a stale
   assumption too — it originally asserted the old `import app.main` path; updated to
   `import Backend.app.main` to match the real fix in step 3.

## Follow-up (2026-07-26): missing `batch/clusterize` import, and a 33GB build context

A new feature (`Backend/app/services/ingestion_service.py`) started importing
`batch.clusterize.PROXIMITY_THRESHOLD`, and `Dockerfile.backend` never copied `batch/` at
all — same class of bug as the `config`/`environments` one above, caught the same way
(`tests/docker/` failed with `ModuleNotFoundError: No module named 'batch'`). Fixed by
copying only the two files actually needed — `COPY batch/__init__.py ./batch/__init__.py`
and `COPY batch/clusterize.py ./batch/clusterize.py` — not all of `batch/`, which is
mostly offline enrichment scripts with heavy ML deps that don't belong in
`requirements-backend.txt`.

That surfaced a second, much bigger problem while verifying the fix locally: the Docker
build context transfer alone took multiple minutes and kept growing past 500MB, even
though `.dockerignore` already existed. Cause: `.dockerignore` has no relationship to
`.gitignore` — files that are gitignored (untracked, invisible to `git`) are **not**
automatically excluded from a Docker build context. `Storage/backups/` (gitignored local
Postgres dumps, 33GB on this machine) and `batch/images/` (the actual meme corpus,
tens of thousands of files) were both being sent as build context on every single build,
despite neither ever being referenced by any `COPY` in `Dockerfile.backend`. Added
explicit excludes for both, plus every other top-level directory the backend image never
touches (`AndroidClient/`, `Frontend/`, `docs/`, `documents/`, `logs/`, `scripts/`,
`tools/`, `tests/`) and common artifact patterns (`.pytest_cache/`, `htmlcov/`, `*.log`).
Context transfer dropped from 500MB+/climbing to 764KB in ~3s; the full `tests/docker/`
suite (which builds the image for real) went from not finishing in 10 minutes to 44s.

**Takeaway for next time:** if a Docker build (or its build-context transfer step
specifically) is unexpectedly slow, check `.dockerignore` against what's *actually on
disk* — `du -sh */` at the repo root — not just against what the Dockerfile's `COPY`
lines reference. A directory can be gitignored, huge, and still fully present in every
build context.
