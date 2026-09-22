from __future__ import annotations

import asyncio
import base64
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from voxoryl.config import settings
from voxoryl.llm import chat_local, parse_json_loose
from voxoryl.memory import memory

_WATCH_PATH: Path | None = None
_LATEST_CONTEXT: dict[str, Any] = {
    "at": None,
    "description": "",
    "path": "",
    "width": 0,
    "height": 0,
}


def latest_screen_context() -> dict[str, Any]:
    """Last watched/looked screen description for chat + planning."""
    return dict(_LATEST_CONTEXT)


def screen_context_prompt(max_chars: int = 900) -> str:
    ctx = latest_screen_context()
    desc = str(ctx.get("description") or "").strip()
    bits: list[str] = []
    try:
        from voxoryl.software_knowledge import prompt_block

        bits.append(prompt_block("chrome", max_chars=400))
    except Exception:
        pass
    if desc:
        bits.append(
            "CURRENT SCREEN (always-on watch — treat as live UI context):\n"
            + desc[:max_chars]
            + "\n"
        )
    return "".join(bits)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(entry: dict[str, Any]) -> None:
    path = settings.voxoryl_data_dir / "computer_use_log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def computer_use_enabled() -> bool:
    return bool(settings.computer_use_enabled)


def _require_enabled() -> dict[str, Any] | None:
    if computer_use_enabled():
        return None
    return {
        "ok": False,
        "error": "Computer use is disabled",
        "hint": "Set COMPUTER_USE_ENABLED=true in .env, restart Voxoryl, then grant screen/input access.",
    }


def capture_screenshot() -> dict[str, Any]:
    """Grab the primary monitor screenshot to data/screenshots/."""
    blocked = _require_enabled()
    if blocked:
        return blocked
    try:
        from PIL import ImageGrab
    except ImportError:
        return {"ok": False, "error": "Pillow not installed — pip install Pillow"}

    shot_dir = settings.voxoryl_data_dir / "screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = shot_dir / f"screen-{stamp}.png"
    img = ImageGrab.grab()
    # Keep vision payloads small for 6GB VRAM VL models
    max_w = 1280
    if img.width > max_w:
        ratio = max_w / img.width
        img = img.resize((max_w, int(img.height * ratio)))
    img.save(path, format="PNG", optimize=True)
    return {
        "ok": True,
        "path": str(path.resolve()),
        "width": img.width,
        "height": img.height,
        "at": _now(),
    }


def _image_to_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


async def vision_describe(path: str, question: str = "", *, require_computer_use: bool = True) -> dict[str, Any]:
    """Ask the local vision model what is in an image (screen or camera)."""
    if require_computer_use:
        blocked = _require_enabled()
        if blocked:
            return blocked
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"Screenshot missing: {path}"}

    prompt = question.strip() or (
        "Describe this computer screen for an assistant that will click and type. "
        "List visible windows, buttons, text fields, and what the user might be doing. Be concrete."
    )
    b64 = _image_to_b64(p)
    payload = {
        "model": settings.ollama_vision_model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [b64],
            }
        ],
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {"temperature": 0.2, "num_ctx": 4096},
    }
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(f"{settings.ollama_base_url}/api/chat", json=payload)
            if r.status_code >= 400:
                return {"ok": False, "error": r.text, "model": settings.ollama_vision_model}
            content = (r.json().get("message") or {}).get("content", "").strip()
            return {
                "ok": True,
                "model": settings.ollama_vision_model,
                "description": content,
                "path": str(p.resolve()),
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "model": settings.ollama_vision_model}


