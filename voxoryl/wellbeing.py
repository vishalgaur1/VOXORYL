from __future__ import annotations

"""
Wellbeing check-ins: ask once or twice a day how you're doing.
Keep motivated; pair with quick deadline todos. Local only.
"""

import json
from datetime import datetime, timezone, timedelta
from typing import Any

from voxoryl.config import settings
from voxoryl.knowledge import knowledge
from voxoryl.memory import memory


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _path():
    p = settings.voxoryl_data_dir / "wellbeing.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load() -> dict[str, Any]:
    path = _path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {
        "enabled": True,
        "max_per_day": 2,
        "checkins": [],
        "last_prompt_at": None,
        "streak_ok_days": 0,
    }


def save(data: dict[str, Any]) -> None:
    _path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _today_key() -> str:
    return _now().astimezone().strftime("%Y-%m-%d")


def prompts_today(data: dict[str, Any] | None = None) -> int:
    data = data or load()
    day = _today_key()
    n = 0
    for c in data.get("checkins") or []:
        if str(c.get("day") or "").startswith(day) or str(c.get("at") or "").startswith(day):
            n += 1
        # also count prompt-only
    # count last_prompt same day
    last = data.get("last_prompt_at")
    if last and str(last).startswith(day):
        # if only prompts without answers, still limit via separate counter
        pass
    prompts = int((data.get("prompts_by_day") or {}).get(day) or 0)
    return max(n, prompts)


def should_prompt() -> bool:
    data = load()
    if not data.get("enabled", True):
        return False
    max_n = int(data.get("max_per_day") or 2)
    day = _today_key()
    prompts = int((data.get("prompts_by_day") or {}).get(day) or 0)
    if prompts >= max_n:
        return False
    last = data.get("last_prompt_at")
    if last:
        try:
            lt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if _now() - lt < timedelta(hours=5):
                return False
        except ValueError:
            pass
    return True


def make_prompt(*, with_todos: bool = True) -> dict[str, Any]:
    from voxoryl.todos import today_brief

    data = load()
    day = _today_key()
    data.setdefault("prompts_by_day", {})
    data["prompts_by_day"][day] = int(data["prompts_by_day"].get(day) or 0) + 1
    data["last_prompt_at"] = _now().isoformat()
    save(data)

    todo_bit = ""
    if with_todos:
        brief = today_brief()
        if brief.get("speak"):
            todo_bit = " " + str(brief["speak"])

    q = (
        "Quick check-in: how are you doing — energy/mood 1-5? "
        "One word is enough. Let's knock out a small win."
        + todo_bit
    )
    return {
        "ok": True,
        "needs_reply": True,
        "question": q,
        "speak": q,
        "prompts_today": data["prompts_by_day"][day],
        "max_per_day": data.get("max_per_day", 2),
    }


def record_reply(message: str) -> dict[str, Any]:
    data = load()
    mood = None
    m = None
    import re

    found = re.search(r"\b([1-5])\b", message or "")
    if found:
        mood = int(found.group(1))
    lower = (message or "").lower()
    if mood is None:
        if any(k in lower for k in ("great", "good", "solid", "ok", "fine", "alright")):
            mood = 4 if "great" in lower or "good" in lower else 3
        elif any(k in lower for k in ("bad", "tired", "low", "awful", "stressed")):
            mood = 2
        elif any(k in lower for k in ("meh", "mid", "okayish")):
            mood = 3

    entry = {
        "at": _now().isoformat(),
        "day": _today_key(),
        "mood": mood,
        "raw": (message or "")[:240],
    }
    data.setdefault("checkins", []).append(entry)
    data["checkins"] = data["checkins"][-120:]
    if mood and mood >= 3:
        data["streak_ok_days"] = int(data.get("streak_ok_days") or 0)
    save(data)

    from voxoryl.todos import suggest_quick_wins

    wins = suggest_quick_wins(limit=2)
    motivate = wins.get("speak") or "Pick one tiny task with a deadline and finish it."
    if mood is not None and mood <= 2:
        speak = f"Got it — rough day (mood {mood}). No heroics. {motivate}"
    elif mood is not None:
        speak = f"Logged mood {mood}/5. {motivate}"
    else:
        speak = f"Logged. {motivate}"

    knowledge.append_facts("Wellbeing", [f"Check-in {_today_key()}: mood={mood} — {(message or '')[:80]}"])
    memory.remember_fact(speak[:200], tags=["wellbeing", "checkin"])
    return {"ok": True, "entry": entry, "quick_wins": wins, "speak": speak}


async def tool_wellbeing(action: str = "check", message: str = "") -> dict[str, Any]:
    act = (action or "check").lower()
    lower = (message or "").lower()
    data = load()

    if act in {"disable", "stop"} or "stop check-in" in lower:
        data["enabled"] = False
        save(data)
        return {"ok": True, "speak": "Wellbeing prompts off. Say 'enable check-ins' to resume."}
    if act in {"enable", "start"} or "enable check-in" in lower:
        data["enabled"] = True
        save(data)
        return {"ok": True, "speak": "Wellbeing prompts on — up to 2/day."}

    if act in {"reply", "answer"} or (
        act == "check"
        and message
        and any(k in lower for k in ("mood", "i'm", "im ", "feeling", "doing", "/5", "tired", "good", "bad"))
        and "how am i" not in lower
        and "check in" not in lower
        and "check-in" not in lower
    ):
        # if last prompt recent, treat as reply
        return record_reply(message)

    if act in {"status", "summary"}:
        today = [c for c in (data.get("checkins") or []) if str(c.get("day")) == _today_key()]
        return {
            "ok": True,
            "enabled": data.get("enabled", True),
            "today": today,
            "prompts_today": int((data.get("prompts_by_day") or {}).get(_today_key()) or 0),
            "speak": f"Check-ins today: {len(today)}. Prompts: {int((data.get('prompts_by_day') or {}).get(_today_key()) or 0)}/{data.get('max_per_day', 2)}.",
        }

    if act in {"prompt", "ask", "nudge"} or "how am i" in lower or "check in" in lower or "check-in" in lower:
        if not should_prompt() and act != "prompt":
            return {
                "ok": True,
                "skipped": True,
                "speak": "Already checked in enough today. Want a quick todo with a deadline instead?",
            }
        return make_prompt()

    # default: prompt if due else status
    if should_prompt():
        return make_prompt()
    return await tool_wellbeing(action="status", message=message)
