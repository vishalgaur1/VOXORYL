from __future__ import annotations

"""
Software knowledge base — per-app shortcuts and recipes so Voxoryl prefers
fast keyboard paths over slow vision click loops.

Store: data/software_knowledge/<app>.json
Auto-learns when Voxoryl successfully uses an action; seeds common shortcuts offline.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from voxoryl.config import settings

# Offline seed — expand as apps are used
SEED: dict[str, dict[str, Any]] = {
    "chrome": {
        "label": "Google Chrome",
        "aliases": ["chrome", "google chrome", "browser"],
        "shortcuts": {
            "new_tab": ["ctrl", "t"],
            "close_tab": ["ctrl", "w"],
            "reopen_tab": ["ctrl", "shift", "t"],
            "next_tab": ["ctrl", "tab"],
            "prev_tab": ["ctrl", "shift", "tab"],
            "address_bar": ["ctrl", "l"],
            "search_bar": ["ctrl", "k"],
            "find_in_page": ["ctrl", "f"],
            "downloads": ["ctrl", "j"],
            "history": ["ctrl", "h"],
            "bookmark": ["ctrl", "d"],
            "devtools": ["ctrl", "shift", "i"],
            "profile_switcher": ["ctrl", "shift", "m"],
            "refresh": ["ctrl", "r"],
            "hard_refresh": ["ctrl", "shift", "r"],
        },
        "recipes": {
            "open": {
                "steps": ["launch_chrome", "wait_profile_or_ready", "select_profile_if_needed"],
                "notes": "May show Who's using Chrome? profile picker.",
            },
            "new_tab_url": {
                "steps": ["hotkey:new_tab", "wait:0.25", "hotkey:address_bar", "paste_url", "enter"],
            },
            "goto_tab": {
                "steps": ["ctrl_shift_a_or_search_tabs", "type_title", "enter"],
                "notes": "Prefer Ctrl+Shift+A (Chrome tab search) when available; else Ctrl+Tab scan.",
            },
        },
        # Profiles are filled from env (VOXORYL_OWNER_*) + optional data/software_knowledge/chrome.json
        "profiles": {},
        "sites": {
            "youtube": "https://www.youtube.com",
            "openai": "https://chatgpt.com",
            "chatgpt": "https://chatgpt.com",
            "apple": "https://www.apple.com",
            "github": "https://github.com",
            "gmail": "https://mail.google.com",
            "google": "https://www.google.com",
            "twitter": "https://x.com",
            "x": "https://x.com",
            "instagram": "https://www.instagram.com",
            "whatsapp": "https://web.whatsapp.com",
            "amazon": "https://www.amazon.in",
            "notion": "https://www.notion.so",
            "reddit": "https://www.reddit.com",
        },
        "learned": [],
        "updated_at": None,
    },
    "notepad": {
        "label": "Windows Notepad",
        "aliases": ["notepad", "notepad app", "text editor"],
        "shortcuts": {
            "new_tab": ["ctrl", "t"],
            "close_tab": ["ctrl", "w"],
            "next_tab": ["ctrl", "tab"],
            "prev_tab": ["ctrl", "shift", "tab"],
            "save": ["ctrl", "s"],
            "new_window": ["ctrl", "n"],
        },
        "recipes": {
            "new_tab": {
                "steps": ["focus_or_launch_notepad", "wait:0.4", "hotkey:new_tab"],
                "notes": "Windows 11 Notepad supports tabs via Ctrl+T.",
            },
        },
        "learned": [],
        "updated_at": None,
    },
    "windows": {
        "label": "Windows shell",
        "aliases": ["windows", "desktop"],
        "shortcuts": {
            "run": ["win", "r"],
            "search": ["win", "s"],
            "task_view": ["win", "tab"],
            "snap_left": ["win", "left"],
            "snap_right": ["win", "right"],
            "minimize": ["win", "down"],
            "maximize": ["win", "up"],
            "clipboard_history": ["win", "v"],
            "emoji": ["win", "."],
            "settings": ["win", "i"],
        },
        "recipes": {},
        "learned": [],
        "updated_at": None,
    },
    "powertoys": {
        "label": "Microsoft PowerToys",
        "aliases": ["powertoys", "power toys", "pt"],
        "shortcuts": {
            "run": "PowerToys.PowerLauncher.exe",
            "color_picker": "PowerToys.ColorPickerUI.exe",
            "text_extractor": "PowerToys.PowerOCR.exe",
            "awake": "PowerToys.Awake.exe",
            "fancyzones_editor": "PowerToys.FancyZonesEditor.exe",
        },
        "recipes": {},
        "learned": [],
        "updated_at": None,
    },
}


def _root() -> Path:
    p = settings.voxoryl_data_dir / "software_knowledge"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(app: str) -> Path:
    safe = re.sub(r"[^a-z0-9_-]+", "_", (app or "app").lower()).strip("_") or "app"
    return _root() / f"{safe}.json"


def ensure_seed(app: str = "chrome") -> dict[str, Any]:
    key = (app or "chrome").lower()
    path = _path(key)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    else:
        data = {}
    seed = SEED.get(key) or {
        "label": key,
        "aliases": [key],
        "shortcuts": {},
        "recipes": {},
        "learned": [],
        "updated_at": None,
    }
    # Merge seed without wiping learned
    merged = {**seed, **data}
    for field in ("shortcuts", "recipes", "sites"):
        base = dict(seed.get(field) or {})
        base.update(dict(data.get(field) or {}))
        merged[field] = base
    # Deep-merge profiles so seed emails / prefer flags fill gaps on disk
    seed_profiles = dict(seed.get("profiles") or {})
    disk_profiles = dict(data.get("profiles") or {})
    profiles: dict[str, Any] = {}
    for pid in set(seed_profiles) | set(disk_profiles):
        sp = dict(seed_profiles.get(pid) or {})
        dp = dict(disk_profiles.get(pid) or {})
        entry = {**sp, **dp}
        for list_key in ("match", "emails", "avoid_labels"):
            if list_key in sp and list_key not in dp:
                entry[list_key] = sp[list_key]
            elif list_key in sp and list_key in dp:
                # keep disk, but add any missing seed emails/matchers
                merged_list: list[Any] = []
                for item in list(dp.get(list_key) or []) + list(sp.get(list_key) or []):
                    if item not in merged_list:
                        merged_list.append(item)
                entry[list_key] = merged_list
        for scalar in ("prefer", "label"):
            if scalar in sp and not dp.get(scalar):
                entry[scalar] = sp[scalar]
        profiles[pid] = entry
    merged["profiles"] = profiles
    learned = list(data.get("learned") or [])
    merged["learned"] = learned[-200:]
    if not path.exists() or not data.get("shortcuts"):
        merged["updated_at"] = _now()
        path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    return merged


def load_app(app: str) -> dict[str, Any]:
    return ensure_seed(app)


def save_app(app: str, data: dict[str, Any]) -> None:
    data["updated_at"] = _now()
    _path(app).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def learn(
    app: str,
    *,
    action: str,
    detail: str = "",
    shortcut: list[str] | str | None = None,
    ok: bool = True,
) -> dict[str, Any]:
    """Record a successful (or failed) control attempt for future fast paths."""
    data = load_app(app)
    entry = {
        "at": _now(),
        "action": action,
        "detail": (detail or "")[:240],
        "shortcut": shortcut,
        "ok": bool(ok),
    }
    data.setdefault("learned", []).append(entry)
    data["learned"] = data["learned"][-200:]
    # Promote repeated successful shortcuts into shortcuts map
    if ok and shortcut and action:
        key = re.sub(r"[^a-z0-9_]+", "_", action.lower()).strip("_")
        if key and key not in (data.get("shortcuts") or {}):
            data.setdefault("shortcuts", {})[key] = shortcut
    save_app(app, data)
    return entry


def resolve_site(app: str, name: str) -> str | None:
    data = load_app(app)
    sites = data.get("sites") or {}
    key = (name or "").lower().strip()
    if key in sites:
        return str(sites[key])
    for k, url in sites.items():
        if k in key or key in k:
            return str(url)
    # bare domain
    if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", key):
        return "https://" + key
    if key.startswith("http://") or key.startswith("https://"):
        return key
    return None


def _owner_profile_from_env() -> dict[str, Any] | None:
    """Build a single personal Chrome profile from VOXORYL_OWNER_* env (no hardcoded PII)."""
    name = settings.owner_name
    email = settings.owner_email
    if not name and not email and not (settings.voxoryl_chrome_profile_match or "").strip():
        return None
    label = name or "Personal profile"
    match = list(settings.chrome_profile_match_phrases)
    emails = [email] if email else []
    prefer = (settings.voxoryl_chrome_profile_prefer or "photo").strip().lower() or "photo"
    return {
        "id": "personal",
        "match": match,
        "prefer": prefer,
        "label": label,
        "emails": emails,
    }


def resolve_profile(app: str, message: str) -> dict[str, Any] | None:
    data = load_app(app)
    lower = (message or "").lower()
    profiles = dict(data.get("profiles") or {})
    owner = _owner_profile_from_env()
    if owner and "personal" not in profiles:
        profiles["personal"] = {k: v for k, v in owner.items() if k != "id"}

    # Prefer longer match strings
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for pid, meta in profiles.items():
        for m in meta.get("match") or []:
            if m.lower() in lower:
                scored.append((len(m), pid, meta))
    if not scored:
        # Generic “my profile” / owner-name fallback
        if any(k in lower for k in ("my profile", "photo profile", "my chrome", "personal profile")):
            meta = profiles.get("personal") or owner
            if meta:
                return {"id": "personal", **meta}
        owner_first = (settings.owner_name or "").split()[0].lower() if settings.owner_name else ""
        if owner_first and owner_first in lower:
            meta = profiles.get("personal") or owner
            if meta:
                return {"id": "personal", **meta}
        return None
    scored.sort(key=lambda x: -x[0])
    pid, meta = scored[0][1], scored[0][2]
    return {"id": pid, **meta}


def list_chrome_local_profiles() -> list[dict[str, Any]]:
    """Read Chrome Local State info_cache (profile directory → name/email)."""
    import os
    import sys
    from pathlib import Path

    candidates: list[Path] = []
    if sys.platform.startswith("win"):
        candidates.append(
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data" / "Local State"
        )
    elif sys.platform == "darwin":
        candidates.append(
            Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Local State"
        )
    else:
        candidates.append(Path.home() / ".config" / "google-chrome" / "Local State")
        candidates.append(Path.home() / ".config" / "chromium" / "Local State")

    local_state = next((p for p in candidates if p.exists()), None)
    if not local_state:
        return []
    try:
        cache = json.loads(local_state.read_text(encoding="utf-8")).get("profile", {}).get("info_cache") or {}
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for directory, meta in cache.items():
        if not isinstance(meta, dict):
            continue
        out.append(
            {
                "directory": directory,
                "name": str(meta.get("name") or ""),
                "gaia_name": str(meta.get("gaia_name") or ""),
                "user_name": str(meta.get("user_name") or "").lower(),
                "hosted_domain": str(meta.get("hosted_domain") or ""),
                "has_gaia_picture": bool(meta.get("gaia_picture_file_name") or meta.get("last_downloaded_gaia_picture_url_with_size")),
                "active_time": float(meta.get("active_time") or 0),
            }
        )
    out.sort(key=lambda x: -float(x.get("active_time") or 0))
    return out


def resolve_chrome_profile_directory(profile: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    Map Voxoryl profile meta → Chrome --profile-directory value.
    Prefer explicit emails, then prefer=photo/orange heuristics using owner name from env.
    """
    if not profile:
        return None
    locals_ = list_chrome_local_profiles()
    if not locals_:
        return None

    emails = [str(e).lower() for e in (profile.get("emails") or []) if e]
    if emails:
        for row in locals_:
            if row.get("user_name") in emails:
                return row

    prefer = str(profile.get("prefer") or "").lower()
    label = str(profile.get("label") or profile.get("id") or "").lower()
    owner = (settings.owner_name or "").lower()
    gaia_needles = [n for n in [owner, *(owner.split()[:1] if owner else [])] if n]
    if prefer == "photo" or "photo" in label:
        candidates = [
            r
            for r in locals_
            if (
                not gaia_needles
                or any(n in (r.get("gaia_name") or "").lower() or n in (r.get("name") or "").lower() for n in gaia_needles)
            )
            and (
                (r.get("user_name") or "").endswith("@gmail.com")
                or str(r.get("hosted_domain") or "") in {"", "NO_HOSTED_DOMAIN"}
            )
            and r.get("has_gaia_picture")
        ]
        if candidates:
            return candidates[0]
        # Fall back: any matching name without hosted domain
        if gaia_needles:
            for r in locals_:
                blob = f"{r.get('name')} {r.get('gaia_name')}".lower()
                if any(n in blob for n in gaia_needles):
                    return r
    if prefer == "orange" or "office" in label or "orange" in label:
        candidates = [
            r
            for r in locals_
            if any(n in (r.get("gaia_name") or "").lower() or n in (r.get("name") or "").lower() for n in gaia_needles or [""])
            and str(r.get("hosted_domain") or "") not in {"", "NO_HOSTED_DOMAIN"}
        ]
        if candidates:
            return candidates[0]

    # Label / name contains profile label tokens
    needle = re.sub(r"\s+", " ", label.split("(")[0]).strip().lower()
    if needle:
        for row in locals_:
            blob = f"{row.get('name')} {row.get('gaia_name')}".lower()
            if needle in blob or any(tok in blob for tok in needle.split() if len(tok) > 2):
                return row

    # Explicit Chrome directory override via env
    import os

    directory = (os.environ.get("VOXORYL_CHROME_PROFILE_DIRECTORY") or "").strip()
    if directory:
        for row in locals_:
            if row.get("directory") == directory:
                return row
        return {"directory": directory, "name": label or directory}

    return None


def list_apps() -> list[str]:
    ensure_seed("chrome")
    ensure_seed("windows")
    ensure_seed("powertoys")
    return sorted(p.stem for p in _root().glob("*.json"))


def prompt_block(app: str = "chrome", max_chars: int = 700) -> str:
    data = load_app(app)
    shortcuts = data.get("shortcuts") or {}
    lines = [f"SOFTWARE KNOWLEDGE ({data.get('label') or app}):"]
    for k, v in list(shortcuts.items())[:16]:
        if isinstance(v, list):
            lines.append(f"- {k}: {'+'.join(v)}")
        else:
            lines.append(f"- {k}: {v}")
    blob = "\n".join(lines)
    return blob[:max_chars] + "\n"
