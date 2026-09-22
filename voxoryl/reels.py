from __future__ import annotations

"""
Reels library — share Instagram Reels *with* VOXORYL (local, private).

Collections: paste multi-line URLs, ZIP/folder of videos, or a JSON manifest.
Each item is understood (oEmbed / ASR / summary), then a **gate** decides
promote | quarantine | reject before anything enters the knowledge vault.

ToS: public oEmbed, files you supply, or optional Meta Graph with *your* tokens.
No login scraping, credential stuffing, or private-account bypass.
"""

import io
import json
import re
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from voxoryl.config import settings

TOS_NOTE = (
    "VOXORYL only uses public Instagram oEmbed metadata, files you upload/export, "
    "or Meta Graph with tokens you own. "
    "It does not log into Instagram, scrape private content, or use stolen credentials."
)

SAVED_GRAPH_NOTE = (
    "Meta Graph does not expose a personal Instagram “Saved” collection to third-party apps. "
    "With tokens set, VOXORYL can list recent media from *your* Business/Creator account. "
    "For Saved Reels from others: paste a URL list, ZIP/folder export, or JSON manifest."
)

_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
_INDEX_NAME = "index.json"
_COLLECTIONS_NAME = "collections.json"

GATE_SYSTEM = """You gate Instagram Reel content for the owner's private knowledge base.
Return ONLY JSON:
{
  "usefulness": 0.0,
  "usefulness_reason": "why actionable or not for this user",
  "credibility": 0.0,
  "credibility_notes": "claims vs speculation; red flags; unverified marks",
  "claims_vs_speculation": "claims|mixed|speculation",
  "red_flags": ["short flag"],
  "outcome": "promote|quarantine|reject",
  "rationale": "one short sentence"
}
Rules:
- usefulness 0-1: is this actionable/reusable for the owner (tips, tools, workflows)?
- credibility 0-1: evidence vs hype; mark shaky health/finance/legal claims low.
- promote: useful AND credible enough to store as knowledge (not as proven fact if soft).
- quarantine: interesting but unverified, speculative, incomplete, or needs human review.
- reject: empty, spam, noise, harmful, or clearly useless.
- Prefer quarantine over promote when unsure. Never invent facts.
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def reels_dir() -> Path:
    path = settings.voxoryl_data_dir / "reels"
    path.mkdir(parents=True, exist_ok=True)
    (path / "media").mkdir(parents=True, exist_ok=True)
    return path


def media_dir() -> Path:
    return reels_dir() / "media"


def _index_path() -> Path:
    return reels_dir() / _INDEX_NAME


def _collections_path() -> Path:
    return reels_dir() / _COLLECTIONS_NAME


def _load_index() -> dict[str, Any]:
    path = _index_path()
    if not path.exists():
        return {"version": 1, "items": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": 1, "items": []}
        data.setdefault("version", 1)
        data.setdefault("items", [])
        return data
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "items": []}


def _save_index(data: dict[str, Any]) -> None:
    path = _index_path()
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_collections() -> dict[str, Any]:
    path = _collections_path()
    if not path.exists():
        return {"version": 1, "collections": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"version": 1, "collections": []}
        data.setdefault("version", 1)
        data.setdefault("collections", [])
        return data
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "collections": []}


def _save_collections(data: dict[str, Any]) -> None:
    path = _collections_path()
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def extract_reel_urls(text: str) -> list[str]:
    urls = re.findall(r"https?://[^\s<>\"']+", text or "")
    out: list[str] = []
    seen: set[str] = set()
    for u in urls:
        u = u.rstrip(").,;]'\"")
        low = u.lower()
        if u in seen:
            continue
        if "instagram.com" in low and ("/reel" in low or "/reels/" in low or "/p/" in low):
            seen.add(u)
            out.append(u)
        elif "instagr.am" in low:
            seen.add(u)
            out.append(u)
    return out


def extract_urls_multiline(text: str) -> list[str]:
    """URLs from paste — one per line or embedded; Instagram preferred, others kept if http(s)."""
    ig = extract_reel_urls(text)
    if ig:
        return ig
    lines = [ln.strip().rstrip(").,;]'\"") for ln in (text or "").splitlines()]
    out: list[str] = []
    for ln in lines:
        if not ln or ln.startswith("#"):
            continue
        m = re.match(r"https?://\S+", ln)
        if m:
            out.append(m.group(0).rstrip(").,;]'\"") )
    return out


async def fetch_oembed(url: str) -> dict[str, Any]:
    """Public Instagram oEmbed — caption/title/author when Meta exposes them."""
    endpoints = [
        "https://www.instagram.com/oembed/",
        "https://api.instagram.com/oembed",
    ]
    headers = {"User-Agent": "VOXORYL/1.0 (local assistant; oEmbed only)"}
    last_err = ""
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        for base in endpoints:
            try:
                r = await client.get(base, params={"url": url, "omitscript": "true"}, headers=headers)
                if r.status_code >= 400:
                    last_err = f"HTTP {r.status_code}"
                    continue
                data = r.json()
                if isinstance(data, dict) and (data.get("title") or data.get("author_name")):
                    return {"ok": True, "oembed": data, "endpoint": base}
                if isinstance(data, dict):
                    return {"ok": True, "oembed": data, "endpoint": base}
            except Exception as exc:
                last_err = str(exc)
    return {
        "ok": False,
        "error": last_err or "oEmbed unavailable",
        "hint": (
            "Instagram often blocks oEmbed without a public post or app token. "
            "Export/download the Reel and upload the video file instead."
        ),
        "tos": TOS_NOTE,
    }


async def _optional_transcribe_video(video_path: Path) -> dict[str, Any]:
    """Extract audio (ffmpeg) and run local ASR when available — soft-fail."""
    if not video_path.exists():
        return {"ok": False, "error": "missing video"}
    import asyncio

    if not shutil.which("ffmpeg"):
        return {"ok": False, "error": "ffmpeg not found", "hint": "Install ffmpeg to auto-transcribe uploads."}

    wav = video_path.with_suffix(".wav")
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(wav),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=120)
    except Exception as exc:
        return {"ok": False, "error": f"ffmpeg failed: {exc}"}

    if not wav.exists() or wav.stat().st_size < 64:
        return {"ok": False, "error": "no audio track extracted"}

    try:
        from voxoryl.transcribe import _transcribe_file

        text, backend = await asyncio.to_thread(_transcribe_file, wav, language="en")
        return {"ok": bool(text.strip()), "transcript": (text or "").strip(), "backend": backend}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "hint": "Install onnx-asr or faster-whisper for transcripts."}
    finally:
        try:
            if wav.exists():
                wav.unlink()
        except OSError:
            pass


async def _summarize_reel(*, title: str, caption: str, transcript: str, url: str = "") -> dict[str, Any]:
    blob = "\n".join(
        x
        for x in (
            f"Title: {title}" if title else "",
            f"Caption: {caption}" if caption else "",
            f"Transcript: {transcript}" if transcript else "",
            f"URL: {url}" if url else "",
        )
        if x
    ).strip()
    if not blob:
        return {"ok": False, "summary": "", "title": title or "Untitled reel"}

    try:
        from voxoryl.llm import chat_local, parse_json_loose

        raw = await chat_local(
            [
                {
                    "role": "system",
                    "content": (
                        "Summarize this Instagram Reel for the owner's local assistant. "
                        'Return ONLY JSON: {"title":"...","summary":"1-2 sentences","tags":["..."]}'
                    ),
                },
                {"role": "user", "content": blob[:6000]},
            ],
            temperature=0.2,
        )
        parsed = parse_json_loose(raw) or {}
        return {
            "ok": True,
            "title": str(parsed.get("title") or title or "Reel").strip(),
            "summary": str(parsed.get("summary") or "").strip(),
            "tags": [str(t).strip() for t in (parsed.get("tags") or []) if str(t).strip()][:8],
        }
    except Exception as exc:
        summary = (caption or transcript or title or "")[:280]
        return {"ok": True, "title": title or "Reel", "summary": summary, "tags": [], "note": str(exc)}


def _heuristic_gate(*, title: str, caption: str, transcript: str, summary: str) -> dict[str, Any]:
    """Deterministic fallback when LLM gate is unavailable."""
    text = " ".join(x for x in (title, caption, transcript, summary) if x).strip()
    if len(text) < 24:
        return {
            "usefulness": 0.1,
            "usefulness_reason": "Too little content to act on.",
            "credibility": 0.2,
            "credibility_notes": "Insufficient text to verify anything.",
            "claims_vs_speculation": "speculation",
            "red_flags": ["empty_or_short"],
            "outcome": "reject",
            "rationale": "Almost no content — rejected.",
            "source": "heuristic",
        }
    low = text.lower()
    red: list[str] = []
    for phrase in (
        "guaranteed",
        "miracle",
        "cure",
        "get rich quick",
        "secret hack",
        "doctors hate",
        "crypto pump",
        "100% returns",
    ):
        if phrase in low:
            red.append(phrase.replace(" ", "_"))
    usefulness = 0.55
    if any(k in low for k in ("tip", "how to", "workflow", "tool", "repo", "shortcut", "template")):
        usefulness = 0.72
    credibility = 0.55 if not red else 0.28
    if red:
        outcome = "quarantine"
        rationale = "Interesting but flagged language — review before treating as fact."
    elif usefulness >= 0.65:
        outcome = "promote"
        rationale = "Looks actionable enough for the tips vault (heuristic)."
    else:
        outcome = "quarantine"
        rationale = "Saved for review — not clearly actionable yet."
    return {
        "usefulness": usefulness,
        "usefulness_reason": "Heuristic length/keyword score (LLM gate unavailable).",
        "credibility": credibility,
        "credibility_notes": "; ".join(red) if red else "No strong red flags in heuristic scan.",
        "claims_vs_speculation": "mixed" if red else "claims",
        "red_flags": red,
        "outcome": outcome,
        "rationale": rationale,
        "source": "heuristic",
    }


async def gate_reel(
    *,
    title: str = "",
    caption: str = "",
    transcript: str = "",
    summary: str = "",
    url: str = "",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Score usefulness + credibility; outcome promote | quarantine | reject."""
    blob = "\n".join(
        x
        for x in (
            f"Title: {title}" if title else "",
            f"Caption: {caption}" if caption else "",
            f"Summary: {summary}" if summary else "",
            f"Transcript: {transcript}" if transcript else "",
            f"Tags: {', '.join(tags or [])}" if tags else "",
            f"URL: {url}" if url else "",
        )
        if x
    ).strip()
    if not blob:
        return _heuristic_gate(title=title, caption=caption, transcript=transcript, summary=summary)

    try:
        from voxoryl.llm import chat_local, parse_json_loose

        raw = await chat_local(
            [
                {"role": "system", "content": GATE_SYSTEM},
                {"role": "user", "content": blob[:7000]},
            ],
            temperature=0.1,
        )
        parsed = parse_json_loose(raw) or {}
        outcome = str(parsed.get("outcome") or "quarantine").strip().lower()
        if outcome not in {"promote", "quarantine", "reject"}:
            outcome = "quarantine"
        try:
            usefulness = float(parsed.get("usefulness", 0.4))
        except (TypeError, ValueError):
            usefulness = 0.4
        try:
            credibility = float(parsed.get("credibility", 0.4))
        except (TypeError, ValueError):
            credibility = 0.4
        usefulness = max(0.0, min(1.0, usefulness))
        credibility = max(0.0, min(1.0, credibility))
        # Soft floor: never promote if either score is very low
        if outcome == "promote" and (usefulness < 0.45 or credibility < 0.4):
            outcome = "quarantine"
        red_flags = [str(x).strip() for x in (parsed.get("red_flags") or []) if str(x).strip()][:8]
        return {
            "usefulness": usefulness,
            "usefulness_reason": str(parsed.get("usefulness_reason") or "").strip()[:400],
            "credibility": credibility,
            "credibility_notes": str(parsed.get("credibility_notes") or "").strip()[:400],
            "claims_vs_speculation": str(parsed.get("claims_vs_speculation") or "mixed").strip()[:40],
            "red_flags": red_flags,
            "outcome": outcome,
            "rationale": str(parsed.get("rationale") or outcome).strip()[:400],
            "source": "llm",
        }
    except Exception:
        return _heuristic_gate(title=title, caption=caption, transcript=transcript, summary=summary)


