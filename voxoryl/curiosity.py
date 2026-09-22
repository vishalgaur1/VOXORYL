from __future__ import annotations

"""
Curiosity / growth — find genuinely interesting websites (owner-related or
knowledge-expanding). ALWAYS-ON ethos: enrich with new experiences, tastefully.
"""

import json
import random
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from voxoryl.config import settings
from voxoryl.knowledge import knowledge
from voxoryl.memory import memory


ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "setup" / "curiosity_sites.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def root() -> Path:
    p = settings.voxoryl_data_dir / "curiosity"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _state_path() -> Path:
    return root() / "state.json"


def _log_path() -> Path:
    return root() / "suggestions.jsonl"


def load_policy() -> dict[str, Any]:
    if POLICY_PATH.exists():
        try:
            return json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"always_interesting": [], "topics": {}, "blocked_patterns": []}


def load_state() -> dict[str, Any]:
    path = _state_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {
        "enabled": True,
        "last_nudge_at": None,
        "shown": [],
        "updated_at": None,
    }


def save_state(data: dict[str, Any]) -> None:
    data["updated_at"] = _now_iso()
    _state_path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _blocked(url: str) -> bool:
    u = (url or "").lower()
    for bad in load_policy().get("blocked_patterns") or []:
        if str(bad).lower() in u:
            return True
    return False


def _owner_topics() -> list[str]:
    topics: list[str] = []
    try:
        profile = memory.read().get("profile") or {}
        for p in profile.get("projects") or []:
            topics.append(str(p))
        style = profile.get("work_style") or {}
        if style.get("default_tools"):
            topics.append(str(style.get("default_tools")))
    except Exception:
        pass
    return topics[:8]


def _pick_seed(message: str = "") -> dict[str, Any]:
    policy = load_policy()
    lower = (message or "").lower()
    topic_map = policy.get("topics") or {}
    for topic, urls in topic_map.items():
        if topic.lower() in lower:
            return {"topic": topic, "urls": list(urls or [])}
    # owner-related: match project words
    for t in _owner_topics():
        tl = t.lower()
        for topic, urls in topic_map.items():
            if topic in tl or any(w in tl for w in topic.split()):
                return {"topic": topic, "urls": list(urls or []), "owner_link": t}
    pool = list(policy.get("always_interesting") or [])
    return {"topic": "general", "urls": pool}


def _persist_suggestion(item: dict[str, Any]) -> None:
    with _log_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
    try:
        knowledge.append_facts(
            "Curiosity",
            [f"{item.get('title') or 'Site'}: {item.get('url')} — {item.get('why') or ''}"[:300]],
        )
    except Exception:
        pass


async def discover(message: str = "", *, force_general: bool = False) -> dict[str, Any]:
    seed = _pick_seed("" if force_general else message)
    state = load_state()
    shown = set(state.get("shown") or [])
    candidates = [u for u in (seed.get("urls") or []) if u not in shown and not _blocked(u)]
    if not candidates:
        candidates = [u for u in (load_policy().get("always_interesting") or []) if not _blocked(u)]
    pick_url = random.choice(candidates) if candidates else ""
    research: dict[str, Any] = {}
    owner = _owner_topics()
    query = message.strip() if (message or "").strip() else (
        f"interesting educational website about {seed.get('topic')} "
        + (f"related to {owner[0]}" if owner else "for a curious lifelong learner")
    )
    try:
        from voxoryl.research import deep_research

        research = await deep_research(query + " genuine high-quality site", conclude=True)
    except Exception as exc:
        research = {"ok": False, "error": str(exc)}

    # pull URLs from research, prefer non-blocked
    found: list[str] = []
    blob = str(research.get("speak") or research.get("abstract") or "") + " " + json.dumps(
        research.get("sources") or research.get("results") or []
    )
    for url in re.findall(r"https?://[^\s\]\)\"']+", blob):
        if not _blocked(url):
            found.append(url.rstrip(".,)"))
    found = list(dict.fromkeys(found))[:6]
    if pick_url and pick_url not in found:
        found = [pick_url] + found

    why = str(research.get("speak") or research.get("abstract") or research.get("conclusion") or "").strip()
    if not why:
        why = f"A solid {seed.get('topic')} rabbit hole — worth ten curious minutes."
    title = urlparse(found[0]).netloc if found else (seed.get("topic") or "curiosity")
    item = {
        "at": _now_iso(),
        "url": found[0] if found else "",
        "alts": found[1:4],
        "title": title,
        "topic": seed.get("topic"),
        "why": why[:400],
        "owner_topics": owner,
    }
    if item["url"]:
        shown_list = list(shown)
        shown_list.append(item["url"])
        state["shown"] = shown_list[-80:]
        save_state(state)
        _persist_suggestion(item)
    speak = (
        f"Try this: {item['url']} — {why[:220]}"
        if item.get("url")
        else f"Curiosity nudge: {why[:280]}"
    )
    return {"ok": True, "suggestion": item, "research_ok": bool(research.get("ok")), "speak": speak}


def should_nudge() -> bool:
    state = load_state()
    if not state.get("enabled", True):
        return False
    last = state.get("last_nudge_at")
    if not last:
        return True
    try:
        prev = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        return (_now() - prev) >= timedelta(hours=20)
    except Exception:
        return True


async def daemon_curiosity_nudge() -> dict[str, Any]:
    if not should_nudge():
        return {"skipped": True, "reason": "already nudged today-ish or disabled"}
    result = await discover("", force_general=True)
    state = load_state()
    state["last_nudge_at"] = _now_iso()
    save_state(state)
    if result.get("speak"):
        memory.remember_fact(str(result["speak"])[:200], tags=["curiosity", "nudge"])
    return result


def curiosity_prompt_block() -> str:
    return (
        "CURIOSITY (always on, tasteful): Periodically enrich the owner with one genuine new "
        "experience, idea, or high-quality website — related to their work OR pure growth. "
        "Not spam. Not clickbait. Prefer depth over novelty theatre. "
        "If they say surprise me / teach me something new / interesting website — deliver one solid lead."
    )


async def tool_curiosity(action: str = "surprise", *, message: str = "") -> dict[str, Any]:
    action = (action or "surprise").lower().strip()
    lower = (message or "").lower()
    if action in {"disable", "off"} or "disable curiosity" in lower:
        state = load_state()
        state["enabled"] = False
        save_state(state)
        return {"ok": True, "speak": "Curiosity daily nudges off. Say enable curiosity anytime."}
    if action in {"enable", "on"} or "enable curiosity" in lower:
        state = load_state()
        state["enabled"] = True
        save_state(state)
        return {"ok": True, "speak": "Curiosity nudges on — one tasteful tip per day max."}
    if action in {"status"}:
        state = load_state()
        return {
            "ok": True,
            "enabled": state.get("enabled", True),
            "last_nudge_at": state.get("last_nudge_at"),
            "shown_count": len(state.get("shown") or []),
            "speak": f"Curiosity {'on' if state.get('enabled', True) else 'off'}; "
            f"{len(state.get('shown') or [])} sites shown so far.",
        }
    # surprise / teach / interesting website
    return await discover(message)
