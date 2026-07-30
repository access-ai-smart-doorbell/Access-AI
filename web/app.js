// AccessAI dashboard - vanilla JS, no build step, no CDN.
// Subscribes to /events, renders the current event + history, and drives the
// Ring / Mode / Reply / Refresh controls.

/* --- Phase 17: bearer-token auth ----------------------------------------
 * When the server has ACCESSAI_TOKEN set, every API call must carry it. The
 * token lives in localStorage; a 401 prompts once and retries. fetch() is
 * wrapped globally so the ~40 existing call sites need no changes; the MJPEG
 * <img> and the WebSocket (no headers possible) get ?token= appended. */
const TOKEN_KEY = "accessai.token";
let authToken = localStorage.getItem(TOKEN_KEY) || "";

function tokenQS(url) {
  if (!authToken) return url;
  return url + (url.includes("?") ? "&" : "?") +
    "token=" + encodeURIComponent(authToken);
}

const _rawFetch = window.fetch.bind(window);
window.fetch = async (url, opts = {}) => {
  if (authToken && typeof url === "string" && url.startsWith("/")) {
    opts = { ...opts, headers: { ...(opts.headers || {}),
                                 "Authorization": `Bearer ${authToken}` } };
  }
  const r = await _rawFetch(url, opts);
  if (r.status === 401 && typeof url === "string" && url.startsWith("/")) {
    const t = window.prompt(
      "This AccessAI server requires an access token (ACCESSAI_TOKEN in its .env).\nEnter the token:");
    if (t && t.trim()) {
      authToken = t.trim();
      localStorage.setItem(TOKEN_KEY, authToken);
      const vid = document.getElementById("video");
      if (vid) vid.src = tokenQS("/video");
      return window.fetch(url, opts);  // retry once with the new token
    }
  }
  return r;
};

const el = (id) => document.getElementById(id);
let currentMode = "both";   // kept in sync with the server via /mode
let userLanguage = "en";    // Phase 8: target language, synced via /translate_status
const statusEl = el("status");
const currentEl = el("current");
const historyList = el("history-list");
const modeSel = el("mode");
const ringBtn = el("ring");
const replyBtn = el("reply-btn");
const replyInput = el("reply-text");
const replyNote = el("reply-note");
const refreshBtn = el("refresh");
const enrollBtn = el("enroll-btn");
const enrollName = el("enroll-name");
const enrollNote = el("enroll-note");
const knownList = el("known-list");
// Phase 13: upload-photo enrollment controls.
const upName = el("up-name");
const upFiles = el("up-files");
const upBtn = el("up-btn");
const upNote = el("up-note");

function setStatus(online) {
  statusEl.textContent = online ? "connected" : "disconnected";
  statusEl.className = "status " + (online ? "online" : "offline");
}

// Phase 12: map an APPROXIMATE age to the same cautious RANGE the server speaks,
// so the card never shows an exact number. Mirrors accessibility._age_descriptor.
function ageRange(age) {
  if (age == null || age === "") return "";
  const a = Number(age);
  if (!Number.isFinite(a)) return "";
  if (a < 13) return "a child";
  if (a < 20) return "a teenager";
  if (a < 30) return "in their twenties";
  if (a < 40) return "in their thirties";
  if (a < 50) return "in their forties";
  if (a < 65) return "middle-aged";
  return "elderly";
}

// Phase 15: render EVERY detected person. KNOWN people become a green name chip
// (name only, never age/gender); UNKNOWN people become an amber card with the
// cautious approximate age/gender + appearance + apparent mood, and a spoof note
// when the face was a photo. Face-less people YOLO counted fold into an "N other
// people" line. Returns "" for single-subject scenes (the existing single-person
// card already covers those), so nothing is double-rendered.
function personCard(p) {
  const bits = [];
  if (p.gender === "man" || p.gender === "woman") bits.push(p.gender);
  const ar = ageRange(p.age);
  if (ar) bits.push(ar);
  const head = bits.length ? bits.join(", ") : "unknown visitor";
  const ap = (p.appearance || "").trim();
  const ex = (p.expression || "").trim();
  return `<div class="person-card">
      <div class="p-head">🧍 ${head}</div>
      ${ap ? `<div class="p-line">👕 ${ap}</div>` : ""}
      ${ex ? `<div class="p-line">🙂 appears ${ex}</div>` : ""}
      ${p.is_spoof ? `<div class="p-line p-spoof">⚠ shown as a photo, possible spoof</div>` : ""}
    </div>`;
}

// A KNOWN person in a group. Phase 16: when the VLM added a clothing/mood line
// they get a green card (name + appearance + mood); otherwise a bare name chip.
// Age/gender is NEVER shown for a recognised person.
function knownItem(p) {
  const ap = (p.appearance || "").trim();
  const ex = (p.expression || "").trim();
  if (!ap && !ex) return `<span class="chip person-known">✅ ${p.name}</span>`;
  return `<div class="person-card known">
      <div class="p-head">✅ ${p.name}</div>
      ${ap ? `<div class="p-line">👕 ${ap}</div>` : ""}
      ${ex ? `<div class="p-line">🙂 appears ${ex}</div>` : ""}
    </div>`;
}

function renderPeople(ev) {
  const people = ev.people || [];
  const extra = ev.extra_unknown || 0;
  if (people.length + extra <= 1) return "";              // single-subject: skip
  const known = people.filter((p) => p.known && !p.is_spoof);
  const unknown = people.filter((p) => !(p.known && !p.is_spoof));
  const knownHtml = known.length
    ? `<div class="people-known">${known.map(knownItem).join(" ")}</div>`
    : "";
  const unknownHtml = unknown.map(personCard).join("");
  const extraHtml = extra > 0
    ? `<div class="p-line p-others">➕ ${extra} other ${
        extra === 1 ? "person" : "people"} (face not clearly visible)</div>`
    : "";
  return `<div class="people">${knownHtml}${unknownHtml}${extraHtml}</div>`;
}