async def _apply_gate_and_vault(item: dict[str, Any], *, run_gate: bool = True) -> dict[str, Any] | None:
    """Attach gate to item; promote → knowledge vault only."""
    if not run_gate:
        item["gate"] = None
        item["kb_status"] = "skipped"
        item["status"] = item.get("status") or "ready"
        return None

    gate = await gate_reel(
        title=str(item.get("title") or ""),
        caption=str(item.get("caption") or ""),
        transcript=str(item.get("transcript") or ""),
        summary=str(item.get("summary") or ""),
        url=str(item.get("url") or ""),
        tags=list(item.get("tags") or []),
    )
    item["gate"] = gate
    outcome = gate.get("outcome") or "quarantine"
    vault: dict[str, Any] | None = None

    if outcome == "promote":
        item["kb_status"] = "promoted"
        item["status"] = "promoted"
        try:
            from voxoryl.ingest import distill_and_save

            body = "\n".join(
                x
                for x in (
                    item.get("title"),
                    item.get("caption"),
                    item.get("transcript"),
                    item.get("summary"),
                    f"Author: {item.get('author')}" if item.get("author") else "",
                    f"Gate: {gate.get('rationale')}",
                )
                if x
            )
            vault = await distill_and_save(body, url=item.get("url") or None)
        except Exception as exc:
            vault = {"ok": False, "error": str(exc)}
            item["kb_status"] = "promote_failed"
            item["status"] = "quarantined"
            item["gate"]["outcome"] = "quarantine"
            item["gate"]["rationale"] = f"Promote failed ({exc}); held in quarantine."
    elif outcome == "reject":
        item["kb_status"] = "rejected"
        item["status"] = "rejected"
    else:
        item["kb_status"] = "quarantined"
        item["status"] = "quarantined"

    return vault


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    """Safe API shape — absolute paths as strings; no secrets."""
    gate = item.get("gate")
    return {
        "id": item.get("id"),
        "created_at": item.get("created_at"),
        "source": item.get("source"),
        "url": item.get("url"),
        "title": item.get("title"),
        "caption": item.get("caption"),
        "summary": item.get("summary"),
        "transcript": item.get("transcript"),
        "tags": item.get("tags") or [],
        "author": item.get("author"),
        "video_path": item.get("video_path"),
        "status": item.get("status"),
        "kb_status": item.get("kb_status"),
        "collection_id": item.get("collection_id"),
        "gate": gate,
        "hints": item.get("hints") or [],
    }


