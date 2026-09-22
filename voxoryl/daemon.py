from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from voxoryl.config import settings
from voxoryl.council import run_council
from voxoryl.memory import memory
from voxoryl.tools import tool_market_scan, tool_notes

scheduler = AsyncIOScheduler()


def _load_daily() -> dict[str, Any]:
    path = settings.daily_path
    if not path.exists():
        return {
            "enabled": True,
            "tasks": [
                {
                    "id": "morning_scan",
                    "prompt": (
                        "Scan what Saint has been building from memory. Propose 3 fresh market-aware ideas "
                        "and one concrete research task for today. Also suggest one marketing angle and one short video idea."
                    ),
                }
            ],
            "last_runs": [],
        }
    return json.loads(path.read_text(encoding="utf-8"))


def _save_daily(data: dict[str, Any]) -> None:
    settings.daily_path.parent.mkdir(parents=True, exist_ok=True)
    settings.daily_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


async def daily_autonomy_tick() -> dict[str, Any]:
    daily = _load_daily()
    if not daily.get("enabled", True):
        return {"skipped": True, "reason": "daily autonomy disabled"}

    profile = memory.read().get("profile", {})
    projects = ", ".join(profile.get("projects", [])) or "unspecified projects"
    scan = await tool_market_scan(projects)

    task_prompt = daily["tasks"][0]["prompt"] if daily.get("tasks") else "Give Saint a useful daily brief."
    council = await run_council(
        (
            f"{task_prompt}\n\n"
            f"Known projects: {projects}\n"
            f"Market brief path: {scan.get('path')}\n"
            f"Research snapshot: {scan.get('findings', [{}])[0].get('abstract') if scan.get('findings') else scan}\n"
            "Be passionate and specific. Prepare a daily brief Saint can act on."
        ),
        execute=True,
    )
    decision = str((council.get("synthesis") or {}).get("decision") or "")
    await tool_notes(decision, title="Daily Voxoryl brief")

    # Seed / refresh deadline todos reminder into notes
    try:
        from voxoryl.todos import today_brief

        brief = today_brief()
        if brief.get("speak"):
            await tool_notes(str(brief["speak"]), title="Today todos (deadlines)")
    except Exception:
        pass

    run = {
        "at": datetime.now(timezone.utc).isoformat(),
        "research_ok": bool(scan.get("ok")),
        "decision": decision,
    }
    daily.setdefault("last_runs", []).append(run)
    daily["last_runs"] = daily["last_runs"][-30:]
    _save_daily(daily)
    return {"ok": True, "run": run, "council": council, "scan": scan}


async def wellbeing_tick() -> dict[str, Any]:
    """Ask how you're doing up to 2x/day — never spam."""
    from voxoryl.wellbeing import should_prompt, make_prompt

    if not should_prompt():
        return {"skipped": True, "reason": "quota or too soon"}
    prompt = make_prompt(with_todos=True)
    # Mild optional book line — never spammy
    try:
        from voxoryl.books import daemon_book_nudge

        book = daemon_book_nudge()
        if book.get("ok") and book.get("speak") and not book.get("skipped"):
            prompt["book_line"] = book.get("speak")
            speak = str(prompt.get("speak") or "")
            prompt["speak"] = (speak + " " + str(book["speak"])).strip()[:500]
    except Exception:
        pass
    memory.remember_fact(str(prompt.get("speak") or "check-in")[:200], tags=["wellbeing", "prompt"])
    return {"ok": True, "prompt": prompt}


async def neuro_improve_tick() -> dict[str, Any]:
    """Daily: research better ways to reply for the owner's stated needs."""
    from voxoryl.neuro import get_profiles, improve_from_research

    if not get_profiles():
        return {"skipped": True, "reason": "no neuro profiles"}
    return await improve_from_research()


async def curiosity_tick() -> dict[str, Any]:
    """One tasteful curiosity nudge per day when enabled."""
    from voxoryl.curiosity import daemon_curiosity_nudge

    return await daemon_curiosity_nudge()


async def sale_watch_tick() -> dict[str, Any]:
    """Periodic price / sale checks for active watches."""
    from voxoryl.sale_watch import tick_all

    return await tick_all()


async def screen_watch_tick() -> dict[str, Any]:
    """Always-on light screen awareness while computer use is enabled."""
    from voxoryl.config import settings
    from voxoryl.screen import computer_use_enabled, refresh_screen_watch

    if not settings.screen_watch_enabled or not computer_use_enabled():
        return {"skipped": True, "reason": "screen watch off"}
    return await refresh_screen_watch()


async def screen_watch_active_tick() -> dict[str, Any]:
    """Faster refresh while Voxoryl is actively clicking/typing."""
    from voxoryl.config import settings
    from voxoryl.screen import computer_use_enabled, control_active, refresh_screen_watch

    if not settings.screen_watch_enabled or not computer_use_enabled():
        return {"skipped": True, "reason": "screen watch off"}
    if not control_active():
        return {"skipped": True, "reason": "not controlling"}
    return await refresh_screen_watch(
        question=(
            "Active control watch: active window title, focused field, "
            "dialogs/modals, and obvious clickable targets. Max 6 bullets."
        )
    )


def start_daemon() -> None:
    if scheduler.running:
        return
    from voxoryl.config import settings

    scheduler.add_job(daily_autonomy_tick, "cron", hour=9, minute=0, id="daily_brief", replace_existing=True)
    scheduler.add_job(neuro_improve_tick, "cron", hour=8, minute=15, id="neuro_improve", replace_existing=True)
    scheduler.add_job(curiosity_tick, "cron", hour=10, minute=30, id="curiosity_daily", replace_existing=True)
    scheduler.add_job(wellbeing_tick, "cron", hour=11, minute=0, id="wellbeing_am", replace_existing=True)
    scheduler.add_job(wellbeing_tick, "cron", hour=18, minute=30, id="wellbeing_pm", replace_existing=True)
    scheduler.add_job(sale_watch_tick, "interval", hours=3, id="sale_watch", replace_existing=True)
    scheduler.add_job(_heartbeat, "interval", hours=1, id="heartbeat", replace_existing=True)
    interval = max(20, int(getattr(settings, "screen_watch_interval_sec", 45) or 45))
    scheduler.add_job(screen_watch_tick, "interval", seconds=interval, id="screen_watch", replace_existing=True)
    scheduler.add_job(
        screen_watch_active_tick,
        "interval",
        seconds=12,
        id="screen_watch_active",
        replace_existing=True,
    )
    scheduler.start()


async def _heartbeat() -> None:
    memory.remember_fact(
        f"Heartbeat OK at {datetime.now(timezone.utc).isoformat()}",
        tags=["heartbeat"],
    )


def stop_daemon() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
