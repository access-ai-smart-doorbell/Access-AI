# AccessAI — Complete Project Reference & Flutter Build Bible

> The single source of truth for the AccessAI project. Give this document to the
> Claude agent in VS Code **together with** `PHASE_16_PROMPT_FLUTTER_APP.md`. It
> contains the mission, the full architecture, every phase, every module, the
> complete data model, every API endpoint contract, how each feature works
> end-to-end, the configuration reference, the honest real-vs-placeholder status,
> and the full native-app (Flutter) specification — design system, screen-by-screen
> behaviour, accessibility, and motion. Read it fully before building.

---

# PART A — THE PRODUCT

## A1. One-paragraph mission

AccessAI is an AI-powered smart doorbell that restores independence, safety, and
dignity to people who are **blind** or **deaf**. An ordinary doorbell communicates
exactly one fact — "someone is here" — through a sound. For a deaf person that
sound is inaccessible; for a blind person it says nothing about **who** is outside
or **why**. AccessAI replaces the chime with **meaning**: it recognises known
visitors by face, describes unknown visitors (age, clothing, carried objects,
apparent mood), reads courier labels, transcribes and translates what the visitor
says, and delivers all of it through a **Blind Mode** (natural spoken announcements
+ vibration) and a **Deaf Mode** (large captions, visual/vibration alerts, and
two-way text↔speech conversation). Everything runs locally on a laptop for privacy;
the same software moves to a ₹2,700 ESP32-CAM door unit by changing one line.

## A2. The two users and their needs

**Blind / low-vision resident.** Cannot see a video feed or read a screen alert.
Needs: to *hear* who is at the door and why, in a natural human voice; to control
the system hands-free by voice; to be warned if a "known" face is actually a photo
(spoofing); to know if the same stranger keeps returning. The phone they hold must
speak to them.

**Deaf / hard-of-hearing resident.** May not perceive the chime at all, and cannot
hear the visitor or be easily understood at the door. Needs: a strong *visual +
vibration* alert; to *read* what the visitor said as a caption (with translation
if they speak another language); and to *reply* by typing text that is spoken aloud
at the door — closing the conversation loop an ordinary doorbell leaves open.

**Both.** The system supports a "both" mode that does spoken output AND visual
captions simultaneously.

## A3. Why it's novel (the defensible contribution)

Not any single model — the **accessibility-first integration**: a doorbell reframed
as an assistive device serving blind and deaf users from one platform; a multi-signal
**context engine** fusing face identity, liveness, objects, scene, speech, language,
and visit history into a single conservative "likely intent"; **two-way doorstep
communication** for deaf users; **local, private** operation (stores face embeddings,
not raw photos); and honest, uncertainty-aware language ("likely", never
"definitely"). No mainstream product (Ring, Nest, Amazon) occupies this space —
they assume a sighted, hearing user looking at a screen.

## A4. Design principles (these govern every decision)

1. **Accessibility is the product, not a feature.** Every output must reach the
   user through the sense they have. A beautiful UI a blind user can't navigate is
   a failure.
2. **Honesty about uncertainty.** The system says "likely a delivery", "appears to
   be in his thirties", "appears calm" — never fabricated certainty. This is a
   trust decision for a system a vulnerable user relies on for safety.
3. **Private by design.** Images/audio never leave the home; the face database
   stores embeddings, not photos; recording is trigger-based, not surveillance.
4. **Graceful degradation everywhere.** Any component that can't load logs a hint
   and returns empty — it never crashes the app. The base system runs even with
   every optional feature disabled.
5. **One data object is the spine.** The `VisitorEvent` flows through every module;
   every module *writes into it*, every output *reads from it*. Add a feature = add
   a field, not a rewrite.
6. **Torch is pinned and fragile.** The whole ML stack sits on torch 2.4.1; a
   careless install has silently broken YOLO before. Guard it.
7. **One-line hardware swap.** The camera lives behind one abstraction so the laptop
   webcam (`CAMERA_SOURCE = 0`) becomes an ESP32-CAM (`"http://<ip>:81/stream"`)
   with no other change.

---

# PART B — SYSTEM ARCHITECTURE

## B1. Three tiers

