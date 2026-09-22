from __future__ import annotations

import asyncio
import io
import re
from typing import Any

from voxoryl.code_act import tool_code_act
from voxoryl.config import settings
from voxoryl.council import run_council
from voxoryl.ingest import tool_ingest_share, tool_whatsapp
from voxoryl.knowledge import knowledge, log_message_to_knowledge, tool_knowledge
from voxoryl.llm import chat_local
from voxoryl.mcp_bridge import tool_mcp
from voxoryl.memory import memory, skills
from voxoryl.pipelines import match_pipeline, run_pipeline
from voxoryl.schema_lock import locked_router_parse
from voxoryl.screen import tool_screen
from voxoryl.style import speak_system, finalize_speak, scrub_unverified_playback_claim
from voxoryl.tools import (
    tool_computer,
    tool_email,
    tool_github,
    tool_market_scan,
    tool_marketing,
    tool_notes,
    tool_remember,
    tool_research,
    tool_video,
)
from voxoryl.verifier import verify_plan


GREETING_RE = re.compile(
    r"^\s*("
    r"hello|hi|hey|yo|sup|howdy|"
    r"good\s*(morning|afternoon|evening|night)|"
    r"thanks|thank\s*you|ty|thx|"
    r"ok|okay|k|cool|great|nice|cheers|"
    r"bye|goodbye|see\s*ya|later|"
    r"how\s+are\s+you|what'?s\s+up|whats\s+up"
    r")[\s!.?,]*$",
    re.I,
)

# Knowledge must clear this to inject into speak / fast-path Q&A
KNOWLEDGE_SCORE_MIN = 6.0

SOCIAL_MARKERS = (
    "hello",
    "hi ",
    "hi,",
    "hey",
    "how are you",
    "how's it going",
    "how r you",
    "good morning",
    "good evening",
    "i am your creator",
    "i'm your creator",
    "my name is",
    "i am ",
    "nice to meet",
)


def _continuation_pipeline(message: str) -> str | None:
    """Map short yes/more to the same pipeline the last assistant turn was about."""
    if not _is_continuation(message):
        return None
    try:
        from voxoryl.chat_session import load

        msgs = list(load().get("messages") or [])
    except Exception:
        return None

    # If the last user ask was about books/quotes, continue that thread
    last_user = ""
    for m in reversed(msgs):
        if m.get("role") == "user":
            last_user = str(m.get("content") or "").lower()
            break
    if any(k in last_user for k in ("quote", "book", "great line", "alchemist", "gatsby")):
        return "books"

    candidates: list[str] = []
    for m in reversed(msgs):
        if m.get("role") != "assistant":
            continue
        c = str(m.get("content") or "").strip()
        if not c:
            continue
        candidates.append(c)
        if len(candidates) >= 4:
            break
    blob = ""
    for c in candidates:
        cl = c.lower()
        if (
            "?" in c
            or "want another" in cl
            or "would you like" in cl
            or "quote" in cl
            or 'try "' in cl
            or "try “" in cl
            or " by " in cl
        ):
            blob = cl
            break
    if not blob and candidates:
        blob = candidates[0].lower()
    if not blob:
        return None
    if any(
        k in blob
        for k in (
            "quote",
            "want another",
            "would you like a different book",
            "different book",
            "great line",
            "book vault",
            "here is a quote",
            'try "',
            "try “",
            "opening chrome",  # don't treat app opens as book offers
        )
    ):
        if "opening " in blob and "quote" not in blob and "try" not in blob:
            return None
        if any(k in blob for k in ("quote", "want another", "book", 'try "', "try “", " by ", "gatsby", "alchemist")):
            return "books"
    if ("book" in blob or " by " in blob or 'try "' in blob) and (
        "?" in blob or "another" in blob or "like" in blob or "try" in blob
    ):
        return "books"
    return None


def _is_continuation(message: str) -> bool:
    """Short yes/more/go-ahead — must continue prior turn, not reset to greeting."""
    lower = (message or "").strip().lower()
    if not lower:
        return False
    if lower in {
        "yes",
        "yep",
        "yeah",
        "yup",
        "sure",
        "ok",
        "okay",
        "k",
        "alright",
        "all right",
        "go for it",
        "go ahead",
        "do it",
        "please",
        "more",
        "another",
        "one more",
        "hit me",
        "continue",
        "keep going",
        "why not",
        "of course",
        "definitely",
    }:
        return True
    return bool(
        re.match(
            r"^\s*(sure|yes|yeah|yep|ok|okay|alright)?[,!.\s]*"
            r"(go\s+for\s+it|go\s+ahead|keep\s+going|continue|another|one\s+more|more\s+please|hit\s+me).*$",
            lower,
        )
    )


def _deterministic_social_reply(message: str) -> str | None:
    """Golden no-ops: hi/thanks/okay — 0 LLM when phrase is exact social."""
    lower = (message or "").strip().lower()
    table = {
        "hi": "Hey.",
        "hello": "Hey.",
        "hey": "Hey.",
        "thanks": "Anytime.",
        "thank you": "Anytime.",
        "ty": "Anytime.",
        "thx": "Anytime.",
        "okay": "Okay.",
        "ok": "Okay.",
        "k": "Okay.",
        "cool": "Cool.",
        "great": "Great.",
    }
    return table.get(lower)


def _is_greeting_or_smalltalk(message: str) -> bool:
    lower = (message or "").strip().lower()
    if not lower:
        return True
    # Affirmations with prior chat = continue thread, not fresh smalltalk
    try:
        from voxoryl.chat_session import has_recent_context

        if has_recent_context() and _is_continuation(message):
            return False
    except Exception:
        pass
    if GREETING_RE.match(lower):
        return True
    # Bare yes/sure only count as smalltalk when there is no prior thread
    if lower in {"yes", "no", "yep", "nope", "sure", "alright", "all right", "got it", "noted"}:
        try:
            from voxoryl.chat_session import has_recent_context

            if has_recent_context():
                return False
        except Exception:
            pass
        return True
    # Social intros / how-are-you with extra words (ChatGPT-style chat openers)
    words = lower.split()
    if len(words) <= 28 and any(m in lower for m in SOCIAL_MARKERS):
        # Not a tool / PC-control request (open Chrome, YouTube play, etc.)
        if not any(
            k in lower
            for k in (
                "research",
                "plan ",
                "todo",
                "email",
                "github",
                "screen",
                "install",
                "watch ",
                "search",
                "book flight",
                "quote ",
                "book ",
                "open ",
                "launch ",
                "chrome",
                "browser",
                "youtube",
                "gmail",
                "notepad",
                "play ",
                "click ",
                "new tab",
                "profile",
            )
        ):
            return True
    return False


