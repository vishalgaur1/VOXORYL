from __future__ import annotations

"""
Instagram publishing scaffold — post Reels *as* VOXORYL's own Business/Creator account.

Uses Meta Graph API when INSTAGRAM_ACCESS_TOKEN + INSTAGRAM_BUSINESS_ACCOUNT_ID
are set in .env. Without tokens, returns an honest configuration error.

P0 limits:
- Graph Reels publishing typically needs a *publicly reachable* video URL
  (or Meta's resumable upload — not wired yet). Local-only paths get a clear hint.
- No credential stuffing / user-password login. Owner creates a Meta app + IG
  Professional account and pastes long-lived tokens locally.
"""

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from voxoryl.config import settings

GRAPH_VERSION = "v21.0"


def is_configured() -> bool:
    return bool(
        (settings.instagram_access_token or "").strip()
        and (settings.instagram_business_account_id or "").strip()
    )


SAVED_NOTE = (
    "Meta Graph does not expose a personal Instagram “Saved” collection. "
    "With tokens set, VOXORYL can list recent media from *your* Business/Creator account only. "
    "For Saved Reels from others: paste URLs, ZIP/folder export, or a JSON manifest."
)


def status() -> dict[str, Any]:
    configured = is_configured()
    return {
        "ok": True,
        "configured": configured,
        "graph_version": (settings.instagram_graph_version or GRAPH_VERSION).strip() or GRAPH_VERSION,
        "business_account_id_set": bool((settings.instagram_business_account_id or "").strip()),
        "access_token_set": bool((settings.instagram_access_token or "").strip()),
        "hint": (
            "Ready to publish when tokens are set."
            if configured
            else (
                "Create a Meta Business/Creator Instagram account for VOXORYL, "
                "add a Meta app with Instagram Content Publishing, then set "
                "INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_BUSINESS_ACCOUNT_ID in .env "
                "(see README Instagram section)."
            )
        ),
        "saved_note": SAVED_NOTE,
        "tos": (
            "Publishing uses Meta Graph API with tokens you own. "
            "VOXORYL never asks for Instagram passwords or scrapes private sessions."
        ),
    }


async def list_recent_media(*, limit: int = 25) -> dict[str, Any]:
    """
    List recent media from the configured IG Business/Creator account.

    This is NOT personal “Saved” posts — Graph does not expose that collection.
    """
    st = status()
    if not st["configured"]:
        return {
            "ok": False,
            "error": "instagram not configured",
            "hint": st["hint"],
            "saved_note": SAVED_NOTE,
            "speak": "Instagram tokens aren’t set — paste a URL collection or export folder instead.",
            "tos": st["tos"],
            "items": [],
        }

    ig_user = settings.instagram_business_account_id.strip()
    token = settings.instagram_access_token.strip()
    base = _graph_base()
    capped = max(1, min(int(limit or 25), 50))
    fields = "id,caption,media_type,media_url,permalink,timestamp,thumbnail_url"

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            r = await client.get(
                f"{base}/{ig_user}/media",
                params={"fields": fields, "limit": capped, "access_token": token},
            )
        except Exception as exc:
            return {
                "ok": False,
                "error": f"graph media failed: {exc}",
                "saved_note": SAVED_NOTE,
                "speak": "Couldn’t reach Meta Graph for account media.",
                "tos": st["tos"],
                "items": [],
            }

        if r.status_code >= 400:
            return {
                "ok": False,
                "error": f"media list HTTP {r.status_code}",
                "detail": r.text[:600],
                "hint": "Check token scopes (instagram_basic / pages_read_engagement) and business account id.",
                "saved_note": SAVED_NOTE,
                "speak": "Graph rejected the media list — check token permissions.",
                "tos": st["tos"],
                "items": [],
            }

        try:
            payload = r.json()
        except Exception:
            payload = {}

    raw = payload.get("data") if isinstance(payload, dict) else []
    items: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            items.append(
                {
                    "id": row.get("id"),
                    "caption": row.get("caption") or "",
                    "media_type": row.get("media_type"),
                    "media_url": row.get("media_url"),
                    "permalink": row.get("permalink") or row.get("media_url"),
                    "url": row.get("permalink") or row.get("media_url"),
                    "timestamp": row.get("timestamp"),
                    "thumbnail_url": row.get("thumbnail_url"),
                }
            )

    return {
        "ok": True,
        "items": items,
        "count": len(items),
        "saved_note": SAVED_NOTE,
        "speak": (
            f"Found {len(items)} recent post(s) on the VOXORYL Instagram account "
            "(not personal Saved)."
            if items
            else "No recent account media returned from Graph."
        ),
        "tos": st["tos"],
    }