```
┌──────────────────────────────────────────────────────────────────────┐
│ TIER 1 — DOOR UNIT (dumb capture)                                      │
│  Now: laptop webcam + mic + speaker.                                   │
│  Later: ESP32-S3-CAM (camera + mic), speaker, PIR motion, button, LED. │
│  Job: capture a frame (+ audio only on the dedicated listen action),   │
│       stream MJPEG, and POST /ring on the physical button. NO AI here. │
└───────────────────────────────┬──────────────────────────────────────┘
                                 │  frame / MJPEG over Wi-Fi
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│ TIER 2 — AI SERVER (the laptop; later a Jetson/Pi5 or cloud)           │
│  FastAPI backend running the full perception + fusion pipeline and the │
│  accessibility engine. Holds the SQLite history + the face gallery.    │
│  Modules: Face → Anti-spoof → Objects(YOLO) → VLM(scene+OCR) →         │
│           Speech(Whisper+VAD) → Translate → Re-ID → Auto-enroll →      │
│           Context/Intent → Accessibility(TTS + routing).               │
└───────────────────────────────┬──────────────────────────────────────┘
                                 │  REST + WebSocket + MJPEG
                                 ▼
┌──────────────────────────────────────────────────────────────────────┐
│ TIER 3 — USER INTERFACE                                                │
│  Desktop dashboard (web/), installable PWA (web/app/), and the native  │
│  Flutter app (mobile/). All are THIN CLIENTS over the same API.        │
│  Blind Mode: spoken (natural Kokoro voice) + haptics + screen-reader.  │
│  Deaf Mode: captions + flash + vibration + two-way typed reply.        │
└──────────────────────────────────────────────────────────────────────┘
```

## B2. The end-to-end flow of one doorbell press

1. **Trigger.** The button (or the dashboard/app "Ring", or later a PIR + `/ring`
   webhook) fires. The doorbell path is **visual-only and fast — it records NO
   audio**. (Audio is captured only by the separate "Hear Visitor" action.)
2. **Capture.** The current camera frame is grabbed from the live stream holder.
3. **Perceive (in the pipeline, some steps in parallel):**
   - **Face detection + recognition** (InsightFace): find every face, embed each,
     match against the known-people gallery → name or "Unknown", plus per-face
     **age + gender**.
   - **Anti-spoof / liveness** per face: a flat photo/screen is downgraded to
     Unknown so a printed photo of a known person is never announced by name.
   - **Object detection** (YOLOv8): people count + carried objects (bag/parcel).
   - **VLM scene + OCR** (cloud, GitHub Models): for *everyone* now (known and
     unknown), one call describes each person left→right (age range, hair, clothing
     + colour, uniform, carried objects, apparent mood) and reads any label text
     (courier name). Skipped only when configured off or no key (falls back to
     local age/gender + YOLO).
   - **Speech** (only on the dedicated "Hear Visitor" action): VAD-gated Whisper
     transcription + language detection.
   - **Translate**: transcript → the user's language (reuses the VLM keys).
   - **Re-ID**: unknown visitors get an appearance embedding, matched against
     recent unknowns → "the same unknown visitor, 3rd time today".
   - **Auto-enroll**: unknown face embeddings accumulate; DBSCAN clusters them; a
     frequent stranger → "Save this visitor?" suggestion.
4. **Fuse.** The **context engine** (pure functions) reads all the signals and sets
   a conservative `intent` + confidence.
5. **Compose + deliver.** The **accessibility engine** builds ONE natural-language
   announcement enumerating every person, then routes it: Blind/Both → speak
   (natural Kokoro voice); Deaf/Both → caption + flash + vibrate. A cooldown stops
   duplicate announcements from double-speaking.
6. **Persist + broadcast.** The full `VisitorEvent` is saved to SQLite with a
   snapshot, and pushed to all connected UIs over the WebSocket.
7. **Idle.** Return to watching the stream.

## B3. The pipeline order (exact)

```
frame ─▶ [Face detect+recognise+age/gender]  ┐ (face ∥ YOLO run concurrently)
        [Object detect (YOLO)]               ┘
      ─▶ [Anti-spoof per face → downgrade spoofed known → Unknown]
      ─▶ [build people[] + reconcile visitor_count with YOLO person count]
      ─▶ [VLM: describe everyone (L→R) + OCR labels]  (if enabled/keyed)
      ─▶ [Speech transcribe]  (ONLY if audio supplied via /hear_visitor)
      ─▶ [Translate transcript → user language]
      ─▶ [Re-ID unknowns; Auto-enroll stash]
      ─▶ [context_engine.infer_intent → intent, confidence]
      ─▶ [accessibility.compose_announcement → announcement_text]
      ─▶ [TTS speak if blind/both]  +  [save event + snapshot]  +  [WS broadcast]
```

