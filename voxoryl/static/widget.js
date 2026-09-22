const orb = document.getElementById("orb");
const state = document.getElementById("state");
const heard = { textContent: "" }; // legacy stub — chat log owns history now
const reply = { textContent: "" };
const chatLog = document.getElementById("chatLog");
const clearChatBtn = document.getElementById("clearChatBtn");
const conversationEl = document.getElementById("conversation");
const conversationToggle = document.getElementById("conversationToggle");
const conversationMeta = document.getElementById("conversationMeta");
const micSelect = document.getElementById("micSelect");
const speakerSelect = document.getElementById("speakerSelect");
const deviceHint = document.getElementById("deviceHint");
const refreshDevicesBtn = document.getElementById("refreshDevicesBtn");
const inferLocal = document.getElementById("inferLocal");
const inferCloud = document.getElementById("inferCloud");
const inferHint = document.getElementById("inferHint");
const typed = document.getElementById("typed");
const listenBtn = document.getElementById("listenBtn");
const stopListenBtn = document.getElementById("stopListenBtn");
const langSelect = document.getElementById("langSelect");
const modeTalk = document.getElementById("modeTalk");
const modeCouncil = document.getElementById("modeCouncil");
const continuousTalk = document.getElementById("continuousTalk");
const accurateMic = document.getElementById("accurateMic");
const openDashboardBtn = document.getElementById("openDashboardBtn");
const modeHint = document.getElementById("modeHint");
const menuBtn = document.getElementById("menuBtn");
const overflowMenu = document.getElementById("overflowMenu");
const settingsBtn = document.getElementById("settingsBtn");
const settingsSheet = document.getElementById("settingsSheet");
const bootOverlay = document.getElementById("bootOverlay");
const bootPhase = document.getElementById("bootPhase");
const bootFill = document.getElementById("bootFill");
const bootError = document.getElementById("bootError");
const bootErrorText = document.getElementById("bootErrorText");
const bootRetry = document.getElementById("bootRetry");
const statusPill = document.getElementById("statusPill");
const statusPillLabel = document.getElementById("statusPillLabel");
const micBtn = document.getElementById("micBtn");
const voiceHintBtn = document.getElementById("voiceHintBtn");
const keyboardBtn = document.getElementById("keyboardBtn");
const voicePill = document.getElementById("voicePill");
const typeForm = document.getElementById("typeForm");
const settingsModeTalk = document.getElementById("settingsModeTalk");
const settingsModeCouncil = document.getElementById("settingsModeCouncil");
const settingsOpenDash = document.getElementById("settingsOpenDash");
const settingsReloadUi = document.getElementById("settingsReloadUi");
const settingsClearChat = document.getElementById("settingsClearChat");

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let listening = false;
let phase = "idle"; // idle | listening | thinking | acting | speaking
let councilMode = false;
let wantContinuous = true;
let useAccurateAsr = false;
let asrAvailable = false;
let stopRequested = false;
let micMuted = false; // HARD mute while Voxoryl speaks (blocks feedback loop)
let ignoreUntil = 0;
let lastSpoken = "";
let currentAudio = null;
let pendingText = "";
let silenceTimer = null;
let mediaRecorder = null;
let mediaStream = null;
let mediaChunks = [];
/** Rolling lead-in chunks so Accurate ASR keeps ~300–400ms before speech. */
let mediaPreRoll = [];
let listenRecoverTimer = null;
/** After first Start / successful mic open — keep ambient wake listening when Idle. */
let wakeArmed = false;
let hasStartedOnce = false;
let wakeOnlyMode = false; // ambient: only promote to full listen on "Hey Voxy"
let preferredMicId = localStorage.getItem("voxoryl.micDeviceId") || "";
let preferredSpeakerId = localStorage.getItem("voxoryl.speakerDeviceId") || "";
let inferenceMode = "local";
let cloudConfigured = false;
let cloudReady = false;
/** Empty = same origin. Set when widget must talk to a healthy local server. */
let API_BASE = "";
/** True when launched via desktop product session — close frees the backend. */
let DESKTOP_SESSION = false;
let shutdownSent = false;
let bootRunning = false;

const POST_SPEAK_COOLDOWN_MS = 900; // wait after TTS before opening mic
const SILENCE_COMMIT_MS = 1450; // longer — compound sentences need room
const POST_ACTION_COOLDOWN_MS = 200; // brief settle after silent actions
const MEDIA_PREROLL_MAX_CHUNKS = 2; // ~500ms at 250ms timeslice
const BOOT_TIMEOUT_MS = 90_000;
/** Primary wake: "Hey Voxy" — also hey/ok voxoryl.
 *  Note: voxoryl != voxy+ryl — use alternation. */
const WAKE_RE = /^(hey\s+(?:voxy|voxoryl)|ok(?:ay)?\s+(?:voxy|voxoryl)|voxoryl)\b[,:]?\s*/i;
const WAKE_ANYWHERE_RE = /\b(hey\s+(?:voxy|voxoryl)|ok(?:ay)?\s+(?:voxy|voxoryl))\b/i;
const LANG_TO_SR = {
  en: "en-GB",
  hi: "hi-IN",
  es: "es-ES",
  fr: "fr-FR",
  de: "de-DE",
};

function apiUrl(path) {
  return `${API_BASE}${path}`;
}

function applyInferenceUI(inf) {
  if (!inf) return;
  inferenceMode = inf.mode === "cloud" || inf.inference_mode === "cloud" ? "cloud" : "local";
  cloudConfigured = !!(inf.cloud_configured || inf.cloud_ready);
  cloudReady = !!(inf.ready !== false && (inferenceMode === "local" || inf.cloud_ready || inf.cloud_configured));
  if (inferLocal) inferLocal.classList.toggle("active", inferenceMode === "local");
  if (inferCloud) inferCloud.classList.toggle("active", inferenceMode === "cloud");
  let hint =
    inf.cloud_hint ||
    inf.hint ||
    inf.inference_hint ||
    (inferenceMode === "local"
      ? "Uses Ollama on this machine. Personal data stays local."
      : "Cloud brain. Tools still run on this PC.");
  if (inferenceMode === "cloud" && !cloudConfigured) {
    hint =
      inf.cloud_hint ||
      inf.hint ||
      "Cloud needs a free API key. Add GROQ_API_KEY=… (or VOXORYL_CLOUD_API_KEY=…) to your .env in the Voxoryl folder, then restart Desktop Voxoryl.";
  }
  if (inferHint) {
    inferHint.textContent = hint;
    inferHint.classList.toggle("is-warn", inferenceMode === "cloud" && !cloudConfigured);
  }
}