def _graph_base() -> str:
    ver = (settings.instagram_graph_version or GRAPH_VERSION).strip() or GRAPH_VERSION
    return f"https://graph.facebook.com/{ver}"


def _is_http_url(value: str) -> bool:
    try:
        p = urlparse(value)
        return p.scheme in {"http", "https"} and bool(p.netloc)
    except Exception:
        return False


async def publish_reel(video_path: str, caption: str = "") -> dict[str, Any]:
    """
    Publish a Reel to VOXORYL's Instagram Professional account.

    video_path: local filesystem path OR https URL Meta can fetch.
    """
    st = status()
    if not st["configured"]:
        return {
            "ok": False,
            "error": "instagram not configured",
            "speak": "Instagram publishing isn’t set up yet — add Meta tokens in .env.",
            **{k: st[k] for k in ("hint", "tos", "configured")},
        }

    video = (video_path or "").strip()
    if not video:
        return {
            "ok": False,
            "error": "empty video_path",
            "hint": "Pass a public https URL to the MP4, or a local file once resumable upload is wired.",
            "speak": "I need a video path or public URL to post.",
            "tos": st["tos"],
        }

    # Local files: Graph container create expects a public video_url for Reels in the simple flow.
    if not _is_http_url(video):
        path = Path(video)
        if not path.exists():
            return {
                "ok": False,
                "error": f"file not found: {video}",
                "speak": "That video file isn’t on disk.",
                "tos": st["tos"],
            }
        return {
            "ok": False,
            "error": "local file publish not implemented in P0",
            "path": str(path.resolve()),
            "hint": (
                "Meta Graph Reels containers need a publicly reachable video_url, "
                "or Instagram’s resumable upload API. Host the MP4 briefly (or use a CDN URL), "
                "then call publish_reel(https://…/clip.mp4, caption=…)."
            ),
            "speak": (
                "I have the file locally, but Instagram needs a public video URL for this scaffold. "
                "Host it temporarily or wait for resumable upload."
            ),
            "configured": True,
            "tos": st["tos"],
        }

    ig_user = settings.instagram_business_account_id.strip()
    token = settings.instagram_access_token.strip()
    caption = (caption or "").strip()
    base = _graph_base()

    async with httpx.AsyncClient(timeout=60.0) as client:
        # 1) Create media container
        try:
            create = await client.post(
                f"{base}/{ig_user}/media",
                data={
                    "media_type": "REELS",
                    "video_url": video,
                    "caption": caption,
                    "share_to_feed": "true",
                    "access_token": token,
                },
            )
        except Exception as exc:
            return {
                "ok": False,
                "error": f"graph create failed: {exc}",
                "speak": "Couldn’t reach Meta Graph API.",
                "tos": st["tos"],
            }

        if create.status_code >= 400:
            detail = create.text[:800]
            return {
                "ok": False,
                "error": f"container create HTTP {create.status_code}",
                "detail": detail,
                "hint": "Check token permissions (instagram_content_publish), account id, and that video_url is public HTTPS.",
                "speak": "Instagram rejected the upload container — check token permissions and the video URL.",
                "tos": st["tos"],
            }

        try:
            created = create.json()
        except Exception:
            created = {}
        creation_id = str(created.get("id") or "").strip()
        if not creation_id:
            return {
                "ok": False,
                "error": "no creation_id from Graph",
                "detail": created,
                "speak": "Instagram didn’t return a media container id.",
                "tos": st["tos"],
            }

        # 2) Publish container
        try:
            pub = await client.post(
                f"{base}/{ig_user}/media_publish",
                data={"creation_id": creation_id, "access_token": token},
            )
        except Exception as exc:
            return {
                "ok": False,
                "error": f"graph publish failed: {exc}",
                "creation_id": creation_id,
                "speak": "Container created but publish failed — try again in a minute.",
                "tos": st["tos"],
            }

        if pub.status_code >= 400:
            return {
                "ok": False,
                "error": f"publish HTTP {pub.status_code}",
                "detail": pub.text[:800],
                "creation_id": creation_id,
                "hint": "Container may still be processing — wait and retry media_publish with the same creation_id.",
                "speak": "Instagram is still processing the Reel — wait a bit and retry.",
                "tos": st["tos"],
            }

        try:
            published = pub.json()
        except Exception:
            published = {}
        media_id = str(published.get("id") or "").strip()

    return {
        "ok": True,
        "creation_id": creation_id,
        "media_id": media_id,
        "caption": caption,
        "video_url": video,
        "speak": "Reel published to VOXORYL’s Instagram account." if media_id else "Publish request accepted.",
        "tos": st["tos"],
    }