---

# PART C — THE BUILD HISTORY (all phases, what each delivered)

Each phase ended with a working, demoable system. This is the chronological record
so the agent understands *why* the code is shaped as it is.

- **Phase 1 — Foundation.** Project scaffold; `config.py`; the `VisitorEvent`
  spine (with fields reserved for ALL later phases); the camera abstraction
  (webcam index OR MJPEG URL); SQLite via SQLAlchemy (tables `events`,
  `known_faces`, `reid_gallery`, `unknown_face_clusters` created up-front);
  FastAPI server with MJPEG `/video`, `/trigger`, WebSocket `/events`, history,
  snapshots, mode; a dark web dashboard; a manual "Ring" that creates + stores a
  (mostly empty) event. `Pipeline.__init__` reserved kwargs for every future
  module. Headless-safe gray-frame fallback.
- **Phase 2 — Face Recognition (InsightFace).** `buffalo_l` (detection +
  ArcFace), cosine matching against a per-person gallery loaded from
  `data/known_faces/<name>/*.jpg`. Live-view enrollment + a CLI enroller. Fills
  `identity`. Verified with a genuine same-person/different-image match (0.79+).
- **Phase 3 — Objects + Context/Intent (YOLOv8).** YOLOv8n detects people +
  carried items; a **pure** context engine fuses signals into a conservative
  intent (known / likely delivery / unknown / none). Reconciles `visitor_count =
  max(faces, people)`. The intent engine was future-proofed to read `ocr_text`
  and `reid_seen_count` before those existed.
- **Phase 4 — Accessibility Output (TTS + modes + reply).** Non-blocking TTS
  (single worker thread + queue); the accessibility engine became the ONE place
  announcements are composed; real `/reply` (type → spoken at door); Deaf-mode
  flash + vibrate + big text; an announcement cooldown.
- **Phase 5 — Anti-Spoofing / Liveness.** A liveness module scores a face crop
  real-vs-photo; a spoofed known face is downgraded to Unknown BEFORE the
  announcement. **Fail-OPEN** if the detector is missing (never lock out a real
  user), **fail-CLOSED** on a confident spoof. Currently the **heuristic
  placeholder** (real MiniFASNet `.onnx` is a drop-in).
- **Phase 6 — VLM Scene + OCR (GitHub Models).** OpenAI-compatible cloud vision
  (`gpt-4o-mini`) with **multi-key failover** (two comma-separated PATs), used for
  unknowns; reads courier labels; a cost guard (only fires when someone is present);
  degrades to YOLO-only offline. Keys from `.env`, masked in logs.
- **Phase 7 — Speech (Whisper + Silero VAD).** Records audio, VAD-gates it (so
  silence isn't transcribed — Whisper hallucinates on silence), transcribes offline
  (fed a numpy array to avoid ffmpeg). Fills `speech_transcript` + `language_detected`.
  **This phase's install silently downgraded torch and broke YOLO** → the pinned
  trio (torch 2.4.1 / torchvision 0.19.1 / torchaudio 2.4.1) was established.
- **Phase 8 — Multi-language + Translation.** Translates the transcript into the
  user's language. Default backend **reuses the Phase-6 GitHub Models keys** (text
  translation) — chosen specifically to avoid pulling torch (IndicTrans2/NLLB would
  have). Same-language = no API call.
- **Phase 9 — Re-ID + Auto-Enroll.** Appearance embeddings for repeat unknowns →
  "same unknown visitor N times today"; DBSCAN clustering of unknown faces →
  "Save this visitor?" suggestions. Re-ID is currently the **HSV-histogram
  placeholder** (real OSNet `.onnx` is a drop-in). Torch-safe by design.
- **Phase 10 — Wake Word + Voice Commands + Hardening.** openWakeWord (opt-in
  always-on; push-to-talk default); a pure `parse_command` + `handle_command`; a
  central `GET /status` health panel; a 41-test pytest suite; `/ring` webhook; and
  `docs/HARDWARE.md` + `docs/MOBILE.md`. Wake word shipped as a **pretrained
  placeholder** ("hey_jarvis"; since upgraded — a custom "Hey Access" model is
  trained offline by `scripts/train_wakeword.py` and auto-loads from
  `models/wakeword/`).
