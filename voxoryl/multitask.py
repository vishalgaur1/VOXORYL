from __future__ import annotations

"""
Multitask / concurrent jobs — run independent tool intents in parallel (asyncio.gather).
Example: flash-fill a form WHILE identifying a song.
"""

import asyncio
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from voxoryl.memory import memory


JobFn = Callable[[], Awaitable[dict[str, Any]]]

_JOBS: dict[str, dict[str, Any]] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_jobs() -> dict[str, Any]:
    jobs = sorted(_JOBS.values(), key=lambda j: str(j.get("created_at") or ""), reverse=True)
    open_n = sum(1 for j in jobs if j.get("status") in {"running", "queued"})
    speak = f"{open_n} active job(s); {len(jobs)} tracked."
    return {"ok": True, "jobs": jobs[:30], "speak": speak}


def get_job(job_id: str) -> dict[str, Any]:
    j = _JOBS.get(job_id)
    if not j:
        return {"ok": False, "error": "unknown job", "speak": f"No job {job_id}."}
    return {"ok": True, "job": j, "speak": f"Job {job_id}: {j.get('status')} — {j.get('label') or ''}"}


def _split_intents(message: str) -> list[str]:
    """Split on while / at the same time / and also — keep meaningful chunks."""
    raw = (message or "").strip()
    if not raw:
        return []
    lower = raw.lower()
    # primary splitters
    parts: list[str] = []
    for sep in (
        r"\bwhile\b",
        r"\bat the same time\b",
        r"\band simultaneously\b",
        r"\band also\b",
        r"\bmultitask:\b",
        r"\b(?:;)\s*",
    ):
        if re.search(sep, lower):
            chunks = re.split(sep, raw, flags=re.I)
            parts = [c.strip(" ,.") for c in chunks if c and c.strip(" ,.")]
            break
    if not parts:
        # "do X and Y" only when both look like tool intents
        if " and " in lower:
            a, b = re.split(r"\band\b", raw, maxsplit=1, flags=re.I)
            if _looks_actionable(a) and _looks_actionable(b):
                parts = [a.strip(), b.strip()]
    return parts if len(parts) >= 2 else ([raw] if raw else [])


def _looks_actionable(text: str) -> bool:
    lower = (text or "").lower()
    keys = (
        "fill",
        "form",
        "flash",
        "screen",
        "song",
        "melody",
        "hum",
        "identify",
        "research",
        "look up",
        "camera",
        "brightness",
        "volume",
        "playlist",
        "book",
        "proxy",
        "todo",
        "email",
        "github",
    )
    return any(k in lower for k in keys)


def _infer_tool(chunk: str) -> dict[str, Any]:
    """Map a chunk to a tool name + args for concurrent dispatch."""
    lower = (chunk or "").lower()
    if any(k in lower for k in ("flash fill", "fill this form", "fill the form", "autofill", "fill form")):
        return {"name": "screen", "args": {"action": "flash_fill", "goal": chunk}, "label": "flash_fill"}
    if any(k in lower for k in ("what song", "identify song", "identify this song", "shazam", "find song", "melody", "hum")):
        return {"name": "melody", "args": {"action": "find_song", "message": chunk}, "label": "identify_song"}
    if any(k in lower for k in ("look at my screen", "see my screen", "what's on my screen")):
        return {"name": "screen", "args": {"action": "look", "goal": chunk}, "label": "screen_look"}
    if any(k in lower for k in ("camera", "what am i showing", "look at this")):
        return {"name": "camera", "args": {"action": "see", "message": chunk}, "label": "camera"}
    if any(k in lower for k in ("suggest songs", "my playlist", "music taste")):
        return {"name": "music", "args": {"message": chunk}, "label": "music"}
    if any(k in lower for k in ("volume", "mute", "brightness")):
        return {"name": "windows", "args": {"message": chunk}, "label": "windows"}
    if any(k in lower for k in ("research", "look up", "search for")):
        return {"name": "research", "args": {"query": chunk}, "label": "research"}
    if any(k in lower for k in ("surprise me", "interesting website", "teach me")):
        return {"name": "curiosity", "args": {"message": chunk}, "label": "curiosity"}
    return {"name": "notes", "args": {"note": chunk, "title": "Multitask fragment"}, "label": "notes_fallback"}


async def _dispatch_one(spec: dict[str, Any], message: str) -> dict[str, Any]:
    """Lazy import agent dispatch to avoid circular imports at module load."""
    from voxoryl.agent import _dispatch

    name = str(spec.get("name") or "")
    args = dict(spec.get("args") or {})
    try:
        result = await _dispatch(name, args, message)
        return {"ok": bool(result.get("ok", True)), "tool": name, "label": spec.get("label"), "result": result}
    except Exception as exc:
        return {"ok": False, "tool": name, "label": spec.get("label"), "error": str(exc)}


async def run_concurrent(message: str) -> dict[str, Any]:
    parts = _split_intents(message)
    if len(parts) < 2:
        return {
            "ok": False,
            "hint": "Use while / at the same time between two clear tasks.",
            "speak": "Give me two tasks — e.g. flash fill this form while identifying this song.",
        }
    specs = [_infer_tool(p) for p in parts[:4]]
    batch_id = str(uuid.uuid4())[:8]
    job_meta = {
        "id": batch_id,
        "created_at": _now(),
        "status": "running",
        "label": " + ".join(str(s.get("label")) for s in specs),
        "parts": parts,
        "specs": specs,
    }
    _JOBS[batch_id] = job_meta

    async def _one(spec: dict[str, Any], chunk: str) -> dict[str, Any]:
        return await _dispatch_one(spec, chunk)

    results = await asyncio.gather(*[_one(s, p) for s, p in zip(specs, parts)], return_exceptions=True)
    cleaned: list[dict[str, Any]] = []
    for r in results:
        if isinstance(r, Exception):
            cleaned.append({"ok": False, "error": str(r)})
        else:
            cleaned.append(r)
    job_meta["status"] = "done"
    job_meta["finished_at"] = _now()
    job_meta["results"] = cleaned
    _JOBS[batch_id] = job_meta

    speaks = []
    for c in cleaned:
        res = c.get("result") if isinstance(c, dict) else None
        if isinstance(res, dict) and res.get("speak"):
            speaks.append(f"{c.get('label')}: {res.get('speak')}")
        elif isinstance(c, dict) and c.get("error"):
            speaks.append(f"{c.get('label')}: {c.get('error')}")
    speak = "Multitask done. " + " | ".join(speaks[:4]) if speaks else "Multitask finished."
    memory.remember_fact(f"multitask {batch_id}: {job_meta['label']}", tags=["multitask"])
    return {
        "ok": all(bool(c.get("ok", True)) for c in cleaned),
        "job_id": batch_id,
        "parts": parts,
        "results": cleaned,
        "speak": speak[:900],
    }


def wants_multitask(message: str) -> bool:
    lower = (message or "").lower()
    if "multitask" in lower:
        return True
    if any(k in lower for k in (" at the same time", " simultaneously")):
        return True
    if " while " in lower and _looks_actionable(lower.split(" while ", 1)[0]) and _looks_actionable(
        lower.split(" while ", 1)[-1]
    ):
        return True
    return False


async def tool_multitask(action: str = "run", *, message: str = "", job_id: str = "") -> dict[str, Any]:
    action = (action or "run").lower().strip()
    if action in {"status", "list"}:
        if job_id:
            return get_job(job_id)
        return list_jobs()
    if action in {"get"} and job_id:
        return get_job(job_id)
    return await run_concurrent(message)
