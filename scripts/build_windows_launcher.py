#!/usr/bin/env python3
"""
Optional Windows one-file launcher via PyInstaller (honest P0).

This is NOT a full GUI installer. It produces a small EXE that opens the
Setup Wizard (scripts/setup_voxoryl.py) when available, else bootstrap.

Requires: pip install pyinstaller
  python scripts/build_windows_launcher.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dist" / "VOXORYL-Launcher"
SPEC_ENTRY = ROOT / "scripts" / "_voxoryl_launcher_entry.py"


def main() -> int:
    if not shutil.which("pyinstaller") and not _has_module("PyInstaller"):
        print("PyInstaller not installed. Run: pip install pyinstaller")
        print("Skipping build — use scripts/Setup VOXORYL.bat or setup_voxoryl.py.")
        return 1

    SPEC_ENTRY.write_text(
        '''\
"""PyInstaller entry: open Setup Wizard (fallback: bootstrap)."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path


def main() -> None:
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).resolve().parent
        roots = [here, here / "VOXORYL", here.parent]
    else:
        roots = [Path(__file__).resolve().parent.parent]

    names = ("setup_voxoryl.py", "bootstrap_voxoryl.py")
    target = None
    for root in roots:
        for name in names:
            cand = root / "scripts" / name
            if cand.is_file():
                target = cand
                break
        if target:
            break

    if target is None:
        print("Could not find scripts/setup_voxoryl.py next to this launcher.")
        print("Unzip the full VOXORYL release and run scripts\\\\Setup VOXORYL.bat instead.")
        sys.exit(1)
    sys.argv = [str(target), *sys.argv[1:]]
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()
''',
        encoding="utf-8",
    )

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name",
        "VOXORYL-Launcher",
        "--distpath",
        str(OUT.parent),
        "--workpath",
        str(ROOT / "build" / "launcher"),
        "--specpath",
        str(ROOT / "build" / "launcher"),
        "--windowed",
        str(SPEC_ENTRY),
    ]
    print("Running:", " ".join(cmd))
    r = subprocess.call(cmd, cwd=str(ROOT))
    if r == 0:
        print(f"Built: {OUT.parent / 'VOXORYL-Launcher.exe'}")
        print("Ship the EXE beside the unzipped VOXORYL folder, or prefer Setup VOXORYL.bat.")
    return r


def _has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
