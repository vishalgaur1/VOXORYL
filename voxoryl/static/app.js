const $ = (id) => document.getElementById(id);

const MODE_HINTS = {
  direct: "Direct — just talk. Voxoryl replies without controlling your PC.",
  tools: "Tools — Voxoryl can control your PC and apps when needed.",
  council: "Council — slower multi-step planning with several viewpoints.",
};

const ASK_HINTS = {
  direct: "Sends your message to Voxoryl",
  tools: "Sends your message — Voxoryl may use tools on your PC",
  council: "Starts a slower Council deliberation",
};

const PANEL_STORAGE_KEY = "voxoryl.panelOpen";

function loadPanelPrefs() {
  try {
    return JSON.parse(localStorage.getItem(PANEL_STORAGE_KEY) || "{}") || {};
  } catch {
    return {};
  }
}

function savePanelPrefs(prefs) {
  try {
    localStorage.setItem(PANEL_STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    /* ignore quota / private mode */
  }
}

function setPanelOpen(panelId, open, { persist = true } = {}) {
  const panel = $(panelId);
  if (!panel) return;
  const toggle = panel.querySelector(".panel__toggle");
  const body = panel.querySelector(".panel__body");
  panel.classList.toggle("is-collapsed", !open);
  if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
  if (body) {
    if (open) body.removeAttribute("hidden");
    else body.setAttribute("hidden", "");
  }
  if (persist) {
    const key = panel.dataset.panel;
    if (key) {
      const prefs = loadPanelPrefs();
      prefs[key] = open;
      savePanelPrefs(prefs);
    }
  }
}

function initPanels() {
  const prefs = loadPanelPrefs();
  // Session stays collapsed until there is content (unless user left it open).
  // Memory & Skills: collapsed by default after first visit; open only if user preferred.
  ["memory", "skills"].forEach((key) => {
    const open = prefs[key] === true;
    setPanelOpen(`${key}Panel`, open, { persist: false });
  });
  const sessionOpen = prefs.session === true;
  setPanelOpen("sessionPanel", sessionOpen, { persist: false });

  document.querySelectorAll(".panel__toggle").forEach((toggle) => {
    toggle.addEventListener("click", () => {
      const panel = toggle.closest(".panel");
      if (!panel) return;
      const open = panel.classList.contains("is-collapsed");
      setPanelOpen(panel.id, open);
      if (panel.id === "sessionPanel" && open) {
        const badge = $("sessionBadge");
        if (badge) badge.hidden = true;
      }
    });
  });
}

function revealSession({ badge = true } = {}) {
  setPanelOpen("sessionPanel", true);
  const empty = $("councilEmpty");
  if (empty) empty.style.display = "none";
  const summary = $("sessionSummary");
  if (summary) summary.textContent = "Latest reply — scroll inside if it’s long";
  if (badge) {
    const badgeEl = $("sessionBadge");
    if (badgeEl) badgeEl.hidden = false;
  }
  const scroll = $("sessionScroll");
  if (scroll) scroll.scrollTop = 0;
}

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.error || res.statusText);
  return data;
}

function getMode() {
  const active = document.querySelector(".mode-btn.is-active");
  return active?.dataset.mode || "tools";
}

function setMode(mode) {
  const switchEl = document.querySelector(".mode-switch");
  const buttons = document.querySelectorAll(".mode-btn");
  buttons.forEach((btn) => {
    const on = btn.dataset.mode === mode;
    btn.classList.toggle("is-active", on);
    btn.setAttribute("aria-checked", on ? "true" : "false");
  });
  if (switchEl) switchEl.dataset.active = mode;
  const hint = $("modeHint");
  if (hint) hint.textContent = MODE_HINTS[mode] || MODE_HINTS.tools;
  const askHint = $("askHint");
  if (askHint) askHint.textContent = ASK_HINTS[mode] || ASK_HINTS.tools;
}

function askPayloadFlags() {
  const mode = getMode();
  return {
    execute: mode !== "direct",
    force_council: mode === "council",
  };
}

async function refreshStatus() {
  const el = $("status");
  const text = $("statusText");
  try {
    const s = await api("/api/status");
    if (!s.ollama) {
      el.className = "status-line bad";
      text.textContent = "Ollama offline — install & pull qwen3.5:4b";
    } else if (!s.model_installed) {
      el.className = "status-line bad";
      text.textContent = `Ollama up — pull ${s.model}`;
    } else {
      el.className = "status-line ok";
      text.textContent = `${s.model} · daemon 24/7 · online advisor ${s.online_advisor ? "on" : "off"}`;
    }
  } catch {
    el.className = "status-line bad";
    text.textContent = "Voxoryl API unreachable";
  }
}

async function refreshMemory() {
  const data = await api("/api/memory");
  const profile = data.profile || {};
  const factsList = data.facts || [];
  const facts = factsList.slice(-6).map((f) => `• ${f.text}`).join("\n");
  const hasAny =
    profile.owner ||
    (profile.projects || []).length ||
    (profile.goals || []).length ||
    (profile.preferences || []).length ||
    factsList.length;

  if (!hasAny) {
    $("memoryBox").textContent = "Nothing here yet";
    return;
  }

  $("memoryBox").textContent =
    `Owner: ${profile.owner || "—"}\n` +
    `Projects: ${(profile.projects || []).join(", ") || "—"}\n` +
    `Goals: ${(profile.goals || []).join(", ") || "—"}\n` +
    `Prefs: ${(profile.preferences || []).join(", ") || "—"}\n\n` +
    `Facts:\n${facts || "• Nothing here yet"}`;
}

