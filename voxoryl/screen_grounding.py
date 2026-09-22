"""Screen grounding — observe UI → absolute screen (x, y) targets.

Priority (structured first; VL only when needed):
  1. UIA / accessibility hit-test → element center
  2. Browser DOM/ARIA via CDP (:9222) → bbox center (+ window client origin)
  3. Vision/VL locate → screenshot coords scaled to absolute screen

Never invent success; callers must verify after click.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from voxoryl.point_executor import clamp_xy, ensure_dpi_aware, screen_size

log = logging.getLogger("voxoryl.screen_grounding")


@dataclass
class GroundedTarget:
    label: str
    x: int
    y: int
    confidence: float
    source: str  # uia | cdp | vl
    role: str = ""
    bbox: tuple[int, int, int, int] | None = None  # left, top, right, bottom
    href: str = ""
    index: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.bbox is not None:
            d["bbox"] = list(self.bbox)
        return d


def capture_screen_full() -> dict[str, Any]:
    """Full-resolution primary-monitor screenshot (no VL downscale)."""
    ensure_dpi_aware()
    from voxoryl.screen import computer_use_enabled
    from voxoryl.config import settings
    from datetime import datetime, timezone
    from pathlib import Path

    if not computer_use_enabled():
        return {"ok": False, "error": "Computer use is disabled"}
    try:
        from PIL import ImageGrab
    except ImportError:
        return {"ok": False, "error": "Pillow not installed"}

    shot_dir = settings.voxoryl_data_dir / "screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = shot_dir / f"ground-{stamp}.png"
    img = ImageGrab.grab()
    img.save(path, format="PNG", optimize=True)
    sw, sh = screen_size()
    return {
        "ok": True,
        "path": str(path.resolve()),
        "width": int(img.width),
        "height": int(img.height),
        "screen_w": sw,
        "screen_h": sh,
        "at": datetime.now(timezone.utc).isoformat(),
    }


def shot_xy_to_screen(x: float, y: float, *, shot_w: int, shot_h: int) -> tuple[int, int] | None:
    """Map screenshot-pixel coords → absolute screen coords (handles VL downscale)."""
    ensure_dpi_aware()
    sw, sh = screen_size()
    if shot_w <= 0 or shot_h <= 0:
        return clamp_xy(x, y)
    # Prefer mapping via live grab size if it differs from reported shot size
    try:
        from PIL import ImageGrab

        full = ImageGrab.grab()
        fw, fh = int(full.width), int(full.height)
    except Exception:
        fw, fh = sw, sh
    sx = float(x) * fw / float(shot_w)
    sy = float(y) * fh / float(shot_h)
    return clamp_xy(sx, sy)


# --- UIA -----------------------------------------------------------------


def _uia_targets(*, name_filter: str = "", max_nodes: int = 80) -> list[GroundedTarget]:
    """Enumerate foreground-window UIA elements with screen centers."""
    import sys

    if not sys.platform.startswith("win"):
        return []
    ensure_dpi_aware()
    targets: list[GroundedTarget] = []
    # Prefer optional uiautomation package
    try:
        import uiautomation as auto  # type: ignore

        root = auto.GetRootControl()
        fg = auto.GetForegroundControl() or root
        needle = (name_filter or "").lower().strip()
        for ctrl, _depth in auto.WalkControl(fg, maxDepth=12):
            try:
                name = (ctrl.Name or "").strip()
                if not name:
                    continue
                if needle and needle not in name.lower() and needle not in (ctrl.ControlTypeName or "").lower():
                    # keep video-ish / link-ish when filtering for youtube
                    if needle in {"youtube", "video", "thumbnail"}:
                        if not any(k in name.lower() for k in ("video", "play", "watch", "youtube", "•", "ago")):
                            ct = (ctrl.ControlTypeName or "").lower()
                            if "hyperlink" not in ct and "listitem" not in ct and "button" not in ct:
                                continue
                    else:
                        continue
                rect = ctrl.BoundingRectangle
                if not rect or rect.width() < 8 or rect.height() < 8:
                    continue
                cx = int(rect.left + rect.width() / 2)
                cy = int(rect.top + rect.height() / 2)
                pt = clamp_xy(cx, cy)
                if pt is None:
                    continue
                role = str(ctrl.ControlTypeName or "element")
                targets.append(
                    GroundedTarget(
                        label=name[:160],
                        x=pt[0],
                        y=pt[1],
                        confidence=0.9,
                        source="uia",
                        role=role,
                        bbox=(int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)),
                    )
                )
                if len(targets) >= max_nodes:
                    break
            except Exception:
                continue
        return targets
    except Exception as exc:
        log.debug("uiautomation unavailable: %s", exc)

    # Lightweight COM fallback via comtypes if present
    try:
        import comtypes  # noqa: F401
        import comtypes.client  # type: ignore

        uia = comtypes.client.CreateObject("{ff48dba4-60ef-4201-aa87-54103eef594e}")  # CUIAutomation
        # Too brittle without type libs — skip deep walk
        _ = uia
    except Exception:
        pass
    return targets


def _chrome_client_origin() -> tuple[int, int] | None:
    """Screen coords of Chrome's client-area top-left (for CDP CSS → screen)."""
    ensure_dpi_aware()
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = (buf.value or "").lower()
        if "chrome" not in title and "youtube" not in title:
            # Still try — user may have focused Chrome with other title
            pass
        pt = wintypes.POINT(0, 0)
        if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
            return None
        return int(pt.x), int(pt.y)
    except Exception:
        return None


