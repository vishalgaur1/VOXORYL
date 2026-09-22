from __future__ import annotations

"""
Sale / price watch — watch Amazon, OLX, or a URL for price drops / listings.
Stores watches in data/sale_watches.json; daemon ticks check via research/fetch.
Genuine marketplace domains preferred.
"""

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from voxoryl.config import settings
from voxoryl.knowledge import knowledge
from voxoryl.memory import memory


GENUINE_HOSTS = {
    "amazon.com",
    "amazon.in",
    "amazon.co.uk",
    "www.amazon.com",
    "www.amazon.in",
    "www.amazon.co.uk",
    "olx.in",
    "www.olx.in",
    "olx.com",
    "www.olx.com",
    "flipkart.com",
    "www.flipkart.com",
    "ebay.com",
    "www.ebay.com",
    "ebay.in",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path():
    p = settings.voxoryl_data_dir / "sale_watches.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load() -> dict[str, Any]:
    path = _path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"watches": [], "alerts": [], "updated_at": None}


def save(data: dict[str, Any]) -> None:
    data["updated_at"] = _now()
    _path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _host_ok(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return False
    if not host:
        return False
    if host in GENUINE_HOSTS:
        return True
    # allow subdomains of known brands
    return any(host.endswith("." + h.removeprefix("www.")) or host == h.removeprefix("www.") for h in GENUINE_HOSTS)


def detect_site(message: str) -> str:
    lower = (message or "").lower()
    if "olx" in lower:
        return "olx"
    if "flipkart" in lower:
        return "flipkart"
    if "ebay" in lower:
        return "ebay"
    if "amazon" in lower:
        return "amazon"
    m = re.search(r"https?://[^\s]+", message or "")
    if m:
        host = urlparse(m.group(0)).netloc.lower()
        for name in ("amazon", "olx", "flipkart", "ebay"):
            if name in host:
                return name
        return "url"
    return "amazon"


def _extract_url(message: str) -> str:
    m = re.search(r"https?://[^\s<>\"]+", message or "")
    return m.group(0).rstrip(".,)") if m else ""


def _extract_query(message: str) -> str:
    raw = (message or "").strip()
    raw = re.sub(r"https?://[^\s]+", "", raw)
    raw = re.sub(
        r"\b(watch this on|watch on|tell me when|when (?:it'?s|its) on sale|check olx for|"
        r"price watch|sale watch|alert me when|notify me when|on sale|goes on sale)\b",
        " ",
        raw,
        flags=re.I,
    )
    raw = re.sub(r"\b(amazon|olx|flipkart|ebay)\b", " ", raw, flags=re.I)
    return re.sub(r"\s+", " ", raw).strip(" :-")[:160]


def add_watch(message: str, *, target_price: float | None = None) -> dict[str, Any]:
    url = _extract_url(message)
    site = detect_site(message)
    query = _extract_query(message) or "item"
    if url and not _host_ok(url):
        return {
            "ok": False,
            "error": "ungenuine_host",
            "speak": (
                "I only watch genuine marketplaces (Amazon, OLX, Flipkart, eBay) "
                "or their official product URLs. Paste a real product link?"
            ),
        }

    # Optional target: "under 5000" / "below $50"
    if target_price is None:
        m = re.search(r"(?:under|below|less than|<)\s*(?:rs\.?|₹|\$|usd)?\s*([\d,]+(?:\.\d+)?)", message or "", re.I)
        if m:
            try:
                target_price = float(m.group(1).replace(",", ""))
            except ValueError:
                target_price = None

    watch = {
        "id": str(uuid.uuid4())[:8],
        "query": query,
        "site": site,
        "url": url,
        "target_price": target_price,
        "last_price": None,
        "last_check": None,
        "last_status": "pending",
        "last_snippet": "",
        "hit": False,
        "active": True,
        "created_at": _now(),
        "source": (message or "")[:240],
    }
    data = load()
    data.setdefault("watches", []).append(watch)
    data["watches"] = data["watches"][-100:]
    save(data)

    knowledge.append_facts(
        "Sale Watches",
        [f"Watching '{query}' on {site}" + (f" under {target_price}" if target_price else "") + "."],
    )
    memory.remember_fact(f"Sale watch: {query} @ {site}", tags=["sale_watch", site])

    speak = (
        f"Watching '{query}' on {site}"
        + (f" — alert under {target_price}" if target_price else " for a sale / better price")
        + ". I'll check periodically and note hits."
    )
    return {"ok": True, "watch": watch, "speak": speak}


def list_watches(*, active_only: bool = True) -> dict[str, Any]:
    data = load()
    watches = [w for w in (data.get("watches") or []) if (not active_only or w.get("active"))]
    if not watches:
        return {
            "ok": True,
            "watches": [],
            "speak": "No active sale watches. Say 'watch this on amazon …' or paste a product URL.",
        }
    bits = [
        f"{w.get('query')}@{w.get('site')} [{w.get('id')}]"
        + (" HIT" if w.get("hit") else "")
        for w in watches[:8]
    ]
    return {"ok": True, "watches": watches, "speak": "Watching: " + "; ".join(bits) + "."}


def remove_watch(watch_id: str = "", query_substr: str = "") -> dict[str, Any]:
    data = load()
    hit = None
    for w in data.get("watches") or []:
        if watch_id and w.get("id") == watch_id:
            hit = w
            break
        if query_substr and query_substr.lower() in str(w.get("query") or "").lower() and w.get("active"):
            hit = w
            break
    if not hit:
        return {"ok": False, "error": "not found", "speak": "No matching watch."}
    hit["active"] = False
    hit["stopped_at"] = _now()
    save(data)
    return {"ok": True, "watch": hit, "speak": f"Stopped watching '{hit.get('query')}'."}


def status_speakable() -> dict[str, Any]:
    data = load()
    active = [w for w in (data.get("watches") or []) if w.get("active")]
    hits = [w for w in active if w.get("hit")]
    alerts = (data.get("alerts") or [])[-5:]
    if hits:
        bits = [f"{w.get('query')} on {w.get('site')}" for w in hits[:3]]
        speak = "Sale hits: " + "; ".join(bits) + "."
    elif active:
        speak = f"{len(active)} watch(es) active — no confirmed sale hits yet."
    else:
        speak = "No sale watches running."
    return {"ok": True, "active": len(active), "hits": hits, "alerts": alerts, "speak": speak}


async def check_watch(watch: dict[str, Any]) -> dict[str, Any]:
    """Fetch via research search — genuine hosts preferred."""
    from voxoryl.research import tool_research

    query = watch.get("query") or ""
    site = watch.get("site") or "amazon"
    url = watch.get("url") or ""
    if url:
        search_q = f"price {query} site:{urlparse(url).netloc}" if query else f"current price {url}"
    else:
        domain = {
            "amazon": "amazon.in OR amazon.com",
            "olx": "olx.in",
            "flipkart": "flipkart.com",
            "ebay": "ebay.com",
        }.get(site, site)
        search_q = f"{query} price sale {domain}"

    try:
        result = await tool_research(search_q, deep=True)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "watch_id": watch.get("id")}

    findings = result.get("related") or result.get("findings") or result.get("results") or []
    snippet = str(result.get("abstract") or result.get("brief") or "")[:280]
    price = None
    genuine = False
    for f in findings[:8]:
        furl = str(f.get("url") or f.get("href") or "")
        if furl and _host_ok(furl):
            genuine = True
        snip = str(f.get("snippet") or f.get("abstract") or f.get("title") or "")
        if not snippet and snip:
            snippet = snip[:280]
        m = re.search(r"(?:₹|rs\.?|\$|usd)\s*([\d,]+(?:\.\d+)?)", snip + " " + snippet, re.I)
        if m and price is None:
            try:
                price = float(m.group(1).replace(",", ""))
            except ValueError:
                pass

    target = watch.get("target_price")
    hit = False
    if target is not None and price is not None and price <= float(target):
        hit = True
    lower_snip = (snippet + " " + str(result.get("speak") or "")).lower()
    if any(k in lower_snip for k in ("on sale", "% off", "deal", "discount", "lightning deal")):
        if target is None or (price is not None and price <= float(target)):
            hit = True
    # Known marketplace site watches count as genuine intent
    if site in {"amazon", "olx", "flipkart", "ebay"} or (url and _host_ok(url)):
        genuine = True
    if hit and findings and not genuine:
        hit = False
        status = "ungenuine_skip"
    else:
        status = "hit" if hit else ("checked" if findings else "empty")

    watch["last_check"] = _now()
    watch["last_price"] = price
    watch["last_snippet"] = snippet
    watch["last_status"] = status
    if hit:
        watch["hit"] = True

    return {
        "ok": True,
        "watch": watch,
        "hit": hit,
        "price": price,
        "snippet": snippet,
        "genuine": genuine,
        "research": {"speak": result.get("speak"), "n": len(findings)},
    }


