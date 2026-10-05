"""
AccessAI - Central configuration (the single source of truth).

Every tunable value and feature flag for the whole project lives here.
Later phases only FLIP flags and adjust values in this file; they never
scatter configuration across modules.

ESP32-CAM one-line swap
-----------------------
Right now CAMERA_SOURCE is `0` (your laptop's built-in webcam). When you later
wire up an ESP32-CAM, you change EXACTLY ONE LINE:

    CAMERA_SOURCE = "http://192.168.1.50:81/stream"

OpenCV's VideoCapture accepts an integer index OR an MJPEG URL string, so the
rest of the codebase does not change at all. That is the whole point of routing
every frame through accessai/camera.py.

Target environment: Python 3.12, Linux, CPU-only.
"""

import os

# ---------------------------------------------------------------------------
# Paths (all absolute, derived from this file's location)
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
KNOWN_FACES_DIR = os.path.join(DATA_DIR, "known_faces")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
WEB_DIR = os.path.join(BASE_DIR, "web")
DB_PATH = os.path.join(DATA_DIR, "accessai.db")

# Create the data directories at import time so the rest of the app never has to.
for _p in (DATA_DIR, KNOWN_FACES_DIR, HISTORY_DIR):
    os.makedirs(_p, exist_ok=True)

# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------
# 0        -> default laptop webcam            (use NOW)
# 1, 2...  -> external USB webcams
# "http://<esp32-ip>:81/stream" -> ESP32-CAM MJPEG stream (use LATER)
#
# Switching to the ESP32 is a ONE-LINE change here. Nothing else changes,
# because all frame access goes through accessai/camera.py.
CAMERA_SOURCE = 0
FRAME_WIDTH = 1280
FRAME_HEIGHT = 720

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
HOST = "0.0.0.0"
PORT = 8000

# --- Security (Phase 17) ---------------------------------------------------
# AUTH_TOKEN: a shared bearer token protecting EVERY sensitive route (camera
# stream, mic, history, enrollment, mode...). Set it in .env:
#     ACCESSAI_TOKEN=some-long-random-string
# Clients send it as  Authorization: Bearer <token>  - or, for MJPEG <img> tags
# and the WebSocket (where headers can't be set), as  ?token=<token>.
# EMPTY = auth disabled (open LAN appliance, the pre-Phase-17 behaviour). The
# boot self-check and /status warn loudly when the server is reachable beyond
# localhost with auth off.
AUTH_TOKEN = os.environ.get("ACCESSAI_TOKEN", "").strip()
# CORS: which *browser origins* may call the API cross-origin. This does NOT
# affect the dashboard/PWA (served by this server, so same-origin) or the
# Flutter mobile app (a native client - it sends no Origin header and CORS
# never applies). It only governs a page on some OTHER origin scripting this
# server, which is exactly the drive-by risk on a shared LAN.
#
# Default is the dev origins for `flutter run -d chrome` plus localhost. Widen
# it with a comma-separated env var if you serve a UI from elsewhere:
#     ACCESSAI_CORS_ORIGINS=http://192.168.1.20:3000,http://myhost:8080
# Setting it to "*" restores the old wildcard (fine with AUTH_TOKEN set, since
# the token gates every route and allow_credentials stays False - but with auth
# OFF the wildcard lets any page on the LAN drive your camera and mic).
_cors_env = os.environ.get("ACCESSAI_CORS_ORIGINS", "").strip()
CORS_ORIGINS = ([o.strip() for o in _cors_env.split(",") if o.strip()]
                if _cors_env else
                ["http://localhost", "http://localhost:8000",
                 "http://127.0.0.1", "http://127.0.0.1:8000"])
# Rate limit for the pipeline-driving routes (/trigger, /ring, /ask, /listen,
# /hear_visitor): each call can burn CPU and a paid cloud VLM request. Token
# bucket per client IP: burst of RATE_BURST, refilling RATE_PER_MIN per minute.
RATE_PER_MIN = 12
RATE_BURST = 4
# HMAC signing for the ESP32 /ring webhook. When RING_HMAC_SECRET is non-empty
# (set ACCESSAI_RING_SECRET in .env), /ring REQUIRES the X-Ring-Signature
# header = hex(HMAC_SHA256(secret, raw request body)) - an empty body signs the
# empty string. This authenticates hardware doorbell presses even on an open
# LAN, independently of AUTH_TOKEN (the ESP32 never holds the user token).
RING_HMAC_SECRET = os.environ.get("ACCESSAI_RING_SECRET", "").strip()

