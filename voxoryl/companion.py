from __future__ import annotations

"""
Companion / brutal-honest mirror from WhatsApp & Instagram chat exports.
ALL analysis runs on local Ollama only — exports never leave the machine.
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
from voxoryl.privacy import privacy_status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _inbox() -> Path:
    p = settings.voxoryl_data_dir / "chat_exports"
    p.mkdir(parents=True, exist_ok=True)
    (p / "inbox").mkdir(parents=True, exist_ok=True)
    (p / "reports").mkdir(parents=True, exist_ok=True)
    return p


# WhatsApp Android/iOS common lines: [DD/MM/YYYY, HH:MM:SS] Name: msg
# or DD/MM/YYYY, HH:MM - Name: msg
WA_PATTERNS = [
    re.compile(
        r"^\[?(\d{1,4}[\/\-.]\d{1,2}[\/\-.]\d{1,4}),?\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APMapm]{2})?\]?\s*[-–]?\s*([^:]+):\s*(.*)$"
    ),
    re.compile(r"^(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}),\s+\d{1,2}:\d{2}\s+-\s+([^:]+):\s*(.*)$"),
]


def parse_whatsapp_txt(text: str, *, owner_hint: str = "") -> dict[str, Any]:
    messages: list[dict[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or "end-to-end encrypted" in line.lower():
            continue
        matched = None
        for pat in WA_PATTERNS:
            m = pat.match(line)
            if m:
                matched = m
                break
        if matched:
            messages.append(
                {
                    "date": matched.group(1),
                    "sender": matched.group(2).strip(),
                    "text": matched.group(3).strip(),
                }
            )
        elif messages and not line.startswith("["):
            messages[-1]["text"] += " " + line
    senders = Counter(m["sender"] for m in messages if m.get("sender"))
    owner = owner_hint.strip()
    if not owner:
        # Heuristic: owner often most frequent in 1:1, or match memory owner name
        mem_owner = str((memory.read().get("profile") or {}).get("owner") or "")
        if mem_owner and any(mem_owner.lower() in s.lower() for s in senders):
            owner = next(s for s in senders if mem_owner.lower() in s.lower())
        elif senders:
            owner = senders.most_common(1)[0][0]
    return {
        "ok": True,
        "platform": "whatsapp",
        "count": len(messages),
        "senders": dict(senders),
        "owner_label": owner,
        "messages": messages,
    }


def parse_instagram_json(data: Any, *, owner_hint: str = "") -> dict[str, Any]:
    """Instagram 'your_instagram_activity' message JSON variants."""
    messages: list[dict[str, str]] = []
    if isinstance(data, dict):
        # common: {"messages":[{"sender_name","content","timestamp_ms"}]}
        raw = data.get("messages") or data.get("chats") or []
        if isinstance(raw, dict):
            raw = raw.get("messages") or []
        for item in raw:
            if not isinstance(item, dict):
                continue
            messages.append(
                {
                    "date": str(item.get("timestamp_ms") or item.get("created_at") or ""),
                    "sender": str(item.get("sender_name") or item.get("sender") or "?"),
                    "text": str(item.get("content") or item.get("text") or ""),
                }
            )
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and (item.get("content") or item.get("text")):
                messages.append(
                    {
                        "date": str(item.get("timestamp_ms") or ""),
                        "sender": str(item.get("sender_name") or "?"),
                        "text": str(item.get("content") or item.get("text") or ""),
                    }
                )
    senders = Counter(m["sender"] for m in messages if m.get("sender"))
    owner = owner_hint or (senders.most_common(1)[0][0] if senders else "")
    return {
        "ok": True,
        "platform": "instagram",
        "count": len(messages),
        "senders": dict(senders),
        "owner_label": owner,
        "messages": messages,
    }


def load_export_file(path: Path, *, owner_hint: str = "") -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {"ok": False, "error": "invalid json"}
        return parse_instagram_json(data, owner_hint=owner_hint)
    # default: whatsapp txt / generic
    return parse_whatsapp_txt(raw, owner_hint=owner_hint)


def _sample_for_model(parsed: dict[str, Any], *, max_chars: int = 12000) -> str:
    owner = parsed.get("owner_label") or ""
    lines = []
    msgs = parsed.get("messages") or []
    # Prefer recent + spread
    step = max(1, len(msgs) // 400)
    for m in msgs[::step]:
        who = "YOU" if owner and m.get("sender") == owner else m.get("sender")
        lines.append(f"{who}: {m.get('text')}")
        if sum(len(x) for x in lines) > max_chars:
            break
    return "\n".join(lines[-500:])


ANALYZE_SYSTEM = """You are a brutal-honest local companion — not a yes-man, not cruel for sport.
Analyze chat excerpts. Return ONLY JSON:
{
  "person_summary": "who the owner seems to be in relationships/comms (2-4 sentences)",
  "how_you_talk": ["pattern"],
  "strengths": ["..."],
  "blind_spots": ["hard truths about YOU"],
  "other_party": {
    "name_or_role": "",
    "their_patterns": ["..."],
    "their_share_of_problems": ["..."]
  },
  "relationship_read": {
    "likely_fracture_points": ["..."],
    "what_you_should_own": ["..."],
    "what_you_should_not_own": ["stand your ground"],
    "changes_worth_trying": ["concrete behavior change"]
  },
  "companion_advice": ["short actionable"],
  "caveat": "This is pattern-reading from text, not clinical diagnosis."
}
Rules:
- Be direct. No sugarcoating. No moral grandstanding.
- Split ownership fairly: their problems vs yours.
- Never invent events not supported by the chat sample.
- If chats are thin, say so and lower confidence.
- If content suggests crisis/self-harm, add companion_advice to seek real human help / local emergency resources — do not dig into methods.
"""


async def analyze_chats(parsed: dict[str, Any], *, focus: str = "") -> dict[str, Any]:
    privacy_status()  # assert local path mindset
    sample = _sample_for_model(parsed)
    if len(sample) < 80:
        return {"ok": False, "error": "not enough chat text", "speak": "Export looks empty — drop a fuller WhatsApp/Insta export."}

    owner = parsed.get("owner_label") or "Owner"
    raw = await chat_local(
        [
            {"role": "system", "content": ANALYZE_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Owner label in chats: {owner}\n"
                    f"Platform: {parsed.get('platform')}\n"
                    f"Message count: {parsed.get('count')}\n"
                    f"Senders: {parsed.get('senders')}\n"
                    f"Focus: {focus or 'overall personality + relationship dynamics'}\n\n"
                    f"CHAT SAMPLE (local only):\n{sample}"
                ),
            },
        ],
        temperature=0.35,
        kind="companion",
        force_local=True,
    )
    parsed_out = parse_json_loose(raw) or {"raw": raw[:2000]}
    report_id = uuid.uuid4().hex[:10]
    report = {
        "id": report_id,
        "created_at": _now(),
        "platform": parsed.get("platform"),
        "message_count": parsed.get("count"),
        "owner_label": owner,
        "focus": focus,
        "local_only": True,
        "analysis": parsed_out,
    }
    path = _inbox() / "reports" / f"companion_{report_id}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # Persist short facts locally
    facts = []
    if parsed_out.get("person_summary"):
        facts.append(str(parsed_out["person_summary"])[:300])
    for b in (parsed_out.get("blind_spots") or [])[:3]:
        facts.append(f"Blind spot (from chats): {b}")
    for c in (parsed_out.get("companion_advice") or [])[:3]:
        facts.append(f"Companion note: {c}")
    if facts:
        knowledge.append_facts("Companion Insights", facts)
        memory.remember_fact(f"Companion analysis {report_id} from {parsed.get('platform')} chats", tags=["companion", "local"])

    md = _render_markdown(report)
    md_path = path.with_suffix(".md")
    md_path.write_text(md, encoding="utf-8")

    summary = str(parsed_out.get("person_summary") or "Analysis saved.")[:280]
    return {
        "ok": True,
        "report_id": report_id,
        "path": str(path.resolve()),
        "markdown": str(md_path.resolve()),
        "local_only": True,
        "analysis": parsed_out,
        "speak": f"Brutal read saved locally. {summary}",
        "privacy": "Chat export + analysis never left this PC (local Ollama only).",
    }


def _render_markdown(report: dict[str, Any]) -> str:
    a = report.get("analysis") or {}
    lines = [
        f"# Companion report — {report.get('id')}",
        "",
        f"_Local only · {report.get('platform')} · {report.get('message_count')} msgs_",
        "",
        "## Who you seem to be",
        str(a.get("person_summary") or ""),
        "",
        "## How you talk",
        *[f"- {x}" for x in (a.get("how_you_talk") or [])],
        "",
        "## Strengths",
        *[f"- {x}" for x in (a.get("strengths") or [])],
        "",
        "## Blind spots (yours)",
        *[f"- {x}" for x in (a.get("blind_spots") or [])],
        "",
        "## The other person",
        f"Role: {(a.get('other_party') or {}).get('name_or_role')}",
        *[f"- {x}" for x in ((a.get("other_party") or {}).get("their_patterns") or [])],
        "",
        "### Their share",
        *[f"- {x}" for x in ((a.get("other_party") or {}).get("their_share_of_problems") or [])],
        "",
        "## Relationship read",
        "### Fracture points",
        *[f"- {x}" for x in ((a.get("relationship_read") or {}).get("likely_fracture_points") or [])],
        "### Own this",
        *[f"- {x}" for x in ((a.get("relationship_read") or {}).get("what_you_should_own") or [])],
        "### Stand your ground",
        *[f"- {x}" for x in ((a.get("relationship_read") or {}).get("what_you_should_not_own") or [])],
        "### Changes to try",
        *[f"- {x}" for x in ((a.get("relationship_read") or {}).get("changes_worth_trying") or [])],
        "",
        "## Companion advice",
        *[f"- {x}" for x in (a.get("companion_advice") or [])],
        "",
        f"_{a.get('caveat') or 'Pattern-reading only. Not a clinical diagnosis.'}_",
        "",
    ]
    return "\n".join(lines)


async def tool_companion(
    action: str = "analyze",
    *,
    path: str = "",
    message: str = "",
    focus: str = "",
    owner_hint: str = "",
) -> dict[str, Any]:
    action = (action or "analyze").lower().strip()
    if action in {"privacy", "status"}:
        return privacy_status()

    if action in {"inbox", "list"}:
        files = sorted((_inbox() / "inbox").glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        reports = sorted((_inbox() / "reports").glob("companion_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        return {
            "ok": True,
            "inbox": [str(p.resolve()) for p in files[:20]],
            "reports": [str(p.resolve()) for p in reports[:10]],
            "hint": "Drop WhatsApp .txt or Instagram .json into data/chat_exports/inbox/",
            "speak": f"{len(files)} exports in inbox, {len(reports)} reports.",
            "local_only": True,
        }

    # Resolve file
    file_path: Path | None = Path(path) if path else None
    if file_path and not file_path.exists():
        file_path = None
    if not file_path:
        # newest in inbox
        candidates = sorted((_inbox() / "inbox").glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        # also allow absolute path mentioned in message
        m = re.search(r'([A-Za-z]:\\[^\s"\']+\.(?:txt|json)|/[^\s"\']+\.(?:txt|json))', message or "")
        if m:
            file_path = Path(m.group(1))
        elif candidates:
            file_path = candidates[0]

    if not file_path or not file_path.exists():
        return {
            "ok": False,
            "error": "no export file",
            "hint": "Export WhatsApp chat (without media) or Instagram messages JSON → put in data/chat_exports/inbox/",
            "speak": "Drop a chat export into data/chat_exports/inbox/ then say 'analyze my chats'.",
            "local_only": True,
        }

    parsed = load_export_file(file_path, owner_hint=owner_hint)
    if not parsed.get("ok"):
        return parsed
    focus_text = focus or message
    # strip command words
    focus_text = re.sub(
        r"(analyze|my|chats?|whatsapp|instagram|export|companion|therapist)",
        " ",
        focus_text,
        flags=re.I,
    ).strip()
    result = await analyze_chats(parsed, focus=focus_text)
    result["source"] = str(file_path.resolve())
    return result
