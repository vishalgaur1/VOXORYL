from __future__ import annotations

"""
Genuine-site bookings / price research.
- Ask value preference if unknown (never assume cheap vs premium).
- Only recommend allowlisted genuine domains from setup/genuine_sites.json.
"""

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from voxoryl.config import settings
from voxoryl.llm import chat_local, parse_json_loose
from voxoryl.prefs import resolve_or_ask, get_value_mode
from voxoryl.tools import tool_research


ROOT = Path(__file__).resolve().parent.parent
SITES_PATH = ROOT / "setup" / "genuine_sites.json"


def load_sites() -> dict[str, Any]:
    if SITES_PATH.exists():
        try:
            return json.loads(SITES_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"travel": [], "gov_in": [], "gov_us": [], "blocked_patterns": []}


def is_genuine_url(url: str, *, category: str = "travel") -> bool:
    cfg = load_sites()
    u = (url or "").lower()
    for bad in cfg.get("blocked_patterns") or []:
        if bad.lower() in u:
            return False
    allowed = []
    if category == "travel":
        allowed = cfg.get("travel") or []
    elif category in {"gov", "gov_in"}:
        allowed = (cfg.get("gov_in") or []) + (cfg.get("gov_us") or [])
    else:
        allowed = (cfg.get("travel") or []) + (cfg.get("gov_in") or []) + (cfg.get("gov_us") or [])
    host = urlparse(url).netloc.lower() if "://" in url else ""
    for a in allowed:
        ah = urlparse(a).netloc.lower()
        if ah and (host == ah or host.endswith("." + ah)):
            return True
        if ah and ah in u:
            return True
    # official gov TLDs
    if host.endswith(".gov") or host.endswith(".gov.in") or host.endswith(".nic.in"):
        return True
    return False


def filter_genuine(results: list[dict[str, Any]], *, category: str = "travel") -> list[dict[str, Any]]:
    out = []
    for r in results:
        href = str(r.get("href") or r.get("url") or "")
        if href and is_genuine_url(href, category=category):
            out.append(r)
    return out


async def find_tickets(query: str, *, value_mode: str | None = None) -> dict[str, Any]:
    pref = resolve_or_ask("ticket booking")
    if pref.get("needs_preference") and not value_mode:
        return pref
    mode = value_mode or pref.get("value_mode") or get_value_mode() or "balanced"

    research = await tool_research(f"{query} flights OR trains official booking")
    results = []
    if isinstance(research, dict):
        for key in ("results", "organic", "related"):
            chunk = research.get(key) or []
            if isinstance(chunk, list):
                for item in chunk:
                    if isinstance(item, dict):
                        results.append(
                            {
                                "href": item.get("href") or item.get("url") or item.get("FirstURL") or "",
                                "title": item.get("title") or item.get("Text") or "",
                            }
                        )
        blob = str(research.get("brief") or research.get("AbstractText") or research)
        for url in re.findall(r"https?://[^\s\]\)]+", blob):
            results.append({"href": url, "title": url})
    genuine = filter_genuine(results, category="travel")
    allow = load_sites().get("travel") or []

    rank_hint = {
        "budget": "Prefer lower fare among genuine sites. Skip luxury-only options unless only choice.",
        "quality": "Prefer reputable carriers, flexible/refundable, comfort — price secondary.",
        "balanced": "Best fare among decent airlines/routes on genuine sites.",
    }.get(mode, "balanced")

    raw = await chat_local(
        [
            {
                "role": "system",
                "content": (
                    "You help book travel using ONLY genuine sites. Return ONLY JSON:\n"
                    '{"summary":"...","picks":[{"site":"...","why":"...","url":"..."}],'
                    '"warnings":["..."],"next_step":"..."}\n'
                    f"Optimize for: {mode}. {rank_hint}\n"
                    "Never invent prices. If price unknown, say check site. No shady resellers."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Query: {query}\nValue mode: {mode}\n"
                    f"Allowlist hosts: {allow[:12]}\n"
                    f"Genuine search hits: {genuine[:10]}\n"
                    f"Research brief: {str((research or {}).get('brief') or research)[:2000]}"
                ),
            },
        ],
        temperature=0.2,
    )
    parsed = parse_json_loose(raw) or {}
    picks = []
    for p in parsed.get("picks") or []:
        url = str(p.get("url") or "")
        if url and not is_genuine_url(url, category="travel"):
            continue
        picks.append(p)
    if not picks:
        picks = [{"site": urlparse(u).netloc, "why": "Allowlisted genuine booking site", "url": u} for u in allow[:5]]

    return {
        "ok": True,
        "value_mode": mode,
        "query": query,
        "genuine_hits": genuine[:10],
        "picks": picks,
        "summary": parsed.get("summary") or f"Options on genuine sites ({mode}).",
        "warnings": parsed.get("warnings") or [],
        "next_step": parsed.get("next_step")
        or "Open a pick, then say 'flash fill this form' if you want identity pasted.",
        "speak": (
            f"{mode.upper()} search — genuine sites only. "
            + str(parsed.get("summary") or f"{len(picks)} places to check.")[:200]
        ),
        "research": {"ok": research.get("ok") if isinstance(research, dict) else True},
    }