# Push notifications (docs/MOBILE_PUSH.md - Phase 17 scaffolding). OFF by
# default: /register_push accepts + stores device tokens either way (additive,
# harmless), but the event-time sender only runs when ENABLE_PUSH is True AND
# FCM credentials exist. Point FCM_CREDENTIALS_JSON at a Firebase service-
# account JSON and set FCM_PROJECT_ID to light up real delivery; until then a
# registered token is simply remembered and the sender logs a one-line hint.
ENABLE_PUSH = False
FCM_PROJECT_ID = os.environ.get("ACCESSAI_FCM_PROJECT", "").strip()
FCM_CREDENTIALS_JSON = os.environ.get("ACCESSAI_FCM_CREDENTIALS", "").strip()

# --- Phase 17: accessibility conveniences ----------------------------------
# Canned quick replies: one tap speaks the sentence at the door (much faster
# than typing for a deaf user answering under time pressure). Shown as buttons
# next to the free-text reply box on the dashboard, PWA, and mobile app.
QUICK_REPLIES = [
    "Please leave the package at the door.",
    "One minute, I am coming.",
    "Please wait.",
    "Not interested, thank you.",
    "Please come back later.",
]

# Auto-greeting: when an UNKNOWN visitor (or unrecognised delivery) rings, the
# doorbell itself asks them to state their name and purpose, then listens for
# a few seconds and attaches the transcription to the event - no manual "Hear
# Visitor" press needed. Opt-in: it records a stranger's voice automatically,
# which is a privacy decision the user must make (the spoken prompt itself
# announces the recording). Known visitors are never auto-interrogated.
ENABLE_AUTO_GREETING = True
AUTO_GREETING_TEXT = ("Hello. The resident will be with you shortly. "
                      "Please state your name and the purpose of your visit "
                      "after the tone.")
AUTO_GREETING_LISTEN_SECONDS = 6

# Smart-home alert webhook: POST a small JSON to this URL on every doorbell
# event, so room lights (Home Assistant / Hue bridge / any relay) can flash a
# colour a deaf user sees anywhere in the house. "" = off. Payload:
#   {"kind": "known|delivery|unknown|spoof", "color": "#RRGGBB",
#    "name": "...", "announcement": "..."}
# Fire-and-forget with a short timeout - it can never delay the doorbell.
ALERT_WEBHOOK_URL = os.environ.get("ACCESSAI_ALERT_WEBHOOK", "").strip()
ALERT_WEBHOOK_COLORS = {
    "known": "#16a34a",      # green  - a recognised person
    "delivery": "#f59e0b",   # amber  - likely delivery
    "unknown": "#2563eb",    # blue   - unknown visitor
    "spoof": "#dc2626",      # red    - possible spoof / warning
}

# ---------------------------------------------------------------------------
# Accessibility
# ---------------------------------------------------------------------------
# "blind" -> voice only | "deaf" -> visual/text only | "both" -> both
ACCESSIBILITY_MODE = "both"
USER_LANGUAGE = "ml"       # ISO code: en, hi, ml, ta, kn, te, bn, ...
                           #   Malayalam: a foreign/English visitor's speech is
                           #   translated INTO Malayalam for the user. Overridable
                           #   at runtime via POST /user_language (persisted to
                           #   data/user_language.txt, reloaded on next boot).

# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------
# Later phases flip these on one at a time. Keeping them here (even unused)
# means later phases only change a value; they never add a new place to look.
ENABLE_FACE = True         # Phase 2  - InsightFace recognition  (LIVE)
ENABLE_VISION = True       # Phase 3  - YOLOv8 object/scene detection  (LIVE)
ENABLE_ANTISPOOF = True    # Phase 5  - face liveness / anti-spoofing  (LIVE)
ENABLE_VLM = True          # Phase 6  - vision-language scene description  (LIVE)
ENABLE_OCR = True          # Phase 6  - parcel-label text reading  (LIVE)
ENABLE_SPEECH = True       # Phase 7  - Whisper speech recognition  (LIVE)
ENABLE_TRANSLATE = True    # Phase 8  - multi-language translation  (LIVE)
ENABLE_REID = True         # Phase 9  - visitor re-identification  (LIVE)
ENABLE_AUTOENROLL = True    # Phase 9  - auto-enrollment of frequent unknowns  (LIVE)
ENABLE_WAKEWORD = True     # Phase 10 - wake word + voice commands  (LIVE)
ENABLE_MOTION = True       # Phase 17 - software motion trigger (trigger='motion').
                           #   ON: the doorbell notices someone approaching instead
                           #   of waiting for a button press. NOTE this rings the
                           #   FULL pipeline, so it belongs on a doorway camera - on
                           #   a desk webcam it will fire on every passer-by. Set
                           #   back to False (or raise MOTION_MIN_AREA) if you are
                           #   developing in front of the camera.

