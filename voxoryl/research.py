from __future__ import annotations

"""
Always-on research method:
1) Search the internet first
2) Prefer big reference platforms (Wikipedia, Stack Overflow, MDN, …)
3) Scan public chatter (Reddit, etc.) for how people fixed things — discount opinions
4) Pull official docs / manuals
5) Form one conclusion (docs > reference > community)
"""

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

import httpx

from voxoryl.config import settings
from voxoryl.llm import chat_local, parse_json_loose
from voxoryl.memory import memory
from voxoryl.style import finalize_speak


ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "setup" / "research_policy.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_policy() -> dict[str, Any]:
    if POLICY_PATH.exists():
        try:
            return json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {
        "always_on": True,
        "tiers": {
            "docs": {"weight": 3.0, "domains": []},
            "reference": {"weight": 2.0, "domains": []},
            "community": {"weight": 0.35, "domains": [], "caution": "discount"},
            "other": {"weight": 0.8, "domains": []},
        },
        "conclusion_rules": [],
    }


def classify_url(url: str) -> str:
    u = (url or "").lower()
    host = urlparse(url).netloc.lower() if "://" in (url or "") else u
    tiers = load_policy().get("tiers") or {}
    for name in ("docs", "reference", "community"):
        for d in (tiers.get(name) or {}).get("domains") or []:
            d = d.lower()
            if d.startswith(".") and host.endswith(d):
                return name
            if d in u or d in host:
                return name
    # heuristic path
    if "/docs/" in u or u.rstrip("/").endswith("/docs") or "documentation" in u:
        return "docs"
    return "other"


def tier_weight(tier: str) -> float:
    tiers = load_policy().get("tiers") or {}
    return float((tiers.get(tier) or {}).get("weight") or 0.8)


async def _search_raw(query: str, *, max_results: int = 8) -> list[dict[str, str]]:
    """Low-level web search — SearXNG → DDG → instant answer."""
    q = query.strip()
    if not q:
        return []

    searx = (settings.searxng_url or "").strip().rstrip("/")
    if searx:
        try:
            async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
                r = await client.get(
                    f"{searx}/search",
                    params={"q": q, "format": "json"},
                    headers={"User-Agent": "VoxorylLocal/0.3"},
                )
                if r.status_code < 400:
                    data = r.json()
                    out = []
                    for x in (data.get("results") or [])[:max_results]:
                        out.append(
                            {
                                "title": x.get("title") or "",
                                "url": x.get("url") or "",
                                "snippet": (x.get("content") or "")[:280],
                                "engine": "searxng",
                            }
                        )
                    if out:
                        return out
        except Exception:
            pass

    try:
        from duckduckgo_search import DDGS

        def _ddg() -> list[dict[str, str]]:
            with DDGS() as ddgs:
                return list(ddgs.text(q, max_results=max_results))

        hits = await asyncio.to_thread(_ddg)
        return [
            {
                "title": h.get("title") or "",
                "url": h.get("href") or h.get("link") or "",
                "snippet": (h.get("body") or "")[:280],
                "engine": "duckduckgo_search",
            }
            for h in hits
        ]
    except Exception:
        pass

    url = f"https://api.duckduckgo.com/?q={quote_plus(q)}&format=json&no_html=1&skip_disambig=1"
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "VoxorylLocal/0.3"})
            data = r.json()
        related = []
        if data.get("AbstractText"):
            related.append(
                {
                    "title": data.get("Heading") or q,
                    "url": data.get("AbstractURL") or "",
                    "snippet": (data.get("AbstractText") or "")[:280],
                    "engine": "duckduckgo_instant",
                }
            )
        for t in data.get("RelatedTopics", []):
            if isinstance(t, dict) and t.get("Text"):
                related.append(
                    {
                        "title": t.get("Text", "")[:120],
                        "url": t.get("FirstURL", ""),
                        "snippet": t.get("Text", "")[:280],
                        "engine": "duckduckgo_instant",
                    }
                )
            elif isinstance(t, dict) and "Topics" in t:
                for sub in t["Topics"][:3]:
                    if sub.get("Text"):
                        related.append(
                            {
                                "title": sub.get("Text", "")[:120],
                                "url": sub.get("FirstURL", ""),
                                "snippet": sub.get("Text", "")[:280],
                                "engine": "duckduckgo_instant",
                            }
                        )
        return related[:max_results]
    except Exception:
        return []


