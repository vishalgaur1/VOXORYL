from __future__ import annotations

"""
Android lab — run an emulator image, install APKs, drive basic E2E checks.
Plumbing for jack-of-all-trades app testing (not a full Appium suite yet).
"""

import asyncio
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voxoryl.config import settings
from voxoryl.memory import memory


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lab_root() -> Path:
    p = settings.voxoryl_data_dir / "android_lab"
    p.mkdir(parents=True, exist_ok=True)
    (p / "apks").mkdir(parents=True, exist_ok=True)
    (p / "reports").mkdir(parents=True, exist_ok=True)
    return p


def _which(name: str) -> str | None:
    return shutil.which(name)


def _sdk_paths() -> dict[str, str | None]:
    home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or ""
    platform_tools = Path(home) / "platform-tools" if home else None
    emulator_dir = Path(home) / "emulator" if home else None
    adb = _which("adb")
    if not adb and platform_tools and (platform_tools / "adb.exe").exists():
        adb = str(platform_tools / "adb.exe")
    emu = _which("emulator")
    if not emu and emulator_dir and (emulator_dir / "emulator.exe").exists():
        emu = str(emulator_dir / "emulator.exe")
    return {
        "ANDROID_HOME": home or None,
        "adb": adb,
        "emulator": emu,
    }


async def _run(cmd: list[str], *, timeout: float = 60.0) -> dict[str, Any]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return {"ok": False, "error": "timeout", "cmd": cmd}
        out = (out_b or b"").decode("utf-8", errors="ignore")
        err = (err_b or b"").decode("utf-8", errors="ignore")
        return {
            "ok": proc.returncode == 0,
            "code": proc.returncode,
            "stdout": out.strip(),
            "stderr": err.strip(),
            "cmd": cmd,
        }
    except FileNotFoundError:
        return {"ok": False, "error": f"not found: {cmd[0]}", "cmd": cmd}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "cmd": cmd}


def status() -> dict[str, Any]:
    paths = _sdk_paths()
    ready = bool(paths.get("adb") and paths.get("emulator"))
    speak = (
        "Android lab ready (adb + emulator found)."
        if ready
        else "Android lab not ready — install Android SDK / platform-tools, set ANDROID_HOME, then retry."
    )
    return {
        "ok": True,
        "ready": ready,
        "paths": paths,
        "apk_inbox": str((_lab_root() / "apks").resolve()),
        "hint": (
            "Install Android Studio cmdline tools, create an AVD, drop APKs in data/android_lab/apks/, "
            "then: 'start android emulator' / 'test apk MyApp.apk'."
        ),
        "speak": speak,
    }


async def list_avds() -> dict[str, Any]:
    paths = _sdk_paths()
    emu = paths.get("emulator")
    if not emu:
        return {**status(), "ok": False, "avds": [], "speak": "No emulator binary — install Android SDK emulator."}
    r = await _run([emu, "-list-avds"])
    avds = [ln.strip() for ln in (r.get("stdout") or "").splitlines() if ln.strip()]
    return {
        "ok": bool(r.get("ok")),
        "avds": avds,
        "speak": f"AVDs: {', '.join(avds)}" if avds else "No AVDs yet — create one in Android Studio Device Manager.",
        "raw": r,
    }


async def list_devices() -> dict[str, Any]:
    paths = _sdk_paths()
    adb = paths.get("adb")
    if not adb:
        return {"ok": False, "devices": [], "speak": "adb missing — install platform-tools."}
    r = await _run([adb, "devices", "-l"])
    devices = []
    for ln in (r.get("stdout") or "").splitlines()[1:]:
        if not ln.strip():
            continue
        parts = ln.split()
        if len(parts) >= 2:
            devices.append({"serial": parts[0], "state": parts[1], "raw": ln})
    online = [d for d in devices if d.get("state") == "device"]
    return {
        "ok": True,
        "devices": devices,
        "online": online,
        "speak": f"{len(online)} device(s) online." if online else "No devices online — start an emulator first.",
        "raw": r,
    }


async def start_emulator(avd: str = "") -> dict[str, Any]:
    paths = _sdk_paths()
    emu = paths.get("emulator")
    if not emu:
        return {"ok": False, "speak": "emulator binary missing."}
    if not avd:
        listed = await list_avds()
        avds = listed.get("avds") or []
        if not avds:
            return listed
        avd = str(avds[0])
    # Non-blocking start
    try:
        subprocess.Popen(
            [emu, "-avd", avd, "-netdelay", "none", "-netspeed", "full"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc), "speak": f"Failed to launch AVD {avd}."}
    memory.remember_fact(f"Android emulator start requested: {avd}", tags=["android", "lab"])
    return {
        "ok": True,
        "avd": avd,
        "speak": f"Starting AVD '{avd}'. Wait ~30s, then say 'android devices' or 'test apk …'.",
    }