function friendlyInferenceError(err, nextMode) {
  const raw = String(err?.message || err || "").trim();
  if (/failed to fetch|networkerror|load failed|aborterror|network request failed/i.test(raw)) {
    if (nextMode === "cloud" && !cloudConfigured) {
      return "Cloud needs a free API key. Add GROQ_API_KEY=… to .env (see .env.example), then restart Desktop Voxoryl.";
    }
    return "Can't reach the Voxoryl server. Use Menu → Reload widget, or restart Desktop Voxoryl.";
  }
  if (/^failed to fetch$/i.test(raw) || !raw) {
    return "Can't reach the Voxoryl server. Restart Desktop Voxoryl and try again.";
  }
  return raw.slice(0, 160);
}

async function refreshInference() {
  try {
    const res = await fetch(apiUrl("/api/inference"));
    if (!res.ok) {
      const s = await fetch(apiUrl("/api/status")).then((r) => r.json());
      applyInferenceUI({
        mode: s.inference_mode || "local",
        cloud_ready: s.cloud_ready || s.cloud_configured,
        cloud_configured: s.cloud_configured || s.cloud_ready,
        cloud_hint: s.inference_hint,
        hint: s.inference_hint,
      });
      return;
    }
    applyInferenceUI(await res.json());
  } catch (err) {
    if (inferHint && inferenceMode === "cloud") {
      inferHint.textContent = friendlyInferenceError(err, "cloud");
      inferHint.classList.add("is-warn");
    }
  }
}

async function setInferenceMode(mode) {
  const next = mode === "cloud" ? "cloud" : "local";
  applyInferenceUI({
    mode: next,
    cloud_ready: cloudConfigured,
    cloud_configured: cloudConfigured,
    cloud_hint:
      next === "cloud" && !cloudConfigured
        ? "Cloud needs a free API key. Add GROQ_API_KEY=… (or VOXORYL_CLOUD_API_KEY=…) to your .env in the Voxoryl folder, then restart Desktop Voxoryl."
        : "",
  });
  try {
    const res = await fetch(apiUrl("/api/inference"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: next }),
    });
    let data = {};
    try {
      data = await res.json();
    } catch (_) {
      data = {};
    }
    if (!res.ok) {
      const detail = data.detail || data.hint || `Could not switch mode (${res.status})`;
      throw new Error(typeof detail === "string" ? detail : "Could not switch mode");
    }
    applyInferenceUI(data);
    if (next === "cloud" && !(data.cloud_ready || data.cloud_configured)) {
      setPhase("idle", "Cloud needs API key");
    } else {
      setPhase("idle", next === "cloud" ? "Cloud brain" : "Local brain");
    }
  } catch (err) {
    const friendly = friendlyInferenceError(err, next);
    if (inferHint) {
      inferHint.textContent = friendly;
      inferHint.classList.add("is-warn");
    }
    setPhase("idle", friendly.slice(0, 48));
  }
}

function isLocalDataDir(dir) {
  const s = String(dir || "");
  return /^[A-Za-z]:/.test(s) || s.includes("\\") || s.startsWith("D:") || s.includes("Voxoryl");
}

async function probeStatus(base) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 2500);
  try {
    const res = await fetch(`${base}/api/status`, { signal: ctrl.signal });
    if (!res.ok) return null;
    return await res.json();
  } catch (_) {
    return null;
  } finally {
    clearTimeout(t);
  }
}

async function resolveApiBase() {
  const candidates = [];
  // Prefer same origin only if it looks like a real local Voxoryl (not Cursor /workspace stub)
  candidates.push({ base: "", label: "same-origin" });
  for (const port of [3848, 3847, 3849]) {
    candidates.push({ base: `http://127.0.0.1:${port}`, label: String(port) });
  }

  let fallback = null;
  for (const c of candidates) {
    const s = await probeStatus(c.base || window.location.origin);
    if (!s || !s.ok) continue;
    const local = isLocalDataDir(s.data_dir);
    const good = s.ollama && local;
    if (good) {
      API_BASE = c.base;
      DESKTOP_SESSION = !!s.desktop_session || new URLSearchParams(location.search).has("desktop");
      return { base: API_BASE, status: s };
    }
    if (local && !fallback) fallback = { base: c.base, status: s };
  }
  if (fallback) {
    API_BASE = fallback.base;
    DESKTOP_SESSION =
      !!fallback.status.desktop_session || new URLSearchParams(location.search).has("desktop");
    return fallback;
  }
  API_BASE = "";
  DESKTOP_SESSION = new URLSearchParams(location.search).has("desktop");
  return { base: "", status: null };
}

function requestDesktopShutdown() {
  if (shutdownSent || !DESKTOP_SESSION) return;
  shutdownSent = true;
  const url = apiUrl("/api/shutdown");
  try {
    if (navigator.sendBeacon) {
      navigator.sendBeacon(url, "{}");
      return;
    }
  } catch (_) {}
  try {
    fetch(url, { method: "POST", keepalive: true, headers: { "Content-Type": "application/json" }, body: "{}" }).catch(
      () => {}
    );
  } catch (_) {}
}

window.addEventListener("pagehide", requestDesktopShutdown);
window.addEventListener("beforeunload", requestDesktopShutdown);

if (continuousTalk) {
  continuousTalk.addEventListener("change", () => {
    wantContinuous = continuousTalk.checked;
  });
  wantContinuous = continuousTalk.checked;
}
if (accurateMic) {
  accurateMic.addEventListener("change", () => {
    useAccurateAsr = accurateMic.checked && asrAvailable;
  });
}

