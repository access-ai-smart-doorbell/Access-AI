# AccessAI — Detailed Project Report

**AI-Powered Smart Accessibility Doorbell for Blind & Deaf People**

_Status as of this report: **Phases 1–17 complete** (software, laptop-first). The
perception pipeline, web dashboard, native Flutter app, and LAN hardening are all
built and running. What remains is **hardware** (an ESP32-CAM door unit) and
**on-device / real-world calibration** — see §6._

---

## 1. Executive Summary

AccessAI is an intelligent doorbell that perceives a visitor on behalf of a blind
or deaf user and communicates that understanding through the sense the user
actually has. It replaces the meaningless chime with a sentence like:

> "Rahul is at the front door. He is carrying a parcel. He said, 'Package for you.'"

- **Blind Mode** → spoken announcement + phone vibration.
- **Deaf Mode** → large on-screen text, live captions, two-way text↔speech chat.

Face recognition, anti-spoofing, speech, translation, re-ID, the wake word, and TTS
all run **locally** and offline. One capability is cloud-assisted and it is worth
naming plainly: the Phase-6 VLM scene description and parcel OCR call GitHub
Models, and only ever for **unknown** visitors — a recognised household member's
face never leaves the machine. With no API key configured the system falls back to
YOLO-only signals and is never blocked.

No subscription. Prototype hardware budget < ₹5,000; the laptop is the AI brain
during development, and a one-line config change swaps in an ESP32-CAM later.

**Core novelty:** not any single model, but the accessibility-first *integration*
of face recognition + scene understanding + speech + a conservative context
engine into one coherent assistant for users mainstream doorbells ignore.

---

## 2. Problem & Motivation

An ordinary doorbell communicates one fact — "somebody is here" — through sound.
- A **deaf** person may not perceive the chime at all, and once at the door
  cannot hear the visitor or be easily understood.
- A **blind** person hears the chime but learns nothing about *who* is outside
  or *why*, forcing a choice between dependence and risk.

Existing smart doorbells (Ring, Nest, Amazon) just move the same
video-and-audio interaction onto a phone screen — still assuming the user can
see and hear. **No affordable, accessibility-first doorbell exists that perceives
the visitor for the user and communicates through their available sense.**

---

## 3. Aim & Objectives

**Aim:** Design an AI doorbell that interprets visitors for blind/deaf users and
delivers that through accessible channels.

**Objectives:**
1. Capture image (+ audio on request) at a doorbell/motion trigger.
2. Recognise known household members by face.
3. Describe unknown visitors and their carried objects.
4. Infer likely intent (delivery / guest / unknown) *without overstating*.
5. Transcribe and, where needed, translate the visitor's speech.
6. Deliver via Blind Mode (voice+vibration) and Deaf Mode (text+captions+2-way).
7. Keep a searchable visitor history.
8. Keep processing local and private; prototype < ₹5,000.
9. Same code runs on laptop webcam now and ESP32-CAM later (config switch only).

---

## 4. System Architecture

Three cooperating tiers:

1. **Doorbell Unit** — laptop webcam now; ESP32-CAM (button, LED, mic, PIR)
   later. Only captures and transmits; deliberately dumb.
2. **AI Processing Server** — the laptop. Runs all models + the context/fusion
   engine + accessibility engine.
3. **User Interface** — web dashboard now; Flutter mobile app later. Same
   backend API serves both.

```
Webcam / (later) ESP32-CAM
        │
        ▼   trigger (button / motion / manual)
   capture snapshot (+ audio)
        │
   ┌────┴───────────────────────────────┐
   ▼                                    ▼
Face pipeline                     Vision pipeline
 detect → anti-spoof → InsightFace   YOLO objects
 → known / unknown                   (skip VLM if known)
   │                                    │
   │                               VLM scene desc
   │                               OCR on parcels
   └───────────────┬────────────────────┘
                   ▼
             Speech pipeline
             VAD → Whisper → langdetect → translate
                   │
                   ▼
             Visitor Re-ID (unknowns only)
                   │
                   ▼
             CONTEXT ENGINE  →  Visitor Event  (the spine)
                   │
   ┌───────────────┼────────────────┐
   ▼               ▼                ▼
 Blind Mode     Deaf Mode        History (SQLite)
 TTS + vibrate  text + captions   + snapshots
                + 2-way chat
```

