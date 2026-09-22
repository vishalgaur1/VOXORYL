from __future__ import annotations

"""
Multilingual voice + reply language pack.

Natural male neural TTS (edge-tts):
  EN  → en-US-AndrewMultilingualNeural  (warm, confident — also handles light Hinglish)
  HI  → hi-IN-MadhurNeural              (native Hindi male)
"""

import json
import re
from datetime import datetime, timezone
from typing import Any

from voxoryl.config import settings

# Cool natural male neural voices (Microsoft Edge Read Aloud)
VOICE_EN = "en-US-AndrewMultilingualNeural"
VOICE_HI = "hi-IN-MadhurNeural"
VOICE_EN_IN = "en-IN-PrabhatNeural"  # optional Indian-English flavour

VOICE_MAP: dict[str, str] = {
    "en": VOICE_EN,
    "en-gb": VOICE_EN,
    "en-us": VOICE_EN,
    "en-in": VOICE_EN_IN,
    "hi": VOICE_HI,
    "hi-in": VOICE_HI,
    "es": "es-ES-AlvaroNeural",
    "fr": "fr-FR-HenriNeural",
    "de": "de-DE-ConradNeural",
    "pt": "pt-BR-AntonioNeural",
    "it": "it-IT-DiegoNeural",
    "ja": "ja-JP-KeitaNeural",
    "ko": "ko-KR-InJoonNeural",
    "zh": "zh-CN-YunxiNeural",
    "ar": "ar-SA-HamedNeural",
    "ta": "ta-IN-ValluvarNeural",
    "te": "te-IN-MohanNeural",
    "bn": "bn-IN-BashkarNeural",
    "mr": "mr-IN-ManoharNeural",
}

_HI_CHARS = re.compile(r"[\u0900-\u097F]")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_ARABIC = re.compile(r"[\u0600-\u06FF]")
_HANGUL = re.compile(r"[\uac00-\ud7af]")
_HIRAGANA = re.compile(r"[\u3040-\u309f]")

_HINDI_HINTS = (
    "namaste",
    "namaskar",
    "kaise ho",
    "kaisa hai",
    "kya haal",
    "dhanyavad",
    "shukriya",
    "theek",
    "haan",
    "nahi",
    "bhai",
    "yaar",
    "accha",
    "matlb",
    "samajh",
)

_LANG_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("hi", _HINDI_HINTS),
    ("es", ("hola", "gracias", "buenos dias", "por favor", "como estas")),
    ("fr", ("bonjour", "merci", "salut", "comment ca va", "s'il vous")),
    ("de", ("hallo", "danke", "guten tag", "bitte", "wie geht")),
    ("pt", ("ola", "obrigado", "bom dia", "por favor")),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path():
    p = settings.voxoryl_data_dir / "languages.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def load_languages() -> dict[str, Any]:
    path = _path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # Migrate stale robotic / old Ryan defaults to Andrew
            voices = dict(data.get("voices") or {})
            changed = False
            if voices.get("en") in {None, "", "en-GB-RyanNeural", "en-GB-ThomasNeural", "en-GB-GeorgeNeural"}:
                voices["en"] = VOICE_EN
                changed = True
            if not voices.get("hi"):
                voices["hi"] = VOICE_HI
                changed = True
            if changed:
                data["voices"] = voices
                save_languages(data)
            return data
        except json.JSONDecodeError:
            pass
    return {
        "current": "en",
        "installed": ["en", "hi"],
        "voices": {"en": VOICE_EN, "hi": VOICE_HI},
        "updated_at": None,
    }


def save_languages(data: dict[str, Any]) -> None:
    data["updated_at"] = _now()
    _path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def detect_language(text: str) -> str:
    """Lightweight locale detection — script first, then keyword hints."""
    raw = (text or "").strip()
    if not raw:
        return load_languages().get("current") or "en"
    if _HI_CHARS.search(raw):
        return "hi"
    if _CJK.search(raw):
        return "zh"
    if _ARABIC.search(raw):
        return "ar"
    if _HANGUL.search(raw):
        return "ko"
    if _HIRAGANA.search(raw):
        return "ja"
    lower = raw.lower()
    # Romanized Hindi / Hinglish cues
    hi_hits = sum(1 for h in _HINDI_HINTS if h in lower)
    if hi_hits >= 2 or (hi_hits >= 1 and len(lower.split()) <= 8):
        return "hi"
    for code, hints in _LANG_HINTS:
        if code == "hi":
            continue
        if any(h in lower for h in hints):
            return code
    m = re.search(
        r"(?:speak|reply|answer|talk)\s+(?:in\s+)?(english|hindi|spanish|french|german|portuguese|japanese|korean|chinese|arabic|tamil|telugu|bengali|marathi)",
        lower,
    )
    if m:
        return _name_to_code(m.group(1))
    return "en"


def _name_to_code(name: str) -> str:
    mapping = {
        "english": "en",
        "hindi": "hi",
        "spanish": "es",
        "french": "fr",
        "german": "de",
        "portuguese": "pt",
        "japanese": "ja",
        "korean": "ko",
        "chinese": "zh",
        "arabic": "ar",
        "tamil": "ta",
        "telugu": "te",
        "bengali": "bn",
        "marathi": "mr",
    }
    return mapping.get((name or "").lower(), "en")