async def _finish_turn(message: str, payload: dict[str, Any], *, user_already_logged: bool = False) -> dict[str, Any]:
    """Persist every turn into chat_session (with rolling summary) before returning."""
    speak = str(payload.get("speak") or "").strip()
    try:
        from voxoryl.chat_session import append as chat_append, compact_if_needed

        if not user_already_logged:
            chat_append("user", message)
        if speak:
            chat_append("assistant", speak)
        # Don't block the reply on summarization
        try:
            asyncio.create_task(compact_if_needed())
        except Exception:
            await compact_if_needed()
    except Exception:
        pass
    if speak and "episode" not in payload:
        try:
            memory.remember_episode(summary=message[:240], outcome=speak[:240])
        except Exception:
            pass
    return payload


def _mentions_bike(message: str) -> bool:
    lower = (message or "").lower()
    return any(k in lower for k in ("bike", "triumph", "motorcycle", "400 xc", "service"))


def _knowledge_is_relevant(retrieved: dict[str, Any] | None, message: str = "") -> bool:
    matches = (retrieved or {}).get("matches") or []
    if not matches:
        return False
    top = matches[0] or {}
    score = float(top.get("score") or 0)
    if score < KNOWLEDGE_SCORE_MIN:
        return False
    # Never inject bike/service vault into unrelated chat
    blob = f"{top.get('heading') or ''} {top.get('fact') or ''} {top.get('text') or ''}".lower()
    if any(k in blob for k in ("triumph", "bike service", "1st service", "first service")) and not _mentions_bike(message):
        return False
    return True


ROUTER_SYSTEM = """You are Voxoryl intent router for the owner.
Return ONLY JSON:
{
  "mode": "council" | "direct" | "answer",
  "speak": "short spoken reply (1 sentence if caveman skill on). Only cite RELEVANT KNOWLEDGE when it clearly answers this message — never dump unrelated vault facts on greetings/small talk. For pure actions (open app, new tab, click, mute) that succeed, speak MUST be empty string — never narrate 'Opening…' or read URLs aloud.",
  "tools": [{"name": "research|market_scan|notes|remember|knowledge|github|email|computer|screen|marketing|video|mcp|code_act|ingest|reels|whatsapp|melody|media|leads|approvals|sites|qna|companion|bookings|docs|prefs|food|health|spend|wellbeing|todos|neuro|android|sandbox|camera|music|books|style_colors|curiosity|windows|net_profile|self_upgrade|multitask|planner|sale_watch|i18n", "args": {}}],
  "use_council": false,
  "confidence": 0.0
}
Rules:
- confidence: 0-1 how sure you are.
- NEVER assume budget/taste/diagnosis — if unknown ask. Never hallucinate wealth, frugality, or ADHD.
- Voxoryl is a jack-of-all-trades local agent — not a medical product. Accessibility modes are optional.
- mode "answer" for greetings, small talk, and normal chat. Do NOT inject random knowledge on hello/hi/thanks/ok.
- ACTION SILENCE: successful open/click/hotkey/new-tab tools → speak "". Only speak when answering a question or reporting an error the user must hear.
- HONESTY: NEVER claim media is "already playing" or offer pause unless a tool result verified /watch (or equivalent). If play/click failed or state is unknown, say a short failure or stay silent — never invent success.
- Only use RELEVANT KNOWLEDGE when the owner asks about those facts or the score is clearly on-topic.
- screen: look|act|flash_fill for display control. camera: see what user shows via webcam.
- SAFETY: never clear/overwrite text Voxoryl did not type; never delete files outside the Voxoryl project. Fail closed.
- android: emulator E2E (start/install/smoke). sandbox: Hyper-V disposable VM for untrusted software then destroy.
- ingest: save reel/link/tip into knowledge vault. reels: share Instagram Reel URL/file with VOXORYL inbox, or recall “what was that reel about?”. whatsapp: status|send|ingest.
- mcp: action=list|call for MCP servers. code_act: goal=... for sandboxed Python in workspace.
- melody: hum→MIDI / find song by lyrics / place in DAW. Args: action=analyze|to_midi|find_song|place.
- media: auto image/video loop. leads: scout/draft MJML/approve/send.
- sites: create website + Vercel approve. qna: interview. companion: local chat analysis.
- bookings: genuine-site tickets/gov forms. docs: local legal vault. prefs: spending preference.
- food: order history taste memory. health: optional food flags (habit nudge only, not medical care). spend: bank coach.
- wellbeing: optional check-ins. todos: MUST have deadlines or refuse.
- neuro: optional short-focus replies — ASK never assume. Not a clinical product.
- music: playlists / taste / suggest songs. books: vault + great lines. style_colors: photo→colors→genuine shops (ask value_mode).
- curiosity: ALWAYS enrich with genuine interesting sites / ideas (tasteful). windows: brightness/volume. net_profile: proxy/VPN ask+reset.
- self_upgrade: log gaps/bugs → Cursor prompt. multitask: run parallel intents when user says while / at the same time.
- planner: day/week/trip/study/event/project plans with deadlines. sale_watch: Amazon/OLX price watches.
- i18n: language pack / reply language / TTS voice locale.
- RESEARCH (always): internet first — docs/manuals > Wikipedia/StackOverflow/MDN > Reddit/chatter (discount opinions) → one conclusion.
- Prefer direct tools over council unless the task is hard/ambiguous.
"""