async def install_apk(apk_path: str = "") -> dict[str, Any]:
    paths = _sdk_paths()
    adb = paths.get("adb")
    if not adb:
        return {"ok": False, "speak": "adb missing."}
    path = Path(apk_path) if apk_path else None
    if not path or not path.exists():
        # pick newest in inbox
        apks = sorted((_lab_root() / "apks").glob("*.apk"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not apks:
            return {
                "ok": False,
                "speak": "Drop an .apk into data/android_lab/apks/ first.",
                "hint": str((_lab_root() / "apks").resolve()),
            }
        path = apks[0]
    devices = await list_devices()
    if not devices.get("online"):
        return {"ok": False, "speak": "No online device — start the emulator first.", "devices": devices}
    r = await _run([adb, "install", "-r", str(path)], timeout=180.0)
    ok = bool(r.get("ok")) and "Success" in ((r.get("stdout") or "") + (r.get("stderr") or ""))
    speak = f"Installed {path.name}." if ok else f"Install failed for {path.name}: {(r.get('stderr') or r.get('stdout') or '')[:160]}"
    return {"ok": ok, "apk": str(path), "raw": r, "speak": speak}


async def smoke_test(package: str = "", apk_path: str = "") -> dict[str, Any]:
    """
    Lightweight E2E smoke: ensure device up → install optional apk → monkey brief run → screenshot.
    """
    paths = _sdk_paths()
    adb = paths.get("adb")
    if not adb:
        st = status()
        return {**st, "ok": False}

    devices = await list_devices()
    if not devices.get("online"):
        return {"ok": False, "speak": "No emulator/device online. Say 'start android emulator' first.", "devices": devices}

    steps: list[dict[str, Any]] = []
    if apk_path or list((_lab_root() / "apks").glob("*.apk")):
        inst = await install_apk(apk_path)
        steps.append({"install": inst})
        if not inst.get("ok") and apk_path:
            return {"ok": False, "steps": steps, "speak": inst.get("speak")}

    pkg = package.strip()
    if not pkg:
        # try to read package from pm list recently installed — optional
        pkg = ""

    monkey = None
    if pkg:
        monkey = await _run(
            [adb, "shell", "monkey", "-p", pkg, "-v", "50"],
            timeout=90.0,
        )
        steps.append({"monkey": monkey})
    else:
        # generic light monkey on launcher
        monkey = await _run([adb, "shell", "monkey", "-v", "20"], timeout=60.0)
        steps.append({"monkey_generic": monkey})

    shot_path = _lab_root() / "reports" / f"android-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.png"
    shot = await _run([adb, "exec-out", "screencap", "-p"], timeout=30.0)
    # exec-out returns binary — use adb shell screencap to file instead
    remote = "/sdcard/voxoryl_smoke.png"
    await _run([adb, "shell", "screencap", "-p", remote])
    pull = await _run([adb, "pull", remote, str(shot_path)])
    steps.append({"screenshot": {"path": str(shot_path) if pull.get("ok") else None, "pull": pull}})

    report = {
        "at": _now(),
        "package": pkg or None,
        "steps": steps,
        "screenshot": str(shot_path) if shot_path.exists() else None,
    }
    report_path = _lab_root() / "reports" / f"report-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    memory.remember_fact(f"Android smoke test saved: {report_path.name}", tags=["android", "e2e"])

    ok = all(
        (s.get("install", {}).get("ok", True) if "install" in s else True)
        for s in steps
    )
    speak = (
        f"Android smoke done. Report: {report_path.name}."
        + (f" Shot: {shot_path.name}." if shot_path.exists() else "")
        + (" Pass package name next time for targeted monkey." if not pkg else "")
    )
    return {"ok": True, "report": str(report_path), "screenshot": report.get("screenshot"), "steps": steps, "speak": speak}


async def tool_android(
    action: str = "status",
    message: str = "",
    avd: str = "",
    apk: str = "",
    package: str = "",
) -> dict[str, Any]:
    act = (action or "status").lower()
    lower = (message or "").lower()

    if act in {"status", "ready"} or "android status" in lower:
        return status()
    if act in {"avds", "list_avds"} or "list avd" in lower:
        return await list_avds()
    if act in {"devices"} or "android devices" in lower:
        return await list_devices()
    if act in {"start", "boot"} or "start android" in lower or "start emulator" in lower:
        # extract avd name after "avd"
        name = avd
        m = re.search(r"avd\s+([A-Za-z0-9_\-.]+)", message or "", re.I)
        if m:
            name = m.group(1)
        return await start_emulator(name)
    if act in {"install"} or "install apk" in lower:
        path = apk
        m = re.search(r"([\w\-.]+\.apk)", message or "", re.I)
        if m and not path:
            cand = _lab_root() / "apks" / m.group(1)
            path = str(cand if cand.exists() else m.group(1))
        return await install_apk(path)
    if act in {"test", "smoke", "e2e"} or any(k in lower for k in ("test apk", "e2e", "smoke test android", "test my app")):
        pkg = package
        m = re.search(r"package\s+([\w.]+)", message or "", re.I)
        if m:
            pkg = m.group(1)
        apk_path = apk
        m2 = re.search(r"([\w\-.]+\.apk)", message or "", re.I)
        if m2:
            cand = _lab_root() / "apks" / m2.group(1)
            apk_path = str(cand if cand.exists() else m2.group(1))
        return await smoke_test(package=pkg, apk_path=apk_path)

    return status()
