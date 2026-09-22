"""Memory Manager — working / episodic / semantic / procedural + promotion/decay."""

from __future__ import annotations

import time
from typing import Any

from voxoryl.memory import _now, _save, memory


class MemoryManager:
    def working_set(self) -> dict[str, Any]:
        from voxoryl.state_store import get_state

        task = get_state().snapshot().get("task") or {}
        return {"task": task}

    def _persist(self, data: dict[str, Any]) -> None:
        data["updated_at"] = _now()
        _save(memory.path, data)

    def add_episode(self, event: str, *, importance: float = 0.5, meta: dict[str, Any] | None = None) -> None:
        data = memory.read()
        episodes = list(data.get("runtime_episodes") or [])
        episodes.append(
            {
                "event": event,
                "importance": importance,
                "confidence": 0.8,
                "created_at": time.time(),
                "last_accessed": time.time(),
                "expires_at": None,
                "superseded_by": None,
                "meta": meta or {},
            }
        )
        data["runtime_episodes"] = episodes[-200:]
        self._persist(data)
        self._maybe_promote(event, importance)

    def touch(self, event_substr: str) -> int:
        data = memory.read()
        episodes = list(data.get("runtime_episodes") or [])
        n = 0
        needle = event_substr.lower()
        now = time.time()
        for e in episodes:
            if needle in str(e.get("event") or "").lower():
                e["last_accessed"] = now
                n += 1
        if n:
            data["runtime_episodes"] = episodes
            self._persist(data)
        return n

    def _maybe_promote(self, event: str, importance: float) -> None:
        data = memory.read()
        episodes = data.get("runtime_episodes") or []
        similar = sum(1 for e in episodes if event[:40].lower() in str(e.get("event") or "").lower())
        promote_semantic = importance >= 0.7 or similar >= 3
        promote_procedural = similar >= 5 or (importance >= 0.85 and "how to" in event.lower())
        if promote_semantic:
            try:
                memory.remember_fact(event[:240], tags=["promoted", "runtime"])
            except Exception:
                pass
        if promote_procedural:
            procedural = list(data.get("procedural") or [])
            procedural.append(
                {
                    "hint": event[:240],
                    "importance": importance,
                    "confidence": 0.75,
                    "created_at": time.time(),
                    "last_accessed": time.time(),
                    "expires_at": None,
                    "superseded_by": None,
                }
            )
            data["procedural"] = procedural[-50:]
            self._persist(data)

    def decay(self) -> int:
        data = memory.read()
        episodes = list(data.get("runtime_episodes") or [])
        now = time.time()
        kept = []
        dropped = 0
        for e in episodes:
            if e.get("superseded_by"):
                dropped += 1
                continue
            exp = e.get("expires_at")
            if exp is not None and float(exp) < now:
                dropped += 1
                continue
            conf = float(e.get("confidence") or 0.5)
            importance = float(e.get("importance") or 0.5)
            last = float(e.get("last_accessed") or e.get("created_at") or now)
            age = now - last
            if age > 86400 * 7:
                conf *= 0.9
                e["confidence"] = conf
            if conf < 0.3 and age > 86400 * 14 and importance < 0.6:
                dropped += 1
                continue
            kept.append(e)
        data["runtime_episodes"] = kept
        procedural = []
        for p in list(data.get("procedural") or []):
            if p.get("superseded_by"):
                dropped += 1
                continue
            procedural.append(p)
        data["procedural"] = procedural
        self._persist(data)
        return dropped

    def procedural_hint(self, query: str) -> str:
        data = memory.read()
        procedural = list(data.get("procedural") or [])
        q = (query or "").lower()
        hits = [p for p in procedural if q and q[:20] in str(p.get("hint") or "").lower()]
        if hits:
            hits[0]["last_accessed"] = time.time()
            self._persist(data)
            return str(hits[0].get("hint") or "")
        try:
            from voxoryl.software_knowledge import prompt_block

            return prompt_block()[:800]
        except Exception:
            return ""


_MM: MemoryManager | None = None


def get_memory_manager() -> MemoryManager:
    global _MM
    if _MM is None:
        _MM = MemoryManager()
    return _MM