// Compact one-liner for the history rows: "👥 Vinay + 2 unknown".
function renderPeopleCompact(ev) {
  const people = ev.people || [];
  const extra = ev.extra_unknown || 0;
  if (people.length + extra <= 1) return "";
  const parts = [];
  const known = people.filter((p) => p.known && !p.is_spoof).map((p) => p.name);
  if (known.length) parts.push(known.join(", "));
  const nUnknown = people.filter((p) => !(p.known && !p.is_spoof)).length + extra;
  if (nUnknown) parts.push(`${nUnknown} unknown`);
  return parts.length ? `<div class="h-meta">👥 ${parts.join(" + ")}</div>` : "";
}

function renderEvent(ev) {
  if (!ev) {
    currentEl.className = "event-card empty";
    currentEl.innerHTML =
      '<p class="hint">No visitor yet. Click <b>Ring Doorbell</b>.</p>';
    return;
  }
  const known = ev.identity && ev.identity.known;
  const spoof = ev.is_spoof;
  const cls = spoof ? "spoof" : known ? "known" : "unknown";
  currentEl.className = "event-card " + cls;

  // Phase 15: a scene with more than one subject gets a per-person breakdown and
  // its own headline; the single-person descr/appearance blocks are suppressed
  // (each person's detail lives in their own card instead).
  const peopleHtml = renderPeople(ev);
  const multi = peopleHtml !== "";

  const who = multi
    ? "Multiple people at the door"
    : spoof ? "Possible spoof" : known ? ev.identity.name : "Unknown visitor";
  const carrying = (ev.carried_objects || []).join(", ");

  // Approximate age + gender (Phase 12). UNKNOWN visitors only: we never label a
  // recognised person by age/gender. Age is shown as a RANGE, never an exact
  // number, mirroring what is spoken.
  let descrHtml = "";
  if (!known && !spoof && !multi) {
    const bits = [];
    if (ev.gender === "man" || ev.gender === "woman") bits.push(ev.gender);
    const ar = ageRange(ev.age);
    if (ar) bits.push(ar);
    if (bits.length)
      descrHtml = `<div class="descr">🧍 Approx: ${bits.join(", ")}</div>`;
  }
  // Appearance: clothing / uniform / carried (Phase 12/16 VLM). Phase 16 shows it
  // for KNOWN singles too (age/gender still stays unknown-only above). May arrive
  // a moment after the first event via an "event_update" broadcast.
  const appearance = (ev.appearance || "").trim();
  const appearanceHtml = (appearance && !multi)
    ? `<div class="appearance">👕 ${appearance}</div>` : "";
  // Apparent mood of a KNOWN single visitor (Phase 16, cautious VLM cue).
  const singleP = (ev.people || [])[0];
  const knownExpr = known && !multi && singleP ? (singleP.expression || "").trim() : "";
  const exprHtml = knownExpr
    ? `<div class="appearance">🙂 appears ${knownExpr}</div>` : "";
  const chips = [];
  // Liveness (Phase 5): loud red badge on a suspected spoof; otherwise a small
  // "live ✓" indicator when a real face was actually checked (score < 1 means
  // the detector ran; exactly 1.0 is the fail-open "not checked" default).
  if (spoof) {
    chips.push(`<span class="chip spoof-badge">⚠ SPOOF SUSPECTED</span>`);
  } else if (ev.face_box && ev.spoof_score != null && ev.spoof_score < 1) {
    chips.push(`<span class="chip live-badge">live ✓ ${Number(ev.spoof_score).toFixed(2)}</span>`);
  }
  // Delivery (Phase 6): stand-out chip when OCR/objects point to a courier.
  if (ev.intent === "likely delivery")
    chips.push(`<span class="chip delivery-badge">📦 Delivery</span>`);
  else if (ev.intent) chips.push(`<span class="chip">${ev.intent}</span>`);
  if (known && ev.identity.confidence != null)
    chips.push(`<span class="chip">match ${Number(ev.identity.confidence).toFixed(2)}</span>`);
  if (ev.visitor_count > 0) chips.push(`<span class="chip">${ev.visitor_count} 👤</span>`);
  // Repeat unknown visitor (Phase 9, re-ID): loud badge on the 2nd+ sighting.
  if (!known && !spoof && ev.reid_seen_count > 1)
    chips.push(`<span class="chip repeat-badge">🔁 Seen ${ev.reid_seen_count}× today</span>`);
  if (ev.language_detected) chips.push(`<span class="chip">${ev.language_detected}</span>`);

  // Detected objects (Phase 3): label + confidence, compact.
  const objs = ev.detected_objects || [];
  const objHtml = objs.length
    ? `<div class="objects">${objs
        .map((o) => `<span class="obj">${o.label} ${Number(o.confidence).toFixed(2)}</span>`)
        .join(" ")}</div>`
    : "";

  // Scene description + label text (Phase 6, VLM). Shown as their own lines so
  // they're legible in Deaf mode even though they're also folded into the
  // spoken announcement.
  const scene = (ev.scene_summary || "").trim();
  const ocr = (ev.ocr_text || "").trim();
  const sceneHtml = scene ? `<div class="scene">👁 ${scene}</div>` : "";
  const ocrHtml = ocr
    ? `<div class="ocr">🏷 Label reads: <span>${ocr}</span></div>` : "";

  // Visitor's spoken words (Phase 7) + translation (Phase 8). When a translation
  // is present, show BOTH the original (with detected language) and the
  // translated line (in the user's language); otherwise just the original.
  const said = (ev.speech_transcript || "").trim();
  const lang = (ev.language_detected || "").trim();
  const translated = (ev.translated_transcript || "").trim();
  let saidHtml = "";
  if (said) {
    saidHtml = `<div class="said">🗣 Visitor said${lang ? ` (${lang})` : ""}: <span>${said}</span>`;
    if (translated) {
      saidHtml += `<div class="translated">🌐 Translated${
        userLanguage ? ` (${userLanguage})` : ""}: <span>${translated}</span></div>`;
    }
    saidHtml += `</div>`;
  }

  currentEl.innerHTML = `
    <div class="headline">${who}</div>
    <div>${chips.join(" ")}</div>
    <div class="meta">${fmtTime(ev.timestamp)}</div>
    ${peopleHtml}
    ${descrHtml}
    ${appearanceHtml}
    ${exprHtml}
    ${carrying ? `<div class="meta">Carrying: ${carrying}</div>` : ""}
    ${objHtml}
    ${sceneHtml}
    ${ocrHtml}
    ${saidHtml}
    <div class="announcement">${ev.announcement_text || ""}</div>
  `;

  deafAlert(ev);   // Deaf/both: per-kind flash + vibrate, escalates until ack
}