def _public_collection(col: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": col.get("id"),
        "name": col.get("name"),
        "created_at": col.get("created_at"),
        "source": col.get("source"),
        "item_ids": list(col.get("item_ids") or []),
        "stats": col.get("stats") or {},
        "notes": col.get("notes") or [],
    }


def list_reels(*, limit: int = 50, status: str = "", collection_id: str = "") -> dict[str, Any]:
    items = _load_index().get("items") or []
    if collection_id.strip():
        cid = collection_id.strip()
        items = [i for i in items if str(i.get("collection_id") or "") == cid]
    if status.strip():
        st = status.strip().lower()
        items = [
            i
            for i in items
            if str(i.get("status") or "").lower() == st
            or str(i.get("kb_status") or "").lower() == st
            or (st == "quarantine" and str(i.get("status") or "").lower() == "quarantined")
        ]
    items = sorted(items, key=lambda x: str(x.get("created_at") or ""), reverse=True)
    clipped = [_public_item(i) for i in items[: max(1, min(limit, 200))]]
    return {
        "ok": True,
        "count": len(clipped),
        "total": len(items),
        "items": clipped,
        "dir": str(reels_dir().resolve()),
        "tos": TOS_NOTE,
        "saved_graph_note": SAVED_GRAPH_NOTE,
    }


