<div align="center">

# 🔔 AccessAI

### An AI-powered accessibility doorbell for blind and deaf users

*A normal doorbell only says "someone is here." AccessAI perceives the visitor on your behalf and communicates through the sense you have.*

[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Phases](https://img.shields.io/badge/Phases-10%2F10%20complete-2ea44f)](#what-it-does-phases-110)
[![Languages](https://img.shields.io/badge/Languages-11-orange)](#what-it-does-phases-110)
[![Tests](https://img.shields.io/badge/tests-pytest-0A9EDC?logo=pytest&logoColor=white)](#testing)
[![Vision](https://img.shields.io/badge/Vision-YOLOv8%20%2B%20InsightFace-blueviolet)](#what-it-does-phases-110)
[![License](https://img.shields.io/badge/License-MIT-yellow)](#)

</div>

> *"Rahul is at the front door. He is carrying a parcel. Likely a delivery.
> They said: 'Package for you.'"*

...delivered as **speech (Blind Mode)** or **large text + flash + two-way chat
(Deaf Mode)**, in **11 languages**, and — in Blind Mode — controllable entirely
**hands-free by voice** ("*hey access… who is at the door?*").

Built in **10 phases**, each one a working, demoable system. **This repo is
complete through Phase 10** — every capability below is wired into a single
event pipeline and a single dashboard.

---

## Table of Contents

- [Quick start](#quick-start)
- [What it does (Phases 1–10)](#what-it-does-phases-110)
- [Phase 10: hands-free voice + hardening](#phase-10-hands-free-voice--hardening)
- [Configuration](#configuration)
- [HTTP API (selected)](#http-api-selected)
- [Testing](#testing)
- [What is real vs. placeholder](#what-is-real-vs-placeholder)
- [Two important environment notes](#two-important-environment-notes)
- [Cloud vision keys](#cloud-vision-keys-phase-6--vlm-scene-description--ocr)
- [Design rules](#design-rules-held-across-all-10-phases)

---

## Quick start

```bash
# 1. Create and activate a virtual environment (Python 3.12)
python3 -m venv .venv
source .venv/bin/activate

# 2. System libraries (Linux)
sudo apt install -y espeak libportaudio2 ffmpeg build-essential python3-dev

# 3. Install dependencies
pip install -r requirements.txt

# 4. (Optional) cloud vision keys for scene description + OCR
cp .env.example .env      # then paste GitHub Models PAT(s) — see below

# 5. Run
python3 run.py
```

Then open the dashboard: **http://localhost:8000**

- **Live View** streams your webcam (or a harmless blank frame if there's no
  camera).
- Click **🔔 Ring Doorbell** → the full pipeline runs on the current frame:
  face → objects → anti-spoof → scene/OCR → speech → translation → re-ID →
  intent → announcement. The result is spoken and/or shown, a snapshot is
  saved, and it lands in **Visitor History**.
- **🎤 Speak a Command** (push-to-talk) or enable **Always listening** to drive
  Blind Mode hands-free.
- **System Health** panel shows every module's live state.

> No webcam? The app still runs headlessly — triggers fall back to a blank frame
> so events, snapshots, and history keep working.

---

## What it does (Phases 1–10)

| Phase | Capability | Module |
|------:|------------|--------|
| 1 | Foundation: camera → `VisitorEvent` → snapshot → SQLite → dashboard | `pipeline`, `camera`, `database`, `server` |
| 2 | Face recognition (InsightFace / ArcFace) | `face_module` |
| 3 | Object & scene detection (YOLOv8) + conservative intent fusion | `vision_module`, `context_engine` |
| 4 | Accessibility output: TTS + Blind/Deaf/Both routing + reply | `accessibility`, `tts_module` |
| 5 | Anti-spoofing / liveness (downgrades a photo-of-a-face to Unknown) | `antispoof_module` |
| 6 | VLM scene description + parcel OCR (cloud, unknown visitors only) | `vlm_module` |
| 7 | Speech recognition (offline Whisper + VAD) | `speech_module` |
| 8 | Multi-language translation (11 Indian/EN languages) | `translate_module` |
| 9 | Visitor re-ID + auto-enrollment of frequent unknowns (DBSCAN) | `reid_module`, `auto_enroll` |
| 10 | **Wake word + voice commands + hardening + ESP32/Flutter readiness** | `wakeword_module`, `voice_commands` |

Everything is fused onto **one object** — the `VisitorEvent` — and every output
reads from it. Adding a capability means *filling a field*, never restructuring.

---

## Phase 10: hands-free voice + hardening

**Voice commands (Blind Mode).** An always-listening detector hears a wake word,
captures a short command, parses it into an intent, acts using the *same*
pipeline/DB the doorbell uses, and **speaks the answer**. Two entry points share
the exact same logic:

- **Push-to-talk** (always available): the dashboard **🎤 Speak a Command**
  button → `POST /listen`.
- **Always-on** (opt-in — CPU + privacy): the wake detector runs in its own
  thread and fires the same interaction on detection.

Supported commands (see `accessai/voice_commands.py::parse_command`, a **pure,
unit-tested** function):

| Say… | Intent | Answer |
|------|--------|--------|
| "who is at the door?" | `who_is_there` | runs the pipeline on the current frame, speaks the announcement |
| "analyze the door" / "what do you see" | `analyze_now` | same as above |
| "recent visitors" / "who came earlier" | `recent` | summarises the last stored event |
| "how many visitors today" | `count_today` | counts today's events |
| "open the camera" | `open_camera` | confirms + focuses the live view on the dashboard |
| "blind/deaf/both mode" | `set_mode` | switches accessibility mode |
| *(anything else)* | `unknown` | a helpful spoken fallback |

**Design split:** `parse_command()` is pure (no I/O — this is what the tests
hit); `handle_command()` is the only place that touches the world; and
`run_voice_interaction()` is the glue shared by both entry points. Voice
**reuses** the Phase-7 `SpeechModule` (Whisper) and Phase-4 TTS — it never
duplicates capture or announcement logic.

**Central health.** `GET /status` returns every module's state (`ok` /
`placeholder` / `unavailable` / `off`), the config flags, `torch` version, and
which voice path is active. A boot **self-check table** prints the same at
startup. The **System Health** dashboard panel renders it with colour dots.

**Robustness.** Every new endpoint returns clean JSON on error (never a stack
trace); the app degrades on no-camera, all-flags-off, and cooldown; a `pytest`
suite covers the pure logic.

**ESP32 / Flutter readiness.** `POST /ring` is a doorbell webhook (accepts a
posted JPEG, else uses the latest frame). See **[docs/HARDWARE.md](docs/HARDWARE.md)**
(ESP32-CAM bring-up) and **[docs/MOBILE.md](docs/MOBILE.md)** (Flutter app spec
over the existing API).

---

## Configuration

Everything tunable lives in **`config.py`** — later phases only flip a flag or
change a value; they never scatter config. Key Phase-10 flags:

```python
ENABLE_WAKEWORD = True              # wake word + voice commands
WAKEWORD_MODEL_DIR = "models/wakeword"  # auto-loads the first .onnx here ("hey access")
WAKEWORD_MODEL = "hey_jarvis"       # pretrained FALLBACK phrase if no custom model exists
WAKEWORD_THRESHOLD = 0.5            # 0–1 detection score; raise to reduce false wakes
WAKEWORD_COMMAND_SECONDS = 4        # seconds of command audio captured after a wake
WAKEWORD_ALWAYS_ON = False          # OPT-IN: start the always-listening mic at boot
WAKEWORD_COOLDOWN = 6              # min seconds between two wake detections
WAKEWORD_INFERENCE_FRAMEWORK = "onnx"
```

Feature flags for every phase (`ENABLE_FACE`, `ENABLE_VISION`, `ENABLE_VLM`,
`ENABLE_SPEECH`, `ENABLE_TRANSLATE`, `ENABLE_REID`, `ENABLE_AUTOENROLL`, …) are
all in the same block. Turn anything off and the app still runs — it simply
reports that module as `off` in `/status`.

---

## HTTP API (selected)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/` | dashboard |
| GET | `/video` | MJPEG live stream |
| POST | `/trigger` | run the pipeline on the current frame (the doorbell) |
| POST | `/ring` | doorbell **webhook** (optional posted JPEG) — for ESP32 |
| GET | `/status` | **central health**: every module + flags + torch version |
| POST | `/listen` | push-to-talk voice command (optional uploaded WAV) |
| GET | `/wakeword_status` | wake detector state |
| POST | `/wakeword/{on\|off}` | toggle always-on listening at runtime |
| GET | `/history`, `/event/{id}`, `/snapshot/{id}` | stored events |
| POST | `/enroll`, `/reply`, `/mode`, `/user_language` | face enrol, two-way reply, mode/lang |
| GET | `/suggestions`, POST `/suggestions/{confirm\|dismiss}` | auto-enroll prompts |
| WS | `/events` | live event + voice broadcasts to the dashboard |

---

## Testing

```bash
pytest -q
```

The suite (`tests/`) covers the **pure logic** end-to-end:

- `test_context_engine.py` — `infer_intent` branch priorities + confidences
- `test_accessibility.py` — `compose_announcement` for every "who" branch + add-ons
- `test_voice_commands.py` — `parse_command` intents + `handle_command`/glue with fakes
- `test_reid.py` — re-ID cosine match (reuse vs. mint) on a temp SQLite gallery
- `test_auto_enroll.py` — DBSCAN clustering → suggestion; noise stays unclustered

No camera, mic, network, or TTS is touched by the tests.

---

## What is real vs. placeholder

AccessAI is a fully working system. Three components originally shipped as
**deliberate, loudly-logged placeholders** with a one-drop-in upgrade — each is
auto-detected at boot and reported honestly in `GET /status` and the health
panel (`placeholder` shows amber only while the fallback is active).

| Component | Fallback (if model files absent) | Upgrade (auto-loads, zero code change) | Status in this repo |
|-----------|----------------------------------|----------------------------------------|---------------------|
| **Anti-spoof** (Phase 5) | Laplacian/texture heuristic — catches obvious printed photos | Two MiniFASNet `.onnx` in `models/antispoof/` (`scripts/fetch_antispoof_models.py`) | ✅ **upgraded** — models present |
| **Re-ID** (Phase 9) | HSV colour-histogram embedding — keys mostly on clothing colour | An OSNet `.onnx` in `models/reid/` (`scripts/fetch_reid_model.py` — download or convert an official torchreid checkpoint) | ⚠️ fallback active — run the fetch script (needs network or a local checkpoint) |
| **Wake word** (Phase 10) | Pretrained openWakeWord "hey jarvis" phrase | A custom **"Hey Access"** `.onnx` in `models/wakeword/`, trained fully **offline** by `scripts/train_wakeword.py` (synthetic Kokoro voices → openWakeWord embeddings → tiny classifier) | ✅ **upgraded** — `hey_access.onnx` trained + streaming-verified |

**Everything else is real:** InsightFace recognition, YOLOv8 detection, the
intent engine, TTS output + mode routing, cloud VLM/OCR, offline Whisper speech,
translation, DBSCAN auto-enrollment, the whole event pipeline, storage, the
dashboard, and the voice-command layer.

Also note: **cloud VLM/OCR** (Phase 6) needs a GitHub Models PAT in `.env` to be
active; without it the app falls back to YOLO-only signals (never blocked).

---

## Two important environment notes

- **ESP32-CAM one-line swap.** All frames go through `accessai/camera.py`, and
  OpenCV's `VideoCapture` accepts an int index *or* an MJPEG URL. To move from a
  laptop webcam to an ESP32-CAM, change **one line** in `config.py`:
  ```python
  CAMERA_SOURCE = "http://192.168.1.50:81/stream"
  ```
  The camera thread reconnects with backoff if the stream drops. Full bring-up
  in **[docs/HARDWARE.md](docs/HARDWARE.md)**.
- **Python 3.12 / no Piper.** `piper-tts` is intentionally avoided — its
  dependency `piper-phonemize` has no Python 3.12 wheel. Phase 4 uses `pyttsx3`
  (system `espeak`). **Torch is pinned at 2.4.1**; openWakeWord is torch-free
  (installed `--no-deps`) so the pin — and YOLO — stay intact.

---

## Cloud vision keys (Phase 6 — VLM scene description + OCR)

For **unknown** visitors only, AccessAI can describe the scene and read parcel
text via **GitHub Models** (OpenAI-compatible). Known faces skip the cloud call
(latency/cost/privacy win). Setup:

```bash
cp .env.example .env
#   GITHUB_MODELS_KEYS=github_pat_xxx,github_pat_yyy
```

Create a fine-grained GitHub PAT with the read-only **Models** permission.
Supply two or more comma-separated keys for automatic failover. Keys are never
logged in full (masked to last 4 chars). If no key is set or all are
rate-limited, the app silently uses YOLO-only signals.

---

## Design rules (held across all 10 phases)

1. **The VisitorEvent is the spine.** Fill fields; never restructure.
2. **Central config.** All tunables + flags in `config.py`; every heavy feature
   behind a flag.
3. **Graceful degradation.** Optional components never crash the app.
4. **The camera is opened only in `accessai/camera.py`.**
5. **Conservative language** in announcements ("likely", "appears to be", never
   "definitely").

<div align="center">

---

Built with ❤️ for accessibility · **Blind Mode** · **Deaf Mode** · **11 languages**

</div>