function normalize(s) {
  return String(s || "")
    .toLowerCase()
    .replace(/[^\w\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function stripWakePrefix(text) {
  return String(text || "").replace(WAKE_RE, "").trim();
}

function hasWakePhrase(text) {
  const t = String(text || "").trim();
  if (!t) return false;
  return WAKE_RE.test(t) || WAKE_ANYWHERE_RE.test(t);
}

function isWakeOnly(text) {
  const t = String(text || "").trim();
  if (!t) return false;
  const rest = stripWakePrefix(t);
  if (rest) return false;
  const low = normalize(t);
  return (
    low === "hey voxy" ||
    low === "hey voxoryl" ||
    low === "ok voxy" ||
    low === "okay voxy" ||
    low === "ok voxoryl" ||
    low === "okay voxoryl" ||
    low === "voxy" ||
    low === "voxoryl" ||
    hasWakePhrase(t)
  );
}

function onWakeDetected() {
  wakeArmed = true;
  hasStartedOnce = true;
  wakeOnlyMode = false;
  micMuted = false;
  stopRequested = false;
  if (phase !== "listening") setPhase("listening", "Listening");
}

function isEcho(heardText) {
  const a = normalize(heardText);
  const b = normalize(lastSpoken);
  if (!a || !b) return false;
  if (a === b) return true;
  if (b.includes(a) && a.length >= 12) return true;
  if (a.includes(b) && b.length >= 12) return true;
  // token overlap
  const at = new Set(a.split(" "));
  const bt = b.split(" ");
  if (bt.length < 4) return false;
  let hit = 0;
  for (const t of bt) if (at.has(t)) hit++;
  return hit / bt.length >= 0.72;
}

function phaseDisplayLabel(next, label) {
  if (label) return label;
  if (next === "listening") return "Listening";
  if (next === "thinking" || next === "acting") return "Thinking";
  if (next === "speaking") return "Speaking";
  return "Idle";
}

function syncStatusChrome(next, displayLabel) {
  const pillPhase = next === "acting" ? "thinking" : next;
  if (statusPill) statusPill.dataset.phase = pillPhase;
  if (statusPillLabel) statusPillLabel.textContent = displayLabel;
  if (voicePill) voicePill.dataset.phase = pillPhase;
  if (micBtn) {
    const active = next !== "idle";
    micBtn.classList.toggle("is-active", active);
    micBtn.setAttribute("aria-label", active ? "Stop" : "Start listening");
    micBtn.title = active ? "Stop" : "Start listening";
  }
  if (voiceHintBtn) {
    voiceHintBtn.textContent =
      next === "listening"
        ? "Listening…"
        : next === "thinking" || next === "acting"
          ? "Thinking…"
          : next === "speaking"
            ? "Speaking…"
            : "Speak naturally...";
  }
}

function syncTransportButtons(next) {
  const active = next !== "idle";
  if (listenBtn) listenBtn.hidden = active;
  if (stopListenBtn) stopListenBtn.hidden = !active;
}

function looksLikeActionCommand(message) {
  const lower = String(message || "").toLowerCase();
  return (
    /\b(open|launch|play|click|navigate|go to|goto|mute|unmute|brightness|volume|new tab)\b/.test(lower) ||
    /\b(chrome|youtube|gmail|notepad|browser)\b/.test(lower)
  );
}

function setPhase(next, label) {
  phase = next;
  const uiPhase = next === "acting" ? "thinking" : next;
  const display = phaseDisplayLabel(next, label);
  orb.classList.remove("idle", "listening", "thinking", "acting", "speaking");
  orb.classList.add(uiPhase);
  if (state) {
    state.dataset.phase = uiPhase;
    // Orb caption stays short uppercase phase names on the main surface
    const orbCaption =
      uiPhase === "listening"
        ? "Listening"
        : uiPhase === "thinking"
          ? "Thinking"
          : uiPhase === "speaking"
            ? "Speaking"
            : label && next === "idle" && label !== "Idle"
              ? label
              : "Idle";
    state.textContent = orbCaption;
    state.classList.remove("is-status-tick");
    void state.offsetWidth;
    state.classList.add("is-status-tick");
  }
  syncStatusChrome(next, display);
  const transport = document.querySelector(".transport");
  if (transport) {
    transport.classList.remove("is-idle", "is-listening", "is-thinking", "is-speaking");
    transport.classList.add(`is-${uiPhase}`);
  }
  syncTransportButtons(next);
  if (listenBtn && next === "idle") {
    listenBtn.textContent = wantContinuous ? "Start" : "Listen";
  }
  if (window.__voxorylOrb) {
    if (next === "idle") window.__voxorylOrb.setProgress(8);
    if (next === "listening") window.__voxorylOrb.setProgress(42);
    if (next === "thinking" || next === "acting") window.__voxorylOrb.setProgress(68);
    if (next === "speaking") window.__voxorylOrb.setProgress(88);
  }
}

function releaseMicMute() {
  micMuted = false;
}

function hardMuteMic() {
  micMuted = true;
  try {
    if (recognition) {
      listening = false;
      recognition.abort();
    }
  } catch (_) {
    try {
      recognition && recognition.stop();
    } catch (_) {}
  }
  // Keep pre-roll stream alive when possible; only pause recorder chunks mid-turn
  stopMediaCapture(false);
}

async function resumeListeningAfterTurn() {
  if (stopRequested) {
    setPhase("idle", "Idle");
    // Keep wake-armed ambient listen after a hard stop flag from turn? No — stopRequested means Stop.
    return;
  }
  releaseMicMute();
  ignoreUntil = Math.max(ignoreUntil, Date.now() + POST_ACTION_COOLDOWN_MS);
  wakeOnlyMode = false;
  wakeArmed = true;
  hasStartedOnce = true;
  // Do NOT claim Listening until startListen actually opens the mic
  setPhase("thinking", "Reconnecting mic…");
  await sleep(POST_ACTION_COOLDOWN_MS);
  if (stopRequested) return;
  if (wantContinuous) {
    startListen(true);
  } else {
    // Continuous off: still arm ambient wake listen after first Start
    armWakeListen();
  }
}

function stopSpeechPlayback() {
  try {
    if (currentAudio) {
      currentAudio.pause();
      currentAudio = null;
    }
  } catch (_) {}
  try {
    window.speechSynthesis && speechSynthesis.cancel();
  } catch (_) {}
}

function escapeHtml(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function bubbleHtml(role, content) {
  const who = role === "assistant" ? "Voxoryl" : "You";
  return `<span class="who">${who}</span><span class="bubble__text">${escapeHtml(content)}</span>`;
}

function setConversationMeta(count) {
  if (!conversationMeta) return;
  conversationMeta.textContent = count > 0 ? `${count} message${count === 1 ? "" : "s"}` : "";
}

function setConversationCollapsed(collapsed) {
  if (!conversationEl || !conversationToggle) return;
  conversationEl.classList.toggle("is-collapsed", collapsed);
  conversationToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
  try {
    localStorage.setItem("voxoryl.conversationCollapsed", collapsed ? "1" : "0");
  } catch (_) {}
}

function renderChat(messages) {
  if (!chatLog) return;
  const msgs = (messages || []).filter((m) => m && m.content && m.role !== "system");
  if (!msgs.length) {
    chatLog.innerHTML = `<p class="chat-empty">Quiet for now — tap Start or type below.</p>`;
    setConversationMeta(0);
  } else {
    chatLog.innerHTML = msgs
      .map((m) => {
        const role = m.role === "assistant" ? "assistant" : "user";
        return `<div class="bubble ${role}">${bubbleHtml(role, m.content)}</div>`;
      })
      .join("");
    chatLog.scrollTop = chatLog.scrollHeight;
    setConversationMeta(msgs.length);
  }
  // API may still send data.summary — never render it in the Talk panel.
}

async function refreshChat() {
  try {
    const res = await fetch(apiUrl("/api/chat"));
    const data = await res.json();
    if (data.ok) renderChat(data.messages || data.preview || []);
  } catch (_) {}
}

function appendLocalBubble(role, content) {
  if (!chatLog) return null;
  const empty = chatLog.querySelector(".chat-empty");
  if (empty) empty.remove();
  const r = role === "assistant" ? "assistant" : "user";
  const div = document.createElement("div");
  div.className = `bubble ${r}`;
  div.innerHTML = bubbleHtml(r, content);
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
  const n = chatLog.querySelectorAll(".bubble").length;
  setConversationMeta(n);
  return div;
}

function setBubbleText(div, content) {
  if (!div) return;
  const role = div.classList.contains("assistant") ? "assistant" : "user";
  div.innerHTML = bubbleHtml(role, content);
  if (chatLog) chatLog.scrollTop = chatLog.scrollHeight;
}

function micAudioConstraints() {
  const base = {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  };
  if (preferredMicId) return { ...base, deviceId: { exact: preferredMicId } };
  return base;
}

async function applySpeakerSink(audioEl) {
  if (!audioEl || !preferredSpeakerId) return;
  if (typeof audioEl.setSinkId !== "function") return;
  try {
    await audioEl.setSinkId(preferredSpeakerId);
  } catch (_) {}
}

function friendlyDeviceLabel(kind, device, index) {
  const raw = (device.label || "").trim();
  if (raw) return raw;
  const n = index + 1;
  return kind === "audioinput" ? `Microphone ${n}` : `Speaker ${n}`;
}

function fillDeviceSelect(select, devices, kind, preferred) {
  if (!select) return;
  const prev = preferred || select.value || "";
  select.innerHTML = "";
  const def = document.createElement("option");
  def.value = "";
  def.textContent = "System default";
  select.appendChild(def);
  devices
    .filter((d) => d.kind === kind && d.deviceId)
    .forEach((d, i) => {
      const opt = document.createElement("option");
      opt.value = d.deviceId;
      opt.textContent = friendlyDeviceLabel(kind, d, i);
      select.appendChild(opt);
    });
  const ids = new Set([...select.options].map((o) => o.value));
  select.value = ids.has(prev) ? prev : "";
}

async function ensureMicPermission() {
  if (!navigator.mediaDevices?.getUserMedia) return false;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop());
    return true;
  } catch (_) {
    return false;
  }
}

async function refreshAudioDevices({ requestPermission = false } = {}) {
  if (!navigator.mediaDevices?.enumerateDevices) {
    if (deviceHint) {
      deviceHint.hidden = false;
      deviceHint.textContent = "This browser can't list audio devices.";
    }
    return;
  }
  let devices = await navigator.mediaDevices.enumerateDevices();
  const labelsMissing = devices.some((d) => (d.kind === "audioinput" || d.kind === "audiooutput") && !d.label);
  if ((requestPermission || labelsMissing) && labelsMissing) {
    await ensureMicPermission();
    devices = await navigator.mediaDevices.enumerateDevices();
  }
  fillDeviceSelect(micSelect, devices, "audioinput", preferredMicId);
  fillDeviceSelect(speakerSelect, devices, "audiooutput", preferredSpeakerId);
  const named = devices.some((d) => d.label);
  if (deviceHint) {
    deviceHint.hidden = named;
    deviceHint.textContent = named
      ? ""
      : "Allow mic access once to show device names.";
  }
}

function reloadWidgetUI() {
  try {
    sessionStorage.setItem("voxoryl.fastBoot", "1");
  } catch (_) {}
  const url = new URL(window.location.href);
  // Preserve desktop=1 and other params; bust Edge --app document cache.
  url.searchParams.set("_r", String(Date.now()));
  window.location.replace(url.toString());
}

async function speakText(text) {
  const cleaned = String(text || "").trim();
  if (!cleaned) {
    reply.textContent = "";
    return;
  }
  reply.textContent = cleaned;
  lastSpoken = cleaned;
  setPhase("speaking", "Speaking");
  hardMuteMic();
  stopSpeechPlayback();
  ignoreUntil = Date.now() + 60_000; // until we clear after speak
  let playedNeural = false;
  try {
    const res = await fetch(apiUrl("/api/speak"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: cleaned }),
    });
    if (res.ok) {
      const blob = await res.blob();
      if (blob.size > 200) {
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        currentAudio = audio;
        await applySpeakerSink(audio);
        await new Promise((resolve, reject) => {
          audio.onended = () => {
            URL.revokeObjectURL(url);
            resolve();
          };
          audio.onerror = reject;
          audio.play().catch(reject);
        });
        playedNeural = true;
      }
    }
  } catch (_) {}
  if (!playedNeural && window.speechSynthesis) {
    state.textContent = "Speaking (system voice)";
    await browserSpeak(cleaned);
  }
  ignoreUntil = Date.now() + POST_SPEAK_COOLDOWN_MS;
  await sleep(POST_SPEAK_COOLDOWN_MS);
  micMuted = false;
}

