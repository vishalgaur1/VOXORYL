from __future__ import annotations

"""
PowerToys bridge — launch / control utilities so Voxoryl can drive the desktop fast.

Uses installed binaries under Program Files\\PowerToys when present.
Hotkeys are best-effort defaults (user may have rebound them in PowerToys settings).
"""

import os
import subprocess
import time
from pathlib import Path
from typing import Any


def _pt_roots() -> list[Path]:
    return [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PowerToys",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "PowerToys",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "PowerToys",
    ]


def find_exe(name: str) -> Path | None:
    for root in _pt_roots():
        cand = root / name
        if cand.exists():
            return cand
    return None


def powertoys_installed() -> dict[str, Any]:
    root = next((r for r in _pt_roots() if r.exists()), None)
    tools = {
        "launcher": find_exe("PowerToys.PowerLauncher.exe"),
        "awake": find_exe("PowerToys.Awake.exe"),
        "fancyzones": find_exe("PowerToys.FancyZones.exe"),
        "fancyzones_editor": find_exe("PowerToys.FancyZonesEditor.exe"),
        "color_picker": find_exe("PowerToys.ColorPickerUI.exe"),
        "text_extractor": find_exe("PowerToys.PowerOCR.exe"),
        "mouse_jump": find_exe("PowerToys.MouseJumpUI.exe"),
        "always_on_top": find_exe("PowerToys.AlwaysOnTop.exe"),
        "main": find_exe("PowerToys.exe"),
    }
    present = {k: str(v) for k, v in tools.items() if v}
    return {
        "ok": True,
        "installed": bool(root),
        "root": str(root) if root else None,
        "tools": present,
        "speak": (
            f"PowerToys ready — {', '.join(present.keys())}."
            if present
            else "PowerToys not found. Install: winget install Microsoft.PowerToys"
        ),
    }


def _launch(exe: Path | None, args: list[str] | None = None, *, label: str = "") -> dict[str, Any]:
    if not exe or not exe.exists():
        return {
            "ok": False,
            "error": f"missing {label or 'tool'}",
            "speak": f"{label or 'That PowerToys tool'} isn't installed.",
            "hint": "winget install Microsoft.PowerToys",
        }
    try:
        subprocess.Popen(
            [str(exe), *(args or [])],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        return {"ok": True, "launched": str(exe), "args": args or [], "speak": f"Opened {label or exe.stem}."}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "speak": f"Couldn't start {label or exe.name}."}


def _hotkey(*keys: str, label: str = "") -> dict[str, Any]:
    try:
        from voxoryl.safety import gate_hotkey

        gated = gate_hotkey(list(keys))
        if not gated.get("ok"):
            return {**gated, "speak": gated.get("speak") or label}
    except Exception:
        pass
    try:
        import pyautogui
    except ImportError:
        return {"ok": False, "error": "pyautogui missing", "speak": "Install pyautogui for hotkeys."}
    try:
        pyautogui.hotkey(*keys)
        return {"ok": True, "hotkey": "+".join(keys), "speak": label or f"Sent {'+'.join(keys)}."}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "speak": f"Hotkey failed: {exc}"}


def open_run(query: str = "") -> dict[str, Any]:
    """Open PowerToys Run (launcher). Optionally type a query and Enter."""
    status = powertoys_installed()
    launcher = find_exe("PowerToys.PowerLauncher.exe")
    launched = _launch(launcher, label="PowerToys Run")
    if not launched.get("ok"):
        # Fallback: Win+R
        alt = _hotkey("win", "r", label="Opened Run dialog (Win+R).")
        if query.strip() and alt.get("ok"):
            time.sleep(0.25)
            try:
                import pyautogui
                import pyperclip

                pyperclip.copy(query.strip())
                pyautogui.hotkey("ctrl", "v")
            except Exception:
                pass
        return {**alt, "powertoys": status}
    if query.strip():
        time.sleep(0.35)
        try:
            import pyautogui
            import pyperclip

            pyperclip.copy(query.strip())
            pyautogui.hotkey("ctrl", "v")
            time.sleep(0.15)
            pyautogui.press("enter")
            launched["speak"] = f"PowerToys Run → {query.strip()[:60]}"
            launched["query"] = query.strip()
        except Exception as exc:
            launched["query_error"] = str(exc)
    return {**launched, "powertoys": status}


def keep_awake(hours: float = 2.0, *, display_on: bool = True) -> dict[str, Any]:
    awake = find_exe("PowerToys.Awake.exe")
    secs = max(60, int(float(hours) * 3600))
    args = ["--time-limit", str(secs)]
    if display_on:
        args.append("--display-on")
    result = _launch(awake, args, label="Awake")
    if result.get("ok"):
        result["speak"] = f"Keeping PC awake for ~{hours:g}h" + (" (display on)." if display_on else ".")
        result["seconds"] = secs
    return result