- **Phase 11 — Natural Voice (Kokoro-ONNX + edge-tts).** Replaced robotic espeak
  with **Kokoro** (natural, offline, onnxruntime — torch-safe) + edge-tts online
  fallback + in-app voice picker. Playback shells out to a subprocess player to
  avoid a PortAudio/ALSA double-free crash. Installed with `--no-deps` to protect
  numpy/onnxruntime pins.
- **Phase 12 — Speed + No-Record Doorbell + Rich Descriptions.** Doorbell stopped
  recording audio (`SPEECH_CAPTURE_ON_TRIGGER=False`) → big latency drop; a
  separate "Hear Visitor" action does audio. Face + YOLO run **concurrently**.
  Re-enabled InsightFace **genderage** (age + gender, local, instant). Richer VLM
  prompt (clothing, uniform, objects, expression). Age spoken as a **range**.
- **Phase 13 — Photo-Upload Enrollment UI.** Register known people by **uploading
  photos** with a name (multi-file), plus a Known-People list with thumbnails +
  delete. Recognition cross-check unchanged.
- **Phase 14 — PWA.** The dashboard as an installable, mobile-first PWA over the
  same API (WebSocket alerts, phone-side speech via `/speak_audio`, offline shell).
- **Phase 15 — Multi-Person + Per-Person Description.** The pipeline recognises +
  describes **every** person, not just the largest. `people[]` on the event; VLM
  describes everyone left→right; count reconciliation for faces YOLO sees but can't
  recognise; spoof-in-crowd handling.
- **Phase 16 — Native Flutter App (this build).** The award-winning, animated,
  accessibility-first native app over the same backend.

---

# PART D — THE DATA MODEL (the spine)

## D1. `VisitorEvent` — every field the app can rely on

| Field | Type | Meaning |
|---|---|---|
| `event_id` | str | `evt_YYYYMMDD_HHMMSS_<6hex>` unique id |
| `timestamp` | str (ISO) | when the event fired |
| `trigger` | str | "manual" / "doorbell" / "ring" / "motion" |
| `identity` | Identity | primary/first-known person (backward compat) |
| `people` | Person[] | **every** detected person (render this) |
| `face_box` | tuple | primary face box (x1,y1,x2,y2) |
| `spoof_score` | float | primary face liveness score (1.0 = real) |
| `is_spoof` | bool | primary face flagged as a photo/screen |
| `visitor_count` | int | reconciled total humans (max of faces, YOLO persons) |
| `detected_objects` | DetectedObject[] | YOLO detections (label, conf, box) |
| `carried_objects` | str[] | pretty carried items ("a backpack", "a package") |
| `scene_summary` | str | overall VLM scene line |
| `hazards` | str | reserved ("none") |
| `age` | int? | primary person age (approx; render as range) |
| `gender` | str | primary person "man"/"woman"/"" |
| `appearance` | str | primary person appearance line (VLM) |
| `ocr_text` | str | label/courier text read by the VLM |
| `speech_transcript` | str | visitor's words (from Hear Visitor) |
| `language_detected` | str | Whisper language code (e.g. "hi") |
| `translated_transcript` | str | transcript in the user's language |
| `reid_id` | str? | appearance id for a repeat unknown |
| `reid_seen_count` | int | how many times this unknown was seen (today) |
| `intent` | str | conservative label (see D3) |
| `confidence` | float | intent confidence 0–1 |
| `announcement_text` | str | the final composed sentence (speak/caption this) |
| `snapshot_path` | str | path to the saved JPEG (serve via /snapshot/{id}) |

## D2. `Person` — one entry in `people[]`

| Field | Type | Meaning |
|---|---|---|
| `known` | bool | matched to an enrolled person |
| `name` | str | the name if known, else "Unknown" |
| `confidence` | float | face match cosine similarity |
| `age` | int? | approximate age (render as a RANGE, never raw) |
| `gender` | str | "man"/"woman"/"" |
| `box` | tuple | face box (x1,y1,x2,y2) |
| `is_spoof` | bool | this face is a photo/screen |
| `spoof_score` | float | liveness score |
| `appearance` | str | VLM description: hair, clothing + colour, objects |
| `expression` | str | cautious mood cue ("appears calm") |