PLAN_SYSTEM = """You control a Windows PC for the owner using mouse and keyboard.
Return ONLY JSON:
{
  "summary": "what you will do",
  "steps": [
    {"action": "click|double_click|right_click|move|type|paste|hotkey|scroll|wait|press|tab", "x": 0, "y": 0, "text": "", "keys": [], "amount": 0, "seconds": 0.5, "reason": ""}
  ],
  "done": false,
  "speak": "short status for the owner"
}
Rules:
- Coordinates are in the screenshot pixel space (same size as the image).
- Prefer keyboard shortcuts when known (Ctrl+T new tab, Ctrl+L address bar, Ctrl+Shift+A tab search) over mouse hunting.
- Chrome profile picker ("Who's using Chrome?"): prefer the PHOTO avatar when the owner asked for a photo/personal profile; avoid letter-only avatar cards unless requested.
- Prefer clicking clear UI targets (buttons, fields, links).
- CRITICAL SPEED: For ANY text into a field, use action "paste" (or "type") with the FULL value.
  Never spell character-by-character. The runtime pastes via clipboard in one shot.
- Forms: click field → paste full value → tab/click next. Batch many fields in one plan.
- hotkey example: keys=["ctrl","s"]
- press/tab example: text="enter" or text="tab"
- Max 12 steps for forms. No destructive actions (no delete files, no format, no shutdown).
- NEVER clear/select-all-delete/overwrite text unless rewriting content Voxoryl already typed into that same field.
- If unsure where to click, return steps=[] and explain in speak.
- When KNOWN FORM VALUES are provided, use ONLY those — never invent personal data.
- Set done=true only when the owner goal is fully achieved on this screen; else done=false so another look can continue.
"""

LOCATE_PROMPT = """You are locating a UI target on a Windows screenshot for a mouse click.
Image size is {width}x{height} pixels. Origin (0,0) is top-left.
Find: {target}

Return ONLY JSON (no markdown):
{{"found": true, "x": 123, "y": 456, "label": "short name of what you clicked", "confidence": 0.0}}
Rules:
- x,y must be the CENTER of the clickable target in screenshot pixel space.
- If multiple similar items exist, pick the one that best matches the description.
- Chrome "Who's using Chrome?": prefer PHOTO avatar for personal/photo profiles; letter-only avatars only when that style was requested.
- If not visible, return {{"found": false, "x": 0, "y": 0, "label": "", "confidence": 0}}.
"""

# While actively controlling, refresh watch more aggressively (daemon still uses SCREEN_WATCH_INTERVAL_SEC)
_CONTROL_ACTIVE_UNTIL: float = 0.0


def mark_control_active(seconds: float = 90.0) -> None:
    """Extend aggressive screen-context window after mouse/keyboard use."""
    import time

    global _CONTROL_ACTIVE_UNTIL
    _CONTROL_ACTIVE_UNTIL = max(_CONTROL_ACTIVE_UNTIL, time.time() + max(5.0, float(seconds)))


def control_active() -> bool:
    import time

    return time.time() < _CONTROL_ACTIVE_UNTIL


FLASH_FILL_SYSTEM = """You fill web/desktop forms FAST using known identity values.
Return ONLY JSON with click + paste steps (same schema as screen control).
For every visible field that matches a known value: click the field, then paste the full string.
Use tab between fields when layout is linear. Prefer paste over type. Max 12 steps.
Skip fields you cannot map confidently.
"""


async def plan_screen_actions(
    goal: str,
    description: str,
    width: int,
    height: int,
    *,
    identity_block: str = "",
    flash: bool = False,
) -> dict[str, Any]:
    system = FLASH_FILL_SYSTEM if flash else PLAN_SYSTEM
    raw = await chat_local(
        [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"Screen size: {width}x{height}\n"
                    f"Vision description:\n{description}\n\n"
                    f"{identity_block}\n\n"
                    f"Owner goal:\n{goal}\n\n"
                    "Plan concrete mouse/keyboard steps as JSON. Paste full field values."
                ),
            },
        ],
        temperature=0.1,
    )
    return parse_json_loose(raw) or {
        "summary": "Could not plan",
        "steps": [],
        "done": True,
        "speak": "I could not confidently plan screen actions from that view.",
    }


async def vision_locate(path: str, target: str, *, width: int = 0, height: int = 0) -> dict[str, Any]:
    """Ask the vision model for click coordinates directly (avoids text-LLM invented coords)."""
    blocked = _require_enabled()
    if blocked:
        return blocked
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"Screenshot missing: {path}"}
    if width <= 0 or height <= 0:
        try:
            from PIL import Image

            with Image.open(p) as im:
                width, height = im.size
        except Exception:
            width, height = 1280, 720

    prompt = LOCATE_PROMPT.format(width=width, height=height, target=(target or "").strip() or "primary button")
    vision = await vision_describe(str(p), prompt)
    if not vision.get("ok"):
        return vision
    parsed = parse_json_loose(str(vision.get("description") or "")) or {}
    found = bool(parsed.get("found"))
    try:
        x = float(parsed.get("x") or 0)
        y = float(parsed.get("y") or 0)
    except (TypeError, ValueError):
        x, y = 0.0, 0.0
    # Reject obvious nonsense / out of bounds
    if found and (x <= 0 or y <= 0 or x >= width or y >= height):
        found = False
    return {
        "ok": True,
        "found": found,
        "x": int(x),
        "y": int(y),
        "label": str(parsed.get("label") or ""),
        "confidence": float(parsed.get("confidence") or 0),
        "width": width,
        "height": height,
        "path": str(p.resolve()),
        "raw": vision.get("description"),
        "model": vision.get("model"),
    }


