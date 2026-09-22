from __future__ import annotations

"""
Auto media pipeline: write prompt -> generate -> critique -> iterate -> pending final approval.
Backends: Pollinations (free online, fast) + ComfyUI (local) + optional MCP later.
"""

import json
import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from voxoryl.approvals import create_approval
from voxoryl.config import settings
from voxoryl.llm import chat_local, parse_json_loose


ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "setup" / "media.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jobs_dir() -> Path:
    p = settings.voxoryl_data_dir / "media_jobs"
    p.mkdir(parents=True, exist_ok=True)
    (p / "images").mkdir(parents=True, exist_ok=True)
    (p / "videos").mkdir(parents=True, exist_ok=True)
    return p


def load_media_config() -> dict[str, Any]:
    if CATALOG.exists():
        try:
            return json.loads(CATALOG.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"max_auto_rounds": 3, "backends": {}}


async def _write_prompt(brief: str, kind: str = "image") -> dict[str, Any]:
    raw = await chat_local(
        [
            {
                "role": "system",
                "content": (
                    "You write image/video generation prompts. Return ONLY JSON:\n"
                    '{"positive":"...","negative":"...","style":"...","notes":"..."}\n'
                    "positive: detailed visual prompt, no camera-meta fluff unless useful.\n"
                    "negative: artifacts to avoid. Keep under 400 chars each."
                ),
            },
            {
                "role": "user",
                "content": f"Kind: {kind}\nBrief: {brief}\nWrite the best first-pass prompt.",
            },
        ],
        temperature=0.4,
    )
    parsed = parse_json_loose(raw) or {}
    positive = str(parsed.get("positive") or brief).strip()
    negative = str(parsed.get("negative") or "blurry, low quality, watermark, text, logo").strip()
    return {
        "positive": positive[:900],
        "negative": negative[:400],
        "style": parsed.get("style") or "",
        "notes": parsed.get("notes") or "",
    }


async def _revise_prompt(brief: str, previous: dict[str, Any], critique: str) -> dict[str, Any]:
    raw = await chat_local(
        [
            {
                "role": "system",
                "content": (
                    "Revise an image prompt from critique. Return ONLY JSON:\n"
                    '{"positive":"...","negative":"...","changes":"one line"}'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Brief: {brief}\nPrevious positive: {previous.get('positive')}\n"
                    f"Previous negative: {previous.get('negative')}\nCritique: {critique}"
                ),
            },
        ],
        temperature=0.35,
    )
    parsed = parse_json_loose(raw) or {}
    return {
        "positive": str(parsed.get("positive") or previous.get("positive") or brief)[:900],
        "negative": str(parsed.get("negative") or previous.get("negative") or "")[:400],
        "changes": parsed.get("changes") or "tweaked from critique",
    }


async def generate_pollinations(prompt: str, out_path: Path) -> dict[str, Any]:
    url = f"https://image.pollinations.ai/prompt/{quote(prompt[:800])}?width=768&height=768&nologo=true"
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            r = await client.get(url)
            if r.status_code >= 400 or not r.content:
                return {"ok": False, "backend": "pollinations", "error": r.text[:300] or f"http {r.status_code}"}
            out_path.write_bytes(r.content)
            return {"ok": True, "backend": "pollinations", "path": str(out_path.resolve()), "url": url}
    except Exception as exc:
        return {"ok": False, "backend": "pollinations", "error": str(exc)}


async def comfy_available() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{settings.comfyui_url}/system_stats")
            return r.status_code == 200
    except Exception:
        return False