function browserSpeak(text) {
  const utter = new SpeechSynthesisUtterance(text);
  const voices = speechSynthesis.getVoices();
  const lang = (langSelect && langSelect.value) || "en";
  const preferred =
    (lang === "hi" &&
      (voices.find((v) => /hi-IN/i.test(v.lang) && /male|madhur|hemant/i.test(v.name)) ||
        voices.find((v) => /hi/i.test(v.lang)))) ||
    voices.find((v) => /en-US/i.test(v.lang) && /Andrew|Brian|Guy|David|Natural/i.test(v.name)) ||
    voices.find((v) => /en-IN/i.test(v.lang) && /Ravi|Prabhat|male/i.test(v.name)) ||
    voices.find((v) => /en-GB/i.test(v.lang) && /Ryan|George|Thomas|male/i.test(v.name)) ||
    voices.find((v) => (LANG_TO_SR[lang] || "en-US").slice(0, 2) === (v.lang || "").slice(0, 2));
  if (preferred) utter.voice = preferred;
  utter.lang = lang === "hi" ? "hi-IN" : preferred?.lang || "en-US";
  utter.rate = 0.92;
  utter.pitch = 0.95;
  return new Promise((resolve) => {
    utter.onend = resolve;
    utter.onerror = resolve;
    speechSynthesis.speak(utter);
  });
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function clearSilenceTimer() {
  if (silenceTimer) {
    clearTimeout(silenceTimer);
    silenceTimer = null;
  }
}

function scheduleCommit() {
  clearSilenceTimer();
  silenceTimer = setTimeout(() => commitUtterance(), SILENCE_COMMIT_MS);
}

async function commitUtterance() {
  clearSilenceTimer();
  if (micMuted || stopRequested) return;
  if (Date.now() < ignoreUntil) return;
  if (phase === "speaking" || phase === "thinking" || phase === "acting") return;

  let text = pendingText.trim();
  pendingText = "";
  if (!text) return;
  if (isEcho(text)) {
    heard.textContent = "(ignored speaker echo)";
    return;
  }

  // Wake-only: enter Listening, don't call the agent
  if (isWakeOnly(text)) {
    onWakeDetected();
    heard.textContent = text;
    if (conversationMeta) conversationMeta.textContent = "Wake: Hey Voxy — Listening";
    setPhase("listening", "Listening");
    // Stay in (or return to) full listen for the following command
    if (!listening) startListen(true);
    return;
  }

  // Wake + command: keep full transcript for agent; promote out of ambient wake mode
  if (hasWakePhrase(text)) {
    onWakeDetected();
  }

  // Optional Parakeet/Whisper re-transcription for accuracy (includes pre-roll chunks)
  if (useAccurateAsr && (mediaChunks.length || mediaPreRoll.length)) {
    setPhase("thinking", "Transcribing…");
    hardMuteMic();
    try {
      const all = [...mediaPreRoll, ...mediaChunks];
      mediaChunks = [];
      mediaPreRoll = [];
      const blob = new Blob(all, { type: "audio/webm" });
      const fd = new FormData();
      fd.append("file", blob, "utterance.webm");
      const lang = (langSelect && langSelect.value) || "en";
      const res = await fetch(apiUrl(`/api/transcribe?language=${encodeURIComponent(lang)}`), {
        method: "POST",
        body: fd,
      });
      const data = await res.json();
      if (data.ok && data.text) {
        // Prefer accurate ASR but never drop a longer browser transcript that kept lead-in words
        const acc = String(data.text).trim();
        if (acc && (acc.length >= text.length * 0.7 || !text)) text = acc;
      }
    } catch (_) {
      /* keep browser transcript */
    }
  }

  // Debug: show raw heard text (full agent input — no wake/filler strip client-side)
  if (conversationMeta) {
    conversationMeta.textContent = `Heard: ${text.slice(0, 80)}${text.length > 80 ? "…" : ""}`;
  }

  await askVoxoryl(text);
}

async function askVoxoryl(message) {
  if (!message.trim()) return;
  if (isEcho(message)) {
    heard.textContent = "(ignored echo)";
    return;
  }
  const resumeAfter = wantContinuous && !stopRequested && !councilMode;
  hardMuteMic();
  clearSilenceTimer();
  pendingText = "";

  heard.textContent = message;
  appendLocalBubble("user", message);
  const acting = looksLikeActionCommand(message);
  setPhase(acting ? "acting" : "thinking", acting ? "Working…" : councilMode ? "Thinking (council)" : "Thinking");
  try {
    let data;
    // Prefer SSE streaming for snappy cloud / greeting replies
    if (!councilMode) {
      data = await askVoxorylStream(message);
    }
    if (!data) {
      let res;
      try {
        res = await fetch(apiUrl("/api/ask"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message,
            execute: true,
            force_council: councilMode,
          }),
        });
      } catch (netErr) {
        await resolveApiBase();
        res = await fetch(apiUrl("/api/ask"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message,
            execute: true,
            force_council: councilMode,
          }),
        });
      }
      data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Request failed");
    }
    const line = (data.speak ?? data.council?.synthesis?.decision ?? "").trim();
    const silentAction = Boolean(data.action_only) || (!line && data.ok !== false && data.mode === "pipeline");
    if (!silentAction) {
      const spoken = line || "Done.";
      if (!data._bubbleShown) appendLocalBubble("assistant", spoken);
      state.textContent = "Speaking";
      await speakText(spoken);
    } else {
      // Pure action success — stay silent (no preference / memory bubbles)
      state.textContent = "Done";
    }
    refreshChat();
    if ((resumeAfter || (silentAction && wantContinuous)) && !stopRequested) {
      await resumeListeningAfterTurn();
    } else {
      releaseMicMute();
      setPhase("idle", silentAction ? "Done" : "Idle");
    }
  } catch (err) {
    setPhase("idle", "Error");
    const msg = String(err && err.message ? err.message : err);
    const friendly =
      /failed to fetch|networkerror|load failed/i.test(msg)
        ? "Can't reach the Voxoryl server. Start it with scripts\\start-windows.ps1 (use port 3848 if 3847 is busy)."
        : msg;
    appendLocalBubble("assistant", `I hit a snag: ${friendly}`);
    await speakText(`I hit a snag: ${friendly}`);
    refreshChat();
    if (resumeAfter && !stopRequested) await resumeListeningAfterTurn();
    else releaseMicMute();
  }
}

