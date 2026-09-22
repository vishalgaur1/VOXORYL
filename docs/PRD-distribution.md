# VOXORYL — Distribution / Productization PRD

| Field | Value |
|---|---|
| **Product** | **VOXORYL** (vox-OR-ill) — Voice Operating eXecutive · On-device Reasoning that Yields Local action; downloadable local-first Windows PC companion |
| **Document status** | Frozen product/distribution scope (2026-09-11) |
| **Companion docs** | Runtime/architecture: [`PRD.md`](PRD.md). Agent Runtime plan (implementation): Cursor plan *Voxoryl Agent Runtime*. |
| **Audience** | Product owner, packaging engineers, support, future distributors |

This document owns **shipping and productization**: installer, onboarding, hardware fit, model downloads, portable profiles, updates, licensing, and support. It does **not** redefine the agent/perception/computer-use architecture — that lives in [`PRD.md`](PRD.md).

**Separation of concerns**

| Layer | Owns |
|---|---|
| **Runtime** ([`PRD.md`](PRD.md) + Agent Runtime) | Supervisor, perception, agent loop, capabilities, safety, inference backends |
| **Distribution** (this doc) | Installer, updates, signing, model downloads, onboarding UI, licensing, pricing, website, support |

Runtime must only **expose** portable knobs (see §15). Distribution consumes them.

---

## 1. Vision & non-goals

### 1.1 Vision

Ship Voxoryl as a **small Windows download**: install → **Explain My PC** → Hardware Score → recommended tier → Model Manager pulls assets → Desktop icon → full Local capabilities that fit that PC. Same product shell for everyone; **capability degradation by tier**, not a “crippled edition.”

- **Local forever** for core chat, voice, memory, tools, and computer use that the tier supports.
- **Cloud opt-in** for smarter chat streaming (tools/ASR/screen stay local).
- Users see tier labels (**Lite / Balanced / Strong / Beast**), not raw tags like `qwen3.5:4b`.

### 1.2 Non-goals (distribution)

- **Not** Windows Store–only for v1 (website / signed `.exe` first).
- **Not** a 10–30 GB installer that bundles all models.
- **Not** promising identical computer-use / vision experience on every GPU.
- **Not** macOS / Linux as first-class in v1 (Windows-first).
- **Not** multi-tenant SaaS hosting of personal data.
- **Not** merging installer/marketing/pricing into the runtime PRD.

---

## 2. Funnel (first-run journey)

```text
Download signed installer
        ↓
Install app runtime (WebView2 check, Ollama check/guide)
        ↓
First-run wizard: Explain My PC
        ↓
Hardware Score → Recommended tier
        ↓
Model Manager: download / resume / verify
        ↓
Create Desktop shortcut + permissions prompts
        ↓
Launch Voxoryl (native shell) → ready
```

**Failure modes (must be recoverable)**

| Failure | User experience |
|---|---|
| No WebView2 | Prompt to install Evergreen WebView2 Runtime; retry |
| No Ollama | Link to ollama.com/download; Doctor recheck |
| Model pull interrupted | Resume from Model Manager; do not brick first-run |
| Insufficient disk | Clear space estimate before pull; offer Lite tier |
| GPU undetected | CPU-only / Lite path with honest capability matrix |

Today’s developer path (`scripts/install-windows.ps1`, `models_setup.py`) is the **seed** for this funnel, not the final consumer UX.

---

## 3. Explain My PC (first-run UI)

First-run (and Settings → Hardware) shows a single screen, not model soup.

```text
┌──────────────────────────────────┐
│ VOXORYL HARDWARE CHECK            │
├──────────────────────────────────┤
│ GPU       …             ✓/⚠/—    │
│ VRAM      … GB          ✓/⚠/—    │
│ RAM       … GB          ✓/⚠/—    │
│ CPU       …             ✓        │
│ Storage   …             ✓/⚠      │
│                                  │
│ Recommended                      │
│ ★ BALANCED  (example)            │
│                                  │
│ Voice             ✓              │
│ Local chat        ✓              │
│ Memory            ✓              │
│ Computer use      ✓              │
│ Vision            ✓ / limited    │
│ Heavy models      —              │
│                                  │
│ [ Apply Recommended ]            │
│ [ Choose another tier ]          │
└──────────────────────────────────┘
```

