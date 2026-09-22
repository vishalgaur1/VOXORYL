"""Cancellation scopes for barge-in and global stop."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Scope(str, Enum):
    GLOBAL = "global"
    TASK = "task"
    TOOL = "tool"
    TTS = "tts"
    ASR = "asr"
    VISION = "vision"


@dataclass
class CancellationManager:
    _flags: dict[str, bool] = field(default_factory=dict)
    _tokens: dict[str, asyncio.Event] = field(default_factory=dict)
    _meta: dict[str, Any] = field(default_factory=dict)

    def _event(self, scope: Scope | str) -> asyncio.Event:
        key = Scope(scope).value if not isinstance(scope, Scope) else scope.value
        if key not in self._tokens:
            self._tokens[key] = asyncio.Event()
        return self._tokens[key]

    def cancel(self, scope: Scope | str = Scope.GLOBAL, *, reason: str = "") -> dict[str, Any]:
        key = Scope(scope).value if not isinstance(scope, Scope) else scope.value
        self._flags[key] = True
        self._event(key).set()
        if key == Scope.GLOBAL.value:
            for s in Scope:
                self._flags[s.value] = True
                self._event(s).set()
        self._meta = {"reason": reason, "scope": key, "ts": time.time()}
        try:
            from voxoryl.event_bus import get_bus

            asyncio.get_event_loop().create_task(
                get_bus().emit("realtime", "interrupt", {"scope": key, "reason": reason})
            )
        except Exception:
            pass
        return {"ok": True, "cancelled": key, "reason": reason}

    def reset(self, scope: Scope | str | None = None) -> None:
        if scope is None:
            self._flags.clear()
            for ev in self._tokens.values():
                ev.clear()
            return
        key = Scope(scope).value if not isinstance(scope, Scope) else scope.value
        self._flags[key] = False
        self._event(key).clear()

    def is_cancelled(self, scope: Scope | str = Scope.GLOBAL) -> bool:
        key = Scope(scope).value if not isinstance(scope, Scope) else scope.value
        return bool(self._flags.get(Scope.GLOBAL.value) or self._flags.get(key))

    def check(self, scope: Scope | str = Scope.GLOBAL) -> None:
        if self.is_cancelled(scope):
            raise asyncio.CancelledError(f"cancelled:{scope}")

    async def wait(self, scope: Scope | str = Scope.GLOBAL) -> None:
        await self._event(scope).wait()

    def status(self) -> dict[str, Any]:
        return {"flags": dict(self._flags), "meta": dict(self._meta)}


_CM: CancellationManager | None = None


def get_cancellation() -> CancellationManager:
    global _CM
    if _CM is None:
        _CM = CancellationManager()
    return _CM
