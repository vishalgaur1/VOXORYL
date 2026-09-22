#!/usr/bin/env python3
"""
Uninstall helper for VOXORYL.

Removes Desktop shortcuts by default. Optionally deletes OS user-data
(%LOCALAPPDATA%\\VOXORYL etc.) only after explicit confirmation.

Deleting the git clone / install folder does NOT wipe user data.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _remove_shortcuts() -> list[str]:
    removed: list[str] = []
    home = Path.home()
    candidates = [
        home / "Desktop" / "Voxoryl.lnk",
        home / "Desktop" / "VOXORYL.lnk",
        home / "OneDrive" / "Desktop" / "Voxoryl.lnk",
    ]
    # Windows public / start menu
    import os

    appdata = Path(os.environ.get("APPDATA", ""))
    if appdata:
        candidates.append(appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Voxoryl.lnk")
    for p in candidates:
        try:
            if p.is_file():
                p.unlink()
                removed.append(str(p))
        except OSError:
            pass
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Uninstall VOXORYL shortcuts / optional user data")
    parser.add_argument(
        "--wipe-user-data",
        action="store_true",
        help="Delete the OS user-data directory (memory, knowledge, config.env). Requires --yes.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required with --wipe-user-data (no interactive prompt in CI).",
    )
    parser.add_argument(
        "--keep-shortcuts",
        action="store_true",
        help="Do not remove Desktop/Start Menu shortcuts.",
    )
    args = parser.parse_args()

    from voxoryl.paths import user_data_dir

    ud = user_data_dir()
    print(f"Install / code folder: {ROOT}")
    print(f"User data folder:      {ud}")
    print()
    print("Note: deleting the repo or Release zip folder does NOT delete user data.")
    print(f"To wipe personal data intentionally, remove: {ud}")
    print()

    if not args.keep_shortcuts:
        gone = _remove_shortcuts()
        if gone:
            print("Removed shortcuts:")
            for g in gone:
                print(f"  - {g}")
        else:
            print("No Desktop/Start Menu shortcuts found.")

    if args.wipe_user_data:
        if not args.yes:
            print("Refusing to wipe user data without --yes")
            print(f"Example: python scripts/uninstall_voxoryl.py --wipe-user-data --yes")
            return 2
        if ud.exists():
            print(f"Deleting user data: {ud}")
            shutil.rmtree(ud, ignore_errors=False)
            print("User data removed.")
        else:
            print("User data folder already absent.")
    else:
        print(f"User data kept at {ud} (pass --wipe-user-data --yes to delete).")

    print("Done. You can delete the install/repo folder separately if you want.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
