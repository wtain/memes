STATUS: DRAFT

# Config management migration: Dynaconf-backed tracked config, secrets-only .env

## Problem

Configuration today lives entirely in `.env.<environment>` files under `environments/`, which are gitignored (treated as secrets). This causes two problems:

1. **Env files are cumbersome and inconsistent.** `.env.metal`, `.env.general`, `.env.it` have diverged — `.env.it` is missing over a dozen keys the other two define, silently relying on hardcoded fallback defaults buried in code.
2. **Config is scattered and duplicated.** ~19 files under `batch/`, plus `Backend/app/main.py`, `Backend/app/services/image_store.py`, and `Storage/config.py`, all call `os.getenv("KEY", <default>)` directly. Defaults are hardcoded per call site rather than centralized — e.g. `CLUSTER_SELECTION_METHOD` defaults to `"eom"` in `batch/build_lemma_clusters.py`, silently overridden to `"leaf"` via `.env.general`, with no tracked record of *why* or that the override exists for that environment specifically.

Additionally, batch scripts have no reliable environment-selection mechanism: they either call a no-op `load_dotenv()` (two scripts) or read `os.environ` as-is, depending on the operator having manually sourced the correct `.env.<environment>` file into their shell beforehand. This is silent-failure-prone — the wrong shell session produces wrong-environment behavior with no error.

## Goals

- Move all **non-secret, environment-specific configuration** into a single tracked YAML file with hierarchical (default + per-environment override) structure.
- Leave **only secrets and machine-specific values** (`DATABASE_URL`, `BASE_PATH`, LAN-facing origins) in gitignored `.env.<environment>` files.
- Provide **one shared config-loading layer** used by both `Backend/` and `batch/`, replacing scattered `os.getenv()` calls.
- Fix the batch-script environment-selection gap: make selecting an environment an explicit, fast-failing operation instead of implicit shell state.
- Full migration in this pass — no dual os.getenv()/settings coexistence left behind.

## Non-goals

- Frontend (Vite) and Android configuration are **out of scope**. Vite requires `VITE_`-prefixed `.env` files and cannot consume YAML; Android has no dependency on this layer. `Frontend/memes-frontend/.env.*` files (as read via `../../environments/`) are untouched, including `VITE_BACKEND_API_URL`.
- No change to the three ports/environments themselves (metal/8081, general/8082, IT/8083) or to how uvicorn/pnpm are invoked.
- No new secrets-vaulting mechanism (e.g. Vault, SOPS) — `.env.<environment>` files remain plain gitignored dotenv files, just smaller.

## Design

### Tool choice: Dynaconf

Dynaconf natively supports the exact shape of this problem: a tracked settings file with `[default]` + per-environment sections that merge automatically, plus automatic highest-precedence overlay from `os.environ` (needed for secrets and CI/Docker overrides) — with almost no custom merge code to write and maintain. The lazy `settings.KEY` access pattern is a close, largely mechanical replacement for today's `os.getenv("KEY")` call sites, which keeps the full-migration scope tractable.

