"""
AccessAI - main entrypoint.

Starts:
  1. A daemon camera-capture thread (writes the newest frame to LatestFrame).
  2. The FastAPI server on http://<HOST>:<PORT>.

The camera thread never crashes the app: if the device can't be opened or a read
fails, it logs, waits, and retries. So `python3 run.py` always brings up the
dashboard - even with no webcam attached.

Usage:
    python3 run.py
"""

import os
import time
import threading
import traceback
import warnings

# Suppress InsightFace's FutureWarning about the deprecated skimage.transform
# .estimate() API — it fires on every pipeline event and is not actionable by
# the user (it's inside the insightface library itself).
warnings.filterwarnings("ignore", category=FutureWarning, module="insightface")

import uvicorn

# Phase 6: load a local .env (GITHUB_MODELS_KEYS=...) BEFORE importing config so
# os.environ is populated. Degrades gracefully if python-dotenv isn't installed.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception as _e:                                   # pragma: no cover
    print(f"[AccessAI] python-dotenv not installed, skipping .env load: {_e}")

import config
from accessai.camera import Camera
from accessai.database import Database
from accessai.face_module import FaceModule
from accessai.vision_module import VisionModule
from accessai.antispoof import AntiSpoofModule
from accessai.tts_module import TTSModule
from accessai.vlm_module import VLMModule
from accessai.ocr_module import OCRModule
from accessai.speech_module import SpeechModule
from accessai.translate_module import TranslateModule
from accessai.reid_module import ReidModule
from accessai.auto_enroll import AutoEnrollModule
from accessai.accessibility import AccessibilityEngine
from accessai.pipeline import Pipeline
from accessai.wakeword_module import WakeWordModule
from accessai import voice_commands
from accessai.server import make_app, LatestFrame
from accessai.video_recorder import EventVideoRecorder


def camera_loop(latest: LatestFrame, stop_event: threading.Event,
                recorder: "EventVideoRecorder | None" = None) -> None:
    """Continuously read frames and publish the newest one. Never crashes."""
    print(f"[Camera] Opening source: {config.CAMERA_SOURCE}")
    while not stop_event.is_set():
        cam = Camera(source=config.CAMERA_SOURCE,
                     width=config.FRAME_WIDTH,
                     height=config.FRAME_HEIGHT)
        try:
            cam.open()
            print(f"[Camera] Connected ({config.FRAME_WIDTH}x{config.FRAME_HEIGHT}).")
            while not stop_event.is_set():
                frame = cam.read()
                if frame is None:
                    print("[Camera] Read failed, reconnecting...")
                    break
                latest.set(frame)
                # Feed every frame into the circular pre-roll buffer so clips
                # include footage from BEFORE the person appeared.
                if recorder is not None:
                    recorder.push_frame(frame)
        except Exception as e:
            print(f"[Camera] Error: {e}")
            traceback.print_exc()
        finally:
            cam.release()
        if not stop_event.is_set():
            print("[Camera] Retrying in 2s...")
            time.sleep(2)


def _selfcheck_row(name, enabled, available, note=""):
    if not enabled:
        state = "OFF"
    elif not available:
        state = "UNAVAILABLE"
    else:
        state = "OK"
    return f"  {name:<12} {state:<12} {note}"


def _print_selfcheck(pipeline, tts, wakeword, speech, access) -> None:
    """Phase 10: a boot self-check table so a demo/operator sees module health
    at a glance. Purely informational; never raises."""
    def ok(fn):
        try:
            return bool(fn())
        except Exception:
            return False

    face = getattr(pipeline, "face", None)
    reid = getattr(pipeline, "reid", None)
    anti = getattr(pipeline, "antispoof", None)
    rows = [
        _selfcheck_row("face", getattr(pipeline, "face_enabled", False),
                       face is not None and ok(face.available),
                       f"{len(face.known_names)} known" if face else ""),
        _selfcheck_row("vision", getattr(pipeline, "vision_enabled", False),
                       ok(lambda: pipeline.vision.available()), "YOLOv8"),
        _selfcheck_row("antispoof", getattr(pipeline, "antispoof_enabled", False),
                       anti is not None and ok(anti.available),
                       (anti.backend_name() + " (placeholder)"
                        if anti and "heuristic" in (anti.backend_name() or "")
                        else (anti.backend_name() if anti else ""))),
        _selfcheck_row("vlm", getattr(pipeline, "vlm_enabled", False),
                       ok(lambda: pipeline.vlm.available()), "cloud scene+OCR"),
        _selfcheck_row("speech", getattr(pipeline, "speech_enabled", False),
                       speech is not None and ok(speech.available),
                       f"Whisper {getattr(speech, 'model_name', '')}" if speech else ""),
        _selfcheck_row("translate", getattr(pipeline, "translate_enabled", False),
                       ok(lambda: pipeline.translate.available()), ""),
        _selfcheck_row("reid", getattr(pipeline, "reid_enabled", False),
                       reid is not None and ok(reid.available),
                       (reid.backend_name() + " (placeholder)"
                        if reid and ok(reid.is_placeholder) else "") if reid else ""),
        _selfcheck_row("autoenroll", getattr(pipeline, "autoenroll_enabled", False),
                       ok(lambda: pipeline.autoenroll.available()), "DBSCAN"),
        _selfcheck_row("tts", getattr(tts, "_enabled", False),
                       ok(tts.available), tts.engine_name()),
        _selfcheck_row("wakeword", config.ENABLE_WAKEWORD,
                       wakeword is not None and ok(wakeword.available),
                       ((f"{wakeword.model_name} (custom)"
                         if not ok(wakeword.is_placeholder)
                         else f"{wakeword.model_name} (placeholder phrase)")
                        if wakeword else "openWakeWord not built")),
    ]
    try:
        import torch
        tv = torch.__version__
    except Exception:
        tv = "not installed"
    print("\n" + "-" * 60)
    print(" AccessAI self-check   (torch " + tv + ")")
    print("-" * 60)
    for r in rows:
        print(r)
    print("-" * 60)


def main() -> None:
    print("=" * 60)
    print(" AccessAI - AI Accessibility Doorbell  (Phase 10: Wake Word + Voice + Hardening)")
    print("=" * 60)

    # --- Core objects ---
    db = Database(config.DB_PATH)

    # Phase 2: face recognition (only built when the flag is on).
    face = None
    if config.ENABLE_FACE:
        face = FaceModule(known_dir=config.KNOWN_FACES_DIR,
                          threshold=config.FACE_MATCH_THRESHOLD,
                          model_name=config.FACE_MODEL_NAME,
                          det_size=config.FACE_DET_SIZE,
                          ctx_id=config.FACE_CTX_ID,
                          min_det_score=config.FACE_MIN_DET_SCORE,
                          db=db)                          # Phase 13: durable persist

    # Phase 3: object/scene detection (only built when the flag is on).
    vision = None
    if config.ENABLE_VISION:
        vision = VisionModule(model_path=config.YOLO_MODEL, conf=config.YOLO_CONF,
                              extra_person_conf=getattr(config, "YOLO_EXTRA_PERSON_CONF", 0.6))

    # Phase 5: liveness / anti-spoofing (only built when the flag is on). The
    # module always constructs and fails OPEN (score 1.0) if no model loads, so a
    # missing detector never blocks real visitors.
    antispoof = None
    if config.ENABLE_ANTISPOOF:
        antispoof = AntiSpoofModule(model_dir=config.ANTISPOOF_MODEL_DIR,
                                    min_score=config.ANTISPOOF_MIN_SCORE,
                                    backend=config.ANTISPOOF_BACKEND)

    # Phase 6: cloud VLM (scene description) + OCR (label reading). Keys are read
    # from the environment first (populated by .env above), then config as a
    # fallback. The module fails soft: no keys / dead network => available()
    # False and the pipeline runs on YOLO-only signals. OCR is a thin wrapper
    # that reuses the SAME combined VLM call (no second API round-trip).
    vlm = None
    ocr = None
    if config.ENABLE_VLM:
        keys = (os.environ.get("GEMINI_API_KEY", "")
                or os.environ.get("OPENAI_API_KEY", "")
                or getattr(config, "VLM_API_KEYS", ""))

        # Fallback model chain (same endpoint, different quota pools):
        # Primary → lite variants that have separate quota limits.
        extra_models = getattr(config, "VLM_FALLBACK_MODELS", [
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
            "gemini-flash-lite-latest",
        ])

        vlm = VLMModule(keys,
                        base_url=config.VLM_BASE_URL,
                        model=config.VLM_MODEL,
                        timeout=config.VLM_TIMEOUT,
                        max_tokens=config.VLM_MAX_TOKENS,
                        temperature=config.VLM_TEMPERATURE,
                        jpeg_quality=config.VLM_JPEG_QUALITY,
                        max_image_width=config.VLM_MAX_IMAGE_WIDTH,
                        extra_models=extra_models,
                        extra_providers=getattr(config, "VLM_EXTRA_PROVIDERS", []))
        if config.ENABLE_OCR:
            ocr = OCRModule(vlm=vlm)
        avail = "available" if vlm.available() else "unavailable (YOLO-only)"
        # NEVER log full keys: only the count + last-4 masks.
        print(f"[AccessAI] VLM: {config.VLM_MODEL} | {vlm.key_count()} key(s) "
              f"{vlm.masked_keys()} | {avail}")

    # Phase 7: speech recognition (Whisper + Silero VAD). Whisper is lazy-loaded
    # on first transcription so startup never blocks on the model download. The
    # app MUST start even if speech libs are missing: available()==False then and
    # every event simply carries an empty transcript.
    speech = None
    if config.ENABLE_SPEECH:
        speech = SpeechModule(model_name=config.WHISPER_MODEL,
                              sample_rate=config.SPEECH_SAMPLE_RATE,
                              seconds=config.SPEECH_SECONDS,
                              use_vad=config.SPEECH_VAD,
                              vad_min_speech_sec=config.SPEECH_VAD_MIN_SPEECH_SEC,
                              language=config.WHISPER_LANGUAGE)
        caps = speech.capabilities()
        avail = "available" if speech.available() else "disabled (no whisper)"
        print(f"[AccessAI] Speech: {config.WHISPER_MODEL} | {avail} | "
              f"whisper={caps['whisper']} mic={caps['mic']} "
              f"silero={caps['silero']}")

    # Phase 8: translation. REUSES the Phase-6 VLMModule (same keys + failover)
    # for a text-only translation call - this is torch-safe (adds no dependency,
    # never moves torch). Degrades to passthrough (original text) when keys are
    # missing. The app MUST start even with translation unavailable.
    translate = None
    if config.ENABLE_TRANSLATE:
        # A runtime change via POST /user_language is persisted to
        # data/user_language.txt; prefer it over the config default so the user's
        # last choice survives a restart. Best-effort: any read error falls back
        # to config.USER_LANGUAGE.
        user_language = config.USER_LANGUAGE
        try:
            _lang_file = os.path.join(config.DATA_DIR, "user_language.txt")
            if os.path.isfile(_lang_file):
                with open(_lang_file, encoding="utf-8") as _fh:
                    _saved = _fh.read().strip()
                if _saved:
                    user_language = _saved
        except Exception as _e:
            print(f"[AccessAI] could not read saved user_language: {_e}")
        groq_keys = os.environ.get("GROQ_API_KEY", "")
        translate = TranslateModule(backend=config.TRANSLATE_BACKEND,
                                    user_language=user_language,
                                    language_names=config.LANGUAGE_NAMES,
                                    vlm=vlm,
                                    groq_keys=groq_keys)
        avail = "available" if translate.available() else "passthrough (original)"
        print(f"[AccessAI] Translate: {translate.backend_name()} -> "
              f"{translate.lang_name(user_language)} | {avail}")

    # Phase 9: memory layer. Re-ID gives repeat UNKNOWN visitors a stable id +
    # "seen N times today"; auto-enroll clusters frequent unknown FACES and
    # suggests saving them. Both are torch-FREE (Re-ID = ONNX/onnxruntime or an
    # HSV-histogram placeholder; auto-enroll = scikit-learn DBSCAN) and fail soft:
    # a missing model / missing sklearn simply skips the feature. Auto-enroll gets
    # the FaceModule so confirm() can promote a cluster to a known face.
    reid = None
    if config.ENABLE_REID:
        reid = ReidModule(backend=config.REID_BACKEND,
                          model_dir=config.REID_MODEL_DIR,
                          match_threshold=config.REID_MATCH_THRESHOLD,
                          ttl_hours=config.REID_GALLERY_TTL_HOURS,
                          max_gallery=config.REID_MAX_GALLERY, db=db)
        tag = "PLACEHOLDER" if reid.is_placeholder() else "model"
        print(f"[AccessAI] Re-ID: {reid.backend_name()} ({tag}) | "
              f"threshold={config.REID_MATCH_THRESHOLD} "
              f"ttl={config.REID_GALLERY_TTL_HOURS}h | "
              f"{'available' if reid.available() else 'unavailable'}")
    autoenroll = None
    if config.ENABLE_AUTOENROLL:
        autoenroll = AutoEnrollModule(db=db, eps=config.AUTOENROLL_EPS,
                                      min_samples=config.AUTOENROLL_MIN_SAMPLES,
                                      suggest_after=config.AUTOENROLL_SUGGEST_AFTER,
                                      face=face)
        print(f"[AccessAI] Auto-enroll: suggest_after="
              f"{config.AUTOENROLL_SUGGEST_AFTER} | "
              f"{'available' if autoenroll.available() else 'unavailable (no sklearn)'}")

    # Phase 4 + Phase 11: text-to-speech + accessibility output engine. TTSModule
    # always constructs and picks the best available natural voice backend
    # (kokoro offline -> edge online -> pyttsx3), degrading to text-only if none
    # are usable. Torch-safe: kokoro-onnx + edge-tts never touch torch.
    tts = TTSModule(enabled=config.ENABLE_TTS,
                    engine=config.TTS_ENGINE,
                    voice=config.KOKORO_VOICE,
                    model_dir=config.TTS_MODEL_DIR,
                    speed=config.KOKORO_SPEED,
                    edge_voice=config.EDGE_VOICE,
                    rate=config.TTS_RATE,
                    volume=config.TTS_VOLUME,
                    lang=config.KOKORO_LANG,
                    voice_choices=config.VOICE_CHOICES,
                    lang_voices=getattr(config, "LANGUAGE_VOICES", {}))
    access = AccessibilityEngine(tts=tts, mode=config.ACCESSIBILITY_MODE)
    print(f"[AccessAI] TTS: {tts.current_voice()} "
          f"(backends: {tts.backends()}) | mode: {access.mode}")
    if antispoof is not None:
        avail = "available" if antispoof.available() else "unavailable (fail-open)"
        print(f"[AccessAI] Liveness backend: {antispoof.backend_name()} ({avail})")

    # Phase 10: wake-word detector (hands-free Blind Mode). Built when the flag is
    # on; on_wake is wired AFTER make_app so it can push results to the dashboard.
    # ALWAYS-ON is opt-in (WAKEWORD_ALWAYS_ON, default off): an open mic is a CPU +
    # privacy choice. The app MUST start even if openWakeWord is missing - then
    # available()==False and only push-to-talk (/listen) works.
    wakeword = None
    if config.ENABLE_WAKEWORD:
        wakeword = WakeWordModule(model=config.WAKEWORD_MODEL,
                                  threshold=config.WAKEWORD_THRESHOLD,
                                  cooldown=config.WAKEWORD_COOLDOWN,
                                  inference_framework=config.WAKEWORD_INFERENCE_FRAMEWORK,
                                  model_dir=getattr(config, "WAKEWORD_MODEL_DIR", ""))
        avail = "available" if wakeword.available() else "unavailable (push-to-talk only)"
        print(f"[AccessAI] Wake word: {wakeword.model_name} | {avail}")

    pipeline = Pipeline(db=db, history_dir=config.HISTORY_DIR,
                        face=face, face_enabled=config.ENABLE_FACE,
                        vision=vision, vision_enabled=config.ENABLE_VISION,
                        antispoof=antispoof,
                        antispoof_enabled=config.ENABLE_ANTISPOOF,
                        vlm=vlm, vlm_enabled=config.ENABLE_VLM,
                        ocr=ocr, ocr_enabled=config.ENABLE_OCR,
                        speech=speech, speech_enabled=config.ENABLE_SPEECH,
                        translate=translate,
                        translate_enabled=config.ENABLE_TRANSLATE,
                        reid=reid, reid_enabled=config.ENABLE_REID,
                        autoenroll=autoenroll,
                        autoenroll_enabled=config.ENABLE_AUTOENROLL,
                        parcel_labels=config.PARCEL_LABELS,
                        courier_keywords=config.COURIER_KEYWORDS,
                        vlm_only_for_unknown=config.VLM_ONLY_FOR_UNKNOWN,
                        vlm_async_enrich=config.VLM_ASYNC_ENRICH,
                        vlm_enrich_speak=config.VLM_ENRICH_SPEAK,
                        vlm_enrich_speak_full=config.VLM_ENRICH_SPEAK_FULL,
                        speech_capture_on_trigger=config.SPEECH_CAPTURE_ON_TRIGGER,
                        translate_announcement=config.TRANSLATE_ANNOUNCEMENT,
                        access=access, cooldown=config.EVENT_COOLDOWN)
    latest = LatestFrame()

    # --- Event video recorder (Phase 18) ---
    # Circular-buffer MP4 clip recorder: saves 10s-before + event + 8s-after
    # for every person detection. Runs fully in background threads.
    recorder = None
    if getattr(config, "ENABLE_VIDEO_CLIPS", True):
        try:
            recorder = EventVideoRecorder(
                clips_dir=getattr(config, "VIDEO_CLIPS_DIR", "data/clips"),
                fps=getattr(config, "VIDEO_CLIPS_FPS", 15),
                pre_roll_sec=getattr(config, "VIDEO_PRE_ROLL_SEC", 10),
                post_roll_sec=getattr(config, "VIDEO_POST_ROLL_SEC", 8),
                min_event_sec=getattr(config, "VIDEO_MIN_EVENT_SEC", 1.0),
                retain_days=getattr(config, "VIDEO_RETAIN_DAYS", 7),
                max_clips=getattr(config, "VIDEO_MAX_CLIPS", 500),
            )
        except Exception as _e:
            print(f"[AccessAI] Video recorder init failed (clips disabled): {_e}")

    # --- Camera thread ---
    stop_event = threading.Event()
    cam_thread = threading.Thread(
        target=camera_loop, args=(latest, stop_event, recorder), daemon=True
    )
    cam_thread.start()

    # --- Model warm-up thread (SPEED) ---
    # Whisper (~140MB) and Kokoro (~310MB) are lazy-loaded, which used to make
    # the FIRST "Hear Visitor" and the FIRST spoken announcement several seconds
    # slower than every later one. Load them in the background now, so they are
    # hot before the first visitor. Daemon + fail-soft: a warm-up failure only
    # means the old lazy path runs on first use, exactly as before.
    def _warm_models():
        try:
            # A dummy inference builds the ONNX/torch graphs now: the FIRST real
            # ring then runs at steady-state speed instead of paying graph-build.
            import numpy as _np
            dummy = _np.zeros((360, 640, 3), dtype=_np.uint8)
            if vision is not None and vision.available():
                vision.detect(dummy)
            if face is not None and face.available():
                face.identify(dummy)
            if tts is not None:
                tts.warm()
            if speech is not None and speech.available():
                speech.warm()
            print("[AccessAI] Warm-up complete: vision/face/TTS/speech are hot.")
        except Exception as e:
            print(f"[AccessAI] Warm-up thread error (lazy load will cover): {e}")

    threading.Thread(target=_warm_models, daemon=True,
                     name="model-warmup").start()

    # --- Server ---
    app = make_app(
        pipeline=pipeline,
        latest=latest,
        db=db,
        web_dir=config.WEB_DIR,
        history_dir=config.HISTORY_DIR,
        mode=config.ACCESSIBILITY_MODE,
        access=access,
        tts=tts,
        speech=speech,
        wakeword=wakeword,
        cfg=config,
        wakeword_command_seconds=config.WAKEWORD_COMMAND_SECONDS,
        visitor_listen_seconds=config.VISITOR_LISTEN_SECONDS,
    )

    # Phase 12: wire the pipeline's background VLM-enrich broadcast to the app's
    # thread-safe bridge, so a late unknown-visitor description (age/gender +
    # clothing) pushed by the enrich thread reaches any open dashboard live. Same
    # pattern as the wake-word wiring below. If the bridge is absent the enrich
    # still updates the DB; the dashboard just picks it up on the next refresh.
    pipeline._enrich_broadcast = getattr(app.state, "broadcast_threadsafe", None)

    # Phase 18: expose the video recorder on app.state so /clips endpoints can
    # access it. None when recording is disabled.
    app.state.recorder = recorder

    # Phase 10: now that the app (and its thread-safe broadcast bridge) exist,
    # wire the wake callback. On a detection it runs the SAME voice interaction as
    # /listen (capture -> parse -> act -> speak) and pushes the result to any open
    # dashboards. Only START the listener if the user opted into always-on.
    if wakeword is not None and wakeword.available():
        def _on_wake():
            result = voice_commands.run_voice_interaction(
                speech=speech, pipeline=pipeline, db=db, latest=latest,
                access=access, seconds=config.WAKEWORD_COMMAND_SECONDS)
            bridge = getattr(app.state, "broadcast_threadsafe", None)
            if bridge is not None:
                bridge({"type": "voice", **result})
        wakeword.set_on_wake(_on_wake)
        if config.WAKEWORD_ALWAYS_ON:
            wakeword.start()
            # Say the phrase that is ACTUALLY loaded, not config.WAKEWORD_MODEL --
            # that setting is only the pretrained fallback, so whenever a custom
            # .onnx was picked up the banner told the user the wrong words.
            phrase = wakeword.model_name_str().replace("_", " ")
            print(f"[AccessAI] Voice: ALWAYS-ON - say '{phrase}' "
                  f"then your command. (also: push-to-talk /listen)")
        else:
            print("[AccessAI] Voice: PUSH-TO-TALK (/listen). Always-on is OPT-IN "
                  "(toggle in dashboard or set WAKEWORD_ALWAYS_ON=True).")
    else:
        print("[AccessAI] Voice: PUSH-TO-TALK only (/listen). "
              "Always-on wake word unavailable.")

    # Phase 17: software motion trigger (trigger='motion'). Watches the shared
    # latest-frame with a cheap absdiff detector; on confirmed motion it runs
    # the SAME pipeline as the doorbell and broadcasts the event to open
    # dashboards. Suppressed for MOTION_COOLDOWN after any fire.
    motion = None
    if getattr(config, "ENABLE_MOTION", False):
        from accessai.motion_module import MotionModule
        motion = MotionModule(latest,
                              min_area=config.MOTION_MIN_AREA,
                              consecutive=config.MOTION_CONSECUTIVE,
                              cooldown=config.MOTION_COOLDOWN,
                              interval=config.MOTION_INTERVAL,
                              warmup=config.MOTION_WARMUP)

        def _on_motion():
            frame = latest.get()
            if frame is None:
                return
            ev = pipeline.run_once(frame, trigger="motion")
            bridge = getattr(app.state, "broadcast_threadsafe", None)
            if bridge is not None:
                from accessai.server import _jsonify
                bridge({"type": "event", "event": _jsonify(ev.to_dict())})
            # Video clip recording: signal recorder based on whether any
            # person was actually detected in this pipeline run.
            if recorder is not None:
                if ev.visitor_count > 0 or ev.people:
                    person_name = "Unknown"
                    recognized = False
                    if ev.identity and ev.identity.known:
                        person_name = ev.identity.name
                        recognized = True
                    elif ev.people:
                        known_p = next((p for p in ev.people if p.known), None)
                        if known_p:
                            person_name = known_p.name
                            recognized = True
                    recorder.on_person_detected({
                        "event_id":   ev.event_id,
                        "timestamp":  ev.timestamp,
                        "person":     person_name,
                        "recognized": recognized,
                        "location":   "front_door",
                        "trigger":    ev.trigger,
                    })
                else:
                    recorder.on_person_gone()

        motion.set_on_motion(_on_motion)
        motion.start()
    app.state.motion = motion

    _print_selfcheck(pipeline, tts, wakeword, speech, access)

    # Phase 17: loudly flag the risky combination - reachable beyond localhost
    # with no auth token. One line, every boot, until it's fixed or accepted.
    if config.HOST not in ("127.0.0.1", "localhost") and not config.AUTH_TOKEN:
        print("[AccessAI] SECURITY WARNING: server is reachable from the LAN "
              f"(HOST={config.HOST}) with NO auth token. Anyone on the network "
              "can view the camera and control the doorbell. Set "
              "ACCESSAI_TOKEN=<random string> in .env to require a token.")
    if config.RING_HMAC_SECRET:
        print("[AccessAI] /ring webhook: HMAC-signed (X-Ring-Signature).")

    print(f"\nOpen the dashboard: http://localhost:{config.PORT}\n")
    try:
        uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        cam_thread.join(timeout=3)
        print("\n[AccessAI] Stopped.")


if __name__ == "__main__":
    main()
