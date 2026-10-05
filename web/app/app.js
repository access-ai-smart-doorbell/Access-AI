/* AccessAI mobile PWA — vanilla JS, no build step, no CDN.
 *
 * A THIN CLIENT over the existing FastAPI backend (Phases 1–15). Everything is
 * keyed off a configurable server base URL (localStorage), defaulting to the
 * origin the app was served from, so the SAME app works on a phone via the LAN
 * IP. It renders the FULL multi-person `people` list, drives the fast visual-only
 * doorbell separately from the audio-recording "Hear Visitor", and speaks live
 * alerts on the phone in Blind/Both mode.
 */

const $ = (id) => document.getElementById(id);

/* ---------------- Server base URL + tiny API layer ---------------- */
const URL_KEY = "accessai.serverUrl";
let SERVER = (localStorage.getItem(URL_KEY) || location.origin).replace(/\/+$/, "");
/* Phase 17: optional bearer token (the server's ACCESSAI_TOKEN). fetch() sends
 * it as a header; <img> tags (MJPEG, snapshots) and the WebSocket can't set
 * headers, so api() appends ?token= for them instead. */
const TOKEN_KEY = "accessai.token";
let TOKEN = localStorage.getItem(TOKEN_KEY) || "";

function tokenQS(url) {
  if (!TOKEN) return url;
  return url + (url.includes("?") ? "&" : "?") +
    "token=" + encodeURIComponent(TOKEN);
}
const api = (path) => tokenQS(`${SERVER}${path}`);
/* Append a query param to a URL that may already carry ?token= */
const withQS = (url, qs) => url + (url.includes("?") ? "&" : "?") + qs;
function wsUrl() {
  const u = new URL(SERVER);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  u.pathname = "/events";
  return tokenQS(u.toString());
}
async function apiFetch(path, opts = {}) {
  if (TOKEN) {
    opts = { ...opts, headers: { ...(opts.headers || {}),
                                 "Authorization": `Bearer ${TOKEN}` } };
  }
  return fetch(api(path), opts);
}
async function apiJSON(path, opts) {
  const r = await apiFetch(path, opts);
  if (r.status === 401) throw new Error("unauthorized — set the access token in Settings");
  if (!r.ok) throw new Error(`${path} → ${r.status}`);
  return r.json();
}

/* ---------------- App state ---------------- */
let currentMode = "both";        // synced with the server via /mode
let lastEvent = null;
let audioUnlocked = false;
let lastSpeechPath = "none";      // "kokoro" | "browser" | "none" (for reporting)
const speakAudioEl = new Audio();
let audioCtx = null;

/* =====================================================================
 * Tab navigation
 * ===================================================================== */