**Rendering rule:** KNOWN person → show **name** + appearance (clothing / carried /
mood); do **NOT** show age/gender (we know who they are). UNKNOWN person → show the
full description with **age as a range** and expression **hedged**. Any `is_spoof`
person → a red "⚠ photo" flag.

## D3. Intent vocabulary (conservative — never "definitely")

`known visitor` · `likely delivery` · `unknown visitor` · `possible spoof attempt`
· `no visitor detected`. Confidence is attached (e.g. likely-delivery is 0.65 for a
bag alone, 0.85 once a courier label is read).

## D4. The `DetectedObject`

`{label: str, confidence: float, box: (x1,y1,x2,y2)}` — raw YOLO output. COCO has
no "parcel" class; `backpack/handbag/suitcase/book` are treated as delivery-ish and
phrased conservatively as "a package".

---

# PART E — THE API CONTRACT (what the app calls)

All endpoints are served by the FastAPI backend on the laptop (default port 8000).
The app talks to `http://<laptop-LAN-IP>:8000`. Confirm exact shapes in
`accessai/server.py`; the below is the behavioural contract.

## E1. Live + core

- **GET `/video`** → MJPEG stream (`multipart/x-mixed-replace; boundary=frame`).
  Render in a live view; it can stall, so provide a reconnect.
- **POST `/trigger`** → runs the pipeline on the current frame (VISUAL ONLY, no
  audio) and returns the created `VisitorEvent` JSON; also broadcast on the WS.
- **WS `/events`** → JSON messages:
  - `{"type":"event","event":{...VisitorEvent...}}` — a doorbell ring.
  - `{"type":"visitor_speech","text":...,"translated":...,"language":...}` — result
    of Hear Visitor.
  - `{"type":"voice","text":...,"answer":...}` — a voice command Q/A.
  Ignore unknown types. Reconnect with backoff.
- **GET `/history?limit=N`** → array of past events (newest first).
- **GET `/event/{id}`** → one event, or 404.
- **GET `/snapshot/{id}`** → the event's JPEG (image/jpeg), or 404.

## E2. Modes, reply, voice

- **GET `/mode`** → `{"mode":"blind|deaf|both"}`. **POST `/mode`** `{mode}` → set it
  (also switches server-side speaking on/off).
- **POST `/reply`** `{text}` → speaks the text at the door (server TTS). Returns
  `{ok, spoken, engine, text}`. Empty text → 400.
- **POST `/hear_visitor`** → records a few seconds at the door, VAD-gates,
  transcribes + translates, attaches to the latest event / returns
  `{transcript, translated, language}`. **The only audio-recording route.** 503 if
  speech unavailable.
- **POST `/listen`** → blind user's voice-command capture at the server mic (legacy
  path). **POST `/command`** `{text}` → parse + run a command, returns
  `{intent, answer}` (preferred for the app, which can capture speech on-device).

## E3. Natural voice on the phone

- **GET `/speak_audio?text=...`** → returns **natural Kokoro TTS audio bytes**
  (wav/mp3). The app plays this so the resident hears the announcement on the phone
  in the good voice. Fallback: on-device `flutter_tts`.
- **GET `/voices`** → `{voices:[{id,label,available}], current, engine}`.
  **POST `/voice`** `{id}` → switch voice; speaks a confirmation.

## E4. Known people (enrollment)

- **GET `/known`** → `{people:[{name, photos, sample?}], known_count}`.
- **GET `/known_photo/{name}`** → a representative JPEG for the thumbnail.
- **POST `/enroll_upload`** (multipart: `name` + one or MORE image files) → detects
  a face in each, saves embedding + photo, returns
  `{name, added, skipped:[reasons], known_count}`. Encourage 2–3 photos.
- **POST `/known/delete`** `{name}` → removes the person (folder + embeddings + DB).

## E5. Memory + status

- **GET `/suggestions`** → open "Save this visitor?" prompts
  `[{cluster_id, size, sample_event_id}]`. **POST `/suggestions/confirm`**
  `{cluster_id, name}` promotes to a known face. **POST `/suggestions/dismiss`**
  `{cluster_id}`.
- **GET `/status`** → the full health JSON: per-module state (ok / placeholder /
  unavailable / off), the active TTS engine + voice, torch version, all ENABLE_*
  flags. **This is the app's health panel + demo safety net.**
