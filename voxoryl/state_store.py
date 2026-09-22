"""Central state store: audio / world / task / wake. Events update state; memory is separate."""

from __future__ import annotations

import threading
import time
from copy import deepcopy
from enum import Enum
from typing import Any


class WakeState(str, Enum):
    AWARE = "aware"
    PAUSED = "paused"
    DISABLED = "disabled"


class AudioPhase(str, Enum):
    MIC_OFF = "mic_off"
    MIC_STARTING = "mic_starting"
    LISTENING = "listening"
    SPEECH_DETECTED = "speech_detected"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    BARGE_IN = "barge_in"
    ERROR = "error"


class TaskPhase(str, Enum):
    IDLE = "idle"
    UNDERSTANDING = "understanding"
    OBSERVING = "observing"
    PLANNING = "planning"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    REPLANNING = "replanning"
    WAITING_EXTERNAL = "waiting_external"
    INTERRUPTED = "interrupted"
    PAUSED = "paused"
    COMPLETE = "complete"
    FAILED = "failed"


def _field(value: Any, *, confidence: float = 1.0, source: str = "system") -> dict[str, Any]:
    return {"value": value, "confidence": confidence, "source": source, "ts": time.time()}


class StateStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._wake = WakeState.AWARE
        self._audio: dict[str, Any] = {
            "phase": AudioPhase.MIC_OFF.value,
            "raw_text": "",
            "normalized_text": "",
            "stable_prefix": "",
            "unstable_suffix": "",
            "asr_confidence": 0.0,
            "endpoint_confidence": 0.0,
            "normalization_confidence": 1.0,
            "speech_timeline": {},
        }
        self._world: dict[str, Any] = {
            "active_window": _field(None, confidence=0.0),
            "active_app": _field(None, confidence=0.0),
            "browser": _field({}, confidence=0.0),
            "ui": _field({}, confidence=0.0),
            "screen": _field({"watch_level": 0}, confidence=0.0),
            "attention": 0.0,
        }
        self._task: dict[str, Any] = {
            "phase": TaskPhase.IDLE.value,
            "task_id": None,
            "goal": None,
            "budget": {},
        }
        self._safe_mode = False

    @property
    def wake(self) -> WakeState:
        with self._lock:
            return self._wake

    def set_wake(self, state: WakeState | str) -> None:
        with self._lock:
            self._wake = WakeState(state)

    def set_audio(self, **kwargs: Any) -> None:
        with self._lock:
            self._audio.update(kwargs)

    def set_world(self, key: str, value: Any, *, confidence: float = 1.0, source: str = "system") -> None:
        with self._lock:
            self._world[key] = _field(value, confidence=confidence, source=source)

    def set_attention(self, score: float) -> None:
        with self._lock:
            self._world["attention"] = max(0.0, min(1.0, float(score)))

    def set_task(self, **kwargs: Any) -> None:
        with self._lock:
            self._task.update(kwargs)

    def set_safe_mode(self, on: bool) -> None:
        with self._lock:
            self._safe_mode = bool(on)

    @property
    def safe_mode(self) -> bool:
        with self._lock:
            return self._safe_mode

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "wake": self._wake.value,
                "safe_mode": self._safe_mode,
                "audio": deepcopy(self._audio),
                "world": deepcopy(self._world),
                "task": deepcopy(self._task),
            }


_STORE: StateStore | None = None


def get_state() -> StateStore:
    global _STORE
    if _STORE is None:
        _STORE = StateStore()
    return _STORE