- **Apply Recommended** writes `MODEL_PROFILE` / env / prefs and starts Model Manager pulls.
- Advanced accordion (optional): show underlying Ollama tags for power users.
- Copy stays plain English (“fits this PC”) — no requirement that users understand VRAM math.

Implementation hooks (existing): [`voxoryl/hardware.py`](../voxoryl/hardware.py), [`voxoryl/models_setup.py`](../voxoryl/models_setup.py), [`setup/models.json`](../setup/models.json).

---

## 4. Hardware Score

Tiers must **not** be VRAM-only. Compute a **Hardware Score** from:

| Signal | Why |
|---|---|
| GPU VRAM (GB) | Primary local LLM / VL residency |
| System RAM (GB) | OS + Ollama + browser + Voxoryl |
| CPU generation / cores | ASR, VAD, UIA, Playwright when GPU busy |
| Storage speed (SSD vs HDD heuristic) | Model load / pull UX |
| OS features | WebView2, UI Automation availability |
| GPU compute capability | CUDA vs CPU-only fallback |

**Mapping (product labels)** — align with [`setup/models.json`](../setup/models.json) windows, refined by score:

| Tier | Typical fit | Chat (behind UI) | Vision |
|---|---|---|---|
| **Lite** | Weak GPU / low RAM / CPU-only | `qwen2.5:1.5b` class | Off / unavailable |
| **Balanced** | ~4–8 GB VRAM, ~12–24 GB RAM (e.g. GTX 1660 Ti + 16 GB) | `qwen3.5:4b` + fast `0.5b` | `qwen2.5vl:3b` |
| **Strong** | ~8–16 GB VRAM | larger chat tier | larger VL |
| **Beast** | 16 GB+ VRAM, high RAM | large local weights | strong VL |

Score algorithm may evolve; **users never must pick raw tags**. Runtime persists `HARDWARE_PROFILE` + `CAPABILITY_TIER`.

---

## 5. Capability degradation matrix

Same Voxoryl product; features degrade honestly by tier.

| Capability | Lite | Balanced | Strong | Beast |
|---|---|---|---|---|
| Voice (ASR/TTS) | Yes | Yes | Yes | Yes |
| Local chat | Yes (small) | Yes | Yes | Yes |
| Memory | Yes | Yes | Yes | Yes |
| Recipes / software knowledge | Yes | Yes | Yes | Yes |
| UIA desktop grounding | Yes | Yes | Yes | Yes |
| Chrome automation | Yes | Yes | Yes | Yes |
| Vision (VL) | Limited / off | Yes | Yes | Yes |
| Long computer-use sessions | No / limited | Yes | Yes | Yes |
| Heavy local model | No | No | Optional | Yes |
| Cloud chat (opt-in) | Yes* | Yes* | Yes* | Yes* |

\*Cloud requires user-supplied free/paid API key; tools remain local.

Degradation is **configuration**, not a separate SKU.

---

## 6. Dynamic post-install retiering

First-run recommendation is a starting point.

```text
Recommended: Balanced
        ↓
Runtime telemetry (VRAM pressure, swap, VL latency)
        ↓
Suggest: lighten vision / prefer recipes / stay on fast model for chat
        ↓
User confirms or Auto-safe mode under sustained thrash
```

Never silently pull Beast weights. Never silently enable Cloud.

---

## 7. Model Download Manager

Do **not** embed multi-GB weights in the installer.

### 7.1 Responsibilities

| Action | Behavior |
|---|---|
| detect | What is installed vs recommended |
| recommend | From Hardware Score + tier |
| download | Pull via Ollama (or future content-addressed store) |
| pause / resume | Interrupted pulls continue |
| verify | Checksum / Ollama integrity |
| retry | Transient network failures |
| remove | Free disk for unused tiers |
| update / rollback | Model profile versioning |

### 7.2 Content-addressed manifests (target)

```json
{
  "id": "chat.balanced",
  "ollama_tag": "qwen3.5:4b",
  "sha256": "…",
  "size_bytes": 0,
  "version": "1",
  "quantization": "…",
  "hardware_tier": ["balanced", "strong"],
  "url_hint": "ollama"
}
```

Failed or partial downloads must leave the app **launchable** (Lite / cached models / Cloud-only chat if configured).

### 7.3 Today → tomorrow