async def gov_form_help(message: str) -> dict[str, Any]:
    from voxoryl.docs_vault import find_for_task
    from voxoryl.screen import computer_use_enabled, tool_screen

    docs = find_for_task(message)
    cfg = load_sites()
    portals = (cfg.get("gov_in") or []) + (cfg.get("gov_us") or [])
    # Match portal
    portal = None
    ml = message.lower()
    if "itr" in ml or "income tax" in ml:
        portal = "https://eportal.incometax.gov.in"
    elif "passport" in ml:
        portal = "https://www.passportindia.gov.in"
    elif "gst" in ml:
        portal = "https://www.gst.gov.in"

    result: dict[str, Any] = {
        "ok": True,
        "portal": portal,
        "allowed_portals": portals[:10],
        "docs": docs,
        "speak": docs.get("speak"),
        "next": "Open the official portal, then say 'flash fill this form'. Upload vault files yourself — Voxoryl won't leak them.",
        "local_only": True,
    }
    if computer_use_enabled() and any(k in ml for k in ("fill", "open", "help me file")):
        goal = (
            f"Open ONLY the official site {portal or 'the official government portal for: ' + message}. "
            f"Do not use third-party tax filers unless user said so. "
            f"Help navigate the form. Identity fields available for paste. "
            f"Needed docs tags: {docs.get('needed_tags')}. Missing: {docs.get('missing_tags')}."
        )
        screen = await tool_screen(action="flash_fill" if "fill" in ml else "act", goal=goal)
        result["screen"] = screen
        result["speak"] = str(screen.get("speak") or result["speak"])
    return result


async def tool_bookings(
    action: str = "tickets",
    *,
    message: str = "",
    value_mode: str = "",
) -> dict[str, Any]:
    action = (action or "tickets").lower()
    msg = message or ""
    lower = msg.lower()

    # Preference set inline
    if any(k in lower for k in ("my preference is", "optimize for", "i prefer")) and any(
        k in lower for k in ("budget", "balanced", "quality", "cheap", "premium")
    ):
        from voxoryl.prefs import tool_prefs

        return await tool_prefs(action="set", message=msg)

    if action in {"gov", "itr", "tax", "form"} or any(
        k in lower for k in ("itr", "income tax", "passport application", "gst return", "govt form", "government form")
    ):
        return await gov_form_help(msg)

    if action in {"tickets", "flights", "travel", "find"} or any(
        k in lower for k in ("flight", "ticket", "train", "hotel", "book")
    ):
        return await find_tickets(msg, value_mode=value_mode or None)

    return await find_tickets(msg or "flights", value_mode=value_mode or None)
