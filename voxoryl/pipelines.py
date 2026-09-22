from __future__ import annotations

"""
LocalClaw-inspired deterministic pipelines: code owns the workflow,
the LLM only helps at the edges. Fast path for small models.
"""

from typing import Any, Callable, Awaitable

import re

from voxoryl.ingest import tool_ingest_share, tool_whatsapp
from voxoryl.knowledge import knowledge, log_message_to_knowledge
from voxoryl.leads import tool_leads
from voxoryl.melody import tool_melody
from voxoryl.media_gen import tool_media
from voxoryl.approvals import tool_approvals
from voxoryl.sites import tool_sites
from voxoryl.qna import tool_qna, _load_session
from voxoryl.companion import tool_companion
from voxoryl.bookings import tool_bookings
from voxoryl.docs_vault import tool_docs
from voxoryl.prefs import tool_prefs
from voxoryl.food_memory import tool_food
from voxoryl.health import tool_health
from voxoryl.spend import tool_spend
from voxoryl.wellbeing import tool_wellbeing
from voxoryl.todos import tool_todos
from voxoryl.neuro import tool_neuro
from voxoryl.android_lab import tool_android
from voxoryl.sandbox_hv import tool_sandbox
from voxoryl.camera import tool_camera
from voxoryl.music_taste import tool_music
from voxoryl.books import tool_books
from voxoryl.style_colors import tool_style_colors
from voxoryl.curiosity import tool_curiosity
from voxoryl.windows_ops import tool_windows
from voxoryl.net_profile import tool_net_profile
from voxoryl.self_upgrade import tool_self_upgrade
from voxoryl.multitask import tool_multitask, wants_multitask
from voxoryl.planner import tool_planner
from voxoryl.sale_watch import tool_sale_watch
from voxoryl.i18n_voice import tool_i18n
from voxoryl.memory import memory
from voxoryl.tools import tool_email, tool_github, tool_market_scan
from voxoryl.screen import tool_screen


PipelineFn = Callable[[str], Awaitable[dict[str, Any]]]


async def pipeline_market(message: str) -> dict[str, Any]:
    scan = await tool_market_scan(message)
    await log_message_to_knowledge(message)
    return {
        "ok": True,
        "pipeline": "market",
        "speak": f"Market scan complete for your focus. Saved brief at {scan.get('path')}.",
        "result": scan,
    }


async def pipeline_research(message: str) -> dict[str, Any]:
    from voxoryl.research import deep_research

    result = await deep_research(message, conclude=True)
    return {
        "ok": bool(result.get("ok")),
        "pipeline": "research",
        "speak": str(result.get("speak") or result.get("abstract") or "Research done."),
        "result": result,
    }


async def pipeline_email(message: str) -> dict[str, Any]:
    inbox = await tool_email()
    if not inbox.get("ok"):
        return {
            "ok": False,
            "pipeline": "email",
            "speak": inbox.get("hint") or inbox.get("error") or "Email not configured.",
            "result": inbox,
        }
    msgs = inbox.get("messages") or []
    top = "; ".join(f"{m.get('from','?')}: {m.get('subject','')}" for m in msgs[:3]) or "No recent mail."
    return {
        "ok": True,
        "pipeline": "email",
        "speak": f"Checked the inbox. Latest: {top}",
        "result": inbox,
    }


async def pipeline_github(message: str) -> dict[str, Any]:
    repos = await tool_github("list")
    if not repos.get("ok"):
        return {
            "ok": False,
            "pipeline": "github",
            "speak": repos.get("hint") or repos.get("error") or "GitHub not configured.",
            "result": repos,
        }
    names = ", ".join(r.get("name") or "" for r in (repos.get("repos") or [])[:5]) or "none"
    return {
        "ok": True,
        "pipeline": "github",
        "speak": f"Pulled your repositories. Top updates: {names}.",
        "result": repos,
    }


async def pipeline_screen_look(message: str) -> dict[str, Any]:
    result = await tool_screen(action="look", goal=message)
    desc = ""
    if isinstance(result.get("vision"), dict):
        desc = str(result["vision"].get("description") or "")[:280]
    return {
        "ok": bool(result.get("ok")),
        "pipeline": "screen_look",
        "speak": desc or result.get("error") or "Screen look finished.",
        "result": result,
    }


async def pipeline_screen_act(message: str) -> dict[str, Any]:
    result = await tool_screen(action="act", goal=message)
    return {
        "ok": bool(result.get("ok")),
        "pipeline": "screen_act",
        "speak": str(result.get("speak") or result.get("error") or "Screen control finished."),
        "result": result,
    }


async def pipeline_flash_fill(message: str) -> dict[str, Any]:
    result = await tool_screen(
        action="flash_fill",
        goal=message or "Flash-fill this form from my saved identity.",
    )
    return {
        "ok": bool(result.get("ok")),
        "pipeline": "flash_fill",
        "speak": str(result.get("speak") or result.get("error") or "Flash fill finished."),
        "result": result,
    }


async def pipeline_screen_type(message: str) -> dict[str, Any]:
    result = await tool_screen(action="type", goal=message, text=message)
    return {
        "ok": bool(result.get("ok")),
        "pipeline": "screen_type",
        "speak": str(result.get("speak") or "Typed."),
        "result": result,
        "fast": True,
    }


async def pipeline_powertoys(message: str) -> dict[str, Any]:
    from voxoryl.powertoys_bridge import tool_powertoys

    result = await tool_powertoys(action="auto", message=message)
    return {
        "ok": bool(result.get("ok", True)),
        "pipeline": "powertoys",
        "speak": str(result.get("speak") or "PowerToys."),
        "result": result,
        "fast": True,
    }


async def pipeline_chrome(message: str) -> dict[str, Any]:
    from voxoryl.chrome_control import tool_chrome

    result = await tool_chrome(action="auto", message=message)
    # Keep API payload serializable (no nested vision rounds / circular refs)
    slim = {
        k: result.get(k)
        for k in (
            "ok",
            "speak",
            "action_only",
            "url",
            "pipeline",
            "opened_ok",
            "nav_ok",
            "nav_url",
            "play_ok",
            "play_method",
            "play_index",
            "verified",
            "method",
            "launch_mode",
            "error",
        )
        if k in result
    }
    ok = bool(result.get("ok"))
    # Honesty: never coerce missing ok to True for chrome play paths
    if result.get("play_ok") is False or result.get("verified") is False:
        ok = False
    return {
        "ok": ok,
        "pipeline": "chrome",
        "speak": str(result.get("speak") if result.get("speak") is not None else ""),
        "result": slim,
        "fast": True,
        "action_only": True,
        "verified": result.get("verified"),
    }


async def pipeline_save_reel(message: str) -> dict[str, Any]:
    use_screen = any(k in message.lower() for k in ("screen", "this reel", "on my screen", "whats on"))
    # If no URL in message, prefer screen/inbox capture
    has_url = "http://" in message.lower() or "https://" in message.lower()
    result = await tool_ingest_share(
        message=message,
        text=message,
        use_screen=use_screen or not has_url,
    )
    return {
        "ok": bool(result.get("ok")),
        "pipeline": "save_reel",
        "speak": str(result.get("speak") or result.get("error") or result.get("hint") or "Ingest finished."),
        "result": result,
    }


async def pipeline_whatsapp(message: str) -> dict[str, Any]:
    lower = message.lower()
    if any(k in lower for k in ("send", "message", "tell", "text ")):
        body = message
        for sep in ("whatsapp:", "whatsapp ", "send:"):
            if sep in lower:
                idx = lower.index(sep)
                body = message[idx + len(sep) :].strip()
                break
        result = await tool_whatsapp(action="send", message=body or message)
    elif any(k in lower for k in ("ingest", "save", "forward", "tip")):
        result = await tool_whatsapp(action="ingest", message=message)
    else:
        result = await tool_whatsapp(action="status")
    return {
        "ok": bool(result.get("ok")),
        "pipeline": "whatsapp",
        "speak": str(result.get("speak") or result.get("hint") or "WhatsApp bridge done."),
        "result": result,
    }


async def pipeline_remember_project(message: str) -> dict[str, Any]:
    memory.update_profile(projects=[message[:200]])
    logged = await log_message_to_knowledge(message)
    return {
        "ok": True,
        "pipeline": "remember_project",
        "speak": "Logged to memory and knowledge.",
        "result": {"knowledge": logged},
    }


async def pipeline_melody(message: str) -> dict[str, Any]:
    lower = message.lower()
    place = any(k in lower for k in ("fl studio", "daw", "put this", "midi track", "guitar", "place"))
    find = any(k in lower for k in ("what song", "find song", "identify song", "shazam", "lyrics"))
    if find and not place:
        result = await tool_melody(action="find_song", message=message)
    else:
        instrument = "mellow guitar"
        if "piano" in lower:
            instrument = "piano"
        elif "bass" in lower:
            instrument = "bass"
        elif "pad" in lower:
            instrument = "pad"
        elif "jazz" in lower:
            instrument = "jazz guitar"
        action = "place" if place else "to_midi"
        result = await tool_melody(
            action=action,
            message=message,
            instrument=instrument,
            place_in_daw=place,
        )
    return {
        "ok": bool(result.get("ok")),
        "pipeline": "melody",
        "speak": str(result.get("speak") or result.get("error") or result.get("hint") or "Melody done."),
        "result": result,
    }


async def pipeline_media(message: str) -> dict[str, Any]:
    lower = message.lower()
    kind = "video" if "video" in lower else "image"
    if any(k in lower for k in ("revise media", "iterate media")):
        result = await tool_media(action="revise", message=message)
    else:
        result = await tool_media(action="auto", message=message, brief=message, kind=kind)
    return {
        "ok": bool(result.get("ok")),
        "pipeline": "media",
        "speak": str(result.get("speak") or result.get("error") or "Media done."),
        "result": result,
    }


async def pipeline_leads(message: str) -> dict[str, Any]:
    lower = message.lower()
    if any(k in lower for k in ("approve", "reject")):
        action = "approve" if "approve" in lower else "reject"
        result = await tool_leads(action=action, message=message)
    elif any(k in lower for k in ("draft", "mjml", "outreach email")):
        result = await tool_leads(action="draft", message=message)
    elif any(k in lower for k in ("send approved", "send leads")):
        result = await tool_leads(action="send", message=message)
    elif any(k in lower for k in ("list lead", "show lead")):
        result = await tool_leads(action="list", message=message)
    else:
        result = await tool_leads(action="scout", message=message)
    return {
        "ok": bool(result.get("ok")),
        "pipeline": "leads",
        "speak": str(result.get("speak") or result.get("error") or "Leads done."),
        "result": result,
    }