### 4.1 The Visitor Event — the backbone

The whole system is unified by one data object. **Every module writes into it;
every output reads from it.** Adding a feature = add a field + have one module
fill it in. No pipeline rework.

```json
{
  "event_id": "evt_20260709_1432_ab12cd",
  "timestamp": "2026-07-09T14:32:10",
  "trigger": "doorbell",
  "identity": { "known": true, "name": "Rahul", "confidence": 0.97 },
  "spoof_score": 0.98, "is_spoof": false,
  "visitor_count": 1,
  "carried_objects": ["a package"],
  "scene_summary": "a man in a casual shirt holding a box",
  "ocr_text": "BlueDart",
  "speech_transcript": "Package for you",
  "language_detected": "en", "translated_transcript": "",
  "reid_id": null, "reid_seen_count": 0,
  "intent": "known visitor", "confidence": 0.9,
  "announcement_text": "Rahul is at the door. Carrying a package.",
  "snapshot_path": "data/history/evt_20260709_1432_ab12cd.jpg"
}
```

---

## 5. What Is Built — Current Code State

Location: `~/AccessAI/`

### 5.1 The Phase-1 foundation

| File | Role | Status |
|---|---|---|
| `config.py` | All tunables (camera, thresholds, feature flags, courier keywords) | ✅ Done |
| `run.py` | Entrypoint: camera thread + uvicorn server | ✅ Done |
| `requirements.txt` | Pinned deps (Piper removed — see §8) | ✅ Done |
| `accessai/camera.py` | Webcam/MJPEG abstraction (ESP32-ready) | ✅ Existing, kept |
| `accessai/visitor_event.py` | The data spine (dataclasses) | ✅ Extended |
| `accessai/face_module.py` | InsightFace `buffalo_l`, cosine matching | ✅ Rewritten |
| `accessai/vision_module.py` | YOLOv8n object detection | ✅ Done |
| `accessai/context_engine.py` | Fuses signals → VisitorEvent + rule-based intent | ✅ Done |
| `accessai/accessibility.py` | Composes announcement, routes Blind/Deaf | ✅ Done |
| `accessai/tts_module.py` | Kokoro-ONNX → edge-tts → pyttsx3 (see §5.3) | ✅ Done |
| `accessai/database.py` | SQLite via SQLAlchemy (events, faces, reid, clusters) | ✅ Done |
| `accessai/pipeline.py` | End-to-end runner; honours skip-VLM-for-known | ✅ Done |
| `accessai/server.py` | FastAPI + WebSocket + MJPEG stream | ✅ Done |
| `web/index.html`, `app.js`, `style.css` | Dashboard: live cam, Ring, event card, history, reply, mode toggle | ✅ Done |
| `README.md` | Setup + troubleshooting | ✅ Done |

### Phase 1 capabilities (working end-to-end)
- Live webcam in the browser (MJPEG stream).
- "Ring Doorbell" → runs the full pipeline.
- Face recognition: known → name; else "Unknown".
- Object detection: reports carried items (backpack/handbag/suitcase/book→package).
- Rule-based intent: known visitor / likely delivery / unknown visitor / no visitor.
- Announcement composed and spoken (pyttsx3) + shown in UI.
- Two-way reply box (Deaf Mode): type → laptop speaks at "door".
- Every event saved to SQLite with a snapshot; history list in UI.
- Blind/Deaf/Both mode toggle.

### Key design decisions locked in
- **InsightFace** over dlib (accuracy).
- **Skip-VLM-for-known-faces**: the heavy vision-language model only runs for
  Unknown visitors → roughly halves latency for the common case.
- **Conservative intent language**: "likely delivery", never "definitely".
- **Privacy**: store embeddings, not raw photos, in the DB.
- **Anti-spoof gate at the module boundary**: a "known but spoofed" face is
  downgraded to Unknown before it reaches the context engine.
- **Dropped**: threat/weapon detection (false-positive risk), speech emotion
  (unreliable). **Deferred**: loitering (needs continuous frames).

---

### 5.2 Phases 2–17, as built