# --- CDP -----------------------------------------------------------------


async def _cdp_youtube_video_targets(max_n: int = 24) -> list[GroundedTarget]:
    """If Chrome CDP :9222 is up, return video tile centers in screen coords."""
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except Exception:
        return []

    origin = _chrome_client_origin()
    try:
        async with async_playwright() as p:
            try:
                browser = await p.chromium.connect_over_cdp("http://127.0.0.1:9222")
            except Exception:
                return []
            page = None
            for ctx in browser.contexts or []:
                for pg in getattr(ctx, "pages", []) or []:
                    if "youtube.com" in (getattr(pg, "url", "") or "").lower():
                        page = pg
                        break
                if page:
                    break
            if page is None:
                for ctx in browser.contexts or []:
                    pages = getattr(ctx, "pages", []) or []
                    if pages:
                        page = pages[0]
                        break
            if page is None:
                return []

            raw = await page.evaluate(
                """(maxN) => {
                  const sels = [
                    'ytd-rich-item-renderer:not([is-slim-media]) a#video-title-link',
                    'ytd-rich-grid-media a#video-title-link',
                    'ytd-video-renderer a#video-title',
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
                      if (a.closest('ytd-rich-shelf-renderer, ytd-reel-shelf-renderer')) continue;
                      // Prefer thumbnail area above title when present
                      let el = a;
                      const item = a.closest('ytd-rich-item-renderer, ytd-video-renderer, ytd-rich-grid-media');
                      const thumb = item && item.querySelector('#thumbnail, a#thumbnail, ytd-thumbnail');
                      if (thumb) el = thumb;
                      const r = el.getBoundingClientRect();
                      if (r.width < 40 || r.height < 24) continue;
                      seen.add(href);
                      const t = (a.getAttribute('title') || a.textContent || '').trim().replace(/\\s+/g, ' ');
                      out.push({
                        title: t.slice(0, 120),
                        href,
                        x: r.x + r.width / 2,
                        y: r.y + r.height / 2,
                        left: r.x, top: r.y, right: r.x + r.width, bottom: r.y + r.height
                      });
                      if (out.length >= maxN) return out;
                    }
                    if (out.length >= 8) break;
                  }
                  return out;
                }""",
                max_n,
            )
            # Device pixel ratio for CSS → physical
            try:
                dpr = float(await page.evaluate("() => window.devicePixelRatio || 1"))
            except Exception:
                dpr = 1.0
            ox, oy = origin or (0, 0)
            # If origin unknown, approximate via window metrics
            if origin is None:
                try:
                    metrics = await page.evaluate(
                        """() => ({
                          screenX: window.screenX,
                          screenY: window.screenY,
                          outerW: window.outerWidth,
                          innerW: window.innerWidth,
                          outerH: window.outerHeight,
                          innerH: window.innerHeight
                        })"""
                    )
                    chrome_x = int(metrics.get("screenX") or 0)
                    chrome_y = int(metrics.get("screenY") or 0)
                    chrome_w = int(metrics.get("outerW") or 0) - int(metrics.get("innerW") or 0)
                    chrome_h = int(metrics.get("outerH") or 0) - int(metrics.get("innerH") or 0)
                    # Rough chrome UI offset (left border ~0, top includes tab bar)
                    ox = chrome_x + max(0, chrome_w // 2)
                    oy = chrome_y + max(0, chrome_h)
                except Exception:
                    ox, oy = 0, 0

            out: list[GroundedTarget] = []
            for i, row in enumerate(raw or []):
                if not isinstance(row, dict):
                    continue
                # CSS pixels * dpr + client origin → physical screen
                cx = float(row.get("x") or 0) * dpr + ox
                cy = float(row.get("y") or 0) * dpr + oy
                # When origin came from ClientToScreen, CSS coords are already in client DIP;
                # ClientToScreen points are physical when DPI-aware — use CSS without extra dpr
                # if origin was from Win32 ClientToScreen (preferred).
                if origin is not None:
                    cx = float(row.get("x") or 0) + ox
                    cy = float(row.get("y") or 0) + oy
                pt = clamp_xy(cx, cy)
                if pt is None:
                    continue
                bbox = None
                try:
                    l = int(float(row.get("left") or 0) + ox)
                    t = int(float(row.get("top") or 0) + oy)
                    r = int(float(row.get("right") or 0) + ox)
                    b = int(float(row.get("bottom") or 0) + oy)
                    bbox = (l, t, r, b)
                except Exception:
                    bbox = None
                out.append(
                    GroundedTarget(
                        label=str(row.get("title") or f"video {i + 1}")[:160],
                        x=pt[0],
                        y=pt[1],
                        confidence=0.95,
                        source="cdp",
                        role="link",
                        bbox=bbox,
                        href=str(row.get("href") or ""),
                        index=i + 1,
                    )
                )
            return out
    except Exception as exc:
        log.debug("cdp grounding failed: %s", exc)
        return []


# --- VL ------------------------------------------------------------------


_VL_MULTI_PROMPT = """Locate clickable YouTube video thumbnails on this screenshot.
Return ONLY JSON:
{{"targets":[{{"label":"short title","x":123,"y":456,"confidence":0.0,"index":1}}]}}
- List normal feed videos left-to-right, top-to-bottom (skip Shorts / ads / sidebar).
- x,y = CENTER of each thumbnail in screenshot pixels (0..{width}-1, 0..{height}-1).
- index is 1-based order matching "first/second/third video".
- At most 8 targets. If none found: {{"targets":[]}}
"""


async def _vl_locate_targets(
    query: str,
    *,
    shot: dict[str, Any] | None = None,
    multi: bool = False,
) -> list[GroundedTarget]:
    from voxoryl.screen import capture_screenshot, vision_describe, vision_locate, computer_use_enabled
    from voxoryl.llm import parse_json_loose

    if not computer_use_enabled():
        return []
    if shot is None or not shot.get("ok"):
        # Use downscaled capture for VL (VRAM-friendly), then map back
        shot = await asyncio.to_thread(capture_screenshot)
    if not shot.get("ok"):
        return []
    path = str(shot["path"])
    w = int(shot.get("width") or 0)
    h = int(shot.get("height") or 0)

    if multi:
        prompt = _VL_MULTI_PROMPT.format(width=w, height=h)
        if query:
            prompt += f"\nFocus: {query[:200]}"
        vision = await vision_describe(path, prompt)
        if not vision.get("ok"):
            return []
        parsed = parse_json_loose(str(vision.get("description") or "")) or {}
        rows = parsed.get("targets") if isinstance(parsed, dict) else None
        if not isinstance(rows, list):
            rows = []
        out: list[GroundedTarget] = []
        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            try:
                x = float(row.get("x") or 0)
                y = float(row.get("y") or 0)
            except (TypeError, ValueError):
                continue
            if x <= 0 or y <= 0 or x >= w or y >= h:
                continue
            mapped = shot_xy_to_screen(x, y, shot_w=w, shot_h=h)
            if mapped is None:
                continue
            idx = row.get("index")
            try:
                idx_i = int(idx) if idx is not None else i + 1
            except (TypeError, ValueError):
                idx_i = i + 1
            out.append(
                GroundedTarget(
                    label=str(row.get("label") or f"video {idx_i}")[:160],
                    x=mapped[0],
                    y=mapped[1],
                    confidence=float(row.get("confidence") or 0.7),
                    source="vl",
                    role="thumbnail",
                    index=idx_i,
                    meta={"shot_x": int(x), "shot_y": int(y), "shot_w": w, "shot_h": h},
                )
            )
        return out

    loc = await vision_locate(path, query, width=w, height=h)
    if not loc.get("found"):
        return []
    mapped = shot_xy_to_screen(float(loc["x"]), float(loc["y"]), shot_w=w, shot_h=h)
    if mapped is None:
        return []
    return [
        GroundedTarget(
            label=str(loc.get("label") or query)[:160],
            x=mapped[0],
            y=mapped[1],
            confidence=float(loc.get("confidence") or 0.65),
            source="vl",
            role="target",
            meta={"shot_x": int(loc["x"]), "shot_y": int(loc["y"]), "shot_w": w, "shot_h": h},
        )
    ]


# --- Public observe API --------------------------------------------------


async def observe_screen(
    *,
    query: str = "",
    want_videos: bool = False,
    index: int | None = None,
    allow_vl: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Produce grounded targets with absolute screen coordinates.
    For YouTube nth-video: set want_videos=True and optional index.
    """
    ensure_dpi_aware()
    t0 = time.perf_counter()
    sources_tried: list[str] = []
    targets: list[GroundedTarget] = []

    if dry_run:
        # Deterministic mock for unit smoke — no OS input
        mock = [
            GroundedTarget(label="Mock video 1", x=400, y=350, confidence=0.99, source="uia", role="link", index=1),
            GroundedTarget(label="Mock video 2", x=700, y=350, confidence=0.99, source="uia", role="link", index=2),
            GroundedTarget(label="Mock video 3", x=1000, y=350, confidence=0.99, source="uia", role="link", index=3),
        ]
        return {
            "ok": True,
            "dry_run": True,
            "targets": [t.to_dict() for t in mock],
            "selected": mock[(index or 1) - 1].to_dict() if index else None,
            "sources_tried": ["dry_run"],
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }

    # 1) CDP accelerator for video tiles
    if want_videos or _looks_like_video_query(query):
        sources_tried.append("cdp")
        cdp = await _cdp_youtube_video_targets()
        if cdp:
            targets = cdp
            try:
                from voxoryl.perception import get_perception

                get_perception().note_ui_tree([t.to_dict() for t in targets], source="cdp")
            except Exception:
                pass

    # 2) UIA if CDP insufficient
    if len(targets) < (index or 1):
        sources_tried.append("uia")
        uia = await asyncio.to_thread(
            _uia_targets,
            name_filter="video" if want_videos else (query or ""),
            max_nodes=60,
        )
        if uia:
            # Prefer hyperlinks / list items for video selection
            if want_videos:
                videoish = [
                    t
                    for t in uia
                    if any(k in (t.label or "").lower() for k in ("ago", "view", "•", "youtube"))
                    or "hyperlink" in (t.role or "").lower()
                    or "listitem" in (t.role or "").lower()
                ]
                if videoish:
                    for i, t in enumerate(videoish):
                        t.index = i + 1
                    targets = videoish
                elif not targets:
                    for i, t in enumerate(uia):
                        t.index = i + 1
                    targets = uia
            elif not targets:
                targets = uia
            try:
                from voxoryl.perception import get_perception

                get_perception().note_ui_tree([t.to_dict() for t in targets[:40]], source="uia")
            except Exception:
                pass

    need_vl = False
    if want_videos or index:
        need_vl = len(targets) < (index or 1)
    elif query and not targets:
        need_vl = True
    elif query and targets and not _any_label_match(targets, query):
        need_vl = True

    # 3) VL only when structured insufficient
    if need_vl and allow_vl:
        sources_tried.append("vl")
        try:
            from voxoryl.perception import get_perception

            get_perception().set_watch(2)
        except Exception:
            pass
        vl_query = query
        if want_videos or index:
            ordinal = {1: "FIRST", 2: "SECOND", 3: "THIRD", 4: "FOURTH", 5: "FIFTH"}.get(
                int(index or 1), f"#{index}"
            )
            vl_query = (
                f"Locate the {ordinal} normal YouTube video thumbnail in the main feed "
                "(not Shorts, not ads). Return its center click point."
            )
            multi = await _vl_locate_targets(vl_query, multi=True)
            if multi and index and len(multi) >= int(index):
                targets = multi
            elif multi:
                targets = multi
            else:
                single = await _vl_locate_targets(vl_query, multi=False)
                if single:
                    single[0].index = int(index or 1)
                    targets = single
        else:
            targets = await _vl_locate_targets(vl_query, multi=False)

    selected: GroundedTarget | None = None
    if index is not None and targets:
        # Prefer explicit index field, else list order
        by_idx = [t for t in targets if t.index == int(index)]
        if by_idx:
            selected = by_idx[0]
        elif len(targets) >= int(index):
            selected = targets[int(index) - 1]
    elif query and targets:
        selected = _best_label_match(targets, query) or targets[0]
    elif targets:
        selected = targets[0]

    # Final clamp / reject nonsense on selected
    if selected is not None:
        pt = clamp_xy(selected.x, selected.y)
        if pt is None:
            selected = None
        else:
            selected.x, selected.y = pt

    result = {
        "ok": bool(selected) or bool(targets),
        "targets": [t.to_dict() for t in targets],
        "selected": selected.to_dict() if selected else None,
        "count": len(targets),
        "sources_tried": sources_tried,
        "source": (selected.source if selected else (targets[0].source if targets else None)),
        "latency_ms": int((time.perf_counter() - t0) * 1000),
    }
    log.info(
        "observe_screen source=%s x=%s y=%s n=%s tried=%s",
        result.get("source"),
        (selected.x if selected else None),
        (selected.y if selected else None),
        len(targets),
        sources_tried,
    )
    return result


def _looks_like_video_query(query: str) -> bool:
    q = (query or "").lower()
    return bool(re.search(r"\b(video|youtube|thumbnail|watch)\b", q))


def _any_label_match(targets: list[GroundedTarget], query: str) -> bool:
    return _best_label_match(targets, query) is not None


def _best_label_match(targets: list[GroundedTarget], query: str) -> GroundedTarget | None:
    q = (query or "").lower().strip()
    if not q:
        return None
    tokens = [t for t in re.split(r"\W+", q) if len(t) > 2]
    best: GroundedTarget | None = None
    best_score = 0
    for t in targets:
        lab = (t.label or "").lower()
        score = sum(1 for tok in tokens if tok in lab)
        if q in lab:
            score += 3
        if score > best_score:
            best_score = score
            best = t
    return best if best_score > 0 else None


async def ground_nth_video(index: int = 1, *, allow_vl: bool = True, dry_run: bool = False) -> dict[str, Any]:
    """Convenience: observe YouTube-like video tiles and select 1-based index."""
    index = max(1, min(20, int(index or 1)))
    # Soft ready gate (callers should also wait; this avoids racing when called alone)
    try:
        from voxoryl.wait_for import wait_for_youtube_ready

        ready = await wait_for_youtube_ready(timeout_ms=18_000)
        if not ready.get("ok"):
            return {
                "ok": False,
                "error": "youtube_not_ready",
                "index": index,
                "ready": ready,
                "verified": False,
            }
    except Exception:
        ready = {"ok": False, "skipped": True}
    obs = await observe_screen(want_videos=True, index=index, allow_vl=allow_vl, dry_run=dry_run)
    sel = obs.get("selected")
    if not sel:
        return {
            "ok": False,
            "error": "target_not_found",
            "index": index,
            "observation": obs,
            "ready": ready,
            "verified": False,
        }
    return {
        "ok": True,
        "index": index,
        "x": sel.get("x"),
        "y": sel.get("y"),
        "label": sel.get("label"),
        "source": sel.get("source"),
        "confidence": sel.get("confidence"),
        "observation": obs,
        "ready": ready,
        "verified": False,
    }