async def pipeline_approvals(message: str) -> dict[str, Any]:
    lower = message.lower()
    if "reject" in lower:
        result = await tool_approvals(action="reject", message=message)
    elif "approve" in lower:
        result = await tool_approvals(action="approve", message=message)
    else:
        result = await tool_approvals(action="list", message=message)
    return {
        "ok": bool(result.get("ok")),
        "pipeline": "approvals",
        "speak": str(result.get("speak") or f"{result.get('count', 0)} pending."),
        "result": result,
    }


async def pipeline_sites(message: str) -> dict[str, Any]:
    lower = message.lower()
    if any(k in lower for k in ("list sites", "my sites")):
        result = await tool_sites(action="list", message=message)
    elif any(k in lower for k in ("deploy site", "deploy to vercel", "host on vercel")):
        result = await tool_sites(action="deploy", message=message)
    else:
        result = await tool_sites(action="create", brief=message, message=message)
    return {
        "ok": bool(result.get("ok")),
        "pipeline": "sites",
        "speak": str(result.get("speak") or result.get("error") or "Site done."),
        "result": result,
    }


async def pipeline_qna(message: str) -> dict[str, Any]:
    lower = message.lower().strip()
    if any(k in lower for k in ("stop qna", "end qna", "cancel qna")):
        result = await tool_qna(action="stop", message=message)
    elif any(k in lower for k in ("qna status", "interview status")):
        result = await tool_qna(action="status", message=message)
    elif any(k in lower for k in ("start qna", "begin qna", "interview me", "learn about me", "qna session")):
        result = await tool_qna(action="start", message=message)
    else:
        # Active session: treat message as answer
        result = await tool_qna(action="answer", message=message, answer=message)
    return {
        "ok": bool(result.get("ok")),
        "pipeline": "qna",
        "speak": str(result.get("speak") or result.get("question") or result.get("error") or "QnA."),
        "result": result,
    }


async def pipeline_companion(message: str) -> dict[str, Any]:
    lower = message.lower()
    if "privacy" in lower:
        result = await tool_companion(action="privacy", message=message)
    elif any(k in lower for k in ("list exports", "chat inbox", "list reports")):
        result = await tool_companion(action="inbox", message=message)
    else:
        result = await tool_companion(action="analyze", message=message, focus=message)
    return {
        "ok": bool(result.get("ok")),
        "pipeline": "companion",
        "speak": str(result.get("speak") or result.get("error") or result.get("hint") or "Companion done."),
        "result": result,
    }


async def pipeline_bookings(message: str) -> dict[str, Any]:
    lower = message.lower()
    if any(k in lower for k in ("itr", "income tax", "passport", "gst", "govt form", "government")):
        result = await tool_bookings(action="gov", message=message)
    else:
        result = await tool_bookings(action="tickets", message=message)
    return {
        "ok": bool(result.get("ok", True)),
        "pipeline": "bookings",
        "speak": str(result.get("speak") or result.get("question") or result.get("error") or "Bookings."),
        "result": result,
    }


async def pipeline_docs(message: str) -> dict[str, Any]:
    lower = message.lower()
    if any(k in lower for k in ("add doc", "store doc", "register doc")):
        result = await tool_docs(action="add", message=message)
    elif any(k in lower for k in ("for itr", "pull docs", "prepare docs", "docs for")):
        result = await tool_docs(action="for_task", message=message)
    else:
        result = await tool_docs(action="list", message=message)
    return {
        "ok": bool(result.get("ok")),
        "pipeline": "docs",
        "speak": str(result.get("speak") or result.get("hint") or "Docs vault."),
        "result": result,
    }


async def pipeline_prefs(message: str) -> dict[str, Any]:
    result = await tool_prefs(action="set" if any(k in message.lower() for k in ("budget", "balanced", "quality")) else "status", message=message)
    return {
        "ok": bool(result.get("ok")),
        "pipeline": "prefs",
        "speak": str(result.get("speak") or result.get("question") or "Prefs."),
        "result": result,
    }


async def pipeline_food(message: str) -> dict[str, Any]:
    lower = message.lower()
    action = "summary"
    if any(k in lower for k in ("swiggy", "zomato", "zepto", "blinkit", "order history", "ingest")):
        action = "ingest"
    result = await tool_food(action=action, message=message)
    return {
        "ok": bool(result.get("ok")),
        "pipeline": "food",
        "speak": str(result.get("speak") or result.get("hint") or "Food memory."),
        "result": result,
    }


async def pipeline_health(message: str) -> dict[str, Any]:
    lower = message.lower()
    action = "scan" if any(k in lower for k in ("my food", "what i eat", "scan")) else "check"
    result = await tool_health(action=action, message=message)
    return {
        "ok": bool(result.get("ok", True)),
        "pipeline": "health",
        "speak": str(result.get("speak") or "Health."),
        "result": result,
    }


async def pipeline_spend(message: str) -> dict[str, Any]:
    lower = message.lower()
    action = "ingest" if any(k in lower for k in ("bank statement", "upi statement", "ingest")) else "coach"
    result = await tool_spend(action=action, message=message)
    return {
        "ok": bool(result.get("ok", True)),
        "pipeline": "spend",
        "speak": str(result.get("speak") or result.get("question") or "Spend."),
        "result": result,
    }


