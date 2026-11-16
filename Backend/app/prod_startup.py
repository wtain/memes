"""Startup checks that only run with APP_STAGE=prod (P5a-2).

A wrong DATABASE_URL (another environment's database) must stop the container instead of serving the wrong data:
the database carries a marker, `ALTER DATABASE memes_<env> SET memes.environment = '<env>'`, set once at
provisioning, and it has to equal APP_ENV.
"""
import os

from sqlalchemy import text


async def verify_environment_marker(session_factory, app_env: str | None = None) -> None:
    """Raise RuntimeError unless `current_setting('memes.environment')` of the connected database equals APP_ENV."""
    app_env = app_env if app_env is not None else os.environ.get("APP_ENV")
    if not app_env:
        raise RuntimeError("APP_STAGE=prod requires APP_ENV to be set explicitly")
    async with session_factory() as session:
        marker = (await session.execute(text("SELECT current_setting('memes.environment', true)"))).scalar()
    if marker != app_env:
        raise RuntimeError(
            f"database environment marker {marker!r} does not match APP_ENV {app_env!r}; refusing to start"
        )
