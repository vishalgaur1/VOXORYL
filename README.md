# VOXORYL

**VOXORYL** (*vox-OR-ill*) — Voice Operating eXecutive · On-device Reasoning that Yields Local action.

Official name **VOXORYL**; short name **Voxy** (wake: **Hey Voxy**).

Local always-on assistant (Ollama by default) with a voice widget, council, tools, private knowledge log, and auto mind map.

## What it can do

VOXORYL is a **desktop companion**, not a chat site with optional plugins. Talk to it, keep context private on disk, and — when you enable it — let it look at the screen and act. Depth varies by OS and flags; the product is designed to **degrade honestly** instead of pretending.

### Voice companion

- **Wake:** say **“Hey Voxy”** (also “Hey Voxoryl” / “Okay Voxy”) — ambient wake-only on the orb until you’re addressed.
- **Talk or Council** through the voice widget; ASR (Parakeet primary, Whisper / browser speech fallback) and TTS for replies.
- **L0-R stop/cancel/pause:** realtime commands (`stop`, `cancel`, `pause`, `resume`, `quiet`, `listen`, `sleep`) bypass the LLM — interrupt work and speech immediately.

### Local-first reasoning (+ optional cloud)

- **On-device by default** via [Ollama](https://ollama.com) (fast model for greetings, main model for work; optional vision + embeddings).
- **Cloud toggle** (Groq / Gemini / OpenRouter / NVIDIA, etc.) speeds general chat when you want it — **tools and computer-use still run on your PC**.
- Sensitive flows (companion, QnA, identity-style data) stay **local-only** per privacy rules.

### Apps, Chrome & YouTube

- **Open apps** (Chrome, Edge, Notepad, VS Code/Cursor, Spotify, and more on Windows; macOS uses best-effort `open -a`).
- **Chrome:** open with a configured profile, new tab / URL, tab search — including Hindi-friendly open phrases where wired.
- **YouTube:** open the site, wait until the page is ready, then **play the Nth video** (first / second / “video 3”, …) via screen grounding — not a blind sleep-and-hope click.
- **Wait-for-page:** navigation and content gates (`wait_for`) require evidence (URL / title / optional CDP) before acting; timeouts fail closed.

### Screen observe → click / type at *x, y*

- With **`COMPUTER_USE_ENABLED=true`**: screenshot → observe (UIA and/or vision) → **click or type at absolute screen coordinates** (DPI-aware on Windows).
- Optional **Chrome CDP** (`--remote-debugging-port=9222` + Playwright) accelerates DOM/bbox and URL verify; **coordinate grounding works without CDP**.
- Default is **off** — enable deliberately in `.env`. Failsafe / owned-write safety apply when control is on.

### Windows control (where supported)

- Focus windows, **Notepad** (including app-scoped new tab where supported), brightness / volume, PowerToys bridges (launcher, Awake, FancyZones, …) on Windows.
- **macOS:** API + widget run; full Win32/UIA computer-use is **not** supported — desktop automation degrades with clear limits (see [Platform notes](#platform-notes)).

### Silent action mode

- Pure actions (open app, new tab, verified click/play) can complete **without narrating** (“Opening…”, reading URLs aloud). Success stays quiet; honest short failure when something didn’t verify.

### Memory & context

- Private **`data/`** store: profile/facts, knowledge log, skills, chat session summary, cold memory, optional mind map — gitignored, never shipped in clones.
- Retrieval and session continuity for follow-ups; vault facts are gated so greetings don’t dump your whole knowledge base.

### Doctor & Safe Mode

- **Doctor** (`GET /api/doctor` or ask about health): Ollama, ASR, hardware, WebView2 hint, optional Playwright, supervisor — guides missing deps without crashing the product.
- **Safe Mode** blocks high-risk capabilities (screen / chrome / computer / shell-style paths) while you diagnose.

### Widget UI

- Native **voice orb** (pywebview / WebView2 on Windows; browser `--app` fallback) at `/widget`.
- Command center dashboard at `/`, mind map at `/mindmap` — Local/Cloud toggle, mic/speaker pickers, Talk vs Council.
- **Reels** quick action + Settings panel: paste an Instagram Reel URL or drop a short video into Voxy’s private inbox (`data/reels/`).

### Instagram (VOXORYL’s own account)

VOXORYL can **receive** Reels you share (inbox above) without logging into Instagram.

To **post as VOXORYL**, create a Professional (Business/Creator) Instagram account for the product, link it to a Facebook Page, and a Meta developer app with Instagram Content Publishing:

1. [Meta for Developers](https://developers.facebook.com/) → create an app → add **Instagram** product.
2. Convert/create an IG Professional account; get the **Instagram Business Account ID**.
3. Generate a long-lived **Page/User access token** with `instagram_basic`, `instagram_content_publish`, `pages_show_list` (and related permissions Meta currently requires).
4. Copy into `.env` (never commit): `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_BUSINESS_ACCOUNT_ID` (optional `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET` / `INSTAGRAM_GRAPH_VERSION`).
5. Call `publish_reel(video_url, caption)` via `voxoryl.instagram` or `POST /api/reels/publish` — Graph needs a **public HTTPS video URL** in this P0 scaffold (local-file resumable upload is not wired yet).

**ToS:** public oEmbed or files you upload only — no credential stuffing, no private-session scraping.

### Windows vs macOS (honest matrix)

| Area | Windows | macOS |
|---|---|---|
| API, voice widget, memory, Doctor | Yes | Yes |
| Native orb | Yes (WebView2) | Yes when pywebview works; else browser |
| Win32 / UIA computer-use & PowerToys | Yes when `COMPUTER_USE_ENABLED` | Not supported — limited / best-effort only |
| Chrome open / profile / YouTube coords | Strongest path | Best-effort open; CU limited |
| Chrome CDP accelerator | Optional | Optional if Chrome debug port is up |

## Quick start (Windows + macOS)

**Requirements:** Python **3.11+**, optional [Ollama](https://ollama.com/download) for local models.

```bash
git clone https://github.com/vishalgaur1/VOXORYL.git
cd VOXORYL

python -m venv .venv

# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1
# macOS / Linux
# source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
# Edit .env — at minimum leave local mode, or add GROQ_API_KEY for cloud chat
```

### Launch

```bash
# Cross-platform (recommended)
python scripts/launch_voxoryl.py --console

# Or developer API only
python run.py

# macOS helper
chmod +x scripts/launch_voxoryl.sh
./scripts/launch_voxoryl.sh
```

**Windows product launch** (silent native widget):

```powershell
wscript .\scripts\launch-voxoryl.vbs
# or
.\.venv\Scripts\python.exe .\scripts\launch_voxoryl.py --native
```

First run bootstraps private `data/` from `setup/templates/`. Doctor: `GET /api/doctor` or talk to Voxoryl about health checks — missing deps should guide you without crashing.

Optional Ollama models:

```bash
ollama pull qwen3.5:4b
ollama pull nomic-embed-text
# vision (if VRAM allows): ollama pull qwen2.5vl:3b
```

- Command center: http://127.0.0.1:3847  
- Voice widget: http://127.0.0.1:3847/widget  
- Mind map: http://127.0.0.1:3847/mindmap  

## Configure (`.env`)

Copy `.env.example` → `.env`. Never commit `.env`.

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` / `GEMINI_API_KEY` / … | Optional cloud chat |
| `VOXORYL_OWNER_NAME` / `VOXORYL_OWNER_EMAIL` | Optional Chrome “my profile” matching |
| `COMPUTER_USE_ENABLED` | Screen click/type (default `false`) |
| `OBSIDIAN_VAULT`, `GITHUB_TOKEN`, `EMAIL_IMAP_*` | Optional connectors |
| `INSTAGRAM_ACCESS_TOKEN`, `INSTAGRAM_BUSINESS_ACCOUNT_ID` | Optional — post Reels *as* VOXORYL via Meta Graph |

See [`.env.example`](.env.example) for the full list.

## Privacy

- Everything under `data/` is **gitignored** (knowledge, memory, logs, DBs).
- Clones get `setup/` templates only — never your facts.
- Local-only by default for sensitive chat kinds (`setup/privacy.json`).
- Read [`SECURITY.md`](SECURITY.md): never commit `.env`; **rotate keys** if they were ever exposed.

## Platform notes

| Feature | Windows | macOS |
|---|---|---|
| API + voice widget | Yes | Yes |
| Native pywebview orb | Yes (WebView2) | Yes when pywebview works; else browser |
| Win32 / UIA computer-use | Yes when enabled | Not supported — honest degrade |
| Chrome open / profile dir | Yes | Best-effort via `open -a` |
| Autostart Desktop shortcut | Yes (`scripts/*.ps1`) | Use Login Items / launchd yourself |

## Repository layout

```
voxoryl/           # Python package (API, agents, tools, static UI)
scripts/           # Launchers, probes, packaging
setup/             # Example configs + bootstrap templates
docs/              # PRD and product docs
tests/             # Smoke tests
.github/workflows/ # Release zip scaffold
.env.example       # Required keys (placeholders only)
```

## Download a Release (no git required)

Non-developers can grab a zip from [GitHub Releases](https://github.com/vishalgaur1/VOXORYL/releases) instead of cloning.

**How it works:** push a version tag (`v0.2.0`, `v0.3.0`, …) → GitHub Actions ([`.github/workflows/release.yml`](.github/workflows/release.yml)) builds a portable zip on Windows, macOS, and Linux runners → assets attach to that Release.

**What you get today:** a **cross-platform Python source package** (`VOXORYL-<version>-source.zip`) with launch scripts — not a frozen EXE/.app yet. Same zip works on Win / Mac / Linux. Native installers are on the roadmap.

### Install from the zip

1. Download **`VOXORYL-*-source.zip`** from the latest [Release](https://github.com/vishalgaur1/VOXORYL/releases) (platform-named zips are the same source tree if you prefer that filename).
2. Unzip; install [Python 3.11+](https://www.python.org/downloads/).
3. In the unzipped folder:

```bash
python -m venv .venv

# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1
# macOS / Linux
# source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
# Edit .env if needed — never commit real keys
```

4. Run:

```bash
python scripts/launch_voxoryl.py --console
# Windows silent orb: wscript .\scripts\launch-voxoryl.vbs
# macOS/Linux: chmod +x scripts/launch_voxoryl.sh && ./scripts/launch_voxoryl.sh
```

Secrets (`.env`), private `data/`, and `.venv` are **never** included in Release zips. Maintainers can rebuild locally with `python scripts/package_release.py`.

## Windows extras

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-windows.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\install-desktop-shortcut.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\register-autostart-windows.ps1
```

## License

Non-commercial use is free under [PolyForm Noncommercial 1.0.0](LICENSE).  
Commercial use requires a paid license — contact **vishalgaur2002@gmail.com**  
(see [`LICENSE`](LICENSE) and [`COMMERCIAL_LICENSE.md`](COMMERCIAL_LICENSE.md)).
