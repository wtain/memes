from datetime import timedelta

ONE_YEAR = int(timedelta(days=365).total_seconds())
ONE_HOUR = int(timedelta(hours=1).total_seconds())


def image_cache_headers() -> dict[str, str]:
    return {
        "Cache-Control": f"public, max-age={ONE_YEAR}, immutable",
    }


def short_cache_headers(seconds: int = 30) -> dict[str, str]:
    return {
        "Cache-Control": f"public, max-age={seconds}",
    }


def no_cache_headers() -> dict[str, str]:
    """Disable caching for frequently changing data."""
    return {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }
