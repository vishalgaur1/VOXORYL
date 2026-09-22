"""L0-R realtime deterministic commands — bypass AgentLoop entirely."""

from __future__ import annotations

import re
from typing import Any

# Partial ASR may truncate; match early prefixes too.
# Wake brand: Voxy / Voxoryl only.
_L0_PREFIX = r"(?:(?:hey\s+)?(?:voxy|voxoryl)[,:]?\s+|ok(?:ay)?\s+(?:voxy|voxoryl)[,:]?\s+)?"

L0R_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("STOP", re.compile(rf"^\s*{_L0_PREFIX}(stop|halt|enough)\b", re.I)),
    ("CANCEL", re.compile(rf"^\s*{_L0_PREFIX}(cancel|abort|never\s*mind|nevermind)\b", re.I)),
    ("PAUSE", re.compile(rf"^\s*{_L0_PREFIX}(pause|wait|hold\s*on)\b", re.I)),
    ("RESUME", re.compile(rf"^\s*{_L0_PREFIX}(resume|continue|go\s*on)\b", re.I)),
    ("QUIET", re.compile(rf"^\s*{_L0_PREFIX}(quiet|shh|silence|mute)\b", re.I)),
    ("LISTEN", re.compile(rf"^\s*{_L0_PREFIX}(listen|hear\s*me)\b", re.I)),
    ("SLEEP", re.compile(rf"^\s*{_L0_PREFIX}(sleep|go\s*to\s*sleep|disable)\b", re.I)),
    (
        "WAKE",
        re.compile(
            r"^\s*(?:hey\s+(?:voxy|voxoryl)|ok(?:ay)?\s+(?:voxy|voxoryl))\b|"
            r"^\s*(?:(?:voxy|voxoryl)[,:]?\s+)?(wake|wake\s*up)\b",
            re.I,
        ),
    ),
]


def match_l0r(text: str, *, partial: bool = False) -> str | None:
    """Return command id or None. Works on partial transcripts for STOP/CANCEL/WAKE."""
    t = (text or "").strip()
    if not t:
        return None
    if partial:
        # Early wake: "hey voxy" / "hey vox"
        if re.search(r"\bhey\s+(?:voxy|voxoryl)\b", t, re.I) or re.match(r"^\s*hey\s+vox", t, re.I):
            rest = re.sub(
                r"^(?:hey\s+(?:voxy|voxoryl)|ok(?:ay)?\s+(?:voxy|voxoryl))\b[,:]?\s*",
                "",
                t,
                flags=re.I,
            ).strip()
            if not rest or len(rest) < 3:
                return "WAKE"
        # Early STOP
        if re.search(r"\b(st|sto|stop|can|canc|cancel)\b", t, re.I) or re.match(
            rf"^\s*{_L0_PREFIX}(st|sto|stop)", t, re.I
        ):
            if re.search(r"\b(stop|sto|st)\b", t, re.I) or re.match(
                rf"^\s*{_L0_PREFIX}(st|sto|stop)", t, re.I
            ):
                if re.search(r"\b(stop|halt)\b", t, re.I) or re.match(
                    rf"^\s*{_L0_PREFIX}(stop|sto)\b", t, re.I
                ):
                    return "STOP"
                if len(t) <= 16 and re.search(r"\bsto?\b", t, re.I):
                    return "STOP"
        if re.search(r"\b(cancel|abort)\b", t, re.I):
            return "CANCEL"
    for cmd, pat in L0R_PATTERNS:
        if pat.search(t):
            # WAKE with trailing command → let agent handle (don't swallow)
            if cmd == "WAKE" and not partial:
                from voxoryl.speech_normalizer import wake_only

                if not wake_only(t):
                    continue
            return cmd
    return None


async def execute_l0r(cmd: str) -> dict[str, Any]:
    """Zero LLM / zero memory / zero VL."""
    from voxoryl.cancellation import Scope, get_cancellation
    from voxoryl.event_bus import get_bus
    from voxoryl.state_store import AudioPhase, WakeState, get_state

    cm = get_cancellation()
    state = get_state()
    bus = get_bus()

    if cmd in {"STOP", "CANCEL"}:
        cm.cancel(Scope.TASK, reason=cmd)
        cm.cancel(Scope.TTS, reason=cmd)
        cm.cancel(Scope.TOOL, reason=cmd)
        cm.cancel(Scope.VISION, reason=cmd)
        state.set_task(phase="interrupted", goal=None)
        await bus.emit("realtime", "l0r", {"cmd": cmd})
        # Clear flags so the *next* user turn can run; in-flight work already saw cancel.
        cm.reset()
        return {"ok": True, "mode": "l0r", "cmd": cmd, "speak": "Stopped." if cmd == "STOP" else "Cancelled."}

    if cmd == "PAUSE":
        cm.cancel(Scope.TASK, reason="PAUSE")
        state.set_task(phase="paused")
        await bus.emit("realtime", "l0r", {"cmd": cmd})
        return {"ok": True, "mode": "l0r", "cmd": cmd, "speak": "Paused."}

    if cmd == "RESUME":
        cm.reset(Scope.TASK)
        cm.reset(Scope.GLOBAL)
        state.set_task(phase="idle")
        await bus.emit("realtime", "l0r", {"cmd": cmd})
        return {"ok": True, "mode": "l0r", "cmd": cmd, "speak": "Resuming."}

    if cmd == "QUIET":
        cm.cancel(Scope.TTS, reason="QUIET")
        await bus.emit("realtime", "l0r", {"cmd": cmd})
        return {"ok": True, "mode": "l0r", "cmd": cmd, "speak": ""}

    if cmd == "LISTEN":
        state.set_wake(WakeState.AWARE)
        state.set_audio(phase=AudioPhase.LISTENING.value)
        cm.reset()
        await bus.emit("realtime", "l0r", {"cmd": cmd})
        return {"ok": True, "mode": "l0r", "cmd": cmd, "speak": "Listening.", "ui": "listening"}

    if cmd == "SLEEP":
        state.set_wake(WakeState.DISABLED)
        cm.cancel(Scope.TTS, reason="SLEEP")
        await bus.emit("realtime", "l0r", {"cmd": cmd})
        return {"ok": True, "mode": "l0r", "cmd": cmd, "speak": "Going quiet."}

    if cmd == "WAKE":
        state.set_wake(WakeState.AWARE)
        state.set_audio(phase=AudioPhase.LISTENING.value)
        cm.reset()
        await bus.emit("realtime", "l0r", {"cmd": cmd, "wake_phrase": "hey voxy"})
        return {"ok": True, "mode": "l0r", "cmd": cmd, "speak": "Listening.", "ui": "listening"}

    return {"ok": False, "mode": "l0r", "cmd": cmd, "speak": "Unknown realtime command."}