async def route_and_act(message: str, *, force_council: bool = False) -> dict[str, Any]:
    # L0-R: realtime deterministic commands — zero LLM / zero memory / zero VL
    if not force_council:
        try:
            from voxoryl.l0r import execute_l0r, match_l0r

            cmd = match_l0r(message, partial=False)
            if cmd:
                out = await execute_l0r(cmd)
                speak = finalize_speak(str(out.get("speak") or ""))
                return await _finish_turn(
                    message,
                    {
                        "ok": bool(out.get("ok", True)),
                        "mode": "l0r",
                        "l0r": cmd,
                        "speak": speak,
                        "tools": [{"tool": "l0r", "result": out}],
                        "knowledge": None,
                        "retrieved": [],
                        "council": None,
                    },
                )
        except Exception:
            pass

    # Golden social no-ops — always deterministic (0 LLM), even with chat history
    if not force_council:
        det = _deterministic_social_reply(message)
        if det is not None:
            return await _finish_turn(
                message,
                {
                    "ok": True,
                    "mode": "noop",
                    "speak": det,
                    "model_tier": "deterministic",
                    "llm_calls": 0,
                    "vl_calls": 0,
                    "tools": [],
                    "knowledge": None,
                    "retrieved": [],
                    "council": None,
                },
            )

    # Auto language pack when user clearly switches language
    try:
        from voxoryl.i18n_voice import maybe_auto_switch

        maybe_auto_switch(message)
    except Exception:
        pass

    smalltalk = _is_greeting_or_smalltalk(message)

    # LocalClaw-style deterministic pipelines first (fast + reliable on 4B)
    pipe_name = None if force_council else match_pipeline(message)
    if not pipe_name and not force_council:
        pipe_name = _continuation_pipeline(message)
    if pipe_name:
        # Fast PC commands: no knowledge retrieve / rewrite (keeps talk snappy)
        fast_pipes = {"windows", "net_profile", "i18n", "todos", "books", "powertoys", "screen_type", "chrome"}
        retrieved: dict[str, Any] = {"matches": [], "context": ""}
        pipe = await run_pipeline(pipe_name, message)
        # Pure-action pipelines may return empty speak — do not coerce to "Done."
        action_silent = bool(pipe.get("action_only")) or pipe_name in {
            "windows",
            "chrome",
            "powertoys",
            "screen_type",
        }
        raw_speak = pipe.get("speak")
        if action_silent and pipe.get("ok", True) and (raw_speak is None or str(raw_speak).strip() == ""):
            speak = ""
        else:
            speak = str(raw_speak or "Done.")
        if pipe_name not in fast_pipes and not smalltalk and not pipe.get("fast"):
            retrieved = await knowledge.retrieve_relevant_async(message)
            if _knowledge_is_relevant(retrieved, message):
                speak = await _knowledge_answer(
                    message,
                    speak,
                    retrieved.get("context") or "",
                    [{"tool": "pipeline", "result": pipe}],
                )
        speak = finalize_speak(speak)
        if pipe_name == "chrome":
            speak = scrub_unverified_playback_claim(
                speak, verified=bool(pipe.get("verified") and pipe.get("ok"))
            )
        return await _finish_turn(
            message,
            {
                "ok": bool(pipe.get("ok", True)),
                "mode": "pipeline",
                "pipeline": pipe_name,
                "speak": speak,
                "action_only": action_silent and not speak,
                "tools": [{"tool": "pipeline", "result": pipe}],
                "knowledge": None,
                "retrieved": retrieved.get("matches") or [],
                "council": None,
            },
        )

    # Greetings / social chat → deterministic goldens first, then tiny model
    if not force_council and smalltalk:
        from voxoryl.chat_session import append as chat_append, history_for_llm
        from voxoryl.inference import effective_mode

        chat_append("user", message)
        det = _deterministic_social_reply(message)
        if det is not None:
            return await _finish_turn(
                message,
                {
                    "ok": True,
                    "mode": "noop",
                    "speak": det,
                    "model_tier": "deterministic",
                    "llm_calls": 0,
                    "vl_calls": 0,
                    "tools": [],
                    "knowledge": None,
                    "retrieved": [],
                    "council": None,
                },
                user_already_logged=True,
            )
        use_cloud = effective_mode() == "cloud"
        # Tiny local model: short prompt + short history. Cloud: fuller chat context.
        if use_cloud:
            hist = history_for_llm(limit=12)
            system = (
                speak_system(kind="chat")
                + "\nYou are Voxoryl in a normal conversation. Reply naturally in 1-3 short sentences. "
                "You can control this Windows PC (tools run locally). Never claim text-only. "
                "Use history for follow-ups. Do not invent vault facts or tasks."
            )
            tier: str = "main"
        else:
            hist = history_for_llm(limit=6)
            system = (
                "You are Voxoryl, a friendly local AI on the owner's PC. "
                "Reply in 1-2 short natural sentences. Be warm and brisk. "
                "Do not invent tasks, vault facts, or bike/service details."
            )
            tier = "fast"
        try:
            speak = await chat_local(
                [{"role": "system", "content": system}, *hist],
                temperature=0.55,
                tier=tier,  # type: ignore[arg-type]
                kind="agent",
            )
        except Exception:
            # Offline / Ollama-down: never fail golden social turns
            speak = _deterministic_social_reply(message) or "Hey — Voxoryl here."
        speak = re.sub(r"[#*`]", "", speak).strip()[:600] or "Hey — Voxoryl here. How can I help today?"
        return await _finish_turn(
            message,
            {
                "ok": True,
                "mode": "chat",
                "speak": speak,
                "model_tier": "cloud" if use_cloud else tier,
                "tools": [],
                "knowledge": None,
                "retrieved": [],
                "council": None,
            },
            user_already_logged=True,
        )

    # Knowledge Q&A fast path — answer from vault without multi-step router thrash
    lower = message.lower().strip()
    looks_like_q = any(
        lower.startswith(p)
        for p in ("what", "who", "when", "where", "which", "how", "do i", "did i", "is my", "are my")
    ) or "?" in message
    retrieved = await knowledge.retrieve_relevant_async(message)
    if (
        not force_council
        and looks_like_q
        and _knowledge_is_relevant(retrieved, message)
        and not any(k in lower for k in ("research", "search for", "look up", "email", "github", "screen", "market"))
    ):
        speak = await _knowledge_answer(message, "", retrieved.get("context") or "", [])
        speak = finalize_speak(speak)
        knowledge_result = await log_message_to_knowledge(message)
        return await _finish_turn(
            message,
            {
                "ok": True,
                "mode": "knowledge",
                "speak": speak,
                "tools": [],
                "knowledge": knowledge_result,
                "retrieved": retrieved.get("matches") or [],
                "council": None,
            },
        )

    # Compound open/play/browser intents must never fall through to memory-dump chat.
    _play_nth = bool(
        re.search(
            r"(?:play|open|click|start)\s+(?:the\s+)?"
            r"(?:first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th|\d{1,2}(?:st|nd|rd|th)?)\s+video",
            lower,
        )
    )
    _action_chrome = (
        any(
            k in lower
            for k in (
                "youtube",
                "chrome",
                "browser",
                "new tab",
                "play the first",
                "play first",
                "play the second",
                "play the third",
                "open gmail",
                "open openai",
            )
        )
        or _play_nth
    ) and (
        any(k in lower for k in ("open", "launch", "go to", "goto", "play", "navigate", "visit", "switch", "click"))
        or _play_nth
    )
    if _action_chrome and not force_council:
        from voxoryl.chrome_control import tool_chrome

        chrome_out = await tool_chrome(action="auto", message=message)
        speak = str(chrome_out.get("speak") or "")
        chrome_ok = bool(chrome_out.get("ok")) and chrome_out.get("verified", True) is not False
        if chrome_out.get("play_ok") is False or chrome_out.get("verified") is False:
            chrome_ok = False
        if chrome_ok and chrome_out.get("action_only") and not speak.strip():
            speak = ""
        speak = finalize_speak(speak)
        speak = scrub_unverified_playback_claim(
            speak, verified=bool(chrome_out.get("verified") and chrome_ok)
        )
        slim = {
            k: chrome_out.get(k)
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
                "error",
            )
            if k in chrome_out
        }
        return await _finish_turn(
            message,
            {
                "ok": chrome_ok,
                "mode": "pipeline",
                "pipeline": "chrome",
                "speak": speak,
                "action_only": bool(chrome_out.get("action_only")) and not speak and chrome_ok,
                "tools": [{"tool": "chrome", "result": slim}],
                "knowledge": None,
                "retrieved": [],
                "council": None,
            },
        )

    mem = memory.context_block(limit=8, query=message)
    skill_block = skills.skills_prompt()
    relevant_ok = _knowledge_is_relevant(retrieved, message)
    relevant = (retrieved.get("context") or "(none)") if relevant_ok else "(none — not on-topic for this message)"
    know_full = knowledge.context_block(800) if relevant_ok else "(skipped — low relevance)"
    try:
        from voxoryl.cold_memory import prompt_block as cold_block

        cold = cold_block(message)
    except Exception:
        cold = ""
    try:
        from voxoryl.chat_session import history_text_for_router

        chat_ctx = history_text_for_router()
    except Exception:
        chat_ctx = ""
    try:
        from voxoryl.screen import screen_context_prompt

        screen_ctx = screen_context_prompt()
    except Exception:
        screen_ctx = ""

    raw = await chat_local(
        [
            {"role": "system", "content": ROUTER_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Recent conversation (use for follow-ups like 'sure' / 'go for it' / 'another'):\n"
                    f"{chat_ctx or '(none yet)'}\n\n"
                    f"{screen_ctx}"
                    f"Memory:\n{mem}\n\nSkills:\n{skill_block}\n\n"
                    f"{cold}\n"
                    f"RELEVANT KNOWLEDGE (cite ONLY if clearly related to this message):\n{relevant}\n\n"
                    f"Knowledge index (truncated):\n{know_full}\n\n"
                    f"Owner said:\n{message}"
                ),
            },
        ],
        temperature=0.2,
        kind="agent",
    )
    plan = await locked_router_parse(raw)
    if retrieved.get("matches") and relevant_ok and plan.get("mode") == "council" and not plan.get("tools"):
        plan["mode"] = "answer"
        plan["use_council"] = False

    if not plan.get("tools"):
        if any(k in lower for k in ("hum", "melody", "midi", "fl studio", "mellow guitar")):
            place = any(k in lower for k in ("fl", "daw", "put", "guitar", "track"))
            plan.update(
                {
                    "mode": "direct",
                    "use_council": False,
                    "tools": [
                        {
                            "name": "melody",
                            "args": {
                                "action": "place" if place else "to_midi",
                                "message": message,
                                "instrument": "mellow guitar",
                                "place_in_daw": place,
                            },
                        }
                    ],
                }
            )
        elif any(k in lower for k in ("flash fill", "fill this form", "autofill", "paste my details")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "screen", "args": {"action": "flash_fill", "goal": message}}]})
        elif lower.startswith("type ") or lower.startswith("paste ") or lower.startswith("likho "):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "screen", "args": {"action": "type", "goal": message, "text": message}}]})
        elif any(k in lower for k in ("look at my screen", "see my screen", "what's on my screen", "whats on my screen")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "screen", "args": {"action": "look", "goal": message}}]})
        elif any(k in lower for k in ("click ", "type into", "take control", "control my screen")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "screen", "args": {"action": "act", "goal": message}}]})
        elif any(
            k in lower
            for k in (
                "powertoys run",
                "keep awake",
                "fancyzones",
                "color picker",
                "text extractor",
                "find my mouse",
                "always on top",
            )
        ) or lower.strip() in {"powertoys", "power toys"}:
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "powertoys", "args": {"message": message}}]})
        elif (
            any(
                k in lower
                for k in (
                    "open openai",
                    "open chatgpt",
                    "open apple",
                    "open youtube",
                    "go to youtube",
                    "switch to youtube",
                    "play the first video",
                    "play first video",
                    "play the second video",
                    "play the third video",
                    "play second video",
                    "chrome profile",
                    "my profile",
                    "photo profile",
                    "personal gmail",
                )
            )
            or bool(
                re.search(
                    r"(?:play|open|click|start)\s+(?:the\s+)?"
                    r"(?:first|second|third|fourth|fifth|1st|2nd|3rd|4th|5th|\d{1,2}(?:st|nd|rd|th)?)\s+video",
                    lower,
                )
            )
            or (
                "new tab" in lower
                and not any(
                    a in lower
                    for a in ("notepad", "spotify", "vscode", "vs code", "cursor", "whatsapp", "calculator")
                )
            )
            or (
                any(p in lower for p in settings.chrome_profile_match_phrases if len(p) > 2)
                and any(k in lower for k in ("chrome", "browser", "profile", "open", "click", "select", " id"))
            )
            or (
                "chrome" in lower
                and any(k in lower for k in ("profile", "openai", "youtube", "apple", "tab", "my "))
            )
        ):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "chrome", "args": {"message": message}}]})
        elif re.search(r"\bnew\s+tab\b", lower) and any(
            a in lower for a in ("notepad", "spotify", "vscode", "vs code", "cursor", "whatsapp")
        ):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "windows", "args": {"action": "new_tab", "message": message}}]})
        elif any(
            k in lower
            for k in (
                "what was that reel",
                "what was the reel",
                "about that reel",
                "about the reel",
                "share this reel",
                "share reel",
                "save this reel",
                "save this tip",
                "ingest this",
                "from instagram",
            )
        ):
            about = any(k in lower for k in ("what was that reel", "what was the reel", "about that reel", "about the reel"))
            if about or "instagram.com" in lower or "instagr.am" in lower:
                plan.update(
                    {
                        "mode": "direct",
                        "use_council": False,
                        "tools": [
                            {
                                "name": "reels",
                                "args": {
                                    "action": "about" if about else "ingest",
                                    "message": message,
                                },
                            }
                        ],
                    }
                )
            else:
                plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "ingest", "args": {"message": message}}]})
        elif "whatsapp" in lower:
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "whatsapp", "args": {"action": "status" if "send" not in lower else "send", "message": message}}]})
        elif "mcp" in lower:
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "mcp", "args": {"action": "list"}}]})
        elif any(k in lower for k in ("run code", "code act", "python in workspace")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "code_act", "args": {"goal": message}}]})
        elif any(k in lower for k in ("organize", "rearrange")):
            plan.update({"mode": "direct", "use_council": False, "speak": "Tidying the workspace.", "tools": [{"name": "computer", "args": {"action": "organize"}}]})
        elif any(k in lower for k in ("cheapest flight", "find flights", "book flight", "find tickets", "itr", "income tax", "passport application", "govt form")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "bookings", "args": {"message": message}}]})
        elif any(k in lower for k in ("docs vault", "pull docs", "documents for itr", "list docs")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "docs", "args": {"action": "for_task" if "itr" in lower or "for" in lower else "list", "message": message}}]})
        elif any(k in lower for k in ("optimize for budget", "optimize for quality", "my preference is")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "prefs", "args": {"action": "set", "message": message}}]})
        elif any(k in lower for k in ("swiggy", "zomato", "zepto", "blinkit", "order history", "what do i eat", "food memory")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "food", "args": {"message": message}}]})
        elif any(k in lower for k in ("bad compounds", "health check", "unhealthy", "what's bad in", "stop eating")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "health", "args": {"message": message}}]})
        elif any(k in lower for k in ("bank statement", "upi statement", "how much did i spend", "spending this month")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "spend", "args": {"message": message}}]})
        elif any(k in lower for k in ("check in", "check-in", "how am i doing", "wellbeing")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "wellbeing", "args": {"message": message}}]})
        elif any(k in lower for k in ("todo:", "task:", "add todo", "daily todos", "tasks for today", "what's on today", "whats on today")) or lower.startswith("done "):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "todos", "args": {"message": message}}]})
        elif any(
            k in lower
            for k in (
                "plan my day",
                "plan my week",
                "plan a trip",
                "study plan",
                "event plan",
                "project plan",
                "trip plan",
                "weekly plan",
            )
        ):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "planner", "args": {"message": message}}]})
        elif any(
            k in lower
            for k in (
                "watch this on amazon",
                "watch on amazon",
                "tell me when on sale",
                "when on sale",
                "check olx for",
                "price watch",
                "sale watch",
            )
        ):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "sale_watch", "args": {"message": message}}]})
        elif any(
            k in lower
            for k in (
                "speak hindi",
                "reply in",
                "language pack",
                "switch language",
                "set language",
                "download language",
            )
        ):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "i18n", "args": {"message": message}}]})
        elif any(
            k in lower
            for k in (
                "adhd mode",
                "adhd-friendly",
                "short-focus",
                "short focus mode",
                "neuro adapt",
                "neuro-adapt",
                "i have adhd",
                "upgrade my style",
                "make yourself better for me",
            )
        ):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "neuro", "args": {"message": message}}]})
        elif any(k in lower for k in ("what am i showing", "look at this", "see this", "webcam", "use the camera")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "camera", "args": {"message": message}}]})
        elif any(k in lower for k in ("android emulator", "start android", "test apk", "android lab", "test my android")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "android", "args": {"message": message}}]})
        elif any(k in lower for k in ("hyper-v", "hyperv", "create sandbox", "destroy sandbox", "suspicious software", "mark safe", "mark malware")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "sandbox", "args": {"message": message}}]})
        elif any(k in lower for k in ("my playlist", "suggest songs", "music taste", "recommend songs")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "music", "args": {"message": message}}]})
        elif any(k in lower for k in ("add book", "great line", "quote from")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "books", "args": {"message": message}}]})
        elif any(k in lower for k in ("what colors suit", "outfit from this photo", "colors suit me")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "style_colors", "args": {"message": message}}]})
        elif any(k in lower for k in ("surprise me", "interesting website", "teach me something new")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "curiosity", "args": {"message": message}}]})
        elif any(
            k in lower
            for k in (
                "lower brightness",
                "volume up",
                "volume down",
                "mute",
                "powertoys",
                "open chrome",
                "open edge",
                "chrome khol",
                "browser khol",
                "khol sakta",
                "launch chrome",
                "start chrome",
            )
        ) or (
            any(k in lower for k in ("khol", "kholo", "open ", "launch "))
            and any(k in lower for k in ("chrome", "edge", "notepad", "spotify", "browser", "vscode", "cursor"))
        ):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "windows", "args": {"message": message, "action": "open"}}]})
        elif any(k in lower for k in ("set proxy", "enable vpn", "reset network", "work is done", "confirm proxy")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "net_profile", "args": {"message": message}}]})
        elif any(k in lower for k in ("voxoryl can't", "voxoryl cant", "add this feature to yourself", "fix your bug", "ask cursor to")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "self_upgrade", "args": {"message": message}}]})
        elif any(k in lower for k in (" at the same time", "multitask", " while ")) and "while" in lower:
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "multitask", "args": {"message": message}}]})
        elif any(k in lower for k in ("analyze my chat", "chat export", "brutal honest", "be my therapist", "why we broke up", "read my chats")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "companion", "args": {"action": "analyze", "message": message}}]})
        elif any(k in lower for k in ("start qna", "interview me", "learn about me", "ask me questions", "get to know me")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "qna", "args": {"action": "start", "message": message}}]})
        elif any(k in lower for k in ("stop qna", "end qna")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "qna", "args": {"action": "stop"}}]})
        elif any(k in lower for k in ("create a website", "build a website", "make a website", "website for", "site for")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "sites", "args": {"action": "create", "brief": message}}]})
        elif any(k in lower for k in ("generate image", "make an image", "create an image", "generate video", "make a video")):
            kind = "video" if "video" in lower else "image"
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "media", "args": {"action": "auto", "brief": message, "kind": kind}}]})
        elif any(k in lower for k in ("find businesses", "scout leads", "need a website", "draft outreach", "approve lead", "mjml")):
            action = "scout"
            if "draft" in lower or "mjml" in lower:
                action = "draft"
            elif "approve" in lower:
                action = "approve"
            elif "reject" in lower:
                action = "reject"
            elif "send" in lower:
                action = "send"
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "leads", "args": {"action": action, "message": message}}]})
        elif any(k in lower for k in ("list approvals", "pending approval")) or (
            ("approve " in lower or "reject " in lower) and "lead" not in lower
        ):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "approvals", "args": {"action": "list" if "list" in lower else ("approve" if "approve" in lower else "reject"), "message": message}}]})
        elif "video" in lower:
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "media", "args": {"action": "auto", "brief": message, "kind": "video"}}]})
        elif "marketing" in lower or "promo" in lower:
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "marketing", "args": {"brief": message}}]})
        elif any(k in lower for k in ("mind map", "mindmap", "knowledge graph")):
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "knowledge", "args": {"action": "mindmap"}}]})
        elif "research" in lower or "search for" in lower or "look up" in lower:
            plan.update({"mode": "direct", "use_council": False, "tools": [{"name": "research", "args": {"query": message}}]})

    tool_names = [((t.get("name") or "").lower()) for t in (plan.get("tools") or [])]
    if tool_names and set(tool_names) <= {"windows", "computer", "i18n"}:
        verification = {"used": False, "skipped": True, "approve": True, "revised_speak": plan.get("speak")}
    else:
        verification = await verify_plan(message, plan, relevant if relevant_ok else "")
        if verification.get("revised_speak"):
            plan["speak"] = verification["revised_speak"]
        if verification.get("used") and verification.get("approve") is False:
            plan["tools"] = []
            plan["mode"] = "answer"
            plan["speak"] = str(verification.get("reason") or "I held back — that plan looked unsafe or unclear.")

    use_council = force_council or bool(plan.get("use_council")) or plan.get("mode") == "council"
    tool_results: list[dict[str, Any]] = []
    council = None

    if use_council:
        council = await run_council(message, execute=True, knowledge_context=relevant if relevant_ok else "")
        speak = str((council.get("synthesis") or {}).get("decision") or plan.get("speak") or "Done.")
        speak = await _spoken_summary(
            message,
            speak,
            {"council": council, "knowledge": relevant if relevant_ok else ""},
        )
        speak = finalize_speak(speak)
        knowledge_result = await log_message_to_knowledge(message)
        return await _finish_turn(
            message,
            {
                "ok": True,
                "mode": "council",
                "speak": speak,
                "plan": plan,
                "council": council,
                "tools": council.get("executed"),
                "knowledge": knowledge_result,
                "retrieved": retrieved.get("matches") or [],
            },
        )

    for spec in plan.get("tools") or []:
        name = (spec.get("name") or "").lower()
        args = spec.get("args") or {}
        tool_results.append({"tool": name, "result": await _dispatch(name, args, message)})

    draft = str(plan.get("speak") or "Task complete.")
    if tool_results:
        screen_hit = next((t for t in tool_results if t.get("tool") == "screen"), None)
        if screen_hit and isinstance(screen_hit.get("result"), dict):
            r = screen_hit["result"]
            draft = str(
                r.get("speak")
                or (r.get("vision") or {}).get("description")
                or draft
            )[:700]
        # Prefer tool's own short speak — skip slow LLM rewrite for PC / simple tools
        tool_speak = None
        action_only_ok = False
        for t in tool_results:
            res = t.get("result")
            if isinstance(res, dict) and "speak" in res:
                tool_speak = str(res.get("speak") or "")
                if res.get("action_only") and res.get("ok", True) and not tool_speak.strip():
                    action_only_ok = True
                break
        fast_tools = {"windows", "computer", "i18n", "todos", "pipeline", "chrome"}
        if action_only_ok and any(t.get("tool") in fast_tools | {"chrome", "windows"} for t in tool_results):
            draft = ""
        elif tool_speak and any(t.get("tool") in fast_tools for t in tool_results):
            draft = tool_speak
        else:
            draft = await _spoken_summary(
                message,
                draft,
                {"tools": tool_results, "knowledge": relevant if relevant_ok else ""},
            )
    elif relevant_ok:
        draft = await _knowledge_answer(message, draft, relevant, tool_results)
    else:
        # Normal chat / answer — multi-turn, no unrelated knowledge
        from voxoryl.chat_session import append as chat_append, history_for_llm
        from voxoryl.cold_memory import prompt_block as cold_block, mark_settled
        from voxoryl.screen import screen_context_prompt

        if any(k in message.lower() for k in ("service is done", "already serviced", "service done", "just an example")):
            mark_settled("bike", note="Triumph 400 XC — service topic settled / was example. Do not ask again.")
        chat_append("user", message)
        hist = history_for_llm(limit=16)
        draft = await chat_local(
            [
                {
                    "role": "system",
                    "content": (
                        speak_system(kind="chat")
                        + "\n"
                        + cold_block(message)
                        + screen_context_prompt()
                        + "Reply naturally like ChatGPT: helpful, clear, 1-5 sentences. "
                        "You can see the live screen context above and control the PC. "
                        "Use prior turns. If the user says sure / go for it / more / another after you offered something, "
                        "do that next — never reset to a generic greeting. "
                        "Do not volunteer bike/service facts unless the user is talking about bikes."
                    ),
                },
                *hist,
            ],
            temperature=0.5,
            kind="agent",
        )
        draft = re.sub(r"[#*`]", "", draft).strip()[:700] or draft[:400]
        # assistant logged in _finish_turn; user already appended
        user_logged_chat = True

    # Prefer research tool's own speak (already concluded)
    for t in tool_results:
        if t.get("tool") == "research" and isinstance(t.get("result"), dict):
            rs = t["result"].get("speak")
            if rs:
                draft = str(rs)
            break
        if t.get("tool") in {"planner", "sale_watch", "i18n", "todos", "books"} and isinstance(t.get("result"), dict):
            rs = t["result"].get("speak")
            if rs:
                draft = str(rs)
                break

    speak = draft
    already_logged = any(t.get("tool") == "knowledge" for t in tool_results)
    if already_logged:
        knowledge_result = next((t.get("result") for t in tool_results if t.get("tool") == "knowledge"), None)
    else:
        knowledge_result = await log_message_to_knowledge(message)
    if tool_results:
        speak = finalize_speak(speak)
    return await _finish_turn(
        message,
        {
            "ok": True,
            "mode": plan.get("mode") or ("direct" if tool_results else "chat"),
            "speak": speak,
            "plan": plan,
            "tools": tool_results,
            "council": None,
            "knowledge": knowledge_result,
            "retrieved": (retrieved.get("matches") or []) if relevant_ok else [],
            "verification": verification,
        },
        user_already_logged=locals().get("user_logged_chat", False),
    )


