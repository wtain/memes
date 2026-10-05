"""Bounded thread-safe LRU for parse results (P3 spec, decision 2). Pure."""
import copy
import threading
from collections import OrderedDict


class ParseCache:
    def __init__(self, max_entries: int):
        self._max = max(int(max_entries), 1)
        self._items: OrderedDict = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key):
        with self._lock:
            if key not in self._items:
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return copy.deepcopy(self._items[key])

    def put(self, key, result) -> None:
        with self._lock:
            self._items[key] = copy.deepcopy(result)
            self._items.move_to_end(key)
            while len(self._items) > self._max:
                self._items.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
