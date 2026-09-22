# Voxoryl Power-ups — make a 4B model punch like a giant

Your hardware: **GTX 1660 Ti 6GB · 16GB RAM · qwen3.5:4b**.  
Goal: **high quality + speed**, not bigger weights.

Research consensus (2025–2026): small models win when **tools + structure + routing** do the heavy lifting. Raw “just debate harder” often *hurts* SLMs ([SLM-MUX](https://slm-mux.github.io/)). A **smart orchestrator + short specialist calls + free online fallback** beats one trillion-parameter chat box for agents.

---

## 1. Architecture patterns (highest ROI)

| Pattern | Why it works | Put into Voxoryl |
|---|---|---|
| **SLM-default, LLM-fallback** | 4B handles 90% fast; Groq/OpenRouter only when uncertain | Already have Groq council advisor — expand to “hard task router” |
| **Orchestrator thinks, specialists don’t** | Papers show reasoning on the planner helps; long CoT on every sub-agent slows & confuses | Keep council for hard jobs only; direct tools for email/github/etc. |
| **Schema-first / grammar decoding** | Forces valid JSON tool calls — closes gap vs huge models | Add Outlines / XGrammar / Ollama structured outputs |
| **Code-as-actions** ([smolagents](https://github.com/huggingface/smolagents)) | Small models err less writing Python than freeform tool JSON | Optional `CodeAgent` path for file/computer tasks |
| **Separate plan vs call** ([grammar-based-agents](https://github.com/krasserm/grammar-based-agents)) | Planner only picks next tool + informal goal; another step fills args | Split current router into plan → fill → execute |
| **Router + deterministic pipelines** ([LocalClaw](https://github.com/PeterGreenAppliedAI/LocalClaw)) | Code owns the workflow; LLM only extracts params | Market scan / email triage as fixed pipelines |
| **Knowledge + Graph RAG** | Model doesn’t need world knowledge if retrieval is strong | You have knowledge.md + mind map — add embeddings next |

---

## 2. Models (keep 4B as daily driver)

| Model | Role | Notes |
|---|---|---|
| **qwen3.5:4b** (current) | Daily brain | Strong native tool family |
| **Phi-4-mini** | Alt agent driver | Often safest ~4B for tools (MIT) |
| **qwen2.5:0.5b / 1.5b** | Draft / classify / title | Speculative draft or intent router only |
| **nomic-embed-text** or **qwen3-embedding** | Embeddings | Semantic search over knowledge (fits RAM) |
| **Groq free** / OpenRouter free tiers | Fallback brain | Already wired — use on low confidence |
| Avoid on this laptop | Dense 27B+, Flash MoE needing 20GB+ RAM | Will crawl or OOM |

Ollama pulls worth trying later:
```text
ollama pull phi4-mini
ollama pull nomic-embed-text
ollama pull qwen2.5:0.5b
```

---

## 3. Inference software (speed)

| Software | Use |
|---|---|
| **Ollama** | Easy path (now) |
| **llama.cpp / llama-server** | More control: flash-attn, cache quant, n-gram speculative decode (almost free VRAM) |
| **LM Studio** | GUI + OpenAI-compatible API |
| **KoboldCPP** | Lightweight local server |

Speed flags to learn (llama.cpp):
- `--flash-attn`
- `--cache-type-k q8_0` / `--cache-type-v q8_0` (KV savings)
- `--spec-type ngram-simple` (no second model; good for JSON/templated output)
- Keep context modest (2k–4k) on 6GB for chat speed

---

## 4. Agent frameworks & repos to steal ideas from

| Repo | Steal this |
|---|---|
| [huggingface/smolagents](https://github.com/huggingface/smolagents) | CodeAgent, MCP tools, tiny loops |
| [danieldagot/edge-agent](https://github.com/danieldagot/edge-agent) | Guardrail → Router → Evaluator → Fallback chain |
| [hussain-alsaibai/tiny-agent](https://github.com/hussain-alsaibai/tiny-agent) | One-file ReAct, budgets, checkpoints |
| [PeterGreenAppliedAI/LocalClaw](https://github.com/PeterGreenAppliedAI/LocalClaw) | Router + specialist pipelines for small Ollama models |
| [krasserm/grammar-based-agents](https://github.com/krasserm/grammar-based-agents) | Plan ≠ function-call split |
| [dottxt-ai/outlines](https://github.com/dottxt-ai/outlines) | Constrained JSON generation |
| [Jianshu1only/Token-Routing](https://github.com/Jianshu1only/Token-Routing) | Only hard tokens go to big/cloud model |
| Heartwood / MindForge / engraph / Kwipu | Knowledge graphs + hybrid retrieval (you already mirrored the idea) |

---

## 5b. Screen see + control (wired in Voxoryl)

| Piece | Detail |
|---|---|
| Vision | `qwen2.5vl:3b` via Ollama (already on this PC) |
| Capture | Pillow `ImageGrab` → `data/screenshots/` |
| Mouse/keyboard | `pyautogui` (+ `pyperclip` for unicode) |
| Chrome profiles | Prefer `chrome.exe --profile-directory=…` from Local State (owner email/name from `.env`). Vision locate+click is fallback for the picker. |
| Enable | `COMPUTER_USE_ENABLED=true` in `.env` (default **false**) |
| Screen watch | `SCREEN_WATCH_ENABLED=true` + interval (default 45s); faster while actively clicking |
| Safety | Failsafe (mouse corner abort), max steps, action log in `data/computer_use_log.jsonl`, **owned-write gate** (`data/safety/owned_writes.json`): no clear/overwrite of text Voxoryl didn't type; no file deletes outside the project/workspace tree |
| Status | `GET /api/status` → `connectors.computer_use`, `connectors.screen_watch`, `connectors.screen_control_on` |
| Say | “open Chrome and my profile”, “look at my screen”, “fill this form”, “click …” |

API: `POST /api/screen` `{ "action": "look"|"act"|"locate"|"type", "goal": "..." }`

Required `.env` for orb Talk + tools:
```text
COMPUTER_USE_ENABLED=true
OLLAMA_VISION_MODEL=qwen2.5vl:3b
SCREEN_WATCH_ENABLED=true
SCREEN_WATCH_INTERVAL_SEC=45
```
Restart Voxoryl after changing these.

Wire these; each one multiplies the 4B model:

| Connector | What it unlocks |
|---|---|
| **MCP servers** (filesystem, browser, git, sqlite) | Cursor-style tool ecosystem |
| **SearXNG** (self-hosted search) | Private research without API keys |
| **Browser-use / Playwright** | Real web actions |
| **Obsidian + Local REST / vault path** | Notes already stubbed |
| **GitHub CLI / API** | Already stubbed |
| **IMAP + Gmail app password** | Already stubbed |
| **Home Assistant** | Smart home Voxoryl |
| **n8n or Activepieces** | No-code automations the agent triggers |
| **ComfyUI** | Video/image (already probed) |
| **Whisper.cpp / faster-whisper** | Better STT than browser-only |
| **Piper / Kokoro TTS** | Fully offline voice (edge-tts needs net) |

MCP starter lists: [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers)

---

## 6. Skills / Cursor-style skills to author

Put reusable skills in `data/skills.json` (private) or `setup/templates/skills.json` (public defaults):

1. **Retrieve-then-answer** — always hit knowledge + mind-map neighbors first *(done)*  
2. **JSON-only tool calls** — reject freeform when executing  
3. **Confidence gate** — if unsure → Groq verifier before execute  
4. **Short context** — never dump whole memory; top-k only  
5. **One tool per step** — reduces 4B confusion  
6. **Verify then act** — dry-run risky computer/email actions  
7. **Daily compound** — morning scan writes knowledge + marketing + video ideas *(partially done)*

---

## 7. Quality tricks that feel “trillion-param”

1. **Retrieval > parameters** — good RAG beats a bigger empty model  
2. **Tools > memorized facts** — calculator, search, code exec  
3. **Strict schemas** — invalid JSON = retry once, then fallback  
4. **Personas only on hard tasks** — your council; don’t run 4 voices on “what’s my bike”  
5. **Free online verifier** — Critic step on Groq 70B for plans, not for every chat  
6. **LoRA later** — fine-tune on *your* tool trajectories (Unsloth) when you have logs  
7. **Embedding search** over `knowledge.md` sections (nomic-embed-text)  
8. **Caches** — cache research/market answers for hours  

---

## 8. Suggested install order for *this* laptop

### Phase A — INSTALLED in this repo
- [x] Keep `qwen3.5:4b` + Groq fallback verifier (`voxoryl/verifier.py`)
- [x] **nomic-embed-text** hybrid knowledge retrieve (`voxoryl/embeddings.py`)
- [x] Strict JSON schema lock (`voxoryl/schema_lock.py`)
- [x] Better research: duckduckgo-search + optional SearXNG
- [x] LocalClaw-style pipelines (`voxoryl/pipelines.py`)
- [x] smolagents-style code-act sandbox (`voxoryl/code_act.py`)
- [x] MCP server registry (`setup/mcp.servers.json` + `voxoryl/mcp_bridge.py`)
- [x] Screen see/control (`voxoryl/screen.py`)

### Phase B — next / partially shipped
- [x] Idea backlog (`setup/IDEAS.md`) — prune later
- [x] Flash form-fill via clipboard paste + identity (`voxoryl/identity.py`, screen `flash_fill`)
- [x] Reel / share ingest → `data/reel_vault.md` + knowledge (`voxoryl/ingest.py`)
- [x] Reels inbox (URL + video upload → `data/reels/`, oEmbed, optional ASR/summary, widget **Reels**) — `voxoryl/reels.py`, `GET|POST /api/reels`
- [x] Instagram publish scaffold (Meta Graph tokens in `.env`) — `voxoryl/instagram.py`
- [x] WhatsApp bridge stub (Desktop send + forward ingest)
- [ ] MCP filesystem + browser + git  
- [ ] Offline TTS (Piper) / Whisper STT  
- [ ] Tiny intent router model  

### Phase C — when hungry
- [ ] llama.cpp server + n-gram speculative decode  
- [ ] LoRA on your council/tool logs  
- [ ] Telegram bridge / clipboard watcher  
- [ ] Optional second tiny model as intent classifier only  

---

## 9. What *not* to do on 6GB / 16GB

- Don’t force 35B MoE Flash models (RAM wall)  
- Don’t run 4 full council voices on every “hi”  
- Don’t load draft+target speculative models that double VRAM  
- Don’t expect chat-only 4B to match GPT without tools  

---

## 10. One-line philosophy

> **Small model = CPU of the agent. Tools, memory, schemas, and a free big verifier = the GPU of intelligence.**

Voxoryl already has: local 4B, council, Groq fallback, knowledge log, mind map, research/notes/github/email stubs.  
Next highest leverage: **embeddings + MCP + schema lock + SearXNG**.