# Software motion detector tuning (accessai/motion_module.py). The detector
# samples the shared latest-frame a few times a second and fires when at least
# MOTION_MIN_AREA of the (downscaled) frame changes for MOTION_CONSECUTIVE
# samples in a row. After a fire - or any doorbell/manual trigger - motion is
# suppressed for MOTION_COOLDOWN seconds so one visitor isn't announced twice.
MOTION_MIN_AREA = 0.02       # fraction of pixels that must change (2%)
MOTION_CONSECUTIVE = 3       # samples in a row before firing (rejects flicker)
MOTION_COOLDOWN = 30         # seconds of silence after a fire / ring
MOTION_INTERVAL = 0.3        # seconds between samples (~3/s, sub-ms each)
MOTION_WARMUP = 5            # seconds after boot before the first fire

# ---------------------------------------------------------------------------
# Face recognition (Phase 2 - InsightFace)
# ---------------------------------------------------------------------------
# buffalo_l is a model pack containing a face DETECTOR + an ArcFace RECOGNISER.
# On first use it downloads ~300 MB to ~/.insightface/ (one time).
FACE_MODEL_NAME = "buffalo_l"     # InsightFace model pack (det + ArcFace)
FACE_DET_SIZE = (640, 640)        # detector input size
# Cosine similarity of normed embeddings is a dot product. Same-person pairs
# usually score > 0.5, different people < 0.3. 0.42 safely accommodates close-ups
# and varied lighting without false accepts.
FACE_MATCH_THRESHOLD = 0.42       # higher = stricter (fewer false accepts)
FACE_MIN_DET_SCORE = 0.5          # ignore very low-confidence face detections
FACE_CTX_ID = -1                  # -1 = CPU, 0 = first GPU

# ---------------------------------------------------------------------------
# Vision / Object detection (Phase 3 - YOLOv8)
# ---------------------------------------------------------------------------
# The nano model is fast and CPU-friendly. yolov8n.pt (~6 MB) auto-downloads to
# the working directory the first time predict() runs (one time).
YOLO_MODEL = "yolov8n.pt"     # nano = fast, CPU-friendly; auto-downloads once
YOLO_CONF = 0.4               # detection confidence threshold
# Counting an EXTRA, unrecognised visitor ("N other people") is announced to the
# user, so it must be high-precision: a weak person box (a pillow, a reflection,
# a phone) must NOT become a phantom second visitor. Extra people are counted only
# above this stricter confidence AND only when their body box does not already
# contain a recognised face. Keep >= YOLO_CONF.
YOLO_EXTRA_PERSON_CONF = 0.6  # min confidence to count a faceless body as a person
# COCO has NO native "parcel/box" class. These are the carried items we treat as
# delivery-ish; Phase 6 OCR refines "is this actually a courier parcel".
# "book" is included because small boxes are frequently detected as book/handbag
# by COCO models; wording stays conservative ("a package").
PARCEL_LABELS = {"backpack", "handbag", "suitcase", "book"}

# ---------------------------------------------------------------------------
# Anti-spoofing / Liveness (Phase 5 - Silent-Face / MiniFASNet)
# ---------------------------------------------------------------------------
# The liveness check downgrades a matched-but-spoofed face (a printed photo or a
# phone screen) to Unknown BEFORE it is announced, so nobody can impersonate a
# known person with a picture.
#
# Backend selection (identical interface, priority A -> B -> C):
#   A "silent-face-pip"  - a pip wrapper around Silent-Face, if installable on 3.12
#   B "onnx-minifasnet"  - two MiniFASNet .onnx models (~2 MB total) placed in
#                          ANTISPOOF_MODEL_DIR, run with onnxruntime (a Phase-2 dep)
#   C "heuristic"        - a laplacian/texture PLACEHOLDER (NOT production-grade;
#                          logs a loud warning). Used only if A and B are absent.
ANTISPOOF_MODEL_DIR = os.path.join(BASE_DIR, "models", "antispoof")
# "real" score >= this => treated as a live person; below => spoof (identity
# downgraded to Unknown). RAISING it is STRICTER: fewer spoofs accepted, but more
# genuine faces may be rejected in poor light. 0.55 is a balanced default.
ANTISPOOF_MIN_SCORE = 0.55
# "auto" picks A -> B -> C automatically; force with "onnx" or "heuristic".
ANTISPOOF_BACKEND = "auto"

# ---------------------------------------------------------------------------
# VLM scene description + OCR (Phase 6 - cloud vision, OpenAI-compatible)
# ---------------------------------------------------------------------------
# One cloud call describes the scene for a blind listener and transcribes any
# visible parcel-label text. If no keys are set or every key fails, the app
# runs on YOLO-only signals - it NEVER crashes and NEVER blocks the doorbell.
#
# PRIMARY PROVIDER: OpenRouter / Qwen3.8 27B (free, fast, excellent vision).
#   API key: set OPENROUTER_API_KEY in .env
#   Model: configurable via OPENROUTER_MODEL env var (default qwen/qwen3.8-27b:free)
#
# FALLBACK PROVIDER: Gemini (tried automatically when OpenRouter fails).
#   API key: set GEMINI_API_KEY in .env
#   Model chain: gemini-3.6-flash → gemini-3.5-flash-lite → gemini-3.1-flash-lite
#
# If both providers fail, the pipeline runs on YOLO-only signals (never crashes).
#

# --- OpenRouter (PRIMARY VLM) ------------------------------------------------
# Set OPENROUTER_API_KEY in .env. Do NOT hardcode real keys here.
OPENROUTER_API_KEY = ""                       # keep empty; use .env instead
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "qwen/qwen3.8-27b:free")

# --- Gemini (FALLBACK VLM) ---------------------------------------------------
VLM_API_KEYS = ""                             # keep empty; use .env instead
VLM_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
VLM_MODEL = "gemini-3.6-flash"                 # Gemini primary fallback model

# --- Shared VLM settings -----------------------------------------------------
# VLM_TIMEOUT_SECONDS env var overrides the default. Short timeouts ensure fast
# failover: a slow/dead provider falls over quickly, never stalling the doorbell.
VLM_TIMEOUT = int(os.environ.get("VLM_TIMEOUT_SECONDS", "8"))
VLM_MAX_TOKENS = 1200
VLM_TEMPERATURE = 0.0                         # 0 = most factual/repeatable
VLM_ONLY_FOR_UNKNOWN = False                  # describe KNOWN people too
VLM_COOLDOWN = 8                              # Minimum seconds between VLM calls (bypassed on scene change)
# Phase 12 (SPEED): SPEAK a FAST local announcement first, then run the richer
# VLM appearance call in a BACKGROUND thread and update the stored event +
# dashboard + history when it returns. The doorbell is NEVER blocked by the VLM.
VLM_ASYNC_ENRICH = True
# Phase 12 (RICHNESS): when the background VLM enrich returns, SPEAK the details
# as a short follow-up utterance. Only in blind/both mode.
VLM_ENRICH_SPEAK = True
# Follow-up mode: speak delta-only (the full visual scene description)
# rather than repeating the instant opening announcement twice.
VLM_ENRICH_SPEAK_FULL = False
VLM_JPEG_QUALITY = 80                         # frame is re-encoded before upload
VLM_MAX_IMAGE_WIDTH = 768                     # downscale wide frames to save tokens

# Courier / delivery keywords. When OCR text (Phase 6) contains one of these AND
# a parcel-like object was detected, the context engine upgrades intent to
# "likely delivery" with higher confidence. Passed INTO infer_intent so the
# context engine stays a pure function with no config import.
COURIER_KEYWORDS = [
    "fedex", "dhl", "ups", "amazon", "usps", "bluedart", "delhivery",
    "dtdc", "ekart", "shiprocket", "courier", "parcel", "package",
    "delivery", "prime", "flipkart",
]

# ---------------------------------------------------------------------------
# Speech recognition (Phase 7 - Whisper + Silero VAD)
# ---------------------------------------------------------------------------
# On a doorbell press we (optionally) record a few seconds of microphone audio,
# gate it with Voice Activity Detection so we don't transcribe silence, and run
# Whisper OFFLINE on the CPU. The transcript is added to the event, spoken in the
# ' They said: "..."' tail (Blind), and shown as a caption (Deaf).
#
# 16 kHz MONO float32 is what BOTH Whisper and Silero expect - and feeding
# Whisper a numpy array (not a file) means we DON'T need ffmpeg at runtime.
#
# Everything degrades: no mic / no libs / no speech => empty transcript and the
# event proceeds exactly as Phase 6.
SPEECH_SECONDS = 5              # blind user's voice-command / wake-word capture
SPEECH_SAMPLE_RATE = 16000      # 16 kHz mono - Whisper + Silero native rate
WHISPER_MODEL = "base"          # tiny | base | small (bigger = slower/accurate).
                                #   Phase 12: the doorbell no longer touches Whisper
                                #   at all; only /hear_visitor + /listen do. Drop to
                                #   "tiny" for ~2x faster (less accurate) transcripts.