async def generate_comfy(positive: str, negative: str, out_path: Path) -> dict[str, Any]:
    if not await comfy_available():
        return {"ok": False, "backend": "comfyui", "error": "ComfyUI not reachable"}

    wf_path = ROOT / "setup" / "comfy_txt2img_api.json"
    if not wf_path.exists():
        return {"ok": False, "backend": "comfyui", "error": "missing setup/comfy_txt2img_api.json"}

    workflow = json.loads(wf_path.read_text(encoding="utf-8"))
    # Inject prompts into CLIP nodes
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") == "CLIPTextEncode":
            text = str((node.get("inputs") or {}).get("text") or "")
            if "PROMPT_POSITIVE" in text or text == "PROMPT_POSITIVE":
                node["inputs"]["text"] = positive
            elif "PROMPT_NEGATIVE" in text or text == "PROMPT_NEGATIVE":
                node["inputs"]["text"] = negative

    client_id = uuid.uuid4().hex
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(
                f"{settings.comfyui_url}/prompt",
                json={"prompt": workflow, "client_id": client_id},
            )
            if r.status_code >= 400:
                return {"ok": False, "backend": "comfyui", "error": r.text[:400]}
            prompt_id = r.json().get("prompt_id")
            # Poll history briefly
            import asyncio

            image_meta = None
            for _ in range(40):
                await asyncio.sleep(1.5)
                h = await client.get(f"{settings.comfyui_url}/history/{prompt_id}")
                if h.status_code >= 400:
                    continue
                hist = h.json().get(prompt_id) or {}
                outputs = hist.get("outputs") or {}
                for node_out in outputs.values():
                    images = node_out.get("images") or []
                    if images:
                        image_meta = images[0]
                        break
                if image_meta:
                    break
            if not image_meta:
                return {
                    "ok": False,
                    "backend": "comfyui",
                    "error": "timed out waiting for ComfyUI output (check checkpoint name in workflow)",
                    "prompt_id": prompt_id,
                }
            view = await client.get(
                f"{settings.comfyui_url}/view",
                params={
                    "filename": image_meta.get("filename"),
                    "subfolder": image_meta.get("subfolder") or "",
                    "type": image_meta.get("type") or "output",
                },
            )
            if view.status_code >= 400:
                return {"ok": False, "backend": "comfyui", "error": "could not download image"}
            out_path.write_bytes(view.content)
            return {
                "ok": True,
                "backend": "comfyui",
                "path": str(out_path.resolve()),
                "prompt_id": prompt_id,
            }
    except Exception as exc:
        return {"ok": False, "backend": "comfyui", "error": str(exc)}


async def generate_image(positive: str, negative: str, prefer: str = "auto") -> dict[str, Any]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = _jobs_dir() / "images" / f"gen_{stamp}.png"
    prefer = (prefer or "auto").lower()

    if prefer in {"auto", "comfyui", "comfy"} and await comfy_available():
        result = await generate_comfy(positive, negative, out)
        if result.get("ok"):
            return result
        # fall through to pollinations
    if prefer in {"auto", "pollinations", "online", "free"}:
        return await generate_pollinations(f"{positive}. Avoid: {negative}", out)
    if prefer in {"comfyui", "comfy"}:
        return await generate_comfy(positive, negative, out)
    return await generate_pollinations(f"{positive}. Avoid: {negative}", out)


async def critique_image(path: str, brief: str, prompt: str) -> dict[str, Any]:
    """Vision critique if computer vision model available; else text-only pass."""
    from voxoryl.screen import vision_describe

    try:
        vision = await vision_describe(
            path,
            (
                f"Owner brief: {brief}\nPrompt used: {prompt}\n"
                "Critique this image for the brief. Return plain text: "
                "score 0-10, what's good, what to change in the next prompt."
            ),
        )
        desc = str(vision.get("description") or "")
    except Exception as exc:
        desc = f"(vision unavailable: {exc})"

    raw = await chat_local(
        [
            {
                "role": "system",
                "content": (
                    "Score media against a brief. Return ONLY JSON:\n"
                    '{"score":0-10,"keep":true/false,"critique":"short","prompt_fixes":"short"}'
                ),
            },
            {
                "role": "user",
                "content": f"Brief: {brief}\nVision notes:\n{desc[:2000]}",
            },
        ],
        temperature=0.2,
    )
    parsed = parse_json_loose(raw) or {}
    score = float(parsed.get("score") or 5)
    return {
        "ok": True,
        "score": score,
        "keep": bool(parsed.get("keep", score >= 7)),
        "critique": str(parsed.get("critique") or desc)[:500],
        "prompt_fixes": str(parsed.get("prompt_fixes") or "")[:400],
        "vision": desc[:800],
    }


