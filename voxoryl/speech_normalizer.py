"""Speech normalizer — conservative; never LLM-rewrite ASR.

Keep FULL raw transcript for the agent. Only strip exact wake prefixes into
command_text for L0-R matching. Do not eat conversational 'hey' / 'can you' /
discourse fillers that change meaning.
"""

from __future__ import annotations

import re
from typing import Any

# Protect proper nouns / product names from stripping
PROTECTED = {
    "chrome",
    "gmail",
    "github",
    "cursor",
    "voxoryl",
    "voxy",
    "openai",
    "youtube",
    "spotify",
    "notion",
    "slack",
    "profile",
}


def _protected_tokens() -> set[str]:
    """Merge static tokens with owner name parts from env (no hardcoded personal names)."""
    out = set(PROTECTED)
    try:
        from voxoryl.config import settings

        name = (settings.owner_name or "").strip().lower()
        for part in name.replace(",", " ").split():
            if len(part) > 1:
                out.add(part)
    except Exception:
        pass
    return out

# Only true vocalized hesitations — never "hey", "can", "you", "like" as discourse
FILLERS = re.compile(
    r"\b(uh+|um+|erm+|ahh+|hmm+)\b",
    re.I,
)

# Primary wake: "hey voxy". Also accept "hey voxoryl" / ok(ay) variants.
# Note: "voxoryl" is NOT "voxy"+"ryl" — use alternation, not optional suffix.
WAKE_PHRASES: tuple[str, ...] = (
    "hey voxy",
    "hey voxoryl",
    "ok voxy",
    "okay voxy",
    "ok voxoryl",
    "okay voxoryl",
)

# Leading wake only — never strip conversational "hey" alone.
WAKE_PREFIX = re.compile(
    r"^(?:hey\s+(?:voxy|voxoryl)|ok(?:ay)?\s+(?:voxy|voxoryl)|voxoryl)\b[,:]?\s*",
    re.I,
)

# Match wake anywhere at utterance start (partial ASR may be incomplete)
WAKE_DETECT = re.compile(
    r"(?:^|\b)(hey\s+(?:voxy|voxoryl)|ok(?:ay)?\s+(?:voxy|voxoryl))\b",
    re.I,
)


def has_wake_phrase(text: str) -> bool:
    """True if transcript starts with / contains a dedicated wake phrase."""
    t = (text or "").strip()
    if not t:
        return False
    if WAKE_PREFIX.match(t):
        return True
    return bool(WAKE_DETECT.search(t))


def strip_wake_prefix(text: str) -> str:
    """Remove leading wake for L0-R / command routing. Preserves rest of utterance."""
    return WAKE_PREFIX.sub("", (text or "").strip()).strip()


def wake_only(text: str) -> bool:
    """True when the utterance is just the wake phrase (no command after)."""
    t = (text or "").strip()
    if not t:
        return False
    rest = strip_wake_prefix(t)
    if rest and rest.lower() != t.lower():
        return False
    # Exact wake / near-exact
    low = re.sub(r"[^\w\s]", "", t.lower()).strip()
    low = re.sub(r"\s+", " ", low)
    if low in {p.lower() for p in WAKE_PHRASES} or low in {"voxy", "voxoryl"}:
        return True
    return has_wake_phrase(t) and not strip_wake_prefix(t)


def normalize_transcript(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        return {
            "raw_text": "",
            "normalized_text": "",
            "command_text": "",
            "normalization_confidence": 1.0,
            "wake_detected": False,
            "wake_only": False,
        }
    # Light cleanup only — keep leading conversational words for the agent
    tokens = text.split()
    kept: list[str] = []
    for tok in tokens:
        bare = re.sub(r"[^\w]", "", tok).lower()
        if bare in _protected_tokens():
            kept.append(tok)
            continue
        if FILLERS.fullmatch(tok.strip(",.")):
            continue
        kept.append(tok)
    normalized = " ".join(kept)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    # Prefer raw-preserving normalized (almost identical) for agent input
    agent_text = normalized or text
    wake = has_wake_phrase(agent_text) or has_wake_phrase(text)
    # Command routing drops wake prefix; agent/UI keep the full transcript.
    stripped = strip_wake_prefix(agent_text)
    if wake:
        command_text = stripped  # may be "" for wake-only
    else:
        command_text = stripped or agent_text
    conf = 0.95 if (command_text or agent_text) else 0.5
    return {
        "raw_text": text,
        "normalized_text": agent_text,
        "command_text": command_text,
        "normalization_confidence": conf,
        "wake_detected": wake,
        "wake_only": wake_only(agent_text) or wake_only(text),
    }
