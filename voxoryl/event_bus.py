"""Async event bus with channels, coalesce, and debounce."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, DefaultDict


Handler = Callable[[dict[str, Any]], Awaitable[None] | None]

CHANNELS = ("realtime", "perception", "agent", "platform")


@dataclass
class _Pending:
    event_type: str
    payload: dict[str, Any]
    deadline: float


@dataclass
class EventBus:
    """Channelled pub/sub. Realtime handlers must stay non-blocking."""

    _subs: DefaultDict[str, list[tuple[str | None, Handler]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    _coalesce_ms: dict[str, int] = field(
        default_factory=lambda: {
            "window.changed": 50,
            "screen.changed": 150,
            "ui.element.changed": 100,
            "dom.changed": 100,
            "environment_changed": 100,
        }
    )
    _pending: dict[str, _Pending] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _history: list[dict[str, Any]] = field(default_factory=list)
    _history_max: int = 500

    def subscribe(self, channel: str, handler: Handler, *, event_type: str | None = None) -> None:
        if channel not in CHANNELS:
            raise ValueError(f"unknown channel: {channel}")
        self._subs[channel].append((event_type, handler))

    def unsubscribe(self, channel: str, handler: Handler) -> None:
        self._subs[channel] = [(t, h) for t, h in self._subs[channel] if h is not handler]

    async def emit(
        self,
        channel: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        coalesce: bool | None = None,
    ) -> None:
        if channel not in CHANNELS:
            raise ValueError(f"unknown channel: {channel}")
        body = {
            "channel": channel,
            "type": event_type,
            "ts": time.time(),
            "payload": payload or {},
        }
        # Realtime / interrupts: never coalesce delay
        if channel == "realtime" or event_type in {"speech.started", "interrupt", "kill", "l0r"}:
            await self._dispatch(body)
            return

        window = self._coalesce_ms.get(event_type, 0) if coalesce is not False else 0
        if window <= 0:
            await self._dispatch(body)
            return

        key = f"{channel}:{event_type}"
        async with self._lock:
            self._pending[key] = _Pending(event_type, body["payload"], time.time() + window / 1000.0)
        asyncio.create_task(self._flush_later(key, window / 1000.0))

    async def _flush_later(self, key: str, delay: float) -> None:
        await asyncio.sleep(delay)
        async with self._lock:
            pending = self._pending.pop(key, None)
        if not pending:
            return
        body = {
            "channel": key.split(":", 1)[0],
            "type": pending.event_type if pending.event_type != "environment_changed" else "environment_changed",
            "ts": time.time(),
            "payload": pending.payload,
        }
        # Merge rapid UI/screen into environment_changed for agent consumers
        if pending.event_type in {"window.changed", "screen.changed", "ui.element.changed", "dom.changed"}:
            body["type"] = "environment_changed"
            body["payload"] = {**pending.payload, "source_event": pending.event_type}
        await self._dispatch(body)

    async def _dispatch(self, body: dict[str, Any]) -> None:
        self._history.append(body)
        if len(self._history) > self._history_max:
            self._history = self._history[-self._history_max :]
        channel = body["channel"]
        et = body["type"]
        for want, handler in list(self._subs.get(channel, [])):
            if want is not None and want != et:
                continue
            try:
                result = handler(body)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass

    def recent(self, *, limit: int = 50, channel: str | None = None) -> list[dict[str, Any]]:
        items = self._history
        if channel:
            items = [e for e in items if e.get("channel") == channel]
        return items[-limit:]


_BUS: EventBus | None = None


def get_bus() -> EventBus:
    global _BUS
    if _BUS is None:
        _BUS = EventBus()
    return _BUS
