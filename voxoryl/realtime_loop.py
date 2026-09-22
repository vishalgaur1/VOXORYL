"""RealtimeLoop — never waits on LLM/VL/research; never blocking I/O."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from voxoryl.event_bus import get_bus
from voxoryl.l0r import execute_l0r, match_l0r
from voxoryl.state_store import AudioPhase, WakeState, get_state


class RealtimeLoop:
    """
    Owns: mic events, VAD signals, ASR partials, interruption, TTS cancel hooks,
    cheap screen/window events, emergency L0-R.

    MUST NOT: call LLM, VL, research, large DB, network connectors, or model load.
    """

    def __init__(self) -> None:
        self._running = False
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="voxoryl-realtime-loop")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def push(self, event: dict[str, Any]) -> None:
        """Non-blocking enqueue from audio/UI threads."""
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

    async def on_asr_partial(self, text: str, *, confidence: float = 0.5) -> dict[str, Any] | None:
        state = get_state()
        # Wake phrase can re-arm even from DISABLED (user said Hey Voxy)
        from voxoryl.speech_normalizer import has_wake_phrase

        wake_hit = has_wake_phrase(text)
        if state.wake == WakeState.DISABLED and not wake_hit:
            return None
        if wake_hit and state.wake != WakeState.AWARE:
            state.set_wake(WakeState.AWARE)
        state.set_audio(
            phase=AudioPhase.SPEECH_DETECTED.value,
            unstable_suffix=text,
            asr_confidence=confidence,
        )
        cmd = match_l0r(text, partial=True)
        if cmd:
            return await execute_l0r(cmd)
        await get_bus().emit("realtime", "speech.partial", {"text": text, "confidence": confidence, "wake": wake_hit})
        return None

    async def on_asr_final(
        self,
        raw: str,
        normalized: str,
        *,
        asr_confidence: float = 1.0,
        endpoint_confidence: float = 1.0,
        command_text: str | None = None,
    ) -> dict[str, Any] | None:
        from voxoryl.speech_normalizer import has_wake_phrase, normalize_transcript, wake_only

        state = get_state()
        # Prefer structured normalizer when caller didn't strip wake
        norm = normalize_transcript(raw or normalized)
        agent_text = normalized or norm["normalized_text"] or raw
        cmd_text = (command_text if command_text is not None else norm.get("command_text")) or agent_text
        wake_hit = bool(norm.get("wake_detected")) or has_wake_phrase(agent_text) or has_wake_phrase(raw)
        if wake_hit:
            state.set_wake(WakeState.AWARE)
            state.set_audio(phase=AudioPhase.LISTENING.value)

        state.set_audio(
            phase=AudioPhase.TRANSCRIBING.value,
            raw_text=raw,
            normalized_text=agent_text,
            stable_prefix=agent_text,
            unstable_suffix="",
            asr_confidence=asr_confidence,
            endpoint_confidence=endpoint_confidence,
            speech_timeline={
                **(state.snapshot()["audio"].get("speech_timeline") or {}),
                "speech_ended_at": time.time(),
            },
        )
        # Wake-only → enter Listening, do not forward empty command to agent
        if wake_only(agent_text) or wake_only(raw):
            return await execute_l0r("WAKE")

        # L0-R matches on command_text (wake prefix stripped); agent gets full normalized.
        cmd = match_l0r(str(cmd_text), partial=False)
        if cmd:
            return await execute_l0r(cmd)
        await get_bus().emit(
            "realtime",
            "transcript.ready",
            {
                "raw": raw,
                "normalized": agent_text,
                "command_text": cmd_text,
                "asr_confidence": asr_confidence,
                "wake_detected": wake_hit,
            },
        )
        return None

    async def on_barge_in(self) -> dict[str, Any]:
        from voxoryl.cancellation import Scope, get_cancellation

        get_cancellation().cancel(Scope.TTS, reason="barge_in")
        get_state().set_audio(phase=AudioPhase.BARGE_IN.value)
        await get_bus().emit("realtime", "barge_in", {})
        return {"ok": True, "barge_in": True}

    async def _run(self) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=0.05)
            except asyncio.TimeoutError:
                # Fast clock: cheap window tick only — never VL / never blocking I/O
                try:
                    from voxoryl.perception import get_perception

                    await get_perception().tick_fast()
                except Exception:
                    pass
                continue
            except asyncio.CancelledError:
                break
            kind = event.get("kind")
            try:
                if kind == "partial":
                    await self.on_asr_partial(str(event.get("text") or ""), confidence=float(event.get("confidence") or 0.5))
                elif kind == "final":
                    await self.on_asr_final(
                        str(event.get("raw") or ""),
                        str(event.get("normalized") or event.get("raw") or ""),
                        asr_confidence=float(event.get("asr_confidence") or 1.0),
                        endpoint_confidence=float(event.get("endpoint_confidence") or 1.0),
                    )
                elif kind == "barge_in":
                    await self.on_barge_in()
                elif kind == "vad":
                    from voxoryl.audio_runtime import get_audio_runtime

                    pcm = event.get("pcm") or b""
                    if isinstance(pcm, (bytes, bytearray)):
                        get_audio_runtime().feed_vad(bytes(pcm))
                elif kind == "window":
                    # Cheap WorldState update only — no VL
                    title = event.get("title")
                    app = event.get("app")
                    st = get_state()
                    if title is not None:
                        st.set_world("active_window", title, confidence=0.95, source="win32")
                    if app is not None:
                        st.set_world("active_app", app, confidence=0.95, source="win32")
                    await get_bus().emit("perception", "window.changed", event)
            except Exception:
                pass


_RT: RealtimeLoop | None = None


def get_realtime() -> RealtimeLoop:
    global _RT
    if _RT is None:
        _RT = RealtimeLoop()
    return _RT
