#!/usr/bin/env python3
"""
Zero-setup onboarding for VOXORYL (Windows / macOS / Linux).

Creates a venv, installs deps, seeds OS user-data (not the git folder),
detects hardware + local LLMs, pulls a fitting Ollama model, then launches.

  python scripts/bootstrap_voxoryl.py
  python scripts/bootstrap_voxoryl.py --no-launch
  python scripts/bootstrap_voxoryl.py --native   # Windows pywebview when available
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _log(msg: str) -> None:
    print(f"[voxoryl-bootstrap] {msg}", flush=True)


def _check_python() -> None:
    if sys.version_info < (3, 11):
        _log(f"Python 3.11+ required (found {sys.version.split()[0]}).")
        _log("Install from https://www.python.org/downloads/ and re-run.")
        sys.exit(1)
    _log(f"Python {sys.version.split()[0]} OK")


def _venv_python() -> Path:
    if platform.system() == "Windows":
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def _ensure_venv() -> Path:
    py = _venv_python()
    if py.exists():
        _log(f"Using existing venv: {py}")
        return py
    _log("Creating .venv …")
    subprocess.check_call([sys.executable, "-m", "venv", str(ROOT / ".venv")], cwd=str(ROOT))
    if not py.exists():
        _log("venv created but python not found — check permissions.")
        sys.exit(1)
    return py


def _pip_install(py: Path) -> None:
    req = ROOT / "requirements.txt"
    _log("Upgrading pip …")
    subprocess.check_call([str(py), "-m", "pip", "install", "--upgrade", "pip"], cwd=str(ROOT))
    _log("Installing requirements.txt …")
    subprocess.check_call([str(py), "-m", "pip", "install", "-r", str(req)], cwd=str(ROOT))


def _run_module(py: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(py), "-m", *args],
        cwd=str(ROOT),
        text=True,
        capture_output=False,
        check=check,
    )


def _ollama_bin() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    if platform.system() == "Windows":
        local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
        if local.is_file():
            return str(local)
    return None


def _try_install_ollama() -> dict[str, Any]:
    """Best-effort winget/brew; never hang forever. Always print download link."""
    link = "https://ollama.com/download"
    system = platform.system()
    if _ollama_bin():
        return {"ok": True, "already": True}

    _log(f"Ollama not found. Install from {link}")
    if system == "Windows" and shutil.which("winget"):
        _log("Trying winget install Ollama.Ollama (may need approval) …")
        try:
            r = subprocess.run(
                ["winget", "install", "-e", "--id", "Ollama.Ollama", "--accept-package-agreements", "--accept-source-agreements"],
                timeout=180,
                check=False,
            )
            if r.returncode == 0 and _ollama_bin():
                return {"ok": True, "installed_via": "winget"}
        except (subprocess.TimeoutExpired, OSError) as exc:
            _log(f"winget skipped: {exc}")
    elif system == "Darwin" and shutil.which("brew"):
        _log("Trying brew install ollama …")
        try:
            r = subprocess.run(["brew", "install", "ollama"], timeout=300, check=False)
            if r.returncode == 0 and _ollama_bin():
                return {"ok": True, "installed_via": "brew"}
        except (subprocess.TimeoutExpired, OSError) as exc:
            _log(f"brew skipped: {exc}")

    if _ollama_bin():
        return {"ok": True, "already": True}
    return {"ok": False, "link": link, "error": "Install Ollama manually, then re-run bootstrap."}


def _ensure_ollama_running() -> bool:
    bin_path = _ollama_bin()
    if not bin_path:
        return False
    # Probe API
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as resp:
            if 200 <= resp.status < 300:
                return True
    except Exception:
        pass
    _log("Starting ollama serve …")
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if platform.system() == "Windows" else 0
        subprocess.Popen(
            [bin_path, "serve"],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except OSError as exc:
        _log(f"Could not start Ollama: {exc}")
        return False
    for _ in range(20):
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=1.5) as resp:
                if 200 <= resp.status < 300:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def _pull_with_progress(name: str, timeout: int = 3600) -> dict[str, Any]:
    bin_path = _ollama_bin()
    if not bin_path:
        return {"ok": False, "model": name, "error": "ollama missing"}
    _log(f"Pulling model `{name}` (this can take a while) …")
    try:
        proc = subprocess.Popen(
            [bin_path, "pull", name],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(ROOT),
        )
        assert proc.stdout is not None
        last = time.time()
        for line in proc.stdout:
            line = line.rstrip()
            if line and (time.time() - last > 2.0 or "success" in line.lower() or "pulling" in line.lower()):
                _log(line[:120])
                last = time.time()
        code = proc.wait(timeout=timeout)
        return {"ok": code == 0, "model": name, "error": None if code == 0 else f"exit {code}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "model": name, "error": "pull timed out"}
    except Exception as exc:
        return {"ok": False, "model": name, "error": str(exc)}


def bootstrap(*, launch: bool, native: bool, skip_models: bool, console: bool) -> int:
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    _check_python()
    py = _ensure_venv()
    _pip_install(py)

    # Re-exec under venv so imports resolve cleanly for the rest
    if Path(sys.executable).resolve() != py.resolve():
        _log("Re-launching bootstrap inside .venv …")
        cmd = [str(py), str(Path(__file__).resolve()), *sys.argv[1:]]
        return subprocess.call(cmd, cwd=str(ROOT))

    from voxoryl.bootstrap import ensure_private_layout
    from voxoryl.hardware import detect_hardware
    from voxoryl.model_planner import plan_models, plan_to_env_updates, save_plan
    from voxoryl.models_setup import _patch_env, ollama_installed_models
    from voxoryl.paths import ensure_user_dirs, migrate_from_repo_if_needed, user_data_dir, user_env_path

    _log("Migrating / ensuring OS user-data directory …")
    mig = migrate_from_repo_if_needed()
    dirs = ensure_user_dirs()
    _log(f"User data: {dirs['user_data_dir']}")
    if mig.get("migrated"):
        _log(f"Migrated from repo: {mig.get('copied')}")

    boot = ensure_private_layout()
    _log(f"Bootstrap created: {boot.get('created') or '(nothing new)'}")
    _log(f"Config: {user_env_path()}")

    hw = detect_hardware()
    score = hw.get("hardware_score") or {}
    _log(
        f"Hardware: RAM={hw.get('ram_gb')}GB VRAM={hw.get('best_vram_gb')}GB "
        f"→ tier `{score.get('tier')}` (score {score.get('score')})"
    )

    ollama = _try_install_ollama()
    if not ollama.get("ok"):
        _log(ollama.get("error") or "Ollama missing")
        _log("VOXORYL can still start; add Ollama for local models, or set a cloud key in config.env.")
    else:
        if not _ensure_ollama_running():
            _log("Ollama installed but API not reachable yet — continue anyway.")

    plan = plan_models(hw=hw, installed=ollama_installed_models())
    save_plan(plan)
    _log(f"Model plan: chat={plan.get('chat_model')} fast={plan.get('fast_model')} tier={plan.get('tier')}")
    _log(f"Reason: {plan.get('reason')}")

    updates = plan_to_env_updates(plan)
    env_result = _patch_env(updates)
    _log(f"Wrote model prefs → {env_result.get('path')} (API keys untouched)")

    pulls: list[dict[str, Any]] = []
    if not skip_models and ollama.get("ok"):
        for name in plan.get("pulls_needed") or []:
            # Never pull huge models on low tier
            if plan.get("tier") in ("lite", "cpu_only"):
                size_m = re.search(r"(\d+(?:\.\d+)?)b", str(name).lower())
                if size_m and float(size_m.group(1)) > 4:
                    _log(f"Skipping large model `{name}` on {plan.get('tier')} hardware")
                    continue
            pulls.append(_pull_with_progress(str(name)))
        for p in pulls:
            _log(f"pull {p.get('model')}: {'OK' if p.get('ok') else p.get('error')}")
    elif skip_models:
        _log("Skipping model pulls (--skip-models)")

    if not launch:
        _log("Done (--no-launch). Start with: python scripts/launch_voxoryl.py")
        _log(f"Deleting the repo does NOT delete {user_data_dir()}")
        return 0

    _log("Launching VOXORYL …")
    launch_script = ROOT / "scripts" / "launch_voxoryl.py"
    args = [str(py), str(launch_script)]
    if native and platform.system() == "Windows":
        args.append("--native")
    elif console:
        args.append("--console")
    else:
        # Prefer native on Windows; console elsewhere for visibility
        if platform.system() == "Windows":
            args.append("--native")
        else:
            args.append("--console")
    return subprocess.call(args, cwd=str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="VOXORYL zero-setup bootstrap")
    parser.add_argument("--no-launch", action="store_true", help="Install/configure only")
    parser.add_argument("--native", action="store_true", help="Windows native widget")
    parser.add_argument("--console", action="store_true", help="Developer console launch")
    parser.add_argument("--skip-models", action="store_true", help="Do not ollama pull")
    args = parser.parse_args()
    try:
        return bootstrap(
            launch=not args.no_launch,
            native=args.native,
            skip_models=args.skip_models,
            console=args.console,
        )
    except subprocess.CalledProcessError as exc:
        _log(f"Command failed: {exc}")
        return exc.returncode or 1
    except KeyboardInterrupt:
        _log("Interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