// Deaf Mode (and "both"): the visitor can't hear the spoken announcement, so
// draw attention visually - flash + vibrate, ESCALATING until acknowledged.
function applyMode(mode) {
  currentMode = mode;
  const visual = mode === "deaf" || mode === "both";
  document.body.classList.toggle("deaf", visual);   // drives big-text CSS
}

/* --- Phase 17: per-kind alert channels + escalation until acknowledged ----
 * One alert_kind string (from the server) keys every channel so a deaf or
 * deafblind user can classify the visitor without reading:
 *   vibration patterns  - distinct rhythm per kind (spoof = long SOS-ish)
 *   flash               - animated card flash, or a persistent high-contrast
 *                         banner under prefers-reduced-motion (the animated
 *                         flash is disabled there, but the alert channel must
 *                         survive - a static banner isn't motion)
 *   OS notification     - when the tab is hidden (Notification API)
 * The alert repeats every 6s until the user acknowledges by clicking/keying
 * anywhere or focusing the tab, so one missed flash isn't a missed visitor. */
const VIBRATIONS = {
  known:    [150, 80, 300],                        // short-long "ta-da"
  delivery: [120, 90, 120, 90, 120],               // three even taps
  unknown:  [400],                                 // one plain buzz
  spoof:    [600, 200, 600, 200, 600],             // three long - warning
};
const KIND_LABEL = {
  known: "Known visitor", delivery: "Likely delivery",
  unknown: "Unknown visitor", spoof: "⚠ Possible spoof (photo shown)",
};
const REDUCED_MOTION =
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;
let escalateTimer = null;
let escalateCount = 0;

function alertKindOf(ev) {
  return ev.alert_kind ||
    (ev.is_spoof ? "spoof"
     : (ev.identity && ev.identity.known) ? "known"
     : /delivery/.test(ev.intent || "") ? "delivery" : "unknown");
}

function deafAlert(ev) {
  if (currentMode !== "deaf" && currentMode !== "both") return;
  const kind = alertKindOf(ev || {});
  fireAlertOnce(kind, ev);
  // Escalate: re-flash + re-buzz every 6s (max 5 rounds) until acknowledged.
  stopEscalation();
  escalateCount = 0;
  escalateTimer = setInterval(() => {
    if (++escalateCount >= 5) { stopEscalation(); return; }
    fireAlertOnce(kind, ev);
  }, 6000);
}

function fireAlertOnce(kind, ev) {
  if (REDUCED_MOTION) {
    // Non-animated fallback: a persistent inverted banner (appears instantly,
    // no motion) instead of the flash animation the media query disables.
    showAlertBanner(kind, ev);
  } else {
    currentEl.classList.remove("flash");
    void currentEl.offsetWidth;            // reflow so the animation restarts
    currentEl.classList.add("flash");
    showAlertBanner(kind, ev);             // banner doubles as the ack surface
  }
  if (navigator.vibrate) {
    try { navigator.vibrate(VIBRATIONS[kind] || VIBRATIONS.unknown); } catch {}
  }
  notifyIfHidden(kind, ev);
}

function showAlertBanner(kind, ev) {
  let b = el("alert-banner");
  if (!b) {
    b = document.createElement("div");
    b.id = "alert-banner";
    b.setAttribute("role", "alert");
    b.addEventListener("click", ackAlert);
    document.body.appendChild(b);
  }
  b.dataset.kind = kind;
  b.innerHTML = `<span class="ab-kind">${KIND_LABEL[kind] || kind}</span>
    <span class="ab-text">${(ev && ev.announcement_text) || "Someone is at the door."}</span>
    <button class="ab-ok" aria-label="Acknowledge alert">OK</button>`;
  b.hidden = false;
}

function ackAlert() {
  stopEscalation();
  const b = el("alert-banner");
  if (b) b.hidden = true;
}

function stopEscalation() {
  if (escalateTimer) { clearInterval(escalateTimer); escalateTimer = null; }
}

// Any interaction or returning to the tab acknowledges the alert.
["click", "keydown"].forEach((evName) =>
  document.addEventListener(evName, () => { if (escalateTimer) ackAlert(); }));
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && escalateTimer) ackAlert();
});

/* OS notification when the dashboard tab is hidden - the doorbell must reach
 * a user who is in another tab. Permission is requested lazily on the first
 * hidden-tab alert (browsers require a prior user gesture for auto-grant, so
 * the earlier Ring/mode clicks usually satisfy it). */
function notifyIfHidden(kind, ev) {
  if (!document.hidden || !("Notification" in window)) return;
  const fire = () => {
    try {
      const n = new Notification(`AccessAI — ${KIND_LABEL[kind] || kind}`, {
        body: (ev && ev.announcement_text) || "Someone is at the door.",
        tag: "accessai-doorbell",       // replaces, never stacks
        requireInteraction: kind === "spoof",
      });
      n.onclick = () => { window.focus(); ackAlert(); n.close(); };
    } catch {}
  };
  if (Notification.permission === "granted") fire();
  else if (Notification.permission !== "denied") {
    Notification.requestPermission().then((p) => { if (p === "granted") fire(); });
  }
}

function fmtTime(ts) {
  const d = new Date(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleString();
}

async function refreshHistory() {
  try {
    const q = (el("history-search")?.value || "").trim();
    const url = "/history?limit=30" + (q ? `&q=${encodeURIComponent(q)}` : "");
    const r = await fetch(url);
    const rows = await r.json();
    historyList.innerHTML = "";
    if (q && rows.length === 0) {
      historyList.innerHTML =
        `<li class="h-empty">No visits match “${q.replace(/</g, "&lt;")}”.</li>`;
      return;
    }
    for (const ev of rows) {
      const li = document.createElement("li");
      const img = `<img src="/snapshot/${ev.event_id}" alt="snapshot" onerror="this.style.visibility='hidden'">`;
      const hKnown = ev.identity && ev.identity.known;
      const who = ev.is_spoof
        ? "⚠ Possible spoof"
        : hKnown ? ev.identity.name : "Unknown";
      if (ev.is_spoof) li.classList.add("spoof");
      // Phase 12: approximate age/gender + appearance, UNKNOWN visitors only.
      let hDescr = "";
      if (!hKnown && !ev.is_spoof) {
        const bits = [];
        if (ev.gender === "man" || ev.gender === "woman") bits.push(ev.gender);
        const ar = ageRange(ev.age);
        if (ar) bits.push(ar);
        if (bits.length) hDescr = `<div class="h-meta">🧍 ${bits.join(", ")}</div>`;
      }
      const hPeople = renderPeopleCompact(ev);
      // Phase 16: appearance line shown for known singles too (not for the
      // multi-person rows, which already summarise everyone in hPeople).
      const hAppear = (ev.appearance && !hPeople)
        ? `<div class="h-meta">👕 ${ev.appearance}</div>` : "";
      const hTitle = hPeople ? "Multiple people" : who;
      li.innerHTML = `
        ${img}
        <div>
          <div class="h-title">${hTitle} <span class="chip">${ev.intent || ""}</span></div>
          <div class="h-meta">${fmtTime(ev.timestamp)}</div>
          ${hPeople}
          ${hPeople ? "" : hDescr}
          ${hAppear}
          <div class="h-meta">${ev.announcement_text || ""}</div>
        </div>
        <button class="h-del" title="Delete this event" aria-label="Delete">✕</button>`;
      const delBtn = li.querySelector(".h-del");
      if (delBtn) delBtn.addEventListener("click", () => deleteEvent(ev.event_id));
      historyList.appendChild(li);
    }
  } catch (e) {
    console.warn("history refresh failed", e);
  }
}

// Delete a single visit event (row + snapshot). The server broadcasts
// history_update, but we also refresh locally for immediate feedback.
async function deleteEvent(eventId) {
  if (!eventId) return;
  try {
    const r = await fetch(`/event/${encodeURIComponent(eventId)}/delete`,
                          { method: "POST" });
    if (!r.ok && r.status !== 404) console.warn("delete failed", r.status);
  } catch (e) {
    console.warn("delete event error", e);
  } finally {
    refreshHistory();
  }
}

// Clear the entire visit history (events + snapshots). Known people and the
// re-ID "repeat visitor" memory are NOT affected.
async function clearHistory() {
  if (!confirm("Clear ALL visit history? This deletes every event and its "
             + "saved photo. Known people are kept.")) return;
  try {
    const r = await fetch("/history/clear", { method: "POST" });
    const j = await r.json().catch(() => ({}));
    console.log(`history cleared: ${j.cleared ?? "?"} event(s)`);
  } catch (e) {
    console.warn("clear history error", e);
  } finally {
    refreshHistory();
  }
}

async function ring() {
  ringBtn.disabled = true;
  // Phase 12: the doorbell is VISUAL-ONLY and fast - it records NO audio, so
  // there is deliberately NO "listening" indicator here (that would misrepresent
  // what the button does). Two-way audio is the separate "Hear Visitor" button.
  const hint = el("ring-hint");
  if (hint) hint.textContent = "";
  try {
    const r = await fetch("/trigger", { method: "POST" });
    if (r.ok) {
      const ev = await r.json();
      renderEvent(ev);
      refreshHistory();
    } else {
      alert("Trigger failed: " + r.status);
    }
  } catch (e) {
    alert("Trigger error: " + e);
  } finally {
    ringBtn.disabled = false;
  }
}

// Phase 12: OPT-IN two-way audio. Records the VISITOR for a few seconds, then
// transcribes + translates and attaches the result to the latest event. This is
// the ONLY path that captures visitor audio; the doorbell never does.
async function hearVisitor() {
  const btn = el("hear-btn");
  const hint = el("listen-hint");
  if (btn) btn.disabled = true;
  if (hint) hint.textContent = "🎤 Listening to visitor… (6s)";
  try {
    const r = await fetch("/hear_visitor", { method: "POST" });
    const j = await r.json().catch(() => ({}));
    if (r.ok) {
      const said = (j.transcript || "").trim();
      if (said) {
        hint.textContent = j.translated
          ? `🗣 “${said}” → 🌐 “${j.translated}”`
          : `🗣 “${said}”`;
      } else {
        hint.textContent = "No speech detected.";
      }
      refreshHistory();
    } else {
      hint.textContent = j.detail || `Hear Visitor failed: ${r.status}`;
    }
  } catch (e) {
    if (hint) hint.textContent = "Hear Visitor error: " + e;
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Optional `preset` sends a canned quick-reply without touching the input.
async function reply(preset) {
  const text = (preset != null ? preset : replyInput.value).trim();
  if (!text) return;
  replyNote.textContent = "Sending…";
  try {
    const r = await fetch("/reply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const j = await r.json().catch(() => ({}));
    if (r.ok) {
      replyNote.textContent = j.spoken
        ? "🔊 Spoken at the door."
        : `TTS unavailable (${j.engine || "none"}) — reply shown as text only.`;
      if (preset == null) replyInput.value = "";
    } else {
      replyNote.textContent = j.detail || "Reply failed: " + r.status;
    }
  } catch (e) {
    replyNote.textContent = "Reply error: " + e;
  }
}

// Phase 17: canned quick replies — one-tap sentences for a deaf user in a hurry.
async function loadQuickReplies() {
  const box = el("quick-replies");
  if (!box) return;
  try {
    const r = await fetch("/quick_replies");
    if (!r.ok) return;
    const j = await r.json();
    box.innerHTML = "";
    for (const text of (j.replies || [])) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip-reply";
      b.textContent = text;
      b.addEventListener("click", () => reply(text));
      box.appendChild(b);
    }
  } catch {}
}

// Phase 17: live captions — toggle the server loop and stream lines in.
const captionsBtn = el("captions-btn");
const captionsLog = el("captions-log");
function setCaptionsUi(on) {
  if (!captionsBtn) return;
  captionsBtn.setAttribute("aria-pressed", on ? "true" : "false");
  captionsBtn.textContent = on ? "■ Stop captions" : "▶ Start captions";
  captionsBtn.classList.toggle("active", on);
}
function appendCaption(msg) {
  if (!captionsLog) return;
  const said = (msg.text || "").trim();
  if (!said) return;
  const line = document.createElement("div");
  line.className = "caption-line";
  line.textContent = msg.translated ? `${said} → ${msg.translated}` : said;
  captionsLog.appendChild(line);
  while (captionsLog.childElementCount > 12) {
    captionsLog.removeChild(captionsLog.firstChild);
  }
  captionsLog.scrollTop = captionsLog.scrollHeight;
}
async function toggleCaptions() {
  const on = captionsBtn.getAttribute("aria-pressed") === "true";
  const action = on ? "off" : "on";
  try {
    const r = await fetch(`/captions/${action}`, { method: "POST" });
    const j = await r.json().catch(() => ({}));
    if (r.ok) setCaptionsUi(!!j.on);
    else replyNote.textContent = j.detail || `Captions failed: ${r.status}`;
  } catch (e) {
    replyNote.textContent = "Captions error: " + e;
  }
}
if (captionsBtn) captionsBtn.addEventListener("click", toggleCaptions);
loadQuickReplies();

// Small HTML escaper for user-supplied names rendered into markup (Phase 13).
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function refreshKnown() {
  try {
    const r = await fetch("/known");
    const j = await r.json();
    const people = j.people || [];
    knownList.textContent = people.length
      ? people.map((p) => `${p.name} (${p.photos ?? p.count ?? 0})`).join(", ")
      : "none yet";
    renderKnownPeople(people);
  } catch (e) {
    knownList.textContent = "unavailable";
  }
}

// Phase 13: render the "Known People" list - thumbnail + name + photo count +
// a Delete button. People with no saved photo (e.g. auto-enrolled) get a glyph.
function renderKnownPeople(people) {
  const box = el("known-people");
  if (!box) return;
  if (!people.length) {
    box.innerHTML = `<li class="muted small">No known people yet.</li>`;
    return;
  }
  box.innerHTML = people
    .map((p) => {
      const n = p.photos ?? p.count ?? 0;
      // Cache-bust so a re-added person's thumbnail refreshes.
      const thumb =
        n > 0
          ? `<img class="kp-thumb" src="/known_photo/${encodeURIComponent(
              p.name
            )}?t=${Date.now()}" alt="" />`
          : `<span class="kp-thumb kp-noimg">👤</span>`;
      return `<li class="kp-row">
        ${thumb}
        <span class="kp-name">${esc(p.name)}</span>
        <span class="kp-count muted small">${n} photo${n === 1 ? "" : "s"}</span>
        <button class="kp-del ghost" data-name="${esc(p.name)}">Delete</button>
      </li>`;
    })
    .join("");
  box.querySelectorAll(".kp-del").forEach((b) =>
    b.addEventListener("click", () => deletePerson(b.dataset.name))
  );
}

// Phase 13: upload one or more photos + a name -> POST /enroll_upload (FormData).
async function uploadEnroll() {
  const name = (upName.value || "").trim();
  const files = upFiles.files;
  if (!name) { upNote.textContent = "Enter a name first."; return; }
  if (!files || !files.length) { upNote.textContent = "Choose at least one photo."; return; }
  const fd = new FormData();
  fd.append("name", name);
  for (const f of files) fd.append("files", f);
  upBtn.disabled = true;
  upNote.textContent = `Uploading ${files.length} photo(s)…`;
  try {
    const r = await fetch("/enroll_upload", { method: "POST", body: fd });
    const j = await r.json().catch(() => ({}));
    if (r.ok) {
      let msg = `Added ${j.added ?? 0} photo(s) for ${name}` +
        ` (${j.photos_for_name ?? 0} on file).`;
      const notes = [...(j.warnings || []), ...(j.skipped || [])];
      if (notes.length) msg += " " + notes.join("; ") + ".";
      upNote.textContent = msg;
      if ((j.added ?? 0) > 0) { upName.value = ""; upFiles.value = ""; clearPreviews(); }
      refreshKnown();
    } else {
      upNote.textContent = j.detail || `Upload failed: ${r.status}`;
    }
  } catch (e) {
    upNote.textContent = "Upload error: " + e;
  } finally {
    upBtn.disabled = false;
  }
}

async function deletePerson(name) {
  if (!name) return;
  if (!confirm(`Delete "${name}" and all their saved photos?`)) return;
  try {
    const r = await fetch("/known/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    await r.json().catch(() => ({}));
    refreshKnown();
  } catch (e) {
    /* fail-soft: the list simply won't change */
  }
}

// Client-side previews of the selected files (before upload).
function clearPreviews() {
  const box = el("up-previews");
  if (box) box.innerHTML = "";
}
function showPreviews() {
  const box = el("up-previews");
  if (!box) return;
  box.innerHTML = "";
  const files = upFiles.files;
  if (!files) return;
  [...files].slice(0, 8).forEach((f) => {
    const rd = new FileReader();
    rd.onload = (e) => {
      const img = document.createElement("img");
      img.className = "up-thumb";
      img.src = e.target.result;
      box.appendChild(img);
    };
    rd.readAsDataURL(f);
  });
}

async function enroll() {
  const name = enrollName.value.trim();
  if (!name) { enrollNote.textContent = "Enter a name first."; return; }
  enrollBtn.disabled = true;
  enrollNote.textContent = "Enrolling…";
  try {
    const r = await fetch("/enroll", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const j = await r.json().catch(() => ({}));
    if (r.ok) {
      enrollNote.textContent = j.message || (j.ok ? "Enrolled." : "Failed.");
      if (j.ok) enrollName.value = "";
      refreshKnown();
    } else {
      enrollNote.textContent = j.detail || `Enroll failed: ${r.status}`;
    }
  } catch (e) {
    enrollNote.textContent = "Enroll error: " + e;
  } finally {
    enrollBtn.disabled = false;
  }
}

async function refreshVlmStatus() {
  const pill = el("vlm-status");
  if (!pill) return;
  try {
    const r = await fetch("/vlm_status");
    const j = await r.json();
    if (j.enabled && j.available) {
      pill.textContent = `vision ✓ ${j.model}`;
      pill.className = "status online";
      pill.title = `Cloud VLM live — ${j.key_count} key(s), unknown-only=${j.only_for_unknown}`;
    } else if (j.enabled) {
      pill.textContent = "vision (YOLO-only)";
      pill.className = "status offline";
      pill.title = j.reason || "No usable API keys — running on YOLO signals only.";
    } else {
      pill.textContent = "vision off";
      pill.className = "status";
      pill.title = "ENABLE_VLM is off.";
    }
  } catch (e) {
    pill.textContent = "vision ?";
    pill.className = "status";
  }
}

async function refreshSpeechStatus() {
  const pill = el("speech-status");
  if (!pill) return;
  try {
    const r = await fetch("/speech_status");
    const j = await r.json();
    if (j.enabled && j.available) {
      const mic = j.mic ? "mic✓" : "mic✗";
      const vad = j.silero ? "vad✓" : "vad~";   // ~ = energy fallback VAD
      pill.textContent = `speech ✓ ${j.model} (${mic} ${vad})`;
      pill.className = "status online";
      pill.title = `Whisper '${j.model}' ready. ${j.mic ? "Live mic capture on." :
        "No mic — use the WAV upload / Transcribe route."} ${
        j.silero ? "Silero VAD." : "Energy-threshold VAD (silero not loaded)."}`;
    } else if (j.enabled) {
      pill.textContent = "speech (no whisper)";
      pill.className = "status offline";
      pill.title = "openai-whisper not installed — speech disabled, rest works.";
    } else {
      pill.textContent = "speech off";
      pill.className = "status";
      pill.title = "ENABLE_SPEECH is off.";
    }
  } catch (e) {
    pill.textContent = "speech ?";
    pill.className = "status";
  }
}

async function refreshTranslateStatus() {
  const pill = el("translate-status");
  const sel = el("lang-select");
  try {
    const r = await fetch("/translate_status");
    const j = await r.json();
    userLanguage = j.user_language || "en";
    if (sel && sel.value !== userLanguage) sel.value = userLanguage;
    if (!pill) return;
    if (j.enabled && j.available) {
      pill.textContent = `translate ✓ ${j.backend} → ${userLanguage}`;
      pill.className = "status online";
      pill.title = `Translating visitor speech into ${j.user_language_name || userLanguage} via '${j.backend}'.`;
    } else if (j.enabled) {
      pill.textContent = `translate (passthrough → ${userLanguage})`;
      pill.className = "status offline";
      pill.title = "No translation backend available — showing the original transcript.";
    } else {
      pill.textContent = "translate off";
      pill.className = "status";
      pill.title = "ENABLE_TRANSLATE is off.";
    }
  } catch (e) {
    if (pill) { pill.textContent = "translate ?"; pill.className = "status"; }
  }
}

async function setUserLanguage(lang) {
  try {
    const r = await fetch("/user_language", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lang }),
    });
    if (r.ok) {
      const j = await r.json();
      userLanguage = j.user_language || lang;
      refreshTranslateStatus();
    }
  } catch (e) {
    console.warn("set user language failed", e);
  }
}

async function refreshReidStatus() {
  const pill = el("reid-status");
  if (!pill) return;
  try {
    const r = await fetch("/reid_status");
    const j = await r.json();
    if (j.enabled && j.available) {
      const tag = j.placeholder ? "histogram" : j.backend;
      pill.textContent = `re-ID ✓ ${tag} (${j.gallery_size})`;
      pill.className = "status online";
      pill.title = `Repeat-visitor memory via '${j.backend}', ${j.gallery_size} in 24h gallery.${
        j.placeholder ? " Placeholder backend (clothing-colour histogram)." : ""}`;
    } else if (j.enabled) {
      pill.textContent = "re-ID (off)";
      pill.className = "status offline";
      pill.title = j.reason || "Re-ID unavailable.";
    } else {
      pill.textContent = "re-ID off";
      pill.className = "status";
      pill.title = "ENABLE_REID is off.";
    }
  } catch (e) {
    pill.textContent = "re-ID ?";
    pill.className = "status";
  }
}

// Auto-enrollment (Phase 9): show open "save this visitor?" prompts. Each prompt
// carries a sample snapshot, a name input, and Save / Dismiss actions.
async function refreshSuggestions() {
  const box = el("suggest-box");
  const list = el("suggest-list");
  if (!box || !list) return;
  try {
    const r = await fetch("/suggestions");
    const j = await r.json();
    const items = j.suggestions || [];
    if (!items.length) {
      box.style.display = "none";
      list.innerHTML = "";
      return;
    }
    box.style.display = "";
    list.innerHTML = "";
    for (const s of items) {
      const li = document.createElement("li");
      const img = `<img src="/snapshot/${s.sample_event_id}" alt="visitor" onerror="this.style.visibility='hidden'">`;
      li.innerHTML = `
        ${img}
        <div class="suggest-body">
          <div class="h-meta">Seen ${s.size}× — save as a known face?</div>
          <div class="reply-row">
            <input type="text" placeholder="Name…" data-cid="${s.cluster_id}" class="suggest-name" />
            <button class="suggest-save" data-cid="${s.cluster_id}">Save</button>
            <button class="ghost suggest-dismiss" data-cid="${s.cluster_id}">Dismiss</button>
          </div>
        </div>`;
      list.appendChild(li);
    }
    list.querySelectorAll(".suggest-save").forEach((b) =>
      b.addEventListener("click", () => confirmSuggestion(b.dataset.cid)));
    list.querySelectorAll(".suggest-dismiss").forEach((b) =>
      b.addEventListener("click", () => dismissSuggestion(b.dataset.cid)));
  } catch (e) {
    console.warn("suggestions refresh failed", e);
  }
}

async function confirmSuggestion(cid) {
  const input = document.querySelector(`.suggest-name[data-cid="${cid}"]`);
  const name = (input && input.value.trim()) || "";
  if (!name) { if (input) input.focus(); return; }
  try {
    const r = await fetch("/suggestions/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cluster_id: cid, name }),
    });
    if (r.ok) { refreshSuggestions(); refreshKnown(); }
  } catch (e) { console.warn("confirm failed", e); }
}

async function dismissSuggestion(cid) {
  try {
    const r = await fetch("/suggestions/dismiss", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cluster_id: cid }),
    });
    if (r.ok) refreshSuggestions();
  } catch (e) { console.warn("dismiss failed", e); }
}