def list_quarantined(*, limit: int = 50) -> dict[str, Any]:
    out = list_reels(limit=limit, status="quarantined")
    n = out.get("total") or 0
    out["speak"] = (
        f"{n} quarantined reel(s) waiting for review in data/reels/."
        if n
        else "No quarantined reels right now."
    )
    return out


def get_reel(reel_id: str) -> dict[str, Any]:
    for item in _load_index().get("items") or []:
        if str(item.get("id")) == str(reel_id):
            return {"ok": True, "item": _public_item(item), "tos": TOS_NOTE}
    return {"ok": False, "error": "reel not found", "id": reel_id}


def latest_reel() -> dict[str, Any] | None:
    items = _load_index().get("items") or []
    if not items:
        return None
    items = sorted(items, key=lambda x: str(x.get("created_at") or ""), reverse=True)
    return items[0]


def _append_item(item: dict[str, Any]) -> dict[str, Any]:
    data = _load_index()
    data["items"].append(item)
    _save_index(data)
    return item


def _update_item(reel_id: str, mutator) -> dict[str, Any] | None:
    data = _load_index()
    for i, item in enumerate(data.get("items") or []):
        if str(item.get("id")) == str(reel_id):
            mutator(item)
            data["items"][i] = item
            _save_index(data)
            return item
    return None


def list_collections(*, limit: int = 30) -> dict[str, Any]:
    cols = _load_collections().get("collections") or []
    cols = sorted(cols, key=lambda x: str(x.get("created_at") or ""), reverse=True)
    clipped = [_public_collection(c) for c in cols[: max(1, min(limit, 100))]]
    return {
        "ok": True,
        "count": len(clipped),
        "total": len(cols),
        "collections": clipped,
        "tos": TOS_NOTE,
        "saved_graph_note": SAVED_GRAPH_NOTE,
    }


def get_collection(collection_id: str) -> dict[str, Any]:
    for col in _load_collections().get("collections") or []:
        if str(col.get("id")) == str(collection_id):
            items = [
                _public_item(i)
                for i in (_load_index().get("items") or [])
                if str(i.get("collection_id") or "") == str(collection_id)
            ]
            return {
                "ok": True,
                "collection": _public_collection(col),
                "items": items,
                "tos": TOS_NOTE,
            }
    return {"ok": False, "error": "collection not found", "id": collection_id}


def collection_learned(*, collection_id: str = "") -> dict[str, Any]:
    """Summarize what was promoted / quarantined from a collection (or latest)."""
    cols = _load_collections().get("collections") or []
    col = None
    if collection_id.strip():
        for c in cols:
            if str(c.get("id")) == collection_id.strip():
                col = c
                break
    else:
        if cols:
            cols = sorted(cols, key=lambda x: str(x.get("created_at") or ""), reverse=True)
            col = cols[0]
    if not col:
        return {
            "ok": False,
            "error": "no collections yet",
            "speak": "No collections imported yet. Paste a URL list or drop a ZIP in Settings → Reels.",
            "tos": TOS_NOTE,
        }

    items = [
        i
        for i in (_load_index().get("items") or [])
        if str(i.get("collection_id") or "") == str(col.get("id"))
    ]
    promoted = [i for i in items if str(i.get("kb_status") or "") == "promoted"]
    quarantined = [i for i in items if str(i.get("status") or "") == "quarantined"]
    rejected = [i for i in items if str(i.get("status") or "") == "rejected"]

    tips = []
    for i in promoted[:8]:
        line = (i.get("summary") or i.get("title") or "").strip()
        if line:
            tips.append(line)

    name = col.get("name") or "that collection"
    stats = col.get("stats") or {}
    speak_parts = [
        f"From “{name}”: {stats.get('promoted', len(promoted))} promoted to knowledge, "
        f"{stats.get('quarantined', len(quarantined))} quarantined, "
        f"{stats.get('rejected', len(rejected))} rejected."
    ]
    if tips:
        speak_parts.append("Learned: " + "; ".join(tips[:3]))
    elif quarantined:
        speak_parts.append("Nothing promoted yet — review quarantined items in the Reels library.")

    return {
        "ok": True,
        "collection": _public_collection(col),
        "promoted": [_public_item(i) for i in promoted[:20]],
        "quarantined": [_public_item(i) for i in quarantined[:20]],
        "rejected_count": len(rejected),
        "speak": " ".join(speak_parts),
        "tos": TOS_NOTE,
    }


def _new_collection(*, name: str, source: str, notes: list[str] | None = None) -> dict[str, Any]:
    col = {
        "id": uuid.uuid4().hex[:12],
        "name": (name or "Imported collection").strip()[:120],
        "created_at": _now_iso(),
        "source": source,
        "item_ids": [],
        "stats": {"promoted": 0, "quarantined": 0, "rejected": 0, "failed": 0},
        "notes": notes or [],
    }
    data = _load_collections()
    data["collections"].append(col)
    _save_collections(data)
    return col