async function askVoxorylStream(message) {
  const res = await fetch(apiUrl("/api/ask/stream"), {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ message, execute: true, force_council: false }),
  });
  if (!res.ok || !res.body) return null;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let streamed = "";
  let finalPayload = null;
  let bubble = null;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";
    for (const block of chunks) {
      const line = block.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;
      let evt;
      try {
        evt = JSON.parse(line.slice(5).trim());
      } catch (_) {
        continue;
      }
      if (evt.type === "token" && evt.text) {
        streamed += evt.text;
        if (!bubble) {
          bubble = appendLocalBubble("assistant", streamed);
        } else {
          setBubbleText(bubble, streamed);
        }
        if (conversationMeta) conversationMeta.textContent = "Streaming…";
      } else if (evt.type === "done") {
        finalPayload = evt;
      } else if (evt.type === "error") {
        throw new Error(evt.speak || "Stream failed");
      }
    }
  }
  if (finalPayload && bubble && finalPayload.speak) {
    setBubbleText(bubble, finalPayload.speak);
    finalPayload._bubbleShown = true;
  } else if (finalPayload && streamed && !finalPayload.speak) {
    // Keep streamed text only when it isn't an action-only empty speak
    if (!(finalPayload.action_only && finalPayload.ok !== false)) {
      finalPayload.speak = streamed;
      if (bubble) finalPayload._bubbleShown = true;
    }
  }
  return finalPayload;
}

async function startMediaCapture() {
  stopMediaCapture(false);
  if (!navigator.mediaDevices?.getUserMedia) return;
  const onChunk = (e) => {
    if (!e.data || !e.data.size) return;
    // Continuous buffer: keep a short pre-roll ring, then grow utterance chunks
    if (!pendingText) {
      mediaPreRoll.push(e.data);
      while (mediaPreRoll.length > MEDIA_PREROLL_MAX_CHUNKS) mediaPreRoll.shift();
    } else {
      mediaChunks.push(e.data);
    }
  };
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: micAudioConstraints(),
    });
    mediaChunks = [];
    mediaRecorder = new MediaRecorder(mediaStream);
    mediaRecorder.ondataavailable = onChunk;
    mediaRecorder.onerror = () => {
      if (wantContinuous && !stopRequested && phase === "listening") {
        scheduleListenRecover("recorder error");
      }
    };
    mediaRecorder.start(250);
  } catch (_) {
    // Exact deviceId may fail if unplugged — fall back to default once.
    if (preferredMicId) {
      try {
        mediaStream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });
        mediaChunks = [];
        mediaRecorder = new MediaRecorder(mediaStream);
        mediaRecorder.ondataavailable = onChunk;
        mediaRecorder.start(250);
        return;
      } catch (_) {}
    }
    mediaStream = null;
    mediaRecorder = null;
  }
}

function stopMediaCapture(clearChunks = true) {
  try {
    if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
  } catch (_) {}
  mediaRecorder = null;
  if (mediaStream) {
    mediaStream.getTracks().forEach((t) => t.stop());
    mediaStream = null;
  }
  if (clearChunks) {
    mediaChunks = [];
    mediaPreRoll = [];
  }
}

