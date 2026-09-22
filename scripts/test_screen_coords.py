"""Unit/smoke for coordinate computer-use (observe → xy → click).

Run: .venv\\Scripts\\python.exe scripts\\test_screen_coords.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_click_xy_callable() -> None:
    from voxoryl.point_executor import click_xy, clamp_xy, ensure_dpi_aware, screen_size

    ensure_dpi_aware()
    sw, sh = screen_size()
    assert sw > 0 and sh > 0
    assert clamp_xy(-9999, -9999) is None
    assert clamp_xy(sw / 2, sh / 2) is not None
    # dry_run must not move the mouse / require computer_use for callability check
    r = click_xy(sw / 2, sh / 2, dry_run=True)
    assert r.get("ok") is True
    assert r.get("dry_run") is True
    assert "x" in r and "y" in r
    print("PASS click_xy callable", r)


async def test_observe_dry_run() -> None:
    from voxoryl.screen_grounding import ground_nth_video, observe_screen

    obs = await observe_screen(want_videos=True, index=2, dry_run=True)
    assert obs.get("ok")
    assert obs.get("dry_run")
    assert len(obs.get("targets") or []) >= 2
    sel = obs.get("selected") or {}
    assert sel.get("index") == 2 or sel.get("x")
    g = await ground_nth_video(2, dry_run=True)
    assert g.get("ok")
    assert g.get("x") and g.get("y")
    print("PASS observe dry_run", {"source": g.get("source"), "x": g.get("x"), "y": g.get("y")})


async def test_play_second_routes_coords() -> None:
    """Ensure play-nth ordinal routes to coordinate grounding when CDP is mocked off."""
    from voxoryl.chrome_control import parse_video_ordinal, play_nth_youtube_via_coords

    assert parse_video_ordinal("play the second video") == 2

    # Patch ground_nth_video to dry coords so we don't move mouse / need VL
    import voxoryl.screen_grounding as sg
    import voxoryl.chrome_control as cc

    async def _fake_ground(index: int = 1, **kw):
        return {
            "ok": True,
            "index": index,
            "x": 640,
            "y": 400,
            "label": "Fake second video",
            "source": "uia",
            "confidence": 0.9,
            "observation": {"source": "uia", "targets": []},
        }

    async def _fake_verify(**kw):
        return {"ok": False, "verified": False, "method": "unit_test", "error": "intentional"}

    orig_g = sg.ground_nth_video
    orig_v = cc.verify_watch_heuristic
    sg.ground_nth_video = _fake_ground  # type: ignore
    cc.verify_watch_heuristic = _fake_verify  # type: ignore
    try:
        # Force computer_use gate by patching
        orig_gate = cc.computer_use_gate_ok
        cc.computer_use_gate_ok = lambda: True  # type: ignore
        # click dry via patching click_xy_and_verify
        import voxoryl.point_executor as pe

        async def _fake_click(x, y, **kw):
            return {
                "ok": False,  # verified false
                "clicked": True,
                "x": x,
                "y": y,
                "verified": False,
                "verify": {"ok": False, "method": "unit_test"},
            }

        orig_c = pe.click_xy_and_verify
        pe.click_xy_and_verify = _fake_click  # type: ignore
        try:
            out = await play_nth_youtube_via_coords(2, message="play the second video")
        finally:
            pe.click_xy_and_verify = orig_c  # type: ignore
            cc.computer_use_gate_ok = orig_gate  # type: ignore
    finally:
        sg.ground_nth_video = orig_g  # type: ignore
        cc.verify_watch_heuristic = orig_v  # type: ignore

    assert out.get("method") == "coords"
    assert out.get("source") == "uia"
    assert out.get("x") == 640 and out.get("y") == 400
    assert out.get("verified") is False
    assert out.get("clicked") is True
    # Honesty: must not claim done / playing
    speak = str(out.get("speak") or "").lower()
    assert "playing" not in speak or "couldn't" in speak or "couldn" in speak
    print(
        "PASS play_second routes coords",
        {
            "source": out.get("source"),
            "x": out.get("x"),
            "y": out.get("y"),
            "verified": out.get("verified"),
            "speak": out.get("speak"),
        },
    )


async def main() -> int:
    test_click_xy_callable()
    await test_observe_dry_run()
    await test_play_second_routes_coords()
    print("ALL SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
