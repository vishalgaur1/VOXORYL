from __future__ import annotations

"""
VPN / proxy profile helpers for Windows.
Ask before changing system network settings. Always reset when user says work is done.
Stores last profile in data/net_profile.json — NEVER leave proxy on after reset.
"""

import json
import re
import subprocess
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from voxoryl.config import settings
from voxoryl.memory import memory


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path():
    p = settings.voxoryl_data_dir / "net_profile.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_profile() -> dict[str, Any]:
    path = _path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {
        "proxy_enabled": False,
        "proxy_server": "",
        "bypass": "<local>",
        "vpn_note": "",
        "last_change_at": None,
        "pending_confirm": None,
        "history": [],
    }


def save_profile(data: dict[str, Any]) -> None:
    data["last_change_at"] = _now()
    _path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _run_ps(script: str, *, timeout: float = 15.0) -> dict[str, Any]:
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
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def read_windows_proxy() -> dict[str, Any]:
    ps = (
        "$p = Get-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings'; "
        "[PSCustomObject]@{ ProxyEnable=$p.ProxyEnable; ProxyServer=$p.ProxyServer; ProxyOverride=$p.ProxyOverride } "
        "| ConvertTo-Json -Compress"
    )
    result = _run_ps(ps)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error") or result.get("stderr"), "hint": "Need PowerShell registry read access."}
    try:
        data = json.loads(result.get("stdout") or "{}")
        return {"ok": True, "windows": data}
    except json.JSONDecodeError:
        return {"ok": False, "error": "bad proxy json", "raw": result.get("stdout")}


def _parse_proxy_server(message: str) -> str | None:
    # host:port or http://host:port
    m = re.search(r"(?:proxy|server)\s*[=:]\s*([^\s]+)", message or "", re.I)
    if m:
        return m.group(1).strip().strip('"')
    m2 = re.search(r"\b((?:\d{1,3}\.){3}\d{1,3}|[a-zA-Z0-9.-]+):(\d{2,5})\b", message or "")
    if m2:
        return f"{m2.group(1)}:{m2.group(2)}"
    urls = re.findall(r"https?://[^\s]+", message or "")
    if urls:
        u = urlparse(urls[0])
        if u.hostname:
            return f"{u.hostname}:{u.port or (443 if u.scheme == 'https' else 80)}"
    return None


def apply_proxy(server: str, *, bypass: str = "<local>", confirm: bool = False) -> dict[str, Any]:
    server = (server or "").strip()
    if not server:
        return {
            "ok": False,
            "speak": "Which proxy? Example: set proxy 127.0.0.1:8080 — I'll ask before applying.",
        }
    data = load_profile()
    if not confirm:
        data["pending_confirm"] = {"action": "set_proxy", "server": server, "bypass": bypass, "at": _now()}
        save_profile(data)
        return {
            "ok": True,
            "needs_confirm": True,
            "pending": data["pending_confirm"],
            "speak": (
                f"About to set Windows user proxy to {server}. "
                "This changes system network settings. Reply: confirm proxy — or cancel. "
                "Say work is done / reset network when finished so I clear it."
            ),
            "rule": "Ask before changing system network settings.",
        }
    # escape for PowerShell single-quoted string
    safe = server.replace("'", "''")
    bypass_safe = (bypass or "<local>").replace("'", "''")
    ps = (
        f"Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings' "
        f"-Name ProxyServer -Value '{safe}'; "
        f"Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings' "
        f"-Name ProxyEnable -Value 1; "
        f"Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings' "
        f"-Name ProxyOverride -Value '{bypass_safe}'"
    )
    result = _run_ps(ps)
    if not result.get("ok"):
        return {
            "ok": False,
            "error": result.get("stderr") or result.get("error"),
            "hint": "Registry write failed — try an elevated user session or set proxy manually in Windows Settings.",
            "speak": "Couldn't apply the proxy. Check permissions or set it in Windows Settings → Network → Proxy.",
        }
    data["proxy_enabled"] = True
    data["proxy_server"] = server
    data["bypass"] = bypass
    data["pending_confirm"] = None
    data.setdefault("history", []).append({"at": _now(), "action": "set_proxy", "server": server})
    data["history"] = data["history"][-30:]
    save_profile(data)
    memory.remember_fact(f"proxy enabled: {server}", tags=["network", "proxy"])
    return {
        "ok": True,
        "proxy_server": server,
        "speak": f"Proxy on → {server}. When work is done, say reset network so we don't leave it on.",
    }