def click_xy(x: float, y: float, *, shot_w: int, shot_h: int) -> dict[str, Any]:
    """Click screenshot-space coordinates after scaling to the live desktop."""
    blocked = _require_enabled()
    if blocked:
        return blocked
    try:
        import pyautogui
    except ImportError:
        return {"ok": False, "error": "pyautogui not installed — pip install pyautogui"}
    try:
        from voxoryl.point_executor import ensure_dpi_aware

        ensure_dpi_aware()
    except Exception:
        pass
    sx, sy = _scale_xy(x, y, shot_w, shot_h)
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = float(settings.computer_use_pause)
    pyautogui.click(sx, sy)
    mark_control_active()
    return {"ok": True, "action": "click", "x": sx, "y": sy, "shot_x": int(x), "shot_y": int(y)}


async def locate_and_click(
    target: str,
    *,
    verify_gone: tuple[str, ...] | list[str] | None = None,
    retries: int = 2,
) -> dict[str, Any]:
    """Screenshot → VL locate coords → click → optional verify strings gone from look."""
    blocked = _require_enabled()
    if blocked:
        return blocked

    attempts: list[dict[str, Any]] = []
    for i in range(max(1, int(retries))):
        shot = await asyncio.to_thread(capture_screenshot)
        if not shot.get("ok"):
            return shot
        loc = await vision_locate(
            shot["path"],
            target,
            width=int(shot.get("width") or 0),
            height=int(shot.get("height") or 0),
        )
        attempt: dict[str, Any] = {"n": i + 1, "locate": loc, "screenshot": shot}
        if not loc.get("found"):
            attempts.append(attempt)
            continue
        clicked = await asyncio.to_thread(
            click_xy,
            float(loc["x"]),
            float(loc["y"]),
            shot_w=int(shot.get("width") or loc.get("width") or 1280),
            shot_h=int(shot.get("height") or loc.get("height") or 720),
        )
        attempt["clicked"] = clicked
        attempts.append(attempt)
        _log({"at": _now(), "action": "locate_and_click", "target": target[:200], "attempt": attempt})
        if not clicked.get("ok"):
            continue
        if not verify_gone:
            return {
                "ok": True,
                "clicked": True,
                "locate": loc,
                "executed": {"ok": True, "executed": [clicked], "count": 1},
                "attempts": attempts,
                "speak": f"Clicked {loc.get('label') or 'target'}.",
            }
        await asyncio.sleep(0.85)
        verify = await refresh_screen_watch(
            question="Describe the active window title and whether any profile picker dialog is visible. Be brief."
        )
        desc = str((verify.get("vision") or {}).get("description") or "").lower()
        attempt["verify"] = verify
        if not any(v.lower() in desc for v in verify_gone):
            return {
                "ok": True,
                "clicked": True,
                "verified": True,
                "locate": loc,
                "executed": {"ok": True, "executed": [clicked], "count": 1},
                "attempts": attempts,
                "speak": f"Clicked {loc.get('label') or 'target'}.",
            }
    last_loc = (attempts[-1].get("locate") if attempts else {}) or {}
    return {
        "ok": False,
        "clicked": any(a.get("clicked", {}).get("ok") for a in attempts),
        "locate": last_loc,
        "attempts": attempts,
        "speak": "Could not confidently click that on-screen target.",
    }


