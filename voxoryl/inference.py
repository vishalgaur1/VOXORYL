from __future__ import annotations

"""
Local vs Cloud inference for Voxoryl.

Local  — Ollama on this PC (fast tiny model + main 4B).
Cloud  — free-tier OpenAI-compatible APIs (Groq primary, Gemini secondary).
Tools / computer-use / pipelines always run on this machine either way.
"""

import json
from pathlib import Path
from typing import Any, AsyncIterator, Literal

import httpx

from voxoryl.config import settings

InferenceMode = Literal["local", "cloud"]
CloudProvider = Literal["groq", "gemini", "openrouter", "nvidia"]

PROVIDERS: dict[str, dict[str, str]] = {
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "signup": "https://console.groq.com/keys",
        "note": "Free tier, no card — fastest streaming. ~30 RPM; big models often ~1k RPD.",
    },
    "gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-2.5-flash",
        "signup": "https://aistudio.google.com/apikey",
        "note": "Free API key from AI Studio — strong + streams via OpenAI-compatible endpoint. Quotas vary; check AI Studio.",
    },
    "openrouter": {
        "label": "OpenRouter (free models)",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openrouter/free",
        "signup": "https://openrouter.ai/keys",
        "note": "Free :free models / openrouter/free router — lower RPD unless you add credits.",
    },
    "nvidia": {
        "label": "NVIDIA NIM",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "default_model": "meta/llama-3.3-70b-instruct",
        "signup": "https://build.nvidia.com/settings/api-keys",
        "note": "OpenAI-compatible hosted NIM. Free trial credits (no card to start) — not unlimited forever.",
    },
}


def _prefs_path() -> Path:
    # Shared with widget /api/inference (historical filename)
    return settings.voxoryl_data_dir / "inference_prefs.json"


def load_prefs() -> dict[str, Any]:
    path = _prefs_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_prefs(data: dict[str, Any]) -> dict[str, Any]:
    path = _prefs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = {**load_prefs(), **data}
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def env_mode() -> InferenceMode:
    raw = (settings.voxoryl_inference_mode or "local").strip().lower()
    return "cloud" if raw == "cloud" else "local"


def effective_mode() -> InferenceMode:
    prefs = load_prefs()
    if prefs.get("mode") in {"local", "cloud"}:
        return prefs["mode"]  # type: ignore[return-value]
    return env_mode()


def set_mode(mode: str) -> dict[str, Any]:
    m: InferenceMode = "cloud" if str(mode).strip().lower() == "cloud" else "local"
    save_prefs({"mode": m})
    return status()


def effective_provider() -> CloudProvider:
    prefs = load_prefs()
    raw = str(prefs.get("provider") or settings.voxoryl_cloud_provider or "groq").strip().lower()
    if raw in PROVIDERS:
        return raw  # type: ignore[return-value]
    return "groq"


def set_provider(provider: str) -> dict[str, Any]:
    p = str(provider).strip().lower()
    if p not in PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}'. Use: {', '.join(PROVIDERS)}")
    save_prefs({"provider": p})
    return status()


def _api_key_for(provider: CloudProvider) -> str:
    prefs = load_prefs()
    # Prefs never store secrets — only env / settings
    dedicated = (settings.voxoryl_cloud_api_key or "").strip()
    if dedicated:
        return dedicated
    if provider == "groq":
        return (settings.groq_api_key or "").strip()
    if provider == "gemini":
        return (
            (settings.gemini_api_key or "").strip()
            or (settings.google_api_key or "").strip()
        )
    if provider == "openrouter":
        return (settings.openrouter_api_key or "").strip()
    if provider == "nvidia":
        return (settings.nvidia_api_key or "").strip()
    return ""


def cloud_model(provider: CloudProvider | None = None) -> str:
    prefs = load_prefs()
    p = provider or effective_provider()
    override = str(prefs.get("model") or settings.voxoryl_cloud_model or "").strip()
    if override:
        return override
    if p == "groq" and (settings.groq_model or "").strip():
        return settings.groq_model.strip()
    return PROVIDERS[p]["default_model"]


def cloud_ready(provider: CloudProvider | None = None) -> dict[str, Any]:
    p = provider or effective_provider()
    key = _api_key_for(p)
    meta = PROVIDERS[p]
    if key:
        return {
            "ok": True,
            "provider": p,
            "model": cloud_model(p),
            "signup": meta["signup"],
            "hint": "",
        }
    env_hint = {
        "groq": "Set VOXORYL_CLOUD_API_KEY or GROQ_API_KEY (free: https://console.groq.com/keys)",
        "gemini": "Set VOXORYL_CLOUD_API_KEY or GEMINI_API_KEY (free: https://aistudio.google.com/apikey)",
        "openrouter": "Set VOXORYL_CLOUD_API_KEY or OPENROUTER_API_KEY (https://openrouter.ai/keys)",
        "nvidia": "Set VOXORYL_CLOUD_API_KEY or NVIDIA_API_KEY (free trial: https://build.nvidia.com/settings/api-keys)",
    }.get(p, "Set VOXORYL_CLOUD_API_KEY")
    return {
        "ok": False,
        "provider": p,
        "model": cloud_model(p),
        "signup": meta["signup"],
        "hint": (
            f"Cloud mode needs a free API key for {meta['label']}. {env_hint}. "
            "Tools still run on this PC; only the LLM calls go to the cloud."
        ),
    }


