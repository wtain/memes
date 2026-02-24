from datetime import timedelta

ONE_YEAR = int(timedelta(days=365).total_seconds())


def image_cache_headers() -> dict[str, str]:
    return {
        "Cache-Control": f"public, max-age={ONE_YEAR}, immutable",
    }


def short_cache_headers(seconds: int = 30) -> dict[str, str]:
    return {
        "Cache-Control": f"public, max-age={seconds}",
    }
