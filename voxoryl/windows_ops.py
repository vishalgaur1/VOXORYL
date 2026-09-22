from __future__ import annotations

"""
Windows local controls — open apps, brightness, volume, PowerToys status.
Includes Hindi open phrases (khol / kholo / chalu).
"""

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

APP_CATALOG: dict[str, list[str]] = {
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        "chrome",
        "chrome.exe",
    ],
    "edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "msedge",
        "microsoft-edge:",
    ],
    "firefox": [
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
        "firefox",
    ],
    "notepad": ["notepad.exe"],
    "calculator": ["calc.exe"],
    "explorer": ["explorer.exe"],
    "spotify": [
        os.path.expandvars(r"%APPDATA%\Spotify\Spotify.exe"),
        "spotify:",
        "spotify",
    ],
    "vscode": [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
        r"C:\Program Files\Microsoft VS Code\Code.exe",
        "code",
    ],
    "code": [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
        "code",
    ],
    "cursor": [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\cursor\Cursor.exe"),
        "cursor",
    ],
    "whatsapp": [
        os.path.expandvars(r"%LOCALAPPDATA%\WhatsApp\WhatsApp.exe"),
        "whatsapp:",
        "whatsapp",
    ],
    "settings": ["ms-settings:"],
    "terminal": ["wt.exe", "powershell.exe"],
    "cmd": ["cmd.exe"],
    "powershell": ["powershell.exe"],
}

APP_ALIASES: list[tuple[str, str]] = [
    ("google chrome", "chrome"),
    ("chrome browser", "chrome"),
    ("chromium", "chrome"),
    ("chrome", "chrome"),
    ("ms edge", "edge"),
    ("microsoft edge", "edge"),
    ("edge", "edge"),
    ("firefox", "firefox"),
    ("notepad", "notepad"),
    ("calculator", "calculator"),
    ("calc", "calculator"),
    ("file explorer", "explorer"),
    ("explorer", "explorer"),
    ("spotify", "spotify"),
    ("vs code", "vscode"),
    ("vscode", "vscode"),
    ("visual studio code", "vscode"),
    ("cursor", "cursor"),
    ("whatsapp", "whatsapp"),
    ("settings", "settings"),
    ("windows settings", "settings"),
    ("terminal", "terminal"),
    ("powershell", "powershell"),
    ("cmd", "cmd"),
    ("command prompt", "cmd"),
]


