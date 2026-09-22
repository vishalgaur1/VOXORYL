"""Supervisor — owns lifecycle of realtime, agent readiness, kill switch, health."""

from __future__ import annotations

import time
from typing import Any

from voxoryl.cancellation import get_cancellation
from voxoryl.event_bus import get_bus
from voxoryl.realtime_loop import get_realtime
from voxoryl.state_store import WakeState, get_state


class Supervisor:
    def __init__(self) -> None:
        self.started_at: float | None = None
        self._healthy = False

    async def start(self) -> dict[str, Any]:
        self.started_at = time.time()
        get_cancellation().reset()
        get_state().set_wake(WakeState.AWARE)
        await get_realtime().start()
        await get_bus().emit("platform", "supervisor.started", {})
        self._healthy = True
        return {"ok": True, "supervisor": "started"}

    async def stop(self) -> dict[str, Any]:
        get_cancellation().cancel(reason="supervisor_stop")
        await get_realtime().stop()
        get_state().set_wake(WakeState.DISABLED)
        await get_bus().emit("platform", "supervisor.stopped", {})
        self._healthy = False
        return {"ok": True, "supervisor": "stopped"}

    async def kill_switch(self) -> dict[str, Any]:
        """Immediate stop of perception + computer-use related work."""
        get_cancellation().cancel(reason="kill_switch")
        get_state().set_wake(WakeState.DISABLED)
        get_state().set_audio(phase="mic_off")
        await get_bus().emit("realtime", "kill", {})
        return {"ok": True, "kill_switch": True}

    async def pause(self) -> dict[str, Any]:
        get_state().set_wake(WakeState.PAUSED)
        return {"ok": True, "wake": "paused"}

    async def resume(self) -> dict[str, Any]:
        get_cancellation().reset()
        get_state().set_wake(WakeState.AWARE)
        if not get_realtime().running:
            await get_realtime().start()
        return {"ok": True, "wake": "aware"}

    def health(self) -> dict[str, Any]:
        st = get_state().snapshot()
        return {
            "ok": True,
            "started": self._healthy,
            "uptime_s": (time.time() - self.started_at) if self.started_at else 0,
            "wake": st["wake"],
            "safe_mode": st["safe_mode"],
            "task_phase": st["task"].get("phase"),
            "audio_phase": st["audio"].get("phase"),
            "realtime_running": get_realtime().running,
            "cancellation": get_cancellation().status(),
        }


_SUP: Supervisor | None = None


def get_supervisor() -> Supervisor:
    global _SUP
    if _SUP is None:
        _SUP = Supervisor()
    return _SUP
