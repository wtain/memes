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

### New package: `config/`

```
config/
  __init__.py
  settings.py
```

`config/settings.py`:

```python
from pathlib import Path

from dotenv import load_dotenv
from dynaconf import Dynaconf, Validator

settings = Dynaconf(
    envvar_prefix=False,
    settings_files=["environments/settings.yaml"],
    environments=True,
    env_switcher="APP_ENV",
    default_env="general",
)
settings.validators.register(Validator("DATABASE_URL", must_exist=True))
settings.validators.validate()


def load_env(name: str | None = None) -> None:
    """Load environments/.env.<name> into the process, then reload settings.

    name defaults to the already-set APP_ENV. Raises if neither is available —
    silently falling back to whatever happens to be in os.environ is the
    exact failure mode this replaces.
    """
    import os

    name = name or os.environ.get("APP_ENV")
    if not name:
        raise RuntimeError(
            "No environment selected — pass --env {metal,general,it} or set APP_ENV"
        )
    load_dotenv(Path("environments") / f".env.{name}", override=True)
    settings.reload()
    settings.validators.validate()
```

This is the one place Dynaconf configuration and the batch env-selection helper live. Placed at the top level (alongside `Backend/`, `batch/`, `Storage/`, `repository/`, `rules/`, `shared/`) rather than inside `Storage/`, since `Storage/` is scoped specifically to the DB access layer per the existing architecture doc, not app-wide config.

### `environments/settings.yaml` (new, tracked)

Stays in `environments/` alongside the `.env.*` files, preserving the existing "env-specific config lives in `environments/`" invariant — `config/settings.py` just points at it.

```yaml
default:
  RULES_LEMMATIZE: false
  CLUSTER_SELECTION_METHOD: eom
  MIN_CLUSTER_SIZE: 2
  CLUSTER_SELECTION_EPSILON: 0.0
  TEXT_EMBED_MODEL: sbert
  OLLAMA_MODEL: qwen2
  OLLAMA_ENABLED: true
  LOOKUP_CONCEPTS: false
  TEXT_SCOPE: unmatched
  FRONTEND_ORIGIN: http://localhost:5173

metal:
  TAGGING_PROFILE: metal
  RULES_FILE: data/rules.json
  TEXT_CONCEPTS_FILE: data/text-concepts.metal.json
  TEXT_CONCEPTS_TEMPLATES_FILE: data/text-concepts.templates.metal.json
  CONCEPT_IMAGES_DIR: images

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

it:
  TAGGING_PROFILE: it
  FRONTEND_ORIGIN: http://localhost:5175
```

`default_env` is `"general"` — `general` is the primary/reference environment; `metal` and `it` are treated as override environments layered on top of `default`, consistent with `general` already being the most complete of the three `.env.*` files today. If `APP_ENV` is unset (e.g. a stray script invocation or an unconfigured test run), Dynaconf falls back to `default` + `general`, not an undefined/empty environment.

Note `it` now inherits every tuning default (`CLUSTER_SELECTION_METHOD`, `MIN_CLUSTER_SIZE`, etc.) instead of silently relying on hardcoded per-call-site fallbacks scattered across `batch/` — this directly fixes the `.env.it` divergence from goal 1.

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
| `Backend/app/services/image_store.py` | `os.getenv('BASE_PATH')`, `os.getenv('INCOMING_PATH')`, `os.getenv('BUG_REPORTS_PATH')` → `settings.BASE_PATH`, `settings.INCOMING_PATH`, `settings.BUG_REPORTS_PATH`. |
| All 19 `batch/*.py` files using `os.getenv` | `os.getenv("KEY", <hardcoded default>)` → `settings.KEY`, with the hardcoded default moved into `settings.yaml`'s `default:` section. Where a script has argparse CLI overrides (e.g. `build_lemma_clusters.py`'s `args.X if args.X is not None else os.getenv(...)`), the fallback becomes `args.X if args.X is not None else settings.X`; add the new `--env` flag per the "Environment selection" section above. |
| `batch/build_image_embeddings.py`, `batch/load_images_from_internet.py` | Remove the existing no-op `load_dotenv()` calls (superseded by `config.settings.load_env`). |
| `requirements.txt`, `requirements-backend.txt` | Add `dynaconf` (pure-Python, no C extensions — safe for the slim `Dockerfile.backend` image). |
| `Backend/tests/conftest.py` | Keep the existing `os.environ.setdefault('DATABASE_URL', ...)` / `setdefault('BASE_PATH', ...)` block — Dynaconf reads `os.environ` as its highest-precedence layer regardless of which settings file loaded, so this keeps working unchanged. Add `os.environ.setdefault('APP_ENV', 'general')` for explicitness in test runs. |
| `CLAUDE.md` | Add `config/` to the layer-structure diagram; add a short "Configuration" subsection documenting the tracked/secret split, `APP_ENV`, and the new `--env` batch flag. Update the alembic migration snippet's env file reference if key names shift (no change expected — it just sources shell vars). |

### Testing

- No automated backend test gate exists beyond what's documented in CLAUDE.md ("Before committing backend changes"); this migration doesn't add one. Verify manually per that checklist: server starts without import errors under each of the three `--env-file` invocations, `/api/diagnostics/health` and `/api/images?limit=1` respond correctly for at least one environment.
- Run `cd Backend && pytest` — confirms `conftest.py`'s `os.environ.setdefault` + Dynaconf's validator interact correctly under the mocked-DB test suite.
- For batch: manually invoke at least one migrated script per environment (e.g. `python -m batch.build_tags_from_ocr --env general`) and confirm it reads the expected `RULES_FILE` / tuning values, and that omitting `--env` with no `APP_ENV` set raises the new fast-fail error instead of silently using stale/default values.

## Risks / open questions

- Dynaconf's lazy settings proxy caches on first access; `load_env()`'s `settings.reload()` call must run *before* any other module-level code reads `settings.X` at import time in a batch script. This means batch scripts must call `load_env()` inside `if __name__ == "__main__":` (or at the very top of `main()`), never at module import time — matches the existing pattern where CLI-overridable settings are already resolved inside `main()`, not at import time.
- `Backend/app/main.py`'s CORS origin values (`FRONTEND_ORIGIN`, `ALTERNATIVE_FRONTEND_ORIGIN`) come from two different layers now (tracked YAML vs. `.env` secrets respectively) instead of one flat file — a reviewer skimming `main.py` needs to know this split exists. Documented in the CLAUDE.md addition above.