async def run_auto_media(
    brief: str,
    *,
    kind: str = "image",
    prefer: str = "auto",
    max_rounds: int | None = None,
) -> dict[str, Any]:
    """
    Fully automatic until final human approval:
    write prompt -> generate -> critique -> revise (N times) -> queue approval.
    """
    cfg = load_media_config()
    rounds = max_rounds or int(cfg.get("max_auto_rounds") or 3)
    job_id = uuid.uuid4().hex[:10]
    history: list[dict[str, Any]] = []

    prompt = await _write_prompt(brief, kind=kind)
    best: dict[str, Any] | None = None

    for i in range(max(1, rounds)):
        gen = await generate_image(prompt["positive"], prompt.get("negative") or "", prefer=prefer)
        if not gen.get("ok"):
            history.append({"round": i + 1, "error": gen.get("error"), "backend": gen.get("backend")})
            # try alternate backend once
            if prefer == "auto" and i == 0:
                gen = await generate_pollinations(
                    f"{prompt['positive']}. Avoid: {prompt.get('negative')}",
                    _jobs_dir() / "images" / f"gen_{job_id}_{i}.png",
                )
            if not gen.get("ok"):
                continue

        crit = await critique_image(str(gen["path"]), brief, prompt["positive"])
        entry = {
            "round": i + 1,
            "prompt": deepcopy(prompt),
            "generation": {k: gen.get(k) for k in ("ok", "backend", "path")},
            "critique": crit,
        }
        history.append(entry)
        if best is None or float(crit.get("score") or 0) >= float((best.get("critique") or {}).get("score") or 0):
            best = entry
        if crit.get("keep") or float(crit.get("score") or 0) >= 7.5:
            break
        fixes = crit.get("prompt_fixes") or crit.get("critique") or "improve composition and clarity"
        prompt = await _revise_prompt(brief, prompt, str(fixes))

    if not best or not (best.get("generation") or {}).get("path"):
        return {
            "ok": False,
            "error": "generation failed all rounds",
            "history": history,
            "speak": "Media gen failed — is ComfyUI up, or allow Pollinations online?",
            "hint": "Start ComfyUI or check network for pollinations.ai",
        }

    job = {
        "id": job_id,
        "kind": kind,
        "brief": brief,
        "status": "pending_final_approval",
        "best": best,
        "history": history,
        "created_at": _now(),
    }
    job_path = _jobs_dir() / f"job_{job_id}.json"
    job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")

    approval = create_approval(
        "media",
        title=f"Media: {brief[:80]}",
        summary=f"Best score {(best.get('critique') or {}).get('score')} via {(best.get('generation') or {}).get('backend')}",
        payload={"job_id": job_id, "job_path": str(job_path.resolve())},
        preview_path=str((best.get("generation") or {}).get("path") or ""),
    )

    # Video kind: also write a shot script beside the keyframe
    video_meta = None
    if kind == "video":
        from voxoryl.tools import tool_video

        video_meta = await tool_video(brief, mode="script")

    score = (best.get("critique") or {}).get("score")
    return {
        "ok": True,
        "job_id": job_id,
        "job_path": str(job_path.resolve()),
        "preview": (best.get("generation") or {}).get("path"),
        "backend": (best.get("generation") or {}).get("backend"),
        "score": score,
        "rounds": len(history),
        "approval_id": approval["id"],
        "approval_status": "pending",
        "video_script": video_meta,
        "speak": (
            f"Media ready (score {score}, {(best.get('generation') or {}).get('backend')}). "
            f"Pending your final OK — say: approve {approval['id']}  or  reject {approval['id']}."
        ),
        "next": f"approve {approval['id']} | reject {approval['id']} | revise media {job_id} with notes",
    }


async def revise_job(job_id: str, notes: str) -> dict[str, Any]:
    path = _jobs_dir() / f"job_{job_id}.json"
    if not path.exists():
        # fuzzy find
        matches = list(_jobs_dir().glob(f"job_{job_id}*.json"))
        if not matches:
            return {"ok": False, "error": "job not found"}
        path = matches[0]
    job = json.loads(path.read_text(encoding="utf-8"))
    brief = f"{job.get('brief')}\nRevision notes: {notes}"
    return await run_auto_media(brief, kind=str(job.get("kind") or "image"))


async def tool_media(
    action: str = "auto",
    *,
    brief: str = "",
    message: str = "",
    kind: str = "image",
    prefer: str = "auto",
    job_id: str = "",
    notes: str = "",
) -> dict[str, Any]:
    action = (action or "auto").lower()
    text = brief or message
    if action in {"auto", "generate", "image", "make"}:
        k = "video" if kind == "video" or "video" in (message or "").lower() else "image"
        if not text.strip():
            return {"ok": False, "error": "need a brief", "speak": "Tell me what to generate."}
        return await run_auto_media(text, kind=k, prefer=prefer)
    if action in {"revise", "iterate"}:
        m = re.search(r"\b([a-f0-9]{8,10})\b", job_id or message)
        jid = job_id or (m.group(1) if m else "")
        if not jid:
            return {"ok": False, "error": "need job id"}
        return await revise_job(jid, notes or message)
    if action == "status":
        jobs = sorted(_jobs_dir().glob("job_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]
        return {
            "ok": True,
            "jobs": [json.loads(p.read_text(encoding="utf-8")) for p in jobs],
            "comfyui": await comfy_available(),
        }
    return await run_auto_media(text or "abstract tech wallpaper", kind=kind, prefer=prefer)
