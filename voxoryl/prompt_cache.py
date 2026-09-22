"""Prompt prefix cache bookkeeping (STATIC packet hashes)."""

from __future__ import annotations

import hashlib
from typing import Any


class PromptCacheManager:
    def __init__(self) -> None:
        self._last_static_hash: str | None = None
        self.hits = 0
        self.misses = 0

    def hash_static(self, static: str) -> str:
        return hashlib.sha256(static.encode("utf-8", errors="ignore")).hexdigest()[:16]

    def note_packets(self, static: str, dynamic: str) -> dict[str, Any]:
        h = self.hash_static(static)
        reused = self._last_static_hash == h
        if reused:
            self.hits += 1
        else:
            self.misses += 1
            self._last_static_hash = h
        return {
            "static_hash": h,
            "prefix_reused": reused,
            "static_len": len(static),
            "dynamic_len": len(dynamic),
            "hits": self.hits,
            "misses": self.misses,
        }


_PCM: PromptCacheManager | None = None


def get_prompt_cache() -> PromptCacheManager:
    global _PCM
    if _PCM is None:
        _PCM = PromptCacheManager()
    return _PCM
