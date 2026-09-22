"""Coordinate executor — click/type at absolute screen (x, y) with DPI awareness.

All points are absolute physical screen coordinates. Call ensure_dpi_aware()
before mapping or clicking so Win32 / Pillow / pyautogui share one space.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

VerifyFn = Callable[[], Awaitable[dict[str, Any]] | dict[str, Any]]

_DPI_READY = False


def ensure_dpi_aware() -> bool:
    """Make this process per-monitor DPI aware (physical pixels). Idempotent."""
    global _DPI_READY
    if _DPI_READY:
        return True
    import sys

    if not sys.platform.startswith("win"):
        _DPI_READY = True
        return True
    try:
        import ctypes

        # PROCESS_PER_MONITOR_DPI_AWARE_V2 = -4
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            _DPI_READY = True
            return True
        except Exception:
            pass
        try:
            # PROCESS_PER_MONITOR_DPI_AWARE = 2
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            _DPI_READY = True
            return True
        except Exception:
            pass
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            _DPI_READY = True
            return True
        except Exception:
            pass
    except Exception:
        return False
    return _DPI_READY


def screen_size() -> tuple[int, int]:
    """Primary monitor size in the same pixel space pyautogui uses."""
    import sys

    ensure_dpi_aware()
    if sys.platform.startswith("win"):
        try:
            import ctypes

            user32 = ctypes.windll.user32
            return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
        except Exception:
            pass
    try:
        import pyautogui

        sz = pyautogui.size()
        return int(sz[0]), int(sz[1])
    except Exception:
        return 1920, 1080


def clamp_xy(x: float, y: float, *, margin: int = 2) -> tuple[int, int] | None:
    """Clamp to screen bounds. Returns None if input is nonsense (far outside)."""
    ensure_dpi_aware()
    sw, sh = screen_size()
    try:
        xi, yi = float(x), float(y)
    except (TypeError, ValueError):
        return None
    if xi != xi or yi != yi:  # NaN
        return None
    # Reject clearly invented / out-of-world points (allow slight overshoot then clamp)
    if xi < -50 or yi < -50 or xi > sw + 50 or yi > sh + 50:
        return None
    if xi <= 0 or yi <= 0:
        return None
    xi = max(margin, min(sw - margin - 1, int(round(xi))))
    yi = max(margin, min(sh - margin - 1, int(round(yi))))
    return xi, yi


def move_xy(x: float, y: float) -> dict[str, Any]:
    """Move cursor to absolute screen coordinates."""
    ensure_dpi_aware()
    from voxoryl.screen import computer_use_enabled, mark_control_active

    if not computer_use_enabled():
        return {"ok": False, "error": "Computer use is disabled", "verified": False}
    pt = clamp_xy(x, y)
    if pt is None:
        return {"ok": False, "error": "coords_out_of_bounds", "x": x, "y": y, "verified": False}
    try:
        import pyautogui
    except ImportError:
        return {"ok": False, "error": "pyautogui not installed", "verified": False}
    sx, sy = pt
    pyautogui.FAILSAFE = True
    pyautogui.moveTo(sx, sy, duration=0.12)
    mark_control_active()
    return {"ok": True, "action": "move", "x": sx, "y": sy, "verified": False}


def click_xy(
    x: float,
    y: float,
    *,
    button: str = "left",
    clicks: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Click at absolute screen coordinates. Does not claim postcondition success."""
    ensure_dpi_aware()
    from voxoryl.config import settings
    from voxoryl.screen import computer_use_enabled, mark_control_active

    if not computer_use_enabled() and not dry_run:
        return {"ok": False, "error": "Computer use is disabled", "verified": False, "clicked": False}
    pt = clamp_xy(x, y)
    if pt is None:
        return {
            "ok": False,
            "error": "coords_out_of_bounds",
            "x": x,
            "y": y,
            "verified": False,
            "clicked": False,
        }
    sx, sy = pt
    if dry_run:
        return {"ok": True, "action": "click", "x": sx, "y": sy, "button": button, "dry_run": True, "clicked": False, "verified": False}
    try:
        import pyautogui
    except ImportError:
        return {"ok": False, "error": "pyautogui not installed", "verified": False, "clicked": False}
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = float(settings.computer_use_pause)
    btn = (button or "left").lower()
    if btn == "right":
        pyautogui.rightClick(sx, sy)
    elif btn == "middle":
        pyautogui.middleClick(sx, sy)
    elif int(clicks) >= 2:
        pyautogui.doubleClick(sx, sy)
    else:
        pyautogui.click(sx, sy)
    mark_control_active()
    return {
        "ok": True,
        "action": "click",
        "x": sx,
        "y": sy,
        "button": btn,
        "clicked": True,
        "verified": False,  # caller must verify
    }


def type_at_xy(x: float, y: float, text: str, *, enter: bool = False, dry_run: bool = False) -> dict[str, Any]:
    """Click to focus, then type text via existing keyboard helpers."""
    ensure_dpi_aware()
    from voxoryl.screen import computer_use_enabled, mark_control_active, type_now

    if not computer_use_enabled() and not dry_run:
        return {"ok": False, "error": "Computer use is disabled", "verified": False}
    clicked = click_xy(x, y, dry_run=dry_run)
    if not clicked.get("ok"):
        return {**clicked, "typed": False}
    if dry_run:
        return {
            "ok": True,
            "action": "type_at",
            "x": clicked.get("x"),
            "y": clicked.get("y"),
            "text_len": len(text or ""),
            "dry_run": True,
            "clicked": False,
            "typed": False,
            "verified": False,
        }
    time.sleep(0.08)
    typed = type_now(text or "", press_enter=enter, clear_first=False, goal="type_at_xy")
    mark_control_active()
    return {
        "ok": bool(typed.get("ok", True)),
        "action": "type_at",
        "x": clicked.get("x"),
        "y": clicked.get("y"),
        "clicked": True,
        "typed": True,
        "type_result": typed,
        "verified": False,
    }


async def click_xy_and_verify(
    x: float,
    y: float,
    *,
    button: str = "left",
    verify: VerifyFn | None = None,
    settle_ms: int = 400,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Click then run optional verify callback.
    Never reports verified=True without attempting verify when a callback is provided.
    If no verify callback can run, returns verified=False.
    """
    clicked = await asyncio.to_thread(click_xy, x, y, button=button, dry_run=dry_run)
    if not clicked.get("ok"):
        return {**clicked, "verified": False}
    if dry_run:
        return {**clicked, "verified": False, "verify": {"skipped": True, "reason": "dry_run"}}
    if verify is None:
        return {**clicked, "verified": False, "verify": {"skipped": True, "reason": "no_verifier"}}
    if settle_ms > 0:
        await asyncio.sleep(settle_ms / 1000.0)
    try:
        result = verify()
        if hasattr(result, "__await__"):
            result = await result  # type: ignore[misc]
    except Exception as exc:
        return {**clicked, "verified": False, "verify": {"ok": False, "error": str(exc)}}
    verified = bool((result or {}).get("ok"))
    return {**clicked, "verified": verified, "ok": bool(clicked.get("ok")) and verified, "verify": result}