// --- Voice commands (Phase 10) -------------------------------------------
// Push-to-talk: POST /listen records one command, the server parses + acts +
// speaks the answer, and returns what it heard. We show both here.
async function speakCommand() {
  const btn = el("voice-btn");
  const note = el("voice-note");
  if (btn) btn.disabled = true;
  if (note) note.textContent = "🎙 Listening for your command…";
  try {
    const r = await fetch("/listen", { method: "POST" });
    const j = await r.json().catch(() => ({}));
    if (r.ok) {
      renderVoice(j);
    } else {
      if (note) note.textContent = j.detail || `Voice failed: ${r.status}`;
    }
  } catch (e) {
    if (note) note.textContent = "Voice error: " + e;
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Render the outcome of a voice interaction (from /listen or a wake broadcast).
function renderVoice(v) {
  const note = el("voice-note");
  if (!v) return;
  const heard = v.command ? `“${v.command}”` : "(nothing heard)";
  const spoke = v.spoke ? "🔊 spoken" : "(TTS off — text only)";
  if (note) note.textContent = `Heard ${heard} → ${v.answer || ""} ${spoke}`;
  // A voice "open camera" command scrolls the live view into focus.
  if (v.intent === "open_camera") {
    const live = document.querySelector(".panel.live");
    if (live) live.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

async function refreshWakeStatus() {
  const pill = el("wake-status");
  const toggle = el("wake-toggle");
  const word = el("wake-word");
  try {
    const r = await fetch("/wakeword_status");
    const j = await r.json();
    if (word && j.model && j.model !== "none") word.textContent = j.model.replace(/_/g, " ");
    if (toggle) toggle.checked = !!j.running;
    if (!pill) return;
    if (j.available) {
      pill.textContent = j.running ? "voice ✓ listening" : "voice ✓ push-to-talk";
      pill.className = "status online";
      pill.title = `Wake word '${j.model}' available${
        j.placeholder ? " (placeholder phrase)" : " (custom model)"}.${
        j.running ? " Always-on mic is ON." : " Always-on is off (opt-in)."}`;
    } else {
      pill.textContent = "voice (push-to-talk)";
      pill.className = "status offline";
      pill.title = j.reason || "Always-on wake word unavailable; /listen still works.";
      if (toggle) toggle.disabled = true;
    }
  } catch (e) {
    if (pill) { pill.textContent = "voice ?"; pill.className = "status"; }
  }
}

async function toggleWake(on) {
  const note = el("voice-note");
  try {
    const r = await fetch(`/wakeword/${on ? "on" : "off"}`, { method: "POST" });
    const j = await r.json().catch(() => ({}));
    if (!r.ok && note) note.textContent = j.detail || "Wake toggle failed.";
  } catch (e) {
    if (note) note.textContent = "Wake toggle error: " + e;
  } finally {
    refreshWakeStatus();
  }
}

// --- Natural voice picker (Phase 11) -------------------------------------
// Populate the Voice dropdown from /voices, marking offline (Kokoro) vs online
// (edge) and which are actually usable. Selecting one POSTs /voice; the server
// switches the voice and speaks a sample so the user hears the change.
async function refreshVoices() {
  const sel = el("voice-select");
  if (!sel) return;
  try {
    const r = await fetch("/voices");
    const j = await r.json();
    const voices = j.voices || [];
    sel.innerHTML = "";
    if (!voices.length) {
      const o = document.createElement("option");
      o.value = ""; o.textContent = "(no voices)";
      sel.appendChild(o);
      sel.disabled = true;
      return;
    }
    sel.disabled = false;
    for (const v of voices) {
      const o = document.createElement("option");
      o.value = v.id;
      o.textContent = v.available ? v.label : `${v.label} (unavailable)`;
      o.disabled = !v.available;
      sel.appendChild(o);
    }
    if (j.current && j.current !== "none") sel.value = j.current;
  } catch (e) {
    console.warn("voices refresh failed", e);
  }
}

async function setVoice(id) {
  const note = el("voice-note");
  if (!id) return;
  if (note) note.textContent = "Switching voice…";
  try {
    const r = await fetch("/voice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    const j = await r.json().catch(() => ({}));
    if (note) {
      note.textContent = j.ok
        ? `Voice: ${j.voice}. ${j.spoke ? "🔊 Sample spoken." : "(TTS off — no sample)"}`
        : (j.message || j.detail || "Voice change failed.");
    }
    // If the switch was declined (e.g. edge offline), snap back to the active one.
    if (!j.ok) refreshVoices();
    refreshHealth();
  } catch (e) {
    if (note) note.textContent = "Voice change error: " + e;
  }
}

// Preview the current voice without a real doorbell event.
async function testVoice() {
  const btn = el("test-voice-btn");
  const note = el("voice-note");
  if (btn) btn.disabled = true;
  if (note) note.textContent = "🔊 Speaking a sample…";
  try {
    const r = await fetch("/reply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: "Hello, this is your AccessAI doorbell speaking." }),
    });
    const j = await r.json().catch(() => ({}));
    if (note) {
      note.textContent = j.spoken
        ? `🔊 Sample spoken with ${j.engine}.`
        : `TTS unavailable (${j.engine || "none"}) — sample shown as text only.`;
    }
  } catch (e) {
    if (note) note.textContent = "Test voice error: " + e;
  } finally {
    if (btn) btn.disabled = false;
  }
}

// --- System health panel (Phase 10) --------------------------------------
async function refreshHealth() {
  const list = el("health-list");
  const meta = el("health-meta");
  if (!list) return;
  try {
    const r = await fetch("/status");
    const j = await r.json();
    if (meta) {
      const tv = j.tts || {};
      const voiceStr = tv.voice && tv.voice !== "none"
        ? `${tv.voice}${tv.fellback_to_pyttsx3 ? " ⚠ robotic fallback" : ""}`
        : j.voice_path;
      meta.textContent = `mode: ${j.mode} · voice: ${voiceStr} · path: ${
        j.voice_path} · torch: ${j.torch_version} · phase ${j.phase}`;
    }
    list.innerHTML = "";
    for (const m of j.modules || []) {
      const li = document.createElement("li");
      const dot = `<span class="dot ${m.state}"></span>`;
      const label = m.placeholder && m.state === "placeholder"
        ? `${m.name} <span class="chip">placeholder</span>`
        : m.name;
      li.innerHTML = `${dot}<span class="h-name">${label}</span>
        <span class="h-state ${m.state}">${m.state}</span>
        <span class="h-detail">${m.detail || ""}</span>`;
      list.appendChild(li);
    }
  } catch (e) {
    if (meta) meta.textContent = "health unavailable";
  }
}

async function loadMode() {
  try {
    const r = await fetch("/mode");
    const j = await r.json();
    modeSel.value = j.mode;
    applyMode(j.mode);
  } catch (e) {
    console.warn("mode load failed", e);
  }
}

async function setMode(v) {
  await fetch("/mode", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: v }),
  });
  applyMode(v);
}

function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(tokenQS(`${proto}://${location.host}/events`));
  ws.onopen = () => setStatus(true);
  ws.onclose = () => { setStatus(false); setTimeout(connectWS, 2000); };
  ws.onerror = () => setStatus(false);
  ws.onmessage = (m) => {
    try {
      const msg = JSON.parse(m.data);
      if (msg.type === "event" && msg.event) {
        renderEvent(msg.event);
        refreshHistory();
        refreshReidStatus();       // Phase 9: gallery may have grown
        refreshSuggestions();      // Phase 9: a new cluster may have crossed the threshold
      } else if (msg.type === "event_update" && msg.event) {
        // Phase 12: the background VLM enrich finished - re-render the (still
        // current) unknown visitor with clothing/appearance + refreshed text.
        renderEvent(msg.event);
        refreshHistory();
      } else if (msg.type === "visitor_speech") {
        // Phase 12: two-way audio result pushed from /hear_visitor.
        const hint = el("listen-hint");
        const said = (msg.text || "").trim();
        if (hint && said) {
          hint.textContent = msg.translated
            ? `🗣 “${said}” → 🌐 “${msg.translated}”`
            : `🗣 “${said}”`;
        }
        refreshHistory();
      } else if (msg.type === "known_update") {
        refreshKnown();            // Phase 13: enroll/delete on another tab
      } else if (msg.type === "history_update") {
        refreshHistory();          // a delete/clear happened (maybe another tab)
      } else if (msg.type === "suggestions_update") {
        refreshSuggestions();      // Phase 9: confirm/dismiss on another tab
      } else if (msg.type === "voice") {
        renderVoice(msg);          // Phase 10: a wake-word command was handled
        refreshHistory();          // who_is_there/analyze create an event
      } else if (msg.type === "caption") {
        appendCaption(msg);        // Phase 17: rolling live-caption line
      } else if (msg.type === "caption_state") {
        setCaptionsUi(!!msg.on);   // loop started/stopped (maybe by a timeout)
      }
    } catch {}
  };
}