def _scale_xy(x: float, y: float, shot_w: int, shot_h: int) -> tuple[int, int]:
    """Map screenshot coords to live screen coords (DPI-aware when possible)."""
    try:
        from voxoryl.point_executor import ensure_dpi_aware

        ensure_dpi_aware()
    except Exception:
        pass
    try:
        from voxoryl.screen_grounding import shot_xy_to_screen

        mapped = shot_xy_to_screen(x, y, shot_w=shot_w, shot_h=shot_h)
        if mapped is not None:
            return mapped
    except Exception:
        pass
    try:
        from PIL import ImageGrab

        full = ImageGrab.grab()
        fw, fh = full.size
    except Exception:
        fw, fh = shot_w, shot_h
    if shot_w <= 0 or shot_h <= 0:
        return int(x), int(y)
    return int(x * fw / shot_w), int(y * fh / shot_h)


def execute_steps(
    steps: list[dict[str, Any]],
    *,
    shot_w: int,
    shot_h: int,
    goal: str = "",
) -> dict[str, Any]:
    blocked = _require_enabled()
    if blocked:
        return blocked
    try:
        from voxoryl.safety import gate_keyboard_steps, record_text_write

        gated = gate_keyboard_steps(steps, goal=goal)
        if not gated.get("ok"):
            return gated
    except Exception:
        # Fail-closed if safety module cannot run destructive checks
        from voxoryl.safety import analyze_destructive_steps, refuse

        if analyze_destructive_steps(steps).get("destructive"):
            return refuse("unknown", detail="Safety gate failed closed on destructive steps.")
    try:
        import pyautogui
    except ImportError:
        return {"ok": False, "error": "pyautogui not installed — pip install pyautogui"}

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = float(settings.computer_use_pause)
    results = []
    # Forms need more steps than generic control
    limit = min(len(steps), max(int(settings.computer_use_max_steps), 12))
    pending_select_all = False

    for step in steps[:limit]:
        action = str(step.get("action") or "").lower().strip()
        reason = str(step.get("reason") or "")
        try:
            if action in {"click", "double_click", "right_click", "move"}:
                pending_select_all = False
                x, y = _scale_xy(float(step.get("x") or 0), float(step.get("y") or 0), shot_w, shot_h)
                if action == "move":
                    pyautogui.moveTo(x, y, duration=0.15)
                elif action == "double_click":
                    pyautogui.doubleClick(x, y)
                elif action == "right_click":
                    pyautogui.rightClick(x, y)
                else:
                    pyautogui.click(x, y)
                results.append({"ok": True, "action": action, "x": x, "y": y, "reason": reason})
            elif action in {"type", "paste"}:
                text = str(step.get("text") or "")
                if pending_select_all:
                    from voxoryl.safety import may_clear_or_overwrite_focused_text

                    owned = may_clear_or_overwrite_focused_text(goal=goal)
                    if not owned.get("ok"):
                        results.append({**owned, "action": action, "reason": reason})
                        break
                    pending_select_all = False
                # Flash path: always clipboard paste (orders of magnitude faster than per-key)
                _paste_unicode(text[:4000])
                if text.strip():
                    from voxoryl.safety import record_text_write

                    record_text_write(text[:4000], source="screen_paste")
                results.append({"ok": True, "action": "paste", "chars": len(text[:4000]), "reason": reason})
            elif action == "tab":
                pending_select_all = False
                pyautogui.press("tab")
                results.append({"ok": True, "action": "tab", "reason": reason})
            elif action == "hotkey":
                keys = [str(k).lower() for k in (step.get("keys") or [])][:4]
                keyset = set(keys)
                if keyset == {"ctrl", "a"} or keyset == {"a", "ctrl"}:
                    pending_select_all = True
                elif pending_select_all and keyset & {"delete", "backspace"}:
                    from voxoryl.safety import may_clear_or_overwrite_focused_text

                    owned = may_clear_or_overwrite_focused_text(goal=goal)
                    if not owned.get("ok"):
                        results.append({**owned, "action": action, "keys": keys, "reason": reason})
                        break
                    pending_select_all = False
                elif keyset & {"delete", "backspace"} and keyset & {"shift", "ctrl", "alt", "win"}:
                    from voxoryl.safety import gate_hotkey

                    hk = gate_hotkey(keys, goal=goal)
                    if not hk.get("ok"):
                        results.append({**hk, "action": action, "keys": keys, "reason": reason})
                        break
                if keys:
                    pyautogui.hotkey(*keys)
                results.append({"ok": True, "action": "hotkey", "keys": keys, "reason": reason})
            elif action == "press":
                key = str(step.get("text") or step.get("key") or "enter").lower()
                if pending_select_all and key in {"delete", "del", "backspace", "bksp"}:
                    from voxoryl.safety import may_clear_or_overwrite_focused_text

                    owned = may_clear_or_overwrite_focused_text(goal=goal)
                    if not owned.get("ok"):
                        results.append({**owned, "action": action, "key": key, "reason": reason})
                        break
                    pending_select_all = False
                pyautogui.press(key)
                results.append({"ok": True, "action": "press", "key": key, "reason": reason})
            elif action == "scroll":
                amount = int(step.get("amount") or -400)
                pyautogui.scroll(amount)
                results.append({"ok": True, "action": "scroll", "amount": amount, "reason": reason})
            elif action == "wait":
                import time

                time.sleep(min(float(step.get("seconds") or 0.5), 5.0))
                results.append({"ok": True, "action": "wait", "reason": reason})
            else:
                results.append({"ok": False, "action": action, "error": "unknown action"})
        except Exception as exc:
            results.append({"ok": False, "action": action, "error": str(exc), "reason": reason})
            break

    refused = next((r for r in results if r.get("refused")), None)
    if refused:
        return {
            "ok": False,
            "refused": True,
            "safety": True,
            "executed": results,
            "count": len(results),
            "speak": refused.get("speak"),
            "error": refused.get("error") or refused.get("detail"),
        }
    if results:
        mark_control_active()
    return {"ok": True, "executed": results, "count": len(results)}


