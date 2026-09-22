from __future__ import annotations

"""
Style / colors from photos — vision on a photo → clothing colors that suit / avoid,
then research genuine shopping sites. Asks value_mode before price optimization.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from voxoryl.config import settings
from voxoryl.knowledge import knowledge
from voxoryl.memory import memory
from voxoryl.prefs import resolve_or_ask, get_value_mode


ROOT = Path(__file__).resolve().parent.parent
SITES_PATH = ROOT / "setup" / "genuine_sites.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def root() -> Path:
    p = settings.voxoryl_data_dir / "style_colors"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _history_path() -> Path:
    return root() / "history.jsonl"


def load_fashion_sites() -> list[str]:
    if SITES_PATH.exists():
        try:
            data = json.loads(SITES_PATH.read_text(encoding="utf-8"))
            sites = data.get("fashion") or data.get("shopping") or []
            if isinstance(sites, list) and sites:
                return [str(s) for s in sites]
        except json.JSONDecodeError:
            pass
    return [
        "https://www.uniqlo.com",
        "https://www.zara.com",
        "https://www2.hm.com",
        "https://www.nike.com",
        "https://www.adidas.com",
        "https://www.myntra.com",
        "https://www.ajio.com",
        "https://www.nordstrom.com",
        "https://www.gap.com",
    ]


def is_fashion_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    for a in load_fashion_sites():
        ah = urlparse(a).netloc.lower()
        if ah and (host == ah or host.endswith("." + ah)):
            return True
    return False


def _log(entry: dict[str, Any]) -> None:
    with _history_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _heuristic_colors_from_text(desc: str) -> dict[str, Any]:
    lower = (desc or "").lower()
    warm = any(k in lower for k in ("warm", "olive", "golden", "peach", "brown eyes", "amber"))
    cool = any(k in lower for k in ("cool", "pink undertone", "blue eyes", "ash", "fair cool"))
    deep = any(k in lower for k in ("deep", "dark hair", "rich skin", "brunette"))
    suit = []
    avoid = []
    if warm:
        suit.extend(["olive", "camel", "terracotta", "cream", "forest green"])
        avoid.extend(["icy pastels", "stark pure white", "neon cool pink"])
    elif cool:
        suit.extend(["navy", "charcoal", "jewel teal", "soft rose", "true white"])
        avoid.extend(["orange-heavy", "mustard overload", "muddy browns"])
    else:
        suit.extend(["navy", "charcoal", "white", "soft grey", "muted blue"])
        avoid.extend(["clashing neons", "washed-out beige-on-beige"])
    if deep:
        suit.extend(["burgundy", "emerald", "black"])
    # skin/hair color words in vision text
    for c in ("black", "white", "blue", "red", "green", "beige", "brown", "grey", "gray", "pink"):
        if c in lower and c not in suit:
            suit.append(c)
    return {
        "suit": list(dict.fromkeys(suit))[:8],
        "avoid": list(dict.fromkeys(avoid))[:6],
        "undertone_guess": "warm" if warm else ("cool" if cool else "neutral/unknown"),
        "disclaimer": "Heuristic from photo description — not a professional color analysis.",
    }


async def analyze_photo(
    *,
    message: str = "",
    path: str = "",
    camera_index: int = 0,
) -> dict[str, Any]:
    vision: dict[str, Any] = {}
    try:
        from voxoryl.camera import vision_on_image, see

        q = (
            "Describe the person's visible coloring for clothing advice: skin tone warmth if guessable, "
            "hair color, eye color, and current outfit colors. Be concrete. No medical claims."
        )
        if path:
            vision = await vision_on_image(path, q)
        else:
            vision = await see(question=q, camera_index=camera_index)
            if vision.get("ok") and not vision.get("description"):
                # see() nests vision
                pass
            if isinstance(vision.get("vision"), dict):
                vision = {**vision, **vision["vision"], "ok": vision.get("ok")}
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "hint": "Need opencv + local VL (qwen2.5vl) — or pass path= to an existing photo.",
            "speak": "Camera/vision isn't ready. Share a photo path or install opencv + a vision model.",
        }
    if not vision.get("ok"):
        return {
            "ok": False,
            "error": vision.get("error"),
            "hint": vision.get("hint") or "pip install opencv-python; ensure Ollama vision model is pulled.",
            "speak": str(vision.get("error") or vision.get("hint") or "Couldn't analyze the photo."),
            "vision": vision,
        }
    desc = str(vision.get("description") or "")
    colors = _heuristic_colors_from_text(desc)
    entry = {"at": _now(), "path": vision.get("path") or path, "colors": colors, "desc": desc[:400]}
    _log(entry)
    try:
        knowledge.append_facts(
            "Style Colors",
            [f"Suit: {', '.join(colors['suit'][:5])}. Avoid: {', '.join(colors['avoid'][:3])}."],
        )
    except Exception:
        pass
    speak = (
        f"Colors that likely suit you: {', '.join(colors['suit'][:5])}. "
        f"I'd soften: {', '.join(colors['avoid'][:3])}. "
        f"(Undertone guess: {colors['undertone_guess']}.) Want shopping links? Say outfit from this photo."
    )
    return {"ok": True, "vision": vision, "colors": colors, "speak": speak}


async def shop_for_colors(
    message: str = "",
    *,
    colors: dict[str, Any] | None = None,
    value_mode: str | None = None,
) -> dict[str, Any]:
    pref = resolve_or_ask("clothing / outfit shopping")
    if pref.get("needs_preference") and not value_mode:
        return pref
    mode = value_mode or pref.get("value_mode") or get_value_mode() or "balanced"
    if not colors:
        # try last history
        colors = {"suit": ["navy", "charcoal", "white"], "avoid": []}
        if _history_path().exists():
            try:
                last = _history_path().read_text(encoding="utf-8").strip().splitlines()[-1]
                colors = (json.loads(last).get("colors") or colors)
            except Exception:
                pass
    suit = colors.get("suit") or ["navy"]
    allow = load_fashion_sites()
    query = f"{' '.join(suit[:3])} clothing outfit {mode} buy site:{' OR site:'.join(urlparse(u).netloc for u in allow[:4])}"
    research: dict[str, Any] = {}
    try:
        from voxoryl.research import deep_research

        research = await deep_research(
            f"genuine clothing sites for {', '.join(suit[:4])} outfits preference={mode}",
            conclude=True,
        )
    except Exception as exc:
        research = {"ok": False, "error": str(exc)}
    # filter URLs to allowlist when present
    links: list[str] = []
    blob = str(research.get("speak") or research.get("abstract") or "") + " " + json.dumps(research.get("sources") or [])
    for url in re.findall(r"https?://[^\s\]\)\"']+", blob):
        if is_fashion_url(url):
            links.append(url)
    links = list(dict.fromkeys(links))[:8]
    if not links:
        links = allow[:5]
    rank = {
        "budget": "Lean affordable basics on genuine retailers.",
        "quality": "Prefer durable / better fabric even if pricier.",
        "balanced": "Solid mid-range from trusted brands.",
    }.get(mode, "balanced")
    speak = (
        f"For colors {', '.join(suit[:4])} ({mode}): {rank} "
        f"Start here: {', '.join(links[:3])}."
    )
    if not research.get("ok"):
        speak += " Research was thin — using the allowlisted fashion sites."
    memory.remember_fact(f"style shop {mode}: {suit[:3]}", tags=["style", "fashion"])
    return {
        "ok": True,
        "value_mode": mode,
        "colors": colors,
        "links": links,
        "allowlist": allow,
        "research_ok": bool(research.get("ok")),
        "speak": speak,
    }


async def tool_style_colors(
    action: str = "analyze",
    *,
    message: str = "",
    path: str = "",
    camera_index: int = 0,
    value_mode: str = "",
) -> dict[str, Any]:
    action = (action or "analyze").lower().strip()
    lower = (message or "").lower()

    # extract path from message if present
    if not path:
        m = re.search(r"(?:path|photo|file)\s*[=:]\s*([^\s]+)", message or "", re.I)
        if m:
            path = m.group(1).strip().strip('"')
        else:
            m2 = re.search(r"([A-Za-z]:\\[^\s\"']+\.(?:jpg|jpeg|png|webp))", message or "", re.I)
            if m2:
                path = m2.group(1)

    if action in {"shop", "outfit", "buy"} or any(
        k in lower for k in ("outfit from", "shop for", "buy clothes", "shopping for these colors")
    ):
        analyzed = None
        if path or any(k in lower for k in ("this photo", "camera", "webcam", "what colors suit")):
            analyzed = await analyze_photo(message=message, path=path, camera_index=camera_index)
            if analyzed.get("needs_preference"):
                return analyzed
            if not analyzed.get("ok"):
                # still try shop with defaults after reporting
                pass
        colors = (analyzed or {}).get("colors") if isinstance(analyzed, dict) else None
        shop = await shop_for_colors(message, colors=colors, value_mode=value_mode or None)
        if analyzed and analyzed.get("ok") and shop.get("ok") and not shop.get("needs_preference"):
            shop["speak"] = str(analyzed.get("speak") or "")[:220] + " " + str(shop.get("speak") or "")
            shop["vision"] = analyzed.get("vision")
            shop["colors"] = analyzed.get("colors") or shop.get("colors")
        return shop

    if action in {"analyze", "colors", "suit"} or any(
        k in lower for k in ("what colors suit", "colors suit me", "color analysis", "from this photo")
    ):
        return await analyze_photo(message=message, path=path, camera_index=camera_index)

    return await analyze_photo(message=message, path=path, camera_index=camera_index)
