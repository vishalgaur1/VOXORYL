"""InferenceBackend interface — Ollama default, Cloud, Deterministic stub, future llama.cpp."""

from __future__ import annotations

from typing import Any, AsyncIterator, Protocol


class InferenceBackend(Protocol):
    name: str

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str: ...

    async def stream(self, messages: list[dict[str, str]], **kwargs: Any) -> AsyncIterator[str]: ...


class DeterministicBackend:
    """Offline / bench stub — never touches network or Ollama."""

    name = "deterministic"

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        last = ""
        for m in messages:
            if m.get("role") == "user":
                last = str(m.get("content") or "")
        lower = last.lower().strip()
        if any(k in lower for k in ("hi", "hello", "hey")):
            return "Hey."
        if "thank" in lower:
            return "Anytime."
        if lower in {"ok", "okay", "k"}:
            return "Okay."
        return "Acknowledged."

    async def stream(self, messages: list[dict[str, str]], **kwargs: Any) -> AsyncIterator[str]:
        text = await self.chat(messages, **kwargs)
        yield text


class OllamaBackend:
    name = "ollama"

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        from voxoryl.llm import chat_local

        return await chat_local(messages, **kwargs)

    async def stream(self, messages: list[dict[str, str]], **kwargs: Any) -> AsyncIterator[str]:
        text = await self.chat(messages, **kwargs)
        yield text


class CloudBackend:
    name = "cloud"

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        from voxoryl.llm import chat_local

        return await chat_local(messages, kind=kwargs.get("kind") or "agent")

    async def stream(self, messages: list[dict[str, str]], **kwargs: Any) -> AsyncIterator[str]:
        text = await self.chat(messages, **kwargs)
        yield text


class LlamaCppBackend:
    """Stub for Phase 2 benchmark — not wired."""

    name = "llamacpp"

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        raise RuntimeError("llamacpp backend not enabled — Phase 2")

    async def stream(self, messages: list[dict[str, str]], **kwargs: Any) -> AsyncIterator[str]:
        raise RuntimeError("llamacpp backend not enabled — Phase 2")
        yield  # pragma: no cover


def get_backend(*, prefer_deterministic: bool = False) -> InferenceBackend:
    if prefer_deterministic:
        return DeterministicBackend()
    try:
        from voxoryl.inference import effective_mode

        if effective_mode() == "cloud":
            return CloudBackend()
    except Exception:
        pass
    return OllamaBackend()
