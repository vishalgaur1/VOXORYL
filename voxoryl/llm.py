from __future__ import annotations

import json
import os
from typing import Any, Literal

import httpx

from voxoryl.config import settings

ModelTier = Literal["fast", "main"]


class OllamaError(RuntimeError):
    pass


def resolve_chat_model(*, tier: ModelTier = "main", model: str | None = None) -> str:
    """Pick Ollama tag for this call. Explicit model wins; else fast vs main."""
    if model and model.strip():
        return model.strip()
    if tier == "fast":
        return settings.fast_model
    return settings.main_model


async def ollama_available() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{settings.ollama_base_url}/api/tags")
            return r.status_code == 200
    except Exception:
        return False


async def list_local_models() -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.ollama_base_url}/api/tags")
            r.raise_for_status()
            return [m.get("name", "") for m in r.json().get("models", [])]
    except Exception:
        return []


def _model_installed(name: str, installed: list[str]) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    for m in installed:
        if m == n or m.startswith(n) or n.startswith(m.split(":")[0] + ":"):
            if m == n or m.startswith(n + ":") or n in m:
                return True
        if m.split(":")[0] == n.split(":")[0] and (":" not in n or m.startswith(n)):
            return True
    # Exact / prefix match (ollama tags often include :latest alias)
    return any(m == n or m.startswith(n) or n.startswith(m) for m in installed)