- **GET `/reid_status`, `/vlm_status`, `/speech_status`, `/translate_status`,
  `/wakeword_status`** → per-subsystem detail.
- **POST `/ring`** → hardware webhook (ESP32 button); triggers the pipeline like the
  dashboard Ring (optionally accepts a posted JPEG body).

---

# PART F — CONFIGURATION REFERENCE (behaviour the app should respect)

Key `config.py` values the app interacts with or should be aware of:

- `CAMERA_SOURCE` — `0` (webcam) or an MJPEG URL (ESP32). One-line hardware swap.
- `ACCESSIBILITY_MODE` — "blind" | "deaf" | "both" (also via `/mode`).
- `USER_LANGUAGE` — target language for translation + spoken output.
- `FACE_MATCH_THRESHOLD` — cosine threshold; lower = looser match. Tune if
  mis-naming (0.40 looser, 0.50 stricter). More enrolled photos per person also
  improves reliability.
- `SPEECH_CAPTURE_ON_TRIGGER=False` — the doorbell does NOT record audio.
- `VLM_DESCRIBE_KNOWN=True` — the VLM now describes known people too (clothing /
  objects / mood), not just unknowns.
- `VLM_ONLY_FOR_UNKNOWN` / `VLM_ASYNC_ENRICH` — latency controls.
- `TTS_ENGINE` = "kokoro" (default) with edge-tts + pyttsx3 fallbacks;
  `KOKORO_VOICE` default `af_heart` (warm female). `VOICE_CHOICES` drives the
  picker.
- `GITHUB_MODELS_KEYS` — from `.env`, comma-separated (two accounts), multi-key
  failover. Never hardcoded.
- `ENABLE_*` flags — face, vision, antispoof, vlm, ocr, speech, translate, reid,
  autoenroll, wakeword, tts. Base app runs with any of them off.

---

# PART G — HONEST STATUS: REAL vs PLACEHOLDER

The app's `/status` panel must surface these truthfully (green = production-grade,
amber = placeholder, red = down):

- **Face recognition (InsightFace):** REAL.
- **Object detection (YOLOv8):** REAL.
- **Age/gender (InsightFace genderage):** REAL but approximate → speak as a range.
- **VLM scene + OCR + translation (GitHub Models):** REAL but needs an API key +
  internet; degrades to local-only offline.
- **Speech (Whisper + Silero VAD):** REAL, offline.
- **Natural TTS (Kokoro-ONNX):** REAL, offline; edge-tts online alt.
- **Anti-spoof:** **REAL** — the two MiniFASNet `.onnx` models sit in
  `models/antispoof/` (fetched via `scripts/fetch_antispoof_models.py`; the
  Laplacian heuristic remains the automatic fallback if they are removed).
- **Re-ID:** **PLACEHOLDER** (HSV colour histogram — keys on clothing colour). Drop
  OSNet `.onnx` into `models/reid/` to upgrade — zero code change
  (`scripts/fetch_reid_model.py` downloads or converts an official checkpoint).
- **Wake word:** **REAL** — a custom "Hey Access" model
  (`models/wakeword/hey_access.onnx`) trained fully offline by
  `scripts/train_wakeword.py` from synthetic Kokoro speech; the pretrained
  "hey_jarvis" phrase remains the automatic fallback if it is removed.

Being explicit about this is a strength for the review panel — it demonstrates
engineering maturity, and each placeholder is a documented drop-in.

---

# PART H — CROSS-CUTTING RULES (must hold in every build)

1. **Torch pin.** torch 2.4.1 / torchvision 0.19.1 / torchaudio 2.4.1, numpy
   1.26.4, onnxruntime 1.18.1. Any ML install can break this; after any install,
   verify torch is unchanged AND YOLO detects on `bus.jpg` (bus + persons, not
   zero). Symptom of a broken torch = "YOLO silently finds nothing".
2. **VisitorEvent is append-only.** Add fields; never rename/remove.
3. **context_engine stays pure** (no I/O, no model loading) so it's unit-testable.
4. **Camera only via `accessai/camera.py`.**
5. **Conservative language** in every generated sentence.
6. **Graceful degradation** — never crash on a missing model, key, mic, or network.
7. **Cost discipline** — one VLM call per event; skip when nobody is present;
   same-language = no translate call; known-only optionally skips VLM.

---

# PART I — THE FLUTTER APP SPECIFICATION (build this)

