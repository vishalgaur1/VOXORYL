from __future__ import annotations

"""
Universal planner — day / week / event / trip / study / project plans with deadlines.
Reuses todos patterns: every concrete step needs a deadline when possible.
Stored under data/plans.json.
"""

import json
import re
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from voxoryl.config import settings
from voxoryl.knowledge import knowledge
from voxoryl.memory import memory
from voxoryl.todos import add_todo, parse_deadline


PLAN_KINDS = ("day", "week", "event", "trip", "study", "project")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _path():
    p = settings.voxoryl_data_dir / "plans.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load() -> dict[str, Any]:
    path = _path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"plans": [], "updated_at": None}


def save(data: dict[str, Any]) -> None:
    data["updated_at"] = _now().isoformat()
    _path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def detect_kind(message: str) -> str:
    lower = (message or "").lower()
    if any(k in lower for k in ("plan a trip", "trip plan", "travel plan", "itinerary", "vacation plan")):
        return "trip"
    if any(k in lower for k in ("study plan", "exam plan", "revision plan", "learn plan")):
        return "study"
    if any(k in lower for k in ("event plan", "party plan", "wedding plan", "meetup plan")):
        return "event"
    if any(k in lower for k in ("project plan", "sprint plan", "roadmap", "ship plan")):
        return "project"
    if any(k in lower for k in ("plan my week", "weekly plan", "week plan", "this week")):
        return "week"
    if any(k in lower for k in ("plan my day", "daily plan", "today's plan", "todays plan", "plan today")):
        return "day"
    if "plan" in lower:
        return "day"
    return "day"


def _default_steps(kind: str, title: str) -> list[dict[str, str]]:
    """Scaffold steps with suggested deadline labels — still ask if unclear."""
    local = _now().astimezone()
    if kind == "day":
        slots = [
            ("Morning focus block", local.replace(hour=11, minute=0)),
            ("Afternoon deep work", local.replace(hour=16, minute=0)),
            ("Evening wrap-up", local.replace(hour=20, minute=0)),
        ]
    elif kind == "week":
        slots = [
            ("Mon — set weekly goals", local + timedelta(days=(0 - local.weekday()) % 7)),
            ("Wed — midpoint check", local + timedelta(days=(2 - local.weekday()) % 7)),
            ("Fri — ship / review", local + timedelta(days=(4 - local.weekday()) % 7)),
        ]
    elif kind == "trip":
        slots = [
            (f"Confirm dates & budget for {title}", local + timedelta(days=2)),
            ("Book transport (genuine sites)", local + timedelta(days=5)),
            ("Book stay + docs checklist", local + timedelta(days=7)),
            ("Pack + local SIM / maps offline", local + timedelta(days=14)),
        ]
    elif kind == "study":
        slots = [
            (f"Outline topics for {title}", local + timedelta(hours=6)),
            ("Practice / flashcards block", local + timedelta(days=1)),
            ("Mock test / review gaps", local + timedelta(days=3)),
            ("Final revision pass", local + timedelta(days=5)),
        ]
    elif kind == "event":
        slots = [
            (f"Lock venue / guest list for {title}", local + timedelta(days=3)),
            ("Invites + budget confirm", local + timedelta(days=5)),
            ("Supplies / agenda ready", local + timedelta(days=10)),
            ("Day-of run-of-show", local + timedelta(days=14)),
        ]
    else:  # project
        slots = [
            (f"Define MVP for {title}", local + timedelta(days=1)),
            ("Build first vertical slice", local + timedelta(days=3)),
            ("Test + fix blockers", local + timedelta(days=5)),
            ("Ship / demo", local + timedelta(days=7)),
        ]
    out = []
    for label, when in slots:
        if when.tzinfo is None:
            when = when.replace(tzinfo=local.tzinfo)
        if when < local and kind == "day":
            when = when + timedelta(days=1)
        out.append(
            {
                "title": label,
                "deadline": when.astimezone(timezone.utc).isoformat(),
                "deadline_label": when.strftime("%a %H:%M") if kind == "day" else when.strftime("%Y-%m-%d"),
            }
        )
    return out


