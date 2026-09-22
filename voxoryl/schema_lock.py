from __future__ import annotations

import json
from typing import Any

from voxoryl.llm import chat_local, parse_json_loose


ROUTER_SCHEMA_HINT = (
    '{"mode":"council|direct|answer","speak":"string","tools":[{"name":"string","args":{}}],'
    '"use_council":false,"confidence":0.0}'
)


def validate_router_plan(plan: dict[str, Any] | None) -> dict[str, Any]:
    """Schema lock for router JSON — coerce into a safe shape."""
    if not isinstance(plan, dict):
        return {
            "mode": "answer",
            "speak": "Allow me a moment.",
            "tools": [],
            "use_council": False,
            "confidence": 0.4,
            "_schema_repaired": True,
        }
    mode = str(plan.get("mode") or "answer").lower()
    if mode not in {"council", "direct", "answer"}:
        mode = "answer"
    tools_in = plan.get("tools") or []
    tools: list[dict[str, Any]] = []
    if isinstance(tools_in, list):
        for t in tools_in:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name") or "").lower().strip()
            if not name:
                continue
            args = t.get("args") if isinstance(t.get("args"), dict) else {}
            tools.append({"name": name, "args": args})
    confidence = plan.get("confidence")
    try:
        confidence = float(confidence) if confidence is not None else 0.6
    except (TypeError, ValueError):
        confidence = 0.6
    confidence = max(0.0, min(1.0, confidence))
    return {
        "mode": mode,
        "speak": str(plan.get("speak") or "On it.")[:500],
        "tools": tools,
        "use_council": bool(plan.get("use_council")) if mode != "answer" else False,
        "confidence": confidence,
    }


async def repair_json_with_model(raw: str, schema_hint: str = ROUTER_SCHEMA_HINT) -> dict[str, Any] | None:
    fixed = await chat_local(
        [
            {
                "role": "system",
                "content": f"Repair into ONLY valid JSON matching: {schema_hint}",
            },
            {"role": "user", "content": raw[:3000]},
        ],
        temperature=0.0,
    )
    return parse_json_loose(fixed)


async def locked_router_parse(raw: str) -> dict[str, Any]:
    parsed = parse_json_loose(raw)
    if parsed is None:
        parsed = await repair_json_with_model(raw)
    return validate_router_plan(parsed)
