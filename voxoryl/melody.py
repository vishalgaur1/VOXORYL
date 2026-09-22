from __future__ import annotations

"""
Hum / melody → pitch notes + BPM → MIDI → optional DAW place (FL Studio etc.).
Shazam-for-humming lite: identify by lyrics search + local pitch track (not commercial fingerprinting).
"""

import json
import math
import struct
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voxoryl.config import settings


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _melody_dir() -> Path:
    path = settings.voxoryl_data_dir / "melodies"
    path.mkdir(parents=True, exist_ok=True)
    return path


def hz_to_midi(hz: float) -> int | None:
    if hz is None or hz <= 0:
        return None
    midi = int(round(69 + 12 * math.log2(hz / 440.0)))
    if midi < 21 or midi > 108:
        return None
    return midi


def midi_to_name(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def _read_wav_mono(path: Path) -> tuple[list[float], int]:
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        rate = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)
    if sampwidth == 1:
        fmt = f"{nframes * channels}B"
        data = struct.unpack(fmt, raw)
        samples = [(x - 128) / 128.0 for x in data]
    elif sampwidth == 2:
        fmt = f"{nframes * channels}h"
        data = struct.unpack(fmt, raw)
        samples = [x / 32768.0 for x in data]
    else:
        raise ValueError(f"unsupported sample width {sampwidth}")
    if channels > 1:
        mono = []
        for i in range(0, len(samples), channels):
            chunk = samples[i : i + channels]
            mono.append(sum(chunk) / len(chunk))
        samples = mono
    return samples, rate


