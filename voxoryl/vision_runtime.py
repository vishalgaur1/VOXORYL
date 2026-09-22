"""Vision runtime — Watch 2 only when structured state insufficient."""

from __future__ import annotations

from typing import Any

from voxoryl.perception import get_perception


async def maybe_describe(question: str = "") -> dict[str, Any]:
    """Invoke VL only when perception recommends it."""
    perc = get_perception()
    if not perc.should_invoke_vl():
        world = perc.snapshot()["world"]
        return {
            "ok": True,
            "used_vl": False,
            "source": "world_state",
            "world": world,
            "speak_hint": _world_speak(world),
        }
    from voxoryl.screen import tool_screen

    out = await tool_screen(action="look", goal=question or "Describe the screen briefly.", question=question)
    return {"ok": True, "used_vl": True, "result": out}


def _world_speak(world: dict[str, Any]) -> str:
    app = (world.get("active_app") or {}).get("value")
    win = (world.get("active_window") or {}).get("value")
    browser = (world.get("browser") or {}).get("value") or {}
    parts = []
    if app or win:
        parts.append(f"You're in {app or 'an app'}" + (f" — {win}" if win else ""))
    if browser.get("url"):
        parts.append(f"Browser: {browser.get('title') or browser.get('url')}")
    return ". ".join(parts) if parts else "I have structured screen state but nothing notable."
