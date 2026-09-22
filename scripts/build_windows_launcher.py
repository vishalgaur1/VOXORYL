#!/usr/bin/env python3
"""
Optional Windows one-file launcher via PyInstaller (honest P0).

This is NOT a full GUI installer. It produces a small EXE that runs
scripts/bootstrap_voxoryl.py (venv + user-data + models + launch).

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
        print("Skipping build — bootstrap scripts remain the supported zero-setup path.")
        return 1

    SPEC_ENTRY.write_text(
        '''\
"""PyInstaller entry: run VOXORYL bootstrap from the frozen/sibling tree."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path


def main() -> None:
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).resolve().parent
        candidates = [
            here / "scripts" / "bootstrap_voxoryl.py",
            here / "VOXORYL" / "scripts" / "bootstrap_voxoryl.py",
            here.parent / "scripts" / "bootstrap_voxoryl.py",
        ]
    else:
        candidates = [Path(__file__).resolve().parent / "bootstrap_voxoryl.py"]

    target = next((p for p in candidates if p.is_file()), None)
    if target is None:
        print("Could not find scripts/bootstrap_voxoryl.py next to this launcher.")
        print("Unzip the full VOXORYL release and run scripts\\\\install.ps1 instead.")
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
        str(SPEC_ENTRY),
    ]
    print("Running:", " ".join(cmd))
    r = subprocess.call(cmd, cwd=str(ROOT))
    if r == 0:
        print(f"Built: {OUT.parent / 'VOXORYL-Launcher.exe'}")
        print("Ship the EXE beside the unzipped VOXORYL folder, or prefer scripts/install.ps1.")
    return r


def _has_module(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
