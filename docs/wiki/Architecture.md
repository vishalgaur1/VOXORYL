# Architecture (simple)

Words-only sketch — not a full design doc.

```
You (mic / widget)
    → wake word / ASR
    → local brain (Ollama by default; optional cloud chat)
    → tools on your PC (open apps, Chrome, YouTube, optional computer-use)
    → TTS / orb UI
```

**Private data** sits in the OS user-data root (`%LOCALAPPDATA%\VOXORYL` on Windows, Application Support on Mac) — config, memory, reels — not in the shipped zip or a bare git clone.

**Computer-use** is a separate gate (`COMPUTER_USE_ENABLED`). Default off. When on: screenshot → observe → click/type (DPI-aware on Windows).

**Reels** is an inbox pipeline: import → understand → gate → promote into knowledge (or quarantine / reject). Separate from core voice.

For code layout, start at `voxoryl/main.py` and the README “What it does” section — this page stays the 30-second map.
