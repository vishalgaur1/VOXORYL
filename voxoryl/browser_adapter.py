"""Browser adapter — recipe → ARIA/DOM observe → Playwright exec → CDP → vision.

Rule 3: never use vision when structured state / recipe is available.
"""

from __future__ import annotations

import re
from typing import Any

from voxoryl.perception import get_perception
from voxoryl.target_resolver import resolve_target


async def browser_action(
    intent: str,
    *,
    message: str = "",
    aria_nodes: list[dict[str, Any]] | None = None,
    dry_run: bool = False,
    allow_vision: bool = True,
) -> dict[str, Any]:
    """
    Observation: ARIA/DOM. Execution: Playwright when available, else chrome recipes.
    dry_run: plan only (0 LLM / 0 VL) — for goldens / benches.
    """
    text = message or intent
    llm_calls = 0
    vl_calls = 0

    # 0) Known recipe plan (personal Gmail etc.) — deterministic, no LLM/VL
    plan = plan_browser_recipe(text)
    if plan.get("ok"):
        if dry_run:
            return {
                "ok": True,
                "grounding": "recipe",
                "dry_run": True,
                "plan": plan,
                "llm_calls": 0,
                "vl_calls": 0,
                "postcondition": plan.get("postcondition"),
            }
        try:
            from voxoryl.chrome_control import tool_chrome

            recipe = await tool_chrome(action=str(plan.get("action") or "auto"), message=text)
            post = await _check_postcondition(str(plan.get("postcondition") or ""))
            needs_watch = "watch" in str(plan.get("postcondition") or "")
            if needs_watch:
                # Play paths: hard verify — never optimistic ok
                recipe_ok = bool(recipe.get("ok")) and bool(recipe.get("verified"))
                post_ok = bool(post.get("ok"))
            else:
                recipe_ok = bool(recipe.get("ok"))
                # Open/nav: postcondition preferred; if CDP unavailable don't block a clear recipe ok
                post_ok = bool(post.get("ok")) or bool(post.get("skipped")) or (
                    recipe_ok and not str((post.get("detail") or {}).get("url") or "")
                )
            # Recipes are accelerators only when observation/postcondition confirms context
            if recipe_ok and post_ok:
                get_perception().note_browser(
                    url=str(recipe.get("url") or plan.get("url") or ""),
                    title=str(recipe.get("title") or ""),
                )
                return {
                    "ok": True,
                    "grounding": "recipe",
                    "result": recipe,
                    "plan": plan,
                    "postcondition": post,
                    "llm_calls": 0,
                    "vl_calls": int(bool(recipe.get("used_vision"))),
                    "verified": True if needs_watch else bool(post.get("ok") or recipe_ok),
                }
            recipe_err = str(
                recipe.get("speak")
                or recipe.get("error")
                or (post.get("error") if not post_ok else None)
                or "recipe_miss"
            )
        except Exception as exc:
            recipe_err = str(exc)
        else:
            pass
    else:
        recipe_err = "no_recipe"

    # 1) Generic chrome auto (still recipe layer)
    if not dry_run:
        try:
            from voxoryl.chrome_control import tool_chrome

            recipe = await tool_chrome(action="auto", message=text)
            # Never treat unverified play as success
            recipe_ok = bool(recipe.get("ok")) and bool(recipe.get("verified", recipe.get("ok")))
            if recipe_ok:
                get_perception().note_browser(
                    url=str((recipe.get("url") or "")),
                    title=str(recipe.get("title") or ""),
                )
                return {
                    "ok": True,
                    "grounding": "recipe",
                    "result": recipe,
                    "llm_calls": 0,
                    "vl_calls": int(bool(recipe.get("used_vision"))),
                    "verified": bool(recipe.get("verified", True)),
                }
            recipe_err = str(recipe.get("error") or recipe.get("speak") or "recipe_miss")
        except Exception as exc:
            recipe_err = str(exc)

    # 2) Resolve target from ARIA
    target = resolve_target(intent or text, aria_nodes=aria_nodes, recipe=plan if plan.get("ok") else None)
    if dry_run:
        return {
            "ok": True,
            "grounding": target.get("source") or "language",
            "dry_run": True,
            "target": target,
            "llm_calls": 0,
            "vl_calls": 0,
            "recipe_error": recipe_err,
        }

    if target.get("confidence", 0) >= 0.85 and target.get("ref"):
        played = await _playwright_click(target)
        if played.get("ok"):
            return {
                "ok": True,
                "grounding": "playwright",
                "target": target,
                "result": played,
                "llm_calls": llm_calls,
                "vl_calls": vl_calls,
            }

    # 3) CDP soft path already covered by connect_over_cdp in playwright helper
    # 4) Vision last — only if allowed and structured state insufficient
    if allow_vision and get_perception().should_invoke_vl():
        try:
            from voxoryl.screen import tool_screen

            vision = await tool_screen(action="act", goal=text, rounds=2)
            vl_calls = 1
            # Vision alone is never enough for play success — check live URL when possible
            verified = False
            url = str(vision.get("url") or "")
            try:
                post = await _check_postcondition("url_contains('watch')")
                verified = bool(post.get("ok"))
                url = str((post.get("detail") or {}).get("url") or url)
            except Exception:
                verified = False
            return {
                "ok": verified,
                "grounding": "vision",
                "target": target,
                "result": vision,
                "verified": verified,
                "url": url,
                "llm_calls": llm_calls,
                "vl_calls": vl_calls,
                "speak": "" if verified else "Couldn't verify that action on YouTube.",
            }
        except Exception as exc:
            return {
                "ok": False,
                "grounding": "failed",
                "target": target,
                "recipe_error": recipe_err,
                "error": str(exc),
                "llm_calls": llm_calls,
                "vl_calls": vl_calls,
            }

    return {
        "ok": False,
        "grounding": "structured_insufficient",
        "target": target,
        "recipe_error": recipe_err,
        "hint": "Coordinate path: screen.observe → screen.click; or start Chrome with --remote-debugging-port=9222",
        "llm_calls": 0,
        "vl_calls": 0,
    }