def _bump_collection_stats(collection_id: str, item: dict[str, Any]) -> None:
    data = _load_collections()
    for col in data.get("collections") or []:
        if str(col.get("id")) != str(collection_id):
            continue
        ids = list(col.get("item_ids") or [])
        if item.get("id") and item["id"] not in ids:
            ids.append(item["id"])
        col["item_ids"] = ids
        stats = col.setdefault("stats", {"promoted": 0, "quarantined": 0, "rejected": 0, "failed": 0})
        kb = str(item.get("kb_status") or item.get("status") or "")
        if kb == "promoted":
            stats["promoted"] = int(stats.get("promoted") or 0) + 1
        elif kb in {"quarantined", "promote_failed"} or item.get("status") == "quarantined":
            stats["quarantined"] = int(stats.get("quarantined") or 0) + 1
        elif kb == "rejected" or item.get("status") == "rejected":
            stats["rejected"] = int(stats.get("rejected") or 0) + 1
        else:
            stats["failed"] = int(stats.get("failed") or 0) + 1
        _save_collections(data)
        return


async def ingest_url(
    url: str,
    *,
    caption_hint: str = "",
    distill: bool = True,
    collection_id: str | None = None,
    source: str = "url",
) -> dict[str, Any]:
    url = (url or "").strip()
    if not url:
        return {"ok": False, "error": "empty url", "tos": TOS_NOTE}
    found = extract_reel_urls(url) or ([url] if "instagram.com" in url.lower() else [])
    if not found:
        return {
            "ok": False,
            "error": "not an Instagram Reel/post URL",
            "hint": "Paste a link like https://www.instagram.com/reel/… or upload a video file.",
            "tos": TOS_NOTE,
        }
    primary = found[0]
    oembed_result = await fetch_oembed(primary)
    oembed = (oembed_result.get("oembed") or {}) if oembed_result.get("ok") else {}
    title = str(oembed.get("title") or "").strip()
    author = str(oembed.get("author_name") or "").strip()
    caption = (caption_hint or title or "").strip()
    hints: list[str] = []
    if not oembed_result.get("ok"):
        hints.append(str(oembed_result.get("hint") or "oEmbed failed — upload the video file if you can."))

    summarized = await _summarize_reel(title=title or "Instagram Reel", caption=caption, transcript="", url=primary)
    reel_id = uuid.uuid4().hex[:12]
    item = {
        "id": reel_id,
        "created_at": _now_iso(),
        "source": source,
        "url": primary,
        "title": summarized.get("title") or title or "Instagram Reel",
        "caption": caption,
        "summary": summarized.get("summary") or caption[:280],
        "transcript": "",
        "tags": summarized.get("tags") or [],
        "author": author,
        "video_path": None,
        "oembed": oembed if oembed else None,
        "status": "understood" if (caption or summarized.get("summary")) else "needs_file",
        "hints": hints,
        "collection_id": collection_id,
        "kb_status": "pending",
    }
    vault = await _apply_gate_and_vault(item, run_gate=distill)
    _append_item(item)
    if collection_id:
        _bump_collection_stats(collection_id, item)

    outcome = (item.get("gate") or {}).get("outcome") if distill else "skipped"
    speak = f"Saved reel “{item['title']}”"
    if outcome == "promote":
        speak += " — promoted to knowledge."
    elif outcome == "quarantine":
        speak += " — quarantined for review."
    elif outcome == "reject":
        speak += " — rejected (not useful enough)."
    elif not (item.get("summary") or item.get("caption")):
        speak = "Saved the link, but Instagram didn’t share a caption — upload the video file so I can summarize it."
    else:
        speak += "."

    return {
        "ok": True,
        "item": _public_item(item),
        "oembed_ok": bool(oembed_result.get("ok")),
        "vault": vault,
        "speak": speak,
        "tos": TOS_NOTE,
        "hint": hints[0] if hints else None,
    }


async def ingest_file(
    data: bytes,
    filename: str = "reel.mp4",
    *,
    url: str = "",
    caption_hint: str = "",
    distill: bool = True,
    transcribe: bool = True,
    collection_id: str | None = None,
    source: str = "upload",
) -> dict[str, Any]:
    if not data:
        return {"ok": False, "error": "empty file", "tos": TOS_NOTE}
    safe_name = Path(filename or "reel.mp4").name
    suffix = Path(safe_name).suffix.lower() or ".mp4"
    if suffix not in _VIDEO_EXTS:
        return {
            "ok": False,
            "error": f"unsupported type {suffix}",
            "hint": f"Upload one of: {', '.join(sorted(_VIDEO_EXTS))}",
            "tos": TOS_NOTE,
        }
    reel_id = uuid.uuid4().hex[:12]
    dest = media_dir() / f"{_now_stamp()}_{reel_id}{suffix}"
    dest.write_bytes(data)

    transcript = ""
    asr_meta: dict[str, Any] = {}
    if transcribe:
        asr_meta = await _optional_transcribe_video(dest)
        if asr_meta.get("ok"):
            transcript = str(asr_meta.get("transcript") or "")

    caption = (caption_hint or "").strip()
    summarized = await _summarize_reel(
        title=Path(safe_name).stem.replace("_", " "),
        caption=caption,
        transcript=transcript,
        url=url,
    )
    item = {
        "id": reel_id,
        "created_at": _now_iso(),
        "source": source,
        "url": (url or "").strip() or None,
        "title": summarized.get("title") or Path(safe_name).stem,
        "caption": caption,
        "summary": summarized.get("summary") or (transcript[:280] if transcript else caption[:280]),
        "transcript": transcript,
        "tags": summarized.get("tags") or [],
        "author": None,
        "video_path": str(dest.resolve()),
        "oembed": None,
        "status": "understood",
        "hints": [] if asr_meta.get("ok") or not transcribe else [str(asr_meta.get("hint") or asr_meta.get("error") or "")],
        "asr": {k: asr_meta.get(k) for k in ("ok", "backend", "error", "hint") if asr_meta},
        "collection_id": collection_id,
        "kb_status": "pending",
    }
    item["hints"] = [h for h in (item.get("hints") or []) if h]
    vault = await _apply_gate_and_vault(item, run_gate=distill)
    _append_item(item)
    if collection_id:
        _bump_collection_stats(collection_id, item)

    outcome = (item.get("gate") or {}).get("outcome") if distill else "skipped"
    speak = f"Saved reel “{item['title']}”" + (" with transcript" if transcript else "")
    if outcome == "promote":
        speak += " — promoted to knowledge."
    elif outcome == "quarantine":
        speak += " — quarantined for review."
    elif outcome == "reject":
        speak += " — rejected."
    else:
        speak += "."

    return {
        "ok": True,
        "item": _public_item(item),
        "vault": vault,
        "speak": speak,
        "tos": TOS_NOTE,
    }