def _paste_unicode(text: str) -> None:
    """Type non-ASCII via clipboard paste (Ctrl+V)."""
    import pyautogui

    try:
        import pyperclip
    except ImportError:
        pyautogui.write("".join(ch for ch in text if ord(ch) < 128), interval=0.01)
        return

    old = None
    try:
        old = pyperclip.paste()
    except Exception:
        pass
    pyperclip.copy(text)
    pyautogui.hotkey("ctrl", "v")
    if old is not None:
        try:
            pyperclip.copy(old)
        except Exception:
            pass


def type_now(
    text: str,
    *,
    press_enter: bool = False,
    clear_first: bool = False,
    goal: str = "",
) -> dict[str, Any]:
    """Paste into focused control — no vision (rapid). Tracks owned writes."""
    blocked = _require_enabled()
    if blocked:
        return blocked
    raw = (text or "").strip()
    if not raw and not clear_first:
        return {"ok": False, "speak": "Nothing to type."}
    try:
        import pyautogui
    except ImportError:
        return {"ok": False, "error": "pyautogui missing", "speak": "Install pyautogui."}
    try:
        from voxoryl.safety import may_clear_or_overwrite_focused_text, record_text_write

        if clear_first:
            owned = may_clear_or_overwrite_focused_text(goal=goal or "rewrite owned field")
            if not owned.get("ok"):
                return owned
            pyautogui.FAILSAFE = True
            pyautogui.hotkey("ctrl", "a")
            pyautogui.press("backspace")
        pyautogui.FAILSAFE = True
        if raw:
            _paste_unicode(raw)
            record_text_write(raw, source="type_now")
        if press_enter:
            pyautogui.press("enter")
        return {
            "ok": True,
            "typed": raw[:200],
            "cleared_first": bool(clear_first),
            "speak": f"Typed {len(raw)} characters" + (" and Enter." if press_enter else "."),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "speak": f"Type failed: {exc}"}


