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

7. **File layout: one tracked YAML file per environment** (`environments/settings.yaml` common defaults, plus `settings.metal.yaml`/`settings.general.yaml`/`settings.it.yaml`), matching this repo's existing convention of one file per environment per concern (`rules.general.json`, `text-concepts.metal.json`, etc.) instead of one file with internal `default:`/`metal:`/`general:`/`it:` sections.

   Implementation note (discovered during build, corrects the original spec draft): Dynaconf's built-in `environments=True` + `env_switcher` section-merging mode does **not** scope a file's environment section to only-when-active — empirically, loading `settings.general.yaml`'s `general:` section while active env was `it` still leaked `general`'s keys into `it`'s resolved settings, whenever both files were present in `settings_files`. `config/settings.py` instead builds the file list itself (`[settings.yaml, settings.<name>.yaml]`) and constructs a **fresh** `Dynaconf` instance per environment — no `environments=True`, no section wrapping in the YAML files. `config.settings.settings` is a thin proxy object whose backing instance `load_env()` swaps out wholesale (Dynaconf's own `reload()`/`configure()` merge onto existing state rather than replacing it, which reintroduces the same leak on env switch within one process — a fresh instance sidesteps that too).

8. **`environments/settings*.yaml` stays under `environments/`**, not `config/` — preserves the existing CLAUDE.md invariant that environment-specific config lives in `environments/`; `config/settings.py` is just the loader code that points at it.

9. **Full migration in one pass**, not a phased rollout (layer + Backend + a few exemplar batch scripts, rest as follow-up). All ~19 `batch/*.py` files plus `Backend/app/main.py`, `Backend/app/services/image_store.py`, `Storage/config.py` are migrated together — avoids two config systems (`os.getenv` and `settings.X`) coexisting in the codebase during a follow-up gap.

10. **Batch scripts get an explicit `--env {metal,general,it}` CLI flag** (falling back to `APP_ENV` if already set), calling a new `config.settings.load_env()` helper. This replaces today's implicit mechanism — batch scripts either silently no-op `load_dotenv()` or read whatever's already in `os.environ`, depending on the operator having manually sourced the right `.env.<environment>` file into their shell first. The new flag makes environment selection explicit and fast-failing instead of a silent wrong-environment footgun.

11. **Automated integration tests, not just manual verification**: `Backend/tests/test_config_integration.py` and a new `batch/tests/test_env_loading.py` exercise the real tracked `environments/settings*.yaml` against fixture (non-real) `.env` files, proving environment selection, secrets overlay, and fast-fail behavior all work — without depending on any developer's real local secrets.

12. **`DATABASE_URL` validation happens in `load_env()`, not in module-level `_build()`** (discovered during build, corrects the original spec draft). `_build()` only registers the `Validator("DATABASE_URL", must_exist=True)`; it does not call `.validate()`. The module-level `settings = _SettingsProxy(_build(...))` line runs at import time, before a batch script's `main()` has called `load_env()` — if `_build()` validated eagerly, every batch script would crash on import with `DATABASE_URL is required`, since secrets aren't loaded yet at that point. `load_env()` calls `instance.validators.validate()` itself, immediately after loading `.env.<name>` and before swapping the instance into `settings`, so batch scripts still fail fast — just after `load_env()` runs rather than at import. Backend never calls `load_env()` (uvicorn's `--env-file` already populates `os.environ` before `config.settings` is imported), so it relies on `Storage/config.py`'s pre-existing `if not DATABASE_URL: raise RuntimeError(...)` guard for its own fail-fast behavior, independent of Dynaconf's validator.

## Consequences

- New dependency: `dynaconf` (pure-Python, added to both `requirements.txt` and `requirements-backend.txt`).
- `config/load_env(name, base_dir=...)` takes an overridable `base_dir` specifically so tests can point at a fixture directory rather than real `environments/.env.*` files.
- Two pre-existing issues were found during the file-by-file audit and are explicitly *not* fixed here (documented in the spec, not silently changed): `batch/tools/spot_check_losses.py` reads a `PROFILE` env var that no `.env.*` file ever set (distinct from `TAGGING_PROFILE` used everywhere else); `batch/load_images_from_internet.py`'s `SERP_API_KEY` was newly identified as a real secret that must stay in `.env`, not tracked YAML.
- **Follow-up (2026-07-06):** the flat tracked-YAML namespace from this ADR was later restructured into domain-grouped sections (`ocr`, `rules`, `bow`, `lemma_clustering`, `concepts`, `ollama`, `general`) — see `docs/superpowers/specs/2026-07-06-config-settings-hierarchical-structure.md`. All resolved values are unchanged; only the key paths and file nesting changed (e.g. `RULES_FILE` → `rules.file`).