async def pipeline_wellbeing(message: str) -> dict[str, Any]:
    lower = message.lower()
    if any(k in lower for k in ("stop check-in", "disable check-in")):
        action = "disable"
    elif any(k in lower for k in ("enable check-in", "start check-in")):
        action = "enable"
    elif any(k in lower for k in ("how am i", "check in", "check-in", "how are you doing")):
        action = "prompt"
    else:
        action = "reply" if message.strip() else "status"
    result = await tool_wellbeing(action=action, message=message)
    return {
        "ok": bool(result.get("ok", True)),
        "pipeline": "wellbeing",
        "speak": str(result.get("speak") or result.get("question") or "Wellbeing."),
        "result": result,
    }


async def pipeline_todos(message: str) -> dict[str, Any]:
    lower = message.lower()
    if lower.startswith("done ") or "mark done" in lower:
        action = "done"
    elif any(k in lower for k in ("daily todos", "tasks for today", "create my todos")):
        action = "plan"
    elif lower.startswith("todo:") or lower.startswith("task:") or "add todo" in lower:
        action = "add"
    elif "what's on today" in lower or "whats on today" in lower:
        action = "brief"
    else:
        action = "list"
    result = await tool_todos(action=action, message=message)
    return {
        "ok": bool(result.get("ok", True)),
        "pipeline": "todos",
        "speak": str(result.get("speak") or result.get("question") or "Todos."),
        "result": result,
    }


async def pipeline_planner(message: str) -> dict[str, Any]:
    lower = message.lower()
    if "list plans" in lower or "my plans" in lower:
        action = "list"
    else:
        action = "create"
    result = await tool_planner(action=action, message=message)
    return {
        "ok": bool(result.get("ok", True)),
        "pipeline": "planner",
        "speak": str(result.get("speak") or "Plan ready."),
        "result": result,
    }


async def pipeline_sale_watch(message: str) -> dict[str, Any]:
    lower = message.lower()
    if any(k in lower for k in ("list watches", "sale status", "watching status")):
        action = "status" if "status" in lower else "list"
    elif any(k in lower for k in ("check sale", "check watches", "run sale watch")):
        action = "check"
    elif any(k in lower for k in ("stop watching", "remove watch")):
        action = "stop"
    else:
        action = "add"
    result = await tool_sale_watch(action=action, message=message)
    return {
        "ok": bool(result.get("ok", True)),
        "pipeline": "sale_watch",
        "speak": str(result.get("speak") or "Sale watch."),
        "result": result,
    }


async def pipeline_i18n(message: str) -> dict[str, Any]:
    lower = message.lower()
    if "what language" in lower or "current language" in lower:
        action = "status"
    elif any(k in lower for k in ("speak", "reply in", "language pack", "switch language", "set language")):
        action = "set"
    else:
        action = "status"
    result = await tool_i18n(action=action, message=message)
    return {
        "ok": bool(result.get("ok", True)),
        "pipeline": "i18n",
        "speak": str(result.get("speak") or "Language."),
        "result": result,
    }


async def pipeline_neuro(message: str) -> dict[str, Any]:
    lower = message.lower()
    if any(k in lower for k in ("upgrade", "make yourself better", "improve my style", "learn rules")):
        action = "improve"
    elif any(k in lower for k in ("off", "disable", "turn off")):
        action = "disable"
    elif any(k in lower for k in ("adhd", "anxiety", "short-focus", "short focus", "depression", "low-energy", "i have")):
        action = "set"
    else:
        action = "status"
    result = await tool_neuro(action=action, message=message)
    return {
        "ok": bool(result.get("ok", True)),
        "pipeline": "neuro",
        "speak": str(result.get("speak") or result.get("question") or "Neuro."),
        "result": result,
    }


async def pipeline_android(message: str) -> dict[str, Any]:
    lower = message.lower()
    if any(k in lower for k in ("start android", "start emulator")):
        action = "start"
    elif "install apk" in lower:
        action = "install"
    elif any(k in lower for k in ("test apk", "e2e", "smoke test", "test my app")):
        action = "test"
    elif "avd" in lower:
        action = "avds"
    elif "devices" in lower:
        action = "devices"
    else:
        action = "status"
    result = await tool_android(action=action, message=message)
    return {
        "ok": bool(result.get("ok", True)),
        "pipeline": "android",
        "speak": str(result.get("speak") or result.get("hint") or "Android lab."),
        "result": result,
    }


async def pipeline_sandbox(message: str) -> dict[str, Any]:
    lower = message.lower()
    if any(k in lower for k in ("destroy sandbox", "wipe sandbox", "delete sandbox")):
        action = "destroy"
    elif any(k in lower for k in ("mark safe", "mark suspicious", "mark malware", "verdict")):
        action = "verdict"
    elif any(k in lower for k in ("create sandbox", "new sandbox", "hyper-v", "hyperv", "suspicious")):
        action = "create"
    elif "list" in lower:
        action = "list"
    else:
        action = "status"
    result = await tool_sandbox(action=action, message=message)
    return {
        "ok": bool(result.get("ok", True)),
        "pipeline": "sandbox",
        "speak": str(result.get("speak") or "Sandbox."),
        "result": result,
    }


async def pipeline_camera(message: str) -> dict[str, Any]:
    result = await tool_camera(action="see", message=message)
    return {
        "ok": bool(result.get("ok")),
        "pipeline": "camera",
        "speak": str(result.get("speak") or result.get("error") or "Camera."),
        "result": result,
    }


