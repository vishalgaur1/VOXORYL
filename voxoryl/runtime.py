"""TaskEngine — plan → execute → verify → replan with TaskBudget."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from voxoryl.capabilities import get_registry
from voxoryl.cancellation import get_cancellation
from voxoryl.event_bus import get_bus
from voxoryl.state_store import TaskPhase, get_state


@dataclass
class TaskBudget:
    max_ms: int = 30_000
    max_llm_calls: int = 4
    max_vl_calls: int = 1
    max_tool_calls: int = 12
    max_replans: int = 2
    resource_cost: int = 30

    llm_calls: int = 0
    vl_calls: int = 0
    tool_calls: int = 0
    replans: int = 0
    started: float = field(default_factory=time.perf_counter)

    def expired(self) -> bool:
        return (time.perf_counter() - self.started) * 1000 > self.max_ms

    def allow_tool(self) -> bool:
        return self.tool_calls < self.max_tool_calls and not self.expired()

    def allow_replan(self) -> bool:
        return self.replans < self.max_replans and not self.expired()

    def bump_tool(self) -> None:
        self.tool_calls += 1

    def bump_replan(self) -> None:
        self.replans += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_ms": self.max_ms,
            "max_llm_calls": self.max_llm_calls,
            "max_vl_calls": self.max_vl_calls,
            "max_tool_calls": self.max_tool_calls,
            "max_replans": self.max_replans,
            "resource_cost": self.resource_cost,
            "llm_calls": self.llm_calls,
            "vl_calls": self.vl_calls,
            "tool_calls": self.tool_calls,
            "replans": self.replans,
            "elapsed_ms": int((time.perf_counter() - self.started) * 1000),
        }


class TaskEngine:
    async def run(
        self,
        goal: str,
        steps: list[dict[str, Any]],
        *,
        success_condition: str | None = None,
        budget: TaskBudget | None = None,
        message: str = "",
    ) -> dict[str, Any]:
        budget = budget or TaskBudget()
        task_id = str(uuid.uuid4())[:8]
        state = get_state()
        bus = get_bus()
        reg = get_registry()
        results: list[Any] = []

        state.set_task(phase=TaskPhase.EXECUTING.value, task_id=task_id, goal=goal, budget=budget.as_dict())
        await bus.emit("agent", "task.started", {"task_id": task_id, "goal": goal})

        for step in steps:
            get_cancellation().check()
            if not budget.allow_tool():
                state.set_task(phase=TaskPhase.FAILED.value)
                return {
                    "ok": False,
                    "task_id": task_id,
                    "error": "budget_exhausted",
                    "budget": budget.as_dict(),
                    "results": results,
                }
            name = str(step.get("tool") or step.get("capability") or "")
            args = dict(step.get("args") or {})
            if message and "message" not in args:
                args["message"] = message
            await bus.emit("agent", "tool.started", {"task_id": task_id, "tool": name})
            state.set_task(phase=TaskPhase.EXECUTING.value)
            budget.bump_tool()
            out = await reg.invoke(name, args)
            results.append({"tool": name, "result": out})
            await bus.emit("agent", "tool.completed", {"task_id": task_id, "tool": name, "ok": bool((out or {}).get("ok", True))})

            # V0: execution result
            state.set_task(phase=TaskPhase.VERIFYING.value)
            await bus.emit("agent", "verification.started", {"task_id": task_id, "level": "V0"})
            if isinstance(out, dict) and out.get("ok") is False:
                if budget.allow_replan():
                    budget.bump_replan()
                    state.set_task(phase=TaskPhase.REPLANNING.value)
                    await bus.emit("agent", "verification.failed", {"task_id": task_id, "level": "V0"})
                    continue
                state.set_task(phase=TaskPhase.FAILED.value)
                return {"ok": False, "task_id": task_id, "results": results, "budget": budget.as_dict()}

        # Optional V1 structured condition (URL/window/process) — never VL
        verify: dict[str, Any] | None = None
        if success_condition:
            from voxoryl.wait_for import wait_for_condition

            state.set_task(phase=TaskPhase.VERIFYING.value)
            v1 = await wait_for_condition(success_condition, timeout_ms=3000)
            verify = {"V1": v1}
            if not v1.get("ok"):
                await bus.emit("agent", "verification.failed", {"task_id": task_id, "level": "V1"})
                # V2: VL only if budget allows AND structured state insufficient
                if budget.vl_calls < budget.max_vl_calls:
                    try:
                        from voxoryl.perception import get_perception

                        if get_perception().should_invoke_vl():
                            from voxoryl.screen import tool_screen

                            budget.vl_calls += 1
                            v2 = await tool_screen(action="look", goal=f"Verify: {success_condition}")
                            verify["V2"] = v2
                            await bus.emit("agent", "verification.started", {"task_id": task_id, "level": "V2"})
                            if v2.get("ok"):
                                state.set_task(phase=TaskPhase.COMPLETE.value)
                                await bus.emit("agent", "task.completed", {"task_id": task_id, "verify": verify})
                                return {
                                    "ok": True,
                                    "task_id": task_id,
                                    "results": results,
                                    "verify": verify,
                                    "budget": budget.as_dict(),
                                }
                    except Exception as exc:
                        verify["V2"] = {"ok": False, "error": str(exc)}
                state.set_task(phase=TaskPhase.FAILED.value)
                return {"ok": False, "task_id": task_id, "verify": verify, "results": results, "budget": budget.as_dict()}

        state.set_task(phase=TaskPhase.COMPLETE.value)
        await bus.emit("agent", "task.completed", {"task_id": task_id})
        return {"ok": True, "task_id": task_id, "results": results, "verify": verify, "budget": budget.as_dict()}


_ENGINE: TaskEngine | None = None


def get_task_engine() -> TaskEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = TaskEngine()
    return _ENGINE