Each phase below is wired into the same pipeline and the same `VisitorEvent`. The
column that matters is the last one: what is running **real weights** versus a
documented fallback.

| Phase | Capability | Module | Real or fallback |
|------:|------------|--------|------------------|
| 2 | Face recognition, InsightFace/ArcFace `buffalo_l`, cosine match | `face_module` | real |
| 3 | YOLOv8n object detection + conservative intent fusion | `vision_module`, `context_engine` | real |
| 4 | TTS + Blind/Deaf/Both routing + two-way reply | `accessibility`, `tts_module` | real |
| 5 | Anti-spoof / liveness — a photo-of-a-face downgrades to Unknown | `antispoof_module` | real (two MiniFASNet `.onnx`) |
| 6 | VLM scene description + parcel OCR, unknowns only, cloud | `vlm_module` | real (needs a PAT; falls back to YOLO-only) |
| 7 | Offline speech recognition, Whisper + Silero VAD | `speech_module` | real |
| 8 | Translation across 11 Indian/EN languages | `translate_module` | real |
| 9 | Visitor re-ID + DBSCAN auto-enrollment of frequent unknowns | `reid_module`, `auto_enroll` | real (OSNet `osnet_x0_25.onnx`) — **threshold uncalibrated**, see §6 |
| 10 | Wake word + voice commands + central `/status` health | `wakeword_module`, `voice_commands` | real (custom "hey access" `.onnx`) |
| 11 | Natural neural voice | `tts_module` | real (Kokoro-ONNX; see §5.3) |
| 12 | Speed: announce instantly, enrich with the VLM in the background | `pipeline`, `vlm_module` | real |
| 13 | Photo enrollment from the browser, no CLI | `server`, `face_module` | real |
| 14 | Installable mobile PWA dashboard | `web/` | real |
| 15 | Multi-person scenes: per-person boxes, group announcements | `visitor_event`, `accessibility` | real |
| 16 | Native Flutter app: live view, history, people, voice, alerts | `mobile/` | real (34 Dart files) |
| 17 | LAN hardening: bearer auth, per-IP rate limits, HMAC `/ring`, per-device modes, motion trigger, background phone alerts | `server`, `motion_module`, `mobile/` | real |

**All VisitorEvent fields these phases needed already existed in Phase 1** — every
phase filled fields rather than restructuring the spine. That was the bet made in
§4.1 and it held for sixteen consecutive phases.

### 5.3 Two fallback chains worth knowing

**TTS:** Kokoro-ONNX (neural, offline, primary) → edge-tts (cloud; returns 403 on
some networks) → pyttsx3 (system `espeak`, always available). Each step is logged
at boot and `GET /status` reports the engine actually in use.

**Cloud VLM keys:** `GITHUB_MODELS_KEYS` takes a comma-separated list and the
client rotates to the next key on a quota or auth error, so one dead key does not
disable scene description. The list must stay on **one line** — a `.env` value
cannot span lines, and a wrapped list silently loads only the first key, which
looks exactly like "all keys failed" when that first key is the dead one.

### 5.4 Background phone alerts (LAN-only, no Firebase)

The Flutter app holds the `/events` WebSocket open itself. Android freezes a
backgrounded process within a minute or two, so a foreground service
(`flutter_foreground_task`) keeps the **main** isolate alive, and
`flutter_local_notifications` posts the alert on a max-importance doorbell
channel. The socket is deliberately **not** moved into the service's own isolate:
that would need a duplicate API config, token, TTS, and Riverpod graph, and would
race the UI isolate into double notifications.

The cost is honest and visible: Android forces a persistent "AccessAI is
listening" notice, so the toggle to turn it off lives in Settings rather than
buried in system settings. Nothing leaves the LAN.

---

## 6. Remaining Work

The software is complete. What is left is physical and empirical — the two kinds
of work that cannot be done from a laptop alone.

### 6.1 Hardware: the ESP32-CAM door unit
All frames go through `accessai/camera.py`, and OpenCV's `VideoCapture` accepts an
int index *or* an MJPEG URL, so the swap is one line in `config.py`:

```python
CAMERA_SOURCE = "http://192.168.1.50:81/stream"
```