def stop_awake() -> dict[str, Any]:
    """Best-effort: kill Awake process so normal sleep resumes."""
    try:
        completed = subprocess.run(
            ["taskkill", "/IM", "PowerToys.Awake.exe", "/F"],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        ok = completed.returncode == 0 or "not found" in (completed.stderr or "").lower()
        return {
            "ok": ok,
            "speak": "Awake stopped — normal sleep rules apply." if ok else "Couldn't stop Awake.",
            "stderr": (completed.stderr or "").strip(),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "speak": "Couldn't stop Awake."}


def fancy_zones_editor() -> dict[str, Any]:
    return _launch(find_exe("PowerToys.FancyZonesEditor.exe") or find_exe("PowerToys.FancyZones.exe"), label="FancyZones")


def snap_fancy_zones() -> dict[str, Any]:
    """Hold Win while dragging is interactive; editor is the reliable automation entry."""
    return fancy_zones_editor()


def color_picker() -> dict[str, Any]:
    return _launch(find_exe("PowerToys.ColorPickerUI.exe"), label="Color Picker")


def text_extractor() -> dict[str, Any]:
    """PowerToys Text Extractor (OCR) — user selects a region."""
    return _launch(find_exe("PowerToys.PowerOCR.exe"), label="Text Extractor")


def find_mouse() -> dict[str, Any]:
    """Highlight cursor — try Mouse Jump UI, else double-Ctrl Find My Mouse hotkey."""
    jump = find_exe("PowerToys.MouseJumpUI.exe")
    if jump:
        return _launch(jump, label="Mouse Jump")
    # Default Find My Mouse: left Ctrl twice
    try:
        import pyautogui

        pyautogui.press("ctrl")
        time.sleep(0.08)
        pyautogui.press("ctrl")
        return {"ok": True, "speak": "Find My Mouse pulse sent (Ctrl, Ctrl)."}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "speak": "Couldn't highlight the mouse."}


def always_on_top() -> dict[str, Any]:
    # Default hotkey Win+Ctrl+T — also try launching helper
    result = _hotkey("win", "ctrl", "t", label="Toggled Always On Top (Win+Ctrl+T).")
    if not result.get("ok"):
        return _launch(find_exe("PowerToys.AlwaysOnTop.exe"), label="Always On Top")
    return result


async def tool_powertoys(action: str = "status", *, message: str = "", query: str = "") -> dict[str, Any]:
    action = (action or "status").lower().strip()
    lower = (message or "").lower()
    q = (query or "").strip()

    if action in {"auto", ""}:
        # Infer from message
        if any(k in lower for k in ("powertoys run", "pt run", "launcher")):
            action = "run"
        elif any(k in lower for k in ("keep awake", "stay awake", "don't sleep", "dont sleep", "caffeine")):
            action = "awake"
        elif any(k in lower for k in ("stop awake", "allow sleep")):
            action = "stop_awake"
        elif any(k in lower for k in ("fancyzones", "fancy zones", "zone editor", "snap layout")):
            action = "zones"
        elif any(k in lower for k in ("color picker", "eyedropper", "pick color")):
            action = "color"
        elif any(k in lower for k in ("text extractor", "extract text", "ocr")):
            action = "ocr"
        elif any(k in lower for k in ("find my mouse", "where is my mouse", "mouse jump")):
            action = "find_mouse"
        elif "always on top" in lower:
            action = "aot"
        else:
            action = "status"

    if action in {"status", "list"} or lower.strip() in {"powertoys", "power toys"}:
        return powertoys_installed()

    if action in {"run", "launcher", "search"} or any(
        k in lower for k in ("powertoys run", "power toys run", "pt run", "launcher", "spotlight")
    ):
        if not q:
            # "powertoys run chrome" / "run notepad with powertoys"
            for prefix in ("powertoys run ", "power toys run ", "pt run ", "run "):
                if lower.startswith(prefix) and "power" in lower or lower.startswith("powertoys run"):
                    break
            import re

            m = re.search(r"(?:powertoys run|pt run|launcher)\s+(.+)$", lower)
            if m:
                q = m.group(1).strip()
            elif lower.startswith("run ") and "power" in lower:
                q = lower.split("run ", 1)[-1].strip()
        return open_run(q)

    if action in {"awake", "keep_awake", "caffeine"} or any(
        k in lower for k in ("keep awake", "stay awake", "don't sleep", "dont sleep", "caffeine", "awake on")
    ):
        hours = 2.0
        import re

        m = re.search(r"(\d+(?:\.\d)?)\s*h", lower)
        if m:
            hours = float(m.group(1))
        return keep_awake(hours)

    if action in {"sleep", "stop_awake"} or any(k in lower for k in ("stop awake", "allow sleep", "awake off")):
        return stop_awake()

    if action in {"zones", "fancyzones", "snap"} or any(
        k in lower for k in ("fancyzones", "fancy zones", "window zones", "snap layout", "zone editor")
    ):
        return fancy_zones_editor()

    if action in {"color", "color_picker", "eyedropper"} or any(
        k in lower for k in ("color picker", "eyedropper", "pick color")
    ):
        return color_picker()

    if action in {"ocr", "text_extractor", "extract_text"} or any(
        k in lower for k in ("text extractor", "extract text", "ocr", "copy text from screen")
    ):
        return text_extractor()

    if action in {"find_mouse", "mouse"} or any(
        k in lower for k in ("find my mouse", "where is my mouse", "highlight mouse", "mouse jump")
    ):
        return find_mouse()

    if action in {"aot", "always_on_top"} or "always on top" in lower:
        return always_on_top()

    return powertoys_installed()
