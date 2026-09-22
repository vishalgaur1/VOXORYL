from __future__ import annotations

"""
Daily todos WITH deadlines — no deadline, no task.
Motivational quick wins for wellbeing check-ins.
"""

import json
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from voxoryl.config import settings
from voxoryl.knowledge import knowledge
from voxoryl.memory import memory


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _local_today() -> str:
    return _now().astimezone().strftime("%Y-%m-%d")


def _path():
    p = settings.voxoryl_data_dir / "todos.json"
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
    data["updated_at"] = _now().isoformat()
    _path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


DEADLINE_PATTERNS = [
    re.compile(r"\bby\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", re.I),
    re.compile(r"\b(?:deadline|due)\s*:?\s*([^\n,;]+)", re.I),
    re.compile(r"\btoday\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", re.I),
    re.compile(r"\b(?:before|until)\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", re.I),
    re.compile(r"\bin\s+(\d+)\s*(h|hr|hours|m|min|minutes)\b", re.I),
    re.compile(r"\b(eod|end of day|tonight|noon|this evening)\b", re.I),
]


def parse_deadline(text: str, *, default_hours: float | None = 4.0) -> tuple[str | None, str | None]:
    """
    Returns (iso_deadline, human_label) or (None, ask_reason).
    """
    raw = (text or "").strip()
    lower = raw.lower()
    local = _now().astimezone()

    for pat in DEADLINE_PATTERNS:
        m = pat.search(raw)
        if not m:
            continue
        g = m.group(1) if m.lastindex else m.group(0)
        g_l = g.lower().strip()
        if g_l in {"eod", "end of day", "tonight"}:
            dt = local.replace(hour=21, minute=0, second=0, microsecond=0)
            if dt < local:
                dt = dt + timedelta(days=1)
            return dt.astimezone(timezone.utc).isoformat(), "today 9pm"
        if g_l in {"noon"}:
            dt = local.replace(hour=12, minute=0, second=0, microsecond=0)
            if dt < local:
                dt += timedelta(days=1)
            return dt.astimezone(timezone.utc).isoformat(), "noon"
        if g_l in {"this evening"}:
            dt = local.replace(hour=19, minute=0, second=0, microsecond=0)
            if dt < local:
                dt += timedelta(days=1)
            return dt.astimezone(timezone.utc).isoformat(), "this evening"
        # relative in N h/m
        if m.re.pattern.startswith(r"\bin\s+"):
            n = int(m.group(1))
            unit = m.group(2).lower()
            delta = timedelta(hours=n) if unit.startswith("h") else timedelta(minutes=n)
            dt = local + delta
            return dt.astimezone(timezone.utc).isoformat(), f"in {n}{unit[0]}"
        # clock time
        tm = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", g_l, re.I)
        if tm:
            h = int(tm.group(1))
            mi = int(tm.group(2) or 0)
            ap = (tm.group(3) or "").lower()
            if ap == "pm" and h < 12:
                h += 12
            if ap == "am" and h == 12:
                h = 0
            if not ap and h <= 7:
                # bare small hour → assume evening if morning already passed weirdly; keep as-is
                pass
            dt = local.replace(hour=min(h, 23), minute=mi, second=0, microsecond=0)
            if dt < local:
                dt += timedelta(days=1)
            return dt.astimezone(timezone.utc).isoformat(), g.strip()

    if "today" in lower and "tomorrow" not in lower:
        dt = local.replace(hour=20, minute=0, second=0, microsecond=0)
        if dt < local:
            dt = local + timedelta(hours=2)
        return dt.astimezone(timezone.utc).isoformat(), "today"
    if "tomorrow" in lower:
        dt = (local + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
        return dt.astimezone(timezone.utc).isoformat(), "tomorrow"

    if default_hours is not None and any(
        k in lower for k in ("add todo", "todo:", "task:", "remind me", "deadline")
    ):
        # still require explicit — return ask
        pass
    return None, None


def ask_deadline(title: str) -> dict[str, Any]:
    q = (
        f"Task '{title}' needs a deadline or it won't get done. "
        "When? e.g. by 6pm / in 2h / EOD / tomorrow."
    )
    return {
        "ok": True,
        "needs_deadline": True,
        "pending_title": title,
        "question": q,
        "speak": q,
    }


def add_todo(title: str, *, deadline_iso: str | None = None, deadline_label: str = "", source: str = "") -> dict[str, Any]:
    title = (title or "").strip()[:160]
    if not title:
        return {"ok": False, "error": "empty title"}
    if not deadline_iso:
        iso, label = parse_deadline(title)
        if iso:
            deadline_iso, deadline_label = iso, label or ""
            # strip deadline fragment from title lightly
            title = re.sub(r"\bby\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b", "", title, flags=re.I).strip(" -:")
            title = re.sub(r"\b(?:deadline|due)\s*:?\s*[^\n,;]+", "", title, flags=re.I).strip(" -:") or title
    if not deadline_iso:
        return ask_deadline(title)

    data = load()
    item = {
        "id": str(uuid.uuid4())[:8],
        "title": title,
        "deadline": deadline_iso,
        "deadline_label": deadline_label or deadline_iso[:16],
        "created_at": _now().isoformat(),
        "day": _local_today(),
        "done": False,
        "source": source[:80],
    }
    data.setdefault("items", []).append(item)
    data["items"] = data["items"][-200:]
    save(data)
    knowledge.append_facts("Todos", [f"Todo '{title}' due {item['deadline_label']}."])
    memory.remember_fact(f"Todo: {title} by {item['deadline_label']}", tags=["todo", "deadline"])
    return {
        "ok": True,
        "item": item,
        "speak": f"Locked: '{title}' — deadline {item['deadline_label']}. No deadline, no task.",
    }


def complete(todo_id: str = "", title_substr: str = "") -> dict[str, Any]:
    data = load()
    hit = None
    for it in data.get("items") or []:
        if todo_id and it.get("id") == todo_id:
            hit = it
            break
        if title_substr and title_substr.lower() in str(it.get("title") or "").lower() and not it.get("done"):
            hit = it
            break
    if not hit:
        return {"ok": False, "error": "todo not found", "speak": "No matching open todo."}
    hit["done"] = True
    hit["done_at"] = _now().isoformat()
    save(data)
    open_n = sum(1 for x in data["items"] if not x.get("done") and x.get("day") == _local_today())
    return {
        "ok": True,
        "item": hit,
        "speak": f"Done: '{hit.get('title')}'. {open_n} left today — keep the streak.",
    }


def list_todos(*, day: str | None = None, include_done: bool = False) -> dict[str, Any]:
    data = load()
    day = day or _local_today()
    items = [
        it
        for it in (data.get("items") or [])
        if (it.get("day") == day or (it.get("deadline") or "").startswith(day))
        and (include_done or not it.get("done"))
    ]
    # also overdue open
    now = _now().isoformat()
    overdue = [
        it
        for it in (data.get("items") or [])
        if not it.get("done") and str(it.get("deadline") or "") < now
    ]
    lines = []
    for it in sorted(items, key=lambda x: str(x.get("deadline") or "")):
        lines.append(f"- {it.get('title')} (due {it.get('deadline_label')}) [{it.get('id')}]")
    speak = (
        f"Today: {len(items)} open. " + (" ".join(lines[:5]) if lines else "No open todos — add one WITH a deadline.")
    )
    if overdue:
        speak += f" Overdue: {len(overdue)}."
    return {"ok": True, "items": items, "overdue": overdue, "speak": speak}


def today_brief() -> dict[str, Any]:
    listed = list_todos()
    open_items = listed.get("items") or []
    if not open_items:
        return {
            "ok": True,
            "speak": "No dated todos yet — add 2 tiny ones with deadlines (e.g. 'stretch by 11am').",
            "items": [],
        }
    done_today = sum(
        1
        for it in (load().get("items") or [])
        if it.get("done") and it.get("day") == _local_today()
    )
    top = open_items[0]
    speak = (
        f"{done_today} done today, {len(open_items)} open. "
        f"Next: '{top.get('title')}' due {top.get('deadline_label')}."
    )
    return {"ok": True, "items": open_items, "done_today": done_today, "speak": speak}


def suggest_quick_wins(limit: int = 2) -> dict[str, Any]:
    listed = list_todos()
    items = (listed.get("items") or [])[:limit]
    if items:
        bits = [f"'{it.get('title')}' by {it.get('deadline_label')}" for it in items]
        return {
            "ok": True,
            "items": items,
            "speak": "Quick wins: " + "; ".join(bits) + ".",
        }
    # seed suggestions (not auto-added — user must confirm with deadline)
    suggestions = [
        "Drink water + 2 min stretch",
        "Clear one inbox email",
        "Write 3 bullets for today's main task",
    ]
    return {
        "ok": True,
        "suggestions": suggestions,
        "speak": (
            "No open todos. Say e.g. 'todo: drink water by 10am' — "
            "I refuse tasks without deadlines."
        ),
    }


def plan_day(message: str = "") -> dict[str, Any]:
    """Create a small daily set — each line must still get a deadline."""
    # If user lists tasks without deadlines, ask once for a default window
    lines = [ln.strip("-• \t") for ln in (message or "").splitlines() if ln.strip()]
    if not lines and message:
        # comma / and split
        chunk = re.sub(r"^(plan my day|daily todos|create todos)[:\s]*", "", message, flags=re.I)
        lines = [x.strip() for x in re.split(r",| and ", chunk) if x.strip()]
    if not lines:
        return {
            "ok": True,
            "needs_tasks": True,
            "speak": "List today's tasks — each needs a time. Example: 'ship PR by 2pm, gym by 7pm'.",
        }
    created = []
    asked = []
    for ln in lines[:8]:
        r = add_todo(ln, source="plan_day")
        if r.get("needs_deadline"):
            asked.append(r)
        elif r.get("ok"):
            created.append(r.get("item"))
    if asked and not created:
        return {
            "ok": True,
            "needs_deadline": True,
            "pending": asked,
            "speak": asked[0]["speak"] + " (I'll ask per task — deadlines make them real.)",
        }
    brief = today_brief()
    return {
        "ok": True,
        "created": created,
        "pending_deadline": asked,
        "speak": f"Added {len(created)} dated todo(s). " + str(brief.get("speak") or ""),
    }


async def tool_todos(action: str = "list", message: str = "", title: str = "", deadline: str = "") -> dict[str, Any]:
    act = (action or "list").lower()
    lower = (message or "").lower()

    if act in {"done", "complete"} or lower.startswith("done ") or "mark done" in lower:
        tid = ""
        m = re.search(r"\b([a-f0-9]{8})\b", message or "")
        if m:
            tid = m.group(1)
        substr = title or re.sub(r"^(done|mark done|complete)\s*", "", message or "", flags=re.I).strip()
        return complete(todo_id=tid, title_substr=substr)

    if act in {"add", "create"} or lower.startswith("todo:") or lower.startswith("task:") or "add todo" in lower:
        body = title or message
        body = re.sub(r"^(todo:|task:|add todo|remind me to)\s*", "", body or "", flags=re.I).strip()
        if deadline:
            body = f"{body} deadline {deadline}"
        return add_todo(body, source="chat")

    if act in {"plan", "daily"} or any(k in lower for k in ("plan my day", "daily todos", "create my todos", "tasks for today")):
        return plan_day(message)

    if act in {"brief", "today"} or "what's on today" in lower or "whats on today" in lower:
        return today_brief()

    if act in {"wins", "quick"}:
        return suggest_quick_wins()

    return list_todos()
