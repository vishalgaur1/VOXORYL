"""ResourceManager — GPU/CPU/ASR/vision/TTS arbitration + graceful degrade."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResourceManager:
    vision_loaded: bool = False
    main_loaded: bool = False
    fast_loaded: bool = False
    mode: str = "normal"  # greeting | normal | computer | idle
    _log: list[dict[str, Any]] = field(default_factory=list)

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        if mode == "greeting":
            self.vision_loaded = False
        if mode == "idle":
            self.vision_loaded = False

    def acquire(self, resource: str) -> dict[str, Any]:
        if resource == "vision":
            self.mode = "computer"
            self.vision_loaded = True
            self._log.append({"acquire": "vision"})
            return {"ok": True, "resource": "vision"}
        if resource == "main":
            self.main_loaded = True
            return {"ok": True, "resource": "main"}
        if resource == "fast":
            self.fast_loaded = True
            return {"ok": True, "resource": "fast"}
        return {"ok": False, "error": f"unknown:{resource}"}

    def release(self, resource: str) -> None:
        if resource == "vision":
            self.vision_loaded = False
        self._log.append({"release": resource})

    def degrade(self, path: str) -> list[str]:
        """Return fallback chain for a capability class."""
        chains = {
            "vision": ["uia", "keyboard", "ask_user", "vision"],
            "browser": ["recipe", "playwright", "cdp", "vision"],
            "llm": ["main", "cloud", "fast", "deterministic"],
        }
        return chains.get(path, ["ask_user"])

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "vision_loaded": self.vision_loaded,
            "main_loaded": self.main_loaded,
            "fast_loaded": self.fast_loaded,
            "recent": self._log[-20:],
        }


_RM: ResourceManager | None = None


def get_resources() -> ResourceManager:
    global _RM
    if _RM is None:
        _RM = ResourceManager()
    return _RM
