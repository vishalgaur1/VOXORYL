"""Voxoryl Doctor — subsystem health for support + Safe Mode entry."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from voxoryl.config import settings


async def run_doctor() -> dict[str, Any]:
    checks: dict[str, Any] = {}

    try:
        from voxoryl.paths import user_data_dir, user_env_path

        ud = user_data_dir()
        checks["user_data"] = {
            "ok": ud.exists(),
            "path": str(ud.resolve()),
            "config_env": str(user_env_path().resolve()),
            "config_env_exists": user_env_path().exists(),
            "note": "Personal data lives here — deleting the install/repo does not wipe it.",
        }
    except Exception as exc:
        checks["user_data"] = {"ok": False, "error": str(exc)}

    checks["data_dir"] = {
        "ok": settings.voxoryl_data_dir.exists(),
        "path": str(settings.voxoryl_data_dir.resolve()),
    }

    try:
        from voxoryl.llm import ollama_available, list_local_models

        checks["ollama"] = {"ok": await ollama_available(), "models": await list_local_models()}
    except Exception as exc:
        checks["ollama"] = {"ok": False, "error": str(exc)}

    try:
        from voxoryl.hardware import detect_hardware

        hw = detect_hardware()
        checks["hardware"] = {"ok": True, **hw}
    except Exception as exc:
        checks["hardware"] = {"ok": False, "error": str(exc)}

    try:
        from voxoryl.model_planner import plan_models

        plan = plan_models()
        checks["model_plan"] = {
            "ok": True,
            "tier": plan.get("tier"),
            "hardware_score": plan.get("hardware_score"),
            "chat_model": plan.get("chat_model"),
            "fast_model": plan.get("fast_model"),
            "vision_model": plan.get("vision_model"),
            "embed_model": plan.get("embed_model"),
            "pulls_needed": plan.get("pulls_needed") or [],
            "reason": plan.get("reason"),
            "missing": plan.get("pulls_needed") or [],
        }
    except Exception as exc:
        checks["model_plan"] = {"ok": False, "error": str(exc)}

    try:
        from voxoryl.transcribe import asr_status

        checks["asr"] = asr_status()
    except Exception as exc:
        checks["asr"] = {"ok": False, "error": str(exc)}

    checks["webview2_hint"] = {
        "ok": True,
        "note": "Native widget needs WebView2 Evergreen Runtime on Windows",
    }

    try:
        import playwright  # noqa: F401

        checks["playwright"] = {"ok": True, "installed": True}
    except Exception:
        checks["playwright"] = {"ok": False, "installed": False, "note": "optional until P0-C browser path"}

    try:
        from voxoryl.events_db import status as db_status

        checks["database"] = db_status()
    except Exception as exc:
        checks["database"] = {"ok": False, "error": str(exc)}

    try:
        from voxoryl.supervisor import get_supervisor

        checks["supervisor"] = get_supervisor().health()
    except Exception as exc:
        checks["supervisor"] = {"ok": False, "error": str(exc)}

    try:
        from voxoryl.audio_runtime import get_audio_runtime

        checks["audio_runtime"] = get_audio_runtime().status()
    except Exception as exc:
        checks["audio"] = {"ok": False, "error": str(exc)}

    try:
        from voxoryl.perception import get_perception

        checks["perception"] = get_perception().snapshot()
    except Exception as exc:
        checks["perception"] = {"ok": False, "error": str(exc)}

    import sys

    chrome_ok = bool(shutil.which("chrome") or shutil.which("google-chrome") or shutil.which("chromium"))
    if sys.platform.startswith("win"):
        chrome_ok = chrome_ok or Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe").exists()
    elif sys.platform == "darwin":
        chrome_ok = chrome_ok or Path("/Applications/Google Chrome.app").exists()
    checks["chrome"] = {"ok": chrome_ok, "platform": sys.platform}
    checks["desktop_cu"] = {
        "ok": True,
        "windows_uia": sys.platform.startswith("win"),
        "note": (
            "Full Win32/UIA computer-use is Windows-first; macOS runs the API/widget with limited desktop automation."
            if not sys.platform.startswith("win")
            else "Win32/UIA computer-use available when COMPUTER_USE_ENABLED=true"
        ),
    }

    # Soft-optional: ollama / playwright / silero may be down without blocking core runtime
    soft_optional = {"ollama", "playwright", "model_plan"}
    failed = [k for k, v in checks.items() if isinstance(v, dict) and v.get("ok") is False]
    hard_failed = [k for k in failed if k not in soft_optional]
    soft_failed = [k for k in failed if k in soft_optional]
    return {
        "ok": not hard_failed,
        "failed": hard_failed,
        "soft_failed": soft_failed,
        "checks": checks,
        "safe_mode": _safe_mode_status(),
        "notes": "Ollama/Playwright soft-fail OK for offline path; enable for full capability.",
    }


def _safe_mode_status() -> bool:
    try:
        from voxoryl.state_store import get_state

        return get_state().safe_mode
    except Exception:
        return False


def set_safe_mode(on: bool) -> dict[str, Any]:
    from voxoryl.state_store import get_state

    get_state().set_safe_mode(on)
    return {"ok": True, "safe_mode": on}