def plan_browser_recipe(message: str) -> dict[str, Any]:
    """Deterministic recipe planner — 0 LLM / 0 VL."""
    lower = (message or "").lower()
    personal = any(k in lower for k in ("personal", "my gmail", "photo profile", "my profile"))
    if "gmail" in lower and any(k in lower for k in ("open", "go to", "goto", "launch", "show")):
        return {
            "ok": True,
            "name": "open_personal_gmail" if personal or "my" in lower else "open_gmail",
            "action": "open_and_nav" if personal or "my" in lower else "new_tab",
            "url": "https://mail.google.com",
            "profile": "personal" if personal or "my" in lower else None,
            "confidence": 0.99,
            "postcondition": "url_contains('mail.google.com')",
            "llm_calls": 0,
            "vl_calls": 0,
        }
    if any(k in lower for k in ("open youtube", "go to youtube", "goto youtube", "youtube.com")) or (
        "youtube" in lower
        and any(k in lower for k in ("open", "go to", "goto", "play", "launch", "visit", "this window"))
    ) or bool(
        re.search(
            r"(?:play|open|click|start)\s+(?:the\s+)?(?:first|second|third|fourth|fifth|\d{1,2}(?:st|nd|rd|th)?)\s+video",
            lower,
        )
    ):
        play = bool(
            re.search(
                r"(?:play|open|click|start)\s+(?:the\s+)?(?:first|second|third|fourth|fifth|\d{1,2}(?:st|nd|rd|th)?)\s+video"
                r"|first\s+video|play\s+first|play\s+video\s+\d+",
                lower,
            )
        )
        return {
            "ok": True,
            "name": "youtube_play" if play else "open_youtube",
            "action": "youtube_play" if play else "new_tab",
            "url": "https://www.youtube.com",
            "confidence": 0.98,
            "postcondition": "url_contains('watch')" if play else "url_contains('youtube.com')",
            "llm_calls": 0,
            "vl_calls": 0,
        }
    return {"ok": False}


async def _check_postcondition(cond: str) -> dict[str, Any]:
    if not cond:
        return {"ok": True, "skipped": True}
    from voxoryl.wait_for import wait_for_condition

    # Navigate postconditions need a real load window — not a 2.5s race
    return await wait_for_condition(cond, timeout_ms=18_000)


async def _playwright_click(target: dict[str, Any]) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except Exception:
        return {"ok": False, "error": "playwright_not_installed"}

    try:
        async with async_playwright() as p:
            try:
                browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            except Exception:
                return {
                    "ok": False,
                    "error": "chrome_cdp_unavailable",
                    "hint": "Start Chrome with --remote-debugging-port=9222",
                }
            contexts = browser.contexts
            if not contexts:
                return {"ok": False, "error": "no_browser_context"}
            page = contexts[0].pages[0] if contexts[0].pages else await contexts[0].new_page()
            name = str(target.get("name") or "")
            role = str(target.get("target") or "button")
            loc = page.get_by_role(role, name=name)
            await loc.first.click(timeout=3000)
            url = page.url
            get_perception().note_browser(url=url, title=await page.title())
            return {"ok": True, "url": url, "clicked": name}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def aria_snapshot_hint() -> dict[str, Any]:
    """Fetch ARIA when CDP connected; otherwise soft-fail with empty nodes."""
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except Exception:
        return {"ok": False, "error": "playwright_not_installed", "nodes": []}
    try:
        async with async_playwright() as p:
            try:
                browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            except Exception:
                return {"ok": False, "error": "chrome_cdp_unavailable", "nodes": []}
            contexts = browser.contexts
            if not contexts or not contexts[0].pages:
                return {"ok": False, "error": "no_pages", "nodes": []}
            page = contexts[0].pages[0]
            # Lightweight role dump
            buttons = await page.get_by_role("button").all()
            nodes = []
            for i, b in enumerate(buttons[:40]):
                try:
                    nm = await b.get_attribute("aria-label") or await b.inner_text()
                    nodes.append({"role": "button", "name": (nm or "").strip()[:80], "ref": f"b{i}"})
                except Exception:
                    continue
            get_perception().note_ui_tree(nodes, source="aria")
            try:
                from voxoryl.cache_layer import get_cache

                get_cache().set("a11y.tree", nodes, ttl_s=3.0, source="aria", invalidate_on=["ui.changed"])
            except Exception:
                pass
            return {"ok": True, "nodes": nodes}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "nodes": []}
