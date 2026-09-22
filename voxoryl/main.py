from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from voxoryl.agent import route_and_act, synthesize_speech
from voxoryl.config import settings
from voxoryl.council import run_council
from voxoryl.daemon import daily_autonomy_tick, start_daemon, stop_daemon
from voxoryl.knowledge import knowledge, tool_knowledge
from voxoryl.llm import list_local_models, ollama_available
from voxoryl.memory import memory, skills
from voxoryl.mindmap import mindmap
from voxoryl.code_act import tool_code_act
from voxoryl.mcp_bridge import tool_mcp
from voxoryl.screen import computer_use_enabled, tool_screen
from voxoryl.tools import (
    tool_computer,
    tool_email,
    tool_github,
    tool_market_scan,
    tool_marketing,
    tool_notes,
    tool_remember,
    tool_research,
    tool_video,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
# Process-start stamp; combined with asset mtimes so Reload widget picks up edits without restart.
STATIC_VERSION = str(int(__import__("time").time()))


def _static_bust() -> str:
    stamps = [int(float(STATIC_VERSION))]
    for name in ("widget.css", "orb.js", "widget.js", "widget.html"):
        path = STATIC_DIR / name
        if path.exists():
            try:
                stamps.append(int(path.stat().st_mtime))
            except OSError:
                pass
    return str(max(stamps))


def _widget_html() -> str:
    html = (STATIC_DIR / "widget.html").read_text(encoding="utf-8")
    v = _static_bust()
    for name in ("widget.css", "orb.js", "widget.js"):
        html = html.replace(f"/assets/{name}", f"/assets/{name}?v={v}")
    return html


@asynccontextmanager
async def lifespan(_: FastAPI):
    from voxoryl.bootstrap import ensure_private_layout

    boot = ensure_private_layout()
    settings.voxoryl_data_dir.mkdir(parents=True, exist_ok=True)
    settings.voxoryl_workspace.mkdir(parents=True, exist_ok=True)
    try:
        from voxoryl.supervisor import get_supervisor

        await get_supervisor().start()
    except Exception:
        pass
    for skill in [
        (
            "friendly_pro",
            "Friendly Professional",
            "Always warm, clear, and professional — never cold, never butler fluff.",
            True,
        ),
        (
            "caveman",
            "Concise",
            "Keep replies short and actionable. Friendly + professional, not rude.",
            True,
        ),
        (
            "no_flattery",
            "No Flattery",
            "No fake praise or dramatizing. Encourage with facts.",
            True,
        ),
        ("receipts_only", "Receipts Only", "When using knowledge, name the source in a few words.", True),
        ("council_first", "Council First", "Only for hard/ambiguous work — not every chat.", False),
        ("remember_patterns", "Remember Patterns", "Store what the owner wanted and what worked.", True),
        ("knowledge_log", "Knowledge Log", "File lasting facts under topic headings in data/knowledge.md.", True),
        ("knowledge_reference", "Knowledge Reference", "When the owner asks about known topics, retrieve and cite the knowledge log.", True),
        ("research_market", "Market Research", "When building or idea-hunting, run a market scan.", False),
        ("notes_capture", "Notes Capture", "Save useful conclusions into notes/Obsidian.", False),
        ("one_tool", "One Tool", "Prefer one clear tool call over multi-step theatre.", True),
        ("companion_mirror", "Companion Mirror", "Brutal-honest chat export analysis stays local-only; fair split of ownership.", True),
        ("food_taste", "Food Taste", "Remember order-history likes (Swiggy/Zomato/etc) locally.", True),
        ("health_flags", "Health Flags", "Optional food compound flags — habit nudge only, not medical care.", False),
        ("spend_coach", "Spend Coach", "Bank statement → category coach; ask before assuming cutback hardness.", True),
        ("wellbeing_nudge", "Wellbeing Nudge", "Optional check-ins up to 2x/day — not therapy.", False),
        ("deadline_todos", "Deadline Todos", "Refuse tasks without deadlines — no deadline, no task.", True),
        ("neuro_adapt", "Short Focus", "Optional short-reply mode if owner asks — not a medical product.", False),
        (
            "research_weighted",
            "Research Weighted",
            "Always research online first: docs/manuals > Wikipedia/SO/MDN > Reddit opinions (discounted) → conclusion.",
            True,
        ),
        ("android_lab", "Android Lab", "Emulator E2E: start AVD, install APK, smoke/monkey, screenshot report.", False),
        ("hv_sandbox", "Hyper-V Sandbox", "Disposable VM for untrusted software → verdict → destroy to free space.", False),
        ("camera_see", "Camera See", "Webcam: see what you're showing via local vision model.", True),
        ("jack_of_trades", "Jack of Trades", "Generalist local agent — build, test, research, automate. Not a medical product.", True),
        ("music_taste", "Music Taste", "Ingest playlists; remember artists/genres; suggest similar songs from genuine research.", True),
        ("books_vault", "Books Vault", "Local book titles + great lines; occasional mild surface.", True),
        ("style_colors", "Style Colors", "Photo → colors that suit; genuine fashion sites; ask value_mode first.", True),
        (
            "curiosity_growth",
            "Curiosity Growth",
            "ALWAYS ON: tastefully enrich with genuine interesting sites/ideas — become a more curious human.",
            True,
        ),
        ("windows_ops", "Windows Ops", "Brightness/volume/PowerToys helpers on Windows.", True),
        (
            "software_knowledge",
            "Software Knowledge",
            "Prefer known app shortcuts from data/software_knowledge over slow vision click loops; auto-learn after successful actions.",
            True,
        ),
        ("net_profile", "Net Profile", "Proxy/VPN helpers — ask first; always reset when work is done.", True),
        ("self_upgrade", "Self Upgrade", "Log gaps/bugs → Cursor agent prompts + PR workflow stub.", True),
        ("multitask", "Multitask", "Run independent tools in parallel (while / at the same time).", True),
    ]:
        skills.add_skill(*skill)
    try:
        from voxoryl.software_knowledge import ensure_seed

        ensure_seed("chrome")
        ensure_seed("windows")
        ensure_seed("powertoys")
    except Exception:
        pass
    # Soft-build empty mindmap after private files exist
    try:
        graph = mindmap.sync_from_knowledge(knowledge.sections())
        mindmap.render_html(graph)
    except Exception:
        pass
    start_daemon()
    yield
    try:
        from voxoryl.supervisor import get_supervisor

        await get_supervisor().stop()
    except Exception:
        pass
    stop_daemon()


app = FastAPI(title="VOXORYL", version="0.2.1", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    # Local companion only — allow any localhost / 127.0.0.1 port + Edge --app null origin.
    allow_origins=[
        "http://127.0.0.1:3847",
        "http://localhost:3847",
        "http://127.0.0.1:3848",
        "http://localhost:3848",
        "http://127.0.0.1:3849",
        "http://localhost:3849",
        "null",
    ],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def no_store_widget_static(request: Request, call_next):
    """Edge --app aggressively caches /assets; never cache the companion shell."""
    response = await call_next(request)
    path = request.url.path
    if path == "/widget" or path.startswith("/assets/widget") or path == "/assets/orb.js":
        response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


class AskBody(BaseModel):
    message: str = Field(min_length=1)
    execute: bool = True
    force_council: bool = False


class RememberBody(BaseModel):
    text: str
    kind: str = "fact"


class ResearchBody(BaseModel):
    query: str


class NoteBody(BaseModel):
    note: str
    title: str | None = None


class SkillBody(BaseModel):
    id: str
    name: str
    description: str
    always: bool = False


class SpeakBody(BaseModel):
    text: str = Field(min_length=1, max_length=1200)


class ComputerBody(BaseModel):
    action: str = "list"
    path: str = ""
    query: str = ""


class GithubBody(BaseModel):
    action: str = "list"
    repo: str = ""
    path: str = "README.md"


class MarketingBody(BaseModel):
    brief: str
    channel: str = "general"


class VideoBody(BaseModel):
    prompt: str
    mode: str = "script"


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico")
async def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon.ico")


@app.get("/widget")
async def widget() -> HTMLResponse:
    return HTMLResponse(
        _widget_html(),
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/mindmap")
async def mindmap_page() -> FileResponse:
    graph = mindmap.sync_from_knowledge(knowledge.sections())
    mindmap.render_html(graph)
    return FileResponse(settings.mindmap_html_path)


@app.get("/api/knowledge/graph")
async def knowledge_graph() -> dict[str, Any]:
    graph = mindmap.sync_from_knowledge(knowledge.sections())
    mindmap.render_html(graph)
    return {"ok": True, "graph": graph, "html": str(settings.mindmap_html_path.resolve())}


@app.post("/api/knowledge/mindmap")
async def rebuild_mindmap() -> dict[str, Any]:
    return await tool_knowledge(action="mindmap")


def _is_desktop_session() -> bool:
    return os.getenv("VOXORYL_DESKTOP_SESSION", "").strip() in {"1", "true", "yes"}


@app.get("/api/status")
async def status() -> dict[str, Any]:
    models = await list_local_models()
    wanted = settings.main_model
    fast = settings.fast_model
    try:
        from voxoryl.screen import control_active

        controlling = bool(control_active())
    except Exception:
        controlling = False
    supervisor_health: dict[str, Any] = {}
    try:
        from voxoryl.supervisor import get_supervisor

        supervisor_health = get_supervisor().health()
    except Exception:
        supervisor_health = {"ok": False}
    return {
        "ok": True,
        "supervisor": supervisor_health,
        "runtime_rules": "voxoryl/RUNTIME_RULES.md",
        "ollama": await ollama_available(),
        "model": wanted,
        "main_model": wanted,
        "fast_model": fast,
        "model_installed": any(wanted in m or m.startswith(wanted.split(":")[0]) for m in models),
        "fast_model_installed": any(fast in m or m.startswith(fast.split(":")[0]) for m in models),
        "models": models,
        "online_advisor": bool(settings.groq_api_key.strip()),
        "data_dir": str(settings.voxoryl_data_dir.resolve()),
        "workspace": str(settings.voxoryl_workspace.resolve()),
        "user_data_dir": str(settings.user_config_path.parent.resolve()),
        "config_env": str(settings.user_config_path.resolve()),
        "daemon": True,
        "desktop_session": _is_desktop_session(),
        "pid": os.getpid(),
        "connectors": {
            "obsidian": bool(
                settings.obsidian_vault
                and str(settings.obsidian_vault).strip()
                and Path(settings.obsidian_vault).exists()
            ),
            "github": bool(settings.github_token or settings.github_user),
            "email": bool(settings.email_imap_host and settings.email_imap_user),
            "comfyui": settings.comfyui_url,
            "tts_voice": settings.tts_voice,
            "computer_use": computer_use_enabled(),
            "vision_model": settings.ollama_vision_model,
            "embed_model": settings.ollama_embed_model,
            "searxng": bool((settings.searxng_url or "").strip()),
            "screen_watch": bool(getattr(settings, "screen_watch_enabled", False))
            and computer_use_enabled(),
            "screen_control_on": computer_use_enabled()
            and bool(getattr(settings, "screen_watch_enabled", False)),
            "screen_control_active": controlling,
            "powertoys": True,
            "software_knowledge": True,
            "content_safety": True,
            "reels": True,
            "instagram_publish": bool(
                (getattr(settings, "instagram_access_token", "") or "").strip()
                and (getattr(settings, "instagram_business_account_id", "") or "").strip()
            ),
        },
        "tools": [
            "research",
            "market_scan",
            "notes",
            "github",
            "email",
            "computer",
            "screen",
            "mcp",
            "code_act",
            "pipeline",
            "ingest",
            "reels",
            "whatsapp",
            "flash_fill",
            "powertoys",
            "chrome",
            "screen_type",
            "melody",
            "media",
            "leads",
            "approvals",
            "sites",
            "qna",
            "companion",
            "bookings",
            "docs",
            "prefs",
            "food",
            "health",
            "spend",
            "wellbeing",
            "todos",
            "planner",
            "sale_watch",
            "i18n",
            "neuro",
            "android",
            "sandbox",
            "camera",
            "music",
            "books",
            "style_colors",
            "curiosity",
            "windows",
            "net_profile",
            "self_upgrade",
            "multitask",
            "marketing",
            "video",
            "memory",
            "knowledge",
            "mindmap",
            "council",
            "voice",
            "models_advisor",
        ],
        "knowledge_path": str(settings.knowledge_path.resolve()),
        "mindmap_url": "/mindmap",
        **_inference_status_fields(),
    }


def _inference_status_fields() -> dict[str, Any]:
    try:
        from voxoryl.inference import status as inference_status

        inf = inference_status()
        return {
            "inference_mode": inf.get("mode") or "local",
            "cloud_configured": bool(inf.get("cloud_ready")),
            "cloud_ready": bool(inf.get("cloud_ready")),
            "cloud_provider": inf.get("provider") or "none",
            "inference_hint": inf.get("cloud_hint") or "",
            "inference": inf,
            "static_version": _static_bust(),
        }
    except Exception:
        return {
            "inference_mode": "local",
            "cloud_configured": bool(settings.groq_api_key.strip()),
            "cloud_ready": bool(settings.groq_api_key.strip()),
            "cloud_provider": "groq" if settings.groq_api_key.strip() else "none",
            "inference_hint": "",
            "static_version": _static_bust(),
        }


@app.post("/api/shutdown")
async def api_shutdown(request: Request) -> dict[str, Any]:
    """Graceful stop for desktop sessions only (localhost)."""
    client = (request.client.host if request.client else "") or ""
    if client not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(status_code=403, detail="local only")
    if not _is_desktop_session():
        raise HTTPException(status_code=403, detail="not a desktop session")

    async def _exit_soon() -> None:
        await asyncio.sleep(0.35)
        try:
            stop_daemon()
        except Exception:
            pass
        os._exit(0)

    asyncio.create_task(_exit_soon())
    return {"ok": True, "stopping": True, "pid": os.getpid()}


class OpenUrlBody(BaseModel):
    url: str = "/"
    path: str | None = None


@app.post("/api/open-url")
async def api_open_url(request: Request, body: OpenUrlBody | None = None) -> dict[str, Any]:
    """Open web dashboard (or another local Voxoryl URL) in the system browser."""
    from voxoryl.lifecycle import open_url_in_browser

    client = (request.client.host if request.client else "") or ""
    if client not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(status_code=403, detail="local only")

    payload = body or OpenUrlBody()
    raw = (payload.path or payload.url or "/").strip() or "/"
    if raw.startswith("/"):
        base = str(request.base_url).rstrip("/")
        target = f"{base}{raw}"
    else:
        target = raw
        if "127.0.0.1" not in target and "localhost" not in target:
            raise HTTPException(status_code=400, detail="local Voxoryl URLs only")

    result = open_url_in_browser(target)
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error") or "open failed")
    return result


@app.get("/api/setup")
async def setup_status() -> dict[str, Any]:
    from voxoryl.bootstrap import ensure_private_layout, load_setup

    setup = load_setup()
    boot = ensure_private_layout()
    return {
        "ok": True,
        "setup_file": "setup/voxoryl.setup.json",
        "manifest": setup,
        "bootstrap": boot,
    }


@app.get("/api/models")
async def models_advice() -> dict[str, Any]:
    from voxoryl.models_setup import recommend_models

    return recommend_models()


@app.get("/api/hardware")
async def hardware() -> dict[str, Any]:
    from voxoryl.hardware import detect_hardware

    return detect_hardware()


class ModelsApplyBody(BaseModel):
    pull: bool = True


@app.post("/api/models/apply")
async def models_apply(body: ModelsApplyBody) -> dict[str, Any]:
    from voxoryl.models_setup import apply_recommendation

    return apply_recommendation(pull=body.pull, use_recommended=True)


@app.post("/api/models/install")
async def models_install() -> dict[str, Any]:
    from voxoryl.models_setup import install_default_models

    return install_default_models(also_recommend=True)


class MelodyBody(BaseModel):
    action: str = "to_midi"
    message: str = ""
    instrument: str = "mellow guitar"
    place_in_daw: bool = False
    audio_path: str = ""


@app.post("/api/melody")
async def melody_api(body: MelodyBody) -> dict[str, Any]:
    from voxoryl.melody import tool_melody

    return await tool_melody(
        action=body.action,
        audio_path=body.audio_path,
        message=body.message,
        instrument=body.instrument,
        place_in_daw=body.place_in_daw,
    )


@app.post("/api/melody/upload")
async def melody_upload_file(
    file: UploadFile = File(...),
    action: str = Form("to_midi"),
    message: str = Form(""),
    instrument: str = Form("mellow guitar"),
    place_in_daw: bool = Form(False),
) -> dict[str, Any]:
    from voxoryl.melody import save_upload, tool_melody

    data = await file.read()
    path = await save_upload(data, file.filename or "hum.wav")
    return await tool_melody(
        action=action,
        audio_path=str(path),
        message=message,
        instrument=instrument,
        place_in_daw=place_in_daw,
    )


class ReelsBody(BaseModel):
    url: str = ""
    caption: str = ""
    message: str = ""
    action: str = "ingest"
    reel_id: str = ""
    video_path: str = ""
    distill: bool = True
    transcribe: bool = True
    collection_id: str = ""
    folder_path: str = ""
    urls_text: str = ""
    use_graph: bool = False
    name: str = ""
    manifest: dict | list | None = None
    manifest_text: str = ""


class ReelsCollectionBody(BaseModel):
    name: str = ""
    urls_text: str = ""
    folder_path: str = ""
    use_graph: bool = False
    distill: bool = True
    transcribe: bool = True
    manifest: dict | list | None = None
    manifest_text: str = ""


@app.get("/api/reels")
async def reels_list(
    limit: int = 50,
    status: str = "",
    collection_id: str = "",
) -> dict[str, Any]:
    from voxoryl.instagram import status as ig_status
    from voxoryl.reels import list_reels

    out = list_reels(limit=limit, status=status, collection_id=collection_id)
    out["instagram"] = ig_status()
    return out


@app.get("/api/reels/collections")
async def reels_collections(limit: int = 30) -> dict[str, Any]:
    from voxoryl.reels import list_collections

    return list_collections(limit=limit)


@app.get("/api/reels/collections/{collection_id}")
async def reels_collection_get(collection_id: str) -> dict[str, Any]:
    from voxoryl.reels import get_collection

    return get_collection(collection_id)


@app.get("/api/reels/quarantined")
async def reels_quarantined(limit: int = 50) -> dict[str, Any]:
    from voxoryl.reels import list_quarantined

    return list_quarantined(limit=limit)


@app.get("/api/reels/learned")
async def reels_learned(collection_id: str = "") -> dict[str, Any]:
    from voxoryl.reels import collection_learned

    return collection_learned(collection_id=collection_id)


@app.post("/api/reels/collections")
async def reels_collections_import(request: Request) -> dict[str, Any]:
    """
    Import a batch of saved Reels.
    - JSON: {urls_text, folder_path, manifest, use_graph, name, …}
    - Multipart: file=ZIP (+ optional urls_text / name form fields)
    """
    from voxoryl.reels import import_collection

    content_type = (request.headers.get("content-type") or "").lower()
    if "multipart/form-data" in content_type:
        form = await request.form()
        upload = form.get("file")
        name = str(form.get("name") or "").strip()
        urls_text = str(form.get("urls_text") or form.get("urls") or "").strip()
        folder_path = str(form.get("folder_path") or "").strip()
        manifest_text = str(form.get("manifest_text") or form.get("manifest") or "").strip()
        use_graph = str(form.get("use_graph") or "").lower() in {"1", "true", "yes"}
        distill_raw = str(form.get("distill") if form.get("distill") is not None else "true").lower()
        transcribe_raw = str(form.get("transcribe") if form.get("transcribe") is not None else "true").lower()
        distill = distill_raw not in {"0", "false", "no"}
        transcribe = transcribe_raw not in {"0", "false", "no"}
        zip_bytes = None
        zip_filename = "collection.zip"
        if upload is not None and hasattr(upload, "read"):
            zip_bytes = await upload.read()
            zip_filename = getattr(upload, "filename", None) or "collection.zip"
        return await import_collection(
            name=name,
            urls_text=urls_text,
            folder_path=folder_path,
            zip_bytes=zip_bytes,
            zip_filename=str(zip_filename),
            manifest_text=manifest_text,
            use_graph=use_graph,
            distill=distill,
            transcribe=transcribe,
        )

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    body = ReelsCollectionBody.model_validate(payload)
    return await import_collection(
        name=body.name,
        urls_text=body.urls_text,
        folder_path=body.folder_path,
        manifest=body.manifest,
        manifest_text=body.manifest_text,
        use_graph=body.use_graph,
        distill=body.distill,
        transcribe=body.transcribe,
    )


@app.get("/api/reels/{reel_id}")
async def reels_get(reel_id: str) -> dict[str, Any]:
    from voxoryl.reels import get_reel

    return get_reel(reel_id)


@app.post("/api/reels")
async def reels_create(request: Request) -> dict[str, Any]:
    """
    Share a Reel with VOXORYL.
    - JSON: {"url": "https://www.instagram.com/reel/…", "caption": "…"}
    - Multipart: file=video + optional url/caption form fields
    """
    from voxoryl.reels import ingest_file, ingest_url, tool_reels

    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        body = ReelsBody.model_validate(payload)
        action = (body.action or "ingest").strip().lower()
        if action in {
            "list",
            "status",
            "about",
            "recall",
            "publish",
            "post",
            "import",
            "collection",
            "import_collection",
            "batch",
            "learned",
            "what_learned",
            "quarantine",
            "quarantined",
            "collections",
        }:
            return await tool_reels(
                action=action,
                url=body.url,
                message=body.message,
                reel_id=body.reel_id,
                video_path=body.video_path,
                caption=body.caption,
                collection_id=body.collection_id,
                folder_path=body.folder_path,
                urls_text=body.urls_text,
                use_graph=body.use_graph,
                name=body.name,
            )
        # Multi-line paste on url/message → collection import
        blob = (body.urls_text or body.url or body.message or "").strip()
        if blob.count("http") > 1 or (body.urls_text and body.urls_text.strip()):
            from voxoryl.reels import import_collection

            return await import_collection(
                name=body.name,
                urls_text=body.urls_text or blob,
                folder_path=body.folder_path,
                manifest=body.manifest,
                manifest_text=body.manifest_text,
                use_graph=body.use_graph,
                distill=body.distill,
                transcribe=body.transcribe,
            )
        target = (body.url or body.message or "").strip()
        if not target:
            raise HTTPException(status_code=400, detail="url or message required")
        return await ingest_url(target, caption_hint=body.caption, distill=body.distill)

    form = await request.form()
    upload = form.get("file")
    url = str(form.get("url") or "").strip()
    caption = str(form.get("caption") or "").strip()
    distill_raw = str(form.get("distill") if form.get("distill") is not None else "true").lower()
    transcribe_raw = str(form.get("transcribe") if form.get("transcribe") is not None else "true").lower()
    distill = distill_raw not in {"0", "false", "no"}
    transcribe = transcribe_raw not in {"0", "false", "no"}

    if upload is not None and hasattr(upload, "read"):
        data = await upload.read()
        filename = getattr(upload, "filename", None) or "reel.mp4"
        # ZIP → collection import
        if str(filename).lower().endswith(".zip"):
            from voxoryl.reels import import_collection

            return await import_collection(
                name=str(form.get("name") or "").strip(),
                urls_text=str(form.get("urls_text") or "").strip(),
                zip_bytes=data,
                zip_filename=str(filename),
                distill=distill,
                transcribe=transcribe,
            )
        return await ingest_file(
            data,
            str(filename),
            url=url,
            caption_hint=caption,
            distill=distill,
            transcribe=transcribe,
        )
    if url:
        return await ingest_url(url, caption_hint=caption, distill=distill)
    raise HTTPException(
        status_code=400,
        detail="Provide JSON {url} or multipart file / form url",
    )


@app.post("/api/reels/publish")
async def reels_publish(body: ReelsBody) -> dict[str, Any]:
    from voxoryl.reels import tool_reels

    return await tool_reels(
        action="publish",
        reel_id=body.reel_id,
        video_path=body.video_path,
        caption=body.caption,
        url=body.url,
        message=body.message,
    )


@app.get("/api/memory")
async def get_memory() -> dict[str, Any]:
    return memory.read()


@app.get("/api/skills")
async def get_skills() -> dict[str, Any]:
    return {"skills": skills.list_skills()}


@app.post("/api/skills")
async def add_skill(body: SkillBody) -> dict[str, Any]:
    return skills.add_skill(body.id, body.name, body.description, body.always)


@app.post("/api/remember")
async def remember(body: RememberBody) -> dict[str, Any]:
    return await tool_remember(body.text, body.kind)


class KnowledgeBody(BaseModel):
    action: str = "log"
    heading: str = ""
    text: str = ""
    message: str = ""


@app.get("/api/knowledge")
async def get_knowledge() -> dict[str, Any]:
    return {
        "ok": True,
        "path": str(knowledge.path.resolve()),
        "headings": knowledge.list_headings(),
        "content": knowledge.read(),
    }


@app.post("/api/knowledge")
async def post_knowledge(body: KnowledgeBody) -> dict[str, Any]:
    return await tool_knowledge(
        action=body.action,
        heading=body.heading,
        text=body.text,
        message=body.message or body.text,
    )


@app.post("/api/research")
async def research(body: ResearchBody) -> dict[str, Any]:
    return await tool_research(body.query)


@app.post("/api/market")
async def market(body: ResearchBody) -> dict[str, Any]:
    return await tool_market_scan(body.query)


@app.post("/api/notes")
async def notes(body: NoteBody) -> dict[str, Any]:
    return await tool_notes(body.note, title=body.title)


@app.post("/api/github")
async def github(body: GithubBody) -> dict[str, Any]:
    return await tool_github(body.action, body.repo, body.path)


@app.post("/api/email")
async def email_inbox() -> dict[str, Any]:
    return await tool_email()


@app.post("/api/computer")
async def computer(body: ComputerBody) -> dict[str, Any]:
    return await tool_computer(body.action, body.path, body.query)


class ScreenBody(BaseModel):
    action: str = "look"
    goal: str = ""
    question: str = ""
    text: str = ""
    rounds: int = 1


@app.post("/api/screen")
async def screen(body: ScreenBody) -> dict[str, Any]:
    return await tool_screen(
        action=body.action,
        goal=body.goal,
        question=body.question,
        text=body.text,
        rounds=body.rounds,
    )


class IngestBody(BaseModel):
    url: str = ""
    text: str = ""
    message: str = ""
    use_screen: bool = False


@app.post("/api/ingest")
async def ingest(body: IngestBody) -> dict[str, Any]:
    from voxoryl.ingest import tool_ingest_share

    return await tool_ingest_share(
        url=body.url,
        text=body.text,
        message=body.message or body.text,
        use_screen=body.use_screen,
    )


class WhatsAppBody(BaseModel):
    action: str = "status"
    message: str = ""
    to: str = ""


@app.post("/api/whatsapp")
async def whatsapp(body: WhatsAppBody) -> dict[str, Any]:
    from voxoryl.ingest import tool_whatsapp

    return await tool_whatsapp(action=body.action, message=body.message, to=body.to)


class IdentityBody(BaseModel):
    full_name: str = ""
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    postal: str = ""
    country: str = ""
    company: str = ""
    job_title: str = ""


@app.get("/api/identity")
async def identity_get() -> dict[str, Any]:
    from voxoryl.identity import resolve_identity

    return {"ok": True, "identity": resolve_identity()}


@app.post("/api/identity")
async def identity_set(body: IdentityBody) -> dict[str, Any]:
    from voxoryl.identity import save_identity_fields

    fields = {k: v for k, v in body.model_dump().items() if v}
    return save_identity_fields(**fields)


@app.get("/api/mcp")
async def mcp_list() -> dict[str, Any]:
    return await tool_mcp("list")


class McpBody(BaseModel):
    action: str = "list"
    server: str = ""
    tool: str = ""
    arguments: dict[str, Any] = {}


@app.post("/api/mcp")
async def mcp_call(body: McpBody) -> dict[str, Any]:
    return await tool_mcp(body.action, body.server, body.tool, body.arguments)


class CodeActBody(BaseModel):
    goal: str


@app.post("/api/code_act")
async def code_act(body: CodeActBody) -> dict[str, Any]:
    return await tool_code_act(body.goal)


@app.post("/api/marketing")
async def marketing(body: MarketingBody) -> dict[str, Any]:
    return await tool_marketing(body.brief, body.channel)


@app.post("/api/video")
async def video(body: VideoBody) -> dict[str, Any]:
    # Prefer auto media loop for generate; script still available via mode=script
    if body.mode in {"generate", "auto", "image"}:
        from voxoryl.media_gen import tool_media

        kind = "video" if "video" in (body.prompt or "").lower() or body.mode == "generate" else "image"
        if body.mode == "image":
            kind = "image"
        return await tool_media(action="auto", brief=body.prompt, kind=kind)
    return await tool_video(body.prompt, body.mode)


class MediaBody(BaseModel):
    brief: str
    kind: str = "image"
    prefer: str = "auto"
    action: str = "auto"
    job_id: str = ""
    notes: str = ""


@app.post("/api/media")
async def media_api(body: MediaBody) -> dict[str, Any]:
    from voxoryl.media_gen import tool_media

    return await tool_media(
        action=body.action,
        brief=body.brief,
        kind=body.kind,
        prefer=body.prefer,
        job_id=body.job_id,
        notes=body.notes,
        message=body.brief,
    )


class LeadsBody(BaseModel):
    action: str = "scout"
    message: str = ""
    niche: str = ""
    location: str = "USA"
    lead_id: str = ""
    limit: int = 8


@app.post("/api/leads")
async def leads_api(body: LeadsBody) -> dict[str, Any]:
    from voxoryl.leads import tool_leads

    return await tool_leads(
        action=body.action,
        message=body.message,
        niche=body.niche,
        location=body.location,
        lead_id=body.lead_id,
        limit=body.limit,
    )


class ApprovalBody(BaseModel):
    action: str = "list"
    approval_id: str = ""
    message: str = ""
    kind: str = ""
    notes: str = ""


@app.post("/api/approvals")
async def approvals_api(body: ApprovalBody) -> dict[str, Any]:
    from voxoryl.approvals import tool_approvals

    return await tool_approvals(
        action=body.action,
        approval_id=body.approval_id,
        message=body.message,
        kind=body.kind,
        notes=body.notes,
    )


class SitesBody(BaseModel):
    action: str = "create"
    brief: str = ""
    message: str = ""
    slug: str = ""
    approval_id: str = ""


@app.post("/api/sites")
async def sites_api(body: SitesBody) -> dict[str, Any]:
    from voxoryl.sites import tool_sites

    return await tool_sites(
        action=body.action,
        brief=body.brief or body.message,
        message=body.message or body.brief,
        slug=body.slug,
        approval_id=body.approval_id,
    )


class QnaBody(BaseModel):
    action: str = "start"
    message: str = ""
    answer: str = ""
    rounds: int = 6


@app.post("/api/qna")
async def qna_api(body: QnaBody) -> dict[str, Any]:
    from voxoryl.qna import tool_qna

    return await tool_qna(
        action=body.action,
        message=body.message,
        answer=body.answer,
        rounds=body.rounds,
    )


class CompanionBody(BaseModel):
    action: str = "analyze"
    path: str = ""
    message: str = ""
    focus: str = ""
    owner_hint: str = ""


@app.post("/api/companion")
async def companion_api(body: CompanionBody) -> dict[str, Any]:
    from voxoryl.companion import tool_companion

    return await tool_companion(
        action=body.action,
        path=body.path,
        message=body.message,
        focus=body.focus,
        owner_hint=body.owner_hint,
    )


@app.get("/api/privacy")
async def privacy_api() -> dict[str, Any]:
    from voxoryl.privacy import privacy_status

    return privacy_status()


class InferenceBody(BaseModel):
    mode: str = "local"
    provider: str = ""


@app.post("/api/inference")
async def inference_set(body: InferenceBody) -> dict[str, Any]:
    from voxoryl.inference import set_mode, set_provider, status

    if (body.provider or "").strip():
        try:
            set_provider(body.provider)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if (body.mode or "").strip():
        return set_mode(body.mode)
    return status()


@app.get("/api/inference")
async def inference_get() -> dict[str, Any]:
    from voxoryl.inference import status

    return status()


@app.post("/api/ask/stream")
async def ask_stream(body: AskBody) -> Any:
    """SSE: token stream for chat turns; full payload for pipelines/tools."""
    import json as _json
    import re as _re

    from fastapi.responses import StreamingResponse

    from voxoryl.agent import _finish_turn, _is_greeting_or_smalltalk, route_and_act
    from voxoryl.inference import effective_mode, missing_cloud_speak, stream_cloud, stream_ollama
    from voxoryl.llm import resolve_chat_model
    from voxoryl.style import speak_system

    async def gen():
        msg = (body.message or "").strip()
        # Pipelines / tools / council: one-shot (tools stay local)
        if body.force_council or not _is_greeting_or_smalltalk(msg):
            try:
                result = await route_and_act(msg, force_council=body.force_council)
                yield f"data: {_json.dumps({'type': 'done', **result})}\n\n"
            except Exception as exc:
                yield f"data: {_json.dumps({'type': 'error', 'speak': str(exc)[:400]})}\n\n"
            return

        try:
            from voxoryl.chat_session import append as chat_append, history_for_llm
            from voxoryl.inference import cloud_ready

            use_cloud = effective_mode() == "cloud"
            if use_cloud and not cloud_ready().get("ok"):
                speak = missing_cloud_speak()
                result = await _finish_turn(
                    msg,
                    {
                        "ok": False,
                        "mode": "chat",
                        "speak": speak,
                        "tools": [],
                        "knowledge": None,
                        "retrieved": [],
                        "council": None,
                    },
                )
                yield f"data: {_json.dumps({'type': 'done', **result})}\n\n"
                return

            chat_append("user", msg)
            hist = history_for_llm(limit=12 if use_cloud else 6)
            system = (
                speak_system(kind="chat")
                + "\nReply in 1-3 short natural sentences. You control this Windows PC via local tools."
                if use_cloud
                else "You are Voxoryl, a friendly local AI. Reply in 1-2 short natural sentences."
            )
            messages = [{"role": "system", "content": system}, *hist]
            parts: list[str] = []
            if use_cloud:
                async for piece in stream_cloud(messages, temperature=0.55, max_tokens=160):
                    parts.append(piece)
                    yield f"data: {_json.dumps({'type': 'token', 'text': piece})}\n\n"
            else:
                model = resolve_chat_model(tier="fast")
                async for piece in stream_ollama(
                    messages, model=model, temperature=0.55, num_predict=96, num_ctx=2048
                ):
                    parts.append(piece)
                    yield f"data: {_json.dumps({'type': 'token', 'text': piece})}\n\n"
            speak = _re.sub(r"[#*`]", "", "".join(parts)).strip()[:600] or "Hey — Voxoryl here."
            result = await _finish_turn(
                msg,
                {
                    "ok": True,
                    "mode": "chat",
                    "speak": speak,
                    "model_tier": "cloud" if use_cloud else "fast",
                    "streamed": True,
                    "tools": [],
                    "knowledge": None,
                    "retrieved": [],
                    "council": None,
                },
                user_already_logged=True,
            )
            yield f"data: {_json.dumps({'type': 'done', **result})}\n\n"
        except Exception as exc:
            try:
                result = await route_and_act(msg, force_council=False)
                yield f"data: {_json.dumps({'type': 'done', **result})}\n\n"
            except Exception:
                yield f"data: {_json.dumps({'type': 'error', 'speak': str(exc)[:400]})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


class BookingsBody(BaseModel):
    action: str = "tickets"
    message: str = ""
    value_mode: str = ""


@app.post("/api/bookings")
async def bookings_api(body: BookingsBody) -> dict[str, Any]:
    from voxoryl.bookings import tool_bookings

    return await tool_bookings(action=body.action, message=body.message, value_mode=body.value_mode)


class DocsBody(BaseModel):
    action: str = "list"
    message: str = ""
    path: str = ""
    tag: str = ""
    label: str = ""


@app.post("/api/docs")
async def docs_api(body: DocsBody) -> dict[str, Any]:
    from voxoryl.docs_vault import tool_docs

    return await tool_docs(
        action=body.action,
        message=body.message,
        path=body.path,
        tag=body.tag,
        label=body.label,
    )


class PrefsBody(BaseModel):
    action: str = "status"
    message: str = ""
    mode: str = ""
    context: str = ""


@app.post("/api/prefs")
async def prefs_api(body: PrefsBody) -> dict[str, Any]:
    from voxoryl.prefs import tool_prefs

    return await tool_prefs(
        action=body.action,
        message=body.message,
        mode=body.mode,
        context=body.context,
    )


class FoodBody(BaseModel):
    action: str = "summary"
    message: str = ""
    path: str = ""


@app.post("/api/food")
async def food_api(body: FoodBody) -> dict[str, Any]:
    from voxoryl.food_memory import tool_food

    return await tool_food(action=body.action, message=body.message, path=body.path)


class HealthBody(BaseModel):
    action: str = "scan"
    message: str = ""
    item: str = ""


@app.post("/api/health")
async def health_api(body: HealthBody) -> dict[str, Any]:
    from voxoryl.health import tool_health

    return await tool_health(action=body.action, message=body.message, item=body.item)


class SpendBody(BaseModel):
    action: str = "coach"
    message: str = ""
    path: str = ""


@app.post("/api/spend")
async def spend_api(body: SpendBody) -> dict[str, Any]:
    from voxoryl.spend import tool_spend

    return await tool_spend(action=body.action, message=body.message, path=body.path)


class WellbeingBody(BaseModel):
    action: str = "check"
    message: str = ""


@app.post("/api/wellbeing")
async def wellbeing_api(body: WellbeingBody) -> dict[str, Any]:
    from voxoryl.wellbeing import tool_wellbeing

    return await tool_wellbeing(action=body.action, message=body.message)


class TodosBody(BaseModel):
    action: str = "list"
    message: str = ""
    title: str = ""
    deadline: str = ""


@app.post("/api/todos")
async def todos_api(body: TodosBody) -> dict[str, Any]:
    from voxoryl.todos import tool_todos

    return await tool_todos(
        action=body.action,
        message=body.message,
        title=body.title,
        deadline=body.deadline,
    )


class NeuroBody(BaseModel):
    action: str = "status"
    message: str = ""
    profiles: str = ""


@app.post("/api/neuro")
async def neuro_api(body: NeuroBody) -> dict[str, Any]:
    from voxoryl.neuro import tool_neuro

    return await tool_neuro(action=body.action, message=body.message, profiles=body.profiles)


class AndroidBody(BaseModel):
    action: str = "status"
    message: str = ""
    avd: str = ""
    apk: str = ""
    package: str = ""


@app.post("/api/android")
async def android_api(body: AndroidBody) -> dict[str, Any]:
    from voxoryl.android_lab import tool_android

    return await tool_android(
        action=body.action,
        message=body.message,
        avd=body.avd,
        apk=body.apk,
        package=body.package,
    )


class SandboxBody(BaseModel):
    action: str = "status"
    message: str = ""
    name: str = ""
    verdict: str = ""
    path: str = ""


@app.post("/api/sandbox")
async def sandbox_api(body: SandboxBody) -> dict[str, Any]:
    from voxoryl.sandbox_hv import tool_sandbox

    return await tool_sandbox(
        action=body.action,
        message=body.message,
        name=body.name,
        verdict=body.verdict,
        path=body.path,
    )


class CameraBody(BaseModel):
    action: str = "see"
    message: str = ""
    path: str = ""
    camera_index: int = 0


@app.post("/api/camera")
async def camera_api(body: CameraBody) -> dict[str, Any]:
    from voxoryl.camera import tool_camera

    return await tool_camera(
        action=body.action,
        message=body.message,
        path=body.path,
        camera_index=body.camera_index,
    )


class MusicBody(BaseModel):
    action: str = "summary"
    message: str = ""
    text: str = ""


@app.post("/api/music")
async def music_api(body: MusicBody) -> dict[str, Any]:
    from voxoryl.music_taste import tool_music

    return await tool_music(action=body.action, message=body.message, text=body.text)


class BooksBody(BaseModel):
    action: str = "random"
    message: str = ""


@app.post("/api/books")
async def books_api(body: BooksBody) -> dict[str, Any]:
    from voxoryl.books import tool_books

    return await tool_books(action=body.action, message=body.message)


class StyleColorsBody(BaseModel):
    action: str = "analyze"
    message: str = ""
    path: str = ""
    camera_index: int = 0
    value_mode: str = ""


@app.post("/api/style_colors")
async def style_colors_api(body: StyleColorsBody) -> dict[str, Any]:
    from voxoryl.style_colors import tool_style_colors

    return await tool_style_colors(
        action=body.action,
        message=body.message,
        path=body.path,
        camera_index=body.camera_index,
        value_mode=body.value_mode,
    )


class CuriosityBody(BaseModel):
    action: str = "surprise"
    message: str = ""


@app.post("/api/curiosity")
async def curiosity_api(body: CuriosityBody) -> dict[str, Any]:
    from voxoryl.curiosity import tool_curiosity

    return await tool_curiosity(action=body.action, message=body.message)


class WindowsBody(BaseModel):
    action: str = "status"
    message: str = ""
    level: int | None = None


@app.post("/api/windows")
async def windows_api(body: WindowsBody) -> dict[str, Any]:
    from voxoryl.windows_ops import tool_windows

    return await tool_windows(action=body.action, message=body.message, level=body.level)


@app.get("/api/safety")
async def safety_api() -> dict[str, Any]:
    from voxoryl.safety import status as safety_status

    return safety_status()


@app.get("/api/runtime")
async def runtime_api() -> dict[str, Any]:
    from voxoryl.cache_layer import get_cache
    from voxoryl.capabilities import get_registry
    from voxoryl.events_db import status as db_status
    from voxoryl.resource_manager import get_resources
    from voxoryl.state_store import get_state
    from voxoryl.supervisor import get_supervisor

    return {
        "ok": True,
        "supervisor": get_supervisor().health(),
        "state": get_state().snapshot(),
        "resources": get_resources().status(),
        "cache": get_cache().stats(),
        "capabilities": [c.name for c in get_registry().list()],
        "database": db_status(),
    }


@app.post("/api/runtime/kill")
async def runtime_kill() -> dict[str, Any]:
    from voxoryl.supervisor import get_supervisor

    return await get_supervisor().kill_switch()


@app.post("/api/runtime/safe_mode")
async def runtime_safe_mode(request: Request) -> dict[str, Any]:
    body = await request.json()
    from voxoryl.doctor import set_safe_mode

    return set_safe_mode(bool(body.get("on", True)))


@app.get("/api/doctor")
async def doctor_api() -> dict[str, Any]:
    from voxoryl.doctor import run_doctor

    return await run_doctor()


@app.post("/api/realtime/partial")
async def realtime_partial(request: Request) -> dict[str, Any]:
    """ASR partial → L0-R / RealtimeLoop (no LLM)."""
    body = await request.json()
    from voxoryl.realtime_loop import get_realtime

    text = str(body.get("text") or "")
    conf = float(body.get("confidence") or 0.5)
    out = await get_realtime().on_asr_partial(text, confidence=conf)
    return out or {"ok": True, "handled": False}


@app.post("/api/realtime/final")
async def realtime_final(request: Request) -> dict[str, Any]:
    body = await request.json()
    from voxoryl.audio_runtime import get_audio_runtime
    from voxoryl.events_db import log_transcription
    from voxoryl.realtime_loop import get_realtime
    from voxoryl.speech_normalizer import normalize_transcript

    raw = str(body.get("text") or body.get("raw") or "")
    norm = normalize_transcript(raw)
    get_audio_runtime().finalize(raw, asr_confidence=float(body.get("confidence") or 1.0))
    log_transcription(norm["raw_text"], norm["normalized_text"], asr_confidence=float(body.get("confidence") or 1.0))
    out = await get_realtime().on_asr_final(
        norm["raw_text"],
        norm["normalized_text"],
        asr_confidence=float(body.get("confidence") or 1.0),
        endpoint_confidence=float(body.get("endpoint_confidence") or 1.0),
        command_text=str(norm.get("command_text") or norm["normalized_text"]),
    )
    return out or {"ok": True, "handled": False, "normalized": norm["normalized_text"]}


class NetProfileBody(BaseModel):
    action: str = "status"
    message: str = ""
    server: str = ""
    confirm: bool = False


@app.post("/api/net_profile")
async def net_profile_api(body: NetProfileBody) -> dict[str, Any]:
    from voxoryl.net_profile import tool_net_profile

    return await tool_net_profile(
        action=body.action,
        message=body.message,
        server=body.server,
        confirm=body.confirm,
    )


class SelfUpgradeBody(BaseModel):
    action: str = "log"
    message: str = ""
    kind: str = ""


@app.post("/api/self_upgrade")
async def self_upgrade_api(body: SelfUpgradeBody) -> dict[str, Any]:
    from voxoryl.self_upgrade import tool_self_upgrade

    return await tool_self_upgrade(action=body.action, message=body.message, kind=body.kind)


class MultitaskBody(BaseModel):
    action: str = "run"
    message: str = ""
    job_id: str = ""


@app.post("/api/multitask")
async def multitask_api(body: MultitaskBody) -> dict[str, Any]:
    from voxoryl.multitask import tool_multitask

    return await tool_multitask(action=body.action, message=body.message, job_id=body.job_id)


class PlannerBody(BaseModel):
    action: str = "create"
    message: str = ""
    kind: str = ""
    plan_id: str = ""


@app.post("/api/planner")
async def planner_api(body: PlannerBody) -> dict[str, Any]:
    from voxoryl.planner import tool_planner

    return await tool_planner(
        action=body.action,
        message=body.message,
        kind=body.kind,
        plan_id=body.plan_id,
    )


class SaleWatchBody(BaseModel):
    action: str = "add"
    message: str = ""
    watch_id: str = ""


@app.post("/api/sale_watch")
async def sale_watch_api(body: SaleWatchBody) -> dict[str, Any]:
    from voxoryl.sale_watch import tool_sale_watch

    return await tool_sale_watch(action=body.action, message=body.message, watch_id=body.watch_id)


class I18nBody(BaseModel):
    action: str = "status"
    message: str = ""
    lang: str = ""


@app.get("/api/i18n")
async def i18n_get() -> dict[str, Any]:
    from voxoryl.i18n_voice import current_language

    return current_language()


@app.post("/api/i18n")
async def i18n_api(body: I18nBody) -> dict[str, Any]:
    from voxoryl.i18n_voice import tool_i18n

    return await tool_i18n(action=body.action, message=body.message, lang=body.lang)


@app.post("/api/ask")
async def ask(body: AskBody) -> dict[str, Any]:
    return await route_and_act(body.message, force_council=body.force_council)


@app.get("/api/chat")
async def chat_status() -> dict[str, Any]:
    from voxoryl.chat_session import load

    data = load()
    msgs = data.get("messages") or []
    return {
        "ok": True,
        "message_count": len(msgs),
        "summary": str(data.get("summary") or ""),
        "summary_chars": len(str(data.get("summary") or "")),
        "updated_at": data.get("updated_at"),
        "messages": msgs[-40:],
        "preview": msgs[-6:],
    }


@app.post("/api/chat/clear")
async def chat_clear() -> dict[str, Any]:
    from voxoryl.chat_session import clear

    clear()
    return {"ok": True, "speak": "Fresh chat — context cleared."}


@app.post("/api/council")
async def council_only(body: AskBody) -> dict[str, Any]:
    return await run_council(body.message, execute=body.execute)


@app.post("/api/speak")
async def speak(body: SpeakBody) -> Response:
    audio = await synthesize_speech(body.text)
    if not audio:
        raise HTTPException(status_code=503, detail="TTS unavailable — install edge-tts or use browser speech")
    return Response(content=audio, media_type="audio/mpeg")


@app.get("/api/transcribe/status")
async def transcribe_status() -> dict[str, Any]:
    from voxoryl.transcribe import asr_status

    return asr_status()


@app.post("/api/transcribe")
async def transcribe_api(file: UploadFile = File(...), language: str = "en") -> dict[str, Any]:
    from voxoryl.transcribe import transcribe_bytes

    data = await file.read()
    return await transcribe_bytes(data, filename=file.filename or "audio.webm", language=language)


@app.post("/api/daily/run")
async def run_daily() -> dict[str, Any]:
    return await daily_autonomy_tick()