async def pipeline_music(message: str) -> dict[str, Any]:
    lower = message.lower()
    if any(k in lower for k in ("suggest songs", "similar songs", "recommend songs", "song suggestions")):
        action = "suggest"
    elif any(k in lower for k in ("my playlist", "ingest playlist", "save playlist", "spotify.com", "music.youtube")):
        action = "ingest"
    else:
        action = "summary"
    result = await tool_music(action=action, message=message)
    return {
        "ok": bool(result.get("ok", True)),
        "pipeline": "music",
        "speak": str(result.get("speak") or result.get("hint") or "Music taste."),
        "result": result,
    }


async def pipeline_books(message: str) -> dict[str, Any]:
    lower = message.lower()
    if "add book" in lower:
        action = "add"
    elif "great line" in lower:
        action = "line"
    elif any(k in lower for k in ("any book", "a book", "random book", "another quote", "another one", "one more")):
        action = "random"
    elif "quote from" in lower:
        title_hint = lower.split("quote from", 1)[-1].strip()
        # "quote from any book" / vague → random from vault, not research
        if not title_hint or title_hint in {"any book", "a book", "some book", "books"} or "any book" in title_hint:
            action = "random"
        else:
            action = "quote"
    elif any(k in lower for k in ("list books", "book vault")):
        action = "list"
    elif "quote" in lower:
        action = "random"
    else:
        action = "random"
    result = await tool_books(action=action, message=message)
    speak = str(result.get("speak") or "Books.")
    if action == "random" and "want another" not in speak.lower():
        speak = speak.rstrip() + " Want another?"
    return {
        "ok": bool(result.get("ok", True)),
        "pipeline": "books",
        "speak": speak,
        "result": result,
    }


async def pipeline_style_colors(message: str) -> dict[str, Any]:
    lower = message.lower()
    action = "shop" if any(k in lower for k in ("outfit from", "shop for", "buy clothes")) else "analyze"
    result = await tool_style_colors(action=action, message=message)
    return {
        "ok": bool(result.get("ok", True)),
        "pipeline": "style_colors",
        "speak": str(result.get("speak") or result.get("question") or "Style colors."),
        "result": result,
    }


async def pipeline_curiosity(message: str) -> dict[str, Any]:
    lower = message.lower()
    if "disable curiosity" in lower:
        action = "disable"
    elif "enable curiosity" in lower:
        action = "enable"
    elif "curiosity status" in lower:
        action = "status"
    else:
        action = "surprise"
    result = await tool_curiosity(action=action, message=message)
    return {
        "ok": bool(result.get("ok", True)),
        "pipeline": "curiosity",
        "speak": str(result.get("speak") or "Curiosity."),
        "result": result,
    }


async def pipeline_windows(message: str) -> dict[str, Any]:
    from voxoryl.windows_ops import is_new_tab_app_request, is_open_app_request

    if is_new_tab_app_request(message):
        action = "new_tab"
    elif is_open_app_request(message):
        action = "open"
    else:
        action = "auto"
    result = await tool_windows(action=action, message=message)
    return {
        "ok": bool(result.get("ok", True)),
        "pipeline": "windows",
        "speak": str(result.get("speak") if result.get("speak") is not None else result.get("hint") or ""),
        "result": result,
        "fast": True,
        "action_only": True,
    }


async def pipeline_net_profile(message: str) -> dict[str, Any]:
    lower = message.lower()
    if any(k in lower for k in ("reset network", "work is done", "work's done", "clear proxy", "disable proxy")):
        action = "reset"
    elif "confirm proxy" in lower:
        action = "confirm"
    elif any(k in lower for k in ("set proxy", "enable proxy", "enable vpn", "use proxy")):
        action = "set"
    else:
        action = "status"
    result = await tool_net_profile(action=action, message=message)
    return {
        "ok": bool(result.get("ok", True)),
        "pipeline": "net_profile",
        "speak": str(result.get("speak") or result.get("hint") or "Network profile."),
        "result": result,
    }


async def pipeline_self_upgrade(message: str) -> dict[str, Any]:
    lower = message.lower()
    if "ask cursor to" in lower:
        action = "cursor"
    elif any(k in lower for k in ("fix your bug", "log bug")):
        action = "bug"
    elif any(k in lower for k in ("voxoryl can't", "voxoryl cant", "add this feature to yourself")):
        action = "cant"
    elif any(k in lower for k in ("list upgrades", "self upgrade list")):
        action = "list"
    else:
        action = "log"
    result = await tool_self_upgrade(action=action, message=message)
    return {
        "ok": bool(result.get("ok", True)),
        "pipeline": "self_upgrade",
        "speak": str(result.get("speak") or "Self-upgrade logged."),
        "result": result,
    }


async def pipeline_multitask(message: str) -> dict[str, Any]:
    result = await tool_multitask(action="run", message=message)
    return {
        "ok": bool(result.get("ok", True)),
        "pipeline": "multitask",
        "speak": str(result.get("speak") or result.get("hint") or "Multitask."),
        "result": result,
    }


