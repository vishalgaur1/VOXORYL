"""Deterministic wait_for_condition — kills sleep+look loops."""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any


# Bounded defaults for navigate / content-ready gates (honest fail on timeout).
NAV_TIMEOUT_MS = 20_000
CONTENT_TIMEOUT_MS = 25_000
POLL_S = 0.15


async def wait_for_condition(condition: str, *, timeout_ms: int = 5000) -> dict[str, Any]:
    """
    Conditions (examples):
      url_contains('gmail.com')
      url_matches('youtube\\.com')
      window_title_contains('Chrome')
      document_ready
      page_ready
      tab_exists('YouTube')
      youtube_ready / content_ready('youtube')
      process('chrome.exe')
      file_exists('C:/path')
      sleep:500
    """
    cond = (condition or "").strip()
    deadline = time.perf_counter() + max(0.05, timeout_ms / 1000.0)
    t0 = time.perf_counter()

    while time.perf_counter() < deadline:
        from voxoryl.cancellation import get_cancellation

        get_cancellation().check()
        ok, detail = await _eval(cond)
        if ok:
            return {
                "ok": True,
                "condition": cond,
                "detail": detail,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
            }
        await asyncio.sleep(POLL_S)

    return {
        "ok": False,
        "condition": cond,
        "error": "timeout",
        "latency_ms": int((time.perf_counter() - t0) * 1000),
    }


async def wait_for_navigation(
    url_needle: str,
    *,
    timeout_ms: int = NAV_TIMEOUT_MS,
    title_needle: str = "",
    require_document_ready: bool = True,
) -> dict[str, Any]:
    """
    After open/navigate: wait until live URL matches (and optionally document ready / title).
    Never returns ok=True on guesswork — requires evidence from CDP or window title.
    """
    needle = (url_needle or "").strip().lower()
    title_n = (title_needle or "").strip().lower()
    if not needle and not title_n:
        return {"ok": False, "error": "empty_needle", "verified": False}

    parts: list[str] = []
    if needle:
        parts.append(f"url_contains('{needle}')")
    if title_n:
        parts.append(f"window_title_contains('{title_n}')")
    if require_document_ready and needle:
        # Prefer compound: URL first, then soft document_ready in same poll via helper
        pass

    t0 = time.perf_counter()
    deadline = t0 + max(0.05, timeout_ms / 1000.0)
    last: dict[str, Any] = {}
    while time.perf_counter() < deadline:
        from voxoryl.cancellation import get_cancellation

        get_cancellation().check()
        state = await _live_browser_state()
        last = state
        url = str(state.get("url") or "").lower()
        title = str(state.get("title") or "").lower()
        ready = str(state.get("ready_state") or "").lower()
        url_ok = (not needle) or (needle in url)
        title_ok = (not title_n) or (title_n in title) or (title_n in str(state.get("window_title") or "").lower())
        doc_ok = (not require_document_ready) or (not state.get("cdp")) or ready in {"", "complete", "interactive"}
        if url_ok and title_ok and doc_ok:
            # Brief settle only after condition met
            await asyncio.sleep(0.08)
            return {
                "ok": True,
                "verified": True,
                "url": state.get("url"),
                "title": state.get("title") or state.get("window_title"),
                "ready_state": state.get("ready_state"),
                "source": state.get("source"),
                "latency_ms": int((time.perf_counter() - t0) * 1000),
            }
        # Window-title fallback when CDP unavailable
        if not state.get("cdp") and needle:
            wt = str(state.get("window_title") or "").lower()
            # Map common sites to title hints
            hint_ok = False
            if "youtube" in needle and "youtube" in wt:
                hint_ok = True
            elif "gmail" in needle or "mail.google" in needle:
                hint_ok = "gmail" in wt or "inbox" in wt or "mail" in wt
            elif needle.split(".")[0] in wt:
                hint_ok = True
            if hint_ok and (not title_n or title_n in wt):
                await asyncio.sleep(0.08)
                return {
                    "ok": True,
                    "verified": True,
                    "url": "",
                    "title": state.get("window_title"),
                    "ready_state": "",
                    "source": "window_title",
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                }
        await asyncio.sleep(POLL_S)

    return {
        "ok": False,
        "verified": False,
        "error": "navigation_timeout",
        "url": last.get("url"),
        "title": last.get("title") or last.get("window_title"),
        "ready_state": last.get("ready_state"),
        "latency_ms": int((time.perf_counter() - t0) * 1000),
    }


