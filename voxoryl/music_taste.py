from __future__ import annotations

"""
Music taste / playlists — ingest Spotify/YouTube Music links or text lists,
store liked artists/genres under data/music_taste/, suggest similar tracks.
Degrades gracefully when research/LLM unavailable.
"""

import json
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from voxoryl.config import settings
from voxoryl.knowledge import knowledge
from voxoryl.llm import chat_local, parse_json_loose
from voxoryl.memory import memory


GENRE_HINTS = {
    "rock": ("rock", "indie rock", "alternative"),
    "pop": ("pop", "synth-pop", "k-pop"),
    "hip hop": ("hip hop", "rap", "trap"),
    "electronic": ("edm", "house", "techno", "electronic"),
    "jazz": ("jazz", "bebop", "swing"),
    "classical": ("classical", "orchestra", "symphony"),
    "metal": ("metal", "heavy metal", "death metal"),
    "r&b": ("r&b", "rnb", "soul"),
    "folk": ("folk", "acoustic", "americana"),
    "indie": ("indie", "indie pop", "bedroom"),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def root() -> Path:
    p = settings.voxoryl_data_dir / "music_taste"
    p.mkdir(parents=True, exist_ok=True)
    (p / "playlists").mkdir(parents=True, exist_ok=True)
    return p


def _taste_path() -> Path:
    return root() / "taste.json"


def load_taste() -> dict[str, Any]:
    path = _taste_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {
        "artists": {},
        "genres": {},
        "tracks": [],
        "playlists": [],
        "notes": [],
        "updated_at": None,
    }


def save_taste(data: dict[str, Any]) -> None:
    data["updated_at"] = _now()
    _taste_path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _extract_urls(text: str) -> list[str]:
    return re.findall(r"https?://[^\s\]\)\"']+", text or "")


def _is_music_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(
        h in host
        for h in (
            "spotify.com",
            "music.youtube.com",
            "youtube.com",
            "youtu.be",
            "music.apple.com",
            "soundcloud.com",
            "tidal.com",
            "deezer.com",
        )
    )


def parse_track_lines(text: str) -> list[dict[str, str]]:
    """Heuristic: 'Artist - Title' or numbered list lines."""
    tracks: list[dict[str, str]] = []
    for line in (text or "").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        raw = re.sub(r"^\d+[\.\)]\s*", "", raw)
        raw = re.sub(r"^[-•*]\s*", "", raw)
        if raw.lower().startswith(("http://", "https://", "playlist", "spotify:", "open.spotify")):
            continue
        if " - " in raw or " – " in raw or " — " in raw:
            parts = re.split(r"\s+[-–—]\s+", raw, maxsplit=1)
            artist, title = (parts[0].strip(), parts[1].strip()) if len(parts) == 2 else ("", raw)
        elif " by " in raw.lower():
            m = re.search(r"(.+?)\s+by\s+(.+)", raw, re.I)
            title, artist = (m.group(1).strip(), m.group(2).strip()) if m else (raw, "")
        else:
            if len(raw) < 3 or len(raw) > 120:
                continue
            artist, title = "", raw
        if title:
            tracks.append({"artist": artist[:80], "title": title[:120]})
    # dedupe
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for t in tracks:
        key = f"{t['artist'].lower()}|{t['title'].lower()}"
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out[:200]


def _guess_genres(artists: list[str], titles: list[str]) -> list[str]:
    blob = " ".join(artists + titles).lower()
    hits: list[str] = []
    for genre, keys in GENRE_HINTS.items():
        if any(k in blob for k in keys):
            hits.append(genre)
    return hits or ["unknown"]


def _instrument_notes_heuristic(artists: Counter, genres: list[str]) -> list[str]:
    notes: list[str] = []
    if artists:
        top = artists.most_common(3)
        notes.append("You lean toward: " + ", ".join(f"{a} ({n})" for a, n in top) + ".")
    if genres and genres != ["unknown"]:
        notes.append("Genre signals: " + ", ".join(genres[:5]) + ".")
    gset = set(genres)
    if "jazz" in gset or "classical" in gset:
        notes.append("Notes & instruments: likely acoustic / piano / brass or strings — warm harmonic focus.")
    elif "electronic" in gset or "hip hop" in gset:
        notes.append("Notes & instruments: synths, 808s, sampled loops — rhythm-forward production.")
    elif "rock" in gset or "metal" in gset or "indie" in gset:
        notes.append("Notes & instruments: guitars + drums; power chords / riffs may be central.")
    elif "folk" in gset:
        notes.append("Notes & instruments: acoustic guitar / storytelling vocals.")
    else:
        notes.append("Notes & instruments: stub — share more tracks for sharper instrumentation insights.")
    return notes


async def ingest_playlist(message: str = "", *, text: str = "") -> dict[str, Any]:
    blob = (text or message or "").strip()
    if not blob:
        return {
            "ok": False,
            "hint": "Paste a Spotify/YouTube Music playlist link, or a text list like 'Artist - Song'.",
            "speak": "Send a playlist link or a list of tracks and I'll remember your taste.",
        }
    urls = [u for u in _extract_urls(blob) if _is_music_url(u)]
    tracks = parse_track_lines(blob)
    taste = load_taste()
    pl_id = str(uuid.uuid4())[:8]
    entry = {
        "id": pl_id,
        "at": _now(),
        "urls": urls,
        "track_count": len(tracks),
        "sample": tracks[:10],
    }
    # persist raw
    (root() / "playlists" / f"{pl_id}.json").write_text(
        json.dumps({"urls": urls, "tracks": tracks, "raw": blob[:8000]}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    taste.setdefault("playlists", []).append(entry)
    taste["playlists"] = taste["playlists"][-50:]
    for t in tracks:
        taste.setdefault("tracks", []).append({**t, "at": _now(), "playlist_id": pl_id})
        if t.get("artist"):
            a = t["artist"].strip()
            taste.setdefault("artists", {})[a] = int(taste["artists"].get(a) or 0) + 1
    taste["tracks"] = taste["tracks"][-500:]
    genres = _guess_genres(
        [t.get("artist") or "" for t in tracks],
        [t.get("title") or "" for t in tracks],
    )
    for g in genres:
        if g == "unknown":
            continue
        taste.setdefault("genres", {})[g] = int(taste["genres"].get(g) or 0) + 1
    artists_c = Counter(taste.get("artists") or {})
    notes = _instrument_notes_heuristic(artists_c, genres)
    # optional LLM enrichment
    try:
        if tracks:
            raw = await chat_local(
                [
                    {
                        "role": "system",
                        "content": "Return JSON only: {\"genres\":[],\"instruments_notes\":\"\",\"similar_vibe\":\"\"}",
                    },
                    {
                        "role": "user",
                        "content": "Playlist sample:\n"
                        + "\n".join(f"{t.get('artist')} - {t.get('title')}" for t in tracks[:30]),
                    },
                ],
                temperature=0.2,
            )
            parsed = parse_json_loose(raw) or {}
            if isinstance(parsed.get("genres"), list):
                for g in parsed["genres"][:8]:
                    g = str(g).strip().lower()
                    if g:
                        taste.setdefault("genres", {})[g] = int(taste["genres"].get(g) or 0) + 1
            if parsed.get("instruments_notes"):
                notes.append(str(parsed["instruments_notes"])[:240])
    except Exception:
        pass
    taste.setdefault("notes", []).extend(notes)
    taste["notes"] = taste["notes"][-40:]
    save_taste(taste)
    try:
        knowledge.append_facts(
            "Music Taste",
            [f"Ingested playlist {pl_id}: {len(tracks)} tracks, urls={len(urls)}. " + " ".join(notes[:2])],
        )
    except Exception:
        pass
    memory.remember_fact(f"music playlist {pl_id}: {len(tracks)} tracks", tags=["music", "taste"])
    speak_bits = [f"Saved playlist ({len(tracks)} tracks"]
    if urls:
        speak_bits[0] += f", {len(urls)} link(s)"
    speak_bits[0] += ")."
    if notes:
        speak_bits.append(notes[0])
    if not tracks and urls:
        speak_bits.append("Link stored — paste the track list too if you want artist/genre analysis.")
    return {
        "ok": True,
        "playlist_id": pl_id,
        "urls": urls,
        "tracks": tracks[:40],
        "genres": genres,
        "notes": notes,
        "speak": " ".join(speak_bits),
    }


async def analyze_taste(message: str = "") -> dict[str, Any]:
    taste = load_taste()
    artists = Counter(taste.get("artists") or {})
    genres = Counter(taste.get("genres") or {})
    if not artists and not taste.get("tracks"):
        return {
            "ok": True,
            "empty": True,
            "speak": "No music taste yet — paste a playlist link or 'Artist - Song' list and say my playlist.",
            "hint": "Store under data/music_taste/",
        }
    top_a = artists.most_common(8)
    top_g = genres.most_common(6)
    notes = list(taste.get("notes") or [])[-5:]
    if not notes:
        notes = _instrument_notes_heuristic(artists, [g for g, _ in top_g] or ["unknown"])
    speak = (
        "Your taste: "
        + (", ".join(f"{a}" for a, _ in top_a[:4]) or "mixed artists")
        + (f". Genres: {', '.join(g for g, _ in top_g[:4])}." if top_g else ".")
        + (" " + notes[-1] if notes else "")
    )
    return {
        "ok": True,
        "artists": [{"name": a, "count": n} for a, n in top_a],
        "genres": [{"name": g, "count": n} for g, n in top_g],
        "notes": notes,
        "track_count": len(taste.get("tracks") or []),
        "speak": speak,
    }


async def suggest_songs(message: str = "") -> dict[str, Any]:
    taste = load_taste()
    artists = Counter(taste.get("artists") or {})
    top = [a for a, _ in artists.most_common(3)]
    genres = [g for g, _ in Counter(taste.get("genres") or {}).most_common(3)]
    seed = top[0] if top else ""
    query_bits = []
    if seed:
        query_bits.append(f"songs similar to {seed}")
        query_bits.append(f"popular {seed} tracks")
    if genres:
        query_bits.append(f"best {genres[0]} songs recommendations")
    if not query_bits:
        query_bits.append((message or "similar songs recommendations").strip() or "indie song recommendations")
    query = " · ".join(query_bits)
    research: dict[str, Any] = {}
    try:
        from voxoryl.research import deep_research

        research = await deep_research(query, conclude=True)
    except Exception as exc:
        research = {"ok": False, "error": str(exc), "hint": "Research tool unavailable — local heuristic only."}
    # same-artist stub suggestions from local vault
    local_same: list[str] = []
    for t in taste.get("tracks") or []:
        if seed and (t.get("artist") or "").lower() == seed.lower():
            local_same.append(f"{t.get('artist')} - {t.get('title')}")
    local_same = list(dict.fromkeys(local_same))[:8]
    abstract = str(research.get("speak") or research.get("abstract") or research.get("conclusion") or "")[:500]
    speak = abstract or (
        f"Based on {seed or 'your playlists'}: try more from the same artist"
        + (f" ({', '.join(local_same[:3])})" if local_same else "")
        + ". Paste more tracks for sharper suggestions."
    )
    if not research.get("ok") and research.get("hint"):
        speak += f" ({research.get('hint')})"
    return {
        "ok": True,
        "query": query,
        "seed_artists": top,
        "genres": genres,
        "same_artist_local": local_same,
        "research": {
            "ok": bool(research.get("ok")),
            "speak": abstract,
            "sources": research.get("sources") or research.get("results") or [],
        },
        "speak": speak[:700],
    }


async def tool_music(
    action: str = "summary",
    *,
    message: str = "",
    text: str = "",
) -> dict[str, Any]:
    action = (action or "summary").lower().strip()
    lower = (message or "").lower()
    if action in {"ingest", "playlist", "add"} or any(
        k in lower for k in ("my playlist", "ingest playlist", "save playlist", "spotify.com", "music.youtube")
    ):
        if action == "summary" and any(k in lower for k in ("suggest", "similar", "recommend")):
            return await suggest_songs(message)
        return await ingest_playlist(message, text=text or message)
    if action in {"suggest", "recommend", "similar"} or any(
        k in lower for k in ("suggest songs", "similar songs", "recommend songs", "song suggestions")
    ):
        return await suggest_songs(message)
    if action in {"analyze", "summary", "taste", "status"} or "music taste" in lower:
        return await analyze_taste(message)
    return await analyze_taste(message)
