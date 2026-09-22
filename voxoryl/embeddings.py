from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import httpx

from voxoryl.config import settings


def _cache_path() -> Path:
    return settings.voxoryl_data_dir / "embeddings_cache.json"


def _load_cache() -> dict[str, Any]:
    path = _cache_path()
    if not path.exists():
        return {"model": settings.ollama_embed_model, "items": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"model": settings.ollama_embed_model, "items": {}}


def _save_cache(cache: dict[str, Any]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache), encoding="utf-8")


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def embed_text(text: str) -> list[float] | None:
    """Embed via Ollama (nomic-embed-text). Returns None if model unavailable."""
    text = (text or "").strip()
    if not text:
        return None
    key = text[:2000]
    cache = _load_cache()
    if cache.get("model") != settings.ollama_embed_model:
        cache = {"model": settings.ollama_embed_model, "items": {}}
    if key in cache.get("items", {}):
        return cache["items"][key]
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{settings.ollama_base_url}/api/embeddings",
                json={
                    "model": settings.ollama_embed_model,
                    "prompt": key,
                    "keep_alive": "5m",
                },
            )
            if r.status_code >= 400:
                return None
            vec = r.json().get("embedding")
            if not isinstance(vec, list):
                return None
            cache.setdefault("items", {})[key] = vec
            # keep cache bounded
            if len(cache["items"]) > 400:
                for k in list(cache["items"])[:80]:
                    cache["items"].pop(k, None)
            _save_cache(cache)
            return vec
    except Exception:
        return None


async def rank_by_embedding(query: str, docs: dict[str, str], *, limit: int = 4) -> list[tuple[str, float]]:
    """docs: id -> text. Returns [(id, score), ...]. Caps work so 4B boxes stay snappy."""
    if not docs:
        return []
    # Prefer short docs first; never embed more than 12 sections per ask
    items = sorted(docs.items(), key=lambda kv: len(kv[1]))[:12]
    qvec = await embed_text(query)
    if not qvec:
        return []
    scored: list[tuple[str, float]] = []
    for doc_id, text in items:
        dvec = await embed_text(f"{doc_id}\n{text[:1500]}")
        if not dvec:
            continue
        scored.append((doc_id, cosine(qvec, dvec)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]