| Today | Tomorrow |
|---|---|
| `models_setup.recommend_models` / `apply_recommendation` / `ollama pull` | First-run UI + resume + manifest registry |
| `setup/models.json` | Same source of truth for tier tags |

---

## 8. Installer packaging

| Requirement | Spec |
|---|---|
| Format | Signed Windows installer (Inno / MSIX / equivalent) for v1 website distribution |
| Payload | App runtime, scripts/launcher, shortcuts, **not** full model zoo |
| Dependencies | Detect/install guidance for **WebView2**, **Ollama**, VC++ if needed |
| Signing | Authenticode; SmartScreen reputation plan |
| Uninstall | Clean remove of app files; optional keep/delete **user data root**; never wipe unrelated user files |
| Dev path | Keep `scripts/install-windows.ps1` for contributors |

Packaging **implementation** follows after Agent Runtime P0 is usable; this PRD defines the contract.

---

## 9. Portable profile

Do **not** assume every machine uses `D:\VOXORYL`.

| Concept | Env / knob | Contents |
|---|---|---|
| Install root | app location | Code, static UI, launcher |
| **User data root** | `PORTABLE_DATA_ROOT` (or `%LOCALAPPDATA%\Voxoryl`) | memory, prefs, permissions, skills, connectors metadata, model prefs, `voxoryl.db`, logs |
| Secrets | OS keyring direction + `.env` for dev | API keys — never in git |

Profile layout (logical):

```text
<data_root>/
  memory/ …
  preferences/
  permissions/
  skills/
  connectors/
  models/profile.json
  voxoryl.db
  diagnostics/
```

Migration: existing repo `data/` maps to user data root for developer installs.

---

## 10. Database migrations

When runtime adopts `data/voxoryl.db` (or under `PORTABLE_DATA_ROOT`):

- Persist `schema_version` / `MIGRATION_VERSION`.
- Ordered migrations `001_…`, `002_…`.
- Upgrades must be forward-safe; failed migration → Doctor + rollback guidance (see §13).
- Human-readable JSON/MD exports remain optional mirrors, not the sole store long-term.

---

## 11. Permissions, Safe Mode, Wake State

### 11.1 Permissions (first-run + Settings)

| Permission | Why |
|---|---|
| Microphone | Voice |
| Speakers / output device | TTS |
| Screen / capture | Computer use / WorldState |
| Accessibility / UIA | Desktop grounding |
| Optional network | Cloud chat, TTS edge voices, research |

Obvious **kill switch** and session indicator when always-on perception is enabled (spyware risk — see runtime PRD).

### 11.2 Wake State (user-facing)

| State | Capture | ASR | Notes |
|---|---|---|---|
| **AWARE** | On | On | Normal companion |
| **PAUSED** | Optional screen | Off | No listening |
| **DISABLED** | Off | Off | Full quiet |

### 11.3 Safe Mode

```text
Voxoryl Safe Mode
├── no screen control
├── no email send
├── no deployment
├── no shell
├── local only
└── diagnostics enabled
```

Entry: Doctor, crash loop, or “Restart in Safe Mode” support instruction.

---

## 12. Public security

Personal-dev safety (`safety.py` ownership ledger) remains; distribution adds **untrusted-environment** rules:

| Rule | Behavior |
|---|---|
| Side effects | Send email, deploy, purchases, delete → confirmation |
| **Instruction Provenance** | SYSTEM/USER/POLICY/SKILL = instructions; TOOL = data; WEB/EMAIL/SCREEN/OCR = **untrusted data**, never instructions |
| Credentials | Prefer OS Credential Manager / DPAPI / keyring; `.env` for developers only |
| Cloud | Opt-in; sensitive kinds stay local ([`privacy.py`](../voxoryl/privacy.py)) |
| Kill switch | Stops perception + computer use immediately |

Browser/email content must never escalate to shell or file-delete without explicit trusted user intent.

---

## 13. Updates, Doctor, diagnostics, rollback

### 13.1 Update channels

| Channel | Contents |
|---|---|
| App | Runtime + UI |
| Models | Tier manifests / Ollama tags |
| Skills | Procedural packs (later) |

### 13.2 Voxoryl Doctor

Support entrypoint (`voxoryl doctor` / Settings → Doctor / API):

Checks: runtime, Ollama, GPU/VRAM, RAM, mic, speaker, VAD, ASR, TTS, WebView2, UIA permissions, Chrome, Playwright (when shipped), database, model availability, network (optional).