function scheduleListenRecover(reason) {
  if (listenRecoverTimer) return;
  listenRecoverTimer = setTimeout(() => {
    listenRecoverTimer = null;
    if (stopRequested || micMuted) return;
    if (wantContinuous && (phase === "listening" || phase === "idle")) {
      listening = false;
      startListen(true);
    } else if (phase === "listening") {
      setPhase("idle", reason ? `Mic: ${reason}` : "Mic stopped");
    }
  }, 400);
}

function startListen(fromResume = false) {
  if (!SpeechRecognition) {
    setPhase("idle", "Speech unsupported — type below");
    return;
  }
  if (micMuted) {
    // Never show Listening while muted — retry after mute clears
    setTimeout(() => {
      if (!stopRequested && !micMuted) startListen(fromResume);
    }, 120);
    return;
  }
  if (Date.now() < ignoreUntil) {
    const wait = Math.max(0, ignoreUntil - Date.now());
    setTimeout(() => {
      if (!stopRequested && !micMuted) startListen(fromResume);
    }, wait + 50);
    return;
  }
  if (listening) return;
  stopRequested = false;
  stopSpeechPlayback();
  pendingText = "";
  clearSilenceTimer();

  if (!recognition) {
    recognition = new SpeechRecognition();
    recognition.interimResults = true;
    recognition.continuous = true;
    recognition.maxAlternatives = 3;
    recognition.onresult = (event) => {
      if (micMuted || phase === "speaking" || phase === "thinking" || phase === "acting") return;
      if (Date.now() < ignoreUntil) return;

      let interim = "";
      let finals = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const alt = event.results[i][0];
        const t = alt.transcript;
        if (event.results[i].isFinal) finals += t;
        else interim += t;
      }
      // Keep unstable partials visible — don't trim leading words from display/agent buffer
      const display = (pendingText + " " + finals + " " + interim).trim();
      heard.textContent = display;

      // Partial/final wake → Listening (unmute + capture following command)
      if (hasWakePhrase(display) || hasWakePhrase(finals) || hasWakePhrase(interim)) {
        onWakeDetected();
      }

      // Ambient wake-only mode: ignore non-wake speech until Hey Voxy
      if (wakeOnlyMode && !hasWakePhrase(display)) {
        return;
      }

      if (finals.trim()) {
        const chunk = finals.trim();
        if (isEcho(chunk) || isEcho(pendingText + " " + chunk)) {
          heard.textContent = "(ignored speaker echo)";
          return;
        }
        pendingText = (pendingText + " " + chunk).trim();
        // Don't send yet — wait for silence so full compound sentences land
        scheduleCommit();
      } else if (interim.trim()) {
        // Keep extending silence window while user is still talking
        scheduleCommit();
      }
    };
    recognition.onerror = (ev) => {
      const err = (ev && ev.error) || "";
      if (micMuted || stopRequested) return;
      if (err === "aborted") return;
      if (err === "no-speech") {
        if (wantContinuous && phase === "listening") {
          try {
            recognition.start();
          } catch (_) {}
        }
        return;
      }
      listening = false;
      if (wantContinuous) {
        scheduleListenRecover(err || "recognition error");
      } else {
        setPhase("idle", "Couldn't catch that — try again");
      }
    };
    recognition.onend = () => {
      if (stopRequested || micMuted) {
        listening = false;
        return;
      }
      if (wantContinuous && phase === "listening") {
        try {
          listening = true;
          recognition.start();
        } catch (_) {
          listening = false;
          scheduleListenRecover("recognition ended");
        }
      } else if (wakeArmed && !wakeOnlyMode && phase === "listening") {
        // Dropped out of full listen — fall back to ambient wake
        listening = false;
        setTimeout(() => armWakeListen(), 350);
      } else if (wakeOnlyMode && (wakeArmed || hasStartedOnce)) {
        try {
          listening = true;
          recognition.start();
        } catch (_) {
          listening = false;
          scheduleListenRecover("wake arm ended");
        }
      } else {
        listening = false;
        if (phase === "listening") setPhase("idle", "Idle");
        if (wakeArmed || hasStartedOnce) setTimeout(() => armWakeListen(), 400);
      }
    };
  }

  const lang = (langSelect && langSelect.value) || "en";
  recognition.lang = LANG_TO_SR[lang] || "en-IN"; // en-IN often better for Indian English accents
  if (lang === "en") recognition.lang = "en-IN";
  listening = true;
  hasStartedOnce = true;
  wakeArmed = true;
  // Only now claim Listening — mic is about to start
  if (wakeOnlyMode) {
    setPhase("idle", "Say Hey Voxy");
  } else {
    setPhase("listening", "Listening");
  }
  if (useAccurateAsr) startMediaCapture();
  try {
    recognition.start();
  } catch (_) {
    // already started
    listening = true;
    if (!wakeOnlyMode) setPhase("listening", "Listening");
  }
}

function armWakeListen() {
  /** Ambient listen for wake phrase when Idle but mic permission / prior Start exists. */
  if (stopRequested || micMuted) return;
  if (!SpeechRecognition) return;
  if (listening || phase === "listening" || phase === "speaking" || phase === "thinking" || phase === "acting") {
    return;
  }
  if (!(wakeArmed || hasStartedOnce || wantContinuous)) return;
  wakeOnlyMode = true;
  stopRequested = false;
  startListen(true);
}

function stopListen() {
  stopRequested = true;
  wantContinuous = continuousTalk ? continuousTalk.checked : false;
  listening = false;
  micMuted = false;
  wakeOnlyMode = false;
  // Hard Stop disarms wake; user must Start again (or continuous will re-arm after resume)
  wakeArmed = false;
  clearSilenceTimer();
  if (listenRecoverTimer) {
    clearTimeout(listenRecoverTimer);
    listenRecoverTimer = null;
  }
  pendingText = "";
  try {
    recognition && recognition.abort();
  } catch (_) {
    try {
      recognition && recognition.stop();
    } catch (_) {}
  }
  stopMediaCapture(true);
  stopSpeechPlayback();
  setPhase("idle", "Idle");
}

function setCouncilMode(on) {
  councilMode = !!on;
  if (modeTalk) modeTalk.classList.toggle("active", !councilMode);
  if (modeCouncil) modeCouncil.classList.toggle("active", councilMode);
  if (settingsModeTalk) settingsModeTalk.classList.toggle("active", !councilMode);
  if (settingsModeCouncil) settingsModeCouncil.classList.toggle("active", councilMode);
  if (modeHint) {
    modeHint.textContent = councilMode
      ? "Council weighs harder calls before answering."
      : "Say Hey Voxy, or tap the mic and speak.";
  }
  if (phase === "idle") {
    setPhase("idle", "Idle");
  }
}

function toggleTypeEntry(forceOpen) {
  if (!typeForm || !keyboardBtn) return;
  const open = forceOpen != null ? !!forceOpen : typeForm.hidden;
  typeForm.hidden = !open;
  keyboardBtn.setAttribute("aria-expanded", open ? "true" : "false");
  if (open && typed) {
    typed.focus();
  }
}