WHISPER_LANGUAGE = None         # None = auto-detect (feeds Phase 8); or "en"/"hi"
SPEECH_VAD = True               # gate transcription on detected speech
SPEECH_VAD_MIN_SPEECH_SEC = 0.3 # ignore clips with less speech than this
# Phase 12 (UX): the doorbell must NOT eavesdrop. /trigger does ZERO audio work;
# the visitor's voice is captured ONLY when the user presses "Hear Visitor"
# (POST /hear_visitor). Leaving this False is the whole point - do not flip it on.
SPEECH_CAPTURE_ON_TRIGGER = False  # /trigger never records (two-way is opt-in)
# How long POST /hear_visitor records the visitor when the user asks to listen.
VISITOR_LISTEN_SECONDS = 6

# ---------------------------------------------------------------------------
# Multi-language + Translation (Phase 8)
# ---------------------------------------------------------------------------
# The visitor may speak ANY language (language_detected comes from Whisper). The
# blind/deaf USER consumes ONE chosen language: USER_LANGUAGE. This layer
# translates visitor -> user so the announcement is spoken (Blind) and captioned
# (Deaf) in a language the user understands. Everything degrades: no translator /
# same language / failure => the original transcript is used unchanged.
#
# USER_LANGUAGE (defined in the Accessibility section above) is the target code.
#
# Backend priority (all behind the SAME TranslateModule interface):
#   "auto"   -> PREFERRED: tries fast Groq LLM (20ms, multilingual) if keys
#               present in .env, with automatic failover to VLM / passthrough.
#   "groq"   -> Groq cloud LLM (qwen/qwen3.8-27b). Free tier, ultra-fast, torch-free.
#   "github" -> VLM chat completion. Adds NO dependency, never moves torch.
#   "none"   -> passthrough: return the original text unchanged (honest fallback).
TRANSLATE_BACKEND = "auto"
# ISO code -> human name, used both in the translation prompt and the UI selector.
LANGUAGE_NAMES = {
    "en": "English", "hi": "Hindi", "ml": "Malayalam", "ta": "Tamil",
    "te": "Telugu", "kn": "Kannada", "bn": "Bengali", "mr": "Marathi",
    "gu": "Gujarati", "pa": "Punjabi", "ur": "Urdu",
    "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
    "ar": "Arabic", "ja": "Japanese", "zh": "Chinese",
}
# When True (or when USER_LANGUAGE != "en"), translate the announcement into
# USER_LANGUAGE so the blind/deaf user hears and sees the entire message in
# their native language with a natural human accent.
TRANSLATE_ANNOUNCEMENT = True

# Per-language ANNOUNCEMENT voices. When a non-English sentence is spoken,
# the TTS worker picks the matching Microsoft Edge Neural voice below instead
# of reading foreign text with English phonemes. Edge Neural voices feature
# authentic native accents, natural human cadence, and realistic intonation.
LANGUAGE_VOICES = {
    "hi": "hi-IN-SwaraNeural",
    "hi-in": "hi-IN-SwaraNeural",
    "ml": "ml-IN-SobhanaNeural",
    "ml-in": "ml-IN-SobhanaNeural",
    "ta": "ta-IN-PallaviNeural",
    "ta-in": "ta-IN-PallaviNeural",
    "te": "te-IN-ShrutiNeural",
    "te-in": "te-IN-ShrutiNeural",
    "kn": "kn-IN-SapnaNeural",
    "kn-in": "kn-IN-SapnaNeural",
    "bn": "bn-IN-TanishaaNeural",
    "bn-in": "bn-IN-TanishaaNeural",
    "mr": "mr-IN-AarohiNeural",
    "mr-in": "mr-IN-AarohiNeural",
    "gu": "gu-IN-DhwaniNeural",
    "gu-in": "gu-IN-DhwaniNeural",
    "pa": "pa-IN-OjasNeural",
    "pa-in": "pa-IN-OjasNeural",
    "ur": "ur-IN-GulNeural",
    "ur-in": "ur-IN-GulNeural",
    "es": "es-ES-ElviraNeural",
    "es-es": "es-ES-ElviraNeural",
    "fr": "fr-FR-DeniseNeural",
    "fr-fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "de-de": "de-DE-KatjaNeural",
    "it": "it-IT-ElsaNeural",
    "it-it": "it-IT-ElsaNeural",
    "ar": "ar-SA-ZariyahNeural",
    "ar-sa": "ar-SA-ZariyahNeural",
    "ja": "ja-JP-NanamiNeural",
    "ja-jp": "ja-JP-NanamiNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "zh-cn": "zh-CN-XiaoxiaoNeural",
}

# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------
EVENT_COOLDOWN = 8         # seconds between announcements (used from Phase 4)
HISTORY_LIMIT = 200        # max events shown on the history page

# ---------------------------------------------------------------------------
# Visitor Re-Identification (Phase 9 - appearance re-ID of repeat UNKNOWNS)
# ---------------------------------------------------------------------------
# For every UNKNOWN, non-spoof visitor we compute an APPEARANCE embedding from
# their body crop (the largest YOLO "person" box) and match it against a rolling
# 24h gallery. A recurrence bumps reid_seen_count, so the announcement can say
# "The same unknown visitor has come 3 times today." (that phrasing already lives
# in accessibility.compose_announcement, keyed on reid_seen_count >= 2).
#
# Backend selection (identical interface, so a stronger model drops in later):
#   "auto"      -> ONNX OSNet if a .onnx sits in REID_MODEL_DIR, else histogram
#   "onnx"      -> force the ONNX OSNet re-ID model (torch-free, via onnxruntime)
#   "histogram" -> force the PLACEHOLDER: an L2-normalised HSV colour histogram
#                  (global 8x8x8 + upper/lower-body 4x4x4) of the body crop. This
#                  is torch-free and always works, but is NOT as robust as OSNet -
#                  it keys mostly on clothing colour. Logged LOUDLY as a placeholder
#                  (same pattern as the Phase-5 anti-spoof heuristic).
REID_BACKEND = "auto"
REID_MODEL_DIR = os.path.join(BASE_DIR, "models", "reid")
# Cosine similarity (dot of L2-normalised vectors) at/above which two sightings
# are called the SAME person. RAISE it to merge fewer (stricter), LOWER to merge
# more.
#
# 0.90, not the old 0.75, because the OSNet feature is taken AFTER the final
# ReLU: every dimension is >= 0, so two unrelated crops already sit around
# 0.6 cosine by construction. On the 33 dev snapshots (desk webcam) unrelated
# people scored a median 0.59 and a max 0.91, so 0.75 would have merged most
# strangers into one identity. The error this setting prefers is "a new
# stranger" (harmless - the announcement just says first visit) over "the same
# visitor again" (actively wrong).
#
# NOT YET CALIBRATED ON REAL FOOTAGE. OSNet is trained on full-body crops with a
# ~2:1 height:width aspect; the dev webcam only ever produced upper-torso crops
# at ~0.72, which is out of distribution, and on that data the model could not
# separate two known people at all (AUC ~0.45, i.e. chance). That is a property
# of the sample, not a bug - it is the doorway camera (ESP32-CAM, full-body
# framing) this model is for. Re-tune on a day of real doorway snapshots before
# trusting the "seen N times today" count.
REID_MATCH_THRESHOLD = 0.90
# Only match against sightings seen within this window; also defines "today" for
# the "N times today" announcement and bounds how long a stranger is remembered.
REID_GALLERY_TTL_HOURS = 24
REID_MAX_GALLERY = 500     # cap stored embeddings (evict oldest beyond this)

# ---------------------------------------------------------------------------
# Auto-Enrollment (Phase 9 - cluster frequent unknown FACES, suggest saving)
# ---------------------------------------------------------------------------
# Unknown FACE embeddings (from InsightFace, the same 512-D ArcFace vectors used
# for recognition) are accumulated and clustered with DBSCAN on COSINE distance.
# When a cluster of the same face grows to AUTOENROLL_SUGGEST_AFTER sightings, the
# UI surfaces a "Save this visitor?" prompt so the user can promote them to a
# known person WITHOUT manually registering a photo. DBSCAN runs periodically /
# lazily (never on every trigger - it is O(n^2) over the accumulated faces).
AUTOENROLL_EPS = 0.35          # DBSCAN eps on (1 - cosine) distance between faces
AUTOENROLL_MIN_SAMPLES = 3     # DBSCAN min_samples to form a cluster core
AUTOENROLL_SUGGEST_AFTER = 5   # cluster size that triggers a "save this?" prompt