def _parse_manifest(obj: Any) -> tuple[str, list[dict[str, Any]]]:
    """Return (name, entries) from a JSON manifest dict/list."""
    name = "Manifest collection"
    entries: list[dict[str, Any]] = []
    if isinstance(obj, list):
        for row in obj:
            if isinstance(row, str):
                entries.append({"url": row})
            elif isinstance(row, dict):
                entries.append(row)
        return name, entries
    if not isinstance(obj, dict):
        return name, entries
    name = str(obj.get("name") or obj.get("title") or name).strip()[:120]
    raw_items = obj.get("items") or obj.get("reels") or obj.get("urls") or []
    if isinstance(raw_items, list):
        for row in raw_items:
            if isinstance(row, str):
                entries.append({"url": row})
            elif isinstance(row, dict):
                entries.append(row)
    return name, entries


async def import_collection(
    *,
    name: str = "",
    urls_text: str = "",
    folder_path: str = "",
    zip_bytes: bytes | None = None,
    zip_filename: str = "collection.zip",
    manifest: dict[str, Any] | list | None = None,
    manifest_text: str = "",
    use_graph: bool = False,
    distill: bool = True,
    transcribe: bool = True,
) -> dict[str, Any]:
    """
    Import a batch of saved Reels.

    Sources (any combination): multi-line URLs, local folder, ZIP, JSON manifest,
    optional Graph recent media (not personal Saved — see SAVED_GRAPH_NOTE).
    """
    notes: list[str] = []
    work: list[dict[str, Any]] = []

    if urls_text.strip():
        for u in extract_urls_multiline(urls_text):
            work.append({"url": u, "caption": ""})

    if manifest_text.strip():
        try:
            parsed = json.loads(manifest_text)
            mname, entries = _parse_manifest(parsed)
            if not name:
                name = mname
            work.extend(entries)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"invalid manifest JSON: {exc}", "tos": TOS_NOTE}

    if manifest is not None:
        mname, entries = _parse_manifest(manifest)
        if not name:
            name = mname
        work.extend(entries)

    if folder_path.strip():
        folder = Path(folder_path.strip())
        if not folder.is_dir():
            return {"ok": False, "error": f"folder not found: {folder}", "tos": TOS_NOTE}
        man = folder / "manifest.json"
        if man.exists():
            try:
                mname, entries = _parse_manifest(json.loads(man.read_text(encoding="utf-8")))
                if not name:
                    name = mname
                for e in entries:
                    if e.get("file") and not Path(str(e["file"])).is_absolute():
                        e = {**e, "file": str((folder / str(e["file"])).resolve())}
                    work.append(e)
            except (json.JSONDecodeError, OSError) as exc:
                notes.append(f"manifest.json skipped: {exc}")
        for p in sorted(folder.rglob("*")):
            if p.is_file() and p.suffix.lower() in _VIDEO_EXTS:
                work.append({"file": str(p.resolve()), "caption": ""})

    if zip_bytes:
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                extract_root = media_dir() / f"import_{_now_stamp()}_{uuid.uuid4().hex[:6]}"
                extract_root.mkdir(parents=True, exist_ok=True)
                zf.extractall(extract_root)
                man_candidates = list(extract_root.rglob("manifest.json"))
                if man_candidates:
                    try:
                        mname, entries = _parse_manifest(
                            json.loads(man_candidates[0].read_text(encoding="utf-8"))
                        )
                        if not name:
                            name = mname
                        for e in entries:
                            if e.get("file") and not Path(str(e["file"])).is_absolute():
                                e = {
                                    **e,
                                    "file": str((man_candidates[0].parent / str(e["file"])).resolve()),
                                }
                            work.append(e)
                    except (json.JSONDecodeError, OSError) as exc:
                        notes.append(f"zip manifest skipped: {exc}")
                for p in sorted(extract_root.rglob("*")):
                    if p.is_file() and p.suffix.lower() in _VIDEO_EXTS:
                        work.append({"file": str(p.resolve()), "caption": ""})
                notes.append(f"Extracted ZIP {zip_filename} → {extract_root.name}")
        except zipfile.BadZipFile:
            return {"ok": False, "error": "invalid ZIP file", "tos": TOS_NOTE}

    if use_graph:
        from voxoryl.instagram import list_recent_media

        graph = await list_recent_media(limit=25)
        notes.append(SAVED_GRAPH_NOTE)
        if graph.get("ok"):
            for m in graph.get("items") or []:
                permalink = str(m.get("permalink") or m.get("url") or "").strip()
                if permalink:
                    work.append(
                        {
                            "url": permalink,
                            "caption": str(m.get("caption") or "")[:500],
                        }
                    )
            if not name:
                name = "Graph account media"
        else:
            notes.append(str(graph.get("error") or graph.get("hint") or "Graph media unavailable"))

    # Dedupe by url or file path
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for row in work:
        key = (str(row.get("url") or "").strip() or str(row.get("file") or "").strip()).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(row)

    if not unique:
        return {
            "ok": False,
            "error": "nothing to import",
            "hint": (
                "Paste multi-line Instagram Reel URLs, upload a ZIP of videos, "
                "point at an export folder, or provide a JSON manifest."
            ),
            "notes": notes,
            "speak": "I need a URL list, ZIP, folder, or manifest to import a collection.",
            "tos": TOS_NOTE,
            "saved_graph_note": SAVED_GRAPH_NOTE,
        }

    col = _new_collection(
        name=name or f"Collection {_now_stamp()}",
        source=(
            "graph"
            if use_graph and not (urls_text or zip_bytes or folder_path or manifest or manifest_text)
            else "mixed"
            if sum(bool(x) for x in (urls_text, zip_bytes, folder_path, manifest, manifest_text, use_graph)) > 1
            else "urls"
            if urls_text
            else "zip"
            if zip_bytes
            else "folder"
            if folder_path
            else "manifest"
            if (manifest or manifest_text)
            else "graph"
        ),
        notes=notes,
    )

    results: list[dict[str, Any]] = []
    for row in unique[:80]:
        caption = str(row.get("caption") or row.get("caption_hint") or "").strip()
        file_path = str(row.get("file") or row.get("path") or row.get("video") or "").strip()
        url = str(row.get("url") or row.get("permalink") or "").strip()
        try:
            if file_path:
                p = Path(file_path)
                if not p.exists():
                    results.append({"ok": False, "error": f"missing file {p}", "url": url or file_path})
                    continue
                r = await ingest_file(
                    p.read_bytes(),
                    p.name,
                    url=url,
                    caption_hint=caption,
                    distill=distill,
                    transcribe=transcribe,
                    collection_id=col["id"],
                    source="collection",
                )
            elif url:
                r = await ingest_url(
                    url,
                    caption_hint=caption,
                    distill=distill,
                    collection_id=col["id"],
                    source="collection",
                )
            else:
                results.append({"ok": False, "error": "row needs url or file"})
                continue
            results.append(
                {
                    "ok": bool(r.get("ok")),
                    "item": r.get("item"),
                    "error": r.get("error"),
                    "speak": r.get("speak"),
                }
            )
        except Exception as exc:
            results.append({"ok": False, "error": str(exc)})

    # Reload collection for fresh stats
    got = get_collection(col["id"])
    stats = (got.get("collection") or {}).get("stats") or col.get("stats") or {}
    ok_n = sum(1 for r in results if r.get("ok"))
    speak = (
        f"Imported {ok_n} of {len(unique)} into “{col['name']}”. "
        f"Promoted {stats.get('promoted', 0)}, quarantined {stats.get('quarantined', 0)}, "
        f"rejected {stats.get('rejected', 0)}."
    )
    return {
        "ok": True,
        "collection": got.get("collection") or _public_collection(col),
        "results": results,
        "imported": ok_n,
        "attempted": len(unique),
        "notes": notes,
        "speak": speak,
        "tos": TOS_NOTE,
        "saved_graph_note": SAVED_GRAPH_NOTE,
    }