def _extract_title(message: str, kind: str) -> str:
    raw = (message or "").strip()
    cleaned = re.sub(
        r"^(plan\s+(?:my|a|the|an)?\s*|create\s+(?:a\s+)?|make\s+(?:a\s+)?)",
        "",
        raw,
        flags=re.I,
    )
    cleaned = re.sub(
        r"\b(day|week|trip|study|event|project)\s*plan\b",
        "",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(r"\bplan\b", "", cleaned, flags=re.I).strip(" :-")
    if cleaned and len(cleaned) > 2:
        return cleaned[:120]
    defaults = {
        "day": "Today",
        "week": "This week",
        "trip": "Trip",
        "study": "Study block",
        "event": "Event",
        "project": "Project",
    }
    return defaults.get(kind, "Plan")


def create_plan(message: str, *, kind: str | None = None, sync_todos: bool = True) -> dict[str, Any]:
    kind = (kind or detect_kind(message)).lower()
    if kind not in PLAN_KINDS:
        kind = "day"
    title = _extract_title(message, kind)

    # User-supplied bullet steps with optional deadlines
    lines = [ln.strip("-• \t") for ln in (message or "").splitlines() if ln.strip()]
    custom_steps: list[dict[str, str]] = []
    if len(lines) > 1:
        for ln in lines[1:8]:
            iso, label = parse_deadline(ln, default_hours=None)
            step_title = re.sub(
                r"\b(by|before|until|deadline|due)\s*:?\s*[^\n,;]+",
                "",
                ln,
                flags=re.I,
            ).strip(" -:") or ln
            if not iso:
                # scaffold relative offset
                iso, label = parse_deadline(ln + " by eod", default_hours=8)
            custom_steps.append(
                {
                    "title": step_title[:160],
                    "deadline": iso or (_now() + timedelta(hours=8)).isoformat(),
                    "deadline_label": label or "soon",
                }
            )

    steps = custom_steps or _default_steps(kind, title)
    plan = {
        "id": str(uuid.uuid4())[:8],
        "kind": kind,
        "title": title,
        "steps": [{**s, "id": str(uuid.uuid4())[:8], "done": False} for s in steps],
        "created_at": _now().isoformat(),
        "source": message[:240],
        "status": "active",
    }

    todo_results = []
    if sync_todos:
        for s in plan["steps"]:
            # Prefer explicit deadline label in add_todo text
            r = add_todo(
                f"{s['title']} by {s.get('deadline_label') or 'eod'}",
                source=f"planner:{kind}",
            )
            todo_results.append(r)

    data = load()
    data.setdefault("plans", []).append(plan)
    data["plans"] = data["plans"][-80:]
    save(data)

    knowledge.append_facts(
        "Plans",
        [f"{kind.title()} plan '{title}' with {len(plan['steps'])} dated steps."],
    )
    memory.remember_fact(f"Plan ({kind}): {title}", tags=["planner", kind])

    step_bits = "; ".join(f"{s['title']} ({s['deadline_label']})" for s in plan["steps"][:4])
    speak = (
        f"{kind.title()} plan ready: {title}. "
        f"{len(plan['steps'])} steps with deadlines — {step_bits}."
        + (" Say if you want different times." if not custom_steps else "")
    )
    return {
        "ok": True,
        "plan": plan,
        "todos": todo_results,
        "speak": speak,
    }


def list_plans(*, kind: str | None = None, limit: int = 10) -> dict[str, Any]:
    data = load()
    plans = [p for p in (data.get("plans") or []) if p.get("status") != "archived"]
    if kind:
        plans = [p for p in plans if p.get("kind") == kind]
    plans = list(reversed(plans))[:limit]
    if not plans:
        return {
            "ok": True,
            "plans": [],
            "speak": "No plans yet. Say 'plan my day', 'plan a trip', or 'study plan for…'.",
        }
    bits = [f"{p.get('kind')}:{p.get('title')} [{p.get('id')}]" for p in plans[:5]]
    return {"ok": True, "plans": plans, "speak": "Plans: " + "; ".join(bits) + "."}


def get_plan(plan_id: str = "") -> dict[str, Any]:
    data = load()
    for p in data.get("plans") or []:
        if p.get("id") == plan_id:
            steps = p.get("steps") or []
            open_n = sum(1 for s in steps if not s.get("done"))
            return {
                "ok": True,
                "plan": p,
                "speak": f"{p.get('kind')} plan '{p.get('title')}': {open_n}/{len(steps)} steps open.",
            }
    return {"ok": False, "error": "not found", "speak": "No plan with that id."}


async def tool_planner(
    action: str = "create",
    message: str = "",
    kind: str = "",
    plan_id: str = "",
) -> dict[str, Any]:
    act = (action or "create").lower()
    lower = (message or "").lower()

    if act in {"list", "status"} or "list plans" in lower or "my plans" in lower:
        return list_plans(kind=kind or None)

    if act in {"get", "show"} or plan_id:
        pid = plan_id
        if not pid:
            m = re.search(r"\b([a-f0-9]{8})\b", message or "")
            pid = m.group(1) if m else ""
        return get_plan(pid)

    # create / plan
    k = kind or detect_kind(message)
    return create_plan(message, kind=k)
