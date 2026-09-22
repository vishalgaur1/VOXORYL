from __future__ import annotations

"""
Neuro-adapt / accessibility layer.
Ask — never assume ADHD or any condition.
Store needs in knowledge + local profile; reshape every reply forever.
Pull research daily to improve coaching style (not medical treatment).
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voxoryl.config import settings
from voxoryl.knowledge import knowledge
from voxoryl.llm import chat_local, parse_json_loose
from voxoryl.memory import memory


ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = ROOT / "setup" / "neuro_baseline.json"

KNOWN_KEYS = ("adhd", "anxiety", "depression", "short_focus", "none")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _adapt_path() -> Path:
    p = settings.voxoryl_data_dir / "neuro_adapt.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_baseline() -> dict[str, Any]:
    if BASELINE_PATH.exists():
        try:
            return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"profiles": {}, "universal_hard_rules": []}


def load_adapt() -> dict[str, Any]:
    path = _adapt_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {
        "enabled": False,
        "profiles": [],  # e.g. ["adhd", "short_focus"]
        "user_notes": "",
        "learned_rules": [],
        "research_log": [],
        "updated_at": None,
    }


def save_adapt(data: dict[str, Any]) -> None:
    data["updated_at"] = _now()
    _adapt_path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def get_profiles() -> list[str]:
    data = load_adapt()
    if not data.get("enabled"):
        # also check work_style
        style = dict(((memory.read().get("profile") or {}).get("work_style") or {}))
        raw = str(style.get("neuro_profiles") or "").strip().lower()
        if raw and raw != "none":
            return [p.strip() for p in re.split(r"[,|/]+", raw) if p.strip() in KNOWN_KEYS and p.strip() != "none"]
        return []
    return [p for p in (data.get("profiles") or []) if p in KNOWN_KEYS and p != "none"]


def is_active() -> bool:
    return bool(get_profiles()) or bool(load_adapt().get("enabled"))


def ask_neuro_needs() -> dict[str, Any]:
    q = (
        "To help you finish stuff: any of these fit? "
        "adhd / anxiety / low-energy / short-focus / none. "
        "I won't assume. Reply with one or more."
    )
    return {
        "ok": True,
        "needs_preference": True,
        "question": q,
        "speak": q,
        "options": ["adhd", "anxiety", "depression", "short_focus", "none"],
        "rule": "Do not invent a diagnosis. Wait for answer.",
    }


def set_profiles(raw: str | list[str], *, note: str = "") -> dict[str, Any]:
    if isinstance(raw, list):
        tokens = [str(x).lower().strip() for x in raw]
    else:
        lower = (raw or "").lower()
        tokens = []
        aliases = {
            "adhd": ("adhd", "add", "executive dysfunction", "can't focus", "cant focus", "neurodivergent"),
            "anxiety": ("anxiety", "anxious", "overwhelm", "overwhelmed"),
            "depression": ("depression", "depressed", "low energy", "low-energy", "no energy"),
            "short_focus": ("short-focus", "short focus", "can't read", "cant read", "walls of text", "paragraphs hard"),
            "none": ("none", "neurotypical", "no thanks", "don't need"),
        }
        for key, keys in aliases.items():
            if any(k in lower for k in keys):
                tokens.append(key)
        if not tokens:
            for k in KNOWN_KEYS:
                if re.search(rf"\b{k}\b", lower):
                    tokens.append(k)

    if "none" in tokens and len(tokens) == 1:
        data = load_adapt()
        data["enabled"] = False
        data["profiles"] = []
        data["user_notes"] = note[:240] or data.get("user_notes") or ""
        save_adapt(data)
        style = dict(((memory.read().get("profile") or {}).get("work_style") or {}))
        style["neuro_profiles"] = "none"
        style["answers"] = style.get("answers") or "short blunt caveman - no fluff"
        memory.update_profile(work_style=style, preferences=["Neuro-adapt: off (user said none)"])
        knowledge.append_facts("Accessibility", ["Owner declined neuro-adapt profiles."])
        return {"ok": True, "enabled": False, "profiles": [], "speak": "Got it — normal short mode. Say 'adhd mode' anytime."}

    profiles = [t for t in tokens if t in KNOWN_KEYS and t != "none"]
    if not profiles:
        return ask_neuro_needs()

    data = load_adapt()
    data["enabled"] = True
    data["profiles"] = profiles
    if note:
        data["user_notes"] = note[:240]
    # seed learned_rules from baseline if empty
    if not data.get("learned_rules"):
        data["learned_rules"] = _baseline_rules(profiles)[:12]
    save_adapt(data)

    style = dict(((memory.read().get("profile") or {}).get("work_style") or {}))
    style["neuro_profiles"] = ",".join(profiles)
    style["answers"] = "neuro-adapt: max 3 lines, one next action, always deadline"
    memory.update_profile(
        work_style=style,
        preferences=[f"Neuro-adapt on: {', '.join(profiles)}. Short replies forever."],
    )
    knowledge.append_facts(
        "Accessibility",
        [
            f"Owner asked for execution support profiles: {', '.join(profiles)}.",
            "Reply forever: max 3 short lines, one next action, deadlines required, no walls of text.",
            "Not medical care — task support only.",
        ],
    )
    labels = ", ".join(profiles)
    return {
        "ok": True,
        "enabled": True,
        "profiles": profiles,
        "speak": f"Locked in: {labels}. I'll keep replies tiny + one next step forever. Research upgrades daily.",
    }


def _baseline_rules(profiles: list[str]) -> list[str]:
    base = load_baseline()
    rules: list[str] = []
    for p in profiles:
        meta = (base.get("profiles") or {}).get(p) or {}
        for r in (meta.get("reply_rules") or []) + (meta.get("task_rules") or []):
            if r not in rules:
                rules.append(r)
    for r in base.get("universal_hard_rules") or []:
        if r not in rules:
            rules.append(r)
    return rules


def active_rules() -> list[str]:
    profiles = get_profiles()
    if not profiles:
        return []
    data = load_adapt()
    learned = [str(x) for x in (data.get("learned_rules") or []) if str(x).strip()]
    base = _baseline_rules(profiles)
    # learned first (fresher research), then baseline fill
    out: list[str] = []
    for r in learned + base:
        if r not in out:
            out.append(r)
    return out[:20]


def neuro_prompt_block() -> str:
    """Inject into every speak_system call."""
    lines = [
        "NEURO / ACCESSIBILITY (hard): Never assume ADHD or any diagnosis.",
        "If the owner struggles with long text or finishing tasks and profile unknown — ASK once (adhd/anxiety/low-energy/short-focus/none).",
        "Never claim to treat illness. Only help get shit done.",
    ]
    profiles = get_profiles()
    if not profiles:
        lines.append("neuro_profiles: UNKNOWN — do not invent; ask if relevant.")
        return "\n".join(lines)

    lines.append(f"neuro_profiles: {', '.join(profiles)} — OBEY for EVERY reply:")
    for r in active_rules()[:10]:
        lines.append(f"- {r}")
    note = (load_adapt().get("user_notes") or "").strip()
    if note:
        lines.append(f"User note: {note}")
    lines.append("FORMAT: max 3 short lines OR max 5 one-line bullets. One next action. No paragraphs.")
    return "\n".join(lines)


def chop_reply(text: str, *, max_lines: int = 5) -> str:
    """Hard post-process when neuro mode on — kill walls of text."""
    if not is_active() or not (text or "").strip():
        return text
    # split paragraphs into lines
    raw = text.replace("\r\n", "\n").strip()
    # if huge paragraph, break on sentences
    if "\n" not in raw and len(raw) > 120:
        parts = re.split(r"(?<=[.!?])\s+", raw)
        raw = "\n".join(p.strip() for p in parts if p.strip())
    lines = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        ln = re.sub(r"^#+\s*", "", ln)
        lines.append(ln)
    # still one fat line → chunk on commas / and
    if len(lines) == 1 and len(lines[0]) > 120:
        chunked = re.split(r"\s*(?:,| and )\s*", lines[0])
        lines = [c.strip() for c in chunked if c.strip()][:max_lines]
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + ["(More on ask — one step first.)"]
    out = []
    for ln in lines:
        if len(ln) > 120:
            ln = ln[:117] + "..."
        out.append(ln)
    return "\n".join(out)


async def improve_from_research() -> dict[str, Any]:
    """
    Pull web research for the owner's profiles; distill new reply/task rules.
    Runs daily. Local knowledge only for the distilled rules.
    """
    profiles = get_profiles()
    if not profiles:
        return {"ok": False, "skipped": True, "reason": "no neuro profiles set"}

    from voxoryl.tools import tool_research

    base = load_baseline()
    queries: list[str] = []
    for p in profiles:
        meta = (base.get("profiles") or {}).get(p) or {}
        queries.extend(list(meta.get("research_queries") or [])[:2])
    if not queries:
        queries = ["ADHD friendly task instructions short steps evidence"]

    findings: list[dict[str, Any]] = []
    blobs: list[str] = []
    for q in queries[:3]:
        r = await tool_research(q)
        findings.append({"query": q, "ok": r.get("ok"), "abstract": (r.get("abstract") or "")[:300], "source": r.get("source")})
        blobs.append(f"Q: {q}\n{(r.get('abstract') or '')}\n" + "\n".join(
            f"- {x.get('title')}: {x.get('snippet') or x.get('url')}" for x in (r.get("related") or [])[:4]
        ))

    distill = await chat_local(
        [
            {
                "role": "system",
                "content": (
                    "You improve an assistant's REPLY STYLE for people who struggle with long text / task completion "
                    f"(profiles: {', '.join(profiles)}). "
                    "NOT medical advice. Distill actionable communication rules only. "
                    'Return JSON: {"rules":["short imperative rule",...],"why":"one line"}. Max 6 rules. '
                    "Each rule must be one line, concrete (e.g. 'Lead with one next action under 10 words')."
                ),
            },
            {"role": "user", "content": "Research notes:\n" + "\n\n".join(blobs)[:7000]},
        ],
        temperature=0.2,
    )
    parsed = parse_json_loose(distill) or {}
    new_rules = [str(x).strip() for x in (parsed.get("rules") or []) if str(x).strip()][:6]
    if not new_rules:
        # fallback: keep baseline emphasis
        new_rules = [
            "Lead with one next action under 10 words.",
            "Max 3 lines. No paragraphs.",
            "Attach a deadline to every task.",
        ]

    data = load_adapt()
    old = list(data.get("learned_rules") or [])
    merged: list[str] = []
    for r in new_rules + old:
        if r not in merged:
            merged.append(r)
    data["learned_rules"] = merged[:24]
    data.setdefault("research_log", []).append(
        {
            "at": _now(),
            "profiles": profiles,
            "queries": queries[:3],
            "new_rules": new_rules,
            "why": str(parsed.get("why") or "")[:200],
            "sources": [f.get("source") for f in findings if f.get("source")],
        }
    )
    data["research_log"] = data["research_log"][-30:]
    save_adapt(data)

    knowledge.append_facts(
        "Accessibility",
        [f"Daily neuro-adapt upgrade: {r}" for r in new_rules[:4]]
        + ([f"Why: {parsed.get('why')}"] if parsed.get("why") else []),
    )
    memory.remember_fact(
        f"Neuro-adapt research refresh: {len(new_rules)} rules for {', '.join(profiles)}",
        tags=["neuro", "research", "accessibility"],
    )

    # also drop a tiny note for the owner
    try:
        from voxoryl.tools import tool_notes

        await tool_notes(
            "Updated how I talk to you:\n" + "\n".join(f"- {r}" for r in new_rules),
            title="Neuro-adapt daily upgrade",
        )
    except Exception:
        pass

    speak = f"Upgraded your special-needs reply rules ({len(new_rules)}). Still max-short + one next step."
    return {
        "ok": True,
        "profiles": profiles,
        "new_rules": new_rules,
        "learned_rules": data["learned_rules"][:12],
        "findings": findings,
        "speak": speak,
    }


def status() -> dict[str, Any]:
    data = load_adapt()
    profiles = get_profiles()
    return {
        "ok": True,
        "enabled": bool(profiles),
        "profiles": profiles,
        "rules": active_rules()[:12],
        "user_notes": data.get("user_notes") or "",
        "last_research": (data.get("research_log") or [])[-1] if data.get("research_log") else None,
        "disclaimer": "Task support only — not diagnosis or therapy.",
        "speak": (
            f"Neuro-adapt ON for: {', '.join(profiles)}. {len(active_rules())} active rules."
            if profiles
            else "Neuro-adapt OFF. Say 'adhd mode' or 'short-focus mode' — I won't assume."
        ),
    }


async def tool_neuro(action: str = "status", message: str = "", profiles: str = "") -> dict[str, Any]:
    act = (action or "status").lower()
    lower = (message or "").lower()

    if act in {"ask"} or "what do i need" in lower:
        return ask_neuro_needs()

    if act in {"set", "enable", "mode"} or any(
        k in lower
        for k in (
            "adhd mode",
            "adhd-friendly",
            "short-focus",
            "short focus mode",
            "anxiety mode",
            "i have adhd",
            "i have anxiety",
            "can't read long",
            "cant read long",
            "neuro adapt",
            "neuro-adapt",
        )
    ):
        return set_profiles(profiles or message, note=message[:200])

    if act in {"disable", "off"} or any(k in lower for k in ("turn off neuro", "disable adhd mode", "neuro off")):
        return set_profiles("none")

    if act in {"improve", "research", "upgrade", "learn"} or "make yourself better" in lower or "upgrade my style" in lower:
        if not get_profiles():
            return ask_neuro_needs()
        return await improve_from_research()

    if act in {"rules", "status", "show"}:
        return status()

    # bare message that looks like a profile answer
    if any(k in lower for k in ("adhd", "anxiety", "depression", "short-focus", "short focus", "none")):
        return set_profiles(message)

    return status()
