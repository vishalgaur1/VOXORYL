# FAQ

## How do I install?

Python **3.11+**, optional [Ollama](https://ollama.com/download). Clone the repo, create a venv, `pip install -e .`, then `python -m voxoryl`. Or grab a Win/Mac zip from [Releases](https://github.com/vishalgaur1/VOXORYL/releases).

Step-by-step: [README → Quick start](https://github.com/vishalgaur1/VOXORYL#quick-start).

## How do I wake it?

Say **“Hey Voxy”** (also “Hey Voxoryl” / “Okay Voxy”). You’ll see the floating voice orb. Realtime stop/cancel/pause commands bypass the LLM.

## Where does my data live?

Personal files stay **out of the git clone** by default:

| OS | Root |
|---|---|
| **Windows** | `%LOCALAPPDATA%\VOXORYL` (usually `C:\Users\<you>\AppData\Local\VOXORYL`) |
| **macOS** | `~/Library/Application Support/VOXORYL/` |
| **Linux** | `~/.local/share/voxoryl/` |

Inside that folder: `config.env`, `data/` (memory, knowledge, reels, logs), and a workspace sandbox. Override with `VOXORYL_USER_DATA_DIR` if you need to.

Contributors can set `VOXORYL_USE_REPO_DATA=1` to keep data next to the repo (legacy / dev).

## Windows vs Mac?

Both run the API, voice widget, memory, and Doctor. Native orb is strongest on Windows (WebView2). Computer-use (screen click/type) is Windows-first when enabled; Mac is best-effort / limited. Chrome & YouTube work on both, with Windows the stronger path for coordinate control.

## Is it cloud-only?

No. Default reasoning is **on-device via Ollama**. Optional cloud (Groq / Gemini / etc.) is for general chat — tools still run on your PC. Sensitive flows stay local-only.

## Something broke — what now?

Use **Doctor** / Safe Mode from the product UI or docs in the README. Prefer filing a real [bug report](https://github.com/vishalgaur1/VOXORYL/issues/new?template=bug_report.yml) with OS + steps.