Firmware and bring-up are in **[HARDWARE.md](HARDWARE.md)**. `POST /ring` is the
webhook for the physical button and accepts an optional posted JPEG. ESP32-S3
Sense is recommended over a plain ESP32-CAM because it has a microphone, which
Phase 7's speech pipeline needs at the door rather than at the laptop.

### 6.2 Calibration on real doorway footage
`REID_MATCH_THRESHOLD` is currently **0.90** and is not calibrated. This is not a
guessed number but it is an unvalidated one: OSNet features are post-ReLU, so
every dimension is ≥ 0 and the cosine similarity between two *unrelated* crops
sits around 0.6 by construction. A threshold that looks conservative on paper can
still merge two strangers, or split one visitor across two identities. It needs a
day of real footage at the actual door, at the actual mounting height, in the
actual light.

### 6.3 On-device verification (Android)
No Android device has been attached during development, so the Flutter app's
background behaviour is verified by construction, not by observation. The handoff
checklist is in **[MOBILE.md](MOBILE.md)**: confirm the merged manifest carries
`android:foregroundServiceType="dataSync"` (Android 14+ requires a typed service),
grant the battery-optimisation exemption on OEM skins that kill unexempted
services, and confirm an event with the app fully closed both rings and vibrates.

### 6.4 Enabling LAN auth
Auth ships **off** so a first run works without ceremony, and the server says so
loudly at boot. Turning it on is two secrets in `.env` (`ACCESSAI_TOKEN`,
`ACCESSAI_RING_SECRET`) and narrowing `CORS_ORIGINS` off the `["*"]` wildcard. The
Flutter app already has an "Access token" field in Settings, so no rebuild is
needed for this.

---

## 7. Technology Stack

- **Vision/camera:** OpenCV
- **Face:** InsightFace (ArcFace `buffalo_l`) + onnxruntime
- **Objects:** Ultralytics YOLOv8n
- **Anti-spoof:** MiniFASNet ×2 (ONNX, CPU)
- **Scene + OCR:** GitHub Models `gpt-4o` (OpenAI-compatible), unknowns only
- **Speech:** OpenAI Whisper + Silero VAD (offline)
- **Translate:** 11 Indian/EN languages
- **Re-ID:** OSNet `osnet_x0_25.onnx`; DBSCAN (scikit-learn)
- **Wake word:** openWakeWord, custom "hey access" model trained offline by
  `scripts/train_wakeword.py` (synthetic Kokoro voices → openWakeWord embeddings
  → a small classifier)
- **TTS:** Kokoro-ONNX → edge-tts → pyttsx3
- **Backend:** Python + FastAPI + WebSockets + uvicorn
- **DB:** SQLite (SQLAlchemy)
- **App:** Flutter (Riverpod, dio, web_socket_channel, speech_to_text,
  flutter_tts) + `flutter_foreground_task` / `flutter_local_notifications`.
  **No Firebase** — alerts are LAN-only by design (§5.4).
- **Door unit (pending):** ESP32-S3/ESP32-CAM, MJPEG streaming

### 7.1 The pinned stack — do not float these

torch 2.4.1+cu121, torchvision 0.19.1+cu121, torchaudio 2.4.1, numpy 1.26.4,
onnxruntime 1.18.1. A careless upgrade once broke YOLO **silently** — zero
detections, no error, no traceback. openWakeWord is installed `--no-deps`
specifically so it cannot drag torch off the pin.

The verification ritual after any dependency change is: confirm the pins are
unchanged, then run `YOLO('yolov8n.pt').predict('bus.jpg')` and check it returns
`{bus: 1, person: 4, stop sign: 1}` at default confidence. A silent regression is
the failure mode here, so the check has to be a positive assertion about output,
not the absence of an error. `scripts/install_deps.sh` plus `constraints.txt`
exist to make the install reproducible rather than a matter of luck.

---

## 8. Known Issues & Environment Notes

- **Piper was dropped.** `piper-tts` needs `piper-phonemize`, which has no Python
  3.12 wheel, and one unresolvable dep aborts the entire `pip install`. Kokoro-ONNX
  replaced it in Phase 11 and is better anyway (neural, offline, no build step).