def _run_ps(script: str, *, timeout: float = 12.0) -> dict[str, Any]:
    try:
        from voxoryl.safety import gate_shell_command

        gated = gate_shell_command(script)
        if not gated.get("ok"):
            return gated
    except Exception:
        from voxoryl.safety import shell_is_destructive, refuse

        if shell_is_destructive(script):
            return refuse("shell", detail="PowerShell safety gate failed closed.")
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return {
            "ok": completed.returncode == 0,
            "stdout": (completed.stdout or "").strip(),
            "stderr": (completed.stderr or "").strip(),
            "code": completed.returncode,
        }
    except FileNotFoundError:
        return {"ok": False, "error": "powershell not found", "hint": "Windows PowerShell required."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "powershell timeout"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _nircmd() -> str | None:
    for name in ("nircmd.exe", "nircmd"):
        found = shutil.which(name)
        if found:
            return found
    for p in (
        Path(os.environ.get("LOCALAPPDATA", "")) / "nircmd" / "nircmd.exe",
        Path("C:/Tools/nircmd.exe"),
        Path("C:/Windows/nircmd.exe"),
    ):
        if p.exists():
            return str(p)
    return None


def detect_app_name(message: str) -> str | None:
    lower = (message or "").lower()
    for alias, key in sorted(APP_ALIASES, key=lambda x: len(x[0]), reverse=True):
        if alias in lower:
            return key
    return None


def is_open_app_request(message: str) -> bool:
    lower = (message or "").lower()
    if not detect_app_name(message):
        return False
    open_words = (
        "open",
        "launch",
        "start",
        "run",
        "khol",
        "kholo",
        "kholna",
        "khol do",
        "khol sakta",
        "khol sakti",
        "chalu",
        "shuru",
        "start kar",
        "open kar",
        "launch kar",
    )
    return any(w in lower for w in open_words)


def is_new_tab_app_request(message: str) -> bool:
    """True for 'new tab in notepad' / 'open a new tab in the notepad app'."""
    lower = (message or "").lower()
    if not re.search(r"\bnew\s+tab\b", lower):
        return False
    key = detect_app_name(message)
    if not key:
        return False
    # Browsers use chrome_control; keep those out of windows new-tab.
    if key in {"chrome", "edge", "firefox"}:
        return False
    return True


def app_new_tab(app_key: str) -> dict[str, Any]:
    """
    Focus/launch an app that supports tabs, then Ctrl+T.
    Soft-fails if the app cannot be opened or focused.
    """
    key = (app_key or "").lower().strip()
    if not key:
        return {"ok": False, "speak": "Which app should get a new tab?", "action_only": True}
    if key in {"chrome", "edge", "firefox"}:
        return {"ok": False, "error": "use chrome pipeline", "speak": "", "action_only": True}

    title_needles = {
        "notepad": ("Notepad", "notepad"),
        "spotify": ("Spotify",),
        "vscode": ("Visual Studio Code", "Code"),
        "code": ("Visual Studio Code", "Code"),
        "cursor": ("Cursor",),
        "whatsapp": ("WhatsApp",),
        "terminal": ("Windows Terminal", "Terminal", "PowerShell", "cmd"),
        "powershell": ("PowerShell",),
        "cmd": ("Command Prompt", "cmd"),
    }.get(key, (key,))

    focused = focus_window_by_title(*title_needles)
    launched = None
    if not focused.get("ok"):
        launched = open_app(key)
        if not launched.get("ok"):
            return {
                "ok": False,
                "app": key,
                "error": launched.get("error") or "app unavailable",
                "speak": launched.get("speak") or f"Couldn't open {key}.",
                "action_only": True,
            }
        import time

        time.sleep(0.55)
        focused = focus_window_by_title(*title_needles)

    try:
        from voxoryl.safety import gate_hotkey

        gated = gate_hotkey(["ctrl", "t"])
        if not gated.get("ok"):
            return {**gated, "app": key, "action_only": True}
    except Exception:
        pass

    try:
        import pyautogui

        pyautogui.FAILSAFE = True
        pyautogui.hotkey("ctrl", "t")
        try:
            from voxoryl.software_knowledge import learn

            learn(key, action="new_tab", shortcut=["ctrl", "t"], ok=True)
        except Exception:
            pass
        return {
            "ok": True,
            "app": key,
            "focused": focused,
            "launched": launched,
            "speak": "",
            "action_only": True,
        }
    except Exception as exc:
        return {
            "ok": False,
            "app": key,
            "error": str(exc),
            "speak": f"Couldn't open a new tab in {key}.",
            "action_only": True,
        }


def open_app(app_key: str, *, extra_args: list[str] | None = None) -> dict[str, Any]:
    import sys

    key = (app_key or "").lower().strip()
    if not sys.platform.startswith("win"):
        return _open_app_posix(key, extra_args=extra_args)
    targets = APP_CATALOG.get(key)
    args = [str(a) for a in (extra_args or []) if str(a).strip()]
    if not targets:
        return {
            "ok": False,
            "error": f"unknown app {app_key}",
            "speak": f"I don't know how to open '{app_key}' yet.",
        }
    last_err = ""
    for target in targets:
        try:
            if ":" in target and not Path(target).exists() and not target.lower().endswith(".exe"):
                if args:
                    # Protocol handlers can't take Chrome profile args reliably
                    last_err = "protocol target cannot take extra_args"
                    continue
                os.startfile(target)  # type: ignore[attr-defined]
                return {"ok": True, "app": key, "launched": target, "speak": "", "action_only": True}
            path = Path(target)
            if path.exists():
                subprocess.Popen(
                    [str(path), *args],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
                return {
                    "ok": True,
                    "app": key,
                    "launched": str(path),
                    "args": args,
                    "speak": "",
                    "action_only": True,
                }
            found = shutil.which(target)
            if found:
                subprocess.Popen(
                    [found, *args],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
                return {
                    "ok": True,
                    "app": key,
                    "launched": found,
                    "args": args,
                    "speak": "",
                    "action_only": True,
                }
            if args:
                # cmd start cannot forward argv cleanly — skip
                last_err = "exe path not found for extra_args launch"
                continue
            completed = subprocess.run(
                ["cmd", "/c", "start", "", target],
                capture_output=True,
                text=True,
                timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if completed.returncode == 0:
                return {"ok": True, "app": key, "launched": target, "speak": "", "action_only": True}
            last_err = (completed.stderr or completed.stdout or "").strip()
        except Exception as exc:
            last_err = str(exc)
    return {
        "ok": False,
        "error": last_err or "launch failed",
        "speak": f"Couldn't open {key}. Is it installed?",
        "app": key,
    }


def _open_app_posix(key: str, *, extra_args: list[str] | None = None) -> dict[str, Any]:
    """Best-effort app launch on macOS/Linux (no Win32 catalog)."""
    import sys

    args = [str(a) for a in (extra_args or []) if str(a).strip()]
    mac_map = {
        "chrome": ["Google Chrome", "chrome"],
        "edge": ["Microsoft Edge"],
        "firefox": ["Firefox"],
        "spotify": ["Spotify"],
        "vscode": ["Visual Studio Code"],
        "code": ["Visual Studio Code"],
        "cursor": ["Cursor"],
        "terminal": ["Terminal"],
    }
    try:
        if sys.platform == "darwin":
            for name in mac_map.get(key, [key]):
                cmd = ["open", "-a", name]
                if args:
                    cmd.extend(["--args", *args])
                completed = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
                if completed.returncode == 0:
                    return {"ok": True, "app": key, "launched": name, "speak": "", "action_only": True, "platform": "darwin"}
            which = shutil.which(key) or shutil.which(f"{key}.exe")
            if which:
                subprocess.Popen([which, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return {"ok": True, "app": key, "launched": which, "speak": "", "action_only": True, "platform": "posix"}
        else:
            which = shutil.which(key) or shutil.which("google-chrome") or shutil.which("chromium")
            if which:
                subprocess.Popen([which, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return {"ok": True, "app": key, "launched": which, "speak": "", "action_only": True, "platform": "linux"}
            subprocess.Popen(["xdg-open", key], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"ok": True, "app": key, "launched": f"xdg-open:{key}", "speak": "", "action_only": True, "platform": "linux"}
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "speak": f"Couldn't open {key} on this platform.",
            "app": key,
            "platform": sys.platform,
        }
    return {
        "ok": False,
        "error": "app not found",
        "speak": f"Couldn't open {key}. Desktop computer-use is limited on this OS.",
        "app": key,
        "platform": sys.platform,
    }


def focus_window_by_title(*needles: str) -> dict[str, Any]:
    """Bring a visible window whose title contains any needle to the foreground."""
    import sys

    needles_l = [n.lower().strip() for n in needles if n and str(n).strip()]
    if not needles_l:
        return {"ok": False, "error": "no title needles"}
    if not sys.platform.startswith("win"):
        return {
            "ok": False,
            "error": "window focus via Win32 is Windows-only",
            "needles": needles_l,
            "platform": sys.platform,
        }
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        found: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _lparam):  # type: ignore[misc]
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = (buf.value or "").lower()
            if any(n in title for n in needles_l):
                found.append(int(hwnd))
                return False
            return True

        user32.EnumWindows(_enum, 0)
        if not found:
            return {"ok": False, "error": "window not found", "needles": needles_l}
        hwnd = found[0]
        # Restore if minimized, then foreground
        SW_RESTORE = 9
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        return {"ok": True, "hwnd": hwnd, "needles": needles_l}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "needles": needles_l}


def powertoys_status() -> dict[str, Any]:
    roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "PowerToys",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PowerToys",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "PowerToys",
    ]
    present = [str(r) for r in roots if r.exists()]
    return {
        "ok": True,
        "installed": bool(present),
        "paths": present,
        "speak": (
            "PowerToys detected on this PC."
            if present
            else "PowerToys not found — optional. winget install Microsoft.PowerToys"
        ),
        "hint": None if present else "winget install Microsoft.PowerToys",
    }


def volume_nudge(direction: str) -> dict[str, Any]:
    direction = (direction or "").lower()
    nircmd = _nircmd()
    if nircmd:
        if direction in {"mute", "unmute", "toggle"}:
            cmd = [nircmd, "mutesysvolume", "2"]
            label = "toggled mute"
        elif direction in {"up", "+"}:
            cmd = [nircmd, "changesysvolume", "5000"]
            label = "volume up"
        else:
            cmd = [nircmd, "changesysvolume", "-5000"]
            label = "volume down"
        try:
            subprocess.run(cmd, capture_output=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return {"ok": True, "engine": "nircmd", "speak": f"Done — {label}."}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "engine": "nircmd"}

    if direction in {"mute", "unmute", "toggle"}:
        key, label = "[char]173", "mute toggle"
    elif direction in {"up", "+"}:
        key, label = "[char]175", "volume up"
    else:
        key, label = "[char]174", "volume down"
    result = _run_ps(
        "Add-Type -AssemblyName System.Windows.Forms; "
        f"[System.Windows.Forms.SendKeys]::SendWait({key})"
    )
    if result.get("ok"):
        return {"ok": True, "engine": "sendkeys", "speak": f"Tried {label} via system keys."}
    return {
        "ok": False,
        "error": result.get("error") or result.get("stderr") or "volume control failed",
        "speak": "Couldn't change volume from here.",
        "powertoys": powertoys_status(),
    }


def set_brightness(level: int | None = None, *, delta: int | None = None) -> dict[str, Any]:
    if level is None and delta is None:
        level = 40
    if level is not None:
        level = max(0, min(100, int(level)))
        ps = (
            "$b = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction SilentlyContinue; "
            "if (-not $b) { Write-Error 'NO_WMI_BRIGHTNESS'; exit 2 }; "
            f"$b | ForEach-Object {{ $_.WmiSetBrightness(1, {level}) }}; "
            f"Write-Output {level}"
        )
        label = f"brightness → {level}%"
    else:
        d = int(delta or -10)
        ps = (
            "$cur = (Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction SilentlyContinue | "
            "Select-Object -First 1).CurrentBrightness; "
            "if ($null -eq $cur) { Write-Error 'NO_WMI_BRIGHTNESS'; exit 2 }; "
            f"$n = [Math]::Max(0, [Math]::Min(100, [int]$cur + ({d}))); "
            "$m = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction SilentlyContinue; "
            "$m | ForEach-Object { $_.WmiSetBrightness(1, $n) }; Write-Output $n"
        )
        label = f"brightness {'up' if d > 0 else 'down'}"

    result = _run_ps(ps)
    if result.get("ok") and "NO_WMI" not in (result.get("stderr") or ""):
        return {"ok": True, "engine": "wmi", "level": result.get("stdout") or "", "speak": f"Done — {label}."}
    nircmd = _nircmd()
    if nircmd and delta is not None:
        try:
            subprocess.run(
                [nircmd, "changebrightness", str(int(delta))],
                capture_output=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return {"ok": True, "engine": "nircmd", "speak": f"Tried {label} via nircmd."}
        except Exception:
            pass
    return {
        "ok": False,
        "error": result.get("stderr") or result.get("error") or "brightness unavailable",
        "speak": "Can't set brightness on this display from software.",
        "powertoys": powertoys_status(),
    }


async def tool_windows(
    action: str = "status",
    *,
    message: str = "",
    level: int | None = None,
    app: str = "",
) -> dict[str, Any]:
    action = (action or "status").lower().strip()
    lower = (message or "").lower()

    if action in {"new_tab", "tab"} or is_new_tab_app_request(message):
        key = (app or "").lower().strip() or detect_app_name(message) or ""
        if key:
            return app_new_tab(key)
        return {"ok": False, "speak": "Which app should get a new tab?", "action_only": True}

    if action in {"open", "launch", "start", "app"} or is_open_app_request(message) or app:
        key = (app or "").lower().strip() or detect_app_name(message) or ""
        if not key:
            m = re.search(r"(?:open|launch|start|kholo?|chalu)\s+([a-z0-9][a-z0-9 ._-]{1,40})", lower)
            if m:
                key = detect_app_name(m.group(1)) or m.group(1).strip().split()[0]
        if key:
            return open_app(key)
        return {"ok": False, "speak": "Kaunsa app? Chrome, Edge, Notepad, Spotify, VS Code…"}

    if action in {"powertoys", "status"} and not any(
        k in lower for k in ("volume", "mute", "brightness", "bright", "open", "khol", "chrome")
    ):
        if "powertoys" in lower or action == "powertoys" or any(
            k in lower
            for k in (
                "awake",
                "fancyzones",
                "color picker",
                "text extractor",
                "find my mouse",
                "powertoys run",
            )
        ):
            from voxoryl.powertoys_bridge import tool_powertoys

            return await tool_powertoys(action="auto", message=message)
        pt = powertoys_status()
        # Prefer rich bridge status
        try:
            from voxoryl.powertoys_bridge import powertoys_installed

            rich = powertoys_installed()
            return {**pt, **rich, "speak": rich.get("speak") or pt.get("speak")}
        except Exception:
            return {
                "ok": True,
                "powertoys": pt,
                "speak": "I can open apps, change volume, and often brightness. " + str(pt.get("speak") or ""),
                "actions": ["open", "volume_up", "volume_down", "mute", "brightness_down", "brightness_up"],
            }

    if action in {"mute"} or "mute" in lower:
        return volume_nudge("mute")
    if action in {"volume_up", "up"} or any(k in lower for k in ("volume up", "louder", "turn up the volume")):
        return volume_nudge("up")
    if action in {"volume_down", "down"} or any(
        k in lower for k in ("volume down", "quieter", "lower the volume", "turn down the volume")
    ):
        return volume_nudge("down")

    if action in {"brightness", "brightness_down", "brightness_up"} or "brightness" in lower or "bright" in lower:
        if level is not None:
            return set_brightness(level)
        if any(k in lower for k in ("lower brightness", "dim", "darker", "brightness down", "less bright")):
            return set_brightness(delta=-15)
        if any(k in lower for k in ("raise brightness", "brighter", "brightness up", "more bright")):
            return set_brightness(delta=15)
        m = re.search(r"brightness\s*(?:to|=)?\s*(\d{1,3})", lower)
        if m:
            return set_brightness(int(m.group(1)))
        return set_brightness(delta=-10)

    if "powertoys" in lower:
        return powertoys_status()
    return powertoys_status()