async def wait_for_youtube_ready(*, timeout_ms: int = CONTENT_TIMEOUT_MS) -> dict[str, Any]:
    """
    Wait until YouTube tab/content is ready for grounding/click:
    URL on youtube.com + (tiles via CDP OR window title OR document complete).
    """
    t0 = time.perf_counter()
    deadline = t0 + max(0.05, timeout_ms / 1000.0)
    last: dict[str, Any] = {}
    while time.perf_counter() < deadline:
        from voxoryl.cancellation import get_cancellation

        get_cancellation().check()
        state = await _live_browser_state()
        last = state
        url = str(state.get("url") or "").lower()
        title = str(state.get("title") or state.get("window_title") or "").lower()
        tiles = int(state.get("video_tiles") or 0)
        ready = str(state.get("ready_state") or "").lower()
        on_yt = "youtube.com" in url or "youtube" in title
        content_ok = tiles >= 1 or ready in {"complete", "interactive"} or (
            "youtube" in title and title.strip() not in {"", "new tab"}
        )
        if on_yt and content_ok:
            await asyncio.sleep(0.1)
            return {
                "ok": True,
                "verified": True,
                "url": state.get("url"),
                "title": state.get("title") or state.get("window_title"),
                "video_tiles": tiles,
                "ready_state": state.get("ready_state"),
                "source": state.get("source"),
                "latency_ms": int((time.perf_counter() - t0) * 1000),
            }
        # Soft UIA/title path without CDP
        if not state.get("cdp") and "youtube" in title:
            await asyncio.sleep(0.12)
            return {
                "ok": True,
                "verified": True,
                "url": "",
                "title": state.get("window_title"),
                "video_tiles": 0,
                "ready_state": "",
                "source": "window_title",
                "latency_ms": int((time.perf_counter() - t0) * 1000),
            }
        await asyncio.sleep(POLL_S)

    return {
        "ok": False,
        "verified": False,
        "error": "youtube_not_ready",
        "url": last.get("url"),
        "title": last.get("title") or last.get("window_title"),
        "video_tiles": last.get("video_tiles") or 0,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
    }


async def wait_after_open(
    site_or_url: str,
    *,
    timeout_ms: int = NAV_TIMEOUT_MS,
    content: bool = False,
) -> dict[str, Any]:
    """Ready gate after opening a site. YouTube optionally waits for tiles/content."""
    raw = (site_or_url or "").strip().lower()
    needle = raw
    if "youtube" in raw:
        needle = "youtube.com"
        if content:
            nav = await wait_for_navigation(needle, timeout_ms=timeout_ms, title_needle="youtube")
            if not nav.get("ok"):
                return nav
            ready = await wait_for_youtube_ready(timeout_ms=min(timeout_ms, CONTENT_TIMEOUT_MS))
            ready["nav"] = nav
            return ready
        return await wait_for_navigation(needle, timeout_ms=timeout_ms, title_needle="youtube")
    if "gmail" in raw or "mail.google" in raw:
        return await wait_for_navigation("mail.google.com", timeout_ms=timeout_ms, title_needle="mail")
    if "github" in raw:
        return await wait_for_navigation("github.com", timeout_ms=timeout_ms, title_needle="github")
    # Generic: strip scheme/path for contains match
    m = re.search(r"https?://([^/]+)", raw)
    if m:
        needle = m.group(1).lower()
    elif "/" in raw:
        needle = raw.split("/", 1)[0]
    return await wait_for_navigation(needle, timeout_ms=timeout_ms)


async def _eval(cond: str) -> tuple[bool, dict[str, Any]]:
    if cond.startswith("sleep:"):
        try:
            ms = int(cond.split(":", 1)[1])
        except ValueError:
            ms = 100
        await asyncio.sleep(ms / 1000.0)
        return True, {"slept_ms": ms}

    low = cond.strip().lower()
    if low in {"document_ready", "page_ready"}:
        state = await _live_browser_state()
        ready = str(state.get("ready_state") or "").lower()
        if state.get("cdp") and ready in {"complete", "interactive"}:
            return True, state
        if not state.get("cdp"):
            # Without CDP we cannot prove document_ready — fail closed unless title exists
            wt = str(state.get("window_title") or "")
            if wt and "new tab" not in wt.lower():
                return True, {**state, "source": "window_title_soft"}
        return False, state

    if low in {"youtube_ready", "youtube_tiles_visible"}:
        r = await wait_for_youtube_ready(timeout_ms=400)
        return bool(r.get("ok")), r

    m = re.search(r"content_ready\(\s*['\"](.+?)['\"]\s*\)", cond, re.I)
    if m:
        site = m.group(1).lower()
        if "youtube" in site:
            r = await wait_for_youtube_ready(timeout_ms=400)
            return bool(r.get("ok")), r
        r = await wait_for_navigation(site, timeout_ms=400)
        return bool(r.get("ok")), r

    m = re.search(r"tab_exists\(\s*['\"](.+?)['\"]\s*\)", cond, re.I)
    if m:
        needle = m.group(1).lower()
        state = await _live_browser_state()
        blob = " ".join(
            [
                str(state.get("url") or ""),
                str(state.get("title") or ""),
                str(state.get("window_title") or ""),
                " ".join(str(t) for t in (state.get("tab_titles") or [])),
            ]
        ).lower()
        return needle in blob, state

    m = re.search(r"url_matches\(\s*['\"](.+?)['\"]\s*\)", cond, re.I)
    if m:
        pat = m.group(1)
        state = await _live_browser_state()
        url = str(state.get("url") or "")
        try:
            if re.search(pat, url, re.I):
                return True, {"url": url, "source": state.get("source")}
        except re.error:
            if pat.lower() in url.lower():
                return True, {"url": url, "source": state.get("source")}
        return False, {"url": url}

    m = re.search(r"url_contains\(\s*['\"](.+?)['\"]\s*\)", cond, re.I)
    if m:
        needle = m.group(1).lower()
        from voxoryl.state_store import get_state

        browser = (get_state().snapshot()["world"].get("browser") or {}).get("value") or {}
        url = str(browser.get("url") or "").lower()
        if needle in url:
            return True, {"url": url, "source": "world"}
        live = await _live_browser_url()
        if live and needle in live.lower():
            try:
                from voxoryl.perception import get_perception

                get_perception().note_browser(url=live)
            except Exception:
                pass
            return True, {"url": live, "source": "cdp"}
        return False, {"url": live or url}

    m = re.search(r"window_title_contains\(\s*['\"](.+?)['\"]\s*\)", cond, re.I)
    if m:
        needle = m.group(1).lower()
        title = await _foreground_window_title()
        return needle in title.lower(), {"title": title}

    m = re.search(r"process\(\s*['\"](.+?)['\"]\s*\)", cond, re.I)
    if m:
        name = m.group(1).lower()
        try:
            import psutil

            for p in psutil.process_iter(["name"]):
                if name in (p.info.get("name") or "").lower():
                    return True, {"process": p.info.get("name")}
        except Exception:
            pass
        return False, {}

    m = re.search(r"file_exists\(\s*['\"](.+?)['\"]\s*\)", cond, re.I)
    if m:
        path = Path(m.group(1))
        return path.exists(), {"path": str(path)}

    # Bare shorthand: gmail.com → url_contains
    if "." in cond and " " not in cond and "(" not in cond:
        from voxoryl.state_store import get_state

        browser = (get_state().snapshot()["world"].get("browser") or {}).get("value") or {}
        url = str(browser.get("url") or "").lower()
        if cond.lower() in url:
            return True, {"url": url, "shorthand": True}
        live = await _live_browser_url()
        if live and cond.lower() in live.lower():
            return True, {"url": live, "shorthand": True, "source": "cdp"}
        return False, {"url": live or url, "shorthand": True}

    return False, {"error": "unknown_condition"}


async def _foreground_window_title() -> str:
    try:
        import ctypes

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or ""
    except Exception:
        from voxoryl.state_store import get_state

        return str((get_state().snapshot()["world"].get("active_window") or {}).get("value") or "")


async def _live_browser_url() -> str:
    """Best-effort URL from Chrome CDP — used to verify postconditions honestly."""
    state = await _live_browser_state()
    return str(state.get("url") or "")


async def _live_browser_state() -> dict[str, Any]:
    """Best-effort live browser snapshot via CDP + window title."""
    out: dict[str, Any] = {
        "url": "",
        "title": "",
        "ready_state": "",
        "video_tiles": 0,
        "tab_titles": [],
        "window_title": await _foreground_window_title(),
        "cdp": False,
        "source": "",
    }
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except Exception:
        out["source"] = "window_title"
        return out
    try:
        async with async_playwright() as p:
            try:
                browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            except Exception:
                out["source"] = "window_title"
                return out
            yt_page = None
            any_page = None
            titles: list[str] = []
            for ctx in browser.contexts or []:
                for pg in getattr(ctx, "pages", []) or []:
                    any_page = any_page or pg
                    u = str(getattr(pg, "url", "") or "")
                    try:
                        t = await pg.title()
                    except Exception:
                        t = ""
                    if t:
                        titles.append(t)
                    if "youtube.com" in u.lower():
                        yt_page = pg
            page = yt_page or any_page
            out["tab_titles"] = titles[:20]
            out["cdp"] = True
            if page is None:
                out["source"] = "cdp_empty"
                return out
            out["url"] = str(getattr(page, "url", "") or "")
            try:
                out["title"] = await page.title()
            except Exception:
                out["title"] = ""
            try:
                out["ready_state"] = await page.evaluate("() => document.readyState || ''")
            except Exception:
                out["ready_state"] = ""
            if "youtube.com" in out["url"].lower():
                try:
                    out["video_tiles"] = int(
                        await page.evaluate(
                            """() => {
                              const sels = [
                                'ytd-rich-item-renderer a#video-title-link',
                                'ytd-rich-grid-media a#video-title-link',
                                'ytd-video-renderer a#video-title',
                                'a#video-title-link',
                                'a#video-title'
                              ];
                              const seen = new Set();
                              let n = 0;
                              for (const sel of sels) {
                                for (const a of document.querySelectorAll(sel)) {
                                  const href = a.href || '';
                                  if (!href || seen.has(href)) continue;
                                  if (!/watch|shorts\\//i.test(href)) continue;
                                  seen.add(href);
                                  n++;
                                  if (n >= 8) return n;
                                }
                              }
                              return n;
                            }"""
                        )
                        or 0
                    )
                except Exception:
                    out["video_tiles"] = 0
            out["source"] = "cdp"
            try:
                from voxoryl.perception import get_perception

                if out["url"]:
                    get_perception().note_browser(url=out["url"], title=out.get("title") or "")
            except Exception:
                pass
            return out
    except Exception:
        out["source"] = "window_title"
        return out
