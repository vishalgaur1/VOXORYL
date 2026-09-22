"""
OS user-data roots for VOXORYL — keep personal files out of the install/repo folder.

| OS      | Root                                      |
|---------|-------------------------------------------|
| Windows | %LOCALAPPDATA%\\VOXORYL                    |
| macOS   | ~/Library/Application Support/VOXORYL/    |
| Linux   | ~/.local/share/voxoryl/  (XDG)            |

Layout under the root:
  config.env          — secrets & model prefs (never in git)
  data/               — memory, knowledge, reels, sqlite, logs, runtime
  workspace_sandbox/  — code-act sandbox
"""

from __future__ import annotations

import json
import os
import platform
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    """Install / clone root (code + templates + .env.example)."""
    return Path(__file__).resolve().parent.parent


def user_data_dir() -> Path:
    """
    Platform user-data root for VOXORYL.

    Override with VOXORYL_USER_DATA_DIR. Contributors can set
    VOXORYL_USE_REPO_DATA=1 to keep data next to the repo (legacy).
    """
    override = (os.environ.get("VOXORYL_USER_DATA_DIR") or "").strip()
    if override:
        return Path(override).expanduser().resolve()

    if os.environ.get("VOXORYL_USE_REPO_DATA", "").strip() in ("1", "true", "yes"):
        return repo_root()

    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "VOXORYL"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "VOXORYL"
    # Linux / other → XDG
    xdg = (os.environ.get("XDG_DATA_HOME") or "").strip()
    if xdg:
        return Path(xdg).expanduser() / "voxoryl"
    return Path.home() / ".local" / "share" / "voxoryl"


def user_env_path() -> Path:
    """Preferred config file in the user-data root (not the repo)."""
    return user_data_dir() / "config.env"


def user_data_subdir() -> Path:
    """Private runtime data (memory, knowledge, reels, logs…)."""
    return user_data_dir() / "data"


def user_workspace_dir() -> Path:
    return user_data_dir() / "workspace_sandbox"


def env_file_candidates() -> list[Path]:
    """
    Ordered dotenv files for Settings.

    User config wins. Repo `.env` is only a fallback for unmigrated
    developer checkouts (and is copied into the user dir on migrate).
    """
    out: list[Path] = []
    ue = user_env_path()
    # Also accept legacy name inside user dir
    legacy_user = user_data_dir() / ".env"
    for p in (ue, legacy_user):
        if p.exists() and p not in out:
            out.append(p)
    if os.environ.get("VOXORYL_USE_REPO_DATA", "").strip() in ("1", "true", "yes"):
        repo_env = repo_root() / ".env"
        if repo_env.exists() and repo_env not in out:
            out.append(repo_env)
    return out


def ensure_user_dirs() -> dict[str, Any]:
    """Create the user-data tree (idempotent)."""
    root = user_data_dir()
    data = user_data_subdir()
    workspace = user_workspace_dir()
    for d in (
        root,
        data,
        data / "logs",
        data / "runtime",
        data / "reels",
        data / "screenshots",
        workspace,
    ):
        d.mkdir(parents=True, exist_ok=True)
    return {
        "ok": True,
        "user_data_dir": str(root.resolve()),
        "data": str(data.resolve()),
        "workspace": str(workspace.resolve()),
        "env": str(user_env_path().resolve()),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dir_has_user_content(path: Path) -> bool:
    if not path.is_dir():
        return False
    skip = {".gitkeep", "README.md", ".DS_Store", "Thumbs.db"}
    for child in path.rglob("*"):
        if child.is_file() and child.name not in skip:
            return True
    return False


def migrate_from_repo_if_needed(*, force: bool = False) -> dict[str, Any]:
    """
    One-time copy of repo-local `data/` + `.env` into the OS user-data root.

    Never deletes the repo copies. Never overwrites newer/existing user files
    unless force=True (still refuses to clobber config.env secrets blindly —
    only fills missing files).
    """
    ensure_user_dirs()
    root = user_data_dir()
    marker = root / ".migration.json"
    report: dict[str, Any] = {
        "ok": True,
        "migrated": False,
        "skipped": False,
        "user_data_dir": str(root.resolve()),
        "copied": [],
        "skipped_existing": [],
        "notes": [],
    }

    if marker.exists() and not force:
        try:
            prev = json.loads(marker.read_text(encoding="utf-8"))
            report["skipped"] = True
            report["previous"] = prev
            report["notes"].append("Migration already recorded; leaving user data as-is.")
            return report
        except json.JSONDecodeError:
            pass

    repo = repo_root()
    # If caller already uses repo as user root, nothing to migrate.
    try:
        if root.resolve() == repo.resolve():
            report["skipped"] = True
            report["notes"].append("VOXORYL_USE_REPO_DATA — user data is the repo root.")
            _write_migration_marker(marker, report)
            return report
    except OSError:
        pass

    repo_env = repo / ".env"
    dest_env = user_env_path()
    if repo_env.is_file() and (not dest_env.exists() or force):
        if not dest_env.exists():
            shutil.copy2(repo_env, dest_env)
            report["copied"].append(f".env → {dest_env.name}")
            rewrite_data_dir_in_env(dest_env)
        else:
            report["skipped_existing"].append(str(dest_env))
    elif dest_env.exists():
        report["skipped_existing"].append(str(dest_env))
        report["notes"].append("User config.env already present — not overwritten.")

    repo_data = repo / "data"
    dest_data = user_data_subdir()
    if _dir_has_user_content(repo_data):
        copied, skipped = _copy_tree_missing(repo_data, dest_data)
        report["copied"].extend(copied)
        report["skipped_existing"].extend(skipped)
        if copied:
            report["migrated"] = True
            report["notes"].append(
                f"Copied repo data/ → {dest_data} (existing user files kept)."
            )
    else:
        report["notes"].append("No substantial repo-local data/ to migrate.")

    # Seed config.env from example if still missing
    if not dest_env.exists():
        example = repo / ".env.example"
        if example.is_file():
            text = example.read_text(encoding="utf-8")
            dest_env.write_text(text, encoding="utf-8")
            rewrite_data_dir_in_env(dest_env)
            report["copied"].append(".env.example → config.env")
            report["migrated"] = True

    if report["copied"]:
        report["migrated"] = True

    log_path = root / "migration.log"
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"{_now()} {json.dumps(report, default=str)}\n")
    except OSError:
        pass

    _write_migration_marker(marker, report)
    return report