const panels = {
  home: $("panel-home"), live: $("panel-live"),
  history: $("panel-history"), settings: $("panel-settings"),
};
function showTab(name) {
  document.body.dataset.tab = name;
  for (const [k, el] of Object.entries(panels)) {
    const on = k === name;
    el.hidden = !on;
    el.classList.toggle("is-active", on);
  }
  document.querySelectorAll(".nav-btn").forEach((b) => {
    const on = b.dataset.tab === name;
    b.classList.toggle("is-active", on);
    if (on) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  if (name === "history") refreshHistory();
  if (name === "settings") refreshSettings();
}
document.querySelectorAll(".nav-btn").forEach((b) =>
  b.addEventListener("click", () => showTab(b.dataset.tab)));

/* =====================================================================
 * Multi-person rendering (shared by Home + the alert overlay)
 * ===================================================================== */

// InsightFace age is approximate, so it is ONLY ever spoken/shown as a RANGE,
// never a raw number, and NEVER for known people.
function ageRange(age) {
  if (age == null || isNaN(age)) return "";
  const a = Math.round(age);
  if (a < 13) return "a child";
  if (a < 20) return "in their teens";
  const decades = {
    20: "twenties", 30: "thirties", 40: "forties", 50: "fifties",
    60: "sixties", 70: "seventies", 80: "eighties",
  };
  const d = Math.floor(a / 10) * 10;
  return decades[d] ? `in their ${decades[d]}` : "an adult";
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Build the "N people — X known, Y unknown" reconciliation line.
function countLine(ev) {
  const people = ev.people || [];
  const extra = ev.extra_unknown || 0;
  const known = people.filter((p) => p.known).length;
  const unknownFaces = people.length - known;
  const total = people.length + extra || ev.visitor_count || 0;
  if (total <= 0) return "";
  const unknown = unknownFaces + extra;
  const bits = [];
  if (known) bits.push(`${known} known`);
  if (unknown) bits.push(`${unknown} unknown`);
  const noun = total === 1 ? "person" : "people";
  return bits.length ? `${total} ${noun} — ${bits.join(", ")}` : `${total} ${noun}`;
}

// One amber card describing an UNKNOWN person (rich, hedged, spoof-aware).
function unknownCard(p) {
  const lines = [];
  const bits = [];
  const ar = ageRange(p.age);
  if (p.gender && ar) bits.push(`a ${esc(p.gender)} ${esc(ar)}`);
  else if (p.gender) bits.push(`a ${esc(p.gender)}`);
  else if (ar) bits.push(esc(ar));
  if (bits.length) lines.push(`<div class="pcard-line"><b>${bits.join(" ")}</b></div>`);
  if (p.appearance) lines.push(`<div class="pcard-line">👕 ${esc(p.appearance)}</div>`);
  if (p.expression) lines.push(`<div class="pcard-line">🙂 appears ${esc(p.expression)}</div>`);
  const spoof = p.is_spoof
    ? `<span class="tag-spoof">⚠ photo</span>` : "";
  return `<div class="pcard${p.is_spoof ? " spoof" : ""}">
      <div class="pcard-head">👤 Unknown visitor ${spoof}</div>
      ${lines.join("")}
    </div>`;
}

// A green chip for a KNOWN person (name only, plus small confidence).
function knownChip(p) {
  const conf = p.confidence ? `<span class="conf">${Math.round(p.confidence * 100)}%</span>` : "";
  const spoof = p.is_spoof ? ` <span class="tag-spoof">⚠ photo</span>` : "";
  return `<span class="chip known">✅ ${esc(p.name)}${conf}</span>${spoof}`;
}

// Render the FULL people list into `container`. Never collapses to one person.
function renderPeople(container, ev) {
  const people = (ev.people || []).slice().sort((a, b) => (a.box?.[0] || 0) - (b.box?.[0] || 0));
  const extra = ev.extra_unknown || 0;
  const line = countLine(ev);
  let html = line ? `<div class="count-line">${esc(line)}</div>` : "";

  if (people.length === 0 && (ev.visitor_count || 0) > 0) {
    html += `<div class="pcard"><div class="pcard-head">👤 ${ev.visitor_count} visitor(s) detected</div></div>`;
  }
  const known = people.filter((p) => p.known);
  const unknown = people.filter((p) => !p.known);
  if (known.length) html += known.map(knownChip).join(" ");
  html += unknown.map(unknownCard).join("");
  if (extra > 0) {
    html += `<div class="pcard"><div class="pcard-head">👥 ${extra} other ${extra === 1 ? "person" : "people"} (face not visible)</div></div>`;
  }
  container.innerHTML = html;
}

/* =====================================================================
 * Home tab
 * ===================================================================== */
function renderHome(ev) {
  lastEvent = ev;
  $("home-title").textContent = ev.identity && ev.identity.known
    ? `${ev.identity.name} is at the door`
    : (ev.announcement_text ? "Visitor at the door" : "Waiting for the doorbell…");
  $("home-time").textContent = ev.timestamp ? fmtTime(ev.timestamp) : "";
  $("home-announce").textContent = ev.announcement_text || "—";

  const snap = $("home-snap");
  if (ev.event_id) {
    snap.src = withQS(api(`/snapshot/${ev.event_id}`), `t=${Date.now()}`);
    snap.hidden = false;
  } else {
    snap.hidden = true;
  }
  renderPeople($("home-people"), ev);
  showCaption(ev.speech_transcript, ev.translated_transcript);
}

function showCaption(text, translated) {
  const c = $("home-caption");
  if (!text && !translated) { c.hidden = true; return; }
  let html = "";
  if (text) html += `🗣 “${esc(text)}”`;
  if (translated && translated !== text) html += `<br>🌐 ${esc(translated)}`;
  c.innerHTML = html;
  c.hidden = false;
}

function fmtTime(iso) {
  try {
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString(undefined, { hour: "2-digit", minute: "2-digit", month: "short", day: "numeric" });
  } catch { return iso; }
}

// Test Ring — VISUAL ONLY + FAST. No "listening" indicator; feels instant.
$("ring-btn").addEventListener("click", async () => {
  unlockAudio();
  const btn = $("ring-btn");
  btn.disabled = true;
  const t0 = performance.now();
  try {
    const ev = await apiJSON("/trigger", { method: "POST" });
    const ms = Math.round(performance.now() - t0);
    renderHome(ev);
    $("home-announce").textContent = ev.announcement_text || $("home-announce").textContent;
    console.log(`[AccessAI] doorbell responded in ${ms} ms (visual-only)`);
  } catch (e) {
    toast($("reply-note"), `Ring failed: ${e.message}`, true);
  } finally {
    btn.disabled = false;
  }
});

// Hear Visitor — the ONLY audio-recording control. Shows a listening indicator.
$("hear-btn").addEventListener("click", () => doHearVisitor($("hear-btn"), $("hear-sub")));
$("alert-hear").addEventListener("click", () => doHearVisitor($("alert-hear")));

async function doHearVisitor(btn, subEl) {
  unlockAudio();
  btn.disabled = true;
  btn.classList.add("listening");
  let secs = 0;
  const origSub = subEl ? subEl.textContent : "";
  const tick = setInterval(() => {
    secs++;
    if (subEl) subEl.textContent = `🎤 Listening… (${secs}s)`;
  }, 1000);
  try {
    const r = await apiJSON("/hear_visitor", { method: "POST" });
    showCaption(r.transcript, r.translated);
    if (r.transcript) toast($("reply-note"), `Heard: “${r.transcript}”`, false);
    else toast($("reply-note"), "No speech detected.", false);
  } catch (e) {
    toast($("reply-note"), `Hear Visitor: ${e.message}`, true);
  } finally {
    clearInterval(tick);
    btn.classList.remove("listening");
    btn.disabled = false;
    if (subEl) subEl.textContent = origSub || "records audio";
  }
}

// Reply — spoken at the door. Optional `preset` lets a quick-reply chip send
// directly without touching the text box.
$("reply-btn").addEventListener("click", () => sendReply());
$("reply-text").addEventListener("keydown", (e) => { if (e.key === "Enter") sendReply(); });
async function sendReply(preset) {
  const text = (preset != null ? preset : $("reply-text").value).trim();
  if (!text) return;
  try {
    const r = await apiJSON("/reply", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (preset == null) $("reply-text").value = "";
    toast($("reply-note"), r.spoken ? "Spoken at the door ✓" : "Sent (audio unavailable at the door).", !r.spoken);
  } catch (e) {
    toast($("reply-note"), `Reply failed: ${e.message}`, true);
  }
}

// Phase 17: load canned quick replies and render them as one-tap chips.
async function loadQuickReplies() {
  const box = $("quick-replies");
  if (!box) return;
  try {
    const r = await apiJSON("/quick_replies");
    const replies = (r && r.replies) || [];
    box.innerHTML = "";
    for (const text of replies) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip-reply";
      b.textContent = text;
      b.addEventListener("click", () => sendReply(text));
      box.appendChild(b);
    }
  } catch {
    box.innerHTML = "";   // server too old / unauthorized — hide silently
  }
}
loadQuickReplies();

// Ask — text or on-phone speech recognition → /command → spoken answer.
$("ask-btn").addEventListener("click", () => sendCommand($("ask-text").value));
$("ask-text").addEventListener("keydown", (e) => { if (e.key === "Enter") sendCommand($("ask-text").value); });
$("ask-mic").addEventListener("click", askByVoice);

async function sendCommand(text) {
  text = (text || "").trim();
  if (!text) return;
  unlockAudio();
  try {
    const r = await apiJSON("/command", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    $("ask-text").value = "";
    toast($("ask-note"), r.answer || "(no answer)", false);
    phoneSpeak(r.answer, true);   // speak the answer on the phone regardless of mode
  } catch (e) {
    toast($("ask-note"), `Ask failed: ${e.message}`, true);
  }
}

function askByVoice() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { toast($("ask-note"), "Voice input isn't supported here — type instead.", true); $("ask-text").focus(); return; }
  unlockAudio();
  const rec = new SR();
  rec.lang = "en-US";
  rec.interimResults = false;
  rec.maxAlternatives = 1;
  const mic = $("ask-mic");
  mic.classList.add("listening");
  toast($("ask-note"), "Listening…", false);
  rec.onresult = (e) => {
    const said = e.results[0][0].transcript;
    $("ask-text").value = said;
    sendCommand(said);
  };
  rec.onerror = (e) => toast($("ask-note"), `Voice error: ${e.error}`, true);
  rec.onend = () => mic.classList.remove("listening");
  try { rec.start(); } catch { mic.classList.remove("listening"); }
}

/* =====================================================================
 * Live tab (MJPEG)
 * ===================================================================== */
const liveImg = $("live-img");
const liveOverlay = $("live-overlay");
function liveConnect() {
  liveImg.src = withQS(api(`/video`), `t=${Date.now()}`);
  liveOverlay.textContent = "Connecting to the camera…";
  liveImg.onload = () => { liveOverlay.style.display = "none"; };
  liveImg.onerror = () => { liveOverlay.style.display = "grid"; liveOverlay.textContent = "Camera unavailable — tap Reconnect."; };
}
function liveStop() { liveImg.removeAttribute("src"); liveOverlay.style.display = "grid"; liveOverlay.textContent = "Live view stopped."; }
$("live-connect").addEventListener("click", liveConnect);
$("live-reconnect").addEventListener("click", liveConnect);
$("live-stop").addEventListener("click", liveStop);

/* =====================================================================
 * History tab
 * ===================================================================== */
$("hist-refresh").addEventListener("click", refreshHistory);
{
  // Debounced live search over the server-side ?q= filter.
  let histTimer = null;
  $("hist-search").addEventListener("input", () => {
    clearTimeout(histTimer);
    histTimer = setTimeout(refreshHistory, 250);
  });
}

async function refreshHistory() {
  const list = $("history-list");
  try {
    const q = ($("hist-search")?.value || "").trim();
    const rows = await apiJSON(
      "/history?limit=50" + (q ? `&q=${encodeURIComponent(q)}` : ""));
    $("history-empty").hidden = rows.length > 0;
    $("history-empty").textContent =
      q && rows.length === 0 ? `No visits match “${q}”.` : "No visits yet.";
    list.innerHTML = "";
    rows.forEach((ev) => list.appendChild(historyItem(ev)));
  } catch (e) {
    list.innerHTML = "";
    $("history-empty").hidden = false;
    $("history-empty").textContent = `Could not load history: ${e.message}`;
  }
}

function historyItem(ev) {
  const li = document.createElement("li");
  const who = historyWho(ev);
  const sub = ev.announcement_text || ev.intent || "";
  const li_html = `
    <img class="hist-thumb" src="${api(`/snapshot/${ev.event_id}`)}" alt="" onerror="this.style.visibility='hidden'"/>
    <div class="hist-main">
      <div class="hist-who">${esc(who)}</div>
      <div class="hist-sub">${esc(sub)}</div>
      <div class="hist-time">${esc(fmtTime(ev.timestamp))}</div>
    </div>
    <button class="hist-del" aria-label="Delete this visit">🗑</button>`;
  const btn = document.createElement("button");
  btn.className = "hist-item";
  btn.innerHTML = li_html;
  btn.addEventListener("click", (e) => {
    if (e.target.closest(".hist-del")) return;    // delete handled below
    toggleHistoryDetail(li, ev.event_id);
  });
  btn.querySelector(".hist-del").addEventListener("click", (e) => {
    e.stopPropagation();
    deleteEvent(ev.event_id, li);
  });
  li.appendChild(btn);
  return li;
}

function historyWho(ev) {
  const people = ev.people || [];
  const knownNames = people.filter((p) => p.known).map((p) => p.name);
  const total = (people.length + (ev.extra_unknown || 0)) || ev.visitor_count || 0;
  let primary = knownNames[0] || (ev.identity && ev.identity.known ? ev.identity.name : "Unknown visitor");
  if (total > 1) primary += ` +${total - 1}`;
  return primary;
}

async function toggleHistoryDetail(li, id) {
  const existing = li.querySelector(".hist-detail");
  if (existing) { existing.remove(); return; }
  try {
    const ev = await apiJSON(`/event/${id}`);
    const box = document.createElement("div");
    box.className = "card hist-detail";
    renderPeople(box, ev);
    if (ev.speech_transcript) {
      const cap = document.createElement("div");
      cap.className = "caption";
      cap.innerHTML = `🗣 “${esc(ev.speech_transcript)}”` +
        (ev.translated_transcript ? `<br>🌐 ${esc(ev.translated_transcript)}` : "");
      box.appendChild(cap);
    }
    li.appendChild(box);
  } catch (e) { /* ignore */ }
}

async function deleteEvent(id, li) {
  if (!confirm("Delete this visit from history?")) return;
  try {
    await apiFetch(`/event/${id}/delete`, { method: "POST" });
    li.remove();
  } catch (e) { /* ignore */ }
}

/* =====================================================================
 * Settings tab
 * ===================================================================== */
$("server-save").addEventListener("click", () => {
  const v = $("server-url").value.trim().replace(/\/+$/, "");
  if (!v) return;
  SERVER = v;
  localStorage.setItem(URL_KEY, v);
  toast($("server-note"), "Saved. Reconnecting…", false);
  connectWS();      // reconnect the live channel to the new server
});
$("server-test").addEventListener("click", async () => {
  try {
    const s = await apiJSON("/status");
    toast($("server-note"), `Connected ✓ (torch ${s.torch_version}, ${(s.modules || []).length} modules)`, false);
  } catch (e) {
    toast($("server-note"), `Failed: ${e.message}`, true);
  }
});
$("token-save").addEventListener("click", () => {
  TOKEN = $("server-token").value.trim();
  if (TOKEN) localStorage.setItem(TOKEN_KEY, TOKEN);
  else localStorage.removeItem(TOKEN_KEY);
  toast($("server-note"), TOKEN ? "Token saved. Reconnecting…" : "Token cleared.", false);
  connectWS();      // reconnect the live channel with the new credentials
});

// Mode segmented control.
document.querySelectorAll(".seg-btn").forEach((b) =>
  b.addEventListener("click", () => setMode(b.dataset.mode)));
async function setMode(mode) {
  try {
    const r = await apiJSON("/mode", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    applyMode(r.mode || mode);
  } catch { applyMode(mode); }
}
function applyMode(mode) {
  currentMode = mode;
  document.body.className = document.body.className.replace(/mode-\w+/, "") + ` mode-${mode}`;
  document.querySelectorAll(".seg-btn").forEach((b) => {
    const on = b.dataset.mode === mode;
    b.classList.toggle("is-on", on);
    b.setAttribute("aria-checked", on ? "true" : "false");
  });
}

// Voices.
async function loadVoices() {
  const sel = $("voice-sel");
  try {
    const v = await apiJSON("/voices");
    sel.innerHTML = "";
    (v.voices || []).forEach((voice) => {
      const id = voice.id || voice;
      const label = voice.label || voice.name || id;
      const opt = document.createElement("option");
      opt.value = id;
      opt.textContent = label + (voice.available === false ? " (offline)" : "");
      if (id === v.current) opt.selected = true;
      sel.appendChild(opt);
    });
    if (!sel.children.length) sel.innerHTML = `<option>No voices</option>`;
  } catch { sel.innerHTML = `<option>Voices unavailable</option>`; }
}
$("voice-sel").addEventListener("change", async (e) => {
  try {
    const r = await apiJSON("/voice", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: e.target.value }),
    });
    toast($("voice-note"), r.ok ? `Voice set: ${r.voice}` : (r.message || "Could not switch voice"), !r.ok);
  } catch (err) { toast($("voice-note"), `Voice error: ${err.message}`, true); }
});
$("voice-test").addEventListener("click", async () => {
  try {
    await apiJSON("/reply", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: "Hello, this is AccessAI." }),
    });
    toast($("voice-note"), "Spoke a test line at the door.", false);
  } catch (e) { toast($("voice-note"), `Test failed: ${e.message}`, true); }
});

// Known people.
async function loadKnown() {
  const list = $("known-list");
  try {
    const d = await apiJSON("/known");
    list.innerHTML = "";
    (d.people || []).forEach((p) => {
      const li = document.createElement("li");
      li.className = "known-row";
      const thumb = p.sample ? api(p.sample) : "";
      li.innerHTML = `
        ${thumb ? `<img src="${thumb}" alt="" onerror="this.style.visibility='hidden'"/>` : `<img alt=""/>`}
        <span class="kn-name">${esc(p.name)}</span>
        <span class="kn-count">${p.photos || 0} photo${(p.photos || 0) === 1 ? "" : "s"}</span>
        <button class="kn-del">Delete</button>`;
      li.querySelector(".kn-del").addEventListener("click", () => deleteKnown(p.name));
      list.appendChild(li);
    });
    if (!list.children.length) list.innerHTML = `<li class="hint">No one enrolled yet.</li>`;
  } catch (e) { list.innerHTML = `<li class="hint">Known people unavailable: ${esc(e.message)}</li>`; }
}
async function deleteKnown(name) {
  if (!confirm(`Delete ${name} and their photos?`)) return;
  try {
    await apiJSON("/known/delete", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    loadKnown();
  } catch (e) { toast($("enroll-note"), `Delete failed: ${e.message}`, true); }
}
$("enroll-btn").addEventListener("click", async () => {
  const name = $("enroll-name").value.trim();
  const files = $("enroll-files").files;
  if (!name) { toast($("enroll-note"), "Enter a name first.", true); return; }
  if (!files.length) { toast($("enroll-note"), "Choose at least one photo.", true); return; }
  const fd = new FormData();
  fd.append("name", name);
  for (const f of files) fd.append("files", f);
  toast($("enroll-note"), "Uploading…", false);
  try {
    const r = await apiJSON("/enroll_upload", { method: "POST", body: fd });
    const skipped = (r.skipped || []).length ? ` · skipped ${r.skipped.length} (${r.skipped.join("; ")})` : "";
    toast($("enroll-note"), `Added ${r.added} photo(s) for ${r.name}${skipped}.`, false);
    $("enroll-name").value = ""; $("enroll-files").value = "";
    loadKnown();
  } catch (e) { toast($("enroll-note"), `Enroll failed: ${e.message}`, true); }
});

// Health.
async function loadHealth() {
  const box = $("health");
  try {
    const s = await apiJSON("/status");
    box.innerHTML = (s.modules || []).map((m) =>
      `<div class="h-row"><span class="h-dot ${m.state}"></span>
        <span class="h-name">${esc(m.name)}</span>
        <span class="h-detail">${esc(m.detail || m.state)}</span></div>`).join("");
  } catch (e) { box.innerHTML = `<div class="hint">Health unavailable: ${esc(e.message)}</div>`; }
}

function refreshSettings() {
  $("server-url").value = SERVER;
  $("server-token").value = TOKEN;
  loadVoices(); loadKnown(); loadHealth();
  apiJSON("/mode").then((r) => applyMode(r.mode)).catch(() => {});
}

/* =====================================================================
 * Live alerts over WebSocket
 * ===================================================================== */
let ws = null;
let wsBackoff = 1000;
function setConn(state, text) {
  const c = $("conn");
  c.className = "conn " + state;
  $("conn-text").textContent = text;
}
function connectWS() {
  if (ws) { try { ws.onclose = null; ws.close(); } catch {} ws = null; }
  setConn("", "connecting…");
  try {
    ws = new WebSocket(wsUrl());
  } catch { scheduleReconnect(); return; }
  ws.onopen = () => { wsBackoff = 1000; setConn("online", "live"); };
  ws.onclose = () => { setConn("offline", "offline"); scheduleReconnect(); };
  ws.onerror = () => { try { ws.close(); } catch {} };
  ws.onmessage = (e) => {
    let msg;
    try { msg = JSON.parse(e.data); } catch { return; }
    handleMessage(msg);
  };
}
function scheduleReconnect() {
  wsBackoff = Math.min(wsBackoff * 1.6, 15000);
  setTimeout(connectWS, wsBackoff);
}
function handleMessage(msg) {
  // Resilient: never throw on an unknown message type.
  switch (msg && msg.type) {
    case "event":      onDoorbell(msg.event); break;
    case "visitor_speech": showCaption(msg.text, msg.translated); break;
    case "voice":      if (msg.answer) toast($("ask-note"), msg.answer, false); break;
    case "history_update": if (document.body.dataset.tab === "history") refreshHistory(); break;
    case "known_update":   if (document.body.dataset.tab === "settings") loadKnown(); break;
    case "ping": default:  break;
  }
}

/* Phase 17: per-kind alert channels. One alert_kind string from the server
 * keys vibration rhythm, notification urgency, and repeat-until-ack, so a
 * deaf user can classify the visitor without reading. */
const VIBRATIONS = {
  known:    [150, 80, 300],
  delivery: [120, 90, 120, 90, 120],
  unknown:  [400],
  spoof:    [600, 200, 600, 200, 600],
};
const KIND_LABEL = {
  known: "Known visitor", delivery: "Likely delivery",
  unknown: "Unknown visitor", spoof: "⚠ Possible spoof",
};
function alertKindOf(ev) {
  return ev.alert_kind ||
    (ev.is_spoof ? "spoof"
     : (ev.identity && ev.identity.known) ? "known"
     : /delivery/.test(ev.intent || "") ? "delivery" : "unknown");
}
let alertRepeat = null;

function onDoorbell(ev) {
  if (!ev) return;
  renderHome(ev);
  showAlert(ev);
  const kind = alertKindOf(ev);
  // DEAF / BOTH: flash + per-kind vibration + beep, repeating until the
  // overlay is dismissed (one missed flash must not be a missed visitor).
  if (currentMode === "deaf" || currentMode === "both") {
    const fire = () => {
      flash(); beep();
      if (navigator.vibrate) {
        try { navigator.vibrate(VIBRATIONS[kind] || VIBRATIONS.unknown); } catch {}
      }
    };
    fire();
    clearInterval(alertRepeat);
    let rounds = 0;
    alertRepeat = setInterval(() => {
      if ($("alert-overlay").hidden || ++rounds >= 5) {
        clearInterval(alertRepeat); alertRepeat = null; return;
      }
      fire();
    }, 6000);
  }
  // Hidden app (other tab / screen off with browser alive): OS notification.
  if (document.hidden && "Notification" in window) {
    const fireNote = () => {
      try {
        const n = new Notification(`AccessAI — ${KIND_LABEL[kind] || kind}`, {
          body: ev.announcement_text || "Someone is at the door.",
          tag: "accessai-doorbell",
          requireInteraction: kind === "spoof",
        });
        n.onclick = () => { window.focus(); n.close(); };
      } catch {}
    };
    if (Notification.permission === "granted") fireNote();
    else if (Notification.permission !== "denied") {
      Notification.requestPermission().then((p) => { if (p === "granted") fireNote(); });
    }
  }
  // BLIND / BOTH: speak the announcement on the phone.
  if (currentMode === "blind" || currentMode === "both") {
    phoneSpeak(ev.announcement_text);
  }
}

/* ---------------- Alert overlay ---------------- */
function showAlert(ev) {
  $("alert-text").textContent = ev.announcement_text || "Someone is at the door.";
  const snap = $("alert-snap");
  if (ev.event_id) { snap.src = withQS(api(`/snapshot/${ev.event_id}`), `t=${Date.now()}`); snap.hidden = false; }
  else snap.hidden = true;
  renderPeople($("alert-people"), ev);
  const ov = $("alert-overlay");
  ov.hidden = false; ov.setAttribute("aria-hidden", "false");
}
function hideAlert() {
  const ov = $("alert-overlay");
  ov.hidden = true; ov.setAttribute("aria-hidden", "true");
  if (alertRepeat) { clearInterval(alertRepeat); alertRepeat = null; }
  speakAudioEl.pause();
}
$("alert-ok").addEventListener("click", hideAlert);
$("alert-close").addEventListener("click", hideAlert);

/* =====================================================================
 * Phone-side speech (Blind/Both) — Kokoro via /speak_audio, else browser
 * ===================================================================== */
async function phoneSpeak(text, force, lang) {
  text = (text || "").trim();
  if (!text) return;
  if (!force && !(currentMode === "blind" || currentMode === "both")) return;
  // Preferred: natural neural voice synthesized server-side.
  try {
    const l = lang ? `&lang=${encodeURIComponent(lang)}` : "";
    const r = await apiFetch(`/speak_audio?text=${encodeURIComponent(text)}${l}`);
    if (r.ok) {
      const blob = await r.blob();
      speakAudioEl.src = URL.createObjectURL(blob);
      await speakAudioEl.play();
      lastSpeechPath = "neural";
      return;
    }
  } catch { /* fall through to the browser voice */ }
  // Fallback: browser Web Speech API (robotic but works offline / no key).
  if ("speechSynthesis" in window) {
    try {
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      window.speechSynthesis.speak(u);
      lastSpeechPath = "browser";
    } catch { /* silent */ }
  }
}

/* ---------------- Mobile autoplay unlock + beep ---------------- */
function unlockAudio() {
  if (audioUnlocked) return;
  audioUnlocked = true;
  try {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
  } catch { audioCtx = null; }
  // Prime speechSynthesis with a silent utterance so later speak() is allowed.
  if ("speechSynthesis" in window) {
    try { const u = new SpeechSynthesisUtterance(""); u.volume = 0; window.speechSynthesis.speak(u); } catch {}
  }
  // Prime the audio element too.
  try { speakAudioEl.muted = true; speakAudioEl.play().catch(() => {}); speakAudioEl.muted = false; } catch {}
}
window.addEventListener("pointerdown", unlockAudio, { once: false });

function beep() {
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
    const t = audioCtx.currentTime;
    [0, 0.28].forEach((off) => {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.type = "sine"; o.frequency.value = 880;
      g.gain.setValueAtTime(0.0001, t + off);
      g.gain.exponentialRampToValueAtTime(0.35, t + off + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + off + 0.22);
      o.connect(g); g.connect(audioCtx.destination);
      o.start(t + off); o.stop(t + off + 0.24);
    });
  } catch { /* no audio device — Deaf users rely on flash + vibrate */ }
}

/* ---------------- Deaf-mode visual flash ---------------- */
function flash() {
  const f = $("flash");
  f.classList.remove("on"); void f.offsetWidth; f.classList.add("on");
  setTimeout(() => f.classList.remove("on"), 1900);
}

/* ---------------- Small toast helper ---------------- */
function toast(el, msg, isErr) {
  if (!el) return;
  el.textContent = msg;
  el.className = "note " + (isErr ? "err" : "ok");
}

/* =====================================================================
 * Service worker — register ONLY in a secure context (per the constraint).
 * ===================================================================== */
function registerSW() {
  const state = $("pwa-state");
  if ("serviceWorker" in navigator && window.isSecureContext) {
    navigator.serviceWorker.register("/app/sw.js", { scope: "/app/" })
      .then(() => { if (state) state.textContent = "installable ✓ (service worker active)"; })
      .catch((e) => { if (state) state.textContent = `service worker failed: ${e.message}`; });
  } else if (state) {
    state.textContent = "works as a web app (install needs HTTPS/localhost)";
  }
}

/* =====================================================================
 * Boot
 * ===================================================================== */
async function boot() {
  $("server-url").value = SERVER;
  try { const r = await apiJSON("/mode"); applyMode(r.mode); } catch { applyMode("both"); }
  connectWS();
  registerSW();
  showTab("home");
}
boot();