def _dedupe(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for h in hits:
        url = (h.get("url") or "").strip().lower()
        key = url or (h.get("title") or "")[:80].lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def annotate(hits: list[dict[str, str]], *, lane: str) -> list[dict[str, Any]]:
    out = []
    for h in hits:
        tier = classify_url(h.get("url") or "")
        # if search was community-targeted but URL is docs, keep docs tier
        item = {
            **h,
            "lane": lane,
            "tier": tier,
            "weight": tier_weight(tier),
        }
        out.append(item)
    return out


async def _wikipedia_hits(query: str, *, limit: int = 3) -> list[dict[str, str]]:
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "opensearch",
                    "search": query,
                    "limit": limit,
                    "namespace": 0,
                    "format": "json",
                },
                headers={"User-Agent": "VoxorylLocal/0.3 (local research agent)"},
            )
            if r.status_code >= 400:
                return []
            data = r.json()
            # [query, titles, descriptions, urls]
            titles = data[1] if len(data) > 1 else []
            descs = data[2] if len(data) > 2 else []
            urls = data[3] if len(data) > 3 else []
            out = []
            for i, title in enumerate(titles):
                out.append(
                    {
                        "title": title,
                        "url": urls[i] if i < len(urls) else "",
                        "snippet": (descs[i] if i < len(descs) else title)[:280],
                        "engine": "wikipedia",
                    }
                )
            return out
    except Exception:
        return []


async def _stackexchange_hits(query: str, *, limit: int = 4) -> list[dict[str, str]]:
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(
                "https://api.stackexchange.com/2.3/search/advanced",
                params={
                    "order": "desc",
                    "sort": "relevance",
                    "q": query,
                    "accepted": "True",
                    "site": "stackoverflow",
                    "pagesize": limit,
                    "filter": "default",
                },
                headers={"User-Agent": "VoxorylLocal/0.3"},
            )
            if r.status_code >= 400:
                return []
            items = (r.json() or {}).get("items") or []
            out = []
            for it in items[:limit]:
                out.append(
                    {
                        "title": it.get("title") or "",
                        "url": it.get("link") or "",
                        "snippet": f"Score {it.get('score')} · answered={it.get('is_answered')} · tags={','.join((it.get('tags') or [])[:4])}",
                        "engine": "stackoverflow",
                    }
                )
            return out
    except Exception:
        return []


async def multi_lane_search(query: str) -> dict[str, Any]:
    """Always: reference + docs + community lanes in parallel (+ direct wiki/SO)."""
    q = query.strip()
    policy = load_policy()
    ref_hint = ((policy.get("tiers") or {}).get("reference") or {}).get("query_hints") or []
    docs_hints = ((policy.get("tiers") or {}).get("docs") or {}).get("query_hints") or ["official documentation"]
    community_hints = ((policy.get("tiers") or {}).get("community") or {}).get("query_hints") or ["site:reddit.com"]

    q_general = q
    q_reference = f"{q} {ref_hint[0]}" if ref_hint else f"{q} site:wikipedia.org OR site:stackoverflow.com"
    q_docs = f"{q} {docs_hints[0]} OR manual OR \"user guide\""
    q_community = f"{q} {community_hints[0]}" if community_hints else f"{q} site:reddit.com"

    general, reference, docs, community, wiki, so = await asyncio.gather(
        _search_raw(q_general, max_results=8),
        _search_raw(q_reference, max_results=6),
        _search_raw(q_docs, max_results=6),
        _search_raw(q_community, max_results=6),
        _wikipedia_hits(q, limit=3),
        _stackexchange_hits(q, limit=4),
    )

    all_hits = _dedupe(
        annotate(wiki, lane="reference")
        + annotate(so, lane="reference")
        + annotate(general, lane="general")
        + annotate(reference, lane="reference")
        + annotate(docs, lane="docs")
        + annotate(community, lane="community")
    )
    all_hits.sort(key=lambda x: float(x.get("weight") or 0), reverse=True)

    by_tier: dict[str, list] = {"docs": [], "reference": [], "community": [], "other": []}
    for h in all_hits:
        by_tier.setdefault(h.get("tier") or "other", []).append(h)

    return {
        "query": q,
        "lanes": {
            "general": general,
            "reference": reference,
            "docs": docs,
            "community": community,
            "wikipedia": wiki,
            "stackoverflow": so,
        },
        "hits": all_hits[:28],
        "by_tier": {k: v[:8] for k, v in by_tier.items()},
        "queries_used": {
            "general": q_general,
            "reference": q_reference,
            "docs": q_docs,
            "community": q_community,
            "wikipedia": "api:wikipedia opensearch",
            "stackoverflow": "api:stackexchange accepted answers",
        },
    }