function toggleListenFromChrome() {
  if (listening || phase === "listening" || phase === "speaking" || phase === "thinking" || phase === "acting") {
    stopListen();
  } else {
    stopRequested = false;
    wakeOnlyMode = false;
    if (continuousTalk) wantContinuous = continuousTalk.checked;
    startListen();
  }
}

function closeMenu() {
  if (!overflowMenu || !menuBtn) return;
  overflowMenu.hidden = true;
  menuBtn.setAttribute("aria-expanded", "false");
}

function openMenu() {
  if (!overflowMenu || !menuBtn) return;
  overflowMenu.hidden = false;
  menuBtn.setAttribute("aria-expanded", "true");
}

function openSettings() {
  closeMenu();
  if (settingsSheet) settingsSheet.hidden = false;
  refreshAudioDevices({ requestPermission: false });
  refreshInference();
}

function closeSettings() {
  if (settingsSheet) settingsSheet.hidden = true;
}

function setBootPhase(label, pct) {
  if (bootPhase) bootPhase.textContent = label;
  if (bootFill) bootFill.style.width = `${Math.max(4, Math.min(100, pct))}%`;
}

function showBootError(message) {
  if (bootError) bootError.hidden = false;
  if (bootErrorText) bootErrorText.textContent = message;
  if (bootOverlay) bootOverlay.setAttribute("aria-busy", "false");
  setBootPhase("Couldn't finish startup", 100);
}

function hideBootError() {
  if (bootError) bootError.hidden = true;
}

function finishBoot() {
  document.body.classList.remove("is-booting");
  document.body.classList.add("is-ready");
  if (bootOverlay) {
    bootOverlay.classList.remove("is-booting");
    bootOverlay.classList.add("is-done");
    bootOverlay.setAttribute("aria-busy", "false");
  }
  const shell = document.getElementById("appShell");
  if (shell) shell.removeAttribute("aria-hidden");
  setPhase("idle", "Idle");
  // Prefer ambient wake after boot when continuous is on (mic permission may prompt on first hear)
  if (wantContinuous && SpeechRecognition) {
    wakeArmed = true;
    setTimeout(() => armWakeListen(), 600);
  }
}

async function probeAsr() {
  try {
    const s = await fetch(apiUrl("/api/transcribe/status")).then((r) => r.json());
    asrAvailable = !!s.ok;
    if (accurateMic) {
      accurateMic.disabled = !asrAvailable;
      accurateMic.title = asrAvailable
        ? `Accurate mic: ${s.backend || s.engine || "asr"} (${s.model || "default"})`
        : s.hint || 'Install onnx-asr for Parakeet: pip install "onnx-asr[cpu,hub]"';
      if (asrAvailable) {
        accurateMic.checked = true;
        useAccurateAsr = true;
      }
    }
  } catch (_) {
    if (accurateMic) accurateMic.disabled = true;
  }
}

async function runBoot() {
  if (bootRunning) return;
  bootRunning = true;
  hideBootError();
  if (bootOverlay) {
    bootOverlay.classList.add("is-booting");
    bootOverlay.classList.remove("is-done");
    bootOverlay.setAttribute("aria-busy", "true");
  }
  document.body.classList.add("is-booting");
  document.body.classList.remove("is-ready");

  let fastBoot = false;
  try {
    fastBoot = sessionStorage.getItem("voxoryl.fastBoot") === "1";
    if (fastBoot) sessionStorage.removeItem("voxoryl.fastBoot");
  } catch (_) {}

  const deadline = Date.now() + (fastBoot ? 12_000 : BOOT_TIMEOUT_MS);
  let lastStatus = null;
  let sawApi = false;

  try {
    setBootPhase(fastBoot ? "Refreshing UI…" : "Starting core…", fastBoot ? 35 : 10);

    while (Date.now() < deadline) {
      const resolved = await resolveApiBase();
      lastStatus = resolved.status;

      if (!lastStatus || !lastStatus.ok) {
        setBootPhase(fastBoot ? "Reconnecting…" : "Starting core…", 18);
        await sleep(fastBoot ? 200 : 450);
        continue;
      }

      sawApi = true;
      DESKTOP_SESSION =
        !!lastStatus.desktop_session || new URLSearchParams(location.search).has("desktop");

      if (lastStatus.inference || lastStatus.inference_mode) {
        applyInferenceUI({
          mode: lastStatus.inference_mode || lastStatus.inference?.mode,
          cloud_ready: lastStatus.cloud_ready ?? lastStatus.inference?.cloud_ready,
          cloud_configured: lastStatus.cloud_configured ?? lastStatus.inference?.cloud_ready,
          cloud_hint: lastStatus.inference_hint || lastStatus.inference?.cloud_hint,
          hint: lastStatus.inference_hint,
        });
      }

      // After a UI refresh, skip waiting on Ollama / model pull — API is enough.
      if (fastBoot) {
        setBootPhase("Ready", 100);
        await refreshChat();
        await probeAsr();
        finishBoot();
        bootRunning = false;
        return;
      }

      if (!lastStatus.ollama) {
        setBootPhase("Waking Ollama…", 42);
        await sleep(500);
        continue;
      }

      if (lastStatus.model_installed === false) {
        setBootPhase("Loading model…", 68);
        await sleep(550);
        continue;
      }

      setBootPhase("Almost ready…", 86);
      applyInferenceUI(lastStatus.inference || lastStatus);
      await refreshInference();
      await refreshChat();
      await probeAsr();
      setBootPhase("Ready", 100);
      await sleep(420);
      finishBoot();
      bootRunning = false;
      return;
    }

    if (!sawApi) {
      showBootError(
        "Couldn't reach the Voxoryl server. Leave this window open and click Retry, or launch again from the Desktop Voxoryl icon."
      );
    } else if (fastBoot && sawApi) {
      await refreshChat();
      await probeAsr();
      finishBoot();
      bootRunning = false;
      return;
    } else if (lastStatus && !lastStatus.ollama) {
      showBootError(
        "Server is up, but Ollama isn't responding. Start Ollama, then click Retry."
      );
    } else if (lastStatus && lastStatus.model_installed === false) {
      // Soft-ready: allow use; model may still be pulling in background
      setBootPhase("Ready (model still loading)", 100);
      await refreshChat();
      await probeAsr();
      await sleep(500);
      finishBoot();
      setPhase("idle", "Idle · model still loading");
      bootRunning = false;
      return;
    } else {
      showBootError("Startup timed out. Click Retry, or check data\\logs\\server.log.");
    }
  } catch (_) {
    showBootError("Something went wrong while starting. Click Retry.");
  }
  bootRunning = false;
}

orb.addEventListener("click", () => {
  toggleListenFromChrome();
});

if (listenBtn) {
  listenBtn.addEventListener("click", () => {
  stopRequested = false;
  wakeOnlyMode = false;
  if (continuousTalk) wantContinuous = continuousTalk.checked;
  startListen();
  });
}
if (stopListenBtn) stopListenBtn.addEventListener("click", () => stopListen());

