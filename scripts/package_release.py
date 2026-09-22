#!/usr/bin/env python3
"""Build a portable VOXORYL source zip for GitHub Releases.

Produces a cross-platform Python source package (not a frozen EXE/app).
Excludes secrets, local data, venvs, caches, and personal/benchmark artifacts.
"""

from __future__ import annotations

import argparse
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "release"

SKIP_DIR_NAMES = {
    ".git",
    ".github",  # CI only; end users don't need workflows in the zip
    ".venv",
    "venv",
    "ENV",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".cache",
    ".idea",
    ".vscode",
    "data",
    "workspace_sandbox",
    "release",
    "dist",
    "build",
    ".eggs",
    "agent-tools",
    "logs",
    "htmlcov",
    "node_modules",
}

SKIP_FILE_NAMES = {
    ".env",
    ".env.local",
    ".DS_Store",
    "Thumbs.db",
    "Desktop.ini",
    ".coverage",
    "credentials.json",
}

SKIP_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".log",
    ".gguf",
    ".lnk",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".zip",
}

# Paths relative to ROOT (posix) that must never ship (PII / local-only / heavy).
SKIP_REL_PREFIXES = (
    "scripts/benchmark_voxoryl.py",  # can emit local timing / env-adjacent reports
)

SKIP_REL_GLOBS_CONTAIN = (
    ".egg-info",
    "Chrome Debug",
    "chrome_debug",
    "User Data",
    "Local State",
)

README_RELEASE = """# VOXORYL — Release package

This zip is a **portable Python source package** for Windows, macOS, and Linux.
It is **not** a full MSI/Inno installer. You need Python 3.11+ installed.

**First step:** open the **Setup Wizard** (normal software flow). An optional
one-file `VOXORYL-Launcher.exe` (PyInstaller) may sit beside the zip and start
the same wizard/bootstrap.

## What's included

- `voxoryl/` package, `scripts/` launchers + Setup Wizard + `install.ps1` / `install.sh`, `setup/` templates
- `requirements.txt`, `pyproject.toml`, `README.md`, `LICENSE`
- `.env.example` (seeded into OS user-data as `config.env` — never commit secrets)

## What's NOT included (by design)

- `.env` / API keys / `config.env`
- Personal `data/` (memory, knowledge, reels — created under OS user-data)
- `.venv` (created by install / wizard)
- Git history and CI workflows

## Install (all platforms) — recommended

1. Unzip this archive.
2. Install [Python 3.11+](https://www.python.org/downloads/) (Windows: check "Add Python to PATH").
3. From the unzipped folder:

```bash
# Windows — double-click, or:
scripts\\Setup VOXORYL.bat
# or:  python scripts\\setup_voxoryl.py

# macOS — double-click Setup VOXORYL.command, or:
chmod +x "scripts/Setup VOXORYL.command" && open "scripts/Setup VOXORYL.command"

# Linux / headless:
python3 scripts/setup_voxoryl.py
# terminal-only bootstrap:
./scripts/install.sh --cli
```

Wizard pages: Welcome → License → Location → Automatic/Custom → optional ENV keys → progress → Launch.
Automatic mode runs `scripts/bootstrap_voxoryl.py` (venv → user-data → hardware → models → config).

### Where your data lives

| OS | Path |
|----|------|
| Windows | `%LOCALAPPDATA%\\VOXORYL\\` |
| macOS | `~/Library/Application Support/VOXORYL/` |
| Linux | `~/.local/share/voxoryl/` |

Deleting this zip folder does **not** delete that user-data directory.
Uninstall shortcuts / wipe data: `python scripts/uninstall_voxoryl.py` (see README).

## Manual launch (after install)

```bash
# Cross-platform
python scripts/launch_voxoryl.py --console

# Windows silent orb
wscript .\\scripts\\launch-voxoryl.vbs

# macOS / Linux helper
chmod +x scripts/launch_voxoryl.sh
./scripts/launch_voxoryl.sh
```

Optional: install [Ollama](https://ollama.com/download) for on-device models
(bootstrap tries winget/brew and prints the download link if missing).

- Command center: http://127.0.0.1:3847
- Voice widget: http://127.0.0.1:3847/widget

## Platform notes

| | Windows | macOS | Linux |
|---|---|---|---|
| API + widget | Yes | Yes | Yes (browser widget fallback) |
| Native orb (pywebview) | Yes (WebView2) | When pywebview works | Best-effort |
| Computer-use / UIA | Strongest | Limited | Limited |

Same zip works on all three OSes — Python is the runtime.

## License

Non-commercial use: PolyForm Noncommercial 1.0.0 (`LICENSE`).
Commercial inquiries: vishalgaur2002@gmail.com
Copyright (c) 2026 Vishal Gaur — https://github.com/vishalgaur1/VOXORYL

See the main `README.md` for full docs.
"""


def _read_pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if not m:
        raise SystemExit("Could not read version from pyproject.toml")
    return m.group(1)


def _normalize_version(raw: str) -> str:
    v = raw.strip()
    if v.startswith("v") or v.startswith("V"):
        v = v[1:]
    return v or datetime.now(timezone.utc).strftime("%Y.%m.%d")


def should_skip(path: Path) -> bool:
    rel = path.relative_to(ROOT)
    rel_posix = rel.as_posix()
    parts = set(rel.parts)

    if parts & SKIP_DIR_NAMES:
        return True
    if path.name in SKIP_FILE_NAMES:
        return True
    # Skip local env overrides, but keep .env.example for end users
    if path.name.startswith(".env.") and path.name != ".env.example":
        return True
    if path.suffix.lower() in SKIP_SUFFIXES:
        return True
    if path.name.endswith(".egg-info") or path.name.endswith(".local.md"):
        return True
    if any(rel_posix == p or rel_posix.startswith(p + "/") for p in SKIP_REL_PREFIXES):
        return True
    if any(token in rel_posix for token in SKIP_REL_GLOBS_CONTAIN):
        return True
    return False


def build_zip(version: str, platform_label: str | None = None) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if platform_label:
        name = f"VOXORYL-{version}-{platform_label}.zip"
    else:
        name = f"VOXORYL-{version}-source.zip"
    out = OUT_DIR / name

    count = 0
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file():
                continue
            if should_skip(path):
                continue
            arc = (Path("VOXORYL") / path.relative_to(ROOT)).as_posix()
            zf.write(path, arcname=arc)
            count += 1

        # Placeholder so users know where private data will live
        zf.writestr("VOXORYL/data/.gitkeep", "")
        zf.writestr("VOXORYL/data/README.md", "# Private data\n\nCreated at runtime. Never ship this folder.\n")
        zf.writestr("VOXORYL/README_RELEASE.md", README_RELEASE)

    print(f"Wrote {out} ({count} project files + release notes)")
    print("Do not include .env or data/ in uploads.")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Package VOXORYL for GitHub Releases")
    parser.add_argument(
        "--version",
        default="",
        help="Version label (default: pyproject.toml; strips leading v)",
    )
    parser.add_argument(
        "--platform",
        default="",
        help="Optional platform suffix (windows|macos|linux). Default: source (cross-platform).",
    )
    args = parser.parse_args()
    version = _normalize_version(args.version or _read_pyproject_version())
    platform = (args.platform or "").strip().lower() or None
    if platform in {"mac", "osx", "darwin"}:
        platform = "macos"
    if platform in {"win", "win32"}:
        platform = "windows"
    if platform in {"ubuntu", "debian"}:
        platform = "linux"
    build_zip(version, platform)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