def voice_for(lang: str | None = None) -> str:
    data = load_languages()
    code = (lang or data.get("current") or "en").lower()
    installed_voice = (data.get("voices") or {}).get(code)
    if installed_voice and "Ryan" not in str(installed_voice):
        return str(installed_voice)
    if code in VOICE_MAP:
        return VOICE_MAP[code]
    base = code.split("-")[0]
    return VOICE_MAP.get(base) or settings.tts_voice or VOICE_EN


def voice_for_text(text: str, *, lang: str | None = None) -> str:
    """Pick the most natural male voice for this utterance (EN vs HI)."""
    detected = detect_language(text)
    if lang:
        detected = lang.lower().split("-")[0]
    # Devanagari → always Madhur (best Hindi)
    if _HI_CHARS.search(text or ""):
        return VOICE_HI
    if detected == "hi":
        # Romanized Hindi: Madhur still often better; Andrew Multi also OK
        return VOICE_HI
    if detected == "en-in":
        return VOICE_EN_IN
    return voice_for(detected)


def ensure_language_pack(lang: str) -> dict[str, Any]:
    code = (lang or "en").lower().split("-")[0]
    data = load_languages()
    installed = list(data.get("installed") or [])
    voices = dict(data.get("voices") or {})
    new = code not in installed
    if new:
        installed.append(code)
    voices[code] = VOICE_MAP.get(code) or VOICE_EN
    if "en" not in voices:
        voices["en"] = VOICE_EN
    if "hi" not in voices:
        voices["hi"] = VOICE_HI
    data["installed"] = installed
    data["voices"] = voices
    data["current"] = code
    save_languages(data)
    return {
        "ok": True,
        "lang": code,
        "voice": voices[code],
        "newly_installed": new,
        "installed": installed,
        "speak": (
            f"Language pack ready: {code} · voice {voices[code]}."
            if new
            else f"Using {code} · voice {voices[code]}."
        ),
    }


def set_language(lang: str) -> dict[str, Any]:
    return ensure_language_pack(lang)


def current_language() -> dict[str, Any]:
    data = load_languages()
    code = data.get("current") or "en"
    return {
        "ok": True,
        "lang": code,
        "voice": voice_for(code),
        "installed": data.get("installed") or ["en"],
        "speak": f"Current language: {code}.",
    }


def maybe_auto_switch(message: str) -> dict[str, Any] | None:
    detected = detect_language(message)
    data = load_languages()
    current = data.get("current") or "en"
    lower = (message or "").lower()
    explicit = bool(
        re.search(r"(?:speak|reply|answer|talk)\s+(?:in\s+)?\w+|language\s*[:=]", lower)
    )
    if detected != current and (detected != "en" or explicit):
        return ensure_language_pack(detected)
    if explicit and detected == current:
        return ensure_language_pack(detected)
    return None


def language_prompt_block() -> str:
    data = load_languages()
    code = data.get("current") or "en"
    if code == "en":
        return (
            "LANGUAGE: Reply in clear natural English unless the owner writes Hindi/Hinglish — "
            "then match their language fluently (Devanagari or romanized).\n"
        )
    names = {
        "hi": "Hindi (हिन्दी) — natural spoken Hindi is fine; Hinglish OK if they write that way",
        "es": "Spanish",
        "fr": "French",
        "de": "German",
        "pt": "Portuguese",
        "ja": "Japanese",
        "ko": "Korean",
        "zh": "Chinese",
        "ar": "Arabic",
        "ta": "Tamil",
        "te": "Telugu",
        "bn": "Bengali",
        "mr": "Marathi",
    }
    label = names.get(code, code)
    return (
        f"LANGUAGE (active pack: {code}): Reply fluently in {label}. "
        "Keep Voxoryl tone — friendly, professional, concise. "
        "Only switch languages if the owner asks or clearly writes in another language.\n"
    )


async def tool_i18n(
    action: str = "status",
    message: str = "",
    lang: str = "",
) -> dict[str, Any]:
    act = (action or "status").lower()
    lower = (message or "").lower()

    if act in {"detect"} or "what language" in lower:
        code = detect_language(message)
        return {"ok": True, "lang": code, "voice": voice_for(code), "speak": f"Detected: {code}."}

    if act in {"set", "switch", "install", "download"} or any(
        k in lower for k in ("speak hindi", "reply in", "language pack", "switch language", "set language")
    ):
        code = lang or detect_language(message)
        if not lang:
            m = re.search(
                r"(?:in|to|language)\s+(english|hindi|spanish|french|german|portuguese|japanese|korean|chinese|arabic|tamil|telugu|bengali|marathi|[a-z]{2})\b",
                lower,
            )
            if m:
                tok = m.group(1)
                code = _name_to_code(tok) if len(tok) > 2 else tok
        return ensure_language_pack(code)

    if act in {"list", "installed"}:
        data = load_languages()
        return {
            "ok": True,
            "installed": data.get("installed") or [],
            "voices": data.get("voices") or {},
            "speak": "Installed: " + ", ".join(data.get("installed") or ["en"]),
        }

    return current_language()
