from __future__ import annotations

from typing import Any

from voxoryl.config import settings
from voxoryl.llm import chat_online


async def verify_plan(goal: str, plan: dict[str, Any], knowledge: str = "") -> dict[str, Any]:
    """
    SLM-default / LLM-fallback verifier (Groq).
    If no key, returns pass-through with skipped=True.
    """
    if not settings.groq_api_key.strip():
        return {
            "used": False,
            "skipped": True,
            "approve": True,
            "reason": "No GROQ_API_KEY — local plan accepted",
            "revised_speak": plan.get("speak"),
        }

    conf = float(plan.get("confidence") or 0.5)
    # Only spend cloud tokens when confidence is low or council/screen/act risk
    risky_tools = {t.get("name") for t in (plan.get("tools") or [])}
    needs = conf < 0.55 or bool(risky_tools & {"screen", "email", "computer"}) or plan.get("mode") == "council"
    if not needs:
        return {
            "used": False,
            "skipped": True,
            "approve": True,
            "reason": "Confidence high — skip verifier",
            "revised_speak": plan.get("speak"),
        }

    online = await chat_online(
        [
            {
                "role": "system",
                "content": (
                    "You verify a local Voxoryl agent plan. Reply ONLY JSON: "
                    '{"approve":true/false,"reason":"...","revised_speak":"...","confidence":0-1}. '
                    "Reject destructive or unclear screen/computer actions."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Goal: {goal}\nKnowledge:\n{knowledge[:1200]}\nPlan:\n{plan}"
                ),
            },
        ]
    )
    if not online.get("used"):
        return {
            "used": False,
            "skipped": True,
            "approve": True,
            "reason": online.get("reason") or "verifier unavailable",
            "revised_speak": plan.get("speak"),
        }

    from voxoryl.llm import parse_json_loose

    parsed = parse_json_loose(str(online.get("content") or "")) or {}
    return {
        "used": True,
        "skipped": False,
        "approve": bool(parsed.get("approve", True)),
        "reason": parsed.get("reason") or online.get("content", "")[:300],
        "revised_speak": parsed.get("revised_speak") or plan.get("speak"),
        "confidence": parsed.get("confidence"),
        "model": online.get("model"),
    }
