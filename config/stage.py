"""Deployment stage of the process: `dev` (the default, every developer environment) or `prod`.

`APP_STAGE` is read once, here, when the module is first imported. `is_prod()` is the only
reader the rest of the code base may use (design: docs/superpowers/specs/2026-11-12-environment-data-sync/
2026-11-12-p5-prod-provisioning-and-hardening-design.md, P5a). With `APP_STAGE` unset or `dev`
nothing in the application behaves differently from before the stage mechanism existed.

This module imports nothing from the project, so it can be used by `config.settings` itself.
"""
import os

_VALID_STAGES = ("dev", "prod")


def _read_stage(environ=os.environ) -> str:
    raw = environ.get("APP_STAGE")
    if raw is None or raw.strip() == "":
        return "dev"
    stage = raw.strip().lower()
    if stage not in _VALID_STAGES:
        # A typo such as "production" must not silently fall back to dev behaviour on a public host.
        raise RuntimeError(f"APP_STAGE must be one of {', '.join(_VALID_STAGES)} (got {raw!r})")
    return stage


STAGE = _read_stage()


def is_prod() -> bool:
    return STAGE == "prod"
