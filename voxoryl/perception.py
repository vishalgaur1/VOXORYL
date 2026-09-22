"""Perception + WorldState — Watch 0/1/2, fast/slow clocks, attention.

Rules: ALWAYS CAPTURE ≠ ALWAYS VL. Never use vision when structured state is available.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from voxoryl.event_bus import get_bus
from voxoryl.state_store import get_state


@dataclass
class FrameMeta:
    ts: float
    title: str = ""
    app: str = ""
    digest: str = ""  # cheap hash / size proxy


@dataclass
class PerceptionRuntime:
    """Watch0 capture, Watch1 structured, Watch2 VL rare."""

    ring: deque[FrameMeta] = field(default_factory=lambda: deque(maxlen=30))  # ~2–5s @ 10Hz
    fast_hz: float = 15.0  # 10–30 Hz
    slow_hz: float = 1.0  # 0.5–2 Hz
    watch_level: int = 0  # 0 capture, 1 structured, 2 vision
    attention_threshold: float = 0.55
    _last_fast: float = 0.0
    _last_slow: float = 0.0

    def set_watch(self, level: int) -> None:
        self.watch_level = max(0, min(2, int(level)))
        get_state().set_world(
            "screen",
            {
                **((get_state().snapshot()["world"].get("screen") or {}).get("value") or {}),
                "watch_level": self.watch_level,
            },
            confidence=0.9,
            source="perception",
        )

    def note_window(self, title: str, app: str = "") -> None:
        meta = FrameMeta(ts=time.time(), title=title, app=app, digest=f"{app}|{title}")
        prev = self.ring[-1].digest if self.ring else ""
        self.ring.append(meta)
        st = get_state()
        st.set_world("active_window", title, confidence=0.95, source="win32")
        if app:
            st.set_world("active_app", app, confidence=0.95, source="win32")
        # Watch 0: capture only. Promote to Watch 1 when we have structured window meta.
        if self.watch_level < 1 and (title or app):
            self.set_watch(1)
        if meta.digest != prev:
            score = 0.6 if app or title else 0.01
            st.set_attention(score)
            st.set_world(
                "screen",
                {"watch_level": self.watch_level, "last_meaningful_change": time.time()},
                confidence=0.9,
                source="perception",
            )
            # Cache structured world for reuse (TTL short)
            try:
                from voxoryl.cache_layer import get_cache

                get_cache().set(
                    "world.active_window",
                    {"title": title, "app": app},
                    ttl_s=2.0,
                    source="perception",
                    invalidate_on=["window.changed"],
                )
            except Exception:
                pass

    def note_browser(self, *, url: str = "", tab: str = "", title: str = "") -> None:
        get_state().set_world(
            "browser",
            {"url": url, "tab": tab, "title": title},
            confidence=0.98 if url else 0.5,
            source="cdp_or_playwright",
        )
        if url:
            get_state().set_attention(0.5)
            if self.watch_level < 1:
                self.set_watch(1)
        try:
            from voxoryl.cache_layer import get_cache

            get_cache().set(
                "world.browser",
                {"url": url, "tab": tab, "title": title},
                ttl_s=5.0,
                source="browser",
                invalidate_on=["browser.navigated", "tab.changed"],
            )
        except Exception:
            pass

    def note_ui_tree(self, tree: dict[str, Any] | list[Any], *, source: str = "uia") -> None:
        get_state().set_world("ui", tree, confidence=0.9, source=source)
        if self.watch_level < 1:
            self.set_watch(1)
        try:
            from voxoryl.cache_layer import get_cache

            get_cache().set(
                "a11y.tree",
                tree,
                ttl_s=3.0,
                source=source,
                invalidate_on=["ui.changed", "window.changed"],
            )
        except Exception:
            pass

    def attention_score(self, kind: str) -> float:
        return {
            "mouse": 0.01,
            "notification": 0.20,
            "window": 0.60,
            "form_submit": 0.90,
            "security": 1.00,
            "unknown_dialog": 0.82,
        }.get(kind, 0.1)

    def should_invoke_vl(self) -> bool:
        """Rule 3: never VL when structured state is available and confidence is high."""
        st = get_state().snapshot()
        attention = float(st["world"].get("attention") or 0)
        browser = (st["world"].get("browser") or {}).get("confidence", 0)
        window = (st["world"].get("active_window") or {}).get("confidence", 0)
        ui = (st["world"].get("ui") or {}).get("confidence", 0)
        if window >= 0.9 and (browser >= 0.9 or ui >= 0.85) and attention < 0.8:
            return False
        if window >= 0.95 and attention < self.attention_threshold:
            return False
        return attention >= self.attention_threshold

    async def tick_fast(self) -> None:
        """Cheap loop — no VL / no blocking I/O beyond Win32 title."""
        now = time.time()
        if now - self._last_fast < 1.0 / self.fast_hz:
            return
        self._last_fast = now
        if self.watch_level == 0:
            self.set_watch(0)
        title, app = _foreground_window()
        if title or app:
            self.note_window(title, app)
            await get_bus().emit("perception", "window.changed", {"title": title, "app": app})

    async def tick_slow(self) -> dict[str, Any]:
        now = time.time()
        if now - self._last_slow < 1.0 / self.slow_hz:
            return {"skipped": True}
        self._last_slow = now
        if self.should_invoke_vl():
            self.set_watch(2)
            return {"watch_level": 2, "vl_recommended": True}
        self.set_watch(1 if self.ring else 0)
        return {"watch_level": self.watch_level, "vl_recommended": False}

    def snapshot(self) -> dict[str, Any]:
        return {
            "watch_level": self.watch_level,
            "fast_hz": self.fast_hz,
            "slow_hz": self.slow_hz,
            "ring_len": len(self.ring),
            "world": get_state().snapshot()["world"],
            "should_vl": self.should_invoke_vl(),
        }


def _foreground_window() -> tuple[str, str]:
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        app = ""
        try:
            import psutil

            app = psutil.Process(int(pid.value)).name()
        except Exception:
            pass
        return buf.value or "", app
    except Exception:
        return "", ""


_PERC: PerceptionRuntime | None = None


def get_perception() -> PerceptionRuntime:
    global _PERC
    if _PERC is None:
        _PERC = PerceptionRuntime()
    return _PERC