async def tick_all() -> dict[str, Any]:
    """Daemon entry — check all active watches, notify on hits."""
    from voxoryl.tools import tool_notes

    data = load()
    active = [w for w in (data.get("watches") or []) if w.get("active")]
    checked = []
    new_hits = []
    for w in active:
        try:
            r = await check_watch(w)
            checked.append(r)
            # persist mutated watch
            for i, existing in enumerate(data["watches"]):
                if existing.get("id") == w.get("id"):
                    data["watches"][i] = w
                    break
            if r.get("hit"):
                new_hits.append(w)
                alert = {
                    "at": _now(),
                    "watch_id": w.get("id"),
                    "query": w.get("query"),
                    "site": w.get("site"),
                    "price": w.get("last_price"),
                    "snippet": w.get("last_snippet"),
                }
                data.setdefault("alerts", []).append(alert)
                data["alerts"] = data["alerts"][-50:]
                memory.remember_fact(
                    f"Sale hit: {w.get('query')} on {w.get('site')}"
                    + (f" ~{w.get('last_price')}" if w.get("last_price") else ""),
                    tags=["sale_watch", "hit"],
                )
                knowledge.append_facts(
                    "Sale Watches",
                    [
                        f"HIT: '{w.get('query')}' on {w.get('site')}"
                        + (f" ~{w.get('last_price')}" if w.get("last_price") else "")
                        + "."
                    ],
                )
                try:
                    await tool_notes(
                        f"Sale watch hit: {w.get('query')} @ {w.get('site')}\n"
                        f"Price: {w.get('last_price')}\n{w.get('last_snippet')}",
                        title=f"Sale hit — {w.get('query')}",
                    )
                except Exception:
                    pass
        except Exception as exc:
            checked.append({"ok": False, "error": str(exc), "watch_id": w.get("id")})

    save(data)
    speak = status_speakable()["speak"]
    if new_hits:
        speak = f"{len(new_hits)} sale hit(s). " + speak
    return {"ok": True, "checked": len(checked), "hits": len(new_hits), "speak": speak, "results": checked}


async def tool_sale_watch(
    action: str = "add",
    message: str = "",
    watch_id: str = "",
) -> dict[str, Any]:
    act = (action or "add").lower()
    lower = (message or "").lower()

    if act in {"list", "status"} or "list watches" in lower or "sale status" in lower:
        if "status" in act or "status" in lower:
            return status_speakable()
        return list_watches()

    if act in {"check", "tick", "run"} or "check sale" in lower or "check watches" in lower:
        return await tick_all()

    if act in {"stop", "remove", "delete"} or "stop watching" in lower or "remove watch" in lower:
        tid = watch_id
        if not tid:
            m = re.search(r"\b([a-f0-9]{8})\b", message or "")
            tid = m.group(1) if m else ""
        substr = re.sub(r".*?(?:stop watching|remove watch)\s*", "", message or "", flags=re.I).strip()
        return remove_watch(watch_id=tid, query_substr=substr)

    # add / watch
    return add_watch(message)
