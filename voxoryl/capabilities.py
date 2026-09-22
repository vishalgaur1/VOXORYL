"""Capability registry — single dispatcher surface for agent + council."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


Handler = Callable[..., Awaitable[Any] | Any]


@dataclass
class Capability:
    name: str
    description: str
    category: str
    handler: Handler
    risk: str = "low"  # low | medium | high
    latency: str = "low"  # ultra_low | low | medium | high
    tool_cost: int = 1
    local_only: bool = True
    requires_confirmation: bool = False
    requires_screen: bool = False
    requires_network: bool = False
    grounding: str = "native"  # recipe | uia | dom | playwright | vision | api
    parallelizable: bool = False
    reversible: bool = True
    timeout_ms: int = 30_000
    preconditions: list[str] = field(default_factory=list)
    postconditions: list[str] = field(default_factory=list)
    fallbacks: list[str] = field(default_factory=list)
    verifier: str | None = None
    schema: dict[str, Any] = field(default_factory=dict)
    examples: list[str] = field(default_factory=list)


@dataclass
class CapabilityScorer:
    speed_weight: float = 1.0
    reliability_weight: float = 1.0
    risk_weight: float = 1.0
    resource_weight: float = 1.0
    confidence_weight: float = 1.0

    def score(
        self,
        cap: Capability,
        *,
        confidence: float = 0.8,
        reliability: float = 0.9,
    ) -> float:
        risk_pen = {"low": 1.0, "medium": 2.0, "high": 5.0}.get(cap.risk, 2.0)
        lat_pen = {"ultra_low": 0.5, "low": 1.0, "medium": 3.0, "high": 10.0}.get(cap.latency, 2.0)
        resource = max(1, cap.tool_cost)
        numer = (
            self.confidence_weight * confidence
            * self.reliability_weight * reliability
            * (1.2 if cap.reversible else 0.8)
        )
        denom = (
            self.speed_weight * lat_pen
            * self.resource_weight * resource
            * self.risk_weight * risk_pen
        )
        return float(numer / max(denom, 0.01))


class CapabilityRegistry:
    def __init__(self) -> None:
        self._caps: dict[str, Capability] = {}
        self.scorer = CapabilityScorer()

    def register(self, cap: Capability) -> None:
        self._caps[cap.name] = cap
        # aliases without dots
        short = cap.name.split(".")[-1]
        if short not in self._caps:
            self._caps[short] = cap

    def get(self, name: str) -> Capability | None:
        return self._caps.get(name)

    def list(self, *, category: str | None = None) -> list[Capability]:
        seen: set[int] = set()
        out: list[Capability] = []
        for c in self._caps.values():
            i = id(c)
            if i in seen:
                continue
            seen.add(i)
            if category and c.category != category:
                continue
            out.append(c)
        return out

    def catalog_for_prompt(self) -> str:
        lines = []
        for c in self.list():
            lines.append(f"- {c.name}: {c.description} [cost={c.tool_cost}, grounding={c.grounding}]")
        return "\n".join(lines)

    async def invoke(self, name: str, args: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        from voxoryl.cancellation import get_cancellation
        from voxoryl.state_store import get_state

        if get_state().safe_mode and name.split(".")[0] in {
            "screen",
            "chrome",
            "computer",
            "shell",
            "email",
            "whatsapp",
        }:
            if name not in {"safety", "memory.read", "doctor"}:
                return {"ok": False, "error": "safe_mode_blocks_capability", "capability": name}

        get_cancellation().check()
        cap = self.get(name)
        if not cap:
            return {"ok": False, "error": f"unknown_capability:{name}"}
        args = dict(args or {})
        args.update(kwargs)
        t0 = time.perf_counter()
        try:
            result = cap.handler(**args) if args else cap.handler()
            if hasattr(result, "__await__"):
                result = await result  # type: ignore[misc]
        except Exception as exc:
            return {"ok": False, "error": str(exc), "capability": name}
        ms = int((time.perf_counter() - t0) * 1000)
        if isinstance(result, dict):
            result.setdefault("capability", name)
            result.setdefault("latency_ms", ms)
            return result
        return {"ok": True, "capability": name, "result": result, "latency_ms": ms}

    def rank(self, names: list[str], *, confidence: float = 0.8) -> list[tuple[str, float]]:
        scored = []
        for n in names:
            c = self.get(n)
            if not c:
                continue
            scored.append((n, self.scorer.score(c, confidence=confidence)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored


_REG: CapabilityRegistry | None = None
_BOOTSTRAPPED = False


def get_registry() -> CapabilityRegistry:
    global _REG, _BOOTSTRAPPED
    if _REG is None:
        _REG = CapabilityRegistry()
    if not _BOOTSTRAPPED:
        _bootstrap(_REG)
        _BOOTSTRAPPED = True
    return _REG


def _bootstrap(reg: CapabilityRegistry) -> None:
    """Register thin wrappers around existing tools."""

    async def _research(query: str = "", message: str = "", **_: Any) -> Any:
        from voxoryl.tools import tool_research

        return await tool_research(query or message)

    async def _chrome(action: str = "auto", message: str = "", **_: Any) -> Any:
        from voxoryl.chrome_control import tool_chrome

        return await tool_chrome(action=action, message=message)

    async def _screen(action: str = "look", goal: str = "", message: str = "", **kw: Any) -> Any:
        from voxoryl.screen import tool_screen

        g = goal or message
        if (action or "look") == "look":
            try:
                from voxoryl.vision_runtime import maybe_describe

                described = await maybe_describe(str(kw.get("question") or g))
                if described.get("ok") and not described.get("used_vl"):
                    return {
                        "ok": True,
                        "action": "look",
                        "used_vl": False,
                        "speak": described.get("speak_hint") or "Screen state ready.",
                        "world": described.get("world"),
                    }
                if described.get("used_vl") and described.get("result"):
                    return described["result"]
            except Exception:
                pass
        return await tool_screen(
            action=action,
            goal=g,
            question=str(kw.get("question") or ""),
            text=str(kw.get("text") or ""),
            rounds=int(kw.get("rounds") or 1),
        )

    async def _windows(message: str = "", **_: Any) -> Any:
        from voxoryl.windows_ops import tool_windows

        return await tool_windows(message=message)

    async def _github(action: str = "list", repo: str = "", path: str = "README.md", **_: Any) -> Any:
        from voxoryl.tools import tool_github

        return await tool_github(action=action, repo=repo, path=path)

    async def _email(limit: int = 8, **_: Any) -> Any:
        from voxoryl.tools import tool_email

        return await tool_email(limit=limit)

    async def _remember(text: str = "", message: str = "", kind: str = "fact", **_: Any) -> Any:
        from voxoryl.tools import tool_remember

        return await tool_remember(text or message, kind=kind)

    async def _computer(action: str = "list", path: str = "", query: str = "", **_: Any) -> Any:
        from voxoryl.tools import tool_computer

        return await tool_computer(action=action, path=path, query=query)

    async def _powertoys(action: str = "auto", message: str = "", query: str = "", **_: Any) -> Any:
        from voxoryl.powertoys_bridge import tool_powertoys

        return await tool_powertoys(action=action, message=message, query=query)

    async def _wait_for(condition: str = "", timeout_ms: int = 18000, **kw: Any) -> Any:
        from voxoryl.wait_for import wait_for_condition

        return await wait_for_condition(condition or str(kw.get("message") or ""), timeout_ms=timeout_ms)

    async def _browser(message: str = "", intent: str = "", **_: Any) -> Any:
        from voxoryl.browser_adapter import browser_action

        return await browser_action(intent or message, message=message)

    async def _screen_click(x: float = 0, y: float = 0, button: str = "left", **kw: Any) -> Any:
        from voxoryl.point_executor import click_xy_and_verify

        dry = bool(kw.get("dry_run"))
        verify = kw.get("verify")
        return await click_xy_and_verify(float(x), float(y), button=str(button or "left"), verify=verify, dry_run=dry)

    async def _screen_type(x: float = 0, y: float = 0, text: str = "", **kw: Any) -> Any:
        from voxoryl.point_executor import type_at_xy
        import asyncio

        return await asyncio.to_thread(
            type_at_xy, float(x), float(y), str(text or ""), enter=bool(kw.get("enter")), dry_run=bool(kw.get("dry_run"))
        )

    async def _screen_observe(query: str = "", message: str = "", **kw: Any) -> Any:
        from voxoryl.screen_grounding import observe_screen

        return await observe_screen(
            query=query or message,
            want_videos=bool(kw.get("want_videos")),
            index=kw.get("index"),
            allow_vl=kw.get("allow_vl", True),
            dry_run=bool(kw.get("dry_run")),
        )

    specs = [
        Capability("research", "Web research", "knowledge", _research, tool_cost=5, requires_network=True, grounding="api"),
        Capability("chrome", "Chrome/profile/tabs/navigate", "browser", _chrome, tool_cost=1, grounding="recipe", latency="ultra_low", postconditions=["browser_ready"]),
        Capability("chrome.open_profile", "Open Chrome profile", "browser", _chrome, tool_cost=1, grounding="recipe", latency="ultra_low"),
        Capability("browser.act", "Structured browser: recipe→ARIA→Playwright→vision", "browser", _browser, tool_cost=3, grounding="playwright"),
        Capability("screen", "Screen look/act", "computer", _screen, tool_cost=20, requires_screen=True, grounding="vision", latency="high"),
        Capability("screen.click", "Click absolute screen x,y", "computer", _screen_click, tool_cost=2, requires_screen=True, grounding="uia", latency="low", postconditions=["verified_optional"]),
        Capability("screen.type", "Click x,y then type text", "computer", _screen_type, tool_cost=2, requires_screen=True, grounding="uia", latency="low"),
        Capability("screen.observe", "Ground on-screen targets to absolute x,y", "computer", _screen_observe, tool_cost=5, requires_screen=True, grounding="uia", latency="medium"),
        Capability("windows", "Windows volume/brightness/apps", "computer", _windows, tool_cost=1, grounding="api", latency="ultra_low"),
        Capability("github", "GitHub list/readme", "dev", _github, tool_cost=5, requires_network=True),
        Capability("email", "Read recent email headers", "comms", _email, tool_cost=5, requires_network=True, risk="medium"),
        Capability("remember", "Store a memory fact", "memory", _remember, tool_cost=1),
        Capability("computer", "Sandbox files", "computer", _computer, tool_cost=1, risk="medium"),
        Capability("powertoys", "PowerToys utilities", "computer", _powertoys, tool_cost=1, grounding="recipe"),
        Capability("wait_for", "Wait for URL/window/process/file", "computer", _wait_for, tool_cost=1, latency="low", grounding="api"),
    ]
    for c in specs:
        reg.register(c)