async def form_conclusion(query: str, bundle: dict[str, Any]) -> dict[str, Any]:
    policy = load_policy()
    rules = policy.get("conclusion_rules") or []
    by_tier = bundle.get("by_tier") or {}
    hits = bundle.get("hits") or []
    solid = len(by_tier.get("docs") or []) + len(by_tier.get("reference") or [])
    weak_only = solid == 0 and (len(by_tier.get("community") or []) + len(by_tier.get("other") or [])) > 0

    if not hits:
        return {
            "conclusion": "No solid web hits yet — retry with a sharper query, or check network/search.",
            "confidence": "low",
            "why": "zero search results",
            "from_docs": [],
            "from_reference": [],
            "from_community_discounted": [],
            "next_action": "Rephrase the problem with the exact error or product name.",
        }

    def _fmt(tier: str, limit: int = 5) -> str:
        lines = []
        for h in (by_tier.get(tier) or [])[:limit]:
            lines.append(f"- [{h.get('title','')[:100]}]({h.get('url','')}) :: {h.get('snippet','')[:200]}")
        return "\n".join(lines) or "(none)"

    prompt = (
        f"Question: {query}\n\n"
        f"OFFICIAL DOCS / MANUALS (highest trust):\n{_fmt('docs')}\n\n"
        f"BIG REFERENCE (Wikipedia, StackOverflow, MDN, etc.):\n{_fmt('reference')}\n\n"
        f"PUBLIC CHATTER (Reddit etc — DISCOUNT; many people are wrong; use only for pain points/workarounds):\n{_fmt('community')}\n\n"
        f"OTHER:\n{_fmt('other', 4)}\n\n"
        "Rules:\n"
        + "\n".join(f"- {r}" for r in rules)
        + "\n- ONLY use the snippets above. If thin, say so and lower confidence."
        + "\n\nReturn ONLY JSON:\n"
        '{"conclusion":"3-6 short lines max — the answer",'
        '"confidence":"high|medium|low",'
        '"why":"one line why this confidence",'
        '"from_docs":["key doc point"],'
        '"from_reference":["key reference point"],'
        '"from_community_discounted":["anecdote used only as hint"],'
        '"next_action":"one concrete next step or empty"}'
    )

    raw = await chat_local(
        [
            {
                "role": "system",
                "content": (
                    "You are Voxoryl research synthesizer. "
                    "Docs/manuals > reference platforms > public opinions. "
                    "Never treat Reddit/Quora as proof. "
                    "Do not invent facts not present in the snippets. Short, blunt conclusion."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.15,
    )
    data = parse_json_loose(raw) or {}
    conclusion = str(data.get("conclusion") or "").strip()
    if not conclusion:
        top_docs = (by_tier.get("docs") or [])[:2]
        top_ref = (by_tier.get("reference") or [])[:2]
        bits = []
        for h in top_docs + top_ref + (by_tier.get("other") or [])[:2]:
            if h.get("snippet"):
                bits.append(h["snippet"][:160])
        conclusion = " | ".join(bits)[:500] or "Sources found but thin — need a clearer query."
        data = {
            "conclusion": conclusion,
            "confidence": "medium" if solid else "low",
            "why": "fallback from top weighted snippets",
            "from_docs": [h.get("title") for h in top_docs],
            "from_reference": [h.get("title") for h in top_ref],
            "from_community_discounted": [],
            "next_action": "",
        }

    conf = str(data.get("confidence") or "medium").lower()
    if solid == 0:
        conf = "low"
    elif weak_only:
        conf = "low"
    elif solid >= 2 and conf == "low":
        conf = "medium"
    data["confidence"] = conf
    data["conclusion"] = conclusion
    return data


def speak_from_conclusion(query: str, synthesized: dict[str, Any], bundle: dict[str, Any]) -> str:
    conf = synthesized.get("confidence") or "medium"
    conclusion = str(synthesized.get("conclusion") or "").strip()
    next_a = str(synthesized.get("next_action") or "").strip()
    docs_n = len((bundle.get("by_tier") or {}).get("docs") or [])
    ref_n = len((bundle.get("by_tier") or {}).get("reference") or [])
    com_n = len((bundle.get("by_tier") or {}).get("community") or [])
    lines = [
        conclusion,
        f"Confidence: {conf} (docs:{docs_n} ref:{ref_n} chatter:{com_n} discounted).",
    ]
    if next_a:
        lines.append(f"Next: {next_a}")
    return finalize_speak("\n".join(lines))


async def deep_research(query: str, *, conclude: bool = True) -> dict[str, Any]:
    """
    Always-on research entry: multi-lane search + weighted conclusion.
    """
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "empty query"}

    # strip leading "research " / "look up "
    q = re.sub(r"^(research|search for|look up|find out)\s+", "", q, flags=re.I).strip() or q

    bundle = await multi_lane_search(q)
    synthesized = await form_conclusion(q, bundle) if conclude else {}
    speak = speak_from_conclusion(q, synthesized, bundle) if conclude else "Research gathered."

    # flat related for backward compat with bookings/etc
    related = [
        {
            "title": h.get("title"),
            "url": h.get("url"),
            "href": h.get("url"),
            "snippet": h.get("snippet"),
            "tier": h.get("tier"),
            "weight": h.get("weight"),
        }
        for h in (bundle.get("hits") or [])
    ]
    abstract = str(synthesized.get("conclusion") or "")
    if not abstract and related:
        abstract = related[0].get("snippet") or ""

    top_source = ""
    for tier in ("docs", "reference", "other", "community"):
        tier_hits = (bundle.get("by_tier") or {}).get(tier) or []
        if tier_hits:
            top_source = tier_hits[0].get("url") or ""
            break

    out = {
        "ok": True,
        "engine": "multi_lane_weighted",
        "method": load_policy().get("method"),
        "query": q,
        "heading": q,
        "abstract": abstract,
        "brief": abstract,
        "source": top_source,
        "related": related,
        "by_tier": bundle.get("by_tier"),
        "lanes_queries": bundle.get("queries_used"),
        "conclusion": synthesized,
        "speak": speak,
        "at": _now(),
    }
    memory.remember_fact(
        f"Research[{synthesized.get('confidence','?')}]: {q} → {abstract[:160]}",
        tags=["research", "deep"],
    )
    return out


async def tool_research(query: str, *, deep: bool = True) -> dict[str, Any]:
    """Public tool — deep multi-lane research is ALWAYS on by default."""
    if deep or load_policy().get("always_on", True):
        return await deep_research(query, conclude=True)
    # unlikely path: single lane
    hits = await _search_raw(query)
    related = [{"title": h.get("title"), "url": h.get("url"), "snippet": h.get("snippet")} for h in hits]
    return {
        "ok": True,
        "engine": "single",
        "query": query,
        "heading": query,
        "abstract": (related[0].get("snippet") if related else "") or "",
        "source": related[0].get("url") if related else "",
        "related": related,
        "at": _now(),
    }
