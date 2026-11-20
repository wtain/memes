"""Observability of the backend (docs/superpowers/specs/2026-11-19-observability/).

Everything here is behind the setting `observability.metrics.enabled` (default false, true in settings.prod.yaml). Importing this
package never imports prometheus_client; `metrics.py` does, and only when something asks for a metric object.
"""


def enabled() -> bool:
    """True when request metrics are switched on. Never raises: a broken settings lookup means off."""
    try:
        from config.settings import settings
        return bool(settings.get("observability.metrics.enabled", False))
    except Exception:
        return False
