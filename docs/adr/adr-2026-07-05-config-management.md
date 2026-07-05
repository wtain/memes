# ADR 2026-07-05: Config management — Dynaconf, tracked YAML + secrets-only .env

STATUS: ACCEPTED

Full design: `docs/superpowers/specs/2026-07-05-config-management-migration.md`

## Context

Configuration for this project (Backend, batch pipeline, Storage) lived entirely in gitignored `.env.<environment>` files. This caused the three environments' config to diverge silently (`.env.it` was missing over a dozen keys the other two defined) and left ~20 files reading `os.getenv("KEY", <hardcoded default>)` with defaults duplicated per call site instead of centralized.

## Decisions

1. **Scope: Python only.** Backend, `batch/`, `Storage/`. Frontend (Vite) and Android are untouched — Vite requires `VITE_`-prefixed `.env` files and can't consume YAML; Android has no dependency on this layer.

2. **Tool: Dynaconf**, over a hand-rolled PyYAML deep-merge loader or `pydantic-settings` with a YAML source. Dynaconf natively supports layered environments (`[default]` + per-environment sections that merge automatically) and an `os.environ` overlay (needed for secrets and CI/Docker overrides), with almost no custom merge code. `settings.KEY` is a close, largely mechanical replacement for `os.getenv("KEY")`, keeping a full (non-phased) migration tractable.

3. **Secrets/config boundary:** `.env.<environment>` = "not safe to commit" (credentials, or values tied to one physical machine/network — `DATABASE_URL`, `BASE_PATH`, `SERP_API_KEY`, LAN-facing origins like `ALTERNATIVE_FRONTEND_ORIGIN`). Tracked YAML = "safe to commit" (repo-relative file paths, tuning parameters, feature flags, deterministic localhost origins). Rejected keeping machine-specific-but-non-secret values (e.g. `BASE_PATH`) in a third "local overrides" YAML layer — one gitignored `.env` file per environment is a simpler mental model than three tiers.

4. **New discriminator env var: `APP_ENV`** (`metal`/`general`/`it`), set once per `.env.<environment>` file. Chosen over Dynaconf's own default (`ENV_FOR_DYNACONF`) to keep the variable name tool-agnostic — a future tool swap wouldn't require renaming it in every `.env.*` file.

5. **`default_env` is `"general"`**, not `"metal"` — `general` is the primary/reference environment and already the most complete of the three `.env.*` files today. If `APP_ENV` is ever unset, Dynaconf falls back to `default` + `general`, not an undefined environment.

6. **Package location: new top-level `config/` package** (`config/settings.py`), not an extension of `Storage/config.py`. `Storage/` is scoped to the DB access layer per the existing architecture doc; app-wide config is a distinct concern.

7. **File layout: one tracked YAML file per environment** (`environments/settings.yaml` for `default:`, plus `settings.metal.yaml`/`settings.general.yaml`/`settings.it.yaml`), not one file with `default:`/`metal:`/`general:`/`it:` sections. Dynaconf merges sections across every file in `settings_files` regardless of which physical file they live in, so splitting costs nothing at the loader level and matches this repo's existing convention of one file per environment per concern (`rules.general.json`, `text-concepts.metal.json`, etc.) instead of introducing a new single-file convention.

8. **`environments/settings*.yaml` stays under `environments/`**, not `config/` — preserves the existing CLAUDE.md invariant that environment-specific config lives in `environments/`; `config/settings.py` is just the loader code that points at it.

9. **Full migration in one pass**, not a phased rollout (layer + Backend + a few exemplar batch scripts, rest as follow-up). All ~19 `batch/*.py` files plus `Backend/app/main.py`, `Backend/app/services/image_store.py`, `Storage/config.py` are migrated together — avoids two config systems (`os.getenv` and `settings.X`) coexisting in the codebase during a follow-up gap.

10. **Batch scripts get an explicit `--env {metal,general,it}` CLI flag** (falling back to `APP_ENV` if already set), calling a new `config.settings.load_env()` helper. This replaces today's implicit mechanism — batch scripts either silently no-op `load_dotenv()` or read whatever's already in `os.environ`, depending on the operator having manually sourced the right `.env.<environment>` file into their shell first. The new flag makes environment selection explicit and fast-failing instead of a silent wrong-environment footgun.

11. **Automated integration tests, not just manual verification**: `Backend/tests/test_config_integration.py` and a new `batch/tests/test_env_loading.py` exercise the real tracked `environments/settings*.yaml` against fixture (non-real) `.env` files, proving environment selection, secrets overlay, and fast-fail behavior all work — without depending on any developer's real local secrets.

## Consequences

- New dependency: `dynaconf` (pure-Python, added to both `requirements.txt` and `requirements-backend.txt`).
- `config/load_env(name, base_dir=...)` takes an overridable `base_dir` specifically so tests can point at a fixture directory rather than real `environments/.env.*` files.
- Two pre-existing issues were found during the file-by-file audit and are explicitly *not* fixed here (documented in the spec, not silently changed): `batch/tools/spot_check_losses.py` reads a `PROFILE` env var that no `.env.*` file ever set (distinct from `TAGGING_PROFILE` used everywhere else); `batch/load_images_from_internet.py`'s `SERP_API_KEY` was newly identified as a real secret that must stay in `.env`, not tracked YAML.
