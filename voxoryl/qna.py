from __future__ import annotations

"""
QnA interview sessions — Voxoryl asks personal/professional questions,
grows knowledge + lasting work-style preferences so you don't repeat yourself.
"""

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voxoryl.config import settings
from voxoryl.knowledge import knowledge
from voxoryl.llm import chat_local, parse_json_loose
from voxoryl.memory import memory


SEED_TOPICS = [
    {
        "id": "work_style",
        "question": "When I do work for you, do you want short clear answers, or a bit more detail? (I'll stay friendly and professional either way.)",
        "heading": "Work Style",
    },
    {
        "id": "priorities",
        "question": "What are you building or focusing on most right now (projects, business, learning)?",
        "heading": "Projects",
    },
    {
        "id": "comms",
        "question": "How do you prefer I contact or interrupt you — only when asked, or proactive nudges for deadlines?",
        "heading": "Preferences",
    },
    {
        "id": "tools",
        "question": "Which tools should I default to (FL Studio, Cursor, Obsidian, WhatsApp, Vercel, etc.)?",
        "heading": "Tools",
    },
    {
        "id": "brand",
        "question": "Any hard rules for writing or design you always want (tone, colors to avoid, brand name)?",
        "heading": "Brand",
    },
    {
        "id": "value_mode",
        "question": "When I shop or book things for you: optimize for (1) lowest real price, (2) balanced value, or (3) best quality even if costlier? Reply budget / balanced / quality — I won't assume.",
        "heading": "Preferences",
    },
    {
        "id": "neuro",
        "question": "To help you finish stuff: any of these fit? adhd / anxiety / low-energy / short-focus / none. I won't assume — this changes how short I talk and how I break tasks forever.",
        "heading": "Accessibility",
    },
    {
        "id": "personal",
        "question": "Anything personal I should remember (bike, city, schedule quirks) that helps me help you?",
        "heading": "Personal",
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_path() -> Path:
    path = settings.voxoryl_data_dir / "qna_session.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_session() -> dict[str, Any] | None:
    path = _session_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _save_session(session: dict[str, Any] | None) -> None:
    path = _session_path()
    if session is None:
        if path.exists():
            path.unlink()
        return
    path.write_text(json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8")


def work_style_block() -> str:
    """Inject into agent context — lasting 'how owner likes work done'."""
    data = memory.read()
    profile = data.get("profile") or {}
    style = profile.get("work_style") or {}
    prefs = profile.get("preferences") or []
    lines = ["Owner work-style (always obey):"]
    if not style and not prefs:
        lines.append("- (none yet — run a QnA session to learn)")
        return "\n".join(lines)
    for k, v in style.items():
        if v:
            lines.append(f"- {k}: {v}")
    for p in prefs[-12:]:
        lines.append(f"- preference: {p}")
    return "\n".join(lines)


def _apply_preference_hints(answer: str, topic_id: str) -> None:
    """Update structured work_style + preferences lists."""
    data = memory.read()
    profile = data.get("profile") or {}
    style = dict(profile.get("work_style") or {})
    a = answer.strip()
    al = a.lower()

    if topic_id == "work_style" or any(k in al for k in ("caveman", "blunt", "short", "detail")):
        if any(k in al for k in ("caveman", "blunt", "short", "terse", "point")):
            style["answers"] = "short clear friendly-professional"
        elif any(k in al for k in ("detail", "thorough", "explain")):
            style["answers"] = "more detail when useful; still friendly-professional"
        else:
            style["answers"] = a[:160]
    if topic_id == "comms" or "proactive" in al or "interrupt" in al:
        if any(k in al for k in ("only when asked", "don't nudge", "do not nudge", "quiet")):
            style["interrupts"] = "only when asked"
        elif "proactive" in al or "nudge" in al:
            style["interrupts"] = "proactive nudges ok for deadlines"
        else:
            style["interrupts"] = a[:160]
    if topic_id == "tools":
        style["default_tools"] = a[:200]
    if topic_id == "brand":
        style["brand_rules"] = a[:240]
    if topic_id == "value_mode" or any(k in al for k in ("budget", "balanced", "quality", "cheap", "premium", "luxury")):
        from voxoryl.prefs import set_value_mode

        if "budget" in al or "cheap" in al or "lowest" in al or "frugal" in al:
            set_value_mode("budget", note=a[:160])
        elif "quality" in al or "premium" in al or "luxury" in al or "best" in al:
            set_value_mode("quality", note=a[:160])
        elif "balanced" in al or "value" in al or "mid" in al:
            set_value_mode("balanced", note=a[:160])
        return  # set_value_mode already persisted
    if topic_id == "neuro" or any(
        k in al
        for k in (
            "adhd",
            "anxiety",
            "short-focus",
            "short focus",
            "low-energy",
            "low energy",
            "depression",
            "can't read",
            "cant read",
        )
    ):
        from voxoryl.neuro import set_profiles

        set_profiles(a, note=a[:160])
        return
    if topic_id == "priorities":
        memory.update_profile(projects=[a[:200]])

    memory.update_profile(work_style=style, preferences=[a[:200]])


async def _extract_facts(question: str, answer: str, heading: str) -> dict[str, Any]:
    owner = str((memory.read().get("profile") or {}).get("owner") or "Owner")
    raw = await chat_local(
        [
            {
                "role": "system",
                "content": (
                    "Turn a QnA answer into lasting knowledge. Return ONLY JSON:\n"
                    '{"heading":"Topic","facts":["third-person fact using owner name"],'
                    '"preference":"one reusable work preference or empty",'
                    '"follow_up":"one related next question or empty"}\n'
                    "facts must be concrete and reusable."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Owner: {owner}\nSuggested heading: {heading}\n"
                    f"Question: {question}\nAnswer: {answer}"
                ),
            },
        ],
        temperature=0.2,
    )
    parsed = parse_json_loose(raw) or {}
    facts = parsed.get("facts") or []
    if isinstance(facts, str):
        facts = [facts]
    facts = [str(f).strip() for f in facts if str(f).strip()]
    if not facts:
        facts = [f"{owner} said (QnA): {answer.strip()[:240]}"]
    h = str(parsed.get("heading") or heading or "Preferences").strip()
    logged = knowledge.append_facts(h, facts)
    pref = str(parsed.get("preference") or "").strip()
    if pref:
        memory.update_profile(preferences=[pref])
    return {
        "heading": h,
        "facts": facts,
        "preference": pref,
        "follow_up": str(parsed.get("follow_up") or "").strip(),
        "knowledge": logged,
    }


async def _next_question(session: dict[str, Any], follow_up: str = "") -> dict[str, Any]:
    asked_ids = {t.get("id") for t in session.get("asked") or []}
    if follow_up:
        return {"id": f"follow_{uuid.uuid4().hex[:6]}", "question": follow_up, "heading": "Preferences"}

    for topic in SEED_TOPICS:
        if topic["id"] not in asked_ids:
            return dict(topic)

    # LLM gap-fill from knowledge
    headings = ", ".join(knowledge.list_headings()[:30]) or "(none)"
    style = work_style_block()
    raw = await chat_local(
        [
            {
                "role": "system",
                "content": (
                    "Propose ONE new interview question to understand the owner's work style or life better. "
                    'Return ONLY JSON: {"question":"...","heading":"Short Topic","id":"kebab"}\n'
                    "Avoid repeating known facts. Prefer useful preferences over trivia."
                ),
            },
            {
                "role": "user",
                "content": f"Known headings: {headings}\n{style}\nAsked count: {len(asked_ids)}",
            },
        ],
        temperature=0.5,
    )
    parsed = parse_json_loose(raw) or {}
    q = str(parsed.get("question") or "").strip()
    if not q:
        q = "What's one thing I keep getting wrong when helping you — how should I do it instead?"
    return {
        "id": str(parsed.get("id") or f"gen_{uuid.uuid4().hex[:6]}"),
        "question": q,
        "heading": str(parsed.get("heading") or "Preferences"),
    }


async def start_session(*, rounds: int = 6, message: str = "") -> dict[str, Any]:
    existing = _load_session()
    if existing and existing.get("status") == "active":
        cur = existing.get("current") or {}
        return {
            "ok": True,
            "resumed": True,
            "session_id": existing.get("id"),
            "question": cur.get("question"),
            "progress": f"{len(existing.get('asked') or [])}/{existing.get('target_rounds')}",
            "speak": f"QnA already open. {cur.get('question')}",
            "hint": "Answer the question, or say 'stop qna'.",
        }

    session = {
        "id": uuid.uuid4().hex[:10],
        "status": "active",
        "target_rounds": max(3, min(int(rounds), 20)),
        "asked": [],
        "started_at": _now(),
        "note": message[:200],
    }
    current = await _next_question(session)
    session["current"] = current
    _save_session(session)
    return {
        "ok": True,
        "session_id": session["id"],
        "question": current["question"],
        "topic": current.get("heading"),
        "progress": f"0/{session['target_rounds']}",
        "speak": f"QnA started. Q1: {current['question']}",
        "hint": "Reply with your answer. Say 'skip' to skip, 'stop qna' to end.",
    }


async def answer_current(answer: str) -> dict[str, Any]:
    session = _load_session()
    if not session or session.get("status") != "active":
        return {
            "ok": False,
            "error": "no active QnA",
            "speak": "No QnA open. Say 'start qna' first.",
        }
    cur = session.get("current") or {}
    question = str(cur.get("question") or "")
    if not answer.strip() or answer.strip().lower() in {"skip", "next", "pass"}:
        session.setdefault("asked", []).append({**cur, "skipped": True, "at": _now()})
        follow = ""
    else:
        extracted = await _extract_facts(question, answer, str(cur.get("heading") or "Preferences"))
        _apply_preference_hints(answer, str(cur.get("id") or ""))
        session.setdefault("asked", []).append(
            {
                **cur,
                "answer": answer.strip()[:500],
                "facts": extracted.get("facts"),
                "at": _now(),
            }
        )
        memory.remember_fact(f"QnA: {answer.strip()[:160]}", tags=["qna", str(cur.get("heading") or "prefs")])
        follow = str(extracted.get("follow_up") or "")

    asked_n = len(session.get("asked") or [])
    target = int(session.get("target_rounds") or 6)
    if asked_n >= target:
        session["status"] = "done"
        session["ended_at"] = _now()
        _save_session(session)
        # archive
        arch = settings.voxoryl_data_dir / "qna_history.jsonl"
        with arch.open("a", encoding="utf-8") as f:
            f.write(json.dumps(session, ensure_ascii=False) + "\n")
        _save_session(None)
        return {
            "ok": True,
            "done": True,
            "answered": asked_n,
            "speak": f"QnA done ({asked_n} turns). Preferences saved — I'll use them from now on.",
            "work_style": (memory.read().get("profile") or {}).get("work_style"),
        }

    nxt = await _next_question(session, follow_up=follow if asked_n % 2 == 1 else "")
    session["current"] = nxt
    _save_session(session)
    return {
        "ok": True,
        "done": False,
        "saved": True,
        "progress": f"{asked_n}/{target}",
        "question": nxt["question"],
        "topic": nxt.get("heading"),
        "speak": f"Saved. Q{asked_n + 1}: {nxt['question']}",
    }


def stop_session() -> dict[str, Any]:
    session = _load_session()
    if not session:
        return {"ok": True, "speak": "No QnA was open."}
    n = len(session.get("asked") or [])
    session["status"] = "stopped"
    session["ended_at"] = _now()
    arch = settings.voxoryl_data_dir / "qna_history.jsonl"
    with arch.open("a", encoding="utf-8") as f:
        f.write(json.dumps(session, ensure_ascii=False) + "\n")
    _save_session(None)
    return {"ok": True, "answered": n, "speak": f"QnA stopped after {n} answers. Knowledge kept."}


def status_session() -> dict[str, Any]:
    session = _load_session()
    if not session:
        return {"ok": True, "active": False, "speak": "No active QnA.", "work_style": work_style_block()}
    cur = session.get("current") or {}
    return {
        "ok": True,
        "active": True,
        "session_id": session.get("id"),
        "progress": f"{len(session.get('asked') or [])}/{session.get('target_rounds')}",
        "question": cur.get("question"),
        "speak": f"Open QnA — {cur.get('question')}",
        "work_style": work_style_block(),
    }


async def tool_qna(
    action: str = "start",
    *,
    message: str = "",
    answer: str = "",
    rounds: int = 6,
) -> dict[str, Any]:
    action = (action or "start").lower().strip()
    msg = (message or "").strip()
    lower = msg.lower()

    if action in {"stop", "end", "cancel"} or lower in {"stop qna", "end qna", "cancel qna"}:
        return stop_session()
    if action in {"status", "current"}:
        return status_session()
    if action in {"start", "begin", "interview"}:
        m = re.search(r"(\d+)\s*(?:questions|rounds|q)", lower)
        n = int(m.group(1)) if m else rounds
        return await start_session(rounds=n, message=msg)

    # answer path
    session = _load_session()
    if session and session.get("status") == "active":
        text = answer or msg
        # strip leading "answer:" 
        text = re.sub(r"^(answer|a)\s*:\s*", "", text, flags=re.I).strip()
        if text:
            return await answer_current(text)
        return status_session()

    if action in {"answer", "reply"}:
        return await answer_current(answer or msg)

    return await start_session(rounds=rounds, message=msg)