PIPELINES: dict[str, PipelineFn] = {
    "market": pipeline_market,
    "research": pipeline_research,
    "email": pipeline_email,
    "github": pipeline_github,
    "screen_look": pipeline_screen_look,
    "screen_act": pipeline_screen_act,
    "flash_fill": pipeline_flash_fill,
    "screen_type": pipeline_screen_type,
    "powertoys": pipeline_powertoys,
    "chrome": pipeline_chrome,
    "save_reel": pipeline_save_reel,
    "whatsapp": pipeline_whatsapp,
    "remember_project": pipeline_remember_project,
    "melody": pipeline_melody,
    "media": pipeline_media,
    "leads": pipeline_leads,
    "approvals": pipeline_approvals,
    "sites": pipeline_sites,
    "qna": pipeline_qna,
    "companion": pipeline_companion,
    "bookings": pipeline_bookings,
    "docs": pipeline_docs,
    "prefs": pipeline_prefs,
    "food": pipeline_food,
    "health": pipeline_health,
    "spend": pipeline_spend,
    "wellbeing": pipeline_wellbeing,
    "todos": pipeline_todos,
    "planner": pipeline_planner,
    "sale_watch": pipeline_sale_watch,
    "i18n": pipeline_i18n,
    "neuro": pipeline_neuro,
    "android": pipeline_android,
    "sandbox": pipeline_sandbox,
    "camera": pipeline_camera,
    "music": pipeline_music,
    "books": pipeline_books,
    "style_colors": pipeline_style_colors,
    "curiosity": pipeline_curiosity,
    "windows": pipeline_windows,
    "net_profile": pipeline_net_profile,
    "self_upgrade": pipeline_self_upgrade,
    "multitask": pipeline_multitask,
}