# ---------------------------------------------------------------------------
# Wake word + Voice commands (Phase 10 - hands-free Blind Mode)
# ---------------------------------------------------------------------------
# The final phase makes the doorbell hands-free for a blind user. Two paths,
# both reuse the SAME building blocks (Phase-7 SpeechModule to capture, Phase-4
# TTS to answer, the pipeline/db for facts) - they never duplicate them:
#
#   PUSH-TO-TALK  (always available)  -> POST /listen records one command, parses
#                 it, acts, and speaks the answer. The dashboard "Speak a command"
#                 button hits this. Works even if openWakeWord is not installed.
#
#   ALWAYS-ON     (OPT-IN, default OFF) -> WakeWordModule keeps the mic open and
#                 listens for WAKEWORD_MODEL. On a detection it runs the SAME
#                 /listen interaction automatically. It is off by default on
#                 purpose: an always-open mic costs CPU and is a privacy choice
#                 the user must make deliberately (toggle in the dashboard, or set
#                 WAKEWORD_ALWAYS_ON = True here).
#
# Detector: openWakeWord (pure-python, onnxruntime, CPU). Model priority (same
# drop-in auto-upgrade pattern as antispoof / re-ID):
#   A CUSTOM   -> the first *.onnx in WAKEWORD_MODEL_DIR (models/wakeword/).
#                 Build it OFFLINE with `scripts/train_wakeword.py` - it
#                 synthesizes "Hey Access" with the Phase-11 Kokoro voices,
#                 trains a tiny classifier on openWakeWord's frozen embeddings,
#                 and exports hey_access.onnx. No longer a placeholder.
#   B PRETRAINED -> openWakeWord's shipped phrases (hey_jarvis, alexa, ...)
#                 auto-download on first use; used as a loudly-logged
#                 PLACEHOLDER phrase until the custom model exists.
# If openWakeWord can't be imported, always-on degrades to unavailable and
# push-to-talk still works - the app never crashes.
WAKEWORD_MODEL_DIR = os.path.join(BASE_DIR, "models", "wakeword")
WAKEWORD_MODEL = "hey_jarvis"     # pretrained FALLBACK phrase (used only when no custom .onnx exists)
WAKEWORD_THRESHOLD = 0.40         # 0-1 detection score; 0.40 gives high sensitivity with low false alarm rate
WAKEWORD_COMMAND_SECONDS = 4.5    # seconds ceiling for command capture (VAD stops early on 0.7s silence)
WAKEWORD_ALWAYS_ON = True         # start the always-listening mic at boot ("hey access"); toggle off in the dashboard
WAKEWORD_COOLDOWN = 3.5           # min seconds between two wake detections (debounce)
WAKEWORD_INFERENCE_FRAMEWORK = "onnx"  # openWakeWord backend: "onnx" (installed) | "tflite"

# ---------------------------------------------------------------------------
# Text-to-speech / Accessibility output (Phase 4 base + Phase 11 natural voice)
# ---------------------------------------------------------------------------
# ACCESSIBILITY_MODE decides whether we speak ("blind"), show big text ("deaf"),
# or both. Without any TTS backend, spoken output is skipped gracefully and the
# announcement still appears as text in the UI.
ENABLE_TTS = True          # master switch for spoken output
TTS_RATE = 165             # words per minute (pyttsx3 last-resort backend)
TTS_VOLUME = 1.0           # 0.0-1.0 (pyttsx3 last-resort backend)
TTS_VOICE = ""             # "" = system default; else a pyttsx3 voice id

# --- Phase 11: NATURAL, human-like voice -----------------------------------
# The doorbell should sound like a person, not a 1990s robot. Three backends
# sit behind the SAME TTSModule interface, tried in this fallback order:
#   "kokoro"  -> Kokoro-ONNX, OFFLINE, private, onnxruntime (NOT torch). DEFAULT.
#   "edge"    -> edge-tts, Microsoft Neural voices, ONLINE (pure HTTP, no torch).
#   "pyttsx3" -> the Phase-4 espeak path; last resort so we are NEVER silent.
# Everything degrades: a missing package, a missing model download, or a headless
# box with no audio device all fall through gracefully (text still shown).
#
# TORCH SAFETY: kokoro-onnx + edge-tts are both torch-free (that is why we chose
# the ONNX build over the standard torch-based `kokoro` package). The pinned
# torch 2.4.1 - and YOLO - stay intact.
TTS_ENGINE = "kokoro"                               # "kokoro" | "edge" | "pyttsx3"
TTS_MODEL_DIR = os.path.join(BASE_DIR, "models", "kokoro")
# The Kokoro model files (kokoro-v1.0.onnx ~310MB + voices-v1.0.bin ~26MB) live
# in TTS_MODEL_DIR. They download ONCE from the kokoro-onnx GitHub releases (URLs
# in requirements.txt). Offline Kokoro is the private default; nothing leaves the
# machine once the files are present.
KOKORO_VOICE = "af_heart"     # default warm female voice; changeable in-app
KOKORO_SPEED = 1.0            # 0.5-2.0 (1.0 = natural pace)
KOKORO_LANG = "en-us"        # Kokoro phoneme language
EDGE_VOICE = "en-US-AriaNeural"    # natural online fallback; en-IN-NeerjaNeural = India
EDGE_RATE = "+0%"            # edge-tts rate delta, e.g. "-10%" / "+10%"