def _yin_pitch(frame: list[float], rate: int, fmin: float = 80.0, fmax: float = 1000.0) -> float | None:
    """Lightweight YIN-ish pitch estimate for one frame."""
    n = len(frame)
    if n < 32:
        return None
    # difference function
    tau_min = max(2, int(rate / fmax))
    tau_max = min(n // 2, int(rate / fmin))
    if tau_max <= tau_min:
        return None
    diffs = []
    for tau in range(tau_min, tau_max):
        s = 0.0
        for i in range(n - tau):
            d = frame[i] - frame[i + tau]
            s += d * d
        diffs.append(s)
    # cumulative mean normalized difference
    cmnd = []
    running = 0.0
    for i, d in enumerate(diffs):
        running += d
        cmnd.append(1.0 if i == 0 else d * (i + 1) / running if running else 1.0)
    # absolute threshold
    thresh = 0.15
    best = None
    for i, v in enumerate(cmnd):
        if v < thresh:
            # parabolic-ish refine: pick local min
            j = i
            while j + 1 < len(cmnd) and cmnd[j + 1] < cmnd[j]:
                j += 1
            best = j
            break
    if best is None:
        # fallback: global min
        best = min(range(len(cmnd)), key=lambda i: cmnd[i])
        if cmnd[best] > 0.4:
            return None
    tau = best + tau_min
    return rate / tau if tau else None


def analyze_wav(path: Path) -> dict[str, Any]:
    samples, rate = _read_wav_mono(path)
    if len(samples) < rate // 10:
        return {"ok": False, "error": "audio too short — hum at least ~1s"}

    win = int(rate * 0.046)
    hop = int(rate * 0.023)
    pitches: list[tuple[float, float]] = []  # time_s, hz
    for start in range(0, len(samples) - win, hop):
        frame = samples[start : start + win]
        # RMS gate — ignore silence
        rms = math.sqrt(sum(x * x for x in frame) / len(frame))
        if rms < 0.02:
            continue
        hz = _yin_pitch(frame, rate)
        if hz:
            pitches.append((start / rate, hz))

    if len(pitches) < 3:
        return {"ok": False, "error": "could not lock pitch — hum louder / clearer"}

    # Quantize to note events (merge stable pitch)
    events: list[dict[str, Any]] = []
    cur_midi = None
    cur_start = pitches[0][0]
    last_t = pitches[0][0]
    for t, hz in pitches:
        m = hz_to_midi(hz)
        if m is None:
            continue
        if cur_midi is None:
            cur_midi, cur_start, last_t = m, t, t
            continue
        if m == cur_midi and (t - last_t) < 0.35:
            last_t = t
            continue
        # close previous
        dur = max(0.08, last_t - cur_start + hop / rate)
        events.append(
            {
                "midi": cur_midi,
                "note": midi_to_name(cur_midi),
                "start": round(cur_start, 3),
                "duration": round(dur, 3),
            }
        )
        cur_midi, cur_start, last_t = m, t, t
    if cur_midi is not None:
        dur = max(0.08, last_t - cur_start + hop / rate)
        events.append(
            {
                "midi": cur_midi,
                "note": midi_to_name(cur_midi),
                "start": round(cur_start, 3),
                "duration": round(dur, 3),
            }
        )

    # BPM from inter-onset of note starts
    starts = [e["start"] for e in events]
    intervals = [starts[i + 1] - starts[i] for i in range(len(starts) - 1) if starts[i + 1] > starts[i]]
    bpm = 120
    if intervals:
        # median interval → assume that is a beat or half-beat
        intervals_sorted = sorted(intervals)
        med = intervals_sorted[len(intervals_sorted) // 2]
        if med > 0.05:
            cand = 60.0 / med
            # fold into musical range
            while cand < 70:
                cand *= 2
            while cand > 180:
                cand /= 2
            bpm = int(round(cand))

    contour = " -> ".join(e["note"] for e in events[:24])
    return {
        "ok": True,
        "path": str(path.resolve()),
        "sample_rate": rate,
        "bpm": bpm,
        "note_count": len(events),
        "notes": events,
        "contour": contour,
        "speak": f"Heard {len(events)} notes @ ~{bpm} BPM. Contour: {contour[:80]}",
    }


def write_midi(events: list[dict[str, Any]], bpm: int, out_path: Path, *, program: int = 25) -> Path:
    """Write a Type-0-ish single-track MIDI using mido if available, else raw bytes."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import mido
    except ImportError:
        return _write_midi_raw(events, bpm, out_path, program=program)

    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    tempo = mido.bpm2tempo(bpm)
    track.append(mido.MetaMessage("set_tempo", tempo=tempo))
    track.append(mido.Message("program_change", program=program, time=0))

    # Convert absolute seconds → ticks
    abs_msgs: list[tuple[float, Any]] = []
    for e in events:
        start = float(e["start"])
        dur = float(e["duration"])
        note = int(e["midi"])
        abs_msgs.append((start, mido.Message("note_on", note=note, velocity=80, time=0)))
        abs_msgs.append((start + dur, mido.Message("note_off", note=note, velocity=64, time=0)))
    abs_msgs.sort(key=lambda x: x[0])
    last = 0.0
    for t, msg in abs_msgs:
        delta_s = max(0.0, t - last)
        msg.time = int(round(delta_s * (480 * (bpm / 60.0))))
        track.append(msg)
        last = t
    mid.save(str(out_path))
    return out_path


def _write_midi_raw(events: list[dict[str, Any]], bpm: int, out_path: Path, *, program: int = 25) -> Path:
    # Minimal single-track SMF
    def vlq(n: int) -> bytes:
        out = bytearray([n & 0x7F])
        n >>= 7
        while n:
            out.insert(0, 0x80 | (n & 0x7F))
            n >>= 7
        return bytes(out)

    ticks_per_beat = 480
    us_per_beat = int(60_000_000 / max(bpm, 1))
    track = bytearray()
    # tempo meta
    track += b"\x00\xff\x51\x03" + us_per_beat.to_bytes(3, "big")
    track += b"\x00\xc0" + bytes([program & 0x7F])
    abs_events: list[tuple[float, bytes]] = []
    for e in events:
        start = float(e["start"])
        dur = float(e["duration"])
        note = int(e["midi"]) & 0x7F
        abs_events.append((start, bytes([0x90, note, 80])))
        abs_events.append((start + dur, bytes([0x80, note, 64])))
    abs_events.sort(key=lambda x: x[0])
    last_tick = 0
    for t, payload in abs_events:
        tick = int(round(t * ticks_per_beat * (bpm / 60.0)))
        delta = max(0, tick - last_tick)
        track += vlq(delta) + payload
        last_tick = tick
    track += b"\x00\xff\x2f\x00"
    track_chunk = b"MTrk" + len(track).to_bytes(4, "big") + bytes(track)
    header = b"MThd" + (6).to_bytes(4, "big") + (0).to_bytes(2, "big") + (1).to_bytes(2, "big") + ticks_per_beat.to_bytes(2, "big")
    out_path.write_bytes(header + track_chunk)
    return out_path


# GM program hints for "mellow guitar" etc.
INSTRUMENT_PROGRAMS = {
    "nylon guitar": 24,
    "steel guitar": 25,
    "mellow guitar": 24,
    "jazz guitar": 26,
    "clean guitar": 27,
    "piano": 0,
    "epiano": 4,
    "pad": 89,
    "bass": 33,
    "synth lead": 80,
}


def pick_program(hint: str) -> tuple[int, str]:
    h = (hint or "").lower()
    for key, prog in INSTRUMENT_PROGRAMS.items():
        if key in h:
            return prog, key
    if "guitar" in h:
        return 24, "nylon guitar"
    if "mellow" in h or "soft" in h:
        return 24, "nylon guitar"
    return 25, "steel guitar"


async def find_song_from_lyrics_or_hum(query: str) -> dict[str, Any]:
    """Not true Shazam fingerprinting — lyric/description search for candidates."""
    from voxoryl.tools import tool_research

    q = query.strip()
    if not q:
        return {"ok": False, "error": "need lyrics or hum description"}
    research = await tool_research(f"song identify lyrics or melody: {q}")
    return {
        "ok": True,
        "query": q,
        "research": research,
        "speak": "Candidates from lyric/melody search — not audio fingerprint. Check top hits.",
        "note": "True humming-ID APIs (ACRCloud/AudD) can be wired later with a key.",
    }


async def tool_melody(
    action: str = "analyze",
    *,
    audio_path: str = "",
    message: str = "",
    instrument: str = "mellow guitar",
    place_in_daw: bool = False,
) -> dict[str, Any]:
    """
    actions:
      analyze — pitch + bpm from wav
      to_midi — analyze + write midi
      find_song — lyric/hum text search
      place — to_midi + screen/DAW assist
    """
    action = (action or "analyze").lower().strip()
    if action in {"find_song", "identify", "shazam"}:
        return await find_song_from_lyrics_or_hum(message or audio_path)

    path = Path(audio_path) if audio_path else None
    if not path or not path.exists():
        # latest upload in melodies/
        wavs = sorted(_melody_dir().glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
        path = wavs[0] if wavs else None
    if not path or not path.exists():
        return {
            "ok": False,
            "error": "no audio",
            "hint": "Record a hum in the widget (Hum button) or POST /api/melody/upload a .wav",
            "speak": "Need a hum recording first.",
        }

    analysis = analyze_wav(path)
    if not analysis.get("ok"):
        return analysis

    # Override BPM from message if user said it
    msg = (message or "").lower()
    import re

    m = re.search(r"(\d{2,3})\s*bpm", msg)
    if m:
        analysis["bpm"] = int(m.group(1))

    if action == "analyze":
        analysis["speak"] = (
            f"{analysis['note_count']} notes @ {analysis['bpm']} BPM. {analysis['contour'][:100]}"
        )
        return analysis

    program, inst_name = pick_program(instrument or message)
    out = _melody_dir() / f"hum_{_now_stamp()}.mid"
    midi_path = write_midi(analysis["notes"], int(analysis["bpm"]), out, program=program)
    # sidecar json
    meta = {
        "midi": str(midi_path.resolve()),
        "wav": str(path.resolve()),
        "bpm": analysis["bpm"],
        "instrument": inst_name,
        "program": program,
        "contour": analysis["contour"],
        "notes": analysis["notes"],
    }
    meta_path = midi_path.with_suffix(".json")
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    result: dict[str, Any] = {
        "ok": True,
        "action": action,
        "analysis": analysis,
        "midi": str(midi_path.resolve()),
        "meta": str(meta_path.resolve()),
        "instrument": inst_name,
        "program": program,
        "bpm": analysis["bpm"],
        "speak": f"MIDI ready @ {analysis['bpm']} BPM -> {inst_name}. File: {midi_path.name}",
    }

    if action in {"place", "daw", "fl"} or place_in_daw:
        from voxoryl.screen import computer_use_enabled, tool_screen

        if not computer_use_enabled():
            result["daw"] = {
                "ok": False,
                "hint": "Enable COMPUTER_USE_ENABLED and open FL Studio / your DAW, then retry.",
            }
            result["speak"] += " DAW place skipped — computer use off."
            return result
        goal = (
            f"In the open DAW (FL Studio or similar): import or drag this MIDI file into a new track: "
            f"{midi_path}. Set tempo to {analysis['bpm']} BPM. "
            f"Load or pick a mellow/soft {inst_name} sound on that track. "
            f"Do not delete other projects. Prefer File→Import or drag from Explorer if visible."
        )
        daw = await tool_screen(action="act", goal=goal)
        result["daw"] = daw
        result["speak"] = (
            f"MIDI @ {analysis['bpm']} BPM ({inst_name}). Tried placing in DAW — "
            f"{daw.get('speak') or 'check the screen'}."
        )
    return result


async def save_upload(data: bytes, filename: str = "hum.wav") -> Path:
    dest = _melody_dir() / f"upload_{_now_stamp()}_{Path(filename).name}"
    # If webm/ogg, try ffmpeg → wav
    suffix = Path(filename).suffix.lower()
    dest.write_bytes(data)
    if suffix in {".webm", ".ogg", ".mp3", ".m4a"}:
        wav = dest.with_suffix(".wav")
        import asyncio
        import shutil

        if shutil.which("ffmpeg"):
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-i", str(dest), str(wav),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            if wav.exists():
                return wav
    return dest