def reset_network(*, confirm: bool = True) -> dict[str, Any]:
    """Always clear user proxy. VPN apps are vendor-specific — document only."""
    ps = (
        "Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings' "
        "-Name ProxyEnable -Value 0; "
        "Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings' "
        "-Name ProxyServer -Value ''"
    )
    result = _run_ps(ps)
    data = load_profile()
    data["proxy_enabled"] = False
    data["proxy_server"] = ""
    data["pending_confirm"] = None
    data.setdefault("history", []).append({"at": _now(), "action": "reset"})
    data["history"] = data["history"][-30:]
    save_profile(data)
    # also try netsh winhttp reset (machine proxy) — best-effort
    try:
        subprocess.run(
            ["netsh", "winhttp", "reset", "proxy"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass
    if not result.get("ok"):
        return {
            "ok": False,
            "error": result.get("stderr") or result.get("error"),
            "speak": "Tried to clear proxy but PowerShell reported an error — check Windows Settings → Proxy.",
            "profile": data,
        }
    return {
        "ok": True,
        "speak": "Network reset — user proxy cleared. You're back to normal routing.",
        "profile": data,
        "vpn_note": (
            data.get("vpn_note")
            or "If a VPN app is still connected, disconnect it in that app (WireGuard/OpenVPN/etc.) — OS proxy is separate."
        ),
    }


def vpn_guidance(message: str = "") -> dict[str, Any]:
    return {
        "ok": True,
        "speak": (
            "I can set a Windows HTTP(S) proxy after you confirm. "
            "Full VPN tunnels need your VPN client (WireGuard, OpenVPN, corporate portal). "
            "Tell me: enable vpn with proxy host:port — then confirm proxy. "
            "When finished: work is done / reset network."
        ),
        "hint": "Never leave proxy enabled after work — always reset.",
        "message": message,
    }


async def tool_net_profile(
    action: str = "status",
    *,
    message: str = "",
    server: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    action = (action or "status").lower().strip()
    lower = (message or "").lower()
    data = load_profile()

    if action in {"confirm"} or "confirm proxy" in lower:
        pending = data.get("pending_confirm") or {}
        if pending.get("action") == "set_proxy" and pending.get("server"):
            return apply_proxy(str(pending["server"]), bypass=str(pending.get("bypass") or "<local>"), confirm=True)
        return {"ok": False, "speak": "Nothing pending to confirm. Say set proxy host:port first."}

    if action in {"cancel"} or "cancel proxy" in lower:
        data["pending_confirm"] = None
        save_profile(data)
        return {"ok": True, "speak": "Cancelled — no network change."}

    if action in {"reset", "done", "clear"} or any(
        k in lower
        for k in (
            "reset network",
            "work is done",
            "work's done",
            "disable proxy",
            "clear proxy",
            "normal network",
        )
    ):
        return reset_network(confirm=True)

    if action in {"set", "proxy", "enable"} or any(
        k in lower for k in ("set proxy", "enable proxy", "enable vpn", "use proxy")
    ):
        srv = server or _parse_proxy_server(message) or ""
        if "vpn" in lower and not srv:
            return vpn_guidance(message)
        return apply_proxy(srv, confirm=confirm or "confirm" in lower)

    # status
    win = read_windows_proxy()
    enabled = bool(data.get("proxy_enabled"))
    speak = (
        f"Proxy profile: {'ON → ' + str(data.get('proxy_server')) if enabled else 'OFF (normal)'}."
    )
    if win.get("ok"):
        w = win.get("windows") or {}
        speak += f" Windows reports ProxyEnable={w.get('ProxyEnable')}."
    if data.get("pending_confirm"):
        speak += " Pending confirm: " + str((data["pending_confirm"] or {}).get("server") or "")
    return {
        "ok": True,
        "profile": data,
        "windows": win,
        "speak": speak,
        "rule": "Ask before changing; always reset when work is done.",
    }
