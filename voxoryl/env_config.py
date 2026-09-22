"""
Read / write user-facing config.env fields (from .env.example).

Used by the Setup Wizard and the widget Settings accordion.
Never commits secrets — only reads/writes the OS user-data config.env.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from voxoryl.paths import (
    ensure_user_dirs,
    repo_root,
    rewrite_data_dir_in_env,
    user_env_path,
)

# Fields shown in Setup Wizard + Settings (key, label, group, secret?, hint)
ENV_FORM_FIELDS: list[dict[str, Any]] = [
    # Cloud / inference
    {
        "key": "VOXORYL_INFERENCE_MODE",
        "label": "Inference mode",
        "group": "Brain",
        "secret": False,
        "hint": "local or cloud",
        "placeholder": "local",
    },
    {
        "key": "VOXORYL_CLOUD_PROVIDER",
        "label": "Cloud provider",
        "group": "Brain",
        "secret": False,
        "hint": "groq | gemini | openrouter | nvidia",
        "placeholder": "groq",
    },
    {
        "key": "GROQ_API_KEY",
        "label": "Groq API key",
        "group": "Cloud keys",
        "secret": True,
        "hint": "https://console.groq.com/keys",
        "placeholder": "",
    },
    {
        "key": "GEMINI_API_KEY",
        "label": "Gemini API key",
        "group": "Cloud keys",
        "secret": True,
        "hint": "https://aistudio.google.com/apikey",
        "placeholder": "",
    },
    {
        "key": "OPENROUTER_API_KEY",
        "label": "OpenRouter API key",
        "group": "Cloud keys",
        "secret": True,
        "hint": "https://openrouter.ai/keys",
        "placeholder": "",
    },
    {
        "key": "NVIDIA_API_KEY",
        "label": "NVIDIA API key",
        "group": "Cloud keys",
        "secret": True,
        "hint": "https://build.nvidia.com",
        "placeholder": "",
    },
    {
        "key": "VOXORYL_CLOUD_API_KEY",
        "label": "Shared cloud API key",
        "group": "Cloud keys",
        "secret": True,
        "hint": "Optional override used by any provider",
        "placeholder": "",
    },
    # Owner
    {
        "key": "VOXORYL_OWNER_NAME",
        "label": "Your name",
        "group": "Owner",
        "secret": False,
        "hint": "Optional — Chrome profile matching",
        "placeholder": "",
    },
    {
        "key": "VOXORYL_OWNER_EMAIL",
        "label": "Your email",
        "group": "Owner",
        "secret": False,
        "hint": "Optional — profile matching only",
        "placeholder": "",
    },
    {
        "key": "VOXORYL_CHROME_PROFILE_MATCH",
        "label": "Chrome profile phrases",
        "group": "Owner",
        "secret": False,
        "hint": "Comma-separated alternate match phrases",
        "placeholder": "",
    },
    # Voice
    {
        "key": "TTS_VOICE",
        "label": "Speaking voice",
        "group": "Voice",
        "secret": False,
        "hint": "edge-tts voice id",
        "placeholder": "en-US-AndrewMultilingualNeural",
    },
    # Integrations
    {
        "key": "GITHUB_TOKEN",
        "label": "GitHub token",
        "group": "Integrations",
        "secret": True,
        "hint": "Personal access token",
        "placeholder": "",
    },
    {
        "key": "GITHUB_USER",
        "label": "GitHub username",
        "group": "Integrations",
        "secret": False,
        "hint": "",
        "placeholder": "",
    },
    {
        "key": "OBSIDIAN_VAULT",
        "label": "Obsidian vault folder",
        "group": "Integrations",
        "secret": False,
        "hint": "Full path to your vault",
        "placeholder": "",
    },
    {
        "key": "COMPUTER_USE_ENABLED",
        "label": "Computer use",
        "group": "Screen & control",
        "secret": False,
        "hint": "true to allow screenshot / click / type on this PC",
        "placeholder": "false",
    },
]


def _parse_env_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key:
            continue
        out[key] = val.strip().strip('"').strip("'")
    return out


def ensure_config_env() -> Path:
    """Create user config.env from .env.example if missing."""
    ensure_user_dirs()
    path = user_env_path()
    if path.exists():
        return path
    example = repo_root() / ".env.example"
    if example.is_file():
        path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        rewrite_data_dir_in_env(path)
    else:
        path.write_text("", encoding="utf-8")
    return path


def read_env_values(keys: list[str] | None = None) -> dict[str, str]:
    """Current values from user config.env (empty string if unset)."""
    path = ensure_config_env()
    try:
        parsed = _parse_env_text(path.read_text(encoding="utf-8"))
    except OSError:
        parsed = {}
    if keys is None:
        keys = [f["key"] for f in ENV_FORM_FIELDS]
    return {k: parsed.get(k, "") for k in keys}


def patch_env_values(updates: dict[str, str], *, only_nonempty: bool = False) -> dict[str, Any]:
    """
    Upsert keys into user config.env.

    By default empty strings clear the value (KEY=).
    If only_nonempty=True, blank values are skipped (leave existing).
    """
    path = ensure_config_env()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""

    applied: dict[str, str] = {}
    for key, value in updates.items():
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            continue
        if only_nonempty and not str(value).strip():
            continue
        value = str(value).strip()
        applied[key] = value
        line = f"{key}={value}"
        pat = re.compile(rf"(?m)^{re.escape(key)}=.*$")
        if pat.search(text):
            text = pat.sub(lambda _m: line, text)
        else:
            text = text.rstrip() + "\n" + line + "\n"

    path.write_text(text, encoding="utf-8")
    rewrite_data_dir_in_env(path)
    return {"ok": True, "path": str(path.resolve()), "updates": applied}


def form_schema(*, include_values: bool = True, empty_only: bool = False) -> dict[str, Any]:
    """
    Schema for wizard / Settings UI.

    empty_only: only return fields whose current value is blank
    (still useful to show “fill later” empty keys).
    """
    values = read_env_values() if include_values else {}
    fields: list[dict[str, Any]] = []
    for meta in ENV_FORM_FIELDS:
        key = str(meta["key"])
        val = values.get(key, "") if include_values else ""
        if empty_only and str(val).strip():
            continue
        item = dict(meta)
        if include_values:
            item["value"] = val
            item["empty"] = not bool(str(val).strip())
        fields.append(item)
    return {
        "ok": True,
        "path": str(user_env_path().resolve()),
        "fields": fields,
        "groups": sorted({str(f.get("group") or "Other") for f in fields}),
    }