Output: pass / warn / fail per subsystem + **diagnostics export** zip (logs, versions, hardware profile — **no secrets**).

### 13.3 Rollback

| Layer | On failure |
|---|---|
| Application | Previous app version |
| Skills | Previous skill pack |
| Configuration | Last known-good prefs |
| Database | Pre-migrate backup |
| Models | Previous `MODEL_PROFILE` |

Flow: update → Doctor smoke → pass activate / fail rollback.

### 13.4 Runtime compatibility contract

Every distributed build declares:

```text
runtime_version
schema_version
capability_tier
model_profile
minimum_ram
minimum_vram
supported_gpu_backends
required_os_features
optional_dependencies
```

Installer/runtime can then state: supported / Balanced / vision supported / heavy model not recommended.

---

## 14. Licensing & compliance

| Topic | Requirement |
|---|---|
| Model licenses | Disclose Ollama/Qwen/etc. terms in About / EULA |
| TTS | edge-tts may need network; document offline limits |
| Telemetry | **Opt-in** only; no screen/mic content by default |
| Screen capture | Clear disclosure when CU / always-on perception enabled |
| Third-party | WebView2, Ollama, Playwright, etc. attribution |

---

## 15. Runtime constraints checklist

Agent Runtime **must** honor these so distribution can ship:

| Knob / behavior | Purpose |
|---|---|
| `PORTABLE_DATA_ROOT` | User data not tied to `D:\VOXORYL` |
| `HARDWARE_PROFILE` | Explain My PC / Doctor |
| `CAPABILITY_TIER` | Degradation matrix |
| `MODEL_PROFILE` | Applied model set |
| `PERMISSIONS` | Mic/screen/UIA/network flags |
| `DIAGNOSTICS_EXPORT` | Support bundles |
| `MIGRATION_VERSION` | DB upgrades |
| First-run hardware probe | Funnel |
| Capability degradation by tier | Honest product |
| Offline core operation | Local forever |
| Model download resume | Model Manager |
| Secure credential storage path | Public security |
| Clean uninstall compatibility | Packaging |
| Safe Mode + kill switch | Support / trust |
| Wake State AWARE/PAUSED/DISABLED | Perception product UX |

---

## 16. Open questions

| ID | Question | Impact |
|---|---|---|
| D1 | Freemium vs open-core vs paid Cloud pack? | Pricing / website |
| D2 | Microsoft Store later vs website `.exe` only? | Signing / updates |
| D3 | Auto-update aggressiveness (silent vs prompt)? | Trust |
| D4 | When to ship macOS/Linux? | Scope |
| D5 | Bundle Ollama vs require separate install? | Funnel friction |
| D6 | Default telemetry off forever, or anonymous opt-in crash only? | Privacy |

**Defaults for now:** website signed installer; Local forever core; Cloud opt-in; telemetry off by default; Ollama as required dependency with guided install; packaging after Runtime P0.

---

## 17. Implementation sequencing (docs → product)

1. **This PRD** (done as living spec).
2. Agent Runtime P0 (Supervisor … cache) + benchmarks — see runtime plan.
3. Wire Explain My PC + Model Manager UI on top of `hardware` / `models_setup`.
4. Portable data root + Doctor export.
5. Signed installer + update channel + rollback.

---

## 18. Related code (today)

| Path | Role |
|---|---|
| [`voxoryl/hardware.py`](../voxoryl/hardware.py) | RAM/VRAM probe |
| [`voxoryl/models_setup.py`](../voxoryl/models_setup.py) | Recommend / apply / pull / warm |
| [`setup/models.json`](../setup/models.json) | Tier catalog |
| [`scripts/install-windows.ps1`](../scripts/install-windows.ps1) | Dev install |
| [`scripts/launch_voxoryl.py`](../scripts/launch_voxoryl.py) | Product launch / shortcut |
| [`voxoryl/lifecycle.py`](../voxoryl/lifecycle.py) | Desktop session start/stop |
| [`voxoryl/privacy.py`](../voxoryl/privacy.py) | Cloud kind gating |
| [`voxoryl/safety.py`](../voxoryl/safety.py) | Destructive action gates |

---

*End of Distribution PRD. For perception/agent/computer-use architecture, see [`PRD.md`](PRD.md).*
