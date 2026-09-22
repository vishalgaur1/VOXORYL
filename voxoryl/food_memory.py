from __future__ import annotations

"""
Ingest Swiggy / Zomato / Zepto / Blinkit (etc.) order history — remember what you eat.
All local under data/food_orders/. Never uploaded.
"""

import json
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voxoryl.config import settings
from voxoryl.knowledge import knowledge
from voxoryl.llm import chat_local, parse_json_loose
from voxoryl.memory import memory


PLATFORMS = ("swiggy", "zomato", "zepto", "blinkit", "instamart", "bigbasket", "dunzo")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def root() -> Path:
    p = settings.voxoryl_data_dir / "food_orders"
    p.mkdir(parents=True, exist_ok=True)
    (p / "inbox").mkdir(parents=True, exist_ok=True)
    return p


def _taste_path() -> Path:
    return root() / "taste.json"


def load_taste() -> dict[str, Any]:
    path = _taste_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"items": {}, "platforms": {}, "orders": [], "likes": [], "updated_at": None}


def save_taste(data: dict[str, Any]) -> None:
    data["updated_at"] = _now()
    _taste_path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def detect_platform(text: str) -> str:
    lower = text.lower()
    for p in PLATFORMS:
        if p in lower:
            return p
    return "unknown"


def _norm_item(name: str) -> str:
    n = re.sub(r"\s+", " ", (name or "").strip().lower())
    n = re.sub(r"[^\w\s&+\-]", "", n)
    return n[:80]


def extract_items_heuristic(text: str) -> list[str]:
    """Pull likely dish/product lines from pasted order history / CSV."""
    items: list[str] = []
    # CSV-ish: Item,Qty or Name,Price
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        lower = raw.lower()
        if any(
            k in lower
            for k in (
                "order id",
                "ordered on",
                "delivered",
                "total",
                "gst",
                "invoice",
                "payment",
                "restaurant",
                "address",
                "swiggy",
                "zomato",
                "zepto",
                "blinkit",
                "instamart",
                "order history",
            )
        ):
            continue
        # skip bare platform labels
        if lower.strip() in set(PLATFORMS) | {"order", "orders"}:
            continue
        # "2 x Butter Chicken" / "Butter Chicken x 1"
        m = re.search(r"(?:(\d+)\s*[x×]\s*)?([A-Za-z][A-Za-z0-9 &\-]{2,60})(?:\s*[x×]\s*(\d+))?", raw)
        if m and not re.match(r"^\d+[\/\-]", raw):
            name = (m.group(2) or "").strip()
            if len(name) < 3:
                continue
            if name.lower() in {"qty", "quantity", "item", "name", "price", "amount"}:
                continue
            items.append(name)
        # comma CSV first column
        if "," in raw:
            first = raw.split(",")[0].strip().strip('"')
            if first and not first.replace(".", "", 1).isdigit() and len(first) > 2:
                items.append(first)
    # dedupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        key = _norm_item(it)
        if key and key not in seen:
            seen.add(key)
            out.append(it.strip()[:80])
    return out[:200]


async def extract_items_llm(text: str) -> list[str]:
    snippet = text[:6000]
    raw = await chat_local(
        [
            {
                "role": "system",
                "content": (
                    "Extract food/grocery item names from delivery order history. "
                    'Return JSON only: {"items":["name",...]} — max 80 items. No prices.'
                ),
            },
            {"role": "user", "content": snippet},
        ],
        temperature=0.1,
    )
    data = parse_json_loose(raw) or {}
    items = data.get("items") if isinstance(data, dict) else None
    if isinstance(items, list):
        return [str(x).strip()[:80] for x in items if str(x).strip()][:80]
    return []


