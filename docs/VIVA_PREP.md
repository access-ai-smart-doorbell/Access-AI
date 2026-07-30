# AccessAI — Viva / Demo Q&A Prep

A cheat-sheet for defending the project: which algorithm, which method, and
*why* — grounded in the actual code, not generic answers.

---

## 0. The 30-second pitch

> AccessAI is a **CPU-only, offline-first smart doorbell for blind and deaf
> users**. When someone rings, it runs a **multi-stage vision + audio pipeline** —
> face recognition, object detection, liveness check, appearance re-ID, a
> vision-language scene description, speech transcription and translation — then
> composes a single conservative announcement that is **spoken** (for blind users)
> and **shown with a visual/vibration alert** (for deaf users), on a laptop
> dashboard and a phone PWA.

Key engineering theme to repeat: **fail-soft, layered, conservative**. Every
heavy module is optional and degrades gracefully; every claim is hedged
("appears to…", "likely…").

---

## 1. "Which algorithm does each part use?" — the master table

| Stage | Task | Algorithm / Model | Library | Why this one |
|------|------|-------------------|---------|--------------|
| Face **detection** | find faces in the frame | **SCRFD / RetinaFace** (single-shot CNN detector) | InsightFace `buffalo_l` | Ships inside the same pack as recognition; fast on CPU |
| Face **recognition** | who is this exact person | **ArcFace** embeddings (512-d) + **cosine similarity** match | InsightFace `buffalo_l` (ONNX) | Purpose-built for identity; a VLM can't do 1:1 ID |
| Age / gender | estimate age range + gender | InsightFace **genderage** CNN | InsightFace | Comes free with the face pack |
| **Liveness / anti-spoof** | is it a real face or a photo? | **MiniFASNet** (Silent-Face CNN classifier) via ONNX | onnxruntime | Detects print/replay attacks; single frame |
| Object / person **detection** | people, bags, parcels | **YOLOv8-nano** (one-stage anchor-free detector) | Ultralytics | ~6 MB, CPU-friendly, 80 COCO classes |
| **Re-identification** | "have I seen this stranger before?" | **HSV colour histogram** + cosine (placeholder; OSNet `.onnx` drops in via `scripts/fetch_reid_model.py`) | OpenCV / onnxruntime | Appearance memory when no face is visible |
| **Scene description (VLM)** | describe clothing / mood / scene | **GPT-4o-mini** (vision) over an OpenAI-compatible API | GitHub Models endpoint | Rich language a local model can't match |
| **OCR** | read a courier label | same GPT-4o-mini call (`describe_and_read`) | GitHub Models | Reuses one VLM round-trip, no 2nd model |
| **Speech-to-text** | transcribe what the visitor says | **Whisper** (base, encoder-decoder transformer) | openai-whisper | Robust, offline, multilingual |
| **Voice-activity detection** | ignore silence/noise before Whisper | **Silero VAD** (neural), fallback = RMS energy gate | silero-vad | Whisper hallucinates on silence; VAD prevents it |
| **Translation** | visitor's language → user's | GitHub Models chat (LLM MT); local NLLB/IndicTrans2 optional | GitHub Models | Free, no heavy local MT model |
| **Wake word** | always-on "hey access" trigger | **openWakeWord** (small ONNX classifier on streaming audio) — custom "Hey Access" model trained offline from synthetic Kokoro speech (`scripts/train_wakeword.py`) | openwakeword | Pure-Python, CPU, no cloud |
| **Text-to-speech** | speak the announcement | **Kokoro** (ONNX neural TTS) → edge-tts → pyttsx3 fallback | kokoro-onnx | Offline, natural voice |
| **Intent engine** | delivery? visitor? stranger? | **Rule-based priority cascade** (pure functions) | — | Explainable, deterministic, no training data |

**One-line version to memorise:** *"InsightFace/ArcFace for who, YOLOv8 for
what, MiniFASNet for real-or-fake, Whisper for what they said, GPT-4o-mini for
describe-the-scene, Kokoro for the spoken reply — all glued by a fail-soft
pipeline."*

---

## 2. "Explain the algorithm / how it works end-to-end"

The **pipeline** (`accessai/pipeline.py`) is the core algorithm. On each ring:

