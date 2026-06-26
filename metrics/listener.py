from collections import Counter, defaultdict


class SimpleMetricsListener:

    def __init__(self):
        self._counters: Counter = Counter()
        self._buckets: dict[str, list[int]] = defaultdict(list)

    def increment(self, metric_name: str) -> None:
        """Increment a named counter by 1."""
        self._counters[metric_name] += 1

    def add(self, metric_name: str, value: int) -> None:
        """Add an arbitrary integer value to a named counter (e.g. total tag count)."""
        self._counters[metric_name] += value

    def bucket(self, metric_name: str, value: int) -> None:
        """Record one observation into a named distribution (e.g. tags-per-image)."""
        self._buckets[metric_name].append(value)

    def print(self) -> None:
        for name, count in sorted(self._counters.items()):
            print(f"  {name} = {count}")
        for name, values in sorted(self._buckets.items()):
            if not values:
                continue
            total = sum(values)
            n = len(values)
            print(
                f"  {name}: n={n}, total={total}, "
                f"avg={total/n:.1f}, min={min(values)}, max={max(values)}"
            )