"""
Desktop session lifecycle helpers for Voxoryl.

Ollama policy:
  - If Ollama is already reachable when Voxoryl starts, leave it running on exit.
  - If Voxoryl starts Ollama, stop that Ollama process on desktop session exit.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def _user_runtime_dirs() -> tuple[Path, Path, Path]:
    """Logs/session live in OS user-data, not the install folder."""
    try:
        from voxoryl.paths import ensure_user_dirs, user_data_subdir

        ensure_user_dirs()
        data = user_data_subdir()
    except Exception:
        data = ROOT / "data"
    runtime = data / "runtime"
    logs = data / "logs"
    return runtime, logs, runtime / "session.json"


RUNTIME_DIR, LOG_DIR, SESSION_PATH = _user_runtime_dirs()
CANDIDATE_PORTS = (3848, 3847, 3849)  # prefer 3848 when 3847 is Cursor / busy


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs() -> None:
    global RUNTIME_DIR, LOG_DIR, SESSION_PATH
    RUNTIME_DIR, LOG_DIR, SESSION_PATH = _user_runtime_dirs()
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def log_dir() -> Path:
    ensure_dirs()
    return LOG_DIR


def session_path() -> Path:
    ensure_dirs()
    return SESSION_PATH


def runtime_dir() -> Path:
    ensure_dirs()
    return RUNTIME_DIR


def python_exe(*, windowed: bool = False) -> Path:
    scripts = ROOT / ".venv" / "Scripts"
    if windowed:
        pyw = scripts / "pythonw.exe"
        if pyw.exists():
            return pyw
    py = scripts / "python.exe"
    if py.exists():
        return py
    return Path(os.environ.get("PYTHON", "python"))


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((host, port)) == 0


def probe_status(port: int, timeout: float = 5.0) -> dict[str, Any] | None:
    """Cold-start /api/status can exceed 2s while Ollama/models wake up."""
    url = f"http://127.0.0.1:{port}/api/status"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            if isinstance(data, dict) and data.get("ok"):
                return data
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    return None


def is_local_data_dir(data_dir: Any) -> bool:
    s = str(data_dir or "")
    if not s:
        return False
    low = s.lower().replace("\\", "/")
    if "voxoryl" in low:
        return True
    if "application support" in low or "/.local/share/" in low:
        return True
    if s.startswith("/workspace"):
        return False
    return bool((len(s) >= 2 and s[1] == ":") or "\\" in s)



def ollama_reachable(timeout: float = 1.5) -> bool:
    base = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    if not base.startswith("http"):
        base = f"http://{base}"
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _ollama_bin() -> str | None:
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    if local.exists():
        return str(local)
    try:
        wh = subprocess.run(
            ["where", "ollama"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=CREATE_NO_WINDOW,
        )
        if wh.returncode == 0:
            line = (wh.stdout or "").splitlines()[0].strip()
            if line:
                return line
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def ensure_ollama() -> dict[str, Any]:
    """Return {ok, already_running, started_by_us, pid?}."""
    if ollama_reachable():
        return {"ok": True, "already_running": True, "started_by_us": False, "pid": None}

    exe = _ollama_bin()
    if not exe:
        return {
            "ok": False,
            "already_running": False,
            "started_by_us": False,
            "pid": None,
            "error": "Ollama not found. Install from https://ollama.com/download",
        }

    ensure_dirs()
    log_path = LOG_DIR / "ollama.log"
    log_f = open(log_path, "a", encoding="utf-8", errors="replace")
    log_f.write(f"\n--- ollama serve {_now()} ---\n")
    log_f.flush()
    try:
        proc = subprocess.Popen(
            [exe, "serve"],
            cwd=str(ROOT),
            stdout=log_f,
            stderr=subprocess.STDOUT,
            creationflags=CREATE_NO_WINDOW,
        )
    except OSError as exc:
        log_f.close()
        return {
            "ok": False,
            "already_running": False,
            "started_by_us": False,
            "pid": None,
            "error": str(exc),
        }

    for _ in range(40):
        if ollama_reachable():
            return {
                "ok": True,
                "already_running": False,
                "started_by_us": True,
                "pid": proc.pid,
                "log": str(log_path),
            }
        if proc.poll() is not None:
            break
        time.sleep(0.25)

    return {
        "ok": ollama_reachable(),
        "already_running": False,
        "started_by_us": True,
        "pid": proc.pid,
        "error": None if ollama_reachable() else "Ollama did not become ready in time",
        "log": str(log_path),
    }


def choose_port() -> tuple[str, int]:
    """
    Prefer reusing a healthy desktop session; otherwise first free local port.
    Skips healthy non-desktop servers (e.g. developer `python run.py`).
    """
    for port in CANDIDATE_PORTS:
        status = probe_status(port)
        if status and is_local_data_dir(status.get("data_dir")):
            if status.get("desktop_session"):
                return ("reuse", port)
            continue
        if not port_in_use(port):
            return ("start", port)
    # Last resort: reuse any healthy local Voxoryl
    for port in CANDIDATE_PORTS:
        status = probe_status(port)
        if status and is_local_data_dir(status.get("data_dir")):
            return ("reuse", port)
    return ("start", CANDIDATE_PORTS[1])


def read_session() -> dict[str, Any] | None:
    ensure_dirs()
    if not SESSION_PATH.exists():
        return None
    try:
        data = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def write_session(data: dict[str, Any]) -> Path:
    ensure_dirs()
    payload = {**data, "updated_at": _now()}
    SESSION_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return SESSION_PATH


def clear_session() -> None:
    ensure_dirs()
    try:
        if SESSION_PATH.exists():
            SESSION_PATH.unlink()
    except OSError:
        pass


def _pid_alive(pid: int | None) -> bool:
    if not pid or int(pid) <= 0:
        return False
    pid = int(pid)
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            try:
                out = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    creationflags=CREATE_NO_WINDOW,
                )
                return str(pid) in (out.stdout or "")
            except (OSError, subprocess.SubprocessError):
                return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def terminate_pid(pid: int | None, *, name: str = "process") -> bool:
    if not pid or not _pid_alive(pid):
        return False
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=15,
                creationflags=CREATE_NO_WINDOW,
            )
        else:
            os.kill(pid, 15)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def request_shutdown(port: int, timeout: float = 3.0) -> bool:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/shutdown",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def stop_session(
    session: dict[str, Any] | None = None,
    *,
    stop_ollama: bool | None = None,
) -> dict[str, Any]:
    """Stop Voxoryl-owned processes from a session dict / session.json.

    Ollama policy (default): stop Ollama only if this session started it.
    Pass stop_ollama=True to also try stopping Ollama when a pid is known
    (aggressive; may affect other apps using Ollama).
    """
    session = session or read_session() or {}
    port = int(session.get("port") or 0)
    server_pid = session.get("server_pid")
    widget_pid = session.get("widget_pid")
    ollama_pid = session.get("ollama_pid")
    started_ollama = bool(session.get("ollama_started_by_us"))
    force_ollama = bool(stop_ollama) if stop_ollama is not None else bool(
        session.get("stop_ollama_on_exit")
    )
    should_stop_ollama = started_ollama or force_ollama

    stopped: list[str] = []

    try:
        if port and probe_status(port, timeout=1.0):
            # Only soft-shutdown desktop sessions (API enforces this).
            if request_shutdown(port):
                stopped.append("api_shutdown")
                for _ in range(20):
                    if not port_in_use(port):
                        break
                    time.sleep(0.15)

        if terminate_pid(server_pid, name="server"):
            stopped.append(f"server:{server_pid}")
        if terminate_pid(widget_pid, name="widget"):
            stopped.append(f"widget:{widget_pid}")

        if should_stop_ollama and terminate_pid(ollama_pid, name="ollama"):
            stopped.append(f"ollama:{ollama_pid}")
    except OSError:
        # Windows/ctypes can surface "exception set" after process exit races.
        pass

    clear_session()
    ollama_stopped = any(s.startswith("ollama:") for s in stopped)
    return {
        "ok": True,
        "stopped": stopped,
        "ollama_left_running": not ollama_stopped,
        "port": port or None,
    }


def find_browser() -> Path | None:
    candidates = [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "Microsoft"
        / "Edge"
        / "Application"
        / "msedge.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Microsoft"
        / "Edge"
        / "Application"
        / "msedge.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "Google"
        / "Chrome"
        / "Application"
        / "chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def open_url_in_browser(url: str) -> dict[str, Any]:
    """Open a URL in the system browser (not the Edge/Chrome --app widget profile)."""
    url = str(url or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return {"ok": False, "error": "only http(s) URLs allowed"}
    browser = find_browser()
    try:
        if browser:
            creation = 0x00000008 if os.name == "nt" else 0  # DETACHED_PROCESS
            subprocess.Popen([str(browser), url], cwd=str(ROOT), creationflags=creation)
            return {"ok": True, "url": url, "browser": str(browser)}
        if os.name == "nt":
            os.startfile(url)  # type: ignore[attr-defined]
            return {"ok": True, "url": url, "browser": "startfile"}
        subprocess.Popen(["xdg-open", url])
        return {"ok": True, "url": url, "browser": "xdg-open"}
    except OSError as exc:
        return {"ok": False, "error": str(exc), "url": url}


def pin_voxoryl_windows() -> None:
    """Best-effort always-on-top for windows titled VOXORYL."""
    if os.name != "nt":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        HWND_TOPMOST = -1
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_SHOWWINDOW = 0x0040

        EnumWindows = user32.EnumWindows
        GetWindowTextW = user32.GetWindowTextW
        GetWindowTextLengthW = user32.GetWindowTextLengthW
        IsWindowVisible = user32.IsWindowVisible
        SetWindowPos = user32.SetWindowPos

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _enum(hwnd, _lparam):  # type: ignore[misc]
            if not IsWindowVisible(hwnd):
                return True
            length = GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            GetWindowTextW(hwnd, buf, length + 1)
            if "VOXORYL" in (buf.value or "").upper():
                SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
            return True

        EnumWindows(_enum, 0)
    except Exception:
        pass