1. **Capture** one frame (`camera.py`) — the single source of truth for the image.
2. **Detect + recognise faces** (run *concurrently* with object detection via a
   thread pool). Each face → a `Person`: name, confidence, age, gender, box.
   - Recognition = ArcFace embedding → cosine similarity vs. every enrolled
     embedding → best match if similarity ≥ **~0.45** threshold, else "Unknown".
3. **Liveness per face** — MiniFASNet score; a confident spoof downgrades that
   person to "Unknown" (so a held-up photo of a known person is rejected).
4. **Object detection** — YOLOv8 → people + carried objects (bag/parcel).
5. **Reconcile the head-count** (the part I just fixed — see §6): total visitors =
   recognised faces + genuine faceless bodies.
6. **VLM scene description + OCR** — for unknown visitors (and, since Phase 16,
   known ones too), one GPT-4o-mini call fills scene + any label text.
7. **Speech** (if the "Hear Visitor" button was used) — Silero VAD gates the clip,
   Whisper transcribes, translate module converts to the user's language.
8. **Intent inference** — a rule cascade (`context_engine.infer_intent`) picks a
   conservative intent (e.g. "likely delivery" if a parcel + courier text).
9. **Compose announcement** (`accessibility.py`) — mode-aware text: age only ever
   as a **range**, never a raw number; never age/gender for known people.
10. **Deliver** — TTS speaks it; the dashboard + phone PWA get it over a
    **WebSocket**; deaf mode flashes + vibrates.

Draw this as **boxes left-to-right** on the whiteboard; that diagram answers 80%
of "explain your project" questions.

---

## 3. Likely questions + crisp answers

**Q: Why ArcFace and not just the VLM / GPT-4o for recognition?**
A: VLMs are weak at *"who is this exact person."* ArcFace maps a face to a 512-d
vector where the *same* person's vectors are close and *different* people are far,
so identity is a cheap cosine similarity. It's the right tool for 1:1 matching;
the VLM is for open-ended description.

**Q: How does face matching actually decide "known"?**
A: Cosine similarity between the live embedding and each stored embedding. For
L2-normalised vectors cosine = dot product. If the best score ≥ threshold (~0.45,
tunable) → that name; otherwise "Unknown". Multiple photos per person = multiple
embeddings = more robust matching.

**Q: Why YOLOv8-nano specifically?**
A: One-stage anchor-free detector — a single forward pass gives boxes + classes,
fast enough on CPU. Nano (~6 MB) is the smallest variant; we don't need the
accuracy of larger ones for "is there a person / a bag." Confidence threshold 0.4.

**Q: How do you stop a photo of someone fooling the system?**
A: Liveness detection with MiniFASNet (Silent-Face). It scores a single frame for
real-vs-spoof. **Fail-open**: if the model is missing it returns "real" so genuine
visitors are never locked out; **fail-closed** on a confident spoof: that face is
stripped of its name.

**Q: Why does Whisper need VAD?**
A: Whisper *hallucinates* words from silence/noise. Silero VAD (a small neural
voice-activity detector) confirms there's real speech first; clips below a minimum
speech duration are dropped. Fallback is a simple RMS energy gate.

**Q: The re-ID says "histogram (11)" — what algorithm is that?**
A: Appearance re-identification for when a stranger's face isn't visible. Current
backend is an **HSV colour histogram** of the body, L2-normalised, compared by
cosine — a lightweight placeholder. The interface is identical to a proper ONNX
ReID network, so a stronger model drops in with zero pipeline change. "(11)" = 11
remembered appearance signatures.

**Q: Is the intent detection machine-learned?**
A: No — it's a **deterministic rule cascade** (pure functions, first-match-wins:
parcel + courier keywords → "likely delivery", etc.). Chosen for
explainability and because there's no labelled training data for a doorbell.

**Q: Where's the AI/cloud vs. on-device split?**
A: On-device/offline: face, object, liveness, re-ID, Whisper, VAD, wake word,
Kokoro TTS. Cloud (free GitHub Models): the VLM scene description, OCR, and
LLM translation. Everything cloud is **fail-soft** — no network just means a
shorter announcement, never a crash.

**Q: How is it real-time to the phone?**
A: A **WebSocket** (`/events`) broadcasts every doorbell event as JSON; the phone
PWA and desktop dashboard both subscribe. A keep-alive ping every 30 s holds the
socket open. The live video is **MJPEG** over `/video`.

**Q: Accessibility — how do blind vs deaf users each get the alert?**
A: Mode-aware. **Blind/Both** → the phone *speaks* (Kokoro via a `/speak_audio`
endpoint, browser speech as fallback). **Deaf/Both** → full-screen visual flash +
`navigator.vibrate` + a beep. Age is spoken only as a *range* ("in their
twenties") — never a precise number, which would be guessy and unhelpful.

**Q: What's your data model?**
A: A `VisitorEvent` dataclass is the "spine": a `people` list (one `Person` each)
plus event-level fields (visitor_count, carried_objects, scene_summary, intent,
transcript, snapshot_path…). Persisted with SQLAlchemy to SQLite; DB writes are
best-effort so a DB error never blocks the live in-memory path.

**Q: How do you enrol a new person?**
A: Upload ≥1 photos + a name → each photo → an ArcFace embedding stored under that
name. More photos = more embeddings = better recall. Names are sanitised
(`is_safe_person_name`) before touching the filesystem.

---

## 4. "Why not X?" — design-decision defence

- **Why not one big end-to-end deep model?** Doorbell needs *explainable, fail-soft*
  behaviour on a CPU. A pipeline lets each stage be swapped, skipped, or degrade
  independently — and you can point at exactly which module made each claim.
- **Why CPU-only?** Target is a cheap home device; no GPU assumption. Every model
  chosen is a small ONNX / nano variant.
- **Why hedged language everywhere?** Wrong-but-confident is dangerous for an
  accessibility user acting on the info. "Appears to be…" is honest about model
  uncertainty.
- **Why free GitHub Models for the VLM instead of local?** A local VLM that fits on
  CPU would be too weak for good descriptions; GitHub Models gives an
  OpenAI-compatible vision endpoint free, and it's fully optional.

---

## 5. Metrics / thresholds worth knowing (they *will* ask numbers)

- Face match threshold: **~0.45** cosine.
- YOLO detection confidence: **0.4**; extra-person counting: **0.6** (stricter, see §6).
- Anti-spoof min live score: **0.55**.
- Re-ID match threshold: **0.75**.
- Wake-word threshold: **0.5**.
- Face embedding dim: **512**. buffalo_l pack ≈ **300 MB** (one-time download).
- YOLOv8n ≈ **6 MB**. Whisper base ≈ **74 M params**.

---

## 6. The multi-person counting fix (fresh, good to mention)

**Problem shown in demo:** one real person (Vinay) was announced as "Multiple
people at the door — 1 other person," because a spurious low-confidence YOLO
person box (0.47) was counted as a second visitor.

**Root cause:** the old code did `extra_people = person_count − face_count`,
counting *every* person box at the 0.4 threshold — including duplicates and weak
false positives.

**Fix (`vision_module.count_extra_people`):** an "extra" visitor is now only a
person box that (a) clears a **stricter 0.6 confidence** gate, (b) is **not an IoU
duplicate** of a body already counted, and (c) does **not geometrically contain an
already-recognised face** (a body wrapping a known face is that same person). So
`visitor_count = recognised faces + genuine faceless bodies`. Single real person →
count 1. A genuinely turned-away second person still counts.

This is a nice thing to volunteer — it shows you understand **IoU, confidence
thresholding, and precision-vs-recall trade-offs**.

---

## 7. Buzzwords to have ready (with a one-line meaning each)

- **Embedding / feature vector** — a fixed-length numeric fingerprint of an input.
- **Cosine similarity** — angle between two vectors; 1 = identical direction.
- **ArcFace** — additive-angular-margin loss that makes face embeddings very
  separable by identity.
- **IoU (Intersection-over-Union)** — overlap ratio of two boxes; used for dedup.
- **One-stage detector** — predicts boxes + classes in a single pass (YOLO).
- **VAD** — voice-activity detection; finds speech in audio.
- **VLM** — vision-language model; describes images in words.
- **Fail-soft / fail-open / fail-closed** — degrade gracefully; on failure default
  to *allow* (liveness) or *deny* (spoof) depending on safety.
- **PWA** — installable web app that works offline via a service worker.