async def _dispatch(name: str, args: dict[str, Any], message: str) -> Any:
    # Prefer capability registry when registered (single surface for agent + council)
    try:
        from voxoryl.capabilities import get_registry

        reg = get_registry()
        if reg.get(name) and name in {
            "research",
            "chrome",
            "browser",
            "screen",
            "windows",
            "github",
            "email",
            "remember",
            "computer",
            "powertoys",
            "pt",
            "wait_for",
        }:
            invoke_name = {"browser": "chrome", "pt": "powertoys"}.get(name, name)
            payload = dict(args or {})
            if "message" not in payload:
                payload["message"] = message
            if invoke_name == "research" and not payload.get("query"):
                payload["query"] = message
            return await reg.invoke(invoke_name, payload)
    except Exception:
        pass

    if name == "research":
        return await tool_research(str(args.get("query") or message))
    if name == "market_scan":
        return await tool_market_scan(args.get("topic") or args.get("query"))
    if name == "notes":
        return await tool_notes(str(args.get("note") or message), title=args.get("title"))
    if name == "remember":
        return await tool_remember(str(args.get("text") or message), kind=str(args.get("kind") or "fact"))
    if name == "knowledge":
        return await tool_knowledge(
            action=str(args.get("action") or "log"),
            heading=str(args.get("heading") or ""),
            text=str(args.get("text") or ""),
            message=str(args.get("message") or message),
        )
    if name == "github":
        return await tool_github(
            action=str(args.get("action") or "list"),
            repo=str(args.get("repo") or ""),
            path=str(args.get("path") or "README.md"),
        )
    if name == "email":
        return await tool_email(limit=int(args.get("limit") or 8))
    if name == "computer":
        return await tool_computer(
            action=str(args.get("action") or "list"),
            path=str(args.get("path") or ""),
            query=str(args.get("query") or ""),
        )
    if name == "screen":
        goal = str(args.get("goal") or message)
        # Pre-dispatch ownership gate for clear/wipe screen goals
        try:
            from voxoryl.safety import gate_text_destruction_goal, goal_requests_text_destruction

            if goal_requests_text_destruction(goal) and str(args.get("action") or "look") in {
                "act",
                "flash_fill",
                "type",
                "paste",
                "click_type",
            }:
                gated = gate_text_destruction_goal(goal)
                if not gated.get("ok"):
                    return gated
        except Exception:
            pass
        return await tool_screen(
            action=str(args.get("action") or "look"),
            goal=goal,
            question=str(args.get("question") or ""),
            text=str(args.get("text") or ""),
            rounds=int(args.get("rounds") or 1),
        )
    if name == "safety":
        from voxoryl.safety import status as safety_status

        return safety_status()
    if name in {"powertoys", "pt"}:
        from voxoryl.powertoys_bridge import tool_powertoys

        return await tool_powertoys(
            action=str(args.get("action") or "auto"),
            message=str(args.get("message") or message),
            query=str(args.get("query") or ""),
        )
    if name in {"chrome", "browser"}:
        from voxoryl.chrome_control import tool_chrome

        return await tool_chrome(
            action=str(args.get("action") or "auto"),
            message=str(args.get("message") or message),
        )
    if name in {"ingest", "share"}:
        return await tool_ingest_share(
            url=str(args.get("url") or ""),
            text=str(args.get("text") or ""),
            message=str(args.get("message") or message),
            use_screen=bool(args.get("use_screen")),
        )
    if name in {"reels", "reel", "instagram"}:
        from voxoryl.reels import tool_reels

        return await tool_reels(
            action=str(args.get("action") or "status"),
            url=str(args.get("url") or ""),
            message=str(args.get("message") or message),
            reel_id=str(args.get("reel_id") or ""),
            video_path=str(args.get("video_path") or ""),
            caption=str(args.get("caption") or ""),
        )
    if name == "whatsapp":
        return await tool_whatsapp(
            action=str(args.get("action") or "status"),
            message=str(args.get("message") or message),
            to=str(args.get("to") or ""),
        )
    if name == "melody":
        from voxoryl.melody import tool_melody

        return await tool_melody(
            action=str(args.get("action") or "to_midi"),
            audio_path=str(args.get("audio_path") or ""),
            message=str(args.get("message") or message),
            instrument=str(args.get("instrument") or "mellow guitar"),
            place_in_daw=bool(args.get("place_in_daw")),
        )
    if name == "media":
        from voxoryl.media_gen import tool_media

        return await tool_media(
            action=str(args.get("action") or "auto"),
            brief=str(args.get("brief") or message),
            message=message,
            kind=str(args.get("kind") or "image"),
            prefer=str(args.get("prefer") or "auto"),
            job_id=str(args.get("job_id") or ""),
            notes=str(args.get("notes") or ""),
        )
    if name == "leads":
        from voxoryl.leads import tool_leads

        return await tool_leads(
            action=str(args.get("action") or "scout"),
            message=str(args.get("message") or message),
            niche=str(args.get("niche") or ""),
            location=str(args.get("location") or ""),
            lead_id=str(args.get("lead_id") or ""),
        )
    if name in {"approvals", "approve"}:
        from voxoryl.approvals import tool_approvals

        return await tool_approvals(
            action=str(args.get("action") or "list"),
            approval_id=str(args.get("approval_id") or ""),
            message=str(args.get("message") or message),
            kind=str(args.get("kind") or ""),
            notes=str(args.get("notes") or ""),
        )
    if name in {"sites", "website", "vercel"}:
        from voxoryl.sites import tool_sites

        return await tool_sites(
            action=str(args.get("action") or "create"),
            brief=str(args.get("brief") or message),
            message=message,
            slug=str(args.get("slug") or ""),
            approval_id=str(args.get("approval_id") or ""),
        )
    if name in {"qna", "interview"}:
        from voxoryl.qna import tool_qna

        return await tool_qna(
            action=str(args.get("action") or "start"),
            message=str(args.get("message") or message),
            answer=str(args.get("answer") or ""),
            rounds=int(args.get("rounds") or 6),
        )
    if name in {"companion", "therapy", "therapist"}:
        from voxoryl.companion import tool_companion

        return await tool_companion(
            action=str(args.get("action") or "analyze"),
            path=str(args.get("path") or ""),
            message=str(args.get("message") or message),
            focus=str(args.get("focus") or ""),
            owner_hint=str(args.get("owner_hint") or ""),
        )
    if name in {"bookings", "tickets", "travel"}:
        from voxoryl.bookings import tool_bookings

        return await tool_bookings(
            action=str(args.get("action") or "tickets"),
            message=str(args.get("message") or message),
            value_mode=str(args.get("value_mode") or ""),
        )
    if name in {"docs", "documents", "vault"}:
        from voxoryl.docs_vault import tool_docs

        return await tool_docs(
            action=str(args.get("action") or "list"),
            message=str(args.get("message") or message),
            path=str(args.get("path") or ""),
            tag=str(args.get("tag") or ""),
            label=str(args.get("label") or ""),
        )
    if name in {"prefs", "preferences"}:
        from voxoryl.prefs import tool_prefs

        return await tool_prefs(
            action=str(args.get("action") or "status"),
            message=str(args.get("message") or message),
            mode=str(args.get("mode") or ""),
            context=str(args.get("context") or ""),
        )
    if name in {"food", "food_memory", "orders"}:
        from voxoryl.food_memory import tool_food

        return await tool_food(
            action=str(args.get("action") or "summary"),
            message=str(args.get("message") or message),
            path=str(args.get("path") or ""),
        )
    if name in {"health", "compounds"}:
        from voxoryl.health import tool_health

        return await tool_health(
            action=str(args.get("action") or "scan"),
            message=str(args.get("message") or message),
            item=str(args.get("item") or ""),
        )
    if name in {"spend", "bank", "money"}:
        from voxoryl.spend import tool_spend

        return await tool_spend(
            action=str(args.get("action") or "coach"),
            message=str(args.get("message") or message),
            path=str(args.get("path") or ""),
        )
    if name in {"wellbeing", "checkin", "check_in"}:
        from voxoryl.wellbeing import tool_wellbeing

        return await tool_wellbeing(
            action=str(args.get("action") or "check"),
            message=str(args.get("message") or message),
        )
    if name in {"todos", "todo", "tasks"}:
        from voxoryl.todos import tool_todos

        return await tool_todos(
            action=str(args.get("action") or "list"),
            message=str(args.get("message") or message),
            title=str(args.get("title") or ""),
            deadline=str(args.get("deadline") or ""),
        )
    if name in {"neuro", "adhd", "accessibility", "neuro_adapt"}:
        from voxoryl.neuro import tool_neuro

        return await tool_neuro(
            action=str(args.get("action") or "status"),
            message=str(args.get("message") or message),
            profiles=str(args.get("profiles") or ""),
        )
    if name in {"android", "emulator", "apk"}:
        from voxoryl.android_lab import tool_android

        return await tool_android(
            action=str(args.get("action") or "status"),
            message=str(args.get("message") or message),
            avd=str(args.get("avd") or ""),
            apk=str(args.get("apk") or ""),
            package=str(args.get("package") or ""),
        )
    if name in {"sandbox", "hyperv", "hyper-v"}:
        from voxoryl.sandbox_hv import tool_sandbox

        return await tool_sandbox(
            action=str(args.get("action") or "status"),
            message=str(args.get("message") or message),
            name=str(args.get("name") or ""),
            verdict=str(args.get("verdict") or ""),
            path=str(args.get("path") or ""),
        )
    if name in {"camera", "webcam", "see"}:
        from voxoryl.camera import tool_camera

        return await tool_camera(
            action=str(args.get("action") or "see"),
            message=str(args.get("message") or message),
            path=str(args.get("path") or ""),
            camera_index=int(args.get("camera_index") or 0),
        )
    if name in {"music", "playlist", "music_taste"}:
        from voxoryl.music_taste import tool_music

        return await tool_music(
            action=str(args.get("action") or "summary"),
            message=str(args.get("message") or message),
            text=str(args.get("text") or ""),
        )
    if name in {"books", "book"}:
        from voxoryl.books import tool_books

        return await tool_books(action=str(args.get("action") or "random"), message=str(args.get("message") or message))
    if name in {"style_colors", "style", "wardrobe", "colors"}:
        from voxoryl.style_colors import tool_style_colors

        return await tool_style_colors(
            action=str(args.get("action") or "analyze"),
            message=str(args.get("message") or message),
            path=str(args.get("path") or ""),
            camera_index=int(args.get("camera_index") or 0),
            value_mode=str(args.get("value_mode") or ""),
        )
    if name in {"curiosity", "surprise"}:
        from voxoryl.curiosity import tool_curiosity

        return await tool_curiosity(action=str(args.get("action") or "surprise"), message=str(args.get("message") or message))
    if name in {"windows", "volume", "brightness", "powertoys"}:
        from voxoryl.windows_ops import is_open_app_request, tool_windows

        act = str(args.get("action") or "")
        msg = str(args.get("message") or message)
        if not act or act == "status":
            act = "open" if is_open_app_request(msg) else "auto"
        return await tool_windows(
            action=act,
            message=msg,
            level=args.get("level"),
            app=str(args.get("app") or ""),
        )
    if name in {"net_profile", "proxy", "vpn", "network"}:
        from voxoryl.net_profile import tool_net_profile

        return await tool_net_profile(
            action=str(args.get("action") or "status"),
            message=str(args.get("message") or message),
            server=str(args.get("server") or ""),
            confirm=bool(args.get("confirm") or False),
        )
    if name in {"self_upgrade", "upgrade_self", "cursor_prompt"}:
        from voxoryl.self_upgrade import tool_self_upgrade

        return await tool_self_upgrade(
            action=str(args.get("action") or "log"),
            message=str(args.get("message") or message),
            kind=str(args.get("kind") or ""),
        )
    if name in {"multitask", "parallel"}:
        from voxoryl.multitask import tool_multitask

        return await tool_multitask(
            action=str(args.get("action") or "run"),
            message=str(args.get("message") or message),
            job_id=str(args.get("job_id") or ""),
        )
    if name in {"planner", "plan", "universal_planner"}:
        from voxoryl.planner import tool_planner

        return await tool_planner(
            action=str(args.get("action") or "create"),
            message=str(args.get("message") or message),
            kind=str(args.get("kind") or ""),
            plan_id=str(args.get("plan_id") or ""),
        )
    if name in {"sale_watch", "price_watch", "watch_sale"}:
        from voxoryl.sale_watch import tool_sale_watch

        return await tool_sale_watch(
            action=str(args.get("action") or "add"),
            message=str(args.get("message") or message),
            watch_id=str(args.get("watch_id") or ""),
        )
    if name in {"i18n", "language", "voice_lang"}:
        from voxoryl.i18n_voice import tool_i18n

        return await tool_i18n(
            action=str(args.get("action") or "status"),
            message=str(args.get("message") or message),
            lang=str(args.get("lang") or ""),
        )
    if name == "mcp":
        return await tool_mcp(
            action=str(args.get("action") or "list"),
            server=str(args.get("server") or ""),
            tool=str(args.get("tool") or ""),
            args=args.get("arguments") if isinstance(args.get("arguments"), dict) else {},
        )
    if name == "code_act":
        return await tool_code_act(str(args.get("goal") or message))
    if name == "marketing":
        return await tool_marketing(str(args.get("brief") or message), channel=str(args.get("channel") or "general"))
    if name == "video":
        return await tool_video(str(args.get("prompt") or message), mode=str(args.get("mode") or "script"))
    return {"ok": False, "error": f"unknown tool {name}"}


