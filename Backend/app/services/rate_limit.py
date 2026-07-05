import asyncio
from collections import defaultdict
from time import monotonic


class SlidingWindowRateLimiter:
    """In-memory sliding-window rate limiter. Not suitable for multi-process deployments."""

    def __init__(self, max_calls: int, window_seconds: float):
        self._max = max_calls
        self._window = window_seconds
        self._lock = asyncio.Lock()
        self._buckets: dict[str, list[float]] = defaultdict(list)

    async def is_allowed(self, key: str) -> bool:
        now = monotonic()
        async with self._lock:
            cutoff = now - self._window
            self._buckets[key] = [t for t in self._buckets[key] if t > cutoff]
            if len(self._buckets[key]) >= self._max:
                return False
            self._buckets[key].append(now)
            return True


# 10 upload requests per IP per minute
upload_limiter = SlidingWindowRateLimiter(max_calls=10, window_seconds=60)

# 20 bug-report requests per IP per minute — higher than uploads since the client
# may retry the same log across multiple configured environments in quick succession
bug_report_limiter = SlidingWindowRateLimiter(max_calls=20, window_seconds=60)