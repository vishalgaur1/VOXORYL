from __future__ import annotations

"""
Chrome control — fast shortcut paths + profile picker for Voxoryl.

Flows:
  open + profile  → launch Chrome with --profile-directory when known;
                    else detect "Who's using Chrome?" and vision-click the owner profile
  goto tab        → Ctrl+Shift+A tab search / Ctrl+Tab scan by title keyword
  new tab + URL   → Ctrl+T, Ctrl+L, paste URL, Enter
"""

import asyncio
import re
from typing import Any

from voxoryl.software_knowledge import (
    learn,
    load_app,
    resolve_chrome_profile_directory,
    resolve_profile,
    resolve_site,
)
from voxoryl.windows_ops import focus_window_by_title, open_app


def _hotkey(*keys: str) -> dict[str, Any]:
    try:
        from voxoryl.safety import gate_hotkey

        gated = gate_hotkey(list(keys))
        if not gated.get("ok"):
            return gated
    except Exception:
        from voxoryl.safety import refuse

        # Fail-closed only for clearly destructive combos
        keyset = {k.lower() for k in keys}
        if keyset & {"delete", "backspace"} and keyset & {"shift", "ctrl", "alt", "win"}:
            return refuse("unknown", detail="Hotkey safety gate failed closed.")
    try:
        import pyautogui

        pyautogui.FAILSAFE = True
        pyautogui.hotkey(*keys)
        return {"ok": True, "keys": list(keys)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _paste(text: str, *, enter: bool = False) -> dict[str, Any]:
    from voxoryl.screen import type_now

    return type_now(text, press_enter=enter)


def _picker_present(description: str) -> bool:
    desc = (description or "").lower()
    if any(k in desc for k in ("who's using chrome", "who is using chrome", "whos using chrome")):
        return True
    # YES/NO look answers
    head = desc[:80]
    if head.strip().startswith("yes") and ("profile" in desc or "chrome" in desc):
        return True
    if "profile picker" in desc or "profile card" in desc:
        return True
    return False


def _act_really_clicked(result: dict[str, Any] | None) -> bool:
    """True only when mouse click steps actually ran (not empty plan with ok=True)."""
    if not result:
        return False
    if result.get("clicked") and result.get("ok"):
        return True
    executed = result.get("executed") or {}
    rows = executed.get("executed") if isinstance(executed, dict) else None
    if isinstance(result.get("rounds"), list):
        for round_row in result["rounds"]:
            if isinstance(round_row, dict) and _act_really_clicked(
                {k: v for k, v in round_row.items() if k != "rounds"}
            ):
                return True
    if isinstance(rows, list):
        return any(
            r.get("ok") and str(r.get("action") or "").lower() in {"click", "double_click"}
            for r in rows
        )
    return False


async def _vision_find_profile_click(profile: dict[str, Any]) -> dict[str, Any]:
    """Use direct vision locate (coords from VL) then click; verify picker dismisses."""
    from voxoryl.screen import computer_use_enabled, locate_and_click, tool_screen

    if not computer_use_enabled():
        return {
            "ok": False,
            "speak": "Computer use is off — enable COMPUTER_USE_ENABLED to click Chrome profiles.",
        }

    prefer = str(profile.get("prefer") or "photo")
    label = str(profile.get("label") or "your profile")

    if prefer == "photo":
        target = (
            "Chrome 'Who's using Chrome?' profile picker: the circular PHOTO avatar of "
            f"'{label}' (real photograph if present). "
            "NOT letter-only avatar cards, NOT Contact/Add. "
            "Return the center of the PHOTO profile circle."
        )
    elif prefer == "orange":
        target = (
            f"Chrome profile picker: the '{label}' card with a colored letter avatar "
            "(office/work style), NOT the photograph avatar."
        )
    else:
        target = f"Chrome profile picker card titled like {label}. Center of that avatar."

    await asyncio.to_thread(focus_window_by_title, "Who's using Chrome", "Google Chrome", "Chrome")
    await asyncio.sleep(0.25)

    locate_result = await locate_and_click(target, verify_gone=("who's using chrome", "who is using chrome"))
    if _act_really_clicked(locate_result) or locate_result.get("ok"):
        return locate_result

    # Fallback: generic act with multi-round loop
    goal = (
        "The Chrome profile picker 'Who's using Chrome?' is on screen. "
        f"Click the profile card for '{label}'. "
    )
    if prefer == "photo":
        goal += (
            "IMPORTANT: Prefer a PHOTOGRAPH avatar when described, "
            "not a letter-only circle, not Contact/Add. "
            "Click once in the center of the matching profile card."
        )
    elif prefer == "orange":
        goal += "Prefer the colored letter avatar (office), not the photo avatar."
    else:
        goal += f"Match the card titled like {label}."

    result = await tool_screen(action="act", goal=goal, rounds=3)
    result["locate_fallback"] = locate_result
    return result


async def open_chrome_with_profile(message: str = "") -> dict[str, Any]:
    """Open Chrome into the right profile — prefer --profile-directory, else vision click."""
    from voxoryl.config import settings
    from voxoryl.software_knowledge import _owner_profile_from_env

    load_app("chrome")
    profile = resolve_profile("chrome", message) or _owner_profile_from_env() or {
        "id": "default",
        "prefer": (settings.voxoryl_chrome_profile_prefer or "photo").strip() or "photo",
        "label": settings.owner_name or "Default",
        "emails": [settings.owner_email] if settings.owner_email else [],
    }

    directory_meta = resolve_chrome_profile_directory(profile)
    extra_args: list[str] = []
    launch_mode = "plain"
    if directory_meta and directory_meta.get("directory"):
        extra_args = [f"--profile-directory={directory_meta['directory']}"]
        launch_mode = "profile_directory"

    launched = await asyncio.to_thread(open_app, "chrome", extra_args=extra_args or None)
    if not launched.get("ok") and extra_args:
        # Retry without profile args
        launched = await asyncio.to_thread(open_app, "chrome")
        launch_mode = "plain_fallback"
        directory_meta = None

    if not launched.get("ok"):
        learn("chrome", action="open", detail=str(launched.get("error")), ok=False)
        return launched

    # Give window / picker time to appear
    await asyncio.sleep(0.85 if launch_mode == "profile_directory" else 1.45)
    await asyncio.to_thread(focus_window_by_title, "Who's using Chrome", "Google Chrome", "Chrome")

    picker_clicked = False
    picker_needed = False
    picker_result: dict[str, Any] | None = None

    try:
        from voxoryl.screen import computer_use_enabled, tool_screen

        # Fast path: --profile-directory normally skips the picker entirely.
        # Only run vision if the picker window title is still present.
        picker_window = await asyncio.to_thread(focus_window_by_title, "Who's using Chrome")
        need_vision = bool(picker_window.get("ok")) or launch_mode != "profile_directory"

        if need_vision and computer_use_enabled():
            look = await tool_screen(
                action="look",
                question=(
                    "Is the Chrome window showing a 'Who's using Chrome?' profile picker "
                    "with profile avatar cards? Answer first line YES or NO, then briefly describe."
                ),
            )
            desc = str((look.get("vision") or {}).get("description") or look.get("speak") or "")
            if _picker_present(desc) or picker_window.get("ok"):
                picker_needed = True
                picker_result = await _vision_find_profile_click(profile)
                picker_clicked = _act_really_clicked(picker_result) and bool(picker_result.get("ok"))
                # Verify dismissal
                if picker_clicked:
                    await asyncio.sleep(0.9)
                    verify = await tool_screen(
                        action="look",
                        question=(
                            "Is 'Who's using Chrome?' profile picker STILL visible? "
                            "Answer first line YES or NO."
                        ),
                    )
                    vdesc = str((verify.get("vision") or {}).get("description") or "").lower()
                    still = vdesc.strip().startswith("yes") or "who's using chrome" in vdesc
                    still = still or bool((await asyncio.to_thread(focus_window_by_title, "Who's using Chrome")).get("ok"))
                    if still:
                        retry = await _vision_find_profile_click(profile)
                        picker_result = {"first": picker_result, "retry": retry, "verify": verify}
                        picker_clicked = _act_really_clicked(retry) and bool(retry.get("ok"))
                    else:
                        picker_result = {**(picker_result or {}), "verified_dismissed": True, "verify": verify}
            elif launch_mode != "profile_directory" and resolve_profile("chrome", message):
                picker_result = {"ok": True, "skipped": True, "reason": "no_picker_visible"}
        elif need_vision and not computer_use_enabled():
            picker_needed = True
            picker_result = {
                "ok": False,
                "speak": "Computer use is off — enable COMPUTER_USE_ENABLED to click Chrome profiles.",
            }
        elif launch_mode == "profile_directory":
            picker_result = {
                "ok": True,
                "skipped": True,
                "reason": "profile_directory",
                "directory": (directory_meta or {}).get("directory"),
            }
    except Exception as exc:
        picker_result = {"ok": False, "error": str(exc)}
        if launch_mode != "profile_directory":
            picker_needed = True

    ok = bool(launched.get("ok")) and (not picker_needed or picker_clicked)
    learn(
        "chrome",
        action="open_with_profile",
        detail=(
            f"{profile.get('id')} mode={launch_mode} "
            f"dir={(directory_meta or {}).get('directory')} picker={picker_clicked}"
        ),
        ok=ok,
    )

    speak_bits: list[str] = []
    if not ok:
        speak_bits.append(str(launched.get("speak") or "Couldn't open Chrome."))
    if launch_mode == "profile_directory" and directory_meta and not ok:
        speak_bits.append(
            f"Tried {profile.get('label') or directory_meta.get('gaia_name') or 'your profile'}."
        )
    if picker_clicked and not ok:
        speak_bits.append(f"Selected {profile.get('label') or 'your profile'}.")
    elif picker_needed and resolve_profile("chrome", message) and not ok:
        if not computer_use_gate_ok():
            speak_bits.append(
                "Profile picker may be open but computer use is disabled — set COMPUTER_USE_ENABLED=true."
            )
        else:
            speak_bits.append(
                "Couldn't confirm the profile click. Say 'select my Chrome profile' and I'll try again."
            )

    return {
        "ok": ok,
        "app": "chrome",
        "profile": profile,
        "profile_directory": directory_meta,
        "launch_mode": launch_mode,
        "launched": launched,
        "picker": picker_result,
        "picker_clicked": picker_clicked,
        # Pure open action — silent on success
        "speak": " ".join(speak_bits) if speak_bits else "",
        "action_only": True,
    }


def computer_use_gate_ok() -> bool:
    try:
        from voxoryl.screen import computer_use_enabled

        return bool(computer_use_enabled())
    except Exception:
        return False


async def chrome_new_tab_url(target: str) -> dict[str, Any]:
    """Ctrl+T → address bar → paste URL → Enter. Never invent Google from junk fragments."""
    raw = (target or "").strip()
    if not raw or _is_junk_nav_token(raw):
        return {
            "ok": False,
            "error": "refused_junk_nav",
            "speak": "Couldn't tell what to open in the browser.",
            "action_only": True,
        }

    url = resolve_site("chrome", raw) or raw
    if not url.startswith("http"):
        # Only search when the token looks like a real query / site-ish phrase
        if not _looks_like_search_query(raw):
            return {
                "ok": False,
                "error": "refused_non_site",
                "detail": raw,
                "speak": "Couldn't tell what to open in the browser.",
                "action_only": True,
            }
        url = "https://www.google.com/search?q=" + re.sub(r"\s+", "+", raw.strip())

    await asyncio.to_thread(focus_window_by_title, "Chrome", "Google Chrome")
    steps: list[dict[str, Any]] = []
    steps.append(await asyncio.to_thread(_hotkey, "ctrl", "t"))
    await asyncio.sleep(0.28)
    steps.append(await asyncio.to_thread(_hotkey, "ctrl", "l"))
    await asyncio.sleep(0.12)
    steps.append(await asyncio.to_thread(_paste, url, enter=True))

    keys_ok = all(s.get("ok", True) for s in steps)
    ready: dict[str, Any] = {"ok": False, "skipped": True}
    if keys_ok:
        from voxoryl.wait_for import wait_after_open

        want_content = "youtube" in url.lower()
        ready = await wait_after_open(url, timeout_ms=20_000, content=want_content)
    # Known sites: require ready evidence. Unknown URLs: keys_ok is enough; verified stays honest.
    critical = any(k in url.lower() for k in ("youtube.com", "mail.google.com", "gmail.com"))
    if critical:
        ok = keys_ok and bool(ready.get("ok"))
    else:
        ok = keys_ok
    learn("chrome", action="new_tab_url", detail=f"{url} ready={ready.get('ok')} ok={ok}", shortcut=["ctrl", "t"], ok=ok)
    return {
        "ok": ok,
        "url": ready.get("url") or url,
        "steps": steps,
        "ready": ready,
        "verified": bool(ready.get("verified") or ready.get("ok")),
        # Pure action — stay silent on success (no narrating the URL)
        "speak": ""
        if ok
        else ("Page didn't finish loading." if keys_ok else "Couldn't open that tab."),
        "action_only": True,
    }


def _is_junk_nav_token(token: str) -> bool:
    """Residuals like 'in the notepad app' must never become a Google search."""
    t = (token or "").strip().lower()
    if not t:
        return True
    if re.fullmatch(r"(?:a\s+)?(?:new\s+)?tab(?:\s+in(?:\s+chrome)?)?", t):
        return True
    if re.match(r"^(?:in|on|the|a|an|to|for|with)\b", t) and len(t.split()) <= 6:
        # "in the notepad app", "on chrome", etc.
        from voxoryl.windows_ops import APP_ALIASES

        if any(alias in t for alias, _key in APP_ALIASES if _key not in {"chrome", "edge", "firefox"}):
            return True
        if re.search(r"\bapp\b", t):
            return True
    from voxoryl.windows_ops import detect_app_name

    app = detect_app_name(t)
    if app and app not in {"chrome", "edge", "firefox"} and not resolve_site("chrome", t):
        return True
    return False


def _looks_like_search_query(token: str) -> bool:
    t = (token or "").strip()
    if not t or _is_junk_nav_token(t):
        return False
    if re.search(r"[./]", t) or "@" in t:
        return True
    # Multi-word user query or single known-looking token
    words = t.split()
    if len(words) >= 2 and not re.match(r"^(?:in|on|the)\b", t.lower()):
        return True
    if len(words) == 1 and len(words[0]) >= 2:
        return True
    return False


async def chrome_goto_tab(title_hint: str) -> dict[str, Any]:
    """
    Switch to an existing tab matching title_hint.
    Prefer Chrome tab search (Ctrl+Shift+A), fall back to typing in omnibox search.
    """
    hint = (title_hint or "").strip()
    if not hint:
        return {"ok": False, "speak": "Which tab should I switch to?"}

    await asyncio.to_thread(focus_window_by_title, "Chrome", "Google Chrome")
    # Chrome tab search / switcher
    steps: list[dict[str, Any]] = []
    steps.append(await asyncio.to_thread(_hotkey, "ctrl", "shift", "a"))
    await asyncio.sleep(0.35)
    steps.append(await asyncio.to_thread(_paste, hint, enter=True))
    await asyncio.sleep(0.25)

    keys_ok = all(s.get("ok", True) for s in steps)
    ready: dict[str, Any] = {"ok": keys_ok, "skipped": True}
    if keys_ok:
        from voxoryl.wait_for import wait_for_condition, wait_for_youtube_ready

        low = hint.lower()
        if "youtube" in low:
            ready = await wait_for_youtube_ready(timeout_ms=18_000)
        else:
            ready = await wait_for_condition(
                f"window_title_contains('{hint[:40]}')",
                timeout_ms=12_000,
            )
    ok = keys_ok and bool(ready.get("ok"))
    learn("chrome", action="goto_tab", detail=f"{hint} ready={ok}", shortcut=["ctrl", "shift", "a"], ok=ok)
    return {
        "ok": ok,
        "hint": hint,
        "steps": steps,
        "ready": ready,
        "verified": bool(ready.get("ok")),
        "speak": "" if ok else f"Couldn't find tab '{hint}'.",
        "action_only": True,
    }


_ORDINAL_MAP: dict[str, int] = {
    "first": 1,
    "1st": 1,
    "second": 2,
    "2nd": 2,
    "third": 3,
    "3rd": 3,
    "fourth": 4,
    "4th": 4,
    "fifth": 5,
    "5th": 5,
    "sixth": 6,
    "6th": 6,
    "seventh": 7,
    "7th": 7,
    "eighth": 8,
    "8th": 8,
    "ninth": 9,
    "9th": 9,
    "tenth": 10,
    "10th": 10,
}


def _wants_youtube(message: str) -> bool:
    return "youtube" in (message or "").lower()


def parse_video_ordinal(message: str) -> int | None:
    """1-based ordinal for 'play the second video' / '2nd video' / 'video 3'. None if not a play-nth intent."""
    lower = (message or "").lower()
    if not lower:
        return None
    # Explicit ordinal word or Nth
    m = re.search(
        r"(?:play|open|click|start)\s+(?:the\s+)?("
        + "|".join(re.escape(k) for k in sorted(_ORDINAL_MAP, key=len, reverse=True))
        + r"|\d{1,2}(?:st|nd|rd|th)?)\s+video\b",
        lower,
    )
    if m:
        token = m.group(1).strip()
        if token in _ORDINAL_MAP:
            return _ORDINAL_MAP[token]
        digits = re.match(r"(\d{1,2})", token)
        if digits:
            n = int(digits.group(1))
            return n if 1 <= n <= 20 else None
    # "play video 2" / "play #2"
    m = re.search(r"(?:play|open|click|start)\s+(?:video\s+#?|#)(\d{1,2})\b", lower)
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= 20 else None
    # "first/second video" without play verb but with click/select
    m = re.search(
        r"\b("
        + "|".join(re.escape(k) for k in sorted(_ORDINAL_MAP, key=len, reverse=True))
        + r")\s+video\b",
        lower,
    )
    if m and any(k in lower for k in ("play", "open", "click", "start", "watch")):
        return _ORDINAL_MAP.get(m.group(1))
    # Bare "play the first" / "play first" (legacy)
    if re.search(r"(?:play|open)\s+(?:the\s+)?first\b|play\s+first|click\s+(?:the\s+)?first", lower):
        return 1
    return None


def _wants_play_video(message: str) -> bool:
    return parse_video_ordinal(message) is not None


def _wants_play_first_video(message: str) -> bool:
    """Backward-compatible alias — true for any play-nth ordinal (not only first)."""
    return _wants_play_video(message)


def wants_play_or_pause_media(message: str) -> bool:
    lower = (message or "").lower()
    if parse_video_ordinal(message) is not None:
        return True
    return bool(
        re.search(
            r"\b(pause|resume|unpause|play\s+(?:it|this|that|video)|is\s+(?:it\s+)?playing)\b",
            lower,
        )
    )


def _wants_this_window(message: str) -> bool:
    lower = (message or "").lower()
    return any(
        k in lower
        for k in (
            "this window",
            "in that",
            "in this",
            "same window",
            "current window",
            "current tab",
            "this tab",
        )
    )


def _profileish(message: str) -> bool:
    from voxoryl.config import settings

    lower = (message or "").lower()
    generic = (
        "my chrome profile",
        "chrome profile",
        "who's using",
        "who is using",
        "select profile",
        "photo profile",
        "my profile",
        "personal profile",
    )
    if any(k in lower for k in generic):
        return True
    for phrase in settings.chrome_profile_match_phrases:
        if phrase and phrase in lower and any(
            k in lower for k in ("chrome", "browser", "profile", "open", "select", " id")
        ):
            return True
    owner_first = (settings.owner_name or "").split()[0].lower() if settings.owner_name else ""
    if owner_first and owner_first in lower and any(
        k in lower for k in ("chrome", "browser", "profile", "open", "select", " id")
    ):
        return True
    return False


def _clean_site_token(token: str) -> str | None:
    """Strip residuals like 'in this window and play the first video' from a site token."""
    t = (token or "").strip().lower()
    if not t:
        return None
    # Prefer a known site name embedded in the token
    data = load_app("chrome")
    for name in sorted((data.get("sites") or {}), key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", t):
            return name
    t = re.sub(r"\b(please|tab|website|site|page|in chrome|on chrome|video|videos)\b", " ", t)
    t = re.sub(r"\b(?:in|on)\s+this\s+window\b.*$", " ", t)
    t = re.sub(r"\band\s+(?:then\s+)?play\b.*$", " ", t)
    t = re.sub(r"\bplay\s+(?:the\s+)?(?:first|second|third|fourth|fifth|\d{1,2}(?:st|nd|rd|th)?)\b.*$", " ", t)
    t = re.sub(r"\bplay\s+(?:the\s+)?first\b.*$", " ", t)
    t = re.sub(r"^(?:in|on)\s+(?:the\s+)?", "", t)
    t = re.sub(r"\s+app$", "", t)
    t = re.sub(r"\s+", " ", t).strip(" .")
    if _is_junk_nav_token(t):
        return None
    return t or None


def _extract_site_token(message: str) -> str | None:
    lower = (message or "").lower().strip()
    data = load_app("chrome")
    # Prefer known sites mentioned anywhere (youtube in long compound utterances)
    for name in sorted((data.get("sites") or {}), key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", lower):
            # Skip noisy google matches when youtube is the real target
            if name in {"google"} and "youtube" in lower:
                continue
            return name
    # open openai / go to youtube / open apple.com
    m = re.search(
        r"(?:open|go to|goto|switch to|navigate to|visit|launch)\s+(?:a\s+)?(?:new\s+)?(?:tab\s+(?:for|to|with)\s+)?(.+)$",
        lower,
    )
    if m:
        return _clean_site_token(m.group(1))
    return None


async def open_youtube(*, new_tab: bool = True, in_existing: bool = False) -> dict[str, Any]:
    """Navigate to youtube.com — new tab by default, or omnibox in the focused window.

    Always waits until the page is actually ready (URL/title/document) before returning ok.
    """
    url = resolve_site("chrome", "youtube") or "https://www.youtube.com"
    await asyncio.to_thread(focus_window_by_title, "Chrome", "Google Chrome", "YouTube")
    await asyncio.sleep(0.2)
    from voxoryl.wait_for import wait_after_open, wait_for_youtube_ready

    if in_existing or not new_tab:
        steps: list[dict[str, Any]] = []
        steps.append(await asyncio.to_thread(_hotkey, "ctrl", "l"))
        await asyncio.sleep(0.12)
        steps.append(await asyncio.to_thread(_paste, url, enter=True))
        keys_ok = all(s.get("ok", True) for s in steps)
        ready = (
            await wait_after_open(url, timeout_ms=20_000, content=True)
            if keys_ok
            else {"ok": False, "error": "keys_failed"}
        )
        ok = keys_ok and bool(ready.get("ok"))
        learn("chrome", action="open_youtube_here", detail=f"{url} ready={ok}", ok=ok)
        return {
            "ok": ok,
            "url": ready.get("url") or url,
            "mode": "omnibox",
            "steps": steps,
            "ready": ready,
            "verified": bool(ready.get("verified") or ready.get("ok")),
            "speak": "" if ok else ("YouTube didn't finish loading." if keys_ok else "Couldn't open YouTube."),
            "action_only": True,
        }
    out = await chrome_new_tab_url("youtube")
    # chrome_new_tab_url already gates; if content tiles still missing, one more content wait
    if out.get("ok") and not (out.get("ready") or {}).get("video_tiles"):
        content = await wait_for_youtube_ready(timeout_ms=15_000)
        out["ready"] = {**(out.get("ready") or {}), "content": content}
        if not content.get("ok"):
            out["ok"] = False
            out["verified"] = False
            out["speak"] = "YouTube opened but feed didn't load in time."
        else:
            out["verified"] = True
            out["url"] = content.get("url") or out.get("url")
    return out


_YT_TILE_SELECTORS = (
    "ytd-rich-item-renderer:not([is-slim-media]) a#video-title-link",
    "ytd-rich-grid-media a#video-title-link",
    "ytd-rich-item-renderer a#video-title-link",
    "ytd-video-renderer a#video-title",
    "ytd-compact-video-renderer a#video-title",
    "a#video-title-link",
    "a#video-title",
)


def _url_is_watch(url: str) -> bool:
    u = (url or "").lower()
    return "/watch" in u or "/shorts/" in u


def _honest_play_fail_speak(index: int, *, reason: str = "") -> str:
    label = {1: "first", 2: "second", 3: "third"}.get(index, f"#{index}")
    base = f"Couldn't play the {label} video."
    if reason:
        return f"{base} {reason}".strip()
    return base


async def _cdp_youtube_page(browser: Any) -> Any | None:
    """Prefer an already-open YouTube tab; never invent a blank Google tab."""
    contexts = getattr(browser, "contexts", None) or []
    yt_page = None
    any_page = None
    for ctx in contexts:
        for pg in getattr(ctx, "pages", []) or []:
            any_page = any_page or pg
            if "youtube.com" in (pg.url or "").lower():
                yt_page = pg
                break
        if yt_page:
            break
    return yt_page or any_page


async def observe_youtube_page(page: Any) -> dict[str, Any]:
    """Structured observation: URL, title, video tiles, player state. No VL."""
    url = str(getattr(page, "url", "") or "")
    try:
        title = await page.title()
    except Exception:
        title = ""

    videos: list[dict[str, Any]] = []
    try:
        raw = await page.evaluate(
            """() => {
              const sels = [
                'ytd-rich-item-renderer:not([is-slim-media]) a#video-title-link',
                'ytd-rich-grid-media a#video-title-link',
                'ytd-rich-item-renderer a#video-title-link',
                'ytd-video-renderer a#video-title',
                'ytd-compact-video-renderer a#video-title',
                'a#video-title-link',
                'a#video-title'
              ];
              const seen = new Set();
              const out = [];
              for (const sel of sels) {
                for (const a of document.querySelectorAll(sel)) {
                  const href = a.href || '';
                  if (!href || seen.has(href)) continue;
                  if (!/watch|shorts\\//i.test(href)) continue;
                  // Skip Shorts shelf tiles when a normal feed is present
                  if (a.closest('ytd-rich-shelf-renderer, ytd-reel-shelf-renderer')) continue;
                  seen.add(href);
                  const t = (a.getAttribute('title') || a.textContent || '').trim().replace(/\\s+/g, ' ');
                  out.push({ title: t.slice(0, 120), href });
                  if (out.length >= 24) return out;
                }
                if (out.length >= 8) break;
              }
              return out;
            }"""
        )
        for i, row in enumerate(raw or []):
            if isinstance(row, dict):
                videos.append(
                    {
                        "index": i + 1,
                        "title": str(row.get("title") or "")[:120],
                        "href": str(row.get("href") or ""),
                    }
                )
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "title": title,
            "videos": [],
            "player": {"on_watch": _url_is_watch(url), "playing": False, "known": False},
            "error": str(exc),
        }

    player: dict[str, Any] = {"on_watch": _url_is_watch(url), "playing": False, "known": False}
    try:
        pstate = await page.evaluate(
            """() => {
              const v = document.querySelector('video');
              if (!v) return { known: false, playing: false, paused: true };
              return {
                known: true,
                playing: !v.paused && !v.ended && v.readyState > 2,
                paused: !!v.paused,
                currentTime: v.currentTime || 0
              };
            }"""
        )
        if isinstance(pstate, dict):
            player.update(pstate)
            player["on_watch"] = _url_is_watch(url)
    except Exception:
        pass

    try:
        from voxoryl.perception import get_perception

        get_perception().note_browser(url=url, title=title)
        if videos:
            get_perception().note_ui_tree(
                [{"role": "link", "name": v["title"], "ref": f"yt{v['index']}", "href": v["href"]} for v in videos],
                source="dom",
            )
    except Exception:
        pass

    return {
        "ok": True,
        "url": url,
        "title": title,
        "videos": videos,
        "player": player,
    }


async def verify_youtube_watch(page: Any, *, timeout_ms: int = 4500) -> dict[str, Any]:
    """Postcondition: URL is /watch (or shorts). Playing preferred but URL is the hard gate."""
    import time as _time

    deadline = _time.perf_counter() + max(0.2, timeout_ms / 1000.0)
    last: dict[str, Any] = {}
    while _time.perf_counter() < deadline:
        last = await observe_youtube_page(page)
        url = str(last.get("url") or "")
        if _url_is_watch(url):
            try:
                from voxoryl.perception import get_perception

                get_perception().note_browser(url=url, title=str(last.get("title") or ""))
            except Exception:
                pass
            return {
                "ok": True,
                "url": url,
                "title": last.get("title"),
                "player": last.get("player"),
                "verified": True,
            }
        await asyncio.sleep(0.15)
    return {
        "ok": False,
        "url": last.get("url"),
        "title": last.get("title"),
        "player": last.get("player"),
        "verified": False,
        "error": "watch_not_reached",
    }


def claim_already_playing_allowed(obs: dict[str, Any] | None) -> bool:
    """Honesty gate: only claim 'already playing' when watch URL + player verified."""
    if not obs:
        return False
    player = obs.get("player") or {}
    url = str(obs.get("url") or "")
    if not _url_is_watch(url):
        return False
    if player.get("known") and player.get("playing"):
        return True
    # On /watch with unknown player — still do not invent "already playing" for pause offers
    return False


async def _click_nth_tile(page: Any, index: int) -> dict[str, Any]:
    """Click 1-based video tile via structured DOM. Prefer observed href/title."""
    obs = await observe_youtube_page(page)
    videos = obs.get("videos") or []
    if len(videos) >= index:
        target = videos[index - 1]
        href = str(target.get("href") or "")
        title = str(target.get("title") or "")
        # Grounded click: match the exact observed href (query-normalized)
        if href:
            try:
                # Match watch?v=ID even when locator has extra query params
                vid = ""
                m = re.search(r"[?&]v=([\w-]{6,})", href) or re.search(r"/shorts/([\w-]{6,})", href)
                if m:
                    vid = m.group(1)
                if vid:
                    loc = page.locator(f'a[href*="v={vid}"], a[href*="/shorts/{vid}"]')
                    if await loc.count() > 0:
                        await loc.first.scroll_into_view_if_needed(timeout=2000)
                        await loc.first.click(timeout=4000)
                        return {
                            "ok": True,
                            "method": "href_grounded",
                            "index": index,
                            "title": title,
                            "href": href,
                            "observation": obs,
                        }
                await page.goto(href, wait_until="domcontentloaded", timeout=12_000)
                return {
                    "ok": True,
                    "method": "goto_href",
                    "index": index,
                    "title": title,
                    "href": href,
                    "observation": obs,
                }
            except Exception as exc:
                return {"ok": False, "error": str(exc), "observation": obs, "index": index}

    for sel in _YT_TILE_SELECTORS:
        try:
            loc = page.locator(sel)
            count = await loc.count()
            if count >= index:
                await loc.nth(index - 1).scroll_into_view_if_needed(timeout=2000)
                await loc.nth(index - 1).click(timeout=4000)
                return {
                    "ok": True,
                    "method": "selector_nth",
                    "index": index,
                    "selector": sel,
                    "observation": obs,
                }
        except Exception:
            continue
    return {
        "ok": False,
        "error": "tile_not_found",
        "index": index,
        "videos_found": len(videos),
        "observation": obs,
    }


async def _foreground_window_title() -> str:
    try:
        import ctypes

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value or ""
    except Exception:
        return ""


async def verify_watch_heuristic(*, before_title: str = "", timeout_ms: int = 4500) -> dict[str, Any]:
    """
    Verify play without requiring a Playwright page object.
    Prefer live CDP URL; else window-title change toward a watch page; else soft VL ask.
    Never returns ok=True on guesswork alone without evidence.
    """
    import time as _time

    from voxoryl.wait_for import _live_browser_url

    deadline = _time.perf_counter() + max(0.2, timeout_ms / 1000.0)
    last_url = ""
    last_title = before_title or ""
    while _time.perf_counter() < deadline:
        live = await _live_browser_url()
        if live:
            last_url = live
            if _url_is_watch(live):
                try:
                    from voxoryl.perception import get_perception

                    get_perception().note_browser(url=live)
                except Exception:
                    pass
                return {"ok": True, "url": live, "method": "cdp_url", "verified": True}
        title = await _foreground_window_title()
        last_title = title or last_title
        # YouTube watch titles usually: "<Video title> - YouTube"
        low = title.lower()
        if "youtube" in low and " - youtube" in low:
            # Home is often just "YouTube" or "YouTube - ..."
            if low.strip() not in {"youtube", "youtube - google chrome"} and "search" not in low:
                if before_title and title.strip() == before_title.strip():
                    pass  # unchanged — keep waiting
                else:
                    return {
                        "ok": True,
                        "url": last_url,
                        "title": title,
                        "method": "window_title",
                        "verified": True,
                    }
        await asyncio.sleep(0.2)

    # Soft VL evidence only if computer use on — must be affirmative
    if computer_use_gate_ok():
        try:
            from voxoryl.screen import tool_screen

            look = await tool_screen(
                action="look",
                question=(
                    "Is a YouTube video player (watch page) visibly open and playing or ready "
                    "to play right now? Reply YES or NO, then one short reason."
                ),
            )
            desc = str((look.get("vision") or {}).get("description") or look.get("speak") or "").lower()
            if desc.strip().startswith("yes") or re.search(r"\byes\b.*\b(play|watch|video player)\b", desc):
                # Title must also look like watch — VL alone is weak; require corroboration
                title = await _foreground_window_title()
                if "youtube" in title.lower() and " - youtube" in title.lower():
                    return {
                        "ok": True,
                        "url": last_url,
                        "title": title,
                        "method": "vl_title",
                        "verified": True,
                        "vl": desc[:200],
                    }
            return {
                "ok": False,
                "url": last_url,
                "title": last_title,
                "method": "vl_negative",
                "verified": False,
                "vl": desc[:200],
            }
        except Exception as exc:
            return {
                "ok": False,
                "url": last_url,
                "title": last_title,
                "verified": False,
                "error": str(exc),
            }

    return {
        "ok": False,
        "url": last_url,
        "title": last_title,
        "verified": False,
        "error": "watch_not_verified",
    }


async def play_nth_youtube_via_coords(index: int = 1, *, message: str = "") -> dict[str, Any]:
    """
    Coordinate path: observe (UIA/CDP-bbox/VL) → click_xy → verify.
    Works without CDP; CDP is an optional accelerator for bbox + URL verify.
    """
    index = max(1, min(20, int(index or 1)))
    if not computer_use_gate_ok():
        return {
            "ok": False,
            "method": "coords",
            "index": index,
            "verified": False,
            "speak": _honest_play_fail_speak(
                index,
                reason="Computer use is off — enable COMPUTER_USE_ENABLED for screen clicks.",
            ),
            "action_only": True,
        }

    await asyncio.to_thread(focus_window_by_title, "Chrome", "Google Chrome", "YouTube")
    await asyncio.sleep(0.15)

    from voxoryl.point_executor import click_xy_and_verify, ensure_dpi_aware
    from voxoryl.screen_grounding import ground_nth_video
    from voxoryl.wait_for import wait_for_youtube_ready

    # Ready gate: never ground/click until YouTube content is actually visible
    ready = await wait_for_youtube_ready(timeout_ms=25_000)
    if not ready.get("ok"):
        learn("chrome", action="play_nth_youtube", detail=f"n={index} coords_not_ready", ok=False)
        return {
            "ok": False,
            "method": "coords",
            "index": index,
            "ready": ready,
            "verified": False,
            "speak": _honest_play_fail_speak(
                index,
                reason="YouTube wasn't ready — page/tiles never loaded.",
            ),
            "action_only": True,
        }

    before_title = await _foreground_window_title()
    ensure_dpi_aware()
    grounded = await ground_nth_video(index, allow_vl=True)
    if not grounded.get("ok"):
        learn("chrome", action="play_nth_youtube", detail=f"n={index} coords_miss", ok=False)
        return {
            "ok": False,
            "method": "coords",
            "index": index,
            "source": (grounded.get("observation") or {}).get("source"),
            "observation": grounded.get("observation"),
            "ready": ready,
            "verified": False,
            "speak": _honest_play_fail_speak(index, reason="Couldn't find that video on screen."),
            "action_only": True,
        }

    x, y = grounded.get("x"), grounded.get("y")
    source = str(grounded.get("source") or "")

    async def _verify() -> dict[str, Any]:
        return await verify_watch_heuristic(before_title=before_title, timeout_ms=4500)

    acted = await click_xy_and_verify(float(x), float(y), verify=_verify, settle_ms=500)
    verified = bool(acted.get("verified"))
    url = str((acted.get("verify") or {}).get("url") or "")
    log_line = f"n={index} source={source} x={x} y={y} verified={verified}"
    learn("chrome", action="play_nth_youtube", detail=log_line, ok=verified)
    try:
        import logging

        logging.getLogger("voxoryl.chrome").info("play_nth_coords %s", log_line)
    except Exception:
        pass

    return {
        "ok": verified,
        "method": "coords",
        "index": index,
        "source": source,
        "x": x,
        "y": y,
        "label": grounded.get("label"),
        "confidence": grounded.get("confidence"),
        "clicked": bool(acted.get("clicked")),
        "verified": verified,
        "url": url,
        "verify": acted.get("verify"),
        "observation": grounded.get("observation"),
        # Silence on verified action_only; honest short fail otherwise — never "done playing"
        "speak": "" if verified else _honest_play_fail_speak(
            index,
            reason="Clicked but couldn't verify the video opened.",
        ),
        "action_only": True,
    }


async def play_nth_youtube_video(index: int = 1, *, message: str = "") -> dict[str, Any]:
    """
    Observe → resolve ordinal → click grounded tile → verify /watch.
    Prefer CDP DOM when available; always fall back to coordinate grounding (UIA/VL).
    Never report success without verify.
    """
    index = max(1, min(20, int(index or 1)))
    await asyncio.to_thread(focus_window_by_title, "Chrome", "Google Chrome", "YouTube")
    await asyncio.sleep(0.15)

    from voxoryl.wait_for import wait_for_youtube_ready

    cdp_err = "cdp_miss"
    try:
        from playwright.async_api import async_playwright  # type: ignore

        async with async_playwright() as p:
            try:
                browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            except Exception as exc:
                browser = None
                cdp_err = str(exc)
            if browser is not None:
                page = await _cdp_youtube_page(browser)
                if page is None:
                    # Fall through to coordinate path
                    cdp_err = "no_chrome_tab"
                else:
                    # Stay on existing YouTube tab; only navigate if not on YouTube at all
                    if "youtube.com" not in (page.url or "").lower():
                        await page.goto(
                            "https://www.youtube.com",
                            wait_until="domcontentloaded",
                            timeout=12_000,
                        )
                    else:
                        try:
                            await page.bring_to_front()
                        except Exception:
                            pass
                    # Event/condition wait for tiles — not blind sleep
                    ready = await wait_for_youtube_ready(timeout_ms=20_000)
                    if not ready.get("ok"):
                        # Honest fail before clicking
                        return {
                            "ok": False,
                            "method": "playwright_cdp",
                            "index": index,
                            "ready": ready,
                            "verified": False,
                            "speak": _honest_play_fail_speak(
                                index,
                                reason="YouTube wasn't ready — feed never loaded.",
                            ),
                            "action_only": True,
                        }
                    try:
                        await page.wait_for_selector(
                            "a#video-title-link, a#video-title, ytd-rich-item-renderer",
                            timeout=8_000,
                            state="visible",
                        )
                    except Exception:
                        pass  # ready gate already passed via URL/title/tiles

                    # Observe → maybe already on watch (only for pause intents — never for play-nth)
                    obs0 = await observe_youtube_page(page)
                    if wants_play_or_pause_media(message) and not parse_video_ordinal(message):
                        if claim_already_playing_allowed(obs0):
                            return {
                                "ok": True,
                                "already_playing": True,
                                "url": obs0.get("url"),
                                "verified": True,
                                "method": "observe",
                                "speak": "",
                                "action_only": True,
                                "observation": {
                                    "url": obs0.get("url"),
                                    "title": obs0.get("title"),
                                    "player": obs0.get("player"),
                                    "video_count": len(obs0.get("videos") or []),
                                },
                            }
                        return {
                            "ok": False,
                            "already_playing": False,
                            "verified": False,
                            "url": obs0.get("url"),
                            "method": "observe",
                            "speak": "Nothing looks like it's playing on YouTube.",
                            "action_only": True,
                        }

                    async def _attempt() -> dict[str, Any]:
                        obs = await observe_youtube_page(page)
                        act = await _click_nth_tile(page, index)
                        if not act.get("ok"):
                            return {
                                "ok": False,
                                "act": act,
                                "observation": obs,
                                "verified": False,
                            }
                        verify = await verify_youtube_watch(page, timeout_ms=4500)
                        return {
                            "ok": bool(verify.get("ok")),
                            "act": act,
                            "verify": verify,
                            "observation": obs,
                            "verified": bool(verify.get("ok")),
                            "url": verify.get("url") or obs.get("url"),
                        }

                    first = await _attempt()
                    result = first
                    if not first.get("ok"):
                        try:
                            await page.mouse.wheel(0, 400)
                        except Exception:
                            pass
                        await page.wait_for_timeout(600)
                        result = await _attempt()
                        result["replanned"] = True
                        result["first_attempt"] = {
                            "ok": first.get("ok"),
                            "error": (first.get("act") or {}).get("error")
                            or (first.get("verify") or {}).get("error"),
                        }

                    ok = bool(result.get("ok")) and bool(result.get("verified"))
                    url = str(result.get("url") or "")

                    # Soft coordinate replan if DOM click failed (CDP still verifies URL)
                    if not ok and computer_use_gate_ok():
                        try:
                            coords = await play_nth_youtube_via_coords(index, message=message)
                            if coords.get("verified"):
                                return coords
                            # Prefer honest coords result over stale CDP miss
                            if coords.get("clicked"):
                                verify = await verify_youtube_watch(page, timeout_ms=2500)
                                if verify.get("ok"):
                                    coords["ok"] = True
                                    coords["verified"] = True
                                    coords["url"] = verify.get("url")
                                    coords["method"] = "coords_cdp_verify"
                                    coords["speak"] = ""
                                    coords["source"] = coords.get("source") or "vl"
                                    return coords
                            # keep going to return coords failure below if DOM also failed
                            if not result.get("act", {}).get("ok"):
                                return coords
                        except Exception:
                            pass

                    learn(
                        "chrome",
                        action="play_nth_youtube",
                        detail=f"n={index} url={url} verified={ok}",
                        ok=ok,
                    )
                    slim_obs = result.get("observation") or {}
                    return {
                        "ok": ok,
                        "url": url,
                        "index": index,
                        "method": "playwright_cdp",
                        "source": "cdp",
                        "verified": ok,
                        "replanned": bool(result.get("replanned")),
                        "clicked_title": ((result.get("act") or {}).get("title")),
                        "video_count": len(slim_obs.get("videos") or []),
                        "speak": "" if ok else _honest_play_fail_speak(index),
                        "action_only": True,
                    }
    except Exception as exc:
        cdp_err = str(exc)

    # Coordinate path — works without CDP (screenshot + UIA and/or VL + heuristic verify)
    coords = await play_nth_youtube_via_coords(index, message=message)
    coords.setdefault("cdp_error", cdp_err)
    return coords


async def play_first_youtube_video() -> dict[str, Any]:
    """Click/play the first video — thin wrapper over play_nth (verified)."""
    return await play_nth_youtube_video(1)


async def run_youtube_play_pipeline(message: str) -> dict[str, Any]:
    """
    Deterministic multi-step: optional Chrome+profile → YouTube → play Nth video.
    Continues after partial success (e.g. Chrome opened to blank New Tab).
    Success requires verified /watch when play was requested.
    """
    lower = (message or "").lower()
    chromeish = any(k in lower for k in ("chrome", "browser", "google chrome"))
    openish = any(k in lower for k in ("open", "launch", "start", "khol", "kholo"))
    profileish = _profileish(message)
    this_window = _wants_this_window(message)
    ordinal = parse_video_ordinal(message)
    wants_play = ordinal is not None
    play_index = ordinal or 1
    wants_yt = _wants_youtube(message) or wants_play  # play-nth on open YT still counts

    results: dict[str, Any] = {"steps": []}
    opened: dict[str, Any] | None = None

    # Open Chrome + profile when asked (owner profile phrases = profile, never a Google query)
    if profileish or (chromeish and openish and not this_window):
        opened = await open_chrome_with_profile(message)
        results["steps"].append({"open_chrome": opened})
        results["opened"] = opened
        # Continue even on partial success (window up, picker uncertain)
        from voxoryl.wait_for import wait_for_condition

        await wait_for_condition("window_title_contains('Chrome')", timeout_ms=8_000)

    # Only navigate to YouTube if explicitly asked OR we need YT but aren't already there.
    # "play the second video" with YT already open → skip new-tab/omnibox open.
    if wants_yt and (_wants_youtube(message) or profileish or (chromeish and openish) or this_window):
        in_existing = this_window or bool(opened) or wants_play
        nav = await open_youtube(new_tab=not in_existing, in_existing=in_existing)
        if not nav.get("ok") and in_existing:
            nav = await open_youtube(new_tab=True, in_existing=False)
        results["steps"].append({"open_youtube": nav})
        results["nav"] = nav
        # open_youtube already waits for ready — fail honestly before play if not ready
        if wants_play and not nav.get("ok"):
            return {
                "ok": False,
                "pipeline": "youtube_play",
                "speak": str(nav.get("speak") or "YouTube didn't finish loading."),
                "action_only": True,
                "nav_ok": False,
                "play_ok": False,
                "steps": results["steps"],
                "nav": nav,
            }
    elif wants_play and not _wants_youtube(message):
        # Assume YouTube is already the active context — still gate on content ready
        from voxoryl.wait_for import wait_for_youtube_ready

        ready = await wait_for_youtube_ready(timeout_ms=20_000)
        results["nav"] = {
            "ok": bool(ready.get("ok")),
            "skipped": True,
            "reason": "play_in_existing_tab",
            "ready": ready,
        }
        if not ready.get("ok"):
            return {
                "ok": False,
                "pipeline": "youtube_play",
                "speak": "YouTube wasn't ready — page/tiles never loaded.",
                "action_only": True,
                "nav_ok": False,
                "play_ok": False,
                "steps": results.get("steps") or [],
                "nav": results["nav"],
            }

    play: dict[str, Any] | None = None
    if wants_play:
        play = await play_nth_youtube_video(play_index, message=message)
        results["steps"].append({"play_nth": play})
        results["play"] = play

    nav_ok = bool((results.get("nav") or {}).get("ok", False)) if results.get("nav") is not None else True
    # Hard honesty: play success requires verified postcondition — never default True
    play_ok = False
    if wants_play:
        play_ok = bool((play or {}).get("ok")) and bool((play or {}).get("verified", (play or {}).get("ok")))
        if play and play.get("ok") and play.get("verified") is False:
            play_ok = False
    else:
        play_ok = True
    # Chrome open soft-ok if window launched even when picker flaky
    open_soft = True
    if opened is not None:
        open_soft = bool(opened.get("ok")) or bool((opened.get("launched") or {}).get("ok"))

    ok = open_soft and nav_ok and play_ok
    speak = ""
    if not ok:
        bits = []
        for key in ("opened", "nav", "play"):
            part = results.get(key) or {}
            if part and not part.get("ok") and part.get("speak"):
                bits.append(str(part["speak"]))
        speak = " ".join(bits).strip() or "Couldn't finish that browser task."

    learn(
        "chrome",
        action="youtube_play_pipeline",
        detail=f"yt={wants_yt} play={wants_play} n={play_index if wants_play else '-'} profile={profileish} ok={ok}",
        ok=ok,
    )
    return {
        "ok": ok,
        "pipeline": "youtube_play",
        "speak": speak,
        "action_only": True,
        "opened_ok": open_soft if opened is not None else None,
        "nav_ok": nav_ok if results.get("nav") is not None else None,
        "nav_url": (results.get("nav") or {}).get("url"),
        "play_ok": play_ok if wants_play else None,
        "play_index": play_index if wants_play else None,
        "play_method": (play or {}).get("method"),
        "play_source": (play or {}).get("source"),
        "play_x": (play or {}).get("x"),
        "play_y": (play or {}).get("y"),
        "verified": bool((play or {}).get("verified")) if wants_play else None,
        "url": (play or {}).get("url"),
    }


def classify_chrome_intent(message: str) -> str | None:
    """
    Returns: youtube_play | open_profile | new_tab | goto_tab | open_and_nav | nav_here | None
    """
    lower = (message or "").lower().strip()
    if not lower:
        return None

    # Never treat non-browser app tab requests as Chrome
    from voxoryl.windows_ops import detect_app_name, is_new_tab_app_request

    if is_new_tab_app_request(message):
        return None
    app = detect_app_name(message)
    if app and app not in {"chrome", "edge", "firefox"} and re.search(r"\b(?:new\s+)?tab\b", lower):
        return None

    profileish = _profileish(message)
    chromeish = any(k in lower for k in ("chrome", "browser", "google chrome"))
    openish = any(k in lower for k in ("open", "launch", "start", "select", "use", "switch", "click", "khol", "kholo"))
    wants_yt = _wants_youtube(message)
    wants_play = _wants_play_video(message)
    this_window = _wants_this_window(message)

    # Play Nth video on (assumed) open YouTube — must not fall through to LLM lies
    if wants_play:
        return "youtube_play"

    # Compound YouTube (+ optional profile) — never stop after blank New Tab
    if wants_yt and (profileish or (chromeish and openish) or this_window):
        return "youtube_play"
    if wants_yt and wants_play:
        return "youtube_play"

    if chromeish and profileish and not wants_yt:
        return "open_profile"
    if profileish and openish and not wants_yt:
        return "open_profile"
    if chromeish and openish and ("profile" in lower or " id" in lower or _profileish(message)) and not wants_yt:
        return "open_profile"

    # go to / switch to existing tab
    if any(
        k in lower
        for k in (
            "go to tab",
            "switch to tab",
            "switch tab",
            "goto tab",
            "existing tab",
            "open tab with",
        )
    ) or (
        any(k in lower for k in ("go to ", "switch to ", "goto "))
        and any(k in lower for k in ("youtube", "gmail", "tab", "already open", "open tab"))
    ):
        if wants_yt and wants_play:
            return "youtube_play"
        if "new tab" in lower or "open a new" in lower:
            return "new_tab"
        if this_window and wants_yt:
            return "nav_here"
        return "goto_tab"

    # open site → new tab (openai, apple, youtube as navigation)
    if any(k in lower for k in ("open openai", "open chatgpt", "open apple", "open youtube", "open github", "open gmail")):
        if wants_yt and (wants_play or this_window):
            return "youtube_play"
        if this_window:
            return "nav_here"
        return "new_tab"
    # "open my personal Gmail" / "open personal gmail" — profile then navigate
    if "gmail" in lower and any(k in lower for k in ("open", "launch", "go to", "goto", "show")):
        if any(k in lower for k in ("personal", "my gmail", "my personal")) or _profileish(message):
            return "open_and_nav"
        return "new_tab"
    if re.search(r"\b(open|visit|navigate to)\s+[a-z0-9.-]+\.[a-z]{2,}\b", lower):
        return "new_tab"
    if re.search(r"\bnew tab\b", lower):
        # Blank new tab, or new tab + site token — never junk residuals
        return "new_tab"
    if chromeish and re.search(r"\b(open|go to|visit)\s+\w+", lower) and not profileish:
        # "open chrome and go to youtube" → full nav pipeline
        if "and" in lower and "chrome" in lower:
            if wants_yt:
                return "youtube_play"
            if profileish:
                return "open_profile"
            return "open_and_nav"
        return "new_tab"

    return None


async def tool_chrome(action: str = "auto", message: str = "") -> dict[str, Any]:
    action = (action or "auto").lower().strip()
    msg = message or ""

    if action == "auto":
        intent = classify_chrome_intent(msg)
        if not intent:
            # Only default to open_profile when message clearly asks for chrome/profile control
            lower = msg.lower()
            if any(k in lower for k in ("chrome", "browser", "profile")) and any(
                k in lower for k in ("open", "launch", "start", "select", "click", "use", "switch")
            ):
                intent = "open_profile"
            else:
                return {
                    "ok": False,
                    "speak": "Say something like 'open Chrome and select my profile'.",
                    "action": action,
                }
        action = intent

    if action in {"youtube_play", "open_youtube", "play_first_youtube", "play_nth_youtube", "youtube_play_full"}:
        # Full compound path (open/profile/nav/play as needed)
        if action == "open_youtube" and not _wants_play_video(msg) and not _profileish(msg):
            return await open_youtube(in_existing=_wants_this_window(msg))
        if action in {"play_first_youtube", "play_nth_youtube"} and not _wants_youtube(msg):
            n = parse_video_ordinal(msg) or 1
            return await play_nth_youtube_video(n, message=msg)
        return await run_youtube_play_pipeline(msg)

    if action in {"open", "open_profile", "profile"}:
        return await open_chrome_with_profile(msg)

    if action in {"nav_here"}:
        token = _extract_site_token(msg) or ("youtube" if _wants_youtube(msg) else None)
        if not token:
            return {
                "ok": False,
                "speak": "Couldn't tell what to open in the browser.",
                "action_only": True,
            }
        if token == "youtube":
            return await open_youtube(in_existing=True)
        url = resolve_site("chrome", token) or token
        await asyncio.to_thread(focus_window_by_title, "Chrome", "Google Chrome")
        steps = [
            await asyncio.to_thread(_hotkey, "ctrl", "l"),
        ]
        await asyncio.sleep(0.12)
        steps.append(await asyncio.to_thread(_paste, url if url.startswith("http") else f"https://{url}", enter=True))
        ok = all(s.get("ok", True) for s in steps)
        return {"ok": ok, "url": url, "speak": "" if ok else "Couldn't navigate.", "action_only": True}

    if action in {"open_and_nav"}:
        opened = await open_chrome_with_profile(msg)
        token = _extract_site_token(msg)
        # Continue remaining steps even if profile click was soft-fail but Chrome launched
        chrome_up = bool(opened.get("ok")) or bool((opened.get("launched") or {}).get("ok"))
        if token and "chrome" not in token and chrome_up:
            if token == "youtube":
                nav = await open_youtube(in_existing=True)
            else:
                nav = await chrome_new_tab_url(token)
            speak = ""
            if not (chrome_up and nav.get("ok")):
                speak = f"{opened.get('speak') or ''} {nav.get('speak') or ''}".strip() or "Couldn't open that."
            out = {
                "ok": chrome_up and bool(nav.get("ok")),
                "opened": opened,
                "nav": nav,
                "speak": speak,
                "action_only": True,
            }
            if _wants_play_video(msg) and nav.get("ok"):
                from voxoryl.wait_for import wait_for_youtube_ready

                ready = await wait_for_youtube_ready(timeout_ms=20_000)
                out["ready"] = ready
                if not ready.get("ok"):
                    out["ok"] = False
                    out["speak"] = (speak + " YouTube wasn't ready for play.").strip()
                    return out
                n = parse_video_ordinal(msg) or 1
                play = await play_nth_youtube_video(n, message=msg)
                out["play"] = play
                out["ok"] = out["ok"] and bool(play.get("ok")) and bool(play.get("verified", play.get("ok")))
                if not out["ok"]:
                    out["speak"] = (speak + " " + str(play.get("speak") or "")).strip()
            return out
        return opened

    if action in {"new_tab", "open_url", "navigate"}:
        # YouTube + play should not stop at blank/new tab
        if _wants_youtube(msg) and (_wants_play_video(msg) or _wants_this_window(msg)):
            return await run_youtube_play_pipeline(msg)
        token = _extract_site_token(msg)
        if token:
            token = re.sub(r"^(open|go to|goto|visit|navigate to)\s+", "", token, flags=re.I).strip()
            if token == "youtube" and _wants_this_window(msg):
                return await open_youtube(in_existing=True)
            return await chrome_new_tab_url(token)
        # Blank new tab — Ctrl+T only, no Google invent
        if re.search(r"\bnew\s+tab\b", (msg or "").lower()):
            await asyncio.to_thread(focus_window_by_title, "Chrome", "Google Chrome")
            step = await asyncio.to_thread(_hotkey, "ctrl", "t")
            ok = bool(step.get("ok", True))
            learn("chrome", action="new_tab", shortcut=["ctrl", "t"], ok=ok)
            return {"ok": ok, "speak": "" if ok else "Couldn't open a new tab.", "action_only": True}
        # Refuse using the raw utterance as a search query
        if _is_junk_nav_token(msg) or not _looks_like_search_query(msg):
            return {
                "ok": False,
                "speak": "Couldn't tell what to open in the browser.",
                "action_only": True,
            }
        return await chrome_new_tab_url(msg)

    if action in {"goto_tab", "switch_tab", "tab"}:
        if _wants_youtube(msg) and _wants_play_video(msg):
            return await run_youtube_play_pipeline(msg)
        token = _extract_site_token(msg) or msg
        token = re.sub(
            r"^(go to|goto|switch to|switch tab to|open tab)\s+",
            "",
            token,
            flags=re.I,
        ).strip()
        return await chrome_goto_tab(token)

    return {"ok": False, "speak": f"Unknown chrome action '{action}'.", "action": action}
