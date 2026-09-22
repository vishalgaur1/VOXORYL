from __future__ import annotations

"""
Hard privacy defaults: user data stays on this machine.
Sensitive paths (chats, therapy, QnA) never go to cloud LLMs.
"""

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PRIVACY_PATH = ROOT / "setup" / "privacy.json"

SENSITIVE_KINDS = frozenset(
    {
        "chat_export",
        "companion",
        "therapy",
        "qna",
        "identity",
        "leads_contacts",
        "knowledge_personal",
        "food_orders",
        "bank_statement",
        "wellbeing",
        "spend",
        "neuro",
        "accessibility",
    }
)


def load_privacy() -> dict[str, Any]:
    if PRIVACY_PATH.exists():
        try:
            return json.loads(PRIVACY_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"local_only": True}


def local_only() -> bool:
    return bool(load_privacy().get("local_only", True))


def allow_cloud_for(kind: str) -> bool:
    """Sensitive personal content is always local-only regardless of keys / mode."""
    if kind in SENSITIVE_KINDS:
        return False
    if local_only() and kind in {"chat_export", "companion", "therapy", "qna"}:
        return False
    # Explicit Cloud inference mode (widget) allows general agent chat/router to leave the PC.
    if kind in {"agent", "general", "router", "council_advisor"}:
        try:
            from voxoryl.inference import effective_mode

            if effective_mode() == "cloud":
                return True
        except Exception:
            pass
    return True


def privacy_status() -> dict[str, Any]:
    cfg = load_privacy()
    return {
        "ok": True,
        "local_only": bool(cfg.get("local_only", True)),
        "data_dir": "data/ (gitignored — never pushed)",
        "never_upload": cfg.get("never_upload") or [],
        "cloud_rule": cfg.get("cloud_allowed_only_when"),
        "speak": "All personal data stays on this PC under data/. Chat exports & companion analysis use local Ollama only — never Groq/cloud.",
    }