async function refreshSkills() {
  const data = await api("/api/skills");
  const skills = data.skills || [];
  const list = $("skillsList");
  const empty = $("skillsEmpty");
  if (!skills.length) {
    list.innerHTML = "";
    if (empty) empty.hidden = false;
    return;
  }
  if (empty) empty.hidden = true;
  list.innerHTML = skills
    .map((s) => `<li><strong>${escapeHtml(s.name)}</strong>${s.always ? " (always on)" : ""} — ${escapeHtml(s.description)}</li>`)
    .join("");
}

function renderCouncil(result) {
  revealSession();
  $("voices").innerHTML = (result.voices || [])
    .map(
      (v) => `<div class="voice"><h3>${escapeHtml(v.name)}</h3><p>${escapeHtml(v.content)}</p></div>`
    )
    .join("");

  const online = result.online || {};
  $("online").innerHTML = online.used
    ? `<div class="online-box"><h3>Online advisor (${escapeHtml(online.model || "groq")})</h3><p>${escapeHtml(online.content)}</p></div>`
    : `<div class="online-box"><h3>Online advisor</h3><p class="meta">${escapeHtml(online.reason || "Not used this time")}</p></div>`;

  const syn = result.synthesis || {};
  const actions = (syn.next_actions || []).map((a) => `• ${a}`).join("\n");
  $("synthesis").innerHTML = `
    <div class="decision">
      <h3>Decision</h3>
      <p>${escapeHtml(syn.decision || "")}</p>
      <p class="meta">${escapeHtml(syn.rationale || "")}</p>
      <p class="meta">Confidence: ${syn.confidence ?? "—"}</p>
      <p>${escapeHtml(actions)}</p>
    </div>`;
}

function renderTalk(result) {
  revealSession();
  $("voices").innerHTML = "";
  $("online").innerHTML = "";
  const toolBits = (result.tools || []).map((t) => t.tool).filter(Boolean).join(", ");
  const meta = result.pipeline
    ? `Via: ${result.pipeline}`
    : toolBits || "Direct reply";
  $("synthesis").innerHTML = `
    <div class="decision">
      <h3>${escapeHtml(result.mode || "Reply")}</h3>
      <p>${escapeHtml(result.speak || "")}</p>
      <p class="meta">${escapeHtml(meta)}</p>
    </div>`;
}

function escapeHtml(str) {
  return String(str || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

initPanels();

document.querySelectorAll(".mode-btn").forEach((btn) => {
  btn.addEventListener("click", () => setMode(btn.dataset.mode));
});
setMode(getMode());

$("askBtn").addEventListener("click", async () => {
  const message = $("prompt").value.trim();
  if (!message) return;
  const flags = askPayloadFlags();
  $("askBtn").disabled = true;
  $("askBtn").textContent = flags.force_council ? "Council thinking…" : "Voxoryl thinking…";
  try {
    const result = await api("/api/ask", {
      method: "POST",
      body: JSON.stringify({
        message,
        execute: flags.execute,
        force_council: flags.force_council,
      }),
    });
    if (result.council || result.mode === "council") {
      renderCouncil(result.council || result);
    } else {
      renderTalk(result);
    }
    await refreshMemory();
  } catch (err) {
    alert(err.message);
  } finally {
    $("askBtn").disabled = false;
    $("askBtn").textContent = "Ask Voxoryl";
  }
});

$("prompt").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
    e.preventDefault();
    $("askBtn").click();
  }
});

async function remember() {
  const text = $("memText").value.trim();
  if (!text) return;
  await api("/api/remember", {
    method: "POST",
    body: JSON.stringify({ text, kind: $("memKind").value }),
  });
  $("memText").value = "";
  await refreshMemory();
  setPanelOpen("memoryPanel", true);
}

const teachForm = $("teachForm");
if (teachForm) {
  teachForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await remember();
    } catch (err) {
      alert(err.message);
    }
  });
} else {
  $("memBtn").addEventListener("click", async () => {
    try {
      await remember();
    } catch (err) {
      alert(err.message);
    }
  });
}

$("dailyBtn").addEventListener("click", async () => {
  const ok = window.confirm(
    "Run Voxoryl’s daily check now?\n\nThis may take a minute and can update memory or start a Council session."
  );
  if (!ok) return;

  $("dailyBtn").disabled = true;
  $("dailyBtn").textContent = "Running daily check…";
  try {
    const result = await api("/api/daily/run", { method: "POST", body: "{}" });
    if (result.council) renderCouncil(result.council);
    await refreshMemory();
  } catch (err) {
    alert(err.message);
  } finally {
    $("dailyBtn").disabled = false;
    $("dailyBtn").textContent = "Run daily check";
  }
});

refreshStatus();
refreshMemory();
refreshSkills();
setInterval(refreshStatus, 15000);
