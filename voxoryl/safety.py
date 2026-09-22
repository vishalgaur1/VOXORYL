from __future__ import annotations

"""
Hard safety policy: Voxoryl must not destroy user content it did not create.

Text: only clear/overwrite text Voxoryl previously wrote into that focus.
Files: only delete paths under the Voxoryl project tree (fail-closed).
"""

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voxoryl.config import settings

ROOT = Path(__file__).resolve().parent.parent
_LOCK = threading.RLock()
_MAX_WRITES = 400

SPEAK_REFUSE_TEXT = (
    "I won't clear or overwrite text I didn't write. "
    "Ask me to change something I typed, or edit it yourself."
)
SPEAK_REFUSE_FILE = (
    "I won't delete files outside the Voxoryl project. "
    "I can only remove files under the Voxoryl folder."
)
SPEAK_REFUSE_SHELL = (
    "I won't run a destructive file command outside the Voxoryl project."
)
SPEAK_REFUSE_UNKNOWN = (
    "I'm not sure that content is mine, so I won't destroy it."
)

# Shell / PS patterns that delete or shred content
_SHELL_DESTRUCTIVE = re.compile(
    r"(?ix)"
    r"\b("
    r"del|erase|rm\b|rmdir|rd\b|unlink|shred|sdelete|"
    r"remove-item|\bri\b|clear-recyclebin|empty-recycle|"
    r"format\s+[a-z]:|"
    r"rm\s+-rf|rm\s+-fr"
    r")\b"
)