def _write_migration_marker(marker: Path, report: dict[str, Any]) -> None:
    payload = {
        "migrated_at": _now(),
        "user_data_dir": report.get("user_data_dir"),
        "copied_count": len(report.get("copied") or []),
        "notes": report.get("notes") or [],
    }
    try:
        marker.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass


def rewrite_data_dir_in_env(env_path: Path) -> None:
    """Point VOXORYL_DATA_DIR / workspace at the user-data tree (absolute)."""
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    data = str(user_data_subdir().resolve())
    workspace = str(user_workspace_dir().resolve())
    import re

    def upsert(key: str, value: str, body: str) -> str:
        line = f"{key}={value}"
        pat = re.compile(rf"(?m)^{re.escape(key)}=.*$")
        if pat.search(body):
            # lambda avoids re.sub interpreting backslashes in Windows paths
            return pat.sub(lambda _m: line, body)
        return body.rstrip() + "\n" + line + "\n"

    text = upsert("VOXORYL_DATA_DIR", data, text)
    text = upsert("VOXORYL_WORKSPACE", workspace, text)
    try:
        env_path.write_text(text, encoding="utf-8")
    except OSError:
        pass


def _copy_tree_missing(src: Path, dest: Path) -> tuple[list[str], list[str]]:
    copied: list[str] = []
    skipped: list[str] = []
    dest.mkdir(parents=True, exist_ok=True)
    for path in src.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(src)
        if path.name in {".gitkeep", ".DS_Store", "Thumbs.db"}:
            continue
        target = dest / rel
        if target.exists():
            skipped.append(str(rel).replace("\\", "/"))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(path, target)
            copied.append(str(rel).replace("\\", "/"))
        except OSError as exc:
            skipped.append(f"{rel} ({exc})")
    return copied, skipped


def resolve_data_dir(raw: str | Path | None) -> Path:
    """
    Normalize VOXORYL_DATA_DIR from env.

    Blank / ./data / data → platform user data/data.
    Relative paths resolve under user_data_dir (not the cwd).
    """
    if raw is None:
        return user_data_subdir()
    s = str(raw).strip().strip('"').strip("'")
    if not s or s in {"./data", "data", ".\\data"}:
        return user_data_subdir()
    p = Path(s).expanduser()
    if p.is_absolute():
        return p
    # Old relative paths meant "next to repo"; after product split, map into user root.
    if s.replace("\\", "/").startswith("data/") or s.replace("\\", "/") == "data":
        return user_data_dir() / s
    return (user_data_dir() / p).resolve()


def resolve_workspace(raw: str | Path | None) -> Path:
    if raw is None:
        return user_workspace_dir()
    s = str(raw).strip().strip('"').strip("'")
    if not s or s in {"./workspace_sandbox", "workspace_sandbox"}:
        return user_workspace_dir()
    p = Path(s).expanduser()
    if p.is_absolute():
        return p
    return (user_data_dir() / p).resolve()
