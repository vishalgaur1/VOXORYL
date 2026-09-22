"""Unit-style checks for Voxoryl content-destruction safety policy.

Usage:
  .\\.venv\\Scripts\\python.exe scripts\\test_safety.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voxoryl.safety import (  # noqa: E402
    analyze_destructive_steps,
    gate_keyboard_steps,
    gate_shell_command,
    is_under_project,
    may_delete_path,
    project_root,
    record_text_write,
    refuse,
    safe_unlink,
    store_path,
)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def test_owned_form_rewrite_allowed() -> None:
    # Simulate Voxoryl typed into Notepad
    entry = record_text_write(
        "hello from voxoryl",
        window="Untitled - Notepad",
        process="notepad.exe",
        source="test",
    )
    _assert(bool(entry.get("id")), "owned write should be recorded")

    steps = [
        {"action": "hotkey", "keys": ["ctrl", "a"]},
        {"action": "paste", "text": "updated value"},
    ]
    # Force ownership match without needing real focus by checking store + gate with mocked focus
    from voxoryl import safety as safety_mod

    real_focus = safety_mod.focus_context

    def fake_focus() -> dict[str, str]:
        return {"window": "Untitled - Notepad", "process": "notepad.exe", "hwnd": "1"}

    safety_mod.focus_context = fake_focus  # type: ignore[assignment]
    try:
        gated = gate_keyboard_steps(steps, goal="change what you typed to updated value")
        _assert(gated.get("ok") is True, f"owned rewrite should be allowed: {gated}")
    finally:
        safety_mod.focus_context = real_focus  # type: ignore[assignment]


def test_unknown_notepad_clear_blocked() -> None:
    from voxoryl import safety as safety_mod

    real_focus = safety_mod.focus_context

    def fake_focus() -> dict[str, str]:
        return {"window": "notes.txt - Notepad", "process": "notepad.exe", "hwnd": "2"}

    safety_mod.focus_context = fake_focus  # type: ignore[assignment]
    try:
        # Ensure no matching owned write for this window title
        steps = [
            {"action": "hotkey", "keys": ["ctrl", "a"]},
            {"action": "press", "text": "delete"},
        ]
        # Clear matching writes by using a unique window that was never recorded
        gated = gate_keyboard_steps(steps, goal="clear all text in notepad")
        _assert(gated.get("ok") is False, f"unknown clear should be blocked: {gated}")
        _assert(gated.get("refused") is True, "should be refused")
        _assert("won't clear" in str(gated.get("speak") or "").lower() or gated.get("safety"), "speak refusal")
    finally:
        safety_mod.focus_context = real_focus  # type: ignore[assignment]


def test_delete_outside_project_blocked() -> None:
    outside = Path(tempfile.gettempdir()) / "voxoryl_safety_should_not_delete.txt"
    outside.write_text("keep me", encoding="utf-8")
    try:
        gate = may_delete_path(outside)
        _assert(gate.get("ok") is False, f"outside delete must be blocked: {gate}")
        result = safe_unlink(outside)
        _assert(result.get("ok") is False, "safe_unlink must refuse outside path")
        _assert(outside.exists(), "file must still exist")
    finally:
        outside.unlink(missing_ok=True)


def test_delete_inside_project_allowed() -> None:
    data = project_root() / "data" / "safety"
    data.mkdir(parents=True, exist_ok=True)
    target = data / "_test_owned_delete.tmp"
    target.write_text("temp", encoding="utf-8")
    _assert(is_under_project(target), "temp file should be under project")
    gate = may_delete_path(target)
    _assert(gate.get("ok") is True, f"inside delete should be allowed: {gate}")
    result = safe_unlink(target)
    _assert(result.get("ok") is True, f"safe_unlink should succeed: {result}")
    _assert(not target.exists(), "temp file should be gone")


def test_shell_destructive_gated() -> None:
    bad = gate_shell_command(r'del "C:\Users\Public\secret.txt"')
    _assert(bad.get("ok") is False, f"shell delete outside must refuse: {bad}")
    good_path = project_root() / "data" / "safety" / "_shell_ok.tmp"
    good_path.parent.mkdir(parents=True, exist_ok=True)
    good_path.write_text("x", encoding="utf-8")
    try:
        ok = gate_shell_command(f'del "{good_path}"')
        _assert(ok.get("ok") is True, f"shell delete under project allowed: {ok}")
    finally:
        good_path.unlink(missing_ok=True)


def test_analyze_select_all_overwrite() -> None:
    analysis = analyze_destructive_steps(
        [
            {"action": "hotkey", "keys": ["ctrl", "a"]},
            {"action": "type", "text": "new"},
        ]
    )
    _assert(analysis.get("destructive") is True, analysis)
    _assert(analysis.get("kind") == "overwrite", analysis)


def main() -> int:
    tests = [
        test_analyze_select_all_overwrite,
        test_owned_form_rewrite_allowed,
        test_unknown_notepad_clear_blocked,
        test_delete_outside_project_blocked,
        test_delete_inside_project_allowed,
        test_shell_destructive_gated,
    ]
    failed = 0
    for fn in tests:
        name = fn.__name__
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as exc:
            failed += 1
            print(f"FAIL  {name}: {exc}")
    print(f"\nStore: {store_path()}")
    print(f"Project: {project_root()}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    # smoke refuse helper
    r = refuse("text")
    assert r.get("speak")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
