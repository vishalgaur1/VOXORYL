from __future__ import annotations

"""
Local speech-to-text for the voice widget.

Primary: NVIDIA Parakeet TDT 0.6B v3 via onnx-asr
  (same family Hex / FluidAudio use — faster & clearer than Whisper for EN/EU langs)

Fallback: faster-whisper / openai-whisper
  (kept for Hindi and when Parakeet isn't installed)
"""

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import Any

from voxoryl.config import settings

_model = None
_backend: str | None = None
_model_name: str | None = None

# Parakeet v3 covers ~25 European languages; Hindi needs Whisper/Qwen ASR.
_PARAKEET_LANGS = {"en", "es", "fr", "de", "it", "pt", "nl", "pl", "uk", "ru", "cs", "sk", "ro", "hu", "sv", "da", "fi", "no", "el", "bg", "hr", "sl", "et", "lv", "lt"}


def asr_status() -> dict[str, Any]:
    """Public status for /api/transcribe/status (and UI)."""
    preferred = (settings.asr_backend or "auto").strip().lower()
    backends: list[str] = []
    try:
        import onnx_asr  # noqa: F401

        backends.append("parakeet")
    except ImportError:
        pass
    try:
        import faster_whisper  # noqa: F401

        backends.append("faster-whisper")
    except ImportError:
        pass
    try:
        import whisper  # noqa: F401

        backends.append("openai-whisper")
    except ImportError:
        pass

    if not backends:
        return {
            "ok": False,
            "backend": None,
            "engine": None,
            "model": None,
            "hint": 'pip install "onnx-asr[cpu,hub]"  (Parakeet — same engine Hex uses)',
            "speak": "Browser speech only — install onnx-asr for accurate mic.",
            "backends": [],
        }

    if preferred == "whisper":
        engine = next((b for b in backends if "whisper" in b), backends[0])
    elif preferred == "parakeet":
        engine = "parakeet" if "parakeet" in backends else backends[0]
    else:
        engine = "parakeet" if "parakeet" in backends else backends[0]

    model = settings.asr_model if engine == "parakeet" else settings.whisper_model
    label = (
        f"Accurate mic ready ({engine} · {model})"
        if engine == "parakeet"
        else f"Accurate mic ready ({engine})"
    )
    return {
        "ok": True,
        "backend": engine,
        "engine": engine,
        "model": model,
        "backends": backends,
        "speak": label,
        "note": "Parakeet = Hex-style ASR. Whisper kept as Hindi/fallback.",
    }


def whisper_status() -> dict[str, Any]:
    """Back-compat alias."""
    return asr_status()


def _load_parakeet():
    global _model, _backend, _model_name
    if _backend == "parakeet" and _model is not None:
        return _model, _backend
    import onnx_asr

    name = settings.asr_model or "nemo-parakeet-tdt-0.6b-v3"
    # Prefer CUDA / DirectML when present; onnx-asr picks providers itself.
    _model = onnx_asr.load_model(name)
    _backend = "parakeet"
    _model_name = name
    return _model, _backend


def _load_whisper():
    global _model, _backend, _model_name
    if _backend and "whisper" in _backend and _model is not None:
        return _model, _backend
    try:
        from faster_whisper import WhisperModel

        device = "cuda"
        wmodel = settings.whisper_model or "small"
        try:
            _model = WhisperModel(wmodel, device=device, compute_type="float16")
        except Exception:
            _model = WhisperModel("base", device="cpu", compute_type="int8")
        _backend = "faster-whisper"
        _model_name = wmodel
        return _model, _backend
    except ImportError:
        pass
    import whisper

    _model = whisper.load_model(settings.whisper_model or "base")
    _backend = "openai-whisper"
    _model_name = settings.whisper_model or "base"
    return _model, _backend


def _pick_backend(language: str | None) -> str:
    preferred = (settings.asr_backend or "auto").strip().lower()
    lang = ((language or "en").strip().lower()[:2]) or "en"
    status = asr_status()
    available = set(status.get("backends") or [])

    if preferred == "whisper" and any("whisper" in b for b in available):
        return "whisper"
    if preferred == "parakeet" and "parakeet" in available:
        return "parakeet"

    # auto: Parakeet for supported langs, Whisper for Hindi / others when available
    if lang in _PARAKEET_LANGS and "parakeet" in available:
        return "parakeet"
    if any("whisper" in b for b in available):
        return "whisper"
    if "parakeet" in available:
        return "parakeet"
    raise RuntimeError("No ASR backend installed")


def _transcribe_file(path: Path, *, language: str | None = None) -> tuple[str, str]:
    choice = _pick_backend(language)
    lang = (language or "").strip() or None
    if lang and len(lang) > 2:
        lang = lang[:2]

    if choice == "parakeet":
        model, backend = _load_parakeet()
        result = model.recognize(str(path))
        if isinstance(result, (list, tuple)):
            text = " ".join(str(x).strip() for x in result if str(x).strip())
        else:
            text = str(result or "").strip()
        return text, backend

    model, backend = _load_whisper()
    if backend == "faster-whisper":
        segments, _info = model.transcribe(
            str(path),
            language=lang,
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segments).strip(), backend
    result = model.transcribe(str(path), language=lang, fp16=False)
    return str(result.get("text") or "").strip(), backend


async def transcribe_bytes(data: bytes, *, filename: str = "audio.webm", language: str = "en") -> dict[str, Any]:
    status = asr_status()
    if not status.get("ok"):
        return {"ok": False, **status}

    suffix = Path(filename).suffix or ".webm"
    tmp_dir = Path(tempfile.mkdtemp(prefix="voxoryl_stt_"))
    raw_path = tmp_dir / f"in{suffix}"
    wav_path = tmp_dir / "in.wav"
    try:
        raw_path.write_bytes(data)
        ffmpeg = shutil.which("ffmpeg")
        path_for_model = raw_path
        if ffmpeg and suffix.lower() not in {".wav", ".mp3", ".flac"}:
            proc = await asyncio.create_subprocess_exec(
                ffmpeg,
                "-y",
                "-i",
                str(raw_path),
                "-ar",
                "16000",
                "-ac",
                "1",
                str(wav_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            if wav_path.exists():
                path_for_model = wav_path

        text, backend = await asyncio.to_thread(
            _transcribe_file, path_for_model, language=language or "en"
        )
        if not text:
            return {"ok": False, "error": "empty transcript", "speak": "Couldn't catch that — try again."}
        return {
            "ok": True,
            "text": text,
            "backend": backend,
            "model": _model_name or status.get("model"),
            "speak": text,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "speak": f"Transcribe failed: {exc}"}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