_CLEAR_GOAL = re.compile(
    r"(?ix)"
    r"\b("
    r"clear|wipe|erase|empty|delete\s+all|select\s+all\s+and\s+delete|"
    r"ctrl\s*\+\s*a.*(?:delete|backspace|clear)|"
    r"remove\s+(?:all\s+)?(?:the\s+)?(?:text|notes?|content)|"
    r"backspace\s+(?:everything|all)"
    r")\b"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def project_root() -> Path:
    return ROOT.resolve()


def store_path() -> Path:
    p = settings.voxoryl_data_dir / "safety" / "owned_writes.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _empty_store() -> dict[str, Any]:
    return {"version": 1, "text_writes": [], "file_writes": []}


def _load() -> dict[str, Any]:
    path = store_path()
    if not path.exists():
        return _empty_store()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_store()
        data.setdefault("text_writes", [])
        data.setdefault("file_writes", [])
        return data
    except (json.JSONDecodeError, OSError):
        return _empty_store()


def _save(data: dict[str, Any]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Truncate oldest
    tw = list(data.get("text_writes") or [])[-_MAX_WRITES:]
    fw = list(data.get("file_writes") or [])[-_MAX_WRITES:]
    payload = {"version": 1, "text_writes": tw, "file_writes": fw, "updated_at": _now()}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()[:32]


def focus_context() -> dict[str, str]:
    """Best-effort foreground window + process (Windows)."""
    out = {"window": "", "process": "", "hwnd": ""}
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return out
        out["hwnd"] = str(int(hwnd))
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        out["window"] = (buf.value or "").strip()

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if handle:
            try:
                size = wintypes.DWORD(260)
                name_buf = ctypes.create_unicode_buffer(260)
                # QueryFullProcessImageNameW
                if kernel32.QueryFullProcessImageNameW(handle, 0, name_buf, ctypes.byref(size)):
                    out["process"] = Path(name_buf.value).name.lower()
            finally:
                kernel32.CloseHandle(handle)
    except Exception:
        pass
    return out


def is_under_project(path: str | Path) -> bool:
    try:
        target = Path(path).expanduser().resolve()
        root = project_root()
        return target == root or root in target.parents
    except Exception:
        return False


def refuse(kind: str = "unknown", *, detail: str = "") -> dict[str, Any]:
    speak = {
        "text": SPEAK_REFUSE_TEXT,
        "file": SPEAK_REFUSE_FILE,
        "shell": SPEAK_REFUSE_SHELL,
        "unknown": SPEAK_REFUSE_UNKNOWN,
    }.get(kind, SPEAK_REFUSE_UNKNOWN)
    return {
        "ok": False,
        "refused": True,
        "safety": True,
        "reason": kind,
        "detail": detail,
        "error": detail or speak,
        "speak": speak,
    }


def record_text_write(
    text: str,
    *,
    window: str = "",
    process: str = "",
    control: str = "",
    source: str = "type",
) -> dict[str, Any]:
    raw = text or ""
    if not raw.strip():
        return {}
    focus = focus_context()
    entry = {
        "id": content_hash(f"{_now()}:{raw[:64]}")[:12],
        "kind": "text",
        "at": _now(),
        "window": (window or focus.get("window") or "")[:240],
        "process": (process or focus.get("process") or "").lower()[:120],
        "control": (control or "")[:120],
        "content_hash": content_hash(raw),
        "snippet": raw[:120],
        "chars": len(raw),
        "source": source,
    }
    with _LOCK:
        data = _load()
        data["text_writes"].append(entry)
        _save(data)
    return entry


def record_file_write(path: str | Path, *, created: bool = True, source: str = "") -> dict[str, Any]:
    try:
        resolved = str(Path(path).expanduser().resolve())
    except Exception:
        resolved = str(path)
    entry = {
        "id": content_hash(f"{_now()}:{resolved}")[:12],
        "kind": "file",
        "at": _now(),
        "path": resolved,
        "created": bool(created),
        "under_project": is_under_project(resolved),
        "source": source,
    }
    with _LOCK:
        data = _load()
        data["file_writes"].append(entry)
        _save(data)
    return entry


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def find_owned_text(
    *,
    window: str = "",
    process: str = "",
    control: str = "",
    content: str = "",
) -> dict[str, Any] | None:
    """
    Fail-closed ownership lookup.
    Require a window-title match (or control id). Process-only is not enough —
    otherwise typing once in Notepad would unlock clearing the user's other notes.
    """
    focus = focus_context()
    win = _norm(window or focus.get("window") or "")
    proc = (process or focus.get("process") or "").lower()
    ctrl = (control or "").strip().lower()
    ch = content_hash(content) if content else ""
    with _LOCK:
        writes = list(reversed(_load().get("text_writes") or []))
    for w in writes:
        w_proc = str(w.get("process") or "").lower()
        w_win = _norm(str(w.get("window") or ""))
        w_ctrl = str(w.get("control") or "").strip().lower()
        win_ok = bool(win and w_win and (win == w_win or win[:48] == w_win[:48] or w_win[:48] in win or win[:48] in w_win))
        ctrl_ok = bool(ctrl and w_ctrl and ctrl == w_ctrl)
        if not (win_ok or ctrl_ok):
            continue
        # Optional process consistency when both known
        if proc and w_proc and proc != w_proc:
            continue
        if ch and w.get("content_hash") == ch:
            return w
        if not ch:
            return w
    return None


def owns_focused_text(*, content: str = "") -> bool:
    return find_owned_text(content=content) is not None


def may_clear_or_overwrite_focused_text(*, goal: str = "") -> dict[str, Any]:
    """
    Allow clearing/overwriting only when Voxoryl owns text in the focused window/process.
    Fail-closed when ownership is unknown.
    """
    owned = find_owned_text()
    if owned:
        return {"ok": True, "owned": owned, "goal": goal}
    return refuse("text", detail="No owned write for the focused window/field.")


def may_delete_path(path: str | Path) -> dict[str, Any]:
    if is_under_project(path):
        return {"ok": True, "path": str(path), "under_project": True}
    return refuse("file", detail=f"Path not under Voxoryl project: {path}")


def safe_unlink(path: str | Path) -> dict[str, Any]:
    gate = may_delete_path(path)
    if not gate.get("ok"):
        return gate
    p = Path(path)
    try:
        if p.is_dir():
            return refuse("file", detail="Directory delete blocked — use an explicit project cleanup path.")
        if not p.exists():
            return {"ok": True, "missing": True, "path": str(p)}
        p.unlink()
        return {"ok": True, "deleted": str(p.resolve())}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "speak": f"Delete failed: {exc}"}


def _keys_set(keys: list[Any] | None) -> set[str]:
    return {str(k).lower().strip() for k in (keys or []) if str(k).strip()}


def classify_keyboard_step(step: dict[str, Any]) -> str | None:
    """
    Return a hazard tag if this single step looks destructive, else None.
    Tags: select_all | delete_key | backspace | clear_combo | file_delete_hotkey
    """
    action = str(step.get("action") or "").lower().strip()
    if action == "hotkey":
        keys = _keys_set(step.get("keys"))
        if keys & {"delete", "backspace"} and keys & {"shift", "ctrl", "alt", "win"}:
            if "shift" in keys and "delete" in keys:
                return "file_delete_hotkey"
            if keys >= {"ctrl", "a"} and keys & {"delete", "backspace"}:
                return "clear_combo"
        if keys == {"ctrl", "a"} or keys == {"a", "ctrl"}:
            return "select_all"
        if "delete" in keys and len(keys) == 1:
            return "delete_key"
    if action == "press":
        key = str(step.get("text") or step.get("key") or "").lower()
        if key in {"delete", "del"}:
            return "delete_key"
        if key in {"backspace", "bksp"}:
            return "backspace"
    if action == "type" and not str(step.get("text") or "").strip():
        # empty type after select-all is a clear
        return None
    return None


def analyze_destructive_steps(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Scan a plan for clear/overwrite patterns.
    Returns {destructive: bool, kind, needs_owned_text: bool, detail}
    """
    tags: list[str] = []
    backspace_count = 0
    saw_select_all = False
    for i, step in enumerate(steps or []):
        tag = classify_keyboard_step(step)
        action = str(step.get("action") or "").lower()
        if tag == "backspace":
            backspace_count += 1
            # amount field used by some planners
            backspace_count += max(0, int(step.get("amount") or 0))
        if tag:
            tags.append(tag)
        if tag == "select_all":
            saw_select_all = True
            # look ahead for delete / empty overwrite
            for nxt in steps[i + 1 : i + 4]:
                ntag = classify_keyboard_step(nxt)
                nact = str(nxt.get("action") or "").lower()
                ntext = str(nxt.get("text") or "")
                if ntag in {"delete_key", "backspace", "clear_combo"}:
                    return {
                        "destructive": True,
                        "kind": "clear",
                        "needs_owned_text": True,
                        "detail": "select-all then delete/backspace",
                    }
                if nact in {"type", "paste"} and not ntext.strip():
                    return {
                        "destructive": True,
                        "kind": "clear",
                        "needs_owned_text": True,
                        "detail": "select-all then empty type",
                    }
                if nact in {"type", "paste"} and ntext.strip():
                    return {
                        "destructive": True,
                        "kind": "overwrite",
                        "needs_owned_text": True,
                        "detail": "select-all then replace text",
                    }
        if tag == "clear_combo":
            return {
                "destructive": True,
                "kind": "clear",
                "needs_owned_text": True,
                "detail": "ctrl+a+delete style hotkey",
            }
        if tag == "file_delete_hotkey":
            return {
                "destructive": True,
                "kind": "file_hotkey",
                "needs_owned_text": False,
                "detail": "shift+delete file hotkey blocked",
                "refuse": "file",
            }
        # Explorer-style delete without ownership context → refuse
        if tag == "delete_key" and not saw_select_all:
            # Single delete key alone is ambiguous (could be form). Gate only with clear goal elsewhere.
            pass

    if backspace_count >= 8:
        return {
            "destructive": True,
            "kind": "clear",
            "needs_owned_text": True,
            "detail": f"backspace flood ({backspace_count})",
        }
    return {"destructive": False, "tags": tags, "backspace_count": backspace_count}


def gate_keyboard_steps(
    steps: list[dict[str, Any]],
    *,
    goal: str = "",
) -> dict[str, Any]:
    """
    Fail-closed gate for screen/keyboard plans.
    Allows overwrite/clear only when focused text is owned by Voxoryl.
    """
    analysis = analyze_destructive_steps(steps)
    if analysis.get("refuse") == "file":
        return refuse("file", detail=str(analysis.get("detail") or ""))
    if not analysis.get("destructive"):
        # Goal-level clear without explicit steps still blocked at tool_screen
        return {"ok": True, "analysis": analysis}
    if analysis.get("needs_owned_text"):
        owned_gate = may_clear_or_overwrite_focused_text(goal=goal)
        if not owned_gate.get("ok"):
            return owned_gate
        return {"ok": True, "analysis": analysis, "owned": owned_gate.get("owned")}
    return refuse("unknown", detail=str(analysis.get("detail") or "destructive keyboard"))


def goal_requests_text_destruction(goal: str) -> bool:
    return bool(_CLEAR_GOAL.search(goal or ""))


def gate_text_destruction_goal(goal: str) -> dict[str, Any]:
    """Pre-flight for goals like 'clear notepad' before planning clicks."""
    if not goal_requests_text_destruction(goal):
        return {"ok": True}
    return may_clear_or_overwrite_focused_text(goal=goal)


def shell_is_destructive(command: str) -> bool:
    return bool(_SHELL_DESTRUCTIVE.search(command or ""))


def gate_shell_command(command: str) -> dict[str, Any]:
    """
    Block destructive shell unless every path-like token is under project
    (fail-closed if paths cannot be proven safe).
    """
    cmd = command or ""
    if not shell_is_destructive(cmd):
        return {"ok": True}
    # Extract quoted paths and bare Windows/posix paths
    tokens = re.findall(r'"([^"]+)"|\'([^\']+)\'|((?:[A-Za-z]:\\|\\\\|/)[^\s&|;]+)', cmd)
    paths: list[str] = []
    for a, b, c in tokens:
        p = a or b or c
        if p and not p.startswith("-"):
            paths.append(p)
    if not paths:
        return refuse("shell", detail="Destructive shell with unknown targets.")
    for p in paths:
        if not is_under_project(p):
            return refuse("shell", detail=f"Destructive shell targets outside project: {p}")
    return {"ok": True, "paths": paths}


def gate_hotkey(keys: list[str] | tuple[str, ...] | None, *, goal: str = "") -> dict[str, Any]:
    step = {"action": "hotkey", "keys": list(keys or [])}
    analysis = analyze_destructive_steps([step])
    if analysis.get("refuse") == "file":
        return refuse("file", detail=str(analysis.get("detail") or ""))
    if analysis.get("destructive") and analysis.get("needs_owned_text"):
        return may_clear_or_overwrite_focused_text(goal=goal)
    # ctrl+a alone is not enough to refuse — wait for follow-up
    return {"ok": True}


def status() -> dict[str, Any]:
    with _LOCK:
        data = _load()
    return {
        "ok": True,
        "project_root": str(project_root()),
        "store": str(store_path()),
        "text_writes": len(data.get("text_writes") or []),
        "file_writes": len(data.get("file_writes") or []),
        "policy": {
            "text": "clear/overwrite only owned writes for focused window/process",
            "files": "delete only under Voxoryl project root",
            "fail_closed": True,
        },
        "speak": "Safety policy on — I won't destroy text or files I don't own.",
    }
