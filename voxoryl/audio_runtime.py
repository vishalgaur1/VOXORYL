"""Audio runtime abstractions — AEC/NS/AGC + VAD + streaming ASR fields (CPU).

Realtime path: never load models or block on I/O here — preprocess is pure CPU;
Silero ONNX is optional and loaded lazily off the hot path when available.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

from voxoryl.speech_normalizer import normalize_transcript
from voxoryl.state_store import AudioPhase, get_state


@dataclass
class AudioPreprocessConfig:
    noise_suppression: bool = True
    automatic_gain_control: bool = True
    echo_reference: bool = True  # AEC reference from TTS playback


@dataclass
class SpeechSegment:
    raw_text: str = ""
    normalized_text: str = ""
    stable_prefix: str = ""
    unstable_suffix: str = ""
    asr_confidence: float = 0.0
    endpoint_confidence: float = 0.0
    normalization_confidence: float = 1.0
    speech_started_at: float | None = None
    speech_ended_at: float | None = None
    heard_until: float | None = None
    interrupted_audio_ms: float | None = None


@dataclass
class VadResult:
    is_speech: bool
    probability: float
    source: str  # silero | energy | stub


class VadEngine:
    """
    P0: deterministic energy VAD + optional Silero ONNX when installed.
    Never blocks realtime on model download.
    """

    def __init__(self, *, energy_threshold: float = 0.02) -> None:
        self.energy_threshold = energy_threshold
        self._silero = None
        self._silero_tried = False

    def _try_silero(self) -> Any:
        if self._silero_tried:
            return self._silero
        self._silero_tried = True
        try:
            # Optional dependency — soft-fail if missing
            import torch  # type: ignore

            model, _utils = torch.hub.load(  # type: ignore[attr-defined]
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                trust_repo=True,
            )
            self._silero = model
        except Exception:
            self._silero = None
        return self._silero

    def score_pcm16(self, pcm: bytes, *, sample_rate: int = 16_000) -> VadResult:
        """Score a short PCM16 mono chunk. Pure CPU energy path by default."""
        if not pcm:
            return VadResult(False, 0.0, "stub")
        # Energy VAD (always available, realtime-safe)
        n = max(1, len(pcm) // 2)
        # Interpret little-endian int16 without numpy
        total = 0.0
        for i in range(0, len(pcm) - 1, 2):
            sample = int.from_bytes(pcm[i : i + 2], "little", signed=True) / 32768.0
            total += sample * sample
        rms = math.sqrt(total / n)
        energy_prob = min(1.0, rms / max(self.energy_threshold, 1e-6) * 0.5)
        is_speech = rms >= self.energy_threshold

        # Optional Silero — only if already warm; never load on first realtime tick
        if self._silero is not None:
            try:
                import torch  # type: ignore

                tensor = torch.frombuffer(bytearray(pcm), dtype=torch.int16).float() / 32768.0
                if sample_rate != 16_000:
                    # Keep P0 simple: energy fallback when rate differs
                    return VadResult(is_speech, energy_prob, "energy")
                prob = float(self._silero(tensor, sample_rate).item())
                return VadResult(prob >= 0.5, prob, "silero")
            except Exception:
                pass
        return VadResult(is_speech, energy_prob, "energy")

    def warm_silero(self) -> dict[str, Any]:
        """Explicit warm — call from agent/idle path, never realtime."""
        m = self._try_silero()
        return {"ok": m is not None, "backend": "silero" if m is not None else "energy_only"}


@dataclass
class AudioRuntime:
    """
    P0: abstractions + state machine hooks.
    Real PCM/Silero ONNX wiring attaches without changing AgentLoop.
    """

    preprocess: AudioPreprocessConfig = field(default_factory=AudioPreprocessConfig)
    vad: VadEngine = field(default_factory=VadEngine)
    min_utterance_ms: int = 280
    end_silence_ms: int = 900
    pre_roll_ms: int = 350
    max_utterance_ms: int = 20_000
    interruption_window_ms: int = 400
    # Keep early unstable partials until endpoint — avoid cutting leading words
    hold_unstable_partials: bool = True
    _speech_started_at: float | None = None
    _last_voice_at: float | None = None
    _utterance_ms: float = 0.0
    _pre_roll_pcm: bytearray = field(default_factory=bytearray)

    def apply_preprocess(self, pcm: bytes, *, tts_reference: bytes | None = None) -> bytes:
        """
        AEC / NS / AGC abstractions. P0: pass-through with bookkeeping.
        Real DSP can replace internals without changing callers.
        """
        out = pcm
        # Echo reference present → mark AEC engaged (no-op filter until DSP wired)
        if self.preprocess.echo_reference and tts_reference:
            pass  # placeholder for AEC subtraction
        if self.preprocess.noise_suppression:
            pass  # placeholder for NS
        if self.preprocess.automatic_gain_control and out:
            # Soft AGC: scale if peak is very low (deterministic, no I/O)
            peak = 1
            for i in range(0, len(out) - 1, 2):
                sample = abs(int.from_bytes(out[i : i + 2], "little", signed=True))
                if sample > peak:
                    peak = sample
            if peak < 4000 and peak > 0:
                gain = min(4.0, 8000.0 / peak)
                buf = bytearray()
                for i in range(0, len(out) - 1, 2):
                    sample = int.from_bytes(out[i : i + 2], "little", signed=True)
                    scaled = int(max(-32768, min(32767, sample * gain)))
                    buf.extend(int(scaled).to_bytes(2, "little", signed=True))
                out = bytes(buf)
        return out

    def begin_speech(self) -> None:
        now = time.time()
        self._speech_started_at = now
        self._last_voice_at = now
        self._utterance_ms = 0.0
        get_state().set_audio(
            phase=AudioPhase.SPEECH_DETECTED.value,
            speech_timeline={"speech_started_at": now},
        )

    def feed_vad(self, pcm: bytes, *, sample_rate: int = 16_000) -> VadResult:
        """Realtime-safe VAD tick; updates endpoint bookkeeping + ~pre_roll buffer."""
        processed = self.apply_preprocess(pcm)
        # Ring-ish pre-roll: keep ~pre_roll_ms of PCM before speech_started
        if self._speech_started_at is None and processed:
            self._pre_roll_pcm.extend(processed)
            # 16-bit mono ≈ 2 bytes/sample; bytes for pre_roll_ms
            max_bytes = int(sample_rate * 2 * (self.pre_roll_ms / 1000.0))
            if len(self._pre_roll_pcm) > max_bytes:
                self._pre_roll_pcm = bytearray(self._pre_roll_pcm[-max_bytes:])
        result = self.vad.score_pcm16(processed, sample_rate=sample_rate)
        now = time.time()
        if result.is_speech:
            if self._speech_started_at is None:
                self.begin_speech()
            self._last_voice_at = now
            if self._speech_started_at:
                self._utterance_ms = (now - self._speech_started_at) * 1000.0
            get_state().set_audio(phase=AudioPhase.SPEECH_DETECTED.value)
        return result

    def take_pre_roll(self) -> bytes:
        """Return and clear buffered lead-in audio (for ASR that supports PCM)."""
        out = bytes(self._pre_roll_pcm)
        self._pre_roll_pcm.clear()
        return out

    def endpoint_confidence(self) -> float:
        """
        Deterministic endpoint score from silence + utterance length.
        P1: semantic turn detector only when this is ambiguous (~0.4–0.7).
        """
        if self._speech_started_at is None or self._last_voice_at is None:
            return 0.0
        now = time.time()
        silence_ms = (now - self._last_voice_at) * 1000.0
        utterance_ms = (now - self._speech_started_at) * 1000.0
        if utterance_ms < self.min_utterance_ms:
            return 0.15
        if utterance_ms >= self.max_utterance_ms:
            return 0.99
        if silence_ms < self.end_silence_ms * 0.5:
            return 0.25
        if silence_ms >= self.end_silence_ms:
            return 0.95
        # Ambiguous band — P1 semantic detector may refine
        return 0.55

    def should_endpoint(self) -> bool:
        return self.endpoint_confidence() >= 0.85

    def update_partial(self, text: str, *, confidence: float = 0.5) -> SpeechSegment:
        # Keep full partial as stable when hold_unstable_partials — don't drop leading words
        parts = (text or "").strip().split()
        if self.hold_unstable_partials:
            stable = text or ""
            unstable = ""
        else:
            stable = " ".join(parts[:-1]) if len(parts) > 1 else ""
            unstable = parts[-1] if parts else text
        get_state().set_audio(
            phase=AudioPhase.SPEECH_DETECTED.value,
            stable_prefix=stable,
            unstable_suffix=unstable,
            asr_confidence=confidence,
            endpoint_confidence=self.endpoint_confidence(),
        )
        return SpeechSegment(
            raw_text=text,
            stable_prefix=stable,
            unstable_suffix=unstable,
            asr_confidence=confidence,
            endpoint_confidence=self.endpoint_confidence(),
            speech_started_at=self._speech_started_at,
        )

    def finalize(self, raw: str, *, asr_confidence: float = 1.0, endpoint_confidence: float | None = None) -> SpeechSegment:
        norm = normalize_transcript(raw)
        now = time.time()
        ep = float(endpoint_confidence if endpoint_confidence is not None else max(self.endpoint_confidence(), 0.9))
        seg = SpeechSegment(
            raw_text=norm["raw_text"],
            normalized_text=norm["normalized_text"],
            stable_prefix=norm["normalized_text"],
            unstable_suffix="",
            asr_confidence=asr_confidence,
            endpoint_confidence=ep,
            normalization_confidence=float(norm["normalization_confidence"]),
            speech_started_at=self._speech_started_at,
            speech_ended_at=now,
            heard_until=now,
        )
        get_state().set_audio(
            phase=AudioPhase.TRANSCRIBING.value,
            raw_text=seg.raw_text,
            normalized_text=seg.normalized_text,
            stable_prefix=seg.stable_prefix,
            asr_confidence=asr_confidence,
            endpoint_confidence=ep,
            normalization_confidence=seg.normalization_confidence,
            speech_timeline={
                **(get_state().snapshot()["audio"].get("speech_timeline") or {}),
                "speech_started_at": self._speech_started_at,
                "speech_ended_at": now,
                "heard_until": now,
            },
        )
        self._speech_started_at = None
        self._last_voice_at = None
        self._utterance_ms = 0.0
        return seg

    def mark_tts_started(self) -> None:
        get_state().set_audio(
            phase=AudioPhase.SPEAKING.value,
            speech_timeline={
                **(get_state().snapshot()["audio"].get("speech_timeline") or {}),
                "agent_audio_started": time.time(),
            },
        )

    def mark_tts_stopped(self, *, interrupted: bool = False) -> None:
        now = time.time()
        tl = dict(get_state().snapshot()["audio"].get("speech_timeline") or {})
        tl["agent_audio_stopped"] = now
        if interrupted:
            tl["user_interrupted_at"] = now
            started = float(tl.get("agent_audio_started") or now)
            tl["interrupted_audio_ms"] = max(0.0, (now - started) * 1000.0)
            tl["heard_until"] = now
        get_state().set_audio(phase=AudioPhase.LISTENING.value, speech_timeline=tl)

    def status(self) -> dict[str, Any]:
        return {
            "preprocess": {
                "noise_suppression": self.preprocess.noise_suppression,
                "automatic_gain_control": self.preprocess.automatic_gain_control,
                "echo_reference": self.preprocess.echo_reference,
            },
            "endpointing": {
                "min_utterance_ms": self.min_utterance_ms,
                "end_silence_ms": self.end_silence_ms,
                "pre_roll_ms": self.pre_roll_ms,
                "max_utterance_ms": self.max_utterance_ms,
                "endpoint_confidence": self.endpoint_confidence(),
                "should_endpoint": self.should_endpoint(),
            },
            "vad": {
                "backend": "silero" if self.vad._silero is not None else "energy",
                "silero_warmed": self.vad._silero_tried and self.vad._silero is not None,
            },
            "audio": get_state().snapshot()["audio"],
        }


_AUDIO: AudioRuntime | None = None


def get_audio_runtime() -> AudioRuntime:
    global _AUDIO
    if _AUDIO is None:
        _AUDIO = AudioRuntime()
    return _AUDIO