def _store_watch(shot: dict[str, Any], vision: dict[str, Any]) -> None:
    global _LATEST_CONTEXT
    _LATEST_CONTEXT = {
        "at": _now(),
        "description": str(vision.get("description") or "")[:2000],
        "path": str(shot.get("path") or ""),
        "width": int(shot.get("width") or 0),
        "height": int(shot.get("height") or 0),
        "model": vision.get("model"),
    }
    try:
        path = settings.voxoryl_data_dir / "screen_watch.json"
        path.write_text(json.dumps(_LATEST_CONTEXT, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


async def refresh_screen_watch(*, question: str = "") -> dict[str, Any]:
    blocked = _require_enabled()
    if blocked:
        return blocked
    shot = await asyncio.to_thread(capture_screenshot)
    if not shot.get("ok"):
        return shot
    ask = question or (
        "Briefly list: active window title, main UI regions, visible buttons, "
        "focused field if any, and any dialog/modals. Max 8 short bullets."
    )
    vision = await vision_describe(shot["path"], ask)
    if vision.get("ok"):
        _store_watch(shot, vision)
    return {
        "ok": bool(vision.get("ok")),
        "action": "watch",
        "screenshot": shot,
        "vision": vision,
        "context": latest_screen_context(),
        "speak": "Screen context updated." if vision.get("ok") else str(vision.get("error") or "Watch failed."),
    }


async def tool_screen(
    action: str = "look",
    goal: str = "",
    question: str = "",
    text: str = "",
    rounds: int = 1,
) -> dict[str, Any]:
    """
    Screen awareness + control.
    action: look | act | flash_fill | click_type | type | paste | watch | locate
    rounds: for act — screenshot→plan→execute→repeat until done or limit
    """
    blocked = _require_enabled()
    if blocked:
        # Fail loudly for UI/voice
        blocked["speak"] = blocked.get("speak") or (
            "Computer use is disabled. Set COMPUTER_USE_ENABLED=true in .env and restart Voxoryl."
        )
        return blocked

    action = (action or "look").lower().strip()
    flash = action in {"flash_fill", "form_fill", "autofill", "fill_form"}
    if flash:
        action = "flash_fill"

    # Fail-closed: refuse clear/wipe goals unless focused text is owned
    try:
        from voxoryl.safety import gate_text_destruction_goal, goal_requests_text_destruction

        if goal_requests_text_destruction(goal or text or "") and action in {
            "act",
            "flash_fill",
            "click_type",
            "type",
            "paste",
            "type_now",
            "locate",
        }:
            destroy_gate = gate_text_destruction_goal(goal or text or "")
            if not destroy_gate.get("ok"):
                return destroy_gate
    except Exception:
        from voxoryl.safety import goal_requests_text_destruction, refuse

        if goal_requests_text_destruction(goal or text or ""):
            return refuse("unknown", detail="Safety gate failed closed on clear/wipe goal.")

    if action in {"type", "paste", "type_now"} or (
        action == "click_type" and (text or goal) and "click" not in (goal or "").lower()
    ):
        payload = text or goal or ""
        payload = re.sub(
            r"^\s*(type|paste|write|enter|likho|likh\s*do)\s*[:\-]?\s*",
            "",
            payload,
            flags=re.I,
        ).strip() or payload
        press_enter = bool(re.search(r"\b(press enter|hit enter|and enter|submit)\b", (goal or text or ""), re.I))
        clear_first = bool(
            re.search(
                r"\b(replace|rewrite|change\s+(?:that|it|this)|clear\s+(?:and\s+)?(?:type|paste)|overwrite)\b",
                (goal or text or ""),
                re.I,
            )
        )
        typed = await asyncio.to_thread(
            type_now,
            payload,
            press_enter=press_enter,
            clear_first=clear_first,
            goal=goal or text or "",
        )
        if typed.get("ok"):
            mark_control_active()
        _log({"at": _now(), "action": "type", "chars": len(payload), "clear_first": clear_first})
        return typed

    if action in {"watch", "refresh_watch"}:
        return await refresh_screen_watch(question=question or goal)

    if action in {"locate", "find_click", "click_target"}:
        target = (goal or question or text or "").strip()
        if not target:
            return {"ok": False, "speak": "What should I click on screen?"}
        return await locate_and_click(target)

    max_rounds = 1
    if action in {"act", "click_type", "flash_fill"}:
        # Default multi-round for act so screenshot→plan→execute can verify
        default_rounds = 3 if action == "act" else 1
        try:
            max_rounds = max(1, min(int(rounds or default_rounds), 5))
        except (TypeError, ValueError):
            max_rounds = default_rounds

    rounds_out: list[dict[str, Any]] = []
    final: dict[str, Any] = {"ok": False, "action": action, "enabled": True}

    for round_i in range(max_rounds):
        shot = await asyncio.to_thread(capture_screenshot)
        if not shot.get("ok"):
            return shot

        if flash:
            ask = (
                question
                or "List every form field label visible (name, email, phone, address, city, etc.) "
                "and roughly where each input box is on screen."
            )
        else:
            ask = question or goal or "What is on the user's screen right now?"

        # Prefer direct VL locate when the goal is clearly a single click target
        clickish = bool(
            re.search(
                r"\b(click|select|press|tap)\b",
                (goal or "").lower(),
            )
        ) and action == "act"
        if clickish and round_i == 0:
            located = await locate_and_click(goal)
            if located.get("ok") and located.get("clicked"):
                _store_watch(shot, {"description": f"Located+clicked: {goal}", "model": settings.ollama_vision_model})
                memory.remember_fact(f"Screen locate: {(goal or '')[:120]}", tags=["screen", "computer_use"])
                located["action"] = action
                located["rounds"] = [located]
                return located

        vision = await vision_describe(shot["path"], ask)
        if not vision.get("ok"):
            return {**vision, "screenshot": shot}

        _store_watch(shot, vision)

        result: dict[str, Any] = {
            "ok": True,
            "action": action,
            "screenshot": shot,
            "vision": vision,
            "enabled": True,
            "context": latest_screen_context(),
            "round": round_i + 1,
        }

        if action in {"look", "see", "describe"}:
            memory.remember_fact(f"Screen: {(vision.get('description') or '')[:180]}", tags=["screen"])
            _log({"at": _now(), "action": "look", "goal": goal, "path": shot.get("path")})
            result["speak"] = (vision.get("description") or "I can see the screen.")[:400]
            return result

        identity_block = ""
        if flash or any(k in (goal or "").lower() for k in ("fill", "form", "autofill")):
            from voxoryl.identity import identity_prompt_block, resolve_identity

            identity_block = identity_prompt_block()
            result["identity_keys"] = list(resolve_identity().keys())

        plan_goal = goal or ask
        if flash and not goal:
            plan_goal = "Fill all visible form fields using known identity values as fast as possible."

        prior = str(latest_screen_context().get("description") or "")
        desc = str(vision.get("description") or "")
        if prior and prior[:80] not in desc:
            desc = prior[:400] + "\n" + desc

        plan = await plan_screen_actions(
            plan_goal,
            desc,
            int(shot.get("width") or 1280),
            int(shot.get("height") or 720),
            identity_block=identity_block,
            flash=flash,
        )
        result["plan"] = plan
        steps = plan.get("steps") or []
        if not steps:
            result["ok"] = False
            result["speak"] = plan.get("speak") or "I can see the screen but need a clearer target."
            _log({"at": _now(), "action": action, "goal": goal, "steps": 0, "round": round_i + 1})
            rounds_out.append(result)
            final = result
            # If planner says done with no steps, stop; else try another look once
            if plan.get("done") or round_i + 1 >= max_rounds:
                break
            await asyncio.sleep(0.4)
            continue

        executed = await asyncio.to_thread(
            execute_steps,
            steps,
            shot_w=int(shot.get("width") or 1280),
            shot_h=int(shot.get("height") or 720),
            goal=plan_goal,
        )
        result["executed"] = executed
        if executed.get("refused"):
            result["ok"] = False
            result["refused"] = True
            result["speak"] = executed.get("speak") or executed.get("error") or "Refused for safety."
            _log({"at": _now(), "action": action, "goal": goal, "refused": True, "executed": executed})
            rounds_out.append(result)
            final = result
            break
        # Require at least one successful input action
        ran = [
            r
            for r in (executed.get("executed") or [])
            if r.get("ok")
            and str(r.get("action") or "")
            in {"click", "double_click", "right_click", "type", "paste", "hotkey", "press", "tab", "scroll"}
        ]
        result["ok"] = bool(ran)
        result["speak"] = plan.get("speak") or plan.get("summary") or (
            "Form flash-filled." if flash else "Screen actions complete."
        )
        memory.remember_fact(f"Screen {action}: {(goal or ask)[:120]}", tags=["screen", "computer_use"])
        _log({"at": _now(), "action": action, "goal": goal, "plan": plan, "executed": executed, "round": round_i + 1})
        rounds_out.append(result)
        final = result
        if plan.get("done") or flash or round_i + 1 >= max_rounds:
            break
        await asyncio.sleep(0.55)

    final["rounds"] = rounds_out
    final["context"] = latest_screen_context()
    if not final.get("speak") and not final.get("ok"):
        final["speak"] = "Screen control could not complete that goal."
    return final