# Voices offered in the dashboard picker (id "engine:voice" -> human label). The
# app marks which are actually available (offline Kokoro needs the model files;
# online edge voices need internet at speak time).
VOICE_CHOICES = [
    {"id": "kokoro:af_heart",   "label": "Heart (female, warm) — offline"},
    {"id": "kokoro:af_bella",   "label": "Bella (female) — offline"},
    {"id": "kokoro:af_sarah",   "label": "Sarah (female, neutral) — offline"},
    {"id": "kokoro:af_nicole",  "label": "Nicole (female, soft) — offline"},
    {"id": "kokoro:am_michael", "label": "Michael (male) — offline"},
    {"id": "edge:en-US-AriaNeural",   "label": "Aria (female) — online neural"},
    {"id": "edge:en-IN-NeerjaNeural", "label": "Neerja (female, Indian) — online"},
    {"id": "edge:hi-IN-SwaraNeural",  "label": "Swara (Hindi female) — online"},
]

# ── Phase 18: Event Video Clip Recorder ──────────────────────────────────────
ENABLE_VIDEO_CLIPS  = True          # set False to disable all clip recording
VIDEO_CLIPS_DIR     = "data/clips"  # directory to save MP4 clips + JSON metadata
VIDEO_CLIPS_FPS     = 15            # frames per second in saved clips
VIDEO_PRE_ROLL_SEC  = 10            # seconds of footage BEFORE detection
VIDEO_POST_ROLL_SEC = 8             # seconds of footage AFTER person disappears
VIDEO_MIN_EVENT_SEC = 1.0           # minimum event length to save a clip
VIDEO_RETAIN_DAYS   = 7             # auto-delete clips older than N days
VIDEO_MAX_CLIPS     = 500           # hard cap on total saved clips

# ── Phase 19: Best-Frame Selector (motion → observation → best frame) ────────
# When motion is confirmed the selector captures frames for up to WINDOW_SEC
# seconds at SAMPLE_FPS, scores each on sharpness/person/face/exposure/stability
# and picks the single best frame for the expensive AI pipeline.  If no frame
# passes MIN_QUALITY the motion event is silently discarded.
FRAME_SELECT_WINDOW_SEC       = 2.5     # max observation window (seconds)
FRAME_SELECT_SAMPLE_FPS       = 10.0    # capture rate inside the window
FRAME_SELECT_MIN_SHARPNESS    = 50.0    # Laplacian variance floor
FRAME_SELECT_MIN_PERSON_CONF  = 0.35    # YOLO person-detection conf floor
FRAME_SELECT_MIN_FACE_CONF    = 0.3     # face-detection conf floor
FRAME_SELECT_MIN_EXPOSURE     = 30      # mean pixel value floor (reject dark)
FRAME_SELECT_MAX_EXPOSURE     = 230     # mean pixel value ceiling (reject bright)
FRAME_SELECT_STABILITY_WINDOW = 0.5     # seconds person must be ~stable for early exit
FRAME_SELECT_MIN_QUALITY      = 0.3     # composite score floor to accept a frame
FRAME_SELECT_EARLY_EXIT       = 0.75    # composite score to trigger early exit

# ── VLM Multi-Model Fallback ─────────────────────────────────────────────────
# Gemini fallback models (tried after primary OpenRouter/Qwen fails).
# All use the same GEMINI_API_KEY and base URL — they have independent quota
# pools so a rate-limited main model falls over instantly.
VLM_FALLBACK_MODELS = [
    "gemini-3.5-flash-lite",     # ✅ tested working, separate quota
    "gemini-3.1-flash-lite",     # ✅ tested working, separate quota
    "gemini-flash-lite-latest",  # ✅ tested working, always latest lite
]

# Gemini is now assembled as an extra_provider at runtime in run.py.
# VLM_EXTRA_PROVIDERS is kept for additional custom providers if needed.
VLM_EXTRA_PROVIDERS = []