async def recall_about(*, reel_id: str = "") -> dict[str, Any]:
    """Answer “what was that reel about?” from the latest or a specific id."""
    item = None
    if reel_id.strip():
        got = get_reel(reel_id.strip())
        if got.get("ok"):
            item = got["item"]
        else:
            return {
                "ok": False,
                "error": "reel not found",
                "speak": "I don’t have that reel id saved.",
                "tos": TOS_NOTE,
            }
    else:
        raw = latest_reel()
        if raw:
            item = _public_item(raw)

    if not item:
        return {
            "ok": False,
            "error": "no reels yet",
            "hint": "Paste an Instagram Reel URL or upload a short video in Settings → Reels.",
            "speak": "No reels saved yet. Share a link, drop a video, or import a collection.",
            "tos": TOS_NOTE,
        }

    summary = (item.get("summary") or item.get("caption") or item.get("transcript") or "").strip()
    title = item.get("title") or "that reel"
    gate = item.get("gate") or {}
    gate_bit = ""
    if gate.get("outcome"):
        gate_bit = f" Gate: {gate.get('outcome')} — {gate.get('rationale') or ''}."
    if not summary:
        return {
            "ok": True,
            "item": item,
            "speak": (
                f"I saved “{title}” but don’t have a caption or transcript yet. "
                "Upload the video file so I can summarize it."
            ),
            "tos": TOS_NOTE,
        }
    return {
        "ok": True,
        "item": item,
        "speak": f"“{title}”: {summary}.{gate_bit}".strip(),
        "tos": TOS_NOTE,
    }