async def _knowledge_answer(
    request: str,
    draft: str,
    relevant: str,
    tools: list[dict[str, Any]],
) -> str:
    try:
        from voxoryl.cold_memory import prompt_block as cold_block

        cold = cold_block(request)
    except Exception:
        cold = ""
    text = await chat_local(
        [
            {
                "role": "system",
                "content": (
                    speak_system(kind="answer")
                    + "\n"
                    + cold
                    + "If cold memory says service is SETTLED, answer the bike question factually "
                    "and do NOT offer service reminders or ask about service."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Request: {request}\nDraft: {draft}\n"
                    f"RELEVANT KNOWLEDGE:\n{relevant or '(none)'}\n"
                    f"Tools: {str(tools)[:800]}"
                ),
            },
        ],
        temperature=0.2,
        kind="agent",
    )
    cleaned = re.sub(r"[#*`]", "", text).strip()
    return finalize_speak(cleaned[:500] or draft[:400])


async def _spoken_summary(request: str, draft: str, context: dict[str, Any]) -> str:
    try:
        from voxoryl.chat_session import history_text_for_router

        prior = history_text_for_router(max_chars=1200)
    except Exception:
        prior = ""
    text = await chat_local(
        [
            {
                "role": "system",
                "content": (
                    speak_system(kind="summary")
                    + "\nIf prior conversation shows you offered more (another quote, another tip) "
                    "and the user agreed, deliver that next — do not greet from scratch."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Prior conversation:\n{prior or '(none)'}\n\n"
                    f"Request: {request}\nDraft: {draft}\nContext: {str(context)[:1600]}"
                ),
            },
        ],
        temperature=0.2,
        kind="agent",
    )
    cleaned = re.sub(r"[#*`]", "", text).strip()
    return finalize_speak(cleaned[:500] or draft[:400])


async def synthesize_speech(text: str, *, voice: str | None = None) -> bytes:
    """Natural male neural TTS via edge-tts (Andrew Multi EN / Madhur HI)."""
    raw = (text or "").strip()
    if not raw:
        return b""
    try:
        import edge_tts

        from voxoryl.i18n_voice import voice_for_text

        chosen = voice or voice_for_text(raw)
        # Slightly slower + warmer than default robotic clip
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                communicate = edge_tts.Communicate(
                    raw,
                    chosen,
                    rate="+12%",
                    pitch="-1Hz",
                )
                buf = io.BytesIO()
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        buf.write(chunk["data"])
                audio = buf.getvalue()
                if audio:
                    return audio
            except Exception as exc:
                last_err = exc
                # One retry after brief pause (token / clock skew)
                await asyncio.sleep(0.35)
        if last_err:
            raise last_err
        return b""
    except Exception:
        return b""
