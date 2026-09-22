from __future__ import annotations

"""ChatGPT / Cursor-style multi-turn session with rolling summarization.

Stores recent turns verbatim and folds older turns into `summary` when the
character budget is exceeded — so short follow-ups ("sure", "go for it") still
see what Voxoryl just said (quotes, offers, topics).
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voxoryl.config import settings

# Tuned for qwen3.5:4b with ~8k context: leave room for system + reply.
RECENT_CHAR_BUDGET = 7000
SUMMARY_MAX_CHARS = 2000
MSG_MAX_CHARS = 3500
KEEP_RECENT_MSGS = 10  # after compact, keep at least this many raw turns
HARD_MAX_MSGS = 40  # absolute cap before compact runs


def _path() -> Path:
    p = settings.voxoryl_data_dir / "chat_session.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load() -> dict[str, Any]:
    path = _path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data.setdefault("messages", [])
            data.setdefault("summary", "")
            return data
        except json.JSONDecodeError:
            pass
    return {"messages": [], "summary": "", "updated_at": None}


def save(data: dict[str, Any]) -> None:
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    _path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def clear() -> None:
    save({"messages": [], "summary": "", "updated_at": None})


def _chars(msgs: list[dict[str, Any]]) -> int:
    return sum(len(str(m.get("content") or "")) for m in msgs)


def append(role: str, content: str) -> None:
    text = (content or "").strip()
    if not text:
        return
    data = load()
    data.setdefault("messages", []).append({"role": role, "content": text[:MSG_MAX_CHARS]})
    # Soft safety cap; compact_if_needed does the real work
    data["messages"] = data["messages"][-HARD_MAX_MSGS:]
    save(data)


def has_recent_context(*, min_msgs: int = 1) -> bool:
    data = load()
    if (data.get("summary") or "").strip():
        return True
    return len(data.get("messages") or []) >= min_msgs


def last_assistant_snippet(n: int = 280) -> str:
    msgs = load().get("messages") or []
    for m in reversed(msgs):
        if m.get("role") == "assistant":
            return str(m.get("content") or "")[:n]
    return ""


def history_for_llm(*, limit: int = 20, budget: int = RECENT_CHAR_BUDGET) -> list[dict[str, str]]:
    """OpenAI-style messages: optional summary system note + recent turns."""
    data = load()
    out: list[dict[str, str]] = []
    summary = str(data.get("summary") or "").strip()
    if summary:
        out.append(
            {
                "role": "system",
                "content": (
                    "Ongoing conversation summary (earlier turns compressed — treat as truth):\n"
                    + summary[:SUMMARY_MAX_CHARS]
                ),
            }
        )

    msgs = data.get("messages") or []
    picked: list[dict[str, str]] = []
    used = 0
    for m in reversed(msgs[-limit:]):
        role = m.get("role") or "user"
        if role not in {"user", "assistant", "system"}:
            role = "user"
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        if used + len(content) > budget and picked:
            break
        picked.append({"role": role, "content": content})
        used += len(content)
    picked.reverse()
    out.extend(picked)
    return out


def history_text_for_router(max_chars: int = 1800) -> str:
    """Compact plain text for the intent router (follow-ups / 'sure go for it')."""
    data = load()
    parts: list[str] = []
    summary = str(data.get("summary") or "").strip()
    if summary:
        parts.append(f"[summary] {summary[:600]}")
    for m in (data.get("messages") or [])[-8:]:
        role = m.get("role") or "?"
        content = str(m.get("content") or "").replace("\n", " ").strip()
        if content:
            parts.append(f"{role}: {content[:320]}")
    blob = "\n".join(parts)
    return blob[-max_chars:] if len(blob) > max_chars else blob


async def compact_if_needed() -> bool:
    """If recent messages exceed the budget, summarize older turns into `summary`."""
    data = load()
    msgs: list[dict[str, Any]] = list(data.get("messages") or [])
    if _chars(msgs) <= RECENT_CHAR_BUDGET and len(msgs) <= HARD_MAX_MSGS - 2:
        return False
    if len(msgs) <= KEEP_RECENT_MSGS:
        return False

    keep = KEEP_RECENT_MSGS
    older = msgs[:-keep]
    recent = msgs[-keep:]
    prev = str(data.get("summary") or "").strip()
    blob = "\n".join(f"{m.get('role')}: {m.get('content')}" for m in older)

    from voxoryl.llm import chat_local

    summary = await chat_local(
        [
            {
                "role": "system",
                "content": (
                    "You compress chat history for a local assistant. "
                    "Preserve: names, preferences, book quotes already given, offers left open "
                    "('want another?'), decisions, and unfinished threads. "
                    "Drop filler greetings. Max ~200 words. Plain prose, no markdown."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Previous summary:\n{prev or '(none)'}\n\n"
                    f"Turns to fold in:\n{blob[:9000]}\n\n"
                    "Write the updated summary:"
                ),
            },
        ],
        temperature=0.2,
    )
    summary = (summary or "").strip()
    summary = summary[:SUMMARY_MAX_CHARS] if summary else prev[:SUMMARY_MAX_CHARS]
    data["summary"] = summary
    data["messages"] = recent
    save(data)
    return True
