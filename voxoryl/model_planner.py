"""
Hardware-aware model plan for VOXORYL.

Inputs: RAM, VRAM/GPU hint, OS, installed Ollama tags (and optional LM Studio paths).
Output: {chat_model, fast_model, vision_model?, embed_model?, tier, reason, …}

Prefers already-installed compatible models over pulling defaults.
Never suggests huge weights on low-RAM machines.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from voxoryl.hardware import detect_hardware, hardware_score
from voxoryl.models_setup import load_catalog, ollama_installed_models
from voxoryl.paths import user_data_subdir

# Approximate parameter-size (billions) from Ollama tag names for ranking.
_SIZE_RE = re.compile(
    r"(?:[:\-_]|^)(\d+(?:\.\d+)?)\s*b(?:illion)?(?:[:\-_]|$)|"
    r":(\d+(?:\.\d+)?)b\b",
    re.IGNORECASE,
)


def _parse_size_b(tag: str) -> float | None:
    t = (tag or "").strip().lower()
    if not t:
        return None
    # Known tiny / embed / vision hints
    if "embed" in t or "nomic" in t or "mxbai" in t:
        return 0.1
    m = _SIZE_RE.search(t.replace(" ", ""))
    if m:
        for g in m.groups():
            if g:
                try:
                    return float(g)
                except ValueError:
                    pass
    # Heuristic family defaults when tag has no size
    if any(x in t for x in ("0.5b", "500m")):
        return 0.5
    if "mini" in t or "tiny" in t:
        return 1.0
    if "phi4" in t and "mini" not in t:
        return 14.0
    if "phi3" in t or "phi-3" in t:
        return 3.8
    return None


def _max_chat_params(ram_gb: float, vram_gb: float) -> float:
    """Upper bound on chat model size (B params) that should fit this PC."""
    if vram_gb <= 0:
        if ram_gb < 8:
            return 0.5
        if ram_gb < 12:
            return 1.5
        if ram_gb < 16:
            return 3.0
        return 4.0
    if vram_gb < 4 or ram_gb < 10:
        return 1.5
    if vram_gb < 6 or ram_gb < 14:
        return 4.0
    if vram_gb < 10:
        return 9.0
    if vram_gb < 16:
        return 14.0
    return 32.0


def _is_vision(tag: str) -> bool:
    t = tag.lower()
    return any(x in t for x in ("vl", "vision", "llava", "moondream", "minicpm-v", "qwen2.5vl"))


def _is_embed(tag: str) -> bool:
    t = tag.lower()
    return any(x in t for x in ("embed", "nomic-embed", "mxbai-embed", "bge-"))


def _is_fast_candidate(tag: str) -> bool:
    size = _parse_size_b(tag)
    if size is not None and size <= 1.0:
        return True
    t = tag.lower()
    return any(x in t for x in ("0.5b", "1b", "1.5b", "tiny", "mini"))


def discover_lm_studio() -> dict[str, Any]:
    """Optional: detect LM Studio install / models folder (hint only — runtime is Ollama)."""
    home = Path.home()
    candidates: list[Path] = []
    if os.name == "nt":
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        candidates.extend(
            [
                local / "LM Studio",
                local / "Programs" / "LM Studio",
                home / ".cache" / "lm-studio" / "models",
                home / ".lmstudio" / "models",
            ]
        )
    elif platform_is_darwin():
        candidates.extend(
            [
                home / "Library" / "Application Support" / "LM Studio",
                home / ".cache" / "lm-studio" / "models",
                home / ".lmstudio" / "models",
            ]
        )
    else:
        candidates.extend(
            [
                home / ".cache" / "lm-studio" / "models",
                home / ".lmstudio" / "models",
            ]
        )
    found = [str(p) for p in candidates if p.exists()]
    return {
        "ok": bool(found),
        "paths": found,
        "note": "LM Studio models are not auto-loaded; install matching Ollama tags for VOXORYL.",
    }


def platform_is_darwin() -> bool:
    import platform

    return platform.system() == "Darwin"


def _tier_defaults(tier: str, catalog: dict[str, Any]) -> dict[str, Any]:
    if tier == "cpu_only":
        fb = catalog.get("cpu_only_fallback") or {}
        return {
            "chat": fb.get("chat") or "qwen2.5:0.5b",
            "fast": "qwen2.5:0.5b",
            "vision": None,
            "embed": fb.get("embed") or "nomic-embed-text",
            "reason": fb.get("reason") or "CPU-only / low resources — keep models tiny.",
        }
    for t in catalog.get("tiers") or []:
        if t.get("id") == tier:
            default = catalog.get("default_bundle") or {}
            return {
                "chat": t.get("chat") or default.get("chat"),
                "fast": default.get("fast") or "qwen2.5:0.5b",
                "vision": t.get("vision"),
                "embed": t.get("embed") or default.get("embed") or "nomic-embed-text",
                "reason": t.get("reason") or t.get("label") or tier,
                "alts": t.get("alts") or [],
            }
    # Map score tiers that don't match catalog ids
    alias = {"lite": "lite", "balanced": "balanced", "strong": "strong", "beast": "beast"}
    tid = alias.get(tier, "balanced")
    for t in catalog.get("tiers") or []:
        if t.get("id") == tid:
            default = catalog.get("default_bundle") or {}
            return {
                "chat": t.get("chat") or default.get("chat"),
                "fast": default.get("fast") or "qwen2.5:0.5b",
                "vision": t.get("vision"),
                "embed": t.get("embed") or default.get("embed") or "nomic-embed-text",
                "reason": t.get("reason") or tid,
                "alts": t.get("alts") or [],
            }
    default = catalog.get("default_bundle") or {}
    return {
        "chat": default.get("chat") or "qwen3.5:4b",
        "fast": default.get("fast") or "qwen2.5:0.5b",
        "vision": default.get("vision"),
        "embed": default.get("embed") or "nomic-embed-text",
        "reason": "catalog default",
        "alts": [],
    }


def _pick_best_installed(
    installed: list[str],
    *,
    role: str,
    max_params: float,
    preferred: str | None,
    alts: list[str],
) -> tuple[str | None, str]:
    """
    Prefer preferred/alts if installed and within budget; else strongest
    installed chat-like model that fits; else None (caller will pull preferred).
    """
    installed_l = list(installed)

    def present(name: str) -> str | None:
        for m in installed_l:
            if m == name or m.startswith(name) or name.startswith(m.split(":")[0] + ":"):
                return m
            # family match: qwen3.5:4b vs qwen3.5:latest
            if m.split(":")[0] == name.split(":")[0] and _parse_size_b(m) == _parse_size_b(name):
                return m
        return None

    # 1) Exact preferred / alts
    for cand in [preferred, *alts]:
        if not cand:
            continue
        hit = present(cand)
        if hit:
            size = _parse_size_b(hit) or _parse_size_b(cand) or 0
            if role == "chat" and size > max_params + 0.5:
                continue
            return hit, f"using installed `{hit}` (matches plan)"

    if role == "embed":
        for m in installed_l:
            if _is_embed(m):
                return m, f"using installed embed `{m}`"
        return None, "will pull embed default"

    if role == "vision":
        for m in installed_l:
            if _is_vision(m):
                size = _parse_size_b(m) or 3.0
                if size <= max_params + 2:
                    return m, f"using installed vision `{m}`"
        return None, "vision not installed (optional)"

    if role == "fast":
        # Prefer tiny installed
        tiny = []
        for m in installed_l:
            if _is_embed(m) or _is_vision(m):
                continue
            if _is_fast_candidate(m):
                tiny.append(m)
        if tiny:
            tiny.sort(key=lambda t: _parse_size_b(t) or 99)
            return tiny[0], f"using installed fast `{tiny[0]}`"
        return None, "will pull fast default"

    # chat: strongest installed within budget (prefer larger)
    candidates = []
    for m in installed_l:
        if _is_embed(m) or _is_vision(m):
            continue
        size = _parse_size_b(m)
        if size is None:
            # unknown size — allow if preferred family or small name
            if any(x in m.lower() for x in ("qwen", "phi", "gemma", "llama", "mistral")):
                size = 4.0  # conservative assume mid
            else:
                continue
        if size <= max_params + 0.25:
            candidates.append((size, m))
    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        best = candidates[0][1]
        # Prefer over pulling if at least as good as half of preferred size
        pref_size = _parse_size_b(preferred or "") or 0
        if pref_size and candidates[0][0] + 0.5 < pref_size * 0.5 and preferred:
            # Too weak vs recommended — still report but mark pull preferred
            return best, f"installed `{best}` is weaker than recommended `{preferred}`"
        return best, f"preferring stronger installed `{best}` within RAM/VRAM budget"

    return None, f"will pull recommended `{preferred}`"


def plan_models(
    *,
    hw: dict[str, Any] | None = None,
    installed: list[str] | None = None,
    catalog: dict[str, Any] | None = None,
    prefer_installed: bool = True,
) -> dict[str, Any]:
    """
    Build a model plan for this PC.

    Returns keys: chat_model, fast_model, vision_model, embed_model, tier,
    hardware_score, reason, pulls_needed, installed_used, lm_studio.
    """
    catalog = catalog or load_catalog()
    hw = hw or detect_hardware()
    score = hw.get("hardware_score") or hardware_score(hw)
    tier = str(score.get("tier") or "balanced")
    ram = float(hw.get("ram_gb") or score.get("ram_gb") or 0)
    vram = float(hw.get("best_vram_gb") or score.get("best_vram_gb") or 0)
    max_params = _max_chat_params(ram, vram)

    # On very low RAM, force lite / tiny defaults regardless of VRAM mis-detect
    if ram and ram < 8:
        tier = "lite" if vram > 0 else "cpu_only"
        max_params = min(max_params, 1.5 if ram >= 6 else 0.5)

    defaults = _tier_defaults(tier, catalog)
    # Cap default chat if catalog suggests something too large
    chat_default = str(defaults.get("chat") or "qwen2.5:1.5b")
    chat_size = _parse_size_b(chat_default) or 4.0
    if chat_size > max_params:
        # Downgrade along catalog tiers
        for fallback_tier in ("lite", "cpu_only"):
            fb = _tier_defaults(fallback_tier, catalog)
            cand = str(fb.get("chat") or "qwen2.5:0.5b")
            if (_parse_size_b(cand) or 0.5) <= max_params:
                chat_default = cand
                defaults = fb
                tier = fallback_tier
                break
        else:
            chat_default = "qwen2.5:0.5b" if max_params <= 0.5 else "qwen2.5:1.5b"
            defaults["chat"] = chat_default
            defaults["vision"] = None

    installed = list(installed if installed is not None else ollama_installed_models())
    reasons: list[str] = []

    chat_model = chat_default
    fast_model = str(defaults.get("fast") or "qwen2.5:0.5b")
    vision_model = defaults.get("vision")
    embed_model = str(defaults.get("embed") or "nomic-embed-text")
    installed_used: dict[str, str] = {}

    if prefer_installed and installed:
        alts = list(defaults.get("alts") or [])
        picked, why = _pick_best_installed(
            installed, role="chat", max_params=max_params, preferred=chat_default, alts=alts
        )
        reasons.append(why)
        if picked:
            pref_size = _parse_size_b(chat_default) or 0
            got_size = _parse_size_b(picked) or 0
            # If installed is far weaker than the tier default, still plan to pull the default.
            if pref_size and got_size + 0.5 < pref_size * 0.5:
                reasons.append(
                    f"installed `{picked}` is undersized vs `{chat_default}` — scheduling pull"
                )
                # keep chat_model = chat_default (pull)
            else:
                chat_model = picked
                installed_used["chat"] = picked

        picked_f, why_f = _pick_best_installed(
            installed, role="fast", max_params=1.5, preferred=fast_model, alts=[]
        )
        reasons.append(why_f)
        if picked_f:
            fast_model = picked_f
            installed_used["fast"] = picked_f

        if vision_model:
            picked_v, why_v = _pick_best_installed(
                installed,
                role="vision",
                max_params=max_params,
                preferred=str(vision_model),
                alts=[],
            )
            reasons.append(why_v)
            if picked_v:
                vision_model = picked_v
                installed_used["vision"] = picked_v

        picked_e, why_e = _pick_best_installed(
            installed, role="embed", max_params=1.0, preferred=embed_model, alts=[]
        )
        reasons.append(why_e)
        if picked_e:
            embed_model = picked_e
            installed_used["embed"] = picked_e
    else:
        reasons.append(defaults.get("reason") or f"tier `{tier}` defaults")

    # Skip vision on lite/cpu_only
    if tier in ("lite", "cpu_only"):
        vision_model = None

    wanted = [chat_model, fast_model, embed_model]
    if vision_model:
        wanted.append(str(vision_model))
    pulls_needed = []
    for name in wanted:
        if not name:
            continue
        if not any(
            name == m or m.startswith(name) or name.startswith(m.split(":")[0]) for m in installed
        ):
            pulls_needed.append(name)

    lm = discover_lm_studio()
    reason = "; ".join(r for r in reasons if r) or defaults.get("reason") or tier

    plan = {
        "ok": True,
        "chat_model": chat_model,
        "fast_model": fast_model,
        "vision_model": vision_model,
        "embed_model": embed_model,
        "tier": tier,
        "hardware_score": score,
        "max_chat_params_b": max_params,
        "reason": reason,
        "pulls_needed": pulls_needed,
        "installed_used": installed_used,
        "installed": installed,
        "hardware": {
            "ram_gb": hw.get("ram_gb"),
            "best_vram_gb": hw.get("best_vram_gb"),
            "os": hw.get("os"),
            "gpus": hw.get("gpus"),
        },
        "lm_studio": lm,
        "defaults_for_tier": {
            "chat": defaults.get("chat"),
            "fast": defaults.get("fast"),
            "vision": defaults.get("vision"),
            "embed": defaults.get("embed"),
        },
    }
    return plan


def save_plan(plan: dict[str, Any], path: Path | None = None) -> Path:
    dest = path or (user_data_subdir() / "model_plan.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    slim = json.loads(json.dumps(plan, default=str))
    dest.write_text(json.dumps(slim, indent=2), encoding="utf-8")
    return dest


def plan_to_env_updates(plan: dict[str, Any]) -> dict[str, str]:
    """Map a plan to VOXORYL_* / OLLAMA_* keys (no API keys)."""
    updates: dict[str, str] = {}
    if plan.get("chat_model"):
        updates["VOXORYL_MAIN_MODEL"] = str(plan["chat_model"])
        updates["OLLAMA_MODEL"] = str(plan["chat_model"])
    if plan.get("fast_model"):
        updates["VOXORYL_FAST_MODEL"] = str(plan["fast_model"])
    if plan.get("embed_model"):
        updates["OLLAMA_EMBED_MODEL"] = str(plan["embed_model"])
    if plan.get("vision_model"):
        updates["OLLAMA_VISION_MODEL"] = str(plan["vision_model"])
    return updates


if __name__ == "__main__":
    print(json.dumps(plan_models(), indent=2))