async def chat_local(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.7,
    model: str | None = None,
    tier: ModelTier = "main",
    num_predict: int | None = None,
    num_ctx: int | None = None,
    timeout: float | None = None,
    keep_alive: str | None = None,
    kind: str = "local_task",
    force_local: bool = False,
    _fallback_tried: bool = False,
) -> str:
    """Chat via Local Ollama (fast/main) or Cloud free API when inference mode is cloud.

    Only kinds agent/general/router/council_advisor use cloud when mode=cloud.
    Sensitive modules must pass their kind (or leave default) so they stay on Ollama.
    Tools still run on this PC — this only picks where the LLM runs.
    """
    from voxoryl.mock import MOCK_VOICES, USE_MOCK
    from voxoryl.privacy import allow_cloud_for

    chosen = resolve_chat_model(tier=tier, model=model)
    cloud_kinds = {"agent", "general", "router", "council_advisor"}

    # Cloud mode: use Groq/Gemini/OpenRouter for general agent chat (streaming-capable providers)
    if not force_local and not USE_MOCK and kind in cloud_kinds:
        try:
            from voxoryl.inference import chat_cloud, effective_mode, missing_cloud_speak

            if effective_mode() == "cloud" and allow_cloud_for(kind):
                try:
                    max_tok = num_predict if num_predict is not None else (96 if tier == "fast" else 512)
                    return await chat_cloud(messages, temperature=temperature, max_tokens=max_tok)
                except Exception as exc:
                    # Missing key → clear message; other errors fall back to local if Ollama is up
                    msg = str(exc)
                    if "API key" in msg or "Cloud mode needs" in msg:
                        raise OllamaError(missing_cloud_speak()) from exc
                    if not await ollama_available():
                        raise OllamaError(msg) from exc
                    # fall through to local
        except OllamaError:
            raise
        except Exception:
            pass

    if USE_MOCK or not await ollama_available():
        if USE_MOCK or os.getenv("VOXORYL_ALLOW_MOCK", "0") == "1":
            joined = " ".join(m.get("content", "") for m in messages).lower()
            if "json" in joined or "output json only" in joined:
                return (
                    '{"decision":"Capture context, research, then act",'
                    '"rationale":"Mock mode — install Ollama + qwen3.5:4b for real deliberation.",'
                    '"next_actions":["Remember the request","Run research","Save a note"],'
                    '"confidence":0.4,'
                    '"needs_tools":["memory","research","notes"]}'
                )
            if "speaking aloud" in joined or "voxoryl speaking" in joined or "rewrite as voxoryl" in joined:
                user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
                draft_line = ""
                for line in user.splitlines():
                    if line.lower().startswith("draft:"):
                        draft_line = line.split(":", 1)[1].strip()
                        break
                return draft_line or "Very good, Sir. Consider it handled."
            if "intent router" in joined:
                return (
                    '{"mode":"direct","speak":"On it, Sir.","use_council":false,'
                    '"tools":[{"name":"research","args":{"query":"latest updates"}}]}'
                )
            for key, text in MOCK_VOICES.items():
                if key in joined:
                    return text
            user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
            return f"[mock] Understood: {user[:240]}"

    # Fast tier: tiny context + short reply so 0.5B stays snappy
    if tier == "fast":
        pred = num_predict if num_predict is not None else 96
        ctx = num_ctx if num_ctx is not None else 2048
        req_timeout = timeout if timeout is not None else 45.0
        alive = keep_alive if keep_alive is not None else "30m"
    else:
        pred = num_predict if num_predict is not None else 220
        ctx = num_ctx if num_ctx is not None else (4096 if len(messages) < 6 else 8192)
        req_timeout = timeout if timeout is not None else 180.0
        alive = keep_alive if keep_alive is not None else "30m"

    payload = {
        "model": chosen,
        "messages": messages,
        "stream": False,
        "think": False,  # qwen3.5 thinks by default — burns minutes on a 4B laptop
        "keep_alive": alive,
        "options": {
            "temperature": temperature,
            "num_ctx": ctx,
            "num_predict": pred,
        },
    }
    try:
        async with httpx.AsyncClient(timeout=req_timeout) as client:
            r = await client.post(f"{settings.ollama_base_url}/api/chat", json=payload)
            if r.status_code >= 400:
                # Missing fast model → fall back to main once (don't block forever on pull)
                if tier == "fast" and not _fallback_tried and chosen != settings.main_model:
                    return await chat_local(
                        messages,
                        temperature=temperature,
                        tier="main",
                        num_predict=num_predict,
                        num_ctx=num_ctx,
                        timeout=timeout,
                        keep_alive=keep_alive,
                        kind=kind,
                        force_local=True,
                        _fallback_tried=True,
                    )
                raise OllamaError(r.text)
            data = r.json()
            return (data.get("message") or {}).get("content", "").strip()
    except httpx.HTTPError as exc:
        if tier == "fast" and not _fallback_tried and chosen != settings.main_model:
            return await chat_local(
                messages,
                temperature=temperature,
                tier="main",
                num_predict=num_predict,
                num_ctx=num_ctx,
                timeout=timeout,
                keep_alive=keep_alive,
                kind=kind,
                force_local=True,
                _fallback_tried=True,
            )
        raise OllamaError(
            f"Cannot reach Ollama at {settings.ollama_base_url}. "
            f"Install Ollama and run: ollama pull {settings.main_model}"
        ) from exc


async def chat_online(messages: list[dict[str, str]], *, kind: str = "general") -> dict[str, Any]:
    """Optional free online advisor via Groq. Blocked for sensitive personal kinds."""
    from voxoryl.privacy import allow_cloud_for

    if not allow_cloud_for(kind):
        return {
            "used": False,
            "skipped": True,
            "reason": f"Privacy: '{kind}' stays local-only — never sent to cloud.",
            "content": "",
        }
    if not settings.groq_api_key.strip():
        return {
            "used": False,
            "skipped": True,
            "reason": "No GROQ_API_KEY set — council continues with local voices only.",
            "content": "",
        }
    headers = {
        "Authorization": f"Bearer {settings.groq_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.groq_model,
        "messages": messages,
        "temperature": 0.4,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            if r.status_code >= 400:
                return {"used": False, "skipped": True, "reason": r.text, "content": ""}
            content = r.json()["choices"][0]["message"]["content"].strip()
            return {
                "used": True,
                "skipped": False,
                "reason": "",
                "content": content,
                "model": settings.groq_model,
            }
    except Exception as exc:
        return {"used": False, "skipped": True, "reason": str(exc), "content": ""}


def parse_json_loose(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
    return None
