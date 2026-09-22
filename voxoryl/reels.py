from __future__ import annotations

"""
Reels inbox — share Instagram Reels *with* VOXORYL (local, private).

Accepts a public Reel URL (oEmbed metadata) or a user-provided short video file.
Stores under data/reels/ (gitignored). Optional ASR + LLM summary so Voxy can
answer “what was that reel about?”

ToS: uses official/public oEmbed or files you supply. No login scraping,
credential stuffing, or private-account bypass.
"""

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from voxoryl.config import settings

TOS_NOTE = (
    "VOXORYL only uses public Instagram oEmbed metadata or files you upload. "
    "It does not log into Instagram, scrape private content, or use stolen credentials."
)

_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
_INDEX_NAME = "index.json"


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


def extract_reel_urls(text: str) -> list[str]:
    urls = re.findall(r"https?://[^\s<>\"']+", text or "")
    out: list[str] = []
    for u in urls:
        u = u.rstrip(").,;]'\"")
        low = u.lower()
        if "instagram.com" in low and ("/reel" in low or "/reels/" in low or "/p/" in low):
            out.append(u)
        elif "instagr.am" in low:
            out.append(u)
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
        # Fallback without LLM
        summary = (caption or transcript or title or "")[:280]
        return {"ok": True, "title": title or "Reel", "summary": summary, "tags": [], "note": str(exc)}


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    """Safe API shape — absolute paths as strings; no secrets."""
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
        "hints": item.get("hints") or [],
    }


def list_reels(*, limit: int = 50) -> dict[str, Any]:
    items = _load_index().get("items") or []
    items = sorted(items, key=lambda x: str(x.get("created_at") or ""), reverse=True)
    clipped = [_public_item(i) for i in items[: max(1, min(limit, 200))]]
    return {
        "ok": True,
        "count": len(clipped),
        "total": len(items),
        "items": clipped,
        "dir": str(reels_dir().resolve()),
        "tos": TOS_NOTE,
    }


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


async def ingest_url(url: str, *, caption_hint: str = "", distill: bool = True) -> dict[str, Any]:
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
        "source": "url",
        "url": primary,
        "title": summarized.get("title") or title or "Instagram Reel",
        "caption": caption,
        "summary": summarized.get("summary") or caption[:280],
        "transcript": "",
        "tags": summarized.get("tags") or [],
        "author": author,
        "video_path": None,
        "oembed": oembed if oembed else None,
        "status": "ready" if (caption or summarized.get("summary")) else "needs_file",
        "hints": hints,
    }
    _append_item(item)

    vault: dict[str, Any] | None = None
    if distill and (item.get("summary") or item.get("caption")):
        try:
            from voxoryl.ingest import distill_and_save

            body = "\n".join(
                x
                for x in (
                    item.get("title"),
                    item.get("caption"),
                    item.get("summary"),
                    f"Author: {author}" if author else "",
                )
                if x
            )
            vault = await distill_and_save(body, url=primary)
        except Exception as exc:
            vault = {"ok": False, "error": str(exc)}

    speak = (
        f"Saved reel “{item['title']}”."
        if item.get("summary") or item.get("caption")
        else "Saved the link, but Instagram didn’t share a caption — upload the video file so I can summarize it."
    )
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
        "source": "upload",
        "url": (url or "").strip() or None,
        "title": summarized.get("title") or Path(safe_name).stem,
        "caption": caption,
        "summary": summarized.get("summary") or (transcript[:280] if transcript else caption[:280]),
        "transcript": transcript,
        "tags": summarized.get("tags") or [],
        "author": None,
        "video_path": str(dest.resolve()),
        "oembed": None,
        "status": "ready",
        "hints": [] if asr_meta.get("ok") or not transcribe else [str(asr_meta.get("hint") or asr_meta.get("error") or "")],
        "asr": {k: asr_meta.get(k) for k in ("ok", "backend", "error", "hint") if asr_meta},
    }
    # Drop empty hint strings
    item["hints"] = [h for h in (item.get("hints") or []) if h]
    _append_item(item)

    vault: dict[str, Any] | None = None
    if distill and (item.get("summary") or transcript or caption):
        try:
            from voxoryl.ingest import distill_and_save

            body = "\n".join(
                x
                for x in (item.get("title"), caption, transcript, item.get("summary"))
                if x
            )
            vault = await distill_and_save(body, url=url or None)
        except Exception as exc:
            vault = {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "item": _public_item(item),
        "vault": vault,
        "speak": f"Saved reel “{item['title']}”" + (" with transcript." if transcript else "."),
        "tos": TOS_NOTE,
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
            "speak": "No reels saved yet. Share a link or drop a video in the Reels panel.",
            "tos": TOS_NOTE,
        }

    summary = (item.get("summary") or item.get("caption") or item.get("transcript") or "").strip()
    title = item.get("title") or "that reel"
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
        "speak": f"“{title}”: {summary}",
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
) -> dict[str, Any]:
    """
    Agent/tool entry:
    - status | list
    - ingest / save (url or message containing url)
    - about / recall (“what was that reel about?”)
    - publish (delegates to Instagram scaffold)
    """
    action = (action or "status").strip().lower()
    blob = " ".join(x for x in (message, url) if x).strip()

    if action in {"about", "recall", "latest", "what"}:
        return await recall_about(reel_id=reel_id)

    if action in {"list", "status"}:
        listed = list_reels(limit=20)
        n = listed.get("total") or 0
        latest = latest_reel()
        speak = (
            f"{n} reel(s) in the inbox."
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

    if action in {"ingest", "save", "add", "share"} or extract_reel_urls(blob) or blob:
        urls = extract_reel_urls(blob)
        if urls:
            return await ingest_url(urls[0], caption_hint=caption)
        if video_path.strip():
            p = Path(video_path.strip())
            if p.exists():
                return await ingest_file(p.read_bytes(), p.name, url=url, caption_hint=caption)
            return {"ok": False, "error": f"file not found: {p}", "tos": TOS_NOTE}
        return {
            "ok": False,
            "error": "need an Instagram Reel URL or video file",
            "hint": "Paste https://www.instagram.com/reel/… or upload via Settings → Reels / POST /api/reels",
            "speak": "Send me a Reel link or upload the video file.",
            "tos": TOS_NOTE,
        }

    return await tool_reels(action="status")


def _publish_configured() -> bool:
    try:
        from voxoryl.instagram import is_configured

        return is_configured()
    except Exception:
        return False