def remember_items(items: list[str], *, platform: str = "unknown", source: str = "") -> dict[str, Any]:
    taste = load_taste()
    counts: dict[str, Any] = taste.setdefault("items", {})
    plats: dict[str, Any] = taste.setdefault("platforms", {})
    plats[platform] = int(plats.get(platform) or 0) + 1
    for it in items:
        key = _norm_item(it)
        if not key:
            continue
        entry = counts.get(key) or {"name": it.strip()[:80], "count": 0, "platforms": []}
        entry["count"] = int(entry.get("count") or 0) + 1
        entry["name"] = it.strip()[:80]
        p_list = list(entry.get("platforms") or [])
        if platform not in p_list:
            p_list.append(platform)
        entry["platforms"] = p_list[-8:]
        counts[key] = entry
    order = {
        "id": str(uuid.uuid4())[:8],
        "at": _now(),
        "platform": platform,
        "source": source[:200],
        "item_count": len(items),
        "sample": items[:12],
    }
    taste.setdefault("orders", []).append(order)
    taste["orders"] = taste["orders"][-100:]
    # top likes
    ranked = sorted(counts.values(), key=lambda x: int(x.get("count") or 0), reverse=True)
    taste["likes"] = [r.get("name") for r in ranked[:25] if r.get("name")]
    save_taste(taste)

    top = ", ".join(taste["likes"][:8]) or "none yet"
    knowledge.append_facts(
        "Food tastes",
        [
            f"Order ingest ({platform}): {len(items)} items. Frequent likes: {top}.",
        ],
    )
    memory.remember_fact(
        f"Food memory updated from {platform}: top likes {top}",
        tags=["food", "taste", platform],
    )
    return {
        "ok": True,
        "platform": platform,
        "items_seen": len(items),
        "likes": taste["likes"][:15],
        "speak": f"Logged {len(items)} items from {platform}. You often get: {top}.",
    }


async def ingest_text(text: str, *, path: str = "") -> dict[str, Any]:
    if not (text or "").strip():
        return {"ok": False, "error": "empty order history", "hint": "Paste history or drop a file in data/food_orders/inbox/"}
    platform = detect_platform(text + " " + path)
    items = extract_items_heuristic(text)
    if len(items) < 3:
        llm_items = await extract_items_llm(text)
        for it in llm_items:
            if _norm_item(it) not in {_norm_item(x) for x in items}:
                items.append(it)
    if not items:
        return {"ok": False, "error": "could not parse items", "speak": "Could not parse food items — paste clearer order lines or CSV."}
    return remember_items(items, platform=platform, source=path or "paste")


async def ingest_inbox() -> dict[str, Any]:
    inbox = root() / "inbox"
    files = [p for p in inbox.iterdir() if p.is_file() and p.suffix.lower() in {".txt", ".csv", ".json", ".md"}]
    if not files:
        return {
            "ok": False,
            "hint": "Drop Swiggy/Zomato/Zepto/Blinkit exports into data/food_orders/inbox/",
            "speak": "No food-order files in inbox.",
        }
    results = []
    for f in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
        text = f.read_text(encoding="utf-8", errors="ignore")
        if f.suffix.lower() == ".json":
            try:
                data = json.loads(text)
                text = json.dumps(data, ensure_ascii=False) if not isinstance(data, str) else data
            except json.JSONDecodeError:
                pass
        r = await ingest_text(text, path=str(f))
        results.append({"file": f.name, **r})
        if r.get("ok"):
            done = inbox / "processed"
            done.mkdir(exist_ok=True)
            dest = done / f.name
            if not dest.exists():
                f.rename(dest)
    ok = any(r.get("ok") for r in results)
    speak = results[0].get("speak") if results else "No files."
    return {"ok": ok, "results": results, "speak": speak}


def taste_summary() -> dict[str, Any]:
    taste = load_taste()
    likes = taste.get("likes") or []
    top_counts = sorted(
        (taste.get("items") or {}).values(),
        key=lambda x: int(x.get("count") or 0),
        reverse=True,
    )[:15]
    speak = (
        f"Food memory: {len(taste.get('orders') or [])} orders. Likes: {', '.join(likes[:10]) or 'none'}."
        if likes
        else "No food memory yet — drop order history in data/food_orders/inbox/."
    )
    return {"ok": True, "likes": likes, "top": top_counts, "platforms": taste.get("platforms"), "speak": speak}


async def tool_food(
    action: str = "summary",
    message: str = "",
    path: str = "",
) -> dict[str, Any]:
    act = (action or "summary").lower()
    lower = (message or "").lower()
    if act in {"ingest", "import", "add"} or any(k in lower for k in ("swiggy", "zomato", "zepto", "blinkit", "order history")):
        if path:
            p = Path(path)
            if p.exists():
                return await ingest_text(p.read_text(encoding="utf-8", errors="ignore"), path=str(p))
        # pasted body after keywords
        body = message
        for cut in ("order history:", "here is my", "paste:"):
            if cut in lower:
                body = message[lower.index(cut) + len(cut) :]
                break
        if len((body or "").strip()) > 40 and not path:
            return await ingest_text(body)
        return await ingest_inbox()
    if act in {"likes", "summary", "status", "what i eat", "taste"}:
        return taste_summary()
    return taste_summary()
