from __future__ import annotations

"""
Camera vision — see what you're showing Voxoryl (webcam / phone cam as webcam).
Uses local vision model; does NOT require mouse/keyboard computer-use.
"""

import asyncio
import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from voxoryl.config import settings
from voxoryl.memory import memory


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cam_dir() -> Path:
    p = settings.voxoryl_data_dir / "camera"
    p.mkdir(parents=True, exist_ok=True)
    return p


def capture_webcam(camera_index: int = 0) -> dict[str, Any]:
    """Grab one frame from a webcam via OpenCV or Pillow fallbacks."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = _cam_dir() / f"cam-{stamp}.jpg"

    # OpenCV path
    try:
        import cv2  # type: ignore

        cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            cap = cv2.VideoCapture(camera_index)
        if not cap.isOpened():
            return {
                "ok": False,
                "error": "camera not opened",
                "hint": "Plug in a webcam or enable phone-as-webcam. pip install opencv-python",
            }
        # warm-up frames
        frame = None
        for _ in range(8):
            ok, frame = cap.read()
            if ok and frame is not None:
                break
        cap.release()
        if frame is None:
            return {"ok": False, "error": "no frame from camera"}
        # resize for VL
        h, w = frame.shape[:2]
        max_w = 1280
        if w > max_w:
            scale = max_w / w
            frame = cv2.resize(frame, (max_w, int(h * scale)))
        cv2.imwrite(str(path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        return {
            "ok": True,
            "path": str(path.resolve()),
            "width": int(frame.shape[1]),
            "height": int(frame.shape[0]),
            "engine": "opencv",
            "at": _now(),
        }
    except ImportError:
        pass
    except Exception as exc:
        return {"ok": False, "error": str(exc), "hint": "pip install opencv-python"}

    return {
        "ok": False,
        "error": "OpenCV not installed",
        "hint": "pip install opencv-python  — then: look at this / what am I showing you",
    }


async def vision_on_image(path: str, question: str = "") -> dict[str, Any]:
    """Describe a local image with Ollama VL — no computer-use gate."""
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"image missing: {path}"}
    prompt = (question or "").strip() or (
        "The user is showing you something through their camera. "
        "Describe clearly what you see: objects, text, diagrams, screens, paper. "
        "If there is text, read it. Be concrete and helpful."
    )
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    payload = {
        "model": settings.ollama_vision_model,
        "messages": [{"role": "user", "content": prompt, "images": [b64]}],
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {"temperature": 0.2, "num_ctx": 4096},
    }
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(f"{settings.ollama_base_url}/api/chat", json=payload)
            if r.status_code >= 400:
                return {"ok": False, "error": r.text, "model": settings.ollama_vision_model}
            content = (r.json().get("message") or {}).get("content", "").strip()
            return {
                "ok": True,
                "model": settings.ollama_vision_model,
                "description": content,
                "path": str(p.resolve()),
            }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "model": settings.ollama_vision_model}


async def see(
    question: str = "",
    *,
    camera_index: int = 0,
    image_path: str = "",
) -> dict[str, Any]:
    if image_path:
        shot = {"ok": True, "path": image_path, "engine": "file"}
    else:
        shot = await asyncio.to_thread(capture_webcam, camera_index)
    if not shot.get("ok"):
        return {**shot, "speak": str(shot.get("hint") or shot.get("error") or "Camera failed.")}

    vision = await vision_on_image(str(shot["path"]), question)
    if not vision.get("ok"):
        return {
            **vision,
            "capture": shot,
            "speak": f"Got a photo but vision failed: {vision.get('error')}",
        }
    desc = str(vision.get("description") or "").strip()
    memory.remember_fact(f"Camera: {desc[:180]}", tags=["camera", "vision"])
    speak = desc[:500] if desc else "I see the frame, but nothing clear to report."
    return {
        "ok": True,
        "capture": shot,
        "vision": vision,
        "speak": speak,
    }


async def tool_camera(
    action: str = "see",
    message: str = "",
    path: str = "",
    camera_index: int = 0,
) -> dict[str, Any]:
    act = (action or "see").lower()
    lower = (message or "").lower()
    if act in {"status"}:
        try:
            import cv2  # noqa: F401

            cv_ok = True
        except ImportError:
            cv_ok = False
        return {
            "ok": True,
            "opencv": cv_ok,
            "vision_model": settings.ollama_vision_model,
            "speak": (
                "Camera ready — say 'look at this' or 'what am I showing you'."
                if cv_ok
                else "Install opencv-python for webcam capture, or pass an image path."
            ),
        }
    q = message
    for prefix in (
        "what am i showing",
        "what am i holding",
        "look at this",
        "see this",
        "camera:",
        "look:",
    ):
        if prefix in lower:
            q = message[lower.index(prefix) + len(prefix) :].strip(" :") or message
            break
    if act in {"capture"} and not path:
        shot = await asyncio.to_thread(capture_webcam, camera_index)
        return {**shot, "speak": f"Saved {shot.get('path')}" if shot.get("ok") else shot.get("speak") or shot.get("error")}
    return await see(q, camera_index=camera_index, image_path=path)
