from __future__ import annotations

"""
Health skill: optional habit flags for compounds / patterns in foods the user eats.
Jack-of-all-trades helper — not medical care, not a diagnosis product.
"""

import json
from collections import Counter
from pathlib import Path
from typing import Any

from voxoryl.config import settings
from voxoryl.food_memory import load_taste
from voxoryl.knowledge import knowledge
from voxoryl.memory import memory


ROOT = Path(__file__).resolve().parent.parent
COMPOUNDS_PATH = ROOT / "setup" / "food_compounds.json"


def load_compounds() -> dict[str, Any]:
    if COMPOUNDS_PATH.exists():
        try:
            return json.loads(COMPOUNDS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"compounds": {}, "item_rules": []}


def flags_for_item(name: str) -> list[dict[str, Any]]:
    cfg = load_compounds()
    compounds = cfg.get("compounds") or {}
    lower = (name or "").lower()
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rule in cfg.get("item_rules") or []:
        keys = rule.get("match") or []
        if any(k in lower for k in keys):
            for fid in rule.get("flags") or []:
                if fid in seen:
                    continue
                seen.add(fid)
                meta = compounds.get(fid) or {"label": fid, "why": ""}
                found.append(
                    {
                        "id": fid,
                        "label": meta.get("label") or fid,
                        "why": meta.get("why") or "",
                        "item": name,
                    }
                )
    return found


def scan_items(items: list[str]) -> dict[str, Any]:
    alerts: list[dict[str, Any]] = []
    for it in items:
        for f in flags_for_item(it):
            alerts.append(f)
    by_flag = Counter(a["id"] for a in alerts)
    ranked = sorted(by_flag.items(), key=lambda x: -x[1])
    lines = []
    for fid, n in ranked[:8]:
        meta = (load_compounds().get("compounds") or {}).get(fid) or {}
        sample = next((a["item"] for a in alerts if a["id"] == fid), "")
        lines.append(
            f"Ease up on {sample or fid}: {meta.get('label') or fid} — {meta.get('why') or 'frequent intake is rough'} (seen ~{n}x in this scan)."
        )
    speak = " ".join(lines[:3]) if lines else "No strong compound flags on these items."
    return {
        "ok": True,
        "alerts": alerts[:40],
        "ranked_flags": [{"id": a, "count": c} for a, c in ranked],
        "advice": lines,
        "speak": speak,
        "disclaimer": "Not medical advice — pattern nudge only.",
    }


def scan_taste_memory() -> dict[str, Any]:
    taste = load_taste()
    items = []
    for key, entry in (taste.get("items") or {}).items():
        name = str(entry.get("name") or key)
        count = int(entry.get("count") or 1)
        items.extend([name] * min(count, 12))
    if not items:
        return {
            "ok": False,
            "speak": "No food memory yet. Ingest Swiggy/Zomato/etc history first, then I can flag rough compounds.",
            "hint": "data/food_orders/inbox/",
        }
    result = scan_items(items)
    if result.get("advice"):
        knowledge.append_facts("Health food flags", result["advice"][:5])
        memory.remember_fact(result["speak"][:240], tags=["health", "food"])
    likes = ", ".join((taste.get("likes") or [])[:5])
    result["speak"] = (
        f"Based on what you order a lot ({likes}): " + result["speak"]
        if likes
        else result["speak"]
    )
    return result


def check_one(item: str) -> dict[str, Any]:
    flags = flags_for_item(item)
    if not flags:
        return {
            "ok": True,
            "item": item,
            "flags": [],
            "speak": f"No strong flags for '{item}' in the local compound list — still fine to keep portions sane.",
        }
    bits = [f"{f['label']}: {f['why']}" for f in flags]
    speak = f"Consider cutting back on {item} — " + " | ".join(bits)
    return {"ok": True, "item": item, "flags": flags, "speak": speak, "disclaimer": "Not medical advice."}


async def tool_health(action: str = "scan", message: str = "", item: str = "") -> dict[str, Any]:
    act = (action or "scan").lower()
    lower = (message or "").lower()
    # extract item after "check" / "is"
    target = (item or "").strip()
    if not target:
        for prefix in ("check health of", "health check", "is ", "about "):
            if prefix in lower:
                target = message[lower.index(prefix) + len(prefix) :].strip(" ?.")
                break
    if act in {"check", "item"} or (target and "scan" not in act):
        if target:
            return check_one(target)
    if act in {"scan", "taste", "memory"} or any(k in lower for k in ("my food", "what i eat", "order history health")):
        return scan_taste_memory()
    if target:
        return check_one(target)
    return scan_taste_memory()