The app is a **native, animated, accessibility-first thin client** over the API
above. Build in `mobile/`. Android first (dev machine is Linux).

## I1. Non-negotiable accessibility (this is an assistive product)

- **Screen reader:** wrap meaningful widgets in `Semantics` with clear labels;
  announce live events with `SemanticsService.announce`; logical focus order;
  meaningful headings. Assume TalkBack is on.
- **Touch targets:** primary actions ≥ 64dp; generous spacing; never tiny controls.
- **Text:** respect the system text-scale factor; support very large text without
  breaking layout; never hardcode small fonts.
- **Contrast + themes:** WCAG-AA colour; light, dark, and high-contrast themes.
- **Deaf support:** rich haptics (`HapticFeedback` + `vibration` patterns), visual
  flash, and captions for everything spoken.
- **Blind support:** spoken output on the phone (Kokoro via `/speak_audio`, then
  `flutter_tts` fallback) + screen-reader announcements; a voice "Ask" affordance.
- **Motion safety:** honour `MediaQuery.disableAnimations` /
  `MediaQuery.accessibleNavigation` — when reduce-motion is on, disable
  parallax/3D/auto-motion and use instant/minimal transitions. **Motion never
  gates function.**

## I2. Visual / motion direction (stunning within those rules)

- A cohesive design system: an 8-pt spacing scale, consistent radii + elevation, a
  signature accent colour, `google_fonts` for a distinctive but legible typeface.
- Depth: glassmorphism (`BackdropFilter` blur), soft cards, gradient meshes, subtle
  tilt-parallax (`sensors_plus`) — all gated by reduce-motion.
- A signature animated **smart-doorbell hero** (use `rive` for interactive
  vector/"3D-feel"; `lottie` for accents; optional true 3D via `model_viewer_plus`
  loading a `.glb`). If no 3D asset is available, achieve depth with layered
  parallax + shaders/gradients rather than blocking the build.
- Micro-interactions via `flutter_animate`: button press ripples, staggered list
  reveals, number count-ups, shimmer loaders.
- Hero shared-element transitions (list → detail snapshots).
- A distinctive **full-screen doorbell alert** (pulse/ripple + snapshot reveal).

## I3. Architecture

- **State:** Riverpod. **Network:** dio (REST) + web_socket_channel (events).
- **Services/repositories:** `ApiService` (REST), `EventsService` (WS with backoff
  reconnect), `AudioService` (`just_audio` for `/speak_audio`, `flutter_tts`
  fallback), `PrefsService` (`shared_preferences`: server URL, mode, voice, theme).
- **Models:** `VisitorEvent`, `Person`, `DetectedObject`, `KnownPerson`,
  `Suggestion`, `Status` — mirror the API shapes; tolerate missing fields.
- **App Mode** (blind/deaf/both) is global, synced with `/mode`, and changes
  behaviour throughout.
- **Robustness:** friendly retry UI on network errors; ignore unknown WS types;
  sensible defaults for missing fields; never crash.

## I4. Screens (all existing capabilities, beautifully)

**HOME / "Door"** — the animated doorbell hero; the latest event as a multi-person
card (count line "3 people — 1 known, 2 unknown"; known = name chip + appearance;
unknown = rich card with age range/hair/clothing colour/objects/mood; spoof = red
flag); announcement text + timestamp + hero snapshot. A big **🔔 Ring** button
(POST `/trigger`, visual-only, fast, NO listening indicator, press animation +
success haptic). A separate **🎤 Hear Visitor** button (POST `/hear_visitor`, the
only recorder, shows a "listening… (Ns)" animation, renders caption + translation).
A **💬 Reply** composer (POST `/reply`) with quick-reply chips.

**LIVE** — the MJPEG view (`flutter_mjpeg` or a robust multipart image) with
reconnect, framed nicely, a subtle reduce-motion-aware overlay.

**HISTORY** — a staggered list (thumbnail, name(s) "Vinay +2", intent, time);
tap → detail with the full people breakdown + a snapshot hero transition;
pull-to-refresh.

**PEOPLE** — a grid of known people (thumbnail via `/known_photo/<name>`, photo
count, delete with confirm); an "Add person" flow: name + pick MULTIPLE photos
(`image_picker`) → POST `/enroll_upload` (dio multipart) with per-file results and
a "2–3 photos for accuracy" hint.

