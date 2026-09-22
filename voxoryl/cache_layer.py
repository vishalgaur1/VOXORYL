"""Cache layer with TTL, version, invalidate_on, negative caching.

Prioritize: WorldState, a11y tree, capability catalog, prompt prefix.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CacheEntry:
    key: str
    value: Any
    source: str = ""
    version: int = 1
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    scope: str = "session"
    invalidate_on: list[str] = field(default_factory=list)
    negative: bool = False


class Cache:
    def __init__(self) -> None:
        self._store: dict[str, CacheEntry] = {}
        self.hits = 0
        self.misses = 0
        self.negative_hits = 0

    def set(
        self,
        key: str,
        value: Any,
        *,
        ttl_s: float | None = 60.0,
        source: str = "",
        version: int = 1,
        scope: str = "session",
        invalidate_on: list[str] | None = None,
        negative: bool = False,
    ) -> None:
        exp = time.time() + ttl_s if ttl_s is not None else None
        self._store[key] = CacheEntry(
            key=key,
            value=value,
            source=source,
            version=version,
            expires_at=exp,
            scope=scope,
            invalidate_on=list(invalidate_on or []),
            negative=negative,
        )

    def set_negative(
        self,
        key: str,
        *,
        ttl_s: float = 15.0,
        source: str = "",
        reason: str = "miss",
        invalidate_on: list[str] | None = None,
    ) -> None:
        """Remember a miss so we do not recompute immediately."""
        self.set(
            key,
            {"negative": True, "reason": reason},
            ttl_s=ttl_s,
            source=source,
            negative=True,
            invalidate_on=invalidate_on,
        )

    def get(self, key: str) -> Any | None:
        e = self.get_entry(key)
        if not e:
            self.misses += 1
            return None
        if e.negative:
            self.negative_hits += 1
            return None
        self.hits += 1
        return e.value

    def get_entry(self, key: str) -> CacheEntry | None:
        e = self._store.get(key)
        if not e:
            return None
        if e.expires_at is not None and time.time() > e.expires_at:
            self._store.pop(key, None)
            return None
        return e

    def is_negative(self, key: str) -> bool:
        e = self.get_entry(key)
        return bool(e and e.negative)

    def invalidate(self, key: str) -> bool:
        return self._store.pop(key, None) is not None

    def invalidate_event(self, event: str) -> int:
        dead = [k for k, e in self._store.items() if event in e.invalidate_on]
        for k in dead:
            self._store.pop(k, None)
        return len(dead)

    def clear_scope(self, scope: str) -> None:
        dead = [k for k, e in self._store.items() if e.scope == scope]
        for k in dead:
            self._store.pop(k, None)

    def stats(self) -> dict[str, Any]:
        return {
            "entries": len(self._store),
            "hits": self.hits,
            "misses": self.misses,
            "negative_hits": self.negative_hits,
            "keys": list(self._store.keys())[:50],
        }


_CACHE: Cache | None = None


def get_cache() -> Cache:
    global _CACHE
    if _CACHE is None:
        _CACHE = Cache()
        _CACHE.set(
            "capability.catalog",
            "",
            ttl_s=30.0,
            source="registry",
            invalidate_on=["capabilities_changed"],
        )
    return _CACHE
