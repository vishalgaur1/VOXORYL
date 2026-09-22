from __future__ import annotations

"""
Cold memory: settled facts Voxoryl knows but must NOT volunteer
unless the user is clearly talking about that topic.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voxoryl.config import settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path() -> Path:
    p = settings.voxoryl_data_dir / "cold_memory.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load() -> dict[str, Any]:
    path = _path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"items": [], "updated_at": None}


def save(data: dict[str, Any]) -> None:
    data["updated_at"] = _now()
    _path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def put(
    topic: str,
    fact: str,
    *,
    triggers: list[str] | None = None,
    settled: bool = True,
    ask_once: bool = False,
) -> dict[str, Any]:
    data = load()
    item = {
        "id": f"c{len(data.get('items') or []) + 1}",
        "topic": topic,
        "fact": fact[:400],
        "triggers": [t.lower() for t in (triggers or [topic])],
        "settled": settled,
        "ask_once": ask_once,
        "asked": False,
        "at": _now(),
    }
    # replace same topic+similar fact
    items = [
        x
        for x in (data.get("items") or [])
        if not (
            str(x.get("topic") or "").lower() == topic.lower()
            and str(x.get("fact") or "").lower()[:40] == fact.lower()[:40]
        )
    ]
    items.append(item)
    data["items"] = items[-80:]
    save(data)
    return {"ok": True, "item": item}


def mark_settled(topic: str, *, note: str = "") -> dict[str, Any]:
    data = load()
    n = 0
    for item in data.get("items") or []:
        if topic.lower() in str(item.get("topic") or "").lower() or topic.lower() in str(item.get("fact") or "").lower():
            item["settled"] = True
            item["ask_once"] = False
            if note:
                item["fact"] = note[:400]
            n += 1
    save(data)
    return {"ok": True, "updated": n}


def relevant_for(message: str) -> list[dict[str, Any]]:
    lower = (message or "").lower()
    out = []
    for item in load().get("items") or []:
        triggers = item.get("triggers") or []
        if any(t in lower for t in triggers if t):
            out.append(item)
    return out


def prompt_block(message: str) -> str:
    hits = relevant_for(message)
    if not hits:
        return (
            "COLD MEMORY: none on-topic. "
            "Do NOT invent or volunteer bike/service/ownership trivia.\n"
        )
    lines = [
        "COLD MEMORY (only use if the user is clearly talking about this topic):",
    ]
    for h in hits[:6]:
        settled = "SETTLED — do not ask again" if h.get("settled") else "may gently confirm once"
        lines.append(f"- [{h.get('topic')}] {h.get('fact')} ({settled})")
    lines.append("Never bring these up in greetings or unrelated chat.")
    return "\n".join(lines) + "\n"


def migrate_bike_service_from_hot() -> dict[str, Any]:
    """Move service-date spam out of knowledge.md into cold memory."""
    from voxoryl.knowledge import knowledge

    text = knowledge.read()
    removed = []
    # Drop service-appointment bullets under Bikes / General
    patterns = [
        r"(?m)^-\s+\*\*[^*]+\*\*\s+[—\-]\s+.*(?:1st service|first service|service of (?:his |the )?bike|service appointment|serviced today).*\n?",
        r"(?m)^-\s+.*(?:1st service|first service|service appointment|Triumph as an example for his service).*\n?",
    ]
    new = text
    for pat in patterns:
        for m in re.finditer(pat, new, flags=re.I):
            removed.append(m.group(0).strip())
        new = re.sub(pat, "", new, flags=re.I)

    if new != text:
        knowledge.path.write_text(new.rstrip() + "\n", encoding="utf-8")

    put(
        "bike",
        "Owner has a Triumph 400 XC. Service topic is SETTLED (done / was an example). "
        "If they ask about the bike, answer factually. Do NOT offer service reminders or ask about service again.",
        triggers=["bike", "triumph", "motorcycle", "400 xc", "service"],
        settled=True,
        ask_once=False,
    )
    return {"ok": True, "removed": removed[:20], "removed_n": len(removed), "cold": True}
