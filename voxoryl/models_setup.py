from __future__ import annotations

"""
Clone-time model install + hardware-aware upgrade/downgrade advisor.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from voxoryl.hardware import detect_hardware

ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = ROOT / "setup" / "models.json"


def load_catalog(path: Path | None = None) -> dict[str, Any]:
    p = path or CATALOG_PATH
    if not p.exists():
        return {"default_bundle": {"chat": "qwen3.5:4b"}, "tiers": [], "pull_on_clone": ["qwen3.5:4b"]}
    return json.loads(p.read_text(encoding="utf-8"))


def _ollama_bin() -> str | None:
    return shutil.which("ollama")


def ollama_installed_models() -> list[str]:
    bin_path = _ollama_bin()
    if not bin_path:
        return []
    try:
        r = subprocess.run([bin_path, "list"], capture_output=True, text=True, timeout=30, check=False)
        lines = (r.stdout or "").splitlines()[1:]  # skip header
        names = []
        for line in lines:
            parts = line.split()
            if parts:
                names.append(parts[0])
        return names
    except Exception:
        return []


def _model_present(name: str, installed: list[str] | None = None) -> bool:
    installed = installed if installed is not None else ollama_installed_models()
    return any(name == m or m.startswith(name) or name.startswith(m.split(":")[0]) for m in installed)


def pull_model(name: str, *, timeout: int = 3600) -> dict[str, Any]:
    bin_path = _ollama_bin()
    if not bin_path:
        return {"ok": False, "model": name, "error": "ollama not on PATH — install from https://ollama.com/download"}
    installed = ollama_installed_models()
    if any(name == m or m.startswith(name) or name.startswith(m.split(":")[0]) for m in installed):
        # still pull to ensure tag exists; ollama is fast if present
        pass
    try:
        r = subprocess.run(
            [bin_path, "pull", name],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        ok = r.returncode == 0
        return {
            "ok": ok,
            "model": name,
            "stdout": (r.stdout or "")[-500:],
            "stderr": (r.stderr or "")[-500:],
            "error": None if ok else (r.stderr or r.stdout or "pull failed")[-300:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "model": name, "error": "pull timed out"}
    except Exception as exc:
        return {"ok": False, "model": name, "error": str(exc)}


def _env_model(key: str, fallback: str | None = None) -> str | None:
    env_path = ROOT / ".env"
    if env_path.exists():
        m = re.search(rf"(?m)^{re.escape(key)}=(.+)$", env_path.read_text(encoding="utf-8"))
        if m:
            val = m.group(1).strip().strip('"').strip("'")
            if val:
                return val
    return os.getenv(key) or fallback


def required_launch_models() -> list[str]:
    """Models Voxoryl expects at product launch (chat + fast + embed; vision optional)."""
    catalog = load_catalog()
    default = catalog.get("default_bundle") or {}
    chat = _env_model("VOXORYL_MAIN_MODEL", "") or _env_model(
        "OLLAMA_MODEL", str(default.get("chat") or "qwen3.5:4b")
    )
    fast = _env_model("VOXORYL_FAST_MODEL", str(default.get("fast") or "qwen2.5:0.5b"))
    embed = _env_model("OLLAMA_EMBED_MODEL", str(default.get("embed") or "nomic-embed-text"))
    names = [n for n in (chat, fast, embed) if n and n.strip()]
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _pull_background(name: str) -> dict[str, Any]:
    """Start ollama pull without waiting for full download (launch must not hang)."""
    bin_path = _ollama_bin()
    if not bin_path:
        return {"ok": False, "model": name, "error": "ollama not on PATH", "started": False}
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            [bin_path, "pull", name],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        return {"ok": True, "model": name, "started": True, "pid": proc.pid}
    except OSError as exc:
        return {"ok": False, "model": name, "error": str(exc), "started": False}


def _warm_model(name: str, *, timeout: float = 8.0) -> dict[str, Any]:
    """Best-effort load into Ollama (may use VRAM). Never blocks longer than timeout."""
    import json
    import urllib.error
    import urllib.request

    base = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    if not base.startswith("http"):
        base = f"http://{base}"
    payload = json.dumps(
        {
            "model": name,
            "prompt": "ok",
            "stream": False,
            "keep_alive": "10m",
            "options": {"num_predict": 1},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return {"ok": 200 <= resp.status < 300, "model": name, "warmed": True}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "model": name, "warmed": False, "error": str(exc)[:200]}


def ensure_models_ready(*, wait_seconds: float = 12.0, warm: bool = True) -> dict[str, Any]:
    """
    Product launch path: confirm required models exist; do not re-download if present.
    Missing models kick off a background pull and we wait briefly, then continue.
    Optional short warm of the chat model (VRAM may stay loaded in Ollama).
    """
    import time

    wanted = required_launch_models()
    installed = ollama_installed_models()
    present = [m for m in wanted if _model_present(m, installed)]
    missing = [m for m in wanted if m not in present]
    pulls_started: list[dict[str, Any]] = []
    for name in missing:
        pulls_started.append(_pull_background(name))

    deadline = time.time() + max(0.0, wait_seconds)
    while missing and time.time() < deadline:
        installed = ollama_installed_models()
        missing = [m for m in wanted if not _model_present(m, installed)]
        if not missing:
            break
        time.sleep(0.5)

    installed = ollama_installed_models()
    present = [m for m in wanted if _model_present(m, installed)]
    still_missing = [m for m in wanted if m not in present]

    warm_results: list[dict[str, Any]] = []
    # Prefer warming the tiny fast model first (snappy "hi"); then main if still present.
    catalog = load_catalog()
    fast = _env_model(
        "VOXORYL_FAST_MODEL",
        str((catalog.get("default_bundle") or {}).get("fast") or "qwen2.5:0.5b"),
    )
    chat = wanted[0] if wanted else None
    warm_order = []
    if fast and fast in present:
        warm_order.append(fast)
    if chat and chat in present and chat not in warm_order:
        warm_order.append(chat)
    if warm:
        budget = min(8.0, max(3.0, wait_seconds))
        per = budget / max(1, len(warm_order)) if warm_order else budget
        for name in warm_order[:2]:
            warm_results.append(_warm_model(name, timeout=per))

    return {
        "ok": not still_missing,
        "wanted": wanted,
        "present": present,
        "missing": still_missing,
        "pulls_started": pulls_started,
        "warm": warm_results[0] if warm_results else None,
        "warm_all": warm_results,
        "note": (
            "Models ready"
            if not still_missing
            else "Continuing launch; missing models are pulling in the background"
        ),
    }


def _pick_tier(hw: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    vram = float(hw.get("best_vram_gb") or 0)
    ram = float(hw.get("ram_gb") or 0)
    if vram <= 0:
        fb = catalog.get("cpu_only_fallback") or {}
        return {
            "id": "cpu_only",
            "label": "CPU-only",
            "chat": fb.get("chat") or "qwen2.5:1.5b",
            "vision": fb.get("vision"),
            "embed": fb.get("embed") or "nomic-embed-text",
            "reason": fb.get("reason") or "No GPU VRAM detected.",
            "alts": [],
        }

    tiers = catalog.get("tiers") or []
    # Prefer tiers whose VRAM window matches; break ties with RAM window
    matches = []
    for t in tiers:
        vmin = float(t.get("min_vram_gb") or 0)
        vmax = float(t.get("max_vram_gb") or 999)
        if vmin <= vram <= vmax:
            matches.append(t)
    if not matches:
        # nearest by vram mid
        matches = sorted(
            tiers,
            key=lambda t: abs(((float(t.get("min_vram_gb") or 0) + float(t.get("max_vram_gb") or 0)) / 2) - vram),
        )[:1]

    # Among VRAM matches, prefer RAM fit
    def ram_fit(t: dict[str, Any]) -> float:
        rmin = float(t.get("min_ram_gb") or 0)
        rmax = float(t.get("max_ram_gb") or 999)
        if rmin <= ram <= rmax:
            return 0.0
        return min(abs(ram - rmin), abs(ram - rmax))

    matches = sorted(matches, key=ram_fit)
    return matches[0]


def recommend_models(hw: dict[str, Any] | None = None, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    catalog = catalog or load_catalog()
    hw = hw or detect_hardware()
    default = catalog.get("default_bundle") or {}
    current_chat = os.getenv("OLLAMA_MODEL") or default.get("chat") or "qwen3.5:4b"
    # Prefer .env if present
    env_path = ROOT / ".env"
    if env_path.exists():
        m = re.search(r"(?m)^OLLAMA_MODEL=(.+)$", env_path.read_text(encoding="utf-8"))
        if m:
            current_chat = m.group(1).strip().strip('"').strip("'")

    tier = _pick_tier(hw, catalog)
    suggested_chat = tier.get("chat")
    suggested_vision = tier.get("vision")
    suggested_embed = tier.get("embed") or default.get("embed") or "nomic-embed-text"

    action = "keep"
    if suggested_chat and suggested_chat != current_chat:
        # crude size compare via known tier order
        order = [t.get("chat") for t in (catalog.get("tiers") or [])]
        try:
            cur_i = order.index(current_chat) if current_chat in order else None
            sug_i = order.index(suggested_chat) if suggested_chat in order else None
            if cur_i is not None and sug_i is not None:
                action = "upgrade" if sug_i > cur_i else "downgrade"
            else:
                # fallback: compare tier id vs balanced
                action = "switch"
        except ValueError:
            action = "switch"

    return {
        "ok": True,
        "hardware": hw,
        "current": {
            "chat": current_chat,
            "vision": os.getenv("OLLAMA_VISION_MODEL") or default.get("vision"),
            "embed": os.getenv("OLLAMA_EMBED_MODEL") or default.get("embed"),
        },
        "recommended": {
            "tier": tier.get("id"),
            "label": tier.get("label"),
            "chat": suggested_chat,
            "vision": suggested_vision,
            "embed": suggested_embed,
            "alts": tier.get("alts") or [],
            "reason": tier.get("reason"),
        },
        "action": action,
        "message": _human_message(action, current_chat, suggested_chat, tier, hw),
        "installed": ollama_installed_models(),
    }


def _human_message(action: str, current: str, suggested: str | None, tier: dict[str, Any], hw: dict[str, Any]) -> str:
    vram = hw.get("best_vram_gb")
    ram = hw.get("ram_gb")
    specs = f"~{vram}GB VRAM, ~{ram}GB RAM" if vram or ram else "detected hardware"
    if action == "keep":
        return f"Your current model `{current}` already matches this PC ({specs}) — {tier.get('label')}."
    if action == "upgrade":
        return f"This PC ({specs}) can run a stronger brain: switch `{current}` → `{suggested}` ({tier.get('label')})."
    if action == "downgrade":
        return f"This PC ({specs}) will be happier on a lighter model: switch `{current}` → `{suggested}` ({tier.get('label')})."
    return f"Suggested model for {specs}: `{suggested}` ({tier.get('label')}). Currently using `{current}`."


def _patch_env(updates: dict[str, str], env_path: Path | None = None) -> dict[str, Any]:
    path = env_path or (ROOT / ".env")
    if not path.exists():
        example = ROOT / ".env.example"
        if example.exists():
            path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            path.write_text("", encoding="utf-8")
    text = path.read_text(encoding="utf-8")
    for key, value in updates.items():
        if not value:
            continue
        pattern = re.compile(rf"(?m)^{re.escape(key)}=.*$")
        line = f"{key}={value}"
        if pattern.search(text):
            text = pattern.sub(line, text)
        else:
            text = text.rstrip() + "\n" + line + "\n"
    path.write_text(text, encoding="utf-8")
    return {"ok": True, "path": str(path.resolve()), "updates": updates}


def apply_recommendation(*, pull: bool = True, use_recommended: bool = True) -> dict[str, Any]:
    rec = recommend_models()
    suggested = rec.get("recommended") or {}
    updates = {}
    if use_recommended:
        if suggested.get("chat"):
            updates["OLLAMA_MODEL"] = str(suggested["chat"])
        if suggested.get("vision"):
            updates["OLLAMA_VISION_MODEL"] = str(suggested["vision"])
        elif suggested.get("vision") is None:
            # leave vision as-is if tier says null
            pass
        if suggested.get("embed"):
            updates["OLLAMA_EMBED_MODEL"] = str(suggested["embed"])
    env_result = _patch_env(updates) if updates else {"ok": True, "updates": {}}

    pulls = []
    if pull:
        for key in ("chat", "embed", "vision"):
            name = suggested.get(key)
            if name:
                pulls.append(pull_model(str(name)))

    report = {
        "ok": True,
        "recommendation": rec,
        "env": env_result,
        "pulls": pulls,
    }
    _save_report(report)
    return report


def install_default_models(*, also_recommend: bool = True) -> dict[str, Any]:
    """
    Called on clone/install: pull the default bundle, then advise upgrade/downgrade.
    """
    catalog = load_catalog()
    hw = detect_hardware()
    to_pull: list[str] = list(catalog.get("pull_on_clone") or [])
    # Conditional extras by VRAM
    cond = catalog.get("pull_on_clone_if_vram_gb_gte") or {}
    vram = float(hw.get("best_vram_gb") or 0)
    for threshold, models in cond.items():
        try:
            if vram >= float(threshold):
                to_pull.extend(models)
        except (TypeError, ValueError):
            continue
    # de-dupe preserve order
    seen = set()
    ordered = []
    for m in to_pull:
        if m not in seen:
            seen.add(m)
            ordered.append(m)

    pulls = [pull_model(m) for m in ordered]
    advice = recommend_models(hw=hw, catalog=catalog) if also_recommend else None

    # Ensure .env has default chat if missing
    default = catalog.get("default_bundle") or {}
    env_path = ROOT / ".env"
    if not env_path.exists() and (ROOT / ".env.example").exists():
        env_path.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
    if env_path.exists() and default.get("chat"):
        text = env_path.read_text(encoding="utf-8")
        if "OLLAMA_MODEL=" not in text:
            _patch_env({"OLLAMA_MODEL": str(default["chat"])})

    report = {
        "ok": all(p.get("ok") for p in pulls) if pulls else True,
        "hardware": hw,
        "pulled": pulls,
        "recommendation": advice,
        "next_step": (
            "Models ready. "
            + (
                advice.get("message")
                if advice
                else "Run: python -m voxoryl.models_setup --recommend"
            )
        ),
    }
    _save_report(report)
    return report


def _save_report(report: dict[str, Any]) -> Path:
    data_dir = ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "hardware_profile.json"
    # strip huge stdout
    slim = json.loads(json.dumps(report, default=str))
    for p in slim.get("pulled") or slim.get("pulls") or []:
        if isinstance(p, dict):
            p.pop("stdout", None)
            p.pop("stderr", None)
    path.write_text(json.dumps(slim, indent=2), encoding="utf-8")
    return path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Voxoryl model install + hardware advisor")
    parser.add_argument("--install", action="store_true", help="Pull default clone models")
    parser.add_argument("--recommend", action="store_true", help="Print hardware recommendation")
    parser.add_argument("--apply", action="store_true", help="Write recommended models into .env and pull them")
    parser.add_argument("--no-pull", action="store_true", help="With --apply, only update .env")
    args = parser.parse_args()

    if args.apply:
        print(json.dumps(apply_recommendation(pull=not args.no_pull), indent=2))
    elif args.install:
        print(json.dumps(install_default_models(), indent=2))
    else:
        # default: recommend (and install if nothing installed)
        installed = ollama_installed_models()
        if not installed:
            print(json.dumps(install_default_models(), indent=2))
        else:
            print(json.dumps(recommend_models(), indent=2))