async def tool_reels(
    *,
    action: str = "status",
    url: str = "",
    message: str = "",
    reel_id: str = "",
    video_path: str = "",
    caption: str = "",
    collection_id: str = "",
    folder_path: str = "",
    urls_text: str = "",
    use_graph: bool = False,
    name: str = "",
) -> dict[str, Any]:
    """
    Agent/tool entry:
    - status | list
    - ingest / save (url or message containing url)
    - import / collection (batch URLs / folder / graph)
    - learned / what_learned
    - quarantine / quarantined
    - about / recall
    - publish (delegates to Instagram scaffold)
    """
    action = (action or "status").strip().lower()
    blob = " ".join(x for x in (message, url, urls_text) if x).strip()
    lower = blob.lower()

    if action in {"about", "recall", "latest", "what"}:
        return await recall_about(reel_id=reel_id)

    if action in {"quarantine", "quarantined", "show_quarantine", "show_quarantined"}:
        return list_quarantined(limit=30)

    if action in {"learned", "what_learned", "collection_summary", "learn"}:
        return collection_learned(collection_id=collection_id)

    if action in {"collections", "list_collections"}:
        listed = list_collections()
        n = listed.get("total") or 0
        listed["speak"] = f"{n} reel collection(s) imported." if n else "No collections yet."
        return listed

    if action in {"import", "collection", "import_collection", "batch"}:
        text = urls_text or message or url
        # Detect intentional multi-URL paste
        urls = extract_urls_multiline(text) if text else []
        return await import_collection(
            name=name,
            urls_text="\n".join(urls) if urls else text,
            folder_path=folder_path or video_path,
            use_graph=use_graph
            or any(
                k in lower
                for k in (
                    "from graph",
                    "via graph",
                    "instagram saved",
                    "saved reels",
                    "my saved",
                    "from instagram account",
                )
            ),
            distill=True,
            transcribe=True,
        )

    if action in {"list", "status"}:
        listed = list_reels(limit=20)
        n = listed.get("total") or 0
        q = sum(1 for i in (listed.get("items") or []) if str(i.get("status") or "") == "quarantined")
        latest = latest_reel()
        speak = (
            f"{n} reel(s) in the library"
            + (f", {q} quarantined" if q else "")
            + "."
            + (f" Latest: {latest.get('title')}." if latest else "")
        )
        return {**listed, "speak": speak, "publish_configured": _publish_configured()}

    if action in {"publish", "post"}:
        from voxoryl.instagram import publish_reel

        path = (video_path or "").strip()
        cap = (caption or "").strip()
        latest = latest_reel()
        if not path and reel_id:
            got = get_reel(reel_id)
            if got.get("ok"):
                path = str((got["item"] or {}).get("video_path") or "")
                if not cap:
                    cap = str((got["item"] or {}).get("caption") or (got["item"] or {}).get("summary") or "")
        if not path and latest:
            path = str(latest.get("video_path") or latest.get("url") or "")
            if not cap:
                cap = str(latest.get("caption") or latest.get("summary") or "")
        return await publish_reel(path, caption=cap)

    # Voice shortcuts without explicit action
    if any(
        k in lower
        for k in (
            "import my saved",
            "import saved reels",
            "import my reels",
            "import collection",
            "batch of reels",
            "saved reels",
        )
    ):
        return await tool_reels(
            action="import",
            message=message,
            url=url,
            urls_text=urls_text or message,
            folder_path=folder_path,
            video_path=video_path,
            use_graph=True,
            name=name,
        )
    if any(k in lower for k in ("quarantined reel", "show quarantine", "show quarantined")):
        return list_quarantined(limit=30)
    if any(
        k in lower
        for k in (
            "what did you learn",
            "learned from that collection",
            "learn from that collection",
            "from that collection",
        )
    ):
        return collection_learned(collection_id=collection_id)

    if action in {"ingest", "save", "add", "share"} or extract_reel_urls(blob) or blob:
        urls = extract_reel_urls(blob)
        if len(urls) > 1:
            return await import_collection(name=name or "Pasted URLs", urls_text="\n".join(urls), distill=True)
        if urls:
            return await ingest_url(urls[0], caption_hint=caption)
        if video_path.strip():
            p = Path(video_path.strip())
            if p.is_dir():
                return await import_collection(name=name, folder_path=str(p), distill=True, transcribe=True)
            if p.exists():
                return await ingest_file(p.read_bytes(), p.name, url=url, caption_hint=caption)
            return {"ok": False, "error": f"file not found: {p}", "tos": TOS_NOTE}
        return {
            "ok": False,
            "error": "need an Instagram Reel URL, video file, or collection",
            "hint": "Paste URLs, upload via Settings → Reels, or say “import my saved reels”.",
            "speak": "Send me a Reel link, a collection of links, or upload video files.",
            "tos": TOS_NOTE,
            "saved_graph_note": SAVED_GRAPH_NOTE,
        }

    return await tool_reels(action="status")


def _publish_configured() -> bool:
    try:
        from voxoryl.instagram import is_configured

        return is_configured()
    except Exception:
        return False
