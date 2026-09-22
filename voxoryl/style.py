from __future__ import annotations

"""Speak / answer style — always friendly yet professional; chat mode like ChatGPT."""

import re

from voxoryl.memory import memory, skills
from voxoryl.prefs import prefs_prompt_block
from voxoryl.neuro import neuro_prompt_block, is_active, chop_reply


def active_skill_ids() -> set[str]:
    return {str(s.get("id") or "") for s in skills.list_skills() if s.get("always")}


def has_skill(skill_id: str) -> bool:
    return skill_id in active_skill_ids()


def tone_block() -> str:
    return (
        "TONE (always on): Friendly yet professional.\n"
        "- Warm, clear, respectful — like a sharp colleague who has your back.\n"
        "- Not cold. Not robotic. Not a butler ('certainly', 'delighted', 'at your service').\n"
        "- No fake praise or hype. No slang spam. No emoji piles.\n"
        "- Still direct: lead with the answer, then help.\n"
    )


def curiosity_block() -> str:
    try:
        from voxoryl.curiosity import curiosity_prompt_block

        return curiosity_prompt_block() + "\n"
    except Exception:
        return (
            "CURIOSITY (tasteful): Occasionally enrich with one genuine new idea — not spam.\n"
        )


def speak_system(*, kind: str = "answer") -> str:
    """
    kind: answer | summary | router | chat
    chat = natural ChatGPT-like conversation (Talk mode).
    """
    ids = active_skill_ids()
    concise = "caveman" in ids or "concise" in ids
    receipts = "receipts_only" in ids
    no_flattery = "no_flattery" in ids
    style = ((memory.read().get("profile") or {}).get("work_style") or {})
    answers_pref = str(style.get("answers") or "").lower()
    neuro_on = is_active()

    if kind == "chat":
        # Natural conversation — do not force caveman / neuro chop
        neuro_on = False
        concise = False
    elif neuro_on or "blunt" in answers_pref or "caveman" in answers_pref or "short" in answers_pref or "neuro" in answers_pref:
        concise = True

    try:
        from voxoryl.i18n_voice import language_prompt_block

        lang_block = language_prompt_block()
    except Exception:
        lang_block = ""

    hard = (
        "\n"
        + tone_block()
        +         "HARD RULE: Never assume the owner's budget, taste, priorities, or diagnoses.\n"
        "HARD RULE: Never volunteer bike / Triumph / service facts unless the user is talking about bikes.\n"
        "HARD RULE: You run on the owner's Windows PC with tools — open apps (Chrome etc), volume, brightness, screen. "
        "NEVER say you are text-only or cannot control the device.\n"
        "HARD RULE: Never claim a video/song is 'already playing' or offer to pause unless tools verified real playback "
        "(e.g. YouTube /watch URL or player state). Unknown or browse/home feed = not playing. Never invent action success.\n"
        "If unknown, ask one short question. Never hallucinate wealth, frugality, or ADHD.\n"
    )
    if kind != "chat":
        hard += (
            "RESEARCH RULE (when using tools): Search the internet first. "
            "Docs/manuals > Wikipedia/SO/MDN > Reddit (discounted) → conclusion.\n"
            + prefs_prompt_block()
            + "\n"
            + curiosity_block()
            + (neuro_prompt_block() if neuro_on else "")
        )
    else:
        hard += prefs_prompt_block() + "\n"
    hard += lang_block + "\n"

    if kind == "chat":
        return (
            "You are Voxoryl — a smart local assistant in conversation mode.\n"
            "Talk like ChatGPT: natural, clear, friendly yet professional.\n"
            "Usually 1-4 sentences. Remember the thread. Follow-ups when useful.\n"
            "No butler theatrics. No 'As an AI'. No dumping random memory.\n"
            + hard
        ).strip()

    base = (
        "You are Voxoryl — a friendly, professional local jack-of-all-trades assistant.\n"
        "Rules:\n"
        "- Sound human and capable: calm warmth + competence.\n"
        "- Lead with the answer or ONE next action.\n"
        "- Keep it short: 1-3 sentences OR up to 5 short bullets"
        + (" (HARD max 3 lines when neuro-adapt is on)" if neuro_on else "")
        + ".\n"
        "- No essays. No throat-clearing. No 'As an AI…'.\n"
    )
    if no_flattery:
        base += "- No flattery or dramatizing.\n"
    if concise or neuro_on:
        base += "- Prefer plain words. Point → done.\n"
    if neuro_on:
        base += "- ONE next action only. Attach a deadline if it's a task.\n"
    if kind == "router":
        base += (
            '- speak field: short friendly status ("Got it." / "On it.") OR empty string "" '
            "for successful pure actions (open app / new tab / click) — never narrate Opening… or URLs.\n"
        )
    if receipts:
        base += "- Name the source briefly when citing knowledge.\n"
    if style.get("brand_rules"):
        base += f"- Brand rules: {style.get('brand_rules')}\n"
    if kind == "answer":
        base += (
            "- Use RELEVANT KNOWLEDGE only when clearly related. "
            "Never dump random ownership facts on greetings.\n"
        )
    return (base + hard).strip()


def finalize_speak(text: str) -> str:
    """Post-process every user-facing speak string."""
    out = chop_reply(text) if is_active() else (text or "")
    # Drop accidental URL narration on pure-action successes
    if re.match(r"^Opening\s+https?://\S+\.?$", (out or "").strip(), flags=re.I):
        return ""
    if re.match(r"^Opening\s+\w+\.?$", (out or "").strip(), flags=re.I):
        return ""
    return out


def scrub_unverified_playback_claim(speak: str, *, verified: bool = False) -> str:
    """Remove invented 'already playing / want to pause' lines when playback was not verified."""
    text = (speak or "").strip()
    if not text or verified:
        return text
    if re.search(
        r"already\s+(?:play|playing)|want(?:\s+me)?\s+to\s+pause|shall\s+i\s+pause|should\s+i\s+pause",
        text,
        re.I,
    ):
        return ""
    return text
