"""Kleiner threadsicherer TTL-Cache für teure, kurzlebige API-Aggregate."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from copy import deepcopy
from typing import Generic, Hashable, Optional, TypeVar

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    def __init__(self, *, ttl_seconds: float, max_entries: int = 128):
        self.ttl_seconds = max(0.1, float(ttl_seconds))
        self.max_entries = max(1, int(max_entries))
        self._lock = threading.Lock()
        self._items: "OrderedDict[K, tuple[float, V]]" = OrderedDict()

    def get(self, key: K) -> Optional[V]:
        now = time.monotonic()
        with self._lock:
            item = self._items.get(key)
            if not item:
                return None
            expires_at, value = item
            if expires_at <= now:
                self._items.pop(key, None)
                return None
            self._items.move_to_end(key)
            return deepcopy(value)

    def set(self, key: K, value: V) -> None:
        with self._lock:
            self._items[key] = (time.monotonic() + self.ttl_seconds, deepcopy(value))
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