Alternatives considered and rejected for this pass: a hand-rolled PyYAML deep-merge loader (full control, zero new dependency semantics, but the team would own merge-precedence edge cases and any future validation/casting) and `pydantic-settings` with a YAML source (reuses the already-adopted Pydantic, but has no native multi-environment layering — the metal/general/it selection logic would need to be hand-built on top, for a similar amount of plumbing as the hand-rolled option without Dynaconf's off-the-shelf environment merging).

### Decision record

All decisions in this spec (tool choice, secrets/config boundary, file layout, scope) are logged in `docs/adr/adr-2026-07-05-config-management.md`.

### New package: `config/`

```
config/
  __init__.py
  settings.py

environments/
  settings.yaml          # default: { ... }  — common to all environments
  settings.metal.yaml    # metal:   { ... }
  settings.general.yaml  # general: { ... }
  settings.it.yaml       # it:      { ... }
```

**Config is split into one tracked YAML file per environment, not one file with internal sections.** This was an open question during design: Dynaconf's `environments=True` mode merges `[default]` + `[<active_env>]` *sections*, and those sections don't have to live in the same physical file — Dynaconf merges sections across every file in `settings_files` before selecting the active one. Splitting the sections into separate files (`settings.<environment>.yaml`) rather than one file with `default:`/`metal:`/`general:`/`it:` keys costs nothing at the `config/settings.py` level (just a longer `settings_files` list) and matches the repo's existing convention of one file per environment per concern (`rules.general.json`, `text-concepts.metal.json`, `ignore-words.general.json`, …) instead of introducing a new single-file-with-sections convention. Each environment's config can be edited and reviewed independently.

`config/settings.py`:

```python
from pathlib import Path

from dotenv import load_dotenv
from dynaconf import Dynaconf, Validator

settings = Dynaconf(
    envvar_prefix=False,
    settings_files=[
        "environments/settings.yaml",
        "environments/settings.metal.yaml",
        "environments/settings.general.yaml",
        "environments/settings.it.yaml",
    ],
    environments=True,
    env_switcher="APP_ENV",
    default_env="general",
)
settings.validators.register(Validator("DATABASE_URL", must_exist=True))
settings.validators.validate()


def load_env(name: str | None = None, base_dir: Path = Path("environments")) -> None:
    """Load <base_dir>/.env.<name> into the process, then reload settings.

    name defaults to the already-set APP_ENV. Raises if neither is available —
    silently falling back to whatever happens to be in os.environ is the
    exact failure mode this replaces. base_dir is overridable so integration
    tests can point at a fixture directory instead of the real, gitignored
    environments/.env.* files.
    """
    import os

    name = name or os.environ.get("APP_ENV")
    if not name:
        raise RuntimeError(
            "No environment selected — pass --env {metal,general,it} or set APP_ENV"
        )
    load_dotenv(base_dir / f".env.{name}", override=True)
    settings.reload()
    settings.validators.validate()
```

This is the one place Dynaconf configuration and the batch env-selection helper live. Placed at the top level (alongside `Backend/`, `batch/`, `Storage/`, `repository/`, `rules/`, `shared/`) rather than inside `Storage/`, since `Storage/` is scoped specifically to the DB access layer per the existing architecture doc, not app-wide config.

### `environments/settings*.yaml` (new, tracked)

Stay in `environments/` alongside the `.env.*` files, preserving the existing "env-specific config lives in `environments/`" invariant — `config/settings.py` just points at all four.

This is the full inventory, audited against every `os.getenv`/`os.environ` call site in `batch/` and `Backend/` (not just the ones sampled during design):

`environments/settings.yaml`:
```yaml
default:
  RULES_LEMMATIZE: false
  OCR_CONFIDENCE_MIN: 0.4
  OCR_LANG_SCORE_MIN: 0.3
  TEXT_SCOPE: unmatched
  LANGUAGE: all
  MIN_CLUSTER_SIZE: 2
  CLUSTER_SELECTION_EPSILON: 0.0
  CLUSTER_SELECTION_METHOD: eom
  TEXT_EMBED_MODEL: sbert
  OLLAMA_MODEL: qwen2
  OLLAMA_ENABLED: true
  LOOKUP_CONCEPTS: false
  BATCH_SIZE: 100
  PROGRESS_EVERY: 10
  BOW_MIN_WORD_LENGTH: 3
  BOW_MIN_FREQUENCY: 2
  TEXT_SOURCE: ocr
  CONCEPT_THRESHOLD: 0.2
  CONCEPT_LIMIT: 50
  PROFILE: general
  FRONTEND_ORIGIN: http://localhost:5173
```

`environments/settings.metal.yaml`:
```yaml
metal:
  TAGGING_PROFILE: metal
  RULES_FILE: data/rules.json
  TEXT_CONCEPTS_FILE: data/text-concepts.metal.json
  TEXT_CONCEPTS_TEMPLATES_FILE: data/text-concepts.templates.metal.json
  CONCEPT_IMAGES_DIR: images
```

`environments/settings.general.yaml`:
```yaml
general:
  TAGGING_PROFILE: general
  RULES_FILE: data/rules.general.json
  TEXT_CONCEPTS_FILE: data/text-concepts.general.json
  TEXT_CONCEPTS_TEMPLATES_FILE: data/text-concepts.templates.general.json
  CONCEPT_IMAGES_DIR: images-general
  CONCEPT_MAPPING_FILE: data/concepts-to-tags.general.json
  BOW_OUTPUT_FILE: output/bow.general.json
  BOW_UNMATCHED_FILE: output/bow.unmatched.general.json
  BOW_IGNORE_FILE: data/ignore-words.general.json
  RULES_LEMMATIZE: true
  CLUSTER_SELECTION_METHOD: leaf
  FRONTEND_ORIGIN: http://localhost:5174
```

`environments/settings.it.yaml`:
```yaml
it:
  TAGGING_PROFILE: it
  FRONTEND_ORIGIN: http://localhost:5175
```

`default_env` is `"general"` — `general` is the primary/reference environment; `metal` and `it` are treated as override environments layered on top of `default`, consistent with `general` already being the most complete of the three `.env.*` files today. If `APP_ENV` is unset (e.g. a stray script invocation or an unconfigured test run), Dynaconf falls back to `default` + `general`, not an undefined/empty environment.

Note `it` now inherits every tuning default (`CLUSTER_SELECTION_METHOD`, `MIN_CLUSTER_SIZE`, etc.) instead of silently relying on hardcoded per-call-site fallbacks scattered across `batch/` — this directly fixes the `.env.it` divergence from goal 1.

Deliberately **not** given a tracked default (preserving today's exact behavior of `os.getenv("KEY")` with no default, i.e. `None` unless an environment sets it): `RULES_FILE` in `default`, `TEXT_CONCEPTS_FILE`, `TEXT_CONCEPTS_TEMPLATES_FILE`, `CONCEPT_IMAGES_DIR`, `CONCEPT_MAPPING_FILE`, `BOW_OUTPUT_FILE`, `BOW_UNMATCHED_FILE`, `BOW_IGNORE_FILE`, `CLUSTER_OUTPUT_FILE`, `MIN_SAMPLES`, `TAGGING_DATA_DIR` (falls back to a script-relative path computed in code, not a literal). `it` still resolves these to `None`/the code fallback exactly as it does today, since its file doesn't set them — this migration doesn't change IT's behavior for the tagging/BOW/concept pipeline, only removes the divergence in *tuning* parameters that do have defaults.

**Newly discovered real secret:** `batch/load_images_from_internet.py` reads `SERP_API_KEY` — an API key, not currently set in any `.env.*` file (the script is presumably unused/dormant), but classified as a secret and documented as such in `.env.*` alongside `DATABASE_URL`. It must not move into tracked YAML.

**Pre-existing inconsistency, not fixed by this migration:** `batch/tools/spot_check_losses.py` reads `os.environ.get("PROFILE", "general")` — a different variable name from the `TAGGING_PROFILE` used everywhere else for what appears to be the same concept. No `.env.*` file ever sets `PROFILE`, so this script silently always runs as if profile were `"general"` regardless of which environment's shell you're in. This migration preserves that exact behavior (`settings.get("PROFILE", "general")`, with `PROFILE: general` in the tracked `default:` section) rather than silently reinterpreting it as `TAGGING_PROFILE` — fixing it is out of scope here since it would change `spot_check_losses.py`'s behavior for existing users, not just relocate config.

### `.env.<environment>` (unchanged location, slimmed content)

Only secrets and machine-specific values remain. Example, `.env.metal`:

```
APP_ENV=metal
DATABASE_URL=postgresql+asyncpg://ocr:ocr@127.0.0.1:5432/ocrdb
BASE_PATH=c:\Users\ramiz\OneDrive\Pictures\Samsung Gallery\DCIM\MetalMemes
ALTERNATIVE_FRONTEND_ORIGIN=http://192.168.1.41:5173
```

Classification rule: **`.env` = "not safe to commit"** (credentials, or values tied to one physical machine/network), **`settings.yaml` = "safe to commit"** (everything else — file paths relative to the repo, tuning parameters, feature flags, deterministic localhost origins). `VITE_BACKEND_API_URL` stays in `.env.<environment>` untouched since it's out of scope (frontend) and already machine/LAN-specific.

`APP_ENV` is a new key, set once per `.env.<environment>` file. It is the sole discriminator Dynaconf uses to pick which YAML section merges on top of `default`.

### Environment selection at runtime

- **Backend (uvicorn)**: unchanged — `--env-file environments/.env.<environment>` already loads the file (now including `APP_ENV`) into `os.environ` before app code runs. `config/settings.py`'s Dynaconf instance picks it up automatically via `os.environ` overlay + `env_switcher`.
- **Migrations (alembic)**: unchanged — the documented PowerShell snippet that sources `.env.<environment>` into the shell session still works; `Storage/config.py` (which alembic imports transitively) will read through `config.settings`.
- **Batch scripts**: new — each script's `if __name__ == "__main__":` block gains a `--env {metal,general,it}` argparse choice (falls back to `APP_ENV` if already set in the shell) and calls `config.settings.load_env(args.env)` before constructing anything that reads settings. Replaces the implicit "hope the shell already has the right vars" behavior with an explicit, fast-failing call.

### Migration of call sites (this pass, full — no follow-up batch)

| File(s) | Change |
|---|---|
| `Storage/config.py` | `DATABASE_URL = os.getenv(...)` → `from config.settings import settings; DATABASE_URL = settings.DATABASE_URL`. Keep the existing `if not DATABASE_URL: raise RuntimeError(...)` guard as a defense-in-depth check (Dynaconf's validator already fails fast at import time; this preserves today's exact error type for any code/tests depending on it). |
| `Backend/app/main.py` | `os.getenv('FRONTEND_ORIGIN')` / `os.getenv('ALTERNATIVE_FRONTEND_ORIGIN')` → `settings.FRONTEND_ORIGIN` / `settings.ALTERNATIVE_FRONTEND_ORIGIN`. Remove the module-level `load_dotenv()` call — redundant once `--env-file` is the sole loading path for the backend process. |
| `Backend/app/services/image_store.py` | `os.getenv('BASE_PATH')`, `os.getenv('INCOMING_PATH')`, `os.getenv('BUG_REPORTS_PATH')` → `settings.BASE_PATH`, `settings.INCOMING_PATH`, `settings.BUG_REPORTS_PATH`. Keep the existing `if not _images_dir: raise RuntimeError(...)` guard (same reasoning as `Storage/config.py`). Remove this file's own module-level `load_dotenv()` call — redundant for the same reason as `main.py`. |
| All 19 `batch/*.py` files using `os.getenv`/`os.environ` (includes `batch/tools/` and `batch/experimental/`) | `os.getenv("KEY", <hardcoded default>)` → `settings.KEY`, with the hardcoded default moved into `settings.yaml`'s `default:` section (full inventory above). Where a script has argparse CLI overrides (e.g. `build_lemma_clusters.py`'s `args.X if args.X is not None else os.getenv(...)`), the fallback becomes `args.X if args.X is not None else settings.X`; add the new `--env` flag per the "Environment selection" section above. `batch/load_images_from_internet.py`'s `SERP_API_KEY` is read via `os.getenv` directly (not `settings`) since it's a secret, not tracked config. |
| `batch/build_image_embeddings.py`, `batch/load_images_from_internet.py` | Remove the existing no-op `load_dotenv()` calls (superseded by `config.settings.load_env`). |
| `requirements.txt`, `requirements-backend.txt` | Add `dynaconf` (pure-Python, no C extensions — safe for the slim `Dockerfile.backend` image). |
| `Backend/tests/conftest.py` | Keep the existing `os.environ.setdefault('DATABASE_URL', ...)` / `setdefault('BASE_PATH', ...)` block — Dynaconf reads `os.environ` as its highest-precedence layer regardless of which settings file loaded, so this keeps working unchanged. Add `os.environ.setdefault('APP_ENV', 'general')` for explicitness in test runs. |
| `CLAUDE.md` | Add `config/` to the layer-structure diagram; add a short "Configuration" subsection documenting the tracked/secret split, `APP_ENV`, and the new `--env` batch flag. Update the alembic migration snippet's env file reference if key names shift (no change expected — it just sources shell vars). |

### Testing

This migration adds two new automated integration test suites, on top of the existing manual checklist in CLAUDE.md ("Before committing backend changes").

**`Backend/tests/test_config_integration.py`** — exercises `config/settings.py` through `Backend`'s own test settings, using `load_env(name, base_dir=<tmp_path fixture>)` so the test never touches real, gitignored `environments/.env.*` secrets:
- For each of `metal`, `general`, `it`: write a fixture `.env.<name>` to `tmp_path` (fake `DATABASE_URL`/`BASE_PATH`/`APP_ENV`), call `load_env(name, base_dir=tmp_path)`, and assert the resulting `settings.TAGGING_PROFILE`, `settings.RULES_FILE` (or its absence for `it`), and `settings.FRONTEND_ORIGIN` match the values committed in `environments/settings.<name>.yaml` — i.e. the tracked, real config, only the secrets layer is faked.
- Assert `settings.DATABASE_URL` resolves from the fixture `.env` file (proves the `os.environ` overlay wins over tracked YAML).
- Assert the validator raises if a fixture `.env` file omits `DATABASE_URL`.
- Assert `load_env(None, base_dir=tmp_path)` with no `APP_ENV` set anywhere raises `RuntimeError` (the fast-fail path).

**`batch/tests/test_env_loading.py`** (new `batch/tests/` directory — none exists yet) — a "dummy batch" integration test, not a real pipeline stage: a small fixture module (or inline in the test) that mimics a batch script's entrypoint shape (`--env` argparse flag → `config.settings.load_env(args.env)` → reads a handful of `settings.*` keys), run via `pytest` for each of the three environments against fixture `.env` files the same way as the backend test. Confirms the pattern every real batch script's `if __name__ == "__main__":` block will use actually resolves the right per-environment values end-to-end (tracked YAML section selection + secrets overlay + `--env` flag), independent of any single batch script's own business logic.
- Add `pytest` invocation for this to CLAUDE.md's Tests section: `pytest batch/tests/`.

**Manual verification (still required, not automatable without real secrets):** server starts without import errors under each of the three real `--env-file environments/.env.<environment>` invocations; `/api/diagnostics/health` and `/api/images?limit=1` respond correctly; at least one real migrated batch script invoked per environment (e.g. `python -m batch.build_tags_from_ocr --env general`) reads the expected values from the real `.env.general`.

## Risks / open questions

- Dynaconf's lazy settings proxy caches on first access; `load_env()`'s `settings.reload()` call must run *before* any other module-level code reads `settings.X` at import time in a batch script. This means batch scripts must call `load_env()` inside `if __name__ == "__main__":` (or at the very top of `main()`), never at module import time — matches the existing pattern where CLI-overridable settings are already resolved inside `main()`, not at import time.
- `Backend/app/main.py`'s CORS origin values (`FRONTEND_ORIGIN`, `ALTERNATIVE_FRONTEND_ORIGIN`) come from two different layers now (tracked YAML vs. `.env` secrets respectively) instead of one flat file — a reviewer skimming `main.py` needs to know this split exists. Documented in the CLAUDE.md addition above.
