"""
Voxoryl desktop product launcher (silent by default on Windows).

One entrypoint: Ollama (if needed) → hidden API → voice widget ASAP
(boot screen covers model warm-up). Closing the widget stops the Voxoryl API.
Ollama is stopped only if this launcher started it (unless --stop-ollama).

  Silent (Desktop icon / VBS) — native pywebview window when available:
    scripts\\launch-voxoryl.vbs
    .\\.venv\\Scripts\\pythonw.exe scripts\\launch_voxoryl.py --native

  Force classic Edge --app shell:
    .\\.venv\\Scripts\\python.exe scripts\\launch_voxoryl.py --browser-widget

  Open web dashboard too:
    .\\.venv\\Scripts\\python.exe scripts\\launch_voxoryl.py --native --dashboard
    set VOXORYL_OPEN_DASHBOARD=1

  Developer:
    .\\.venv\\Scripts\\python.exe scripts\\launch_voxoryl.py --console
    python run.py

  Recreate Desktop icon (only Voxoryl.lnk — never Voxoryl (start)):
    .\\.venv\\Scripts\\python.exe scripts\\launch_voxoryl.py --shortcut
    powershell -ExecutionPolicy Bypass -File .\\scripts\\install-desktop-shortcut.ps1

Logs: data\\logs\\server.log (+ launcher.log, ollama.log)
State: data\\runtime\\session.json
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voxoryl.lifecycle import (  # noqa: E402
    CREATE_NO_WINDOW,
    choose_port,
    clear_session,
    ensure_dirs,
    ensure_ollama,
    find_browser,
    log_dir,
    open_url_in_browser,
    pin_voxoryl_windows,
    port_in_use,
    probe_status,
    python_exe,
    read_session,
    session_path,
    stop_session,
    terminate_pid,
    write_session,
)

ICON_PATH = ROOT / "voxoryl" / "static" / "voxoryl.ico"
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def _log(msg: str, *, console: bool) -> None:
    line = f"[voxoryl-launch] {msg}"
    ensure_dirs()
    from voxoryl import lifecycle as _lc

    with (_lc.log_dir() / "launcher.log").open("a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    if console:
        print(line, flush=True)


def ensure_icon(*, console: bool) -> None:
    if ICON_PATH.is_file():
        return
    maker = ROOT / "scripts" / "make_voxoryl_icon.py"
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    if maker.is_file() and py.is_file():
        subprocess.run([str(py), str(maker)], cwd=str(ROOT), check=False)
        _log("Generated voxoryl.ico", console=console)


def set_windows_app_user_model_id(app_id: str = "Voxoryl.Desktop") -> None:
    """Group taskbar under Voxoryl instead of pythonw.exe."""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)  # type: ignore[attr-defined]
    except Exception:
        pass


def apply_voxoryl_hwnd_icons(icon_path: Path) -> None:
    """Best-effort WM_SETICON on visible VOXORYL windows (title bar / Alt-Tab)."""
    if os.name != "nt" or not icon_path.is_file():
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        LR_DEFAULTSIZE = 0x0040
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1

        LoadImageW = user32.LoadImageW
        LoadImageW.argtypes = [
            wintypes.HINSTANCE,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        LoadImageW.restype = wintypes.HANDLE

        h_small = LoadImageW(None, str(icon_path), IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        h_big = LoadImageW(None, str(icon_path), IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        if not h_small and not h_big:
            h_small = LoadImageW(
                None, str(icon_path), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE
            )
            h_big = h_small
        if not h_small and not h_big:
            return

        EnumWindows = user32.EnumWindows
        GetWindowTextW = user32.GetWindowTextW
        GetWindowTextLengthW = user32.GetWindowTextLengthW
        IsWindowVisible = user32.IsWindowVisible
        SendMessageW = user32.SendMessageW
        GetWindowThreadProcessId = user32.GetWindowThreadProcessId
        our_pid = kernel32.GetCurrentProcessId()

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _enum(hwnd, _lparam):  # type: ignore[misc]
            if not IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) != int(our_pid):
                return True
            length = GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            GetWindowTextW(hwnd, buf, length + 1)
            if "VOXORYL" not in (buf.value or "").upper():
                return True
            if h_small:
                SendMessageW(hwnd, WM_SETICON, ICON_SMALL, h_small)
            if h_big:
                SendMessageW(hwnd, WM_SETICON, ICON_BIG, h_big)
            return True

        EnumWindows(_enum, 0)
    except Exception:
        pass


def _rgb_to_colorref(r: int, g: int, b: int) -> int:
    """Win32 COLORREF is 0x00BBGGRR."""
    return int(r) | (int(g) << 8) | (int(b) << 16)


def apply_voxoryl_light_caption(*, console: bool = False) -> None:
    """
    Match the native pywebview caption to the premium light widget UI.

    Pale icy caption (#f7f9fc) blends with the opaque off-white window.
    Windows 11: DWMWA_CAPTION_COLOR / TEXT_COLOR.
    Windows 10: disable immersive dark mode (best-effort).
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        dwmapi = ctypes.windll.dwmapi  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

        # DWM window attributes (Win10 1809+ / Win11)
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_CAPTION_COLOR = 35
        DWMWA_TEXT_COLOR = 36

        # Blend caption with opaque pale icy background
        caption = _rgb_to_colorref(0xF7, 0xF9, 0xFC)  # #f7f9fc
        text = _rgb_to_colorref(0x1A, 0x23, 0x32)  # --ink
        dark_mode_off = ctypes.c_int(0)
        caption_c = ctypes.c_int(caption)
        text_c = ctypes.c_int(text)

        EnumWindows = user32.EnumWindows
        GetWindowTextW = user32.GetWindowTextW
        GetWindowTextLengthW = user32.GetWindowTextLengthW
        IsWindowVisible = user32.IsWindowVisible
        GetWindowThreadProcessId = user32.GetWindowThreadProcessId
        DwmSetWindowAttribute = dwmapi.DwmSetWindowAttribute
        our_pid = kernel32.GetCurrentProcessId()
        painted = 0

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _enum(hwnd, _lparam):  # type: ignore[misc]
            nonlocal painted
            if not IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(pid.value) != int(our_pid):
                return True
            length = GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            GetWindowTextW(hwnd, buf, length + 1)
            if "VOXORYL" not in (buf.value or "").upper():
                return True
            # Prefer light chrome over dark title bar.
            DwmSetWindowAttribute(
                hwnd,
                DWMWA_USE_IMMERSIVE_DARK_MODE,
                ctypes.byref(dark_mode_off),
                ctypes.sizeof(dark_mode_off),
            )
            # Win11 caption/text colors (ignored / HRESULT fail on older builds).
            DwmSetWindowAttribute(
                hwnd,
                DWMWA_CAPTION_COLOR,
                ctypes.byref(caption_c),
                ctypes.sizeof(caption_c),
            )
            DwmSetWindowAttribute(
                hwnd,
                DWMWA_TEXT_COLOR,
                ctypes.byref(text_c),
                ctypes.sizeof(text_c),
            )
            painted += 1
            return True

        EnumWindows(_enum, 0)
        if painted and console:
            _log(f"Light caption applied to {painted} VOXORYL window(s)", console=console)
    except Exception as exc:
        if console:
            _log(f"Light caption skipped: {exc}", console=console)


def get_desktop() -> Path:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as key:
                val, _ = winreg.QueryValueEx(key, "Desktop")
                p = Path(os.path.expandvars(val))
                if p.is_dir():
                    return p
        except OSError:
            pass
    return Path.home() / "Desktop"


def desktop_dirs() -> list[Path]:
    """All likely Desktop folders (OneDrive + local) for duplicate cleanup."""
    candidates = [
        get_desktop(),
        Path.home() / "Desktop",
        Path(os.environ.get("OneDrive", "") or "") / "Desktop",
        Path(os.environ.get("USERPROFILE", "") or "") / "OneDrive" / "Desktop",
        Path.home() / "OneDrive" / "Desktop",
    ]
    seen: set[str] = set()
    out: list[Path] = []
    for p in candidates:
        try:
            if not p or not str(p).strip() or not p.is_dir():
                continue
            key = str(p.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
        except OSError:
            continue
    return out


def purge_duplicate_voxoryl_shortcuts(*, console: bool) -> list[str]:
    """Remove Voxoryl (start).lnk and any other Voxoryl*.lnk except Voxoryl.lnk."""
    removed: list[str] = []
    for desktop in desktop_dirs():
        try:
            for lnk in desktop.glob("Voxoryl*.lnk"):
                if lnk.name.lower() == "voxoryl.lnk":
                    continue
                try:
                    lnk.unlink(missing_ok=True)
                    removed.append(str(lnk))
                except OSError:
                    pass
            for name in ("Voxoryl (start).lnk", "Voxoryl start.lnk", "Start Voxoryl.lnk"):
                legacy = desktop / name
                if legacy.is_file():
                    try:
                        legacy.unlink()
                        removed.append(str(legacy))
                    except OSError:
                        pass
        except OSError:
            continue
    if removed:
        _log(f"Removed duplicate shortcuts: {removed}", console=console)
    return removed


def create_desktop_shortcut(*, console: bool) -> Path:
    """Point Desktop Voxoryl.lnk at the silent VBS entry (product feel + orb icon)."""
    desktop = get_desktop()
    desktop.mkdir(parents=True, exist_ok=True)
    vbs = ROOT / "scripts" / "launch-voxoryl.vbs"
    lnk_path = desktop / "Voxoryl.lnk"
    ensure_icon(console=console)
    purge_duplicate_voxoryl_shortcuts(console=console)

    def _ps(s: str) -> str:
        return s.replace("'", "''")

    ps = f"""
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut('{_ps(str(lnk_path))}')
$lnk.TargetPath = 'wscript.exe'
$lnk.Arguments = '//nologo "{_ps(str(vbs))}"'
$lnk.WorkingDirectory = '{_ps(str(ROOT))}'
$lnk.WindowStyle = 7
$lnk.Description = 'Voxoryl — Ollama + API + native voice widget (closes cleanly)'
if (Test-Path '{_ps(str(ICON_PATH))}') {{ $lnk.IconLocation = '{_ps(str(ICON_PATH))},0' }}
$lnk.Save()
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        check=False,
        creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    purge_duplicate_voxoryl_shortcuts(console=console)
    _log(f"Desktop shortcut -> {lnk_path} -> {vbs}", console=console)
    return lnk_path


def _start_server(port: int, *, console: bool) -> tuple[subprocess.Popen | None, Path]:
    ensure_dirs()
    log_path = log_dir() / "server.log"
    env = os.environ.copy()
    env["VOXORYL_PORT"] = str(port)
    env["VOXORYL_HOST"] = "127.0.0.1"
    env["VOXORYL_ALLOW_MOCK"] = "0"
    env["VOXORYL_DESKTOP_SESSION"] = "1"
    env.setdefault("PYTHONUTF8", "1")

    py = python_exe(windowed=True)
    cmd = [str(py), str(ROOT / "run.py")]

    if console:
        py = python_exe(windowed=False)
        cmd = [str(py), str(ROOT / "run.py")]
        _log(f"Starting server on :{port} (console / foreground)", console=True)
        subprocess.call(cmd, cwd=str(ROOT), env=env)
        return None, log_path

    log_f = log_path.open("a", encoding="utf-8", errors="replace")
    log_f.write(f"\n--- launch {time.strftime('%Y-%m-%d %H:%M:%S')} port={port} py={py} ---\n")
    log_f.flush()
    flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    startup = None
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0  # SW_HIDE
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
        startupinfo=startup,
    )
    _log(f"Started server pid={proc.pid} on :{port} -> {log_path}", console=False)
    return proc, log_path


def _wait_ready(
    port: int, *, server_proc: subprocess.Popen | None, console: bool, timeout: float = 45.0
) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server_proc is not None and server_proc.poll() is not None:
            raise RuntimeError(
                f"Voxoryl server exited early (code {server_proc.returncode}). See data/logs/server.log"
            )
        status = probe_status(port, timeout=5.0)
        if status and status.get("ok"):
            return status
        time.sleep(0.35)
    raise TimeoutError(f"Voxoryl did not become ready on :{port}. See data/logs/server.log")


def _widget_url(port: int, *, native: bool = False) -> str:
    # _r= busts Edge --app / WebView document cache so UI redesigns actually appear.
    flags = "desktop=1"
    if native:
        flags += "&native=1"
    return f"http://127.0.0.1:{port}/widget?{flags}&_r={int(time.time())}"


def _webview_available() -> bool:
    try:
        import webview  # noqa: F401

        return True
    except ImportError:
        return False


def _open_widget_edge(port: int, *, console: bool = False) -> subprocess.Popen:
    """
    Legacy shell: Chromium/Edge --app mode around the same /widget page.

    Note: Edge/Chrome --app has no supported CLI for a custom .ico; the window /
    taskbar icon usually comes from the page favicon (or the browser itself).
    Prefer --native (pywebview) for a real Voxoryl title-bar / taskbar icon.
    """
    browser = find_browser()
    if not browser:
        raise RuntimeError("Install Microsoft Edge or Google Chrome for the desktop widget.")
    profile = Path(os.environ.get("LOCALAPPDATA", str(ROOT / "data"))) / "VoxorylWidgetProfile"
    profile.mkdir(parents=True, exist_ok=True)
    url = _widget_url(port, native=False)
    # No --icon / --app-icon flag on stable Edge/Chrome; favicon in widget.html is the lever.
    _log(
        "Edge --app widget: custom .ico not supported by browser CLI; using page favicon",
        console=console,
    )
    args = [
        str(browser),
        f"--user-data-dir={profile}",
        f"--app={url}",
        "--disable-http-cache",
        "--window-size=460,780",
        "--window-position=40,40",
    ]
    kwargs: dict = {"cwd": str(ROOT)}
    if os.name == "nt":
        kwargs["creationflags"] = DETACHED_PROCESS
    return subprocess.Popen(args, **kwargs)


def _run_native_widget(port: int, *, console: bool) -> bool:
    """
    Open /widget in a real desktop window via pywebview (WinForms + WebView2).
    Blocks until the window closes. Returns False if native shell is unavailable
    so the caller can fall back to Edge --app.
    """
    try:
        import webview
    except ImportError:
        _log("pywebview not installed — falling back to Edge --app widget", console=console)
        return False

    url = _widget_url(port, native=True)
    ensure_icon(console=console)
    set_windows_app_user_model_id()
    icon = str(ICON_PATH) if ICON_PATH.is_file() else None
    try:
        # pywebview 6.x: icon is on start(), not create_window. Without it, WinForms
        # extracts the icon from sys.executable (pythonw.exe → generic Python snakes).
        webview.create_window(
            title="VOXORYL",
            url=url,
            width=460,
            height=780,
            x=40,
            y=40,
            min_size=(360, 520),
            background_color="#f7f9fc",
            text_select=True,
        )
    except Exception as exc:
        _log(f"Native window create failed ({exc}) — falling back to Edge --app", console=console)
        return False

    def _pin_soon() -> None:
        # Caption + icon need the HWND; retry briefly while WebView2 settles.
        for delay in (0.35, 0.9, 1.8):
            time.sleep(delay)
            try:
                apply_voxoryl_light_caption(console=console)
                if icon:
                    apply_voxoryl_hwnd_icons(ICON_PATH)
                pin_voxoryl_windows()
            except Exception as exc:
                _log(f"pin/caption skipped: {exc}", console=console)

    threading.Thread(target=_pin_soon, name="voxoryl-pin", daemon=True).start()
    _log(f"Native widget (pywebview) url={url} icon={icon or 'default'}", console=console)
    try:
        # Windows: Edge WebView2 (no full browser chrome / "Microsoft Edge" footer).
        start_kwargs: dict = {}
        if icon:
            start_kwargs["icon"] = icon
        if os.name == "nt":
            webview.start(gui="edgechromium", **start_kwargs)
        else:
            webview.start(**start_kwargs)
    except Exception as exc:
        _log(f"Native widget start failed ({exc}) — falling back to Edge --app", console=console)
        return False
    return True


def _open_widget(port: int) -> subprocess.Popen:
    """Back-compat alias: browser --app widget."""
    return _open_widget_edge(port)


def _run_widget(
    port: int,
    *,
    console: bool,
    native: bool,
    browser_widget: bool,
) -> tuple[str, subprocess.Popen | None]:
    """
    Show the voice widget. Prefer pywebview when native=True (product default via VBS).
    Returns (kind, edge_proc_or_none). kind is 'native' or 'edge'.
    For native, blocks until close and returns (native, None).
    For edge, returns the Popen for the caller to wait on.
    """
    want_native = bool(native) and not bool(browser_widget)
    if want_native:
        if _run_native_widget(port, console=console):
            return "native", None
        _log("Using Edge --app fallback for widget", console=console)
    proc = _open_widget_edge(port, console=console)
    return "edge", proc


def _open_dashboard(port: int, *, console: bool) -> None:
    url = f"http://127.0.0.1:{port}/"
    result = open_url_in_browser(url)
    _log(f"Web dashboard: {result}", console=console)


def _ensure_models(*, console: bool) -> None:
    try:
        from voxoryl.models_setup import ensure_models_ready

        info = ensure_models_ready(wait_seconds=12.0, warm=True)
        _log(
            f"Models: present={info.get('present')} missing={info.get('missing')} "
            f"warm={bool((info.get('warm') or {}).get('ok'))} — {info.get('note')}",
            console=console,
        )
    except Exception as exc:
        _log(f"Model readiness skipped: {exc}", console=console)


def _prepare_port() -> tuple[str, int, dict | None]:
    prev = read_session()
    if prev and prev.get("desktop_session") and prev.get("port"):
        port = int(prev["port"])
        status = probe_status(port)
        if status and status.get("desktop_session"):
            terminate_pid(prev.get("widget_pid"))
            return "reuse", port, status
        stop_session(prev)

    mode, port = choose_port()
    status = probe_status(port) if mode == "reuse" else None
    return mode, port, status


def run_product(
    *,
    console: bool = False,
    no_widget: bool = False,
    no_ollama: bool = False,
    smoke: bool = False,
    open_dashboard: bool = False,
    stop_ollama: bool = False,
    native: bool = False,
    browser_widget: bool = False,
) -> int:
    ensure_dirs()
    ensure_icon(console=console)
    set_windows_app_user_model_id()

    ollama: dict = {"ok": True, "started_by_us": False, "pid": None, "already_running": True}
    models_thread: threading.Thread | None = None
    if not no_ollama:
        ollama = ensure_ollama()
        if ollama.get("ok"):
            _log(
                "Ollama ready"
                + (" (started by Voxoryl)" if ollama.get("started_by_us") else " (already running)"),
                console=console,
            )
            # Warm models in the background so the widget can open ASAP with a boot screen.
            if not smoke:
                models_thread = threading.Thread(
                    target=_ensure_models,
                    kwargs={"console": console},
                    name="voxoryl-models",
                    daemon=True,
                )
                models_thread.start()
        else:
            _log(str(ollama.get("error") or "Ollama required"), console=True)
            return 1

    mode, port, existing = _prepare_port()
    server_proc: subprocess.Popen | None = None
    log_path = log_dir() / "server.log"
    started_server = False
    own_for_shutdown = False
    server_pid: int | None = None

    if mode == "reuse" and existing and existing.get("desktop_session"):
        own_for_shutdown = True
        server_pid = int(existing.get("pid") or 0) or None
        _log(f"Reusing desktop session on :{port}", console=console)
    elif mode == "reuse" and existing and not existing.get("desktop_session"):
        if smoke:
            _log(f"Foreign server on :{port} — starting owned smoke instance", console=console)
        elif no_widget:
            _log(f"Voxoryl already running at http://127.0.0.1:{port}", console=console)
            if open_dashboard:
                _open_dashboard(port, console=console)
            return 0
        else:
            _log(
                f"Voxoryl already running on :{port} (developer session). Opening widget only; "
                "close will NOT stop that server.",
                console=console,
            )
            if open_dashboard:
                _open_dashboard(port, console=console)
            kind, widget = _run_widget(
                port, console=console, native=native, browser_widget=browser_widget
            )
            if kind == "edge" and widget is not None:
                time.sleep(1.2)
                pin_voxoryl_windows()
                if not console:
                    widget.wait()
            return 0
    else:
        if port_in_use(port) and not probe_status(port):
            for candidate in (3848, 3847, 3849):
                if not port_in_use(candidate):
                    port = candidate
                    break

        if console and no_widget:
            _start_server(port, console=True)
            return 0

        server_proc, log_path = _start_server(port, console=False)
        started_server = True
        own_for_shutdown = True
        try:
            status = _wait_ready(port, server_proc=server_proc, console=console)
            server_pid = int(status.get("pid") or 0) or (server_proc.pid if server_proc else None)
        except Exception as exc:
            _log(str(exc), console=True)
            if server_proc and server_proc.poll() is None:
                terminate_pid(server_proc.pid)
            return 1

    if smoke and not started_server:
        for candidate in (3849, 3848, 3847):
            if not port_in_use(candidate):
                port = candidate
                break
        server_proc, log_path = _start_server(port, console=False)
        started_server = True
        own_for_shutdown = True
        status = _wait_ready(port, server_proc=server_proc, console=console)
        server_pid = int(status.get("pid") or 0) or (server_proc.pid if server_proc else None)

    session = {
        "desktop_session": True,
        "port": port,
        "base_url": f"http://127.0.0.1:{port}",
        "server_pid": server_pid or (server_proc.pid if server_proc else None),
        "server_started_by_us": started_server,
        "ollama_started_by_us": bool(ollama.get("started_by_us")),
        "ollama_pid": ollama.get("pid"),
        "stop_ollama_on_exit": bool(stop_ollama),
        "log": str(log_path),
        "widget_pid": None,
        "widget_kind": None,
        "session_file": str(session_path()),
    }

    if smoke:
        write_session(session)
        ready = probe_status(port) or {}
        _log(
            f"SMOKE ok port={port} desktop_session={ready.get('desktop_session')} "
            f"pid={ready.get('pid')} log={log_path}",
            console=True,
        )
        try:
            result = stop_session(session, stop_ollama=stop_ollama)
        except OSError as exc:
            _log(f"SMOKE stop warning: {exc}", console=True)
            result = {"stopped": [], "ollama_left_running": True}
            terminate_pid(session.get("server_pid") or (server_proc.pid if server_proc else None))
        time.sleep(0.8)
        busy = port_in_use(port) and bool(probe_status(port, timeout=1.0))
        _log(
            f"SMOKE stopped={result.get('stopped')} ollama_left={result.get('ollama_left_running')} "
            f"port_busy={busy}",
            console=True,
        )
        return 0 if not busy else 2

    if no_widget:
        write_session(session)
        _log(f"Server ready at {session['base_url']} (no widget)", console=console)
        if open_dashboard:
            _open_dashboard(port, console=console)
        return 0

    write_session(session)
    if open_dashboard:
        _open_dashboard(port, console=console)

    try:
        create_desktop_shortcut(console=console)
    except Exception as exc:
        _log(f"Shortcut refresh skipped: {exc}", console=console)

    # Native blocks until close; Edge --app returns a Popen to wait on.
    if bool(native) and not bool(browser_widget):
        session["widget_kind"] = "native"
        session["widget_pid"] = None
        write_session(session)

    kind, widget = _run_widget(
        port, console=console, native=native, browser_widget=browser_widget
    )

    if kind == "edge" and widget is not None:
        session["widget_kind"] = "edge"
        session["widget_pid"] = widget.pid
        write_session(session)
        _log(f"Widget opened pid={widget.pid} url={session['base_url']}/widget", console=console)
        time.sleep(1.5)
        pin_voxoryl_windows()
        widget.wait()

    _log("Widget closed — shutting down Voxoryl session", console=console)

    if own_for_shutdown:
        latest = read_session() or session
        stop_session(
            {
                **latest,
                "server_pid": latest.get("server_pid") or session.get("server_pid"),
                "server_started_by_us": True,
                "ollama_started_by_us": session.get("ollama_started_by_us"),
                "ollama_pid": session.get("ollama_pid"),
                "stop_ollama_on_exit": session.get("stop_ollama_on_exit"),
                "port": latest.get("port") or port,
            },
            stop_ollama=stop_ollama,
        )
    else:
        clear_session()
    return 0


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Voxoryl product launcher")
    ap.add_argument(
        "--console",
        action="store_true",
        help="Show launcher messages in this terminal (API still can be silent unless --server-only).",
    )
    ap.add_argument("--no-widget", action="store_true", help="Start Ollama + API only")
    ap.add_argument("--server-only", action="store_true", help="Alias for --no-widget")
    ap.add_argument(
        "--native",
        action="store_true",
        help="Open widget in a native pywebview window (title VOXORYL; falls back to Edge --app)",
    )
    ap.add_argument(
        "--browser-widget",
        action="store_true",
        help="Force Edge/Chrome --app widget instead of native pywebview",
    )
    ap.add_argument(
        "--shortcut",
        action="store_true",
        help="Recreate Desktop Voxoryl.lnk only (never Voxoryl (start))",
    )
    ap.add_argument("--no-ollama", action="store_true", help="Do not try to start Ollama")
    ap.add_argument(
        "--dashboard",
        "--with-dashboard",
        action="store_true",
        dest="dashboard",
        help="Also open the web dashboard (/) in the system browser",
    )
    ap.add_argument(
        "--stop-ollama",
        action="store_true",
        help="On exit, stop Ollama even if it was already running (aggressive)",
    )
    ap.add_argument(
        "--smoke-test",
        action="store_true",
        help="Start hidden API, verify status, shut down (no widget)",
    )
    ap.add_argument("--stop", action="store_true", help="Stop desktop session from session.json")
    args = ap.parse_args(argv)

    console = bool(args.console) or bool(args.smoke_test) or bool(args.stop) or bool(args.shortcut)
    open_dashboard = bool(args.dashboard) or _env_flag("VOXORYL_OPEN_DASHBOARD")
    stop_ollama = bool(args.stop_ollama) or _env_flag("VOXORYL_STOP_OLLAMA")
    # Product default: native when asked via flag/env (Desktop VBS passes --native).
    native = bool(args.native) or _env_flag("VOXORYL_NATIVE_WIDGET")
    browser_widget = bool(args.browser_widget) or _env_flag("VOXORYL_BROWSER_WIDGET")

    if not (ROOT / ".venv" / "Scripts" / "python.exe").is_file():
        _log("Virtualenv missing. Run scripts\\install-windows.ps1 first.", console=True)
        return 1

    if not (ROOT / ".env").is_file() and (ROOT / ".env.example").is_file():
        (ROOT / ".env").write_bytes((ROOT / ".env.example").read_bytes())

    if args.shortcut:
        create_desktop_shortcut(console=True)
        return 0

    if args.stop:
        result = stop_session(stop_ollama=stop_ollama)
        _log(str(result), console=True)
        return 0

    try:
        return run_product(
            console=console,
            no_widget=bool(args.no_widget or args.server_only),
            no_ollama=bool(args.no_ollama),
            smoke=bool(args.smoke_test),
            open_dashboard=open_dashboard,
            stop_ollama=stop_ollama,
            native=native,
            browser_widget=browser_widget,
        )
    except Exception as exc:
        _log(f"Launch failed: {exc}", console=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