def match_pipeline(message: str) -> str | None:
    """Router keyword → deterministic pipeline (LocalClaw pattern)."""
    lower = message.lower()
    # Multitask first when clear parallel intents
    if wants_multitask(message):
        return "multitask"
    if any(
        k in lower
        for k in (
            "surprise me",
            "interesting website",
            "teach me something new",
            "curiosity nudge",
            "enable curiosity",
            "disable curiosity",
        )
    ):
        return "curiosity"
    if any(
        k in lower
        for k in (
            "my playlist",
            "suggest songs",
            "similar songs",
            "music taste",
            "recommend songs",
            "song suggestions",
            "ingest playlist",
            "save playlist",
        )
    ):
        return "music"
    if any(
        k in lower
        for k in (
            "add book",
            "great line",
            "quote from",
            "book vault",
            "random book line",
            "quote from any book",
            "a quote from",
            "give me a quote",
            "give me a short quote",
            "book quote",
        )
    ) or (("quote" in lower) and ("book" in lower)):
        return "books"
    if any(
        k in lower
        for k in (
            "what colors suit me",
            "colors suit me",
            "outfit from this photo",
            "color analysis",
            "colors from this photo",
        )
    ):
        return "style_colors"
    # App-scoped new tab (Windows Notepad tabs, etc.) — before Chrome "new tab"
    _non_browser_apps = (
        "notepad",
        "spotify",
        "vscode",
        "vs code",
        "cursor",
        "whatsapp",
        "calculator",
        "explorer",
        "terminal",
        "powershell",
    )
    if re.search(r"\bnew\s+tab\b", lower) and any(a in lower for a in _non_browser_apps):
        return "windows"
    if (
        any(k in lower for k in ("open notepad", "launch notepad", "start notepad", "notepad app"))
        or (any(k in lower for k in ("open ", "launch ", "start ")) and "notepad" in lower)
    ) and re.search(r"\b(tab|new tab)\b", lower):
        return "windows"

    # Chrome profile / tab / URL — before generic "open chrome" windows path
    # Require chrome/open/select/click context — bare owner name must NOT open Chrome
    # Bare "new tab" is Chrome; app-scoped "new tab in notepad" already returned windows above.
    from voxoryl.config import settings as _settings

    _chrome_profile_phrases = (
        "chrome profile",
        "select profile",
        "who's using chrome",
        "who is using chrome",
        "photo profile",
        "my profile",
        "personal profile",
        "open openai",
        "open chatgpt",
        "open apple",
        "open youtube",
        "go to youtube",
        "switch to youtube",
        "goto youtube",
        "play the first video",
        "play first video",
        "play the second video",
        "play the third video",
        "play second video",
        "play third video",
        "open a youtube",
        "youtube video",
        "new tab",
        "open github",
        "open gmail",
        "open my personal gmail",
        "personal gmail",
        "open personal gmail",
    )
    _owner_tokens = [p for p in _settings.chrome_profile_match_phrases if len(p) > 2]
    _owner_chrome = any(k in lower for k in _owner_tokens) and any(
        k in lower
        for k in (
            "chrome",
            "browser",
            "profile",
            "open",
            "launch",
            "select",
            "click",
            "use",
            "switch",
            " id",
            "youtube",
        )
    )
    if any(k in lower for k in _chrome_profile_phrases) or _owner_chrome or (
        any(k in lower for k in ("chrome", "google chrome", "browser"))
        and any(k in lower for k in ("profile", " id", "tab", "openai", "youtube", "apple", "my "))
    ) or (
        any(k in lower for k in ("go to ", "switch to ", "goto ", "open "))
        and any(
            k in lower
            for k in (
                "youtube",
                "openai",
                "chatgpt",
                "apple",
                "github",
                "gmail",
                "instagram",
                "whatsapp web",
                "reddit",
                "notion",
            )
        )
    ) or bool(
        re.search(
            r"(?:play|open|click|start)\s+(?:the\s+)?"
            r"(?:first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th|\d{1,2}(?:st|nd|rd|th)?)\s+video",
            lower,
        )
    ):
        return "chrome"
    if any(
        k in lower
        for k in (
            "lower brightness",
            "raise brightness",
            "brightness up",
            "brightness down",
            "volume up",
            "volume down",
            "mute",
            "unmute",
            "powertoys",
            "open chrome",
            "open edge",
            "open notepad",
            "open spotify",
            "launch chrome",
            "start chrome",
            "chrome khol",
            "browser khol",
            "khol sakta",
            "khol do",
            "chrome browser",
        )
    ) or (
        any(k in lower for k in ("khol", "kholo", "open ", "launch ", "start "))
        and any(
            k in lower
            for k in (
                "chrome",
                "edge",
                "firefox",
                "notepad",
                "spotify",
                "vscode",
                "vs code",
                "cursor",
                "whatsapp",
                "calculator",
                "explorer",
                "browser",
            )
        )
    ):
        return "windows"
    if any(
        k in lower
        for k in (
            "brightness",
            "volume",
            "mute",
            "powertoys",
            "louder",
            "quieter",
            "dim",
        )
    ) and any(k in lower for k in ("brightness", "volume", "mute", "powertoys", "louder", "quieter", "dim")):
        return "windows"
    if any(
        k in lower
        for k in (
            "set proxy",
            "enable vpn",
            "enable proxy",
            "reset network",
            "work is done",
            "work's done",
            "confirm proxy",
            "clear proxy",
        )
    ):
        return "net_profile"
    if any(
        k in lower
        for k in (
            "voxoryl can't",
            "voxoryl cant",
            "add this feature to yourself",
            "fix your bug",
            "ask cursor to",
            "self upgrade",
        )
    ):
        return "self_upgrade"
    if any(
        k in lower
        for k in (
            "what am i showing",
            "what am i holding",
            "look at this",
            "see this",
            "use the camera",
            "camera look",
            "webcam",
        )
    ):
        return "camera"
    if any(
        k in lower
        for k in (
            "android emulator",
            "start android",
            "start emulator",
            "test apk",
            "android lab",
            "list avd",
            "android devices",
            "e2e test my app",
            "test my android",
        )
    ):
        return "android"
    if any(
        k in lower
        for k in (
            "hyper-v",
            "hyperv",
            "create sandbox",
            "destroy sandbox",
            "suspicious software",
            "sandbox vm",
            "mark safe",
            "mark suspicious",
            "mark malware",
        )
    ):
        return "sandbox"
    if any(
        k in lower
        for k in (
            "adhd mode",
            "adhd-friendly",
            "short-focus mode",
            "short focus mode",
            "neuro adapt",
            "neuro-adapt",
            "i have adhd",
            "i have anxiety",
            "upgrade my style",
            "make yourself better for me",
            "can't read long",
            "cant read long",
        )
    ):
        return "neuro"
    if any(
        k in lower
        for k in (
            "research ",
            "look up ",
            "search for ",
            "what does the docs say",
            "according to documentation",
        )
    ) or lower.startswith(("research", "look up", "search for")):
        return "research"
    if any(
        k in lower
        for k in (
            "plan my day",
            "plan my week",
            "plan a trip",
            "plan a study",
            "study plan",
            "event plan",
            "project plan",
            "trip plan",
            "weekly plan",
            "daily plan",
            "plan today",
            "list plans",
            "my plans",
        )
    ):
        return "planner"
    if any(
        k in lower
        for k in (
            "watch this on amazon",
            "watch on amazon",
            "tell me when on sale",
            "when on sale",
            "check olx for",
            "price watch",
            "sale watch",
            "list watches",
            "sale status",
            "stop watching",
            "watch this on",
        )
    ):
        return "sale_watch"
    if any(
        k in lower
        for k in (
            "speak hindi",
            "reply in hindi",
            "reply in english",
            "language pack",
            "switch language",
            "set language",
            "download language",
            "what language",
            "current language",
        )
    ):
        return "i18n"
    if any(
        k in lower
        for k in (
            "todo:",
            "task:",
            "add todo",
            "daily todos",
            "tasks for today",
            "what's on today",
            "whats on today",
            "mark done",
            "no deadline",
        )
    ) or lower.startswith("done "):
        return "todos"
    if any(
        k in lower
        for k in (
            "check in",
            "check-in",
            "how am i doing",
            "wellbeing",
            "stop check-in",
            "enable check-in",
        )
    ):
        return "wellbeing"
    if any(
        k in lower
        for k in (
            "bank statement",
            "upi statement",
            "how much did i spend",
            "spending this month",
            "slow down on spend",
        )
    ):
        return "spend"
    if any(
        k in lower
        for k in (
            "swiggy",
            "zomato",
            "zepto",
            "blinkit",
            "order history",
            "what do i eat",
            "food memory",
            "my food likes",
        )
    ):
        return "food"
    if any(
        k in lower
        for k in (
            "bad compounds",
            "health check",
            "unhealthy food",
            "stop eating",
            "what's bad in",
            "health flag",
        )
    ):
        return "health"
    if any(
        k in lower
        for k in (
            "optimize for budget",
            "optimize for quality",
            "optimize for balanced",
            "my preference is budget",
            "my preference is quality",
            "my preference is balanced",
            "spending preference",
        )
    ):
        return "prefs"
    if any(
        k in lower
        for k in (
            "cheapest flight",
            "cheap ticket",
            "find flights",
            "book flight",
            "book train",
            "find tickets",
            "hotel booking",
            "itr",
            "income tax return",
            "file itr",
            "passport application",
            "gst return",
            "govt form",
            "government form",
        )
    ):
        return "bookings"
    if any(
        k in lower
        for k in (
            "docs vault",
            "list docs",
            "store this doc",
            "add document",
            "pull docs for",
            "documents for itr",
        )
    ):
        return "docs"
    if any(
        k in lower
        for k in (
            "analyze my chat",
            "analyze my chats",
            "whatsapp export",
            "instagram export",
            "chat export",
            "brutal honest",
            "be my therapist",
            "companion analysis",
            "why we broke up",
            "why she left",
            "why he left",
            "read my chats",
        )
    ):
        return "companion"
    # Active QnA: any reply counts as answer (except clear other commands)
    sess = _load_session()
    if sess and sess.get("status") == "active":
        if any(
            k in lower
            for k in (
                "stop qna",
                "end qna",
                "cancel qna",
                "qna status",
            )
        ):
            return "qna"
        # Don't steal hard tool commands mid-interview
        if not any(
            k in lower
            for k in (
                "generate image",
                "create a website",
                "flash fill",
                "scout leads",
                "look at my screen",
            )
        ):
            return "qna"
    if any(
        k in lower
        for k in (
            "start qna",
            "begin qna",
            "interview me",
            "learn about me",
            "qna session",
            "ask me questions",
            "get to know me",
        )
    ):
        return "qna"
    if any(
        k in lower
        for k in (
            "create a website",
            "create a site",
            "build a website",
            "build a site",
            "make a website",
            "make a site",
            "deploy to vercel",
            "host on vercel",
            "list sites",
            "hair salon website",
            "website for",
            "site for this",
        )
    ):
        return "sites"
    if any(
        k in lower
        for k in (
            "generate image",
            "generate video",
            "make an image",
            "make a video",
            "create an image",
            "auto generate",
            "comfy",
            "pollinations",
            "revise media",
        )
    ):
        return "media"
    if any(
        k in lower
        for k in (
            "scout leads",
            "find businesses",
            "need a website",
            "need websites",
            "draft outreach",
            "mjml",
            "approve lead",
            "reject lead",
            "send approved leads",
            "list leads",
        )
    ):
        return "leads"
    if any(k in lower for k in ("pending approval", "list approvals", "approve all", "reject all")) or (
        ("approve " in lower or "reject " in lower) and "lead" not in lower
    ):
        return "approvals"
    if any(
        k in lower
        for k in (
            "hum",
            "melody",
            "midi",
            "fl studio",
            "what song is",
            "identify this song",
            "shazam",
            "put this in",
            "mellow guitar",
            "bpm",
        )
    ) and any(
        k in lower
        for k in (
            "hum",
            "melody",
            "midi",
            "song",
            "guitar",
            "piano",
            "daw",
            "fl studio",
            "track",
            "notes",
            "shazam",
        )
    ):
        return "melody"
    if any(
        k in lower
        for k in (
            "save this reel",
            "save the reel",
            "ingest this",
            "save this tip",
            "save this link",
            "from instagram",
            "productivity reel",
            "share inbox",
        )
    ):
        return "save_reel"
    if "whatsapp" in lower:
        return "whatsapp"
    if any(
        k in lower
        for k in (
            "flash fill",
            "flash-fill",
            "autofill",
            "auto fill",
            "fill this form",
            "fill the form",
            "fill form",
            "paste my details",
            "fill with my",
        )
    ):
        return "flash_fill"
    if any(
        k in lower
        for k in (
            "powertoys run",
            "power toys run",
            "pt run",
            "keep awake",
            "stay awake",
            "don't sleep",
            "dont sleep",
            "stop awake",
            "allow sleep",
            "fancyzones",
            "fancy zones",
            "zone editor",
            "color picker",
            "text extractor",
            "extract text from screen",
            "find my mouse",
            "where is my mouse",
            "always on top",
            "mouse jump",
        )
    ) or (lower.strip() in {"powertoys", "power toys"}):
        return "powertoys"
    if (
        lower.startswith("type ")
        or lower.startswith("paste ")
        or lower.startswith("write ")
        or lower.startswith("likho ")
        or lower.startswith("likh ")
        or any(k in lower for k in ("type this:", "paste this:", "type exactly"))
    ):
        return "screen_type"
    if any(k in lower for k in ("look at my screen", "see my screen", "what's on my screen", "whats on my screen", "kya dikh raha", "screen pe kya")) and not any(
        k in lower for k in ("click", "type", "fill", "control", "use this")
    ):
        return "screen_look"
    if any(
        k in lower
        for k in (
            "click",
            "type into",
            "use this website",
            "use this app",
            "use this software",
            "take control",
            "control my",
            "click on",
            "press the",
            "fill that",
            "screen pe click",
            "yahan click",
        )
    ):
        return "screen_act"
    if "email" in lower or "inbox" in lower:
        return "email"
    if any(k in lower for k in ("my github", "github repos", "list repos", "my repositories", "check github")):
        return "github"
    if "market" in lower or "fresh ideas" in lower:
        return "market"
    if any(k in lower for k in ("remember that", "my project is", "log that")):
        return "remember_project"
    return None


async def run_pipeline(name: str, message: str) -> dict[str, Any]:
    fn = PIPELINES.get(name)
    if not fn:
        return {"ok": False, "error": f"unknown pipeline {name}"}
    _ = knowledge.retrieve_relevant(message)
    return await fn(message)