def cloud_configured() -> bool:
    """Any supported free cloud key present."""
    return bool(
        _api_key_for("groq")
        or _api_key_for("gemini")
        or _api_key_for("openrouter")
        or _api_key_for("nvidia")
        or (settings.voxoryl_cloud_api_key or "").strip()
    )


def status() -> dict[str, Any]:
    mode = effective_mode()
    provider = effective_provider()
    ready = cloud_ready(provider)
    configured = cloud_configured()
    return {
        "ok": True,
        "mode": mode,
        "inference_mode": mode,
        "provider": provider,
        "cloud_provider": provider,
        "providers": {
            k: {
                "label": v["label"],
                "default_model": v["default_model"],
                "signup": v["signup"],
                "note": v["note"],
                "configured": bool(_api_key_for(k)),  # type: ignore[arg-type]
            }
            for k, v in PROVIDERS.items()
        },
        "cloud_model": cloud_model(provider),
        "cloud_configured": configured,
        "cloud_ready": bool(ready["ok"]) if mode == "cloud" else configured,
        "ready": mode == "local" or bool(ready["ok"]),
        "cloud_hint": ready.get("hint") or "",
        "hint": (
            str(ready.get("hint") or "")
            if mode == "cloud" and not ready["ok"]
            else (
                f"Cloud brain via {PROVIDERS[provider]['label']} ({cloud_model(provider)}). "
                "Tools still run on this PC."
                if mode == "cloud"
                else f"Local Ollama — fast {settings.fast_model} for greetings, main {settings.main_model} for real work."
            )
        ),
        "signup": ready.get("signup") or PROVIDERS[provider]["signup"],
        "local": {
            "fast_model": settings.fast_model,
            "main_model": settings.main_model,
            "note": "Local mode: greetings → fast tiny model; planning/tools → main 4B. Pipelines skip LLM when possible.",
        },
        "tools_note": "Screen control, PowerToys, Chrome, and other tools always run locally — cloud only replaces the chat/planning LLM.",
        "speak": (
            f"Inference: {mode}."
            + (
                f" Cloud via {PROVIDERS[provider]['label']} ({cloud_model(provider)})."
                if mode == "cloud" and ready["ok"]
                else (
                    f" {ready['hint']}"
                    if mode == "cloud"
                    else f" Local {settings.fast_model} + {settings.main_model}."
                )
            )
        ),
    }


def missing_cloud_speak() -> str:
    ready = cloud_ready()
    return str(
        ready.get("hint")
        or "Cloud mode is on but no API key is set. Add a free Groq key at console.groq.com/keys into GROQ_API_KEY or VOXORYL_CLOUD_API_KEY in .env, then restart Voxoryl."
    )


async def chat_cloud(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.7,
    max_tokens: int = 512,
) -> str:
    ready = cloud_ready()
    if not ready["ok"]:
        raise RuntimeError(missing_cloud_speak())

    provider = effective_provider()
    meta = PROVIDERS[provider]
    model = cloud_model(provider)
    key = _api_key_for(provider)
    url = f"{meta['base_url'].rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://localhost/voxoryl"
        headers["X-Title"] = "VOXORYL"

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=90.0) as client:
        r = await client.post(url, headers=headers, json=payload)
        if r.status_code >= 400:
            raise RuntimeError(f"Cloud {provider} error ({r.status_code}): {r.text[:400]}")
        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        return str(msg.get("content") or "").strip()


async def stream_cloud(
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.7,
    max_tokens: int = 512,
) -> AsyncIterator[str]:
    """Yield text deltas from the cloud provider (SSE / OpenAI stream)."""
    ready = cloud_ready()
    if not ready["ok"]:
        raise RuntimeError(missing_cloud_speak())

    provider = effective_provider()
    meta = PROVIDERS[provider]
    model = cloud_model(provider)
    key = _api_key_for(provider)
    url = f"{meta['base_url'].rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://localhost/voxoryl"
        headers["X-Title"] = "VOXORYL"

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    async with httpx.AsyncClient(timeout=90.0) as client:
        async with client.stream("POST", url, headers=headers, json=payload) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", errors="replace")[:400]
                raise RuntimeError(f"Cloud {provider} error ({resp.status_code}): {body}")
            async for line in resp.aiter_lines():
                if not line:
                    continue
                if line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    yield str(piece)


async def stream_ollama(
    messages: list[dict[str, str]],
    *,
    model: str,
    temperature: float = 0.7,
    num_predict: int = 220,
    num_ctx: int = 4096,
) -> AsyncIterator[str]:
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "think": False,
        "keep_alive": "30m",
        "options": {
            "temperature": temperature,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        },
    }
    async with httpx.AsyncClient(timeout=180.0) as client:
        async with client.stream(
            "POST", f"{settings.ollama_base_url}/api/chat", json=payload
        ) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", errors="replace")[:400]
                raise RuntimeError(f"Ollama stream error ({resp.status_code}): {body}")
            async for line in resp.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = data.get("message") or {}
                piece = msg.get("content")
                if piece:
                    yield str(piece)
                if data.get("done"):
                    break