ringBtn.addEventListener("click", ring);
const hearBtn = el("hear-btn");
if (hearBtn) hearBtn.addEventListener("click", hearVisitor);
replyBtn.addEventListener("click", reply);
refreshBtn.addEventListener("click", refreshHistory);
const clearHistBtn = el("clear-history");
if (clearHistBtn) clearHistBtn.addEventListener("click", clearHistory);
// History search: debounced live filter (server-side ?q=).
const histSearch = el("history-search");
if (histSearch) {
  let searchTimer = null;
  histSearch.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(refreshHistory, 250);
  });
  histSearch.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { clearTimeout(searchTimer); refreshHistory(); }
  });
}
modeSel.addEventListener("change", (e) => setMode(e.target.value));
replyInput.addEventListener("keydown", (e) => { if (e.key === "Enter") reply(); });
enrollBtn.addEventListener("click", enroll);
enrollName.addEventListener("keydown", (e) => { if (e.key === "Enter") enroll(); });
// Phase 13: upload-photo enrollment wiring.
if (upBtn) upBtn.addEventListener("click", uploadEnroll);
if (upFiles) upFiles.addEventListener("change", showPreviews);
if (upName) upName.addEventListener("keydown", (e) => { if (e.key === "Enter") uploadEnroll(); });
const langSel = el("lang-select");
if (langSel) langSel.addEventListener("change", (e) => setUserLanguage(e.target.value));
const voiceBtn = el("voice-btn");
if (voiceBtn) voiceBtn.addEventListener("click", speakCommand);
const wakeToggle = el("wake-toggle");
if (wakeToggle) wakeToggle.addEventListener("change", (e) => toggleWake(e.target.checked));
const healthRefresh = el("health-refresh");
if (healthRefresh) healthRefresh.addEventListener("click", refreshHealth);
const voiceSelect = el("voice-select");
if (voiceSelect) voiceSelect.addEventListener("change", (e) => setVoice(e.target.value));
const testVoiceBtn = el("test-voice-btn");
if (testVoiceBtn) testVoiceBtn.addEventListener("click", testVoice);

loadMode();
refreshHistory();
refreshKnown();
refreshVlmStatus();
refreshSpeechStatus();
refreshTranslateStatus();
refreshReidStatus();       // Phase 9
refreshSuggestions();      // Phase 9
refreshWakeStatus();       // Phase 10
refreshHealth();           // Phase 10
refreshVoices();           // Phase 11: populate the natural-voice picker
connectWS();
// Phase 17: the MJPEG <img> can't send an Authorization header - append the
// token to its URL when one is stored. (No-op with auth off.)
if (authToken) {
  const vid = document.getElementById("video");
  if (vid) vid.src = tokenQS("/video");
}
