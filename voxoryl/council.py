from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from voxoryl.config import settings
from voxoryl.llm import chat_local, chat_online, parse_json_loose
from voxoryl.memory import memory, skills

COUNCIL: list[dict[str, str]] = [
    {
        "id": "strategist",
        "name": "Strategist",
        "voice": (
            "You are the Strategist. Think long-term. Protect Saint's goals, "
            "spot leverage, and refuse busywork that doesn't compound."
        ),
    },
    {
        "id": "critic",
        "name": "Critic",
        "voice": (
            "You are the Critic. Stress-test the plan. Find risks, missing info, "
            "false confidence, and safer alternatives. Be sharp but useful."
        ),
    },
    {
        "id": "builder",
        "name": "Builder",
        "voice": (
            "You are the Builder. Be practical. Break work into concrete next actions "
            "Voxoryl can execute with tools on Saint's machine."
        ),
    },
    {
        "id": "scout",
        "name": "Scout",
        "voice": (
            "You are the Scout. Hunt for market signals, research angles, and fresh ideas "
            "connected to what Saint is already building."
        ),
    },
]


async def run_council(
    user_request: str,
    *,
    execute: bool = False,
    knowledge_context: str = "",
) -> dict[str, Any]:
    mem = memory.context_block()
    skill_block = skills.skills_prompt()
    voices: list[dict[str, Any]] = []
    know_note = (
        f"\n\nRELEVANT KNOWLEDGE (reference these facts):\n{knowledge_context}\n"
        if knowledge_context.strip()
        else ""
    )

    for member in COUNCIL:
        prompt = (
            f"{member['voice']}\n\n"
            f"Memory of owner:\n{mem}\n\n"
            f"Skills:\n{skill_block}\n"
            f"{know_note}\n"
            f"Owner's request:\n{user_request}\n\n"
            "Reply in 4-8 sentences. Be specific. No fluff. End with one clear recommendation. "
            "If knowledge facts relate, cite them."
        )
        content = await chat_local(
            [
                {"role": "system", "content": f"You are {member['name']} on Voxoryl Council."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.75,
            kind="agent",
        )
        voices.append({"id": member["id"], "name": member["name"], "content": content})

    online = await chat_online(
        [
            {
                "role": "system",
                "content": (
                    "You are an external advisor for Voxoryl. Review the local council opinions "
                    "and Saint's request. Challenge weak thinking. Prefer actionable truth."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Request:\n{user_request}\n\n"
                    f"Council opinions:\n"
                    + "\n\n".join(f"## {v['name']}\n{v['content']}" for v in voices)
                    + "\n\nGive a concise independent verdict and recommended action."
                ),
            },
        ]
    )

    synthesis_prompt = (
        "You are Voxoryl, chair of the council. Synthesize the voices into ONE decision.\n"
        "Do not jump to conclusions — weigh disagreement.\n"
        "Return ONLY valid JSON with keys: "
        "decision (string), rationale (string), next_actions (array of strings), "
        "confidence (0-1 number), needs_tools (array of strings from "
        '["research","market","notes","github","email","computer","screen","marketing","video","memory","knowledge"]).\n\n'
        f"Request: {user_request}\n\n"
        f"Voices:\n" + "\n\n".join(f"{v['name']}: {v['content']}" for v in voices) + "\n\n"
        f"Online advisor: {online.get('content') or online.get('reason')}"
    )
    raw = await chat_local(
        [
            {"role": "system", "content": "Output JSON only."},
            {"role": "user", "content": synthesis_prompt},
        ],
        temperature=0.2,
        kind="agent",
    )

    synthesis = parse_json_loose(raw) or {
        "decision": raw[:500],
        "rationale": "Model returned freeform text; treated as decision.",
        "next_actions": [],
        "confidence": 0.5,
        "needs_tools": [],
    }

    result = {
        "request": user_request,
        "voices": voices,
        "online": online,
        "synthesis": synthesis,
        "executed": None,
        "at": datetime.now(timezone.utc).isoformat(),
    }

    if execute:
        from voxoryl.tools import execute_plan

        result["executed"] = await execute_plan(user_request, synthesis)

    memory.remember_episode(
        summary=user_request[:240],
        outcome=str(synthesis.get("decision", ""))[:240],
    )
    _append_log(result)
    return result


def _append_log(result: dict[str, Any]) -> None:
    path = settings.council_log_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")
