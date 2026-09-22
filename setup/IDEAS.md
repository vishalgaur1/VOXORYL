# Voxoryl idea backlog (ship now, prune later)

Capture cool/tech ideas here so we don’t lose them. Implement when useful; delete what we don’t need.

## Identity
- **Jack of all trades** — local generalist: build, research, automate, test, book, remember. Not a medical product.
- Friendly + professional tone always. Optional accessibility (short replies) if the owner asks — never the whole product.

## Agent / brain
- Tiny router model (0.5b/1.5b) for intent only; keep 4B for answers
- Speculative decode / llama.cpp for faster JSON
- Task budgets + checkpoints (stop after N tool calls)
- Hard mode → Groq only when confidence drops

## Android lab (TECH READY — plumbing)
- Emulator via Android SDK (`adb` + `emulator`), APK inbox `data/android_lab/apks/`
- Start AVD → install → monkey smoke → screenshot report
- Module: `voxoryl/android_lab.py` · API `/api/android`
- Say: `start android emulator` / `test apk MyApp.apk`

## Hyper-V sandbox (TECH READY — plumbing)
- Disposable VM for software you don't fully trust yet
- Flow: create sandbox → stage sample in `data/hv_sandbox/inbox/` → test in VM → `mark safe|suspicious|malware` → **destroy sandbox** (frees disk/RAM)
- Only install on main PC after **safe**; never keep junk VMs around
- Needs Hyper-V + Admin shell. Optional `VOXORYL_HV_TEMPLATE` golden image name
- Module: `voxoryl/sandbox_hv.py` · API `/api/sandbox`

## Camera vision (TECH READY)
- Webcam frame → local VL (`qwen2.5vl`) — “what am I showing you?”
- Does **not** need mouse computer-use; needs `opencv-python`
- Module: `voxoryl/camera.py` · API `/api/camera`

## Preferences (HARD RULE — TECH READY)
- Never assume budget/taste/risk. Ask: budget | balanced | quality.
- Module: `voxoryl/prefs.py`
- **Tone (always on):** friendly yet professional

## Food / spend / todos (TECH READY)
- Order history taste memory; optional food flags (habit nudge only — not medical care)
- Bank statement coaching; deadline todos; optional wellbeing nudges

## Accessibility / short-focus (OPTIONAL — ask-first)
- Not the core product. If owner asks: short-focus / adhd mode
- Module: `voxoryl/neuro.py`

## Research method (ALWAYS ON)
- Docs/manuals > Wikipedia/SO/MDN > Reddit (discounted) → conclusion
- `voxoryl/research.py`

## Music taste (TECH READY)
- Ingest Spotify/YouTube Music links or text lists → `data/music_taste/`
- Artists/genres + heuristic notes & instruments; suggest similar / same-artist via research
- Module: `voxoryl/music_taste.py` · API `/api/music`
- Say: `my playlist` / `suggest songs` / `music taste`

## Books / great lines (TECH READY)
- Vault under `data/books/`; random great line; optional quote research
- Mild daemon surface via wellbeing tick (not spammy)
- Module: `voxoryl/books.py` · API `/api/books`
- Say: `add book …` / `great line …` / `quote from …`

## Style / colors from photos (TECH READY)
- Vision on photo → suit/avoid colors; genuine fashion allowlist; ask `value_mode` first
- Module: `voxoryl/style_colors.py` · API `/api/style_colors`
- Say: `what colors suit me` / `outfit from this photo`

## Curiosity / growth (TECH READY — ALWAYS ON)
- Policy `setup/curiosity_sites.json` + research-weighted discovery → `data/curiosity/`
- Tasteful daily nudge; knowledge under **Curiosity**
- Module: `voxoryl/curiosity.py` · API `/api/curiosity`
- Say: `surprise me` / `interesting website` / `teach me something new`

## Windows / PowerToys (TECH READY)
- Brightness / volume / mute via WMI, nircmd, or SendKeys — degrade with hints
- Module: `voxoryl/windows_ops.py` · API `/api/windows`
- Say: `lower brightness` / `volume up` / `mute`

## VPN / proxy (TECH READY)
- Ask before changing; store `data/net_profile.json`; **always reset** on “work is done”
- Module: `voxoryl/net_profile.py` · API `/api/net_profile`
- Say: `set proxy …` / `enable vpn` / `reset network` / `work is done`

## Self-upgrade → Cursor (TECH READY)
- Backlog `data/self_upgrade.jsonl`; draft Cursor prompts; `gh` PR stub (no force-push)
- Module: `voxoryl/self_upgrade.py` · API `/api/self_upgrade`
- Say: `voxoryl can't …` / `add this feature to yourself` / `fix your bug` / `ask cursor to …`

## Multitask (TECH READY)
- Parallel tool runs via `asyncio.gather` when message has `while` / `at the same time`
- Module: `voxoryl/multitask.py` · API `/api/multitask`
- Say: `flash fill this form while identifying this song`

## Universal planner (TECH READY)
- Day / week / event / trip / study / project plans with deadlines (todos sync)
- Module: `voxoryl/planner.py` · API `/api/planner` · data/`plans.json`
- Say: `plan my day` / `plan a trip` / `study plan` / `event plan`

## Sale / price watch (TECH READY)
- Watch Amazon / OLX / Flipkart / eBay (or genuine URL) → `data/sale_watches.json`
- Daemon checks every ~3h via research; notifies memory + notes
- Module: `voxoryl/sale_watch.py` · API `/api/sale_watch`
- Say: `watch this on amazon …` / `tell me when on sale` / `check olx for …`

## Multilingual voice (TECH READY)
- Detect lang → install pack in `data/languages.json` → edge-tts neural voice + reply language
- Default Voxoryl voice: `en-GB-RyanNeural` (British butler/AI). Hindi: `hi-IN-MadhurNeural`
- Module: `voxoryl/i18n_voice.py` · API `/api/i18n`
- Say: `speak Hindi` / `reply in French` — or just write in that language

## Bookings + gov + privacy + companion + media + sites + leads
- Existing TECH READY modules — see code under `voxoryl/`

## Integrations / reliability
- MCP, Telegram, HA, n8n · eval suite · clone kit