if (micBtn) micBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  toggleListenFromChrome();
});
if (voiceHintBtn) voiceHintBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  toggleListenFromChrome();
});
if (keyboardBtn) {
  keyboardBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleTypeEntry();
  });
}

if (modeTalk) modeTalk.addEventListener("click", () => setCouncilMode(false));
if (modeCouncil) modeCouncil.addEventListener("click", () => setCouncilMode(true));
if (settingsModeTalk) settingsModeTalk.addEventListener("click", () => setCouncilMode(false));
if (settingsModeCouncil) settingsModeCouncil.addEventListener("click", () => setCouncilMode(true));

if (typeForm) {
  typeForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const value = typed.value.trim();
    typed.value = "";
    toggleTypeEntry(false);
    askVoxoryl(value);
  });
}

document.querySelectorAll(".quick-action[data-q], .quick button[data-q]").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.id === "humBtn") return;
    const q = btn.getAttribute("data-q");
    if (q) askVoxoryl(q);
  });
});

const askQuickBtn = document.getElementById("askQuickBtn");
if (askQuickBtn) {
  askQuickBtn.addEventListener("click", () => {
    toggleTypeEntry(true);
  });
}

// ---- Hum → MIDI (optional; button may be absent on main surface) ----
const humBtn = document.getElementById("humBtn");
let humRecorder = null;
let humChunks = [];

async function recordHum(seconds = 6) {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setPhase("idle", "Mic unavailable for hum");
    return;
  }
  stopListen();
  setPhase("listening", `Hum now (${seconds}s)`);
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: micAudioConstraints(),
  });
  humChunks = [];
  humRecorder = new MediaRecorder(stream);
  humRecorder.ondataavailable = (e) => {
    if (e.data.size) humChunks.push(e.data);
  };
  const done = new Promise((resolve) => {
    humRecorder.onstop = () => resolve();
  });
  humRecorder.start();
  await sleep(seconds * 1000);
  humRecorder.stop();
  stream.getTracks().forEach((t) => t.stop());
  await done;
  const blob = new Blob(humChunks, { type: "audio/webm" });
  const fd = new FormData();
  fd.append("file", blob, "hum.webm");
  setPhase("thinking", "Turning hum into MIDI…");
  try {
    const res = await fetch(apiUrl("/api/melody/upload?place=false"), { method: "POST", body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || data.error || "Hum failed");
    await speakText(data.speak || "MIDI ready.");
  } catch (err) {
    await speakText(`Hum failed: ${err.message}`);
  }
  setPhase("idle", "Idle");
}

if (humBtn) humBtn.addEventListener("click", () => recordHum(6));

setCouncilMode(false);
setPhase("idle", "Idle");

if (menuBtn && overflowMenu) {
  menuBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (overflowMenu.hidden) openMenu();
    else closeMenu();
  });
  document.addEventListener("click", (e) => {
    if (!overflowMenu.hidden && !overflowMenu.contains(e.target) && e.target !== menuBtn) {
      closeMenu();
    }
  });
}

if (settingsBtn) {
  settingsBtn.addEventListener("click", () => openSettings());
}
if (settingsSheet) {
  settingsSheet.querySelectorAll("[data-close-sheet]").forEach((el) => {
    el.addEventListener("click", () => closeSettings());
  });
}

const pinHint = document.getElementById("pinHint");
if (pinHint) {
  pinHint.addEventListener("click", () => {
    closeMenu();
    setPhase("idle", "Launcher can pin this window on top");
  });
}

if (langSelect) {
  langSelect.addEventListener("change", async () => {
    const code = langSelect.value;
    try {
      await fetch(apiUrl("/api/i18n"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "set", message: `speak ${code}`, code }),
      });
    } catch (_) {}
  });
}

async function clearConversation() {
  try {
    await fetch(apiUrl("/api/chat/clear"), { method: "POST" });
  } catch (_) {}
  renderChat([], "");
  setPhase("idle", "Fresh chat");
}

if (clearChatBtn) {
  clearChatBtn.addEventListener("click", () => clearConversation());
}
if (settingsClearChat) {
  settingsClearChat.addEventListener("click", () => {
    clearConversation();
    closeSettings();
  });
}

if (conversationToggle) {
  conversationToggle.addEventListener("click", () => {
    const collapsed = !conversationEl?.classList.contains("is-collapsed");
    setConversationCollapsed(collapsed);
  });
  try {
    if (localStorage.getItem("voxoryl.conversationCollapsed") === "1") {
      setConversationCollapsed(true);
    }
  } catch (_) {}
}

if (micSelect) {
  micSelect.addEventListener("change", () => {
    preferredMicId = micSelect.value || "";
    try {
      localStorage.setItem("voxoryl.micDeviceId", preferredMicId);
    } catch (_) {}
    setPhase("idle", preferredMicId ? "Mic updated" : "Default mic");
  });
}
if (speakerSelect) {
  speakerSelect.addEventListener("change", () => {
    preferredSpeakerId = speakerSelect.value || "";
    try {
      localStorage.setItem("voxoryl.speakerDeviceId", preferredSpeakerId);
    } catch (_) {}
    setPhase("idle", preferredSpeakerId ? "Speaker updated" : "Default speaker");
  });
}
if (refreshDevicesBtn) {
  refreshDevicesBtn.addEventListener("click", () => {
    refreshAudioDevices({ requestPermission: true });
  });
}
if (navigator.mediaDevices?.addEventListener) {
  navigator.mediaDevices.addEventListener("devicechange", () => {
    refreshAudioDevices({ requestPermission: false });
  });
}

if (inferLocal) {
  inferLocal.addEventListener("click", () => setInferenceMode("local"));
}
if (inferCloud) {
  inferCloud.addEventListener("click", () => setInferenceMode("cloud"));
}

const reloadUiBtn = document.getElementById("reloadUiBtn");
if (reloadUiBtn) {
  reloadUiBtn.addEventListener("click", () => {
    closeMenu();
    reloadWidgetUI();
  });
}
if (settingsReloadUi) {
  settingsReloadUi.addEventListener("click", () => {
    closeSettings();
    reloadWidgetUI();
  });
}

async function openWebDashboard() {
  const path = "/";
  try {
    const res = await fetch(apiUrl("/api/open-url"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (res.ok) return;
  } catch (_) {}
  const origin = API_BASE || window.location.origin;
  window.open(`${origin}/`, "_blank", "noopener,noreferrer");
}

if (openDashboardBtn) {
  openDashboardBtn.addEventListener("click", (e) => {
    e.preventDefault();
    closeMenu();
    openWebDashboard();
  });
}
if (settingsOpenDash) {
  settingsOpenDash.addEventListener("click", (e) => {
    e.preventDefault();
    closeSettings();
    openWebDashboard();
  });
}

if (bootRetry) {
  bootRetry.addEventListener("click", () => {
    runBoot();
  });
}

// Boot overlay polls until API / Ollama / model are ready, then fades into idle UI
runBoot();
