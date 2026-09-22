"""
Thin alias → scripts/launch_voxoryl.py (canonical product entry).

Desktop icon should use scripts\\launch-voxoryl.vbs or:
  .\\.venv\\Scripts\\pythonw.exe .\\scripts\\launch_voxoryl.py --native
Optional dashboard: add --dashboard (or VOXORYL_OPEN_DASHBOARD=1).
Force Edge --app: --browser-widget
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

_TARGET = Path(__file__).resolve().parent / "scripts" / "launch_voxoryl.py"
if not _TARGET.is_file():
    print(f"Missing {_TARGET}", file=sys.stderr)
    raise SystemExit(1)
sys.argv[0] = str(_TARGET)
runpy.run_path(str(_TARGET), run_name="__main__")