- **pyttsx3 on Linux** needs `espeak`: `sudo apt install -y espeak alsa-utils`.
  It is the last fallback, so if it is missing the chain has no floor.
- **edge-tts returns 403 on some networks.** Expected; the chain drops to pyttsx3
  and `/status` reports which engine is live.
- **First run is heavy:** torch (~2 GB), InsightFace `buffalo_l` (~300 MB), YOLO
  weights, Whisper, Kokoro — all download once.
- **Webcam:** on native Linux `CAMERA_SOURCE = 0`. If there is no `/dev/video*`,
  point `CAMERA_SOURCE` at a phone IP-camera app's URL — the same mechanism the
  ESP32 will use, so it is a genuine rehearsal rather than a workaround.
- **Anti-spoof takes raw 0-255 pixels, not normalised.** MiniFASNet was trained
  that way; dividing by 255 makes it flag every real face as a photo.

---

## 9. Testing & Verification

### 9.1 Automated
`python -m pytest tests/ -q` — the suite covers the accessibility text composer
(the module that decides what the user actually hears), the SQLite storage layer,
and the Phase-17 security middleware: auth accepted via `Authorization: Bearer`
and via `?token=` (the MJPEG `<img>` and the WebSocket cannot set headers), token
prefixes rejected, public UI paths still reachable, and the per-IP token bucket's
burst, isolation, and refill.

CI (`.github/workflows/tests.yml`) runs on push and PR. It deliberately installs
**only** the light dependencies, not the ~2 GB ML stack, which keeps it under a
minute — but that means **CI proves the logic is correct, not that the models
still load.** Model loading is verified by the boot self-check: `python run.py`
prints a status block for all 10 modules and reports which are running real
weights versus a fallback.

### 9.2 Manual, on the running system
1. Add `data/known_faces/YourName/1.jpg`, restart → boot log shows the face
   loaded; Ring → the announcement uses your name.
2. Remove the photo → Ring → "unknown visitor."
3. Hold a box → the announcement mentions the carried object.
4. **Hold a phone showing a photo of a face → caught as a spoof** and downgraded
   to Unknown. This is the security-critical one.
5. Speak Malayalam or Hindi at the camera → transcribed and translated.
6. The same stranger twice → "seen ×2".
7. Say "hey access, who's at the door?" → spoken answer, no touch.
8. Type in the reply box (Deaf Mode) → the laptop speaks it at the door.
9. History lists events with snapshots and is searchable by name and date.

### 9.3 Not yet verified
Every item in §6: the ESP32 unit does not exist yet, the re-ID threshold has not
met real footage, and the Android background alert path has not run on a physical
phone. These are stated as open rather than assumed to work.

---

## 10. Ethics & Privacy

- **Local by default, with one stated exception.** All face, speech, translation,
  re-ID, wake-word, and TTS processing is on-device. The VLM scene description
  (Phase 6) is a cloud call, made **only for unknown visitors** — a recognised
  household member is never uploaded. Turning off `ENABLE_VLM` makes the system
  fully local at the cost of richer descriptions.
- **Phone alerts never leave the LAN.** No Firebase, no push service, no relay:
  the phone connects directly to the doorbell over the local network (§5.4).
- DB stores face **embeddings**, not raw photos.
- Non-registered faces can be blurred in stored footage (future).
- System is explicit about uncertainty ("likely," never fact).
- Recording is trigger-based, not continuous surveillance.
- **Auth ships off and says so.** The server prints a loud warning at boot when it
  is LAN-reachable without a token. Silence there would be the real ethical
  failure; a warning the user can act on is not.

---

## 11. Limitations (stated honestly)

Intent is inferred from visible cues and can be wrong. Accuracy degrades in poor
light; the embedded camera is weaker than a webcam. VLMs can mis-describe (hence
the rule-based fallback). Running several models adds latency (mitigated by
analysing one triggered snapshot, not continuous video).

---

## 12. Cost

- **Student prototype:** door-unit parts ≈ ₹2,700 (laptop is the free AI brain)
  → within ₹5,000.
- **Product, cloud AI:** door unit ≈ ₹3,000 + subscription model.
- **Product, local edge AI (private):** + Jetson-class box ≈ ₹18k–25k → premium,
  subscription-free, fully private appliance (a real market gap).