**SETTINGS** — server URL (persisted) + Test connection (GET `/status`) with an
animated health panel (green/amber per module; flag placeholders + missing VLM
keys); Mode toggle (blind/deaf/both, POST `/mode`); voice picker (GET `/voices` /
POST `/voice`) + Test voice; language display; theme (light/dark/high-contrast);
reduce-motion status.

**LIVE ALERTS** (the heart) — a persistent WS to `/events`; on `event`: a
full-screen/top-sheet alert with snapshot + multi-person breakdown + announcement;
Deaf/Both → flash + vibration + caption; Blind/Both → play `/speak_audio` (Kokoro)
via `just_audio` (fallback `flutter_tts`) + `SemanticsService.announce`. On
`visitor_speech` → caption + translation. On `voice` → command Q/A.

**VOICE COMMANDS** — a prominent "Ask" mic; if `speech_to_text` is available,
capture on-device and POST `/command` `{text}`, else a text field; speak the
returned answer via `/speak_audio`. Commands: "who is at the door", "recent
visitors", "open live", "switch to deaf mode", etc.

## I5. Platform gotchas the app MUST handle

- **Android cleartext HTTP:** Android blocks plain `http://` by default. Add
  `res/xml/network_security_config.xml` permitting cleartext to private LAN ranges
  (or `usesCleartextTraffic=true` for dev), reference it in the manifest, and add
  the INTERNET permission. Without this the app cannot reach the laptop.
- **Audio autoplay/unlock:** ensure playback is allowed; play Kokoro audio reliably
  on event.
- **Configurable server URL:** first-run setup + persisted; used for REST, WS, and
  `/video`.
- **iOS is out of scope now** (no Mac); design so a future iOS build is feasible
  (ATS would require HTTPS).

## I6. Packages (lean, pinned to versions that build on current stable)

`flutter_riverpod`, `dio`, `web_socket_channel`, `shared_preferences`,
`just_audio`, `flutter_mjpeg`, `flutter_tts`, `speech_to_text` (optional),
`image_picker`, `vibration`, `flutter_animate`, `rive` and/or `lottie`,
`model_viewer_plus` (optional 3D), `google_fonts`, `sensors_plus` (optional
parallax), `shimmer` (or flutter_animate shimmer). Prefer fewer if any won't build.

## I7. Definition of done (verify + report)

`flutter --version` + trimmed `flutter doctor` (+ any manual steps); `flutter
analyze` clean; `flutter build apk --debug` (or `flutter run -d chrome`); Test
connection shows `/status`; a live WS alert (Deaf flash/vibrate + Blind Kokoro
audio + Semantics announce); multi-person render; fast no-record doorbell vs
Hear-Visitor recorder; multi-photo enrollment + live view + history detail;
accessibility proof (Semantics, big targets, text scaling, reduce-motion degrade);
backend + PWA non-regression.

---

# PART J — GLOSSARY

- **VisitorEvent** — the single data object that flows through the whole system.
- **Context engine** — pure functions that fuse signals into a conservative intent.
- **Accessibility engine** — composes the announcement + routes it to Blind/Deaf.
- **Liveness / anti-spoof** — checks a face is a real present person, not a photo.
- **Re-ID** — recognising a repeat *unknown* by body/appearance, not face.
- **Auto-enroll** — clustering repeat unknown faces to suggest saving them.
- **VLM** — vision-language model (cloud) that describes people + reads labels.
- **VAD** — voice activity detection; gates Whisper so silence isn't transcribed.
- **Fail-open / fail-closed** — missing detector → allow (open); confident spoof →
  deny (closed).
- **Placeholder** — a functional stand-in (anti-spoof heuristic, Re-ID histogram,
  pretrained wake word) with a documented zero-code-change real-model upgrade.

---

# PART K — HOW TO USE THIS DOCUMENT

Read PART A–H to understand the system, PART I to build the Flutter app, and keep
PART D (data model) + PART E (API) open as you code the client. Obey PART H
(cross-cutting rules) at all times — especially the torch pin and the accessibility
requirements. When in doubt, prefer honest, conservative, accessible behaviour over
visual flash. If you need more depth on any single area (a specific module's
internals, the exact JSON of an endpoint, a particular screen's interaction model,
the animation choreography, or the accessibility test matrix), ask for that section
to be expanded — this reference is meant to grow.
```
