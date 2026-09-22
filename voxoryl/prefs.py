from __future__ import annotations

"""
Never assume user taste/budget/risk. Ask when unknown. Persist answers.
"""

from typing import Any

from voxoryl.memory import memory
from voxoryl.knowledge import knowledge


VALUE_MODES = ("budget", "balanced", "quality", "ask")


def get_work_style() -> dict[str, Any]:
    return dict(((memory.read().get("profile") or {}).get("work_style") or {}))


def get_value_mode() -> str | None:
    """
    Returns budget|balanced|quality or None if never stated.
    None => MUST ask — do not hallucinate frugal or luxury.
    """
    style = get_work_style()
    mode = str(style.get("value_mode") or "").strip().lower()
    if mode in {"budget", "cheap", "frugal", "lowest"}:
        return "budget"
    if mode in {"quality", "premium", "best", "luxury", "rich"}:
        return "quality"
    if mode in {"balanced", "value", "mid"}:
        return "balanced"
    return None


def set_value_mode(mode: str, *, note: str = "") -> dict[str, Any]:
    m = (mode or "").strip().lower()
    if m in {"cheap", "frugal", "lowest", "low"}:
        m = "budget"
    elif m in {"premium", "best", "luxury", "rich"}:
        m = "quality"
    elif m in {"mid", "value"}:
        m = "balanced"
    if m not in {"budget", "balanced", "quality"}:
        return {"ok": False, "error": "mode must be budget|balanced|quality"}
    style = get_work_style()
    style["value_mode"] = m
    if note:
        style["value_note"] = note[:240]
    memory.update_profile(work_style=style, preferences=[f"Spending preference: {m}" + (f" — {note}" if note else "")])
    knowledge.append_facts(
        "Preferences",
        [f"Owner spending preference is '{m}'" + (f" ({note})" if note else "")],
    )
    return {"ok": True, "value_mode": m, "speak": f"Got it — I'll optimize for {m} from now on (until you change it)."}


def ask_value_preference(context: str = "") -> dict[str, Any]:
    """Standard question when preference unknown — never invent one."""
    q = (
        "Before I pick options: what matters more for this — "
        "(1) lowest real price, (2) balanced value, or (3) best quality even if costlier? "
        "Reply: budget / balanced / quality."
    )
    if context:
        q = f"For {context}: " + q
    return {
        "ok": True,
        "needs_preference": True,
        "question": q,
        "speak": q,
        "options": ["budget", "balanced", "quality"],
        "rule": "Do not assume. Wait for answer.",
    }


def resolve_or_ask(context: str = "") -> dict[str, Any]:
    mode = get_value_mode()
    if mode:
        return {
            "ok": True,
            "needs_preference": False,
            "value_mode": mode,
            "note": get_work_style().get("value_note") or "",
            "speak": f"Using your saved preference: {mode}.",
        }
    return ask_value_preference(context)


def prefs_prompt_block() -> str:
    mode = get_value_mode()
    style = get_work_style()
    lines = [
        "PREFERENCE RULE (hard): Never assume budget, taste, risk, or priorities.",
        "If unknown — ASK in one short question. Do not hallucinate that the owner is poor or rich.",
    ]
    if mode:
        lines.append(f"Saved value_mode: {mode}")
        if style.get("value_note"):
            lines.append(f"Value note: {style.get('value_note')}")
    else:
        lines.append("value_mode: UNKNOWN — ask before optimizing for cheap vs premium.")
    return "\n".join(lines)


async def tool_prefs(
    action: str = "status",
    *,
    message: str = "",
    mode: str = "",
    context: str = "",
) -> dict[str, Any]:
    action = (action or "status").lower()
    lower = (message or "").lower()
    if action in {"ask", "check"} or "what should i optimize" in lower:
        return resolve_or_ask(context or message)
    if action in {"set", "save"} or any(k in lower for k in ("budget", "balanced", "quality", "cheap", "premium")):
        # extract mode from message
        m = mode
        if not m:
            for key in ("budget", "balanced", "quality", "cheap", "premium", "luxury", "frugal"):
                if key in lower:
                    m = key
                    break
        if not m:
            return ask_value_preference(context or "this task")
        return set_value_mode(m, note=message[:200])
    # status
    mode_now = get_value_mode()
    return {
        "ok": True,
        "value_mode": mode_now,
        "known": mode_now is not None,
        "work_style": get_work_style(),
        "speak": f"Saved preference: {mode_now}" if mode_now else "No spending preference saved — I will ask when it matters.",
        "rule": prefs_prompt_block(),
    }
