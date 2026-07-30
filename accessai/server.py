"""
FastAPI server - exposes the pipeline to the web dashboard (and later the
Flutter app / ESP32 client).

Routes:
    GET  /                    -> dashboard (index.html)
    GET  /video               -> MJPEG stream of the live camera
    POST /trigger             -> simulate a doorbell press (creates an event)
    GET  /history?limit=N     -> recent events
    POST /history/clear       -> delete ALL visit events + their snapshots
    GET  /event/{event_id}    -> one event
    POST /event/{id}/delete   -> delete one visit event + its snapshot
    GET  /snapshot/{event_id} -> jpeg snapshot for an event
    POST /enroll              -> enroll a face from the live frame (Phase 2)
    POST /enroll_upload       -> enroll a person from uploaded photo(s) (Phase 13)
    POST /known/delete        -> delete a known person (photos+embeddings) (P13)
    GET  /known_photo/{name}  -> representative photo thumbnail for a person (P13)
    GET  /known               -> list of enrolled people + counts (Phase 2/13)
    GET  /vlm_status          -> cloud VLM wiring (masked keys, model) (Phase 6)
    GET  /speech_status       -> speech recognition capabilities (Phase 7)
    POST /transcribe          -> transcribe an uploaded WAV (Phase 7)
    GET  /translate_status    -> translation backend + target language (Phase 8)
    POST /translate           -> translate a text string (Phase 8)
    POST /user_language       -> change the user's target language live (Phase 8)
    GET  /reid_status         -> re-ID backend + gallery size (Phase 9)
    GET  /suggestions         -> open "save this visitor?" prompts (Phase 9)
    POST /suggestions/confirm -> promote a clustered unknown to a known face (P9)
    POST /suggestions/dismiss -> dismiss a suggestion (Phase 9)
    POST /reply               -> speak a typed reply at the door (Phase 4)
    GET/POST /mode            -> read / set accessibility mode (drives TTS on/off)
    POST /listen              -> push-to-talk voice command (parse+act+speak) (P10)
    POST /ask                 -> free-form question about the live frame -> VLM (P16)
    GET  /wakeword_status     -> always-on wake-word listener state (Phase 10)
    POST /wakeword/{on|off}   -> start/stop the always-on listener (opt-in) (P10)
    GET  /status              -> central health: modules + flags + torch (Phase 10)
    POST /ring                -> hardware doorbell webhook (optional JPEG) (P10)
    WS   /events              -> pushes new VisitorEvents live
"""

import asyncio
import hashlib
import hmac as _hmac
import json
import os
import secrets as _secrets
import threading
import time

import cv2
import numpy as np
from fastapi import (FastAPI, WebSocket, WebSocketDisconnect, HTTPException,
                     Body, UploadFile, File, Form, Request)
from fastapi.responses import (
    FileResponse, StreamingResponse, JSONResponse, HTMLResponse, Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from . import voice_commands
from .face_module import is_safe_person_name


# --- Security helpers (Phase 17) -------------------------------------------
# Paths anyone may fetch WITHOUT a token: the UI shells and their static
# assets. They contain no data - every piece of live/stored information the
# shells display comes from the API routes below, which ARE protected. The
# dashboard/PWA ask the user for the token on first load and store it locally.
_PUBLIC_PATHS = ("/", "/app", "/app/", "/app/manifest.webmanifest",
                 "/app/sw.js", "/favicon.ico")
_PUBLIC_PREFIXES = ("/static/", "/app/")


def _is_public(path: str) -> bool:
    return path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES)


class _TokenBucket:
    """Per-IP token bucket for the pipeline-driving routes. Thread-safe.

    Each expensive call (full pipeline run, possibly a paid cloud VLM request)
    consumes one token; the bucket refills at per_min/60 tokens per second up
    to `burst`. A drained bucket -> 429 with a clean JSON body.
    """

    def __init__(self, per_min: float, burst: int):
        self.rate = max(0.01, float(per_min)) / 60.0
        self.burst = max(1, int(burst))
        self._lock = threading.Lock()
        self._state = {}          # ip -> (tokens, last_ts)

    def allow(self, ip: str) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._state.get(ip, (float(self.burst), now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens < 1.0:
                self._state[ip] = (tokens, now)
                return False
            self._state[ip] = (tokens - 1.0, now)
            # Bound the table so a spoofed-IP flood can't grow it unboundedly.
            if len(self._state) > 1000:
                oldest = sorted(self._state.items(), key=lambda kv: kv[1][1])
                for k, _v in oldest[:500]:
                    self._state.pop(k, None)
            return True


class LatestFrame:
    """Thread-safe holder for the newest camera frame."""

    def __init__(self):
        self._lock = threading.Lock()
        self._frame = None

    def set(self, frame) -> None:
        with self._lock:
            self._frame = frame

    def get(self):
        with self._lock:
            return None if self._frame is None else self._frame.copy()


# Valid accessibility modes, kept here so both /mode routes agree.
_VALID_MODES = ("blind", "deaf", "both")


def make_app(*, pipeline, latest: LatestFrame, db, web_dir: str,
             history_dir: str, mode: str = "both",
             access=None, tts=None, speech=None, wakeword=None, cfg=None,
             wakeword_command_seconds: int = 4,
             visitor_listen_seconds: int = 6) -> FastAPI:
    app = FastAPI(title="AccessAI")

    # --- Phase 17: bearer-token auth + rate limiting (opt-in via config) -----
    # AUTH_TOKEN set => every non-public route requires
    #   Authorization: Bearer <token>   or   ?token=<token>
    # (the query form exists for MJPEG <img> tags and the WebSocket, where
    # custom headers aren't possible). Comparison is constant-time. AUTH_TOKEN
    # empty => open appliance, exactly the pre-Phase-17 behaviour.
    auth_token = str(getattr(cfg, "AUTH_TOKEN", "") or "")
    ring_secret = str(getattr(cfg, "RING_HMAC_SECRET", "") or "")
    bucket = _TokenBucket(getattr(cfg, "RATE_PER_MIN", 12),
                          getattr(cfg, "RATE_BURST", 4))
    _EXPENSIVE = ("/trigger", "/ring", "/ask", "/listen", "/hear_visitor")

    def _token_ok(request) -> bool:
        if not auth_token:
            return True
        hdr = request.headers.get("authorization", "")
        supplied = hdr[7:] if hdr.lower().startswith("bearer ") else \
            request.query_params.get("token", "")
        return _secrets.compare_digest(supplied, auth_token)

    @app.middleware("http")
    async def _security_mw(request: Request, call_next):
        path = request.url.path
        if not _is_public(path):
            # /ring is exempt here when a ring secret exists - the endpoint
            # itself verifies the HMAC over the raw body (the body can't be
            # read in middleware without breaking the downstream handler).
            ring_hmac_mode = (path == "/ring" and ring_secret)
            if not ring_hmac_mode and not _token_ok(request):
                return JSONResponse(
                    {"error": "unauthorized",
                     "hint": "send Authorization: Bearer <token> or ?token="},
                    status_code=401)
            if any(path == p for p in _EXPENSIVE):
                ip = request.client.host if request.client else "?"
                if not bucket.allow(ip):
                    return JSONResponse(
                        {"error": "rate limited",
                         "hint": "too many pipeline runs; wait a few seconds"},
                        status_code=429)
        return await call_next(request)

    # Phase 16: allow the Flutter app's WEB target (flutter run -d chrome) and
    # other browser origins on the LAN to call these routes cross-origin.
    # Phase 17: the origin list comes from config (default "*"). With AUTH_TOKEN
    # set the wildcard is safe - a hostile page can send requests but not the
    # token (allow_credentials stays False, and the token lives in the app's
    # own storage, unreachable cross-origin). With auth OFF, tighten
    # CORS_ORIGINS in config.py if drive-by pages on the LAN are a concern.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(getattr(cfg, "CORS_ORIGINS", ["*"]) or ["*"]),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Accessibility mode lives in memory (persisted later). Keep it in sync with
    # the accessibility engine so toggling the mode turns speech on/off live.
    start_mode = access.mode if access is not None else \
        (mode if mode in _VALID_MODES else "both")
    # "mode" is the household default. "device_modes" holds per-device overrides
    # (Phase 17): one paired phone can run Blind while another runs Deaf and the
    # dashboard stays on Both. A device with no override follows the default.
    state = {"mode": start_mode, "device_modes": {}}

    # --- Static web dashboard ------------------------------------------------
    if os.path.isdir(web_dir):
        app.mount("/static", StaticFiles(directory=web_dir), name="static")

    # --- Phase 14: mobile PWA (served alongside the desktop dashboard) --------
    # The app lives in web/app/. Explicit routes for the entrypoint, manifest, and
    # service worker (correct MIME + SW scope) are declared BEFORE the catch-all
    # static mount so they take precedence; the mount then serves app.js / app.css
    # / icons/*. The desktop dashboard at / is untouched.
    web_app_dir = os.path.join(web_dir, "app")

    def _app_file(name, media_type=None, headers=None):
        path = os.path.join(web_app_dir, name)
        if not os.path.exists(path):
            raise HTTPException(404, f"{name} not found")
        return FileResponse(path, media_type=media_type, headers=headers or {})

    @app.get("/app", response_class=HTMLResponse)
    @app.get("/app/", response_class=HTMLResponse)
    async def app_index():
        index = os.path.join(web_app_dir, "index.html")
        if not os.path.exists(index):
            return HTMLResponse(
                "<h1>AccessAI</h1><p>The mobile app files are missing "
                "(web/app/). Check the server logs.</p>")
        return FileResponse(index)

    @app.get("/app/manifest.webmanifest")
    async def app_manifest():
        return _app_file("manifest.webmanifest",
                         media_type="application/manifest+json")

    @app.get("/app/sw.js")
    async def app_sw():
        # Served at the /app/ scope so the worker controls /app/*. The
        # Service-Worker-Allowed header lets it claim that scope; never cache the
        # SW file itself so updates take effect on reload.
        return _app_file("sw.js", media_type="text/javascript",
                         headers={"Service-Worker-Allowed": "/app/",
                                  "Cache-Control": "no-cache"})

    if os.path.isdir(web_app_dir):
        app.mount("/app", StaticFiles(directory=web_app_dir, html=True),
                  name="mobileapp")

    # --- WebSocket broadcast -------------------------------------------------
    clients: set[WebSocket] = set()
    clients_lock = asyncio.Lock()

    # --- Phase 17: push fan-out (docs/MOBILE_PUSH.md scaffolding) ------------
    # Every "event" broadcast ALSO fans out to registered push tokens so a
    # CLOSED app can be woken. Fire-and-forget in an executor - push must
    # never delay or break the doorbell. Without ENABLE_PUSH + FCM creds the
    # sender logs one hint per boot and does nothing.
    push_enabled = bool(getattr(cfg, "ENABLE_PUSH", False))
    _push_warned = {"done": False}

    def _push_send_all(ev_dict: dict) -> None:
        try:
            tokens = db.push_tokens()
        except Exception:
            tokens = []
        if not tokens:
            return
        if not push_enabled:
            if not _push_warned["done"]:
                _push_warned["done"] = True
                print(f"[Push] {len(tokens)} device token(s) registered but "
                      "ENABLE_PUSH is off - notifications are stored-only. "
                      "See docs/MOBILE_PUSH.md to enable FCM delivery.")
            return
        creds = str(getattr(cfg, "FCM_CREDENTIALS_JSON", "") or "")
        project = str(getattr(cfg, "FCM_PROJECT_ID", "") or "")
        if not creds or not project:
            if not _push_warned["done"]:
                _push_warned["done"] = True
                print("[Push] ENABLE_PUSH is on but FCM_PROJECT_ID / "
                      "FCM_CREDENTIALS_JSON are unset - cannot deliver. "
                      "See docs/MOBILE_PUSH.md prerequisites.")
            return
        # FCM HTTP v1 delivery. google-auth ships with the existing stack; if
        # it is missing we degrade to a logged hint, never an exception.
        try:
            import google.auth.transport.requests as _gar
            from google.oauth2 import service_account as _sa
            import urllib.request as _rq
            scoped = _sa.Credentials.from_service_account_file(
                creds, scopes=["https://www.googleapis.com/auth/firebase.messaging"])
            scoped.refresh(_gar.Request())
            title = "AccessAI — " + {
                "known": "Known visitor", "delivery": "Likely delivery",
                "spoof": "Possible spoof", "unknown": "Visitor",
            }.get(ev_dict.get("alert_kind", ""), "Visitor")
            body = ev_dict.get("announcement_text") or "Someone is at the door."
            for t in tokens:
                msg = json.dumps({"message": {
                    "token": t["token"],
                    "notification": {"title": title, "body": body},
                    "webpush": {"fcm_options": {"link": "/app"}},
                }}).encode()
                req = _rq.Request(
                    f"https://fcm.googleapis.com/v1/projects/{project}/messages:send",
                    data=msg, method="POST",
                    headers={"Authorization": f"Bearer {scoped.token}",
                             "Content-Type": "application/json"})
                try:
                    _rq.urlopen(req, timeout=8)
                except Exception as e:
                    print(f"[Push] send failed for ...{t['token'][-6:]}: {e}")
        except ImportError:
            if not _push_warned["done"]:
                _push_warned["done"] = True
                print("[Push] google-auth not installed; cannot mint an FCM "
                      "OAuth token. pip install google-auth to enable.")
        except Exception as e:                            # pragma: no cover
            print(f"[Push] delivery error (continuing): {e}")

    # --- Phase 17: smart-home alert webhook ----------------------------------
    # Colour-coded room-light flashes for deaf users: every doorbell event
    # POSTs {kind, color, name, announcement} to ALERT_WEBHOOK_URL (Home
    # Assistant / Hue relay / anything). Fire-and-forget with a short timeout.
    webhook_url = str(getattr(cfg, "ALERT_WEBHOOK_URL", "") or "")
    webhook_colors = dict(getattr(cfg, "ALERT_WEBHOOK_COLORS", {}) or {})

    def _webhook_send(ev_dict: dict) -> None:
        if not webhook_url:
            return
        try:
            import urllib.request as _rq
            kind = ev_dict.get("alert_kind", "unknown")
            body = json.dumps({
                "kind": kind,
                "color": webhook_colors.get(kind, "#2563eb"),
                "name": (ev_dict.get("identity") or {}).get("name", "Unknown"),
                "announcement": ev_dict.get("announcement_text", ""),
            }).encode()
            req = _rq.Request(webhook_url, data=body, method="POST",
                              headers={"Content-Type": "application/json"})
            _rq.urlopen(req, timeout=5)
        except Exception as e:
            print(f"[Webhook] alert POST failed (continuing): {e}")

    # --- Phase 17: auto-greeting (opt-in) ------------------------------------
    # When an UNKNOWN visitor (or unrecognised delivery) rings, the doorbell
    # itself asks for name + purpose, listens for a few seconds, and attaches
    # the transcript to the event - no manual "Hear Visitor" press. Known
    # visitors and spoof warnings are never auto-interrogated. The spoken
    # prompt announces the recording (consent-by-notice); the flag is off by
    # default because it records a stranger's voice automatically.
    auto_greet_on = bool(getattr(cfg, "ENABLE_AUTO_GREETING", False))
    _greet_busy = threading.Lock()

    def _auto_greet_worker(ev_dict: dict) -> None:
        if not _greet_busy.acquire(blocking=False):
            return                    # one interrogation at a time
        try:
            greeting = str(getattr(cfg, "AUTO_GREETING_TEXT", "") or "")
            secs = int(getattr(cfg, "AUTO_GREETING_LISTEN_SECONDS", 6) or 6)
            if access is not None and greeting:
                access.speak_text(greeting)
            # The greeting sits behind the announcement in the TTS queue; wait
            # a rough estimate of both so the mic doesn't record our own voice.
            est = 2.0 + 0.07 * (len(greeting)
                                + len(ev_dict.get("announcement_text", "")))
            time.sleep(min(est, 12.0))
            audio = speech.record(secs)
            if audio is None:
                return
            if speech.use_vad and not speech.has_speech(audio):
                return                # silence - nothing to attach
            text, lang = speech.transcribe(audio)
            if not (text or "").strip():
                return
            translated = ""
            tr = getattr(pipeline, "translate", None)
            if tr is not None and getattr(pipeline, "translate_enabled", False):
                target = getattr(tr, "user_language", "en")
                if (lang or "") != target:
                    out = tr.translate(text, src_lang=lang, target_lang=target)
                    if out and out.strip() and out.strip() != text.strip():
                        translated = out.strip()
            event_id = ev_dict.get("event_id", "")
            if event_id:
                fields = {"speech_transcript": text, "language_detected": lang}
                if translated:
                    fields["translated_transcript"] = translated
                db.update_event_fields(event_id, **fields)
            broadcast_threadsafe({"type": "visitor_speech", "text": text,
                                  "translated": translated, "language": lang,
                                  "event_id": event_id, "auto": True})
            # Read the visitor's answer back to a blind user.
            if access is not None and getattr(access, "mode", "both") in (
                    "blind", "both"):
                say = translated or text
                access.speak_text(f'They said: "{say}"')
        except Exception as e:                            # pragma: no cover
            print(f"[AutoGreet] failed (continuing): {e}")
        finally:
            _greet_busy.release()

    def _maybe_auto_greet(ev_dict: dict) -> None:
        if not auto_greet_on or speech is None or not speech.available():
            return
        if ev_dict.get("trigger") not in ("doorbell", "ring", "motion"):
            return
        if ev_dict.get("alert_kind") not in ("unknown", "delivery"):
            return
        if int(ev_dict.get("visitor_count", 0) or 0) < 1:
            return                    # empty frame - nobody to interrogate
        threading.Thread(target=_auto_greet_worker, args=(ev_dict,),
                         daemon=True, name="auto-greet").start()

    async def broadcast(payload: dict) -> None:
        # Doorbell events additionally fan out to push devices (app closed),
        # the smart-home webhook (room lights), and - when enabled - the
        # auto-greeting interrogation. All fire-and-forget.
        if payload.get("type") == "event" and isinstance(
                payload.get("event"), dict):
            try:
                loop = asyncio.get_running_loop()
                loop.run_in_executor(None, _push_send_all, payload["event"])
                loop.run_in_executor(None, _webhook_send, payload["event"])
                _maybe_auto_greet(payload["event"])
            except Exception:                             # pragma: no cover
                pass
        async with clients_lock:
            dead = []
            for ws in clients:
                try:
                    await ws.send_json(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                clients.discard(ws)

    # Thread-safe broadcast bridge (Phase 10). The always-on wake-word listener
    # runs in its OWN thread (outside the event loop), so it can't await
    # broadcast() directly. We capture the running loop at startup and let the
    # wake callback schedule a broadcast onto it. If the loop isn't up yet, the
    # push is simply skipped - the spoken answer still happens regardless.
    _loop_holder = {}

    @app.on_event("startup")
    async def _capture_loop():
        _loop_holder["loop"] = asyncio.get_running_loop()

    def broadcast_threadsafe(payload: dict) -> None:
        loop = _loop_holder.get("loop")
        if loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(broadcast(payload), loop)
        except Exception as e:                            # pragma: no cover
            print(f"[Server] broadcast_threadsafe failed: {e}")

    app.state.broadcast_threadsafe = broadcast_threadsafe

    def _suppress_motion() -> None:
        """After a doorbell/manual trigger, hold the motion detector's cooldown
        so the same visitor isn't immediately re-announced by motion.
        run.py sets app.state.motion when ENABLE_MOTION is on; None otherwise."""
        m = getattr(app.state, "motion", None)
        if m is not None:
            try:
                m.suppress()
            except Exception:                             # pragma: no cover
                pass

    # --- Routes --------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    async def root():
        index = os.path.join(web_dir, "index.html")
        if not os.path.exists(index):
            return HTMLResponse(
                "<h1>AccessAI</h1><p>Server is running, but the dashboard files "
                "are missing (web/). Check the server logs.</p>"
            )
        return FileResponse(index)

    @app.get("/video")
    def video():
        def gen():
            while True:
                frame = latest.get()
                if frame is None:
                    time.sleep(0.05)
                    continue
                ok, buf = cv2.imencode(".jpg", frame,
                                       [cv2.IMWRITE_JPEG_QUALITY, 80])
                if not ok:
                    continue
                data = buf.tobytes()
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n"
                       b"Content-Length: " + str(len(data)).encode() + b"\r\n\r\n"
                       + data + b"\r\n")
                time.sleep(1 / 20)   # ~20 fps
        return StreamingResponse(
            gen(), media_type="multipart/x-mixed-replace; boundary=frame"
        )

    @app.post("/trigger")
    async def trigger():
        frame = latest.get()
        if frame is None:
            # Headless / no-webcam fallback: use a blank gray frame so the
            # pipeline, snapshot, and history still work. Never 503 forever.
            print("[Server] No camera frame available; using blank gray frame.")
            frame = np.full((720, 1280, 3), 127, dtype=np.uint8)

        loop = asyncio.get_event_loop()
        ev = await loop.run_in_executor(
            None, lambda: pipeline.run_once(frame, trigger="doorbell")
        )
        _suppress_motion()
        payload = {"type": "event", "event": _jsonify(ev.to_dict())}
        await broadcast(payload)
        return JSONResponse(payload["event"])

    @app.get("/history")
    def history(limit: int = 50, q: str = ""):
        """Stored events, newest first. `q` searches name / intent /
        announcement / transcript / scene / OCR / date (Objective 7)."""
        return JSONResponse(db.recent_events(limit=limit, q=q))

    @app.get("/event/{event_id}")
    def event(event_id: str):
        e = db.get_event(event_id)
        if not e:
            raise HTTPException(404, "event not found")
        return JSONResponse(e)

    # --- Visit-history removal (dashboard delete / clear all) ----------------
    def _safe_unlink_snapshot(event_id: str, stored_path: str = "") -> None:
        """Delete an event's snapshot .jpg, but ONLY if it resolves inside
        history_dir. Guards against path traversal and never raises (a missing
        file is fine). Tries both the stored path and history_dir/<id>.jpg."""
        base = os.path.realpath(history_dir)
        candidates = []
        if stored_path:
            candidates.append(stored_path)
        candidates.append(os.path.join(history_dir, f"{event_id}.jpg"))
        for cand in candidates:
            try:
                real = os.path.realpath(cand)
                if (real == base or real.startswith(base + os.sep)) \
                        and os.path.isfile(real):
                    os.remove(real)
            except Exception as e:                        # pragma: no cover
                print(f"[Server] snapshot cleanup skipped for {event_id}: {e}")

    @app.post("/event/{event_id}/delete")
    async def event_delete(event_id: str):
        """Delete ONE visit event (DB row + snapshot). 404 if unknown."""
        if not is_safe_person_name(event_id):
            raise HTTPException(404, "event not found")
        loop = asyncio.get_event_loop()
        path = await loop.run_in_executor(None, lambda: db.delete_event(event_id))
        if path is None:
            raise HTTPException(404, "event not found")
        _safe_unlink_snapshot(event_id, path)
        await broadcast({"type": "history_update"})
        return JSONResponse({"deleted": event_id})

    @app.post("/history/clear")
    async def history_clear():
        """Delete ALL visit events + their snapshots. Known people and re-ID
        visitor memory are left untouched."""
        loop = asyncio.get_event_loop()
        paths = await loop.run_in_executor(None, db.clear_events)
        for p in paths:
            # event_id is unknown here; pass the stored path (still fenced to
            # history_dir by _safe_unlink_snapshot). Use its stem as the id hint.
            stem = os.path.splitext(os.path.basename(p))[0] if p else ""
            _safe_unlink_snapshot(stem, p)
        await broadcast({"type": "history_update"})
        return JSONResponse({"cleared": len(paths)})

    @app.get("/snapshot/{event_id}")
    def snapshot(event_id: str):
        e = db.get_event(event_id)
        p = e.get("snapshot_path") if e else ""
        if not p or not os.path.exists(p):
            alt = os.path.join(history_dir, f"{event_id}.jpg")
            if os.path.exists(alt):
                p = alt
            else:
                raise HTTPException(404, "snapshot not found")
        return FileResponse(p, media_type="image/jpeg")

    @app.post("/enroll")
    async def enroll(payload: dict = Body(...)):
        name = (payload.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name is required")
        face = getattr(pipeline, "face", None)
        if face is None or not face.available():
            raise HTTPException(
                503, "Face recognition is not available. Set ENABLE_FACE=True "
                     "in config.py (and install insightface) and restart.")
        frame = latest.get()
        if frame is None:
            raise HTTPException(503, "No camera frame available yet.")
        # Run enrollment off the event loop (InsightFace inference is blocking).
        loop = asyncio.get_event_loop()
        ok, message = await loop.run_in_executor(
            None, lambda: face.enroll_from_image(name, frame))
        return {"ok": ok, "message": message,
                "known_count": len(face.known_names)}

    @app.get("/known")
    def known():
        face = getattr(pipeline, "face", None)
        if face is None or not face.available():
            return {"people": [], "known_count": 0}
        # Phase 13: richer list (name + photo count + thumbnail URL). Each entry
        # keeps a `count` alias so older clients that read p.count still work.
        return {"people": face.list_people(),
                "known_count": len(face.known_names)}

    # --- Phase 13: upload-photo enrollment + known-people management ---------
    @app.post("/enroll_upload")
    async def enroll_upload(name: str = Form(...),
                            files: list[UploadFile] = File(...)):
        """Enroll a known person from one or MORE uploaded photos.

        multipart/form-data: `name` + one or more image `files`. Each photo's
        face embedding is extracted and added to the recognition gallery
        immediately (no restart) and persisted. Non-image files are skipped
        gracefully (never a 500). 400 on empty name / no files; 503 when face
        recognition is off."""
        person = (name or "").strip()
        if not person:
            raise HTTPException(400, "name is required")
        if not is_safe_person_name(person):
            raise HTTPException(400, "name may not contain '/', '\\', or '..'")
        face = getattr(pipeline, "face", None)
        if face is None or not face.available():
            raise HTTPException(
                503, "Face recognition is not available. Set ENABLE_FACE=True "
                     "in config.py (and install insightface) and restart.")
        if not files:
            raise HTTPException(400, "at least one photo file is required")

        # Decode each upload to a BGR image off the event loop. A file that isn't
        # a decodable image becomes None -> enroll_from_files records a clean skip.
        images = []
        for uf in files:
            data = await uf.read()
            img = None
            if data:
                try:
                    arr = np.frombuffer(data, dtype=np.uint8)
                    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                except Exception:
                    img = None
            images.append(img)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: face.enroll_from_files(person, images))
        await broadcast({"type": "known_update"})
        return JSONResponse(result)

    @app.post("/known/delete")
    async def known_delete(payload: dict = Body(...)):
        """Delete a known person (in-memory gallery + saved photos + DB rows)."""
        name = (payload.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name is required")
        if not is_safe_person_name(name):
            raise HTTPException(400, "name may not contain '/', '\\', or '..'")
        face = getattr(pipeline, "face", None)
        if face is None or not face.available():
            raise HTTPException(
                503, "Face recognition is not available. Set ENABLE_FACE=True "
                     "in config.py and restart.")
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: face.remove_person(name))
        await broadcast({"type": "known_update"})
        return JSONResponse(result)

    @app.get("/known_photo/{name}")
    def known_photo(name: str):
        """Return a representative saved photo for `name` (thumbnail), 404 if none."""
        person = (name or "").strip()
        if not is_safe_person_name(person):
            raise HTTPException(404, "not found")
        face = getattr(pipeline, "face", None)
        if face is None:
            raise HTTPException(404, "not found")
        path = face.sample_photo_path(person)
        if not path or not os.path.exists(path):
            raise HTTPException(404, "no photo for this person")
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/vlm_status")
    def vlm_status():
        """Phase 6: report the cloud VLM's wiring WITHOUT ever leaking a key.

        Keys are masked to last-4 by the module. Safe to expose to the dashboard
        so a user can see at a glance whether scene description is live.
        """
        vlm = getattr(pipeline, "vlm", None)
        if vlm is None:
            return {"enabled": bool(getattr(pipeline, "vlm_enabled", False)),
                    "available": False, "reason": "VLM not built (ENABLE_VLM off)"}
        st = vlm.status()
        st["enabled"] = bool(getattr(pipeline, "vlm_enabled", False))
        st["only_for_unknown"] = bool(getattr(pipeline, "vlm_only_for_unknown",
                                              True))
        return st

    @app.get("/speech_status")
    def speech_status():
        """Phase 7: report speech-recognition capabilities for the status pill."""
        speech = getattr(pipeline, "speech", None)
        if speech is None:
            return {"enabled": bool(getattr(pipeline, "speech_enabled", False)),
                    "available": False}
        caps = speech.capabilities()
        return {"enabled": bool(getattr(pipeline, "speech_enabled", False)),
                "available": speech.available(),
                "model": getattr(speech, "model_name", ""),
                **caps}

    @app.post("/transcribe")
    async def transcribe(file: UploadFile = File(...)):
        """Phase 7: transcribe an uploaded WAV - lets speech be tested without a
        mic. Returns {text, language}. 503 if speech recognition is unavailable."""
        speech = getattr(pipeline, "speech", None)
        if speech is None or not speech.available():
            raise HTTPException(
                503, "Speech recognition is not available. Set ENABLE_SPEECH=True "
                     "in config.py (and install openai-whisper) and restart.")
        data = await file.read()
        if not data:
            raise HTTPException(400, "empty upload")
        loop = asyncio.get_event_loop()
        text, lang = await loop.run_in_executor(
            None, lambda: speech.transcribe_wav(data))
        return {"text": text, "language": lang}

    @app.get("/translate_status")
    def translate_status():
        """Phase 8: report the translation backend + target language for the pill."""
        tr = getattr(pipeline, "translate", None)
        enabled = bool(getattr(pipeline, "translate_enabled", False))
        if tr is None:
            return {"enabled": enabled, "backend": "none", "available": False,
                    "user_language": "en"}
        st = tr.status()
        st["enabled"] = enabled
        return st

    @app.post("/translate")
    async def translate(payload: dict = Body(...)):
        """Phase 8: translate a text string (lets translation be tested without
        speech). Returns {translated}. Falls back to the original text on any
        failure (graceful passthrough), so this never 500s on missing keys."""
        tr = getattr(pipeline, "translate", None)
        if tr is None or not getattr(pipeline, "translate_enabled", False):
            raise HTTPException(
                503, "Translation is not available. Set ENABLE_TRANSLATE=True in "
                     "config.py and restart.")
        text = (payload.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "text is required")
        src = (payload.get("src") or "").strip()
        target = (payload.get("target") or "").strip() or None
        loop = asyncio.get_event_loop()
        out = await loop.run_in_executor(
            None, lambda: tr.translate(text, src_lang=src, target_lang=target))
        return {"translated": out, "backend": tr.backend_name(),
                "available": tr.available()}

    @app.post("/user_language")
    async def user_language(payload: dict = Body(...)):
        """Phase 8: change the user's target language live. Updates the injected
        TranslateModule (the pipeline reads its user_language) AND persists the
        choice to data/user_language.txt so it survives a restart (loaded back in
        run.py at boot). Persistence is best-effort: a write failure never blocks
        the live change."""
        lang = (payload.get("lang") or "").strip()
        if not lang:
            raise HTTPException(400, "lang is required")
        tr = getattr(pipeline, "translate", None)
        if tr is None:
            raise HTTPException(503, "Translation is not available.")
        tr.set_user_language(lang)
        # Persist next to the DB (data/), derived from history_dir's parent so we
        # don't need to import config here. Sanitise to a short code first.
        code = "".join(c for c in tr.user_language if c.isalnum() or c in "-_")[:16]
        try:
            data_dir = os.path.dirname(os.path.realpath(history_dir))
            with open(os.path.join(data_dir, "user_language.txt"), "w",
                      encoding="utf-8") as fh:
                fh.write(code)
        except Exception as e:                                # best-effort only
            print(f"[server] could not persist user_language: {e}")
        return {"user_language": tr.user_language,
                "user_language_name": tr.lang_name(tr.user_language)}

    @app.get("/reid_status")
    def reid_status():
        """Phase 9: report the re-ID backend + gallery size for the status pill."""
        reid = getattr(pipeline, "reid", None)
        enabled = bool(getattr(pipeline, "reid_enabled", False))
        if reid is None:
            return {"enabled": enabled, "available": False,
                    "backend": "none", "gallery_size": 0, "placeholder": False}
        return {"enabled": enabled, "available": reid.available(),
                "backend": reid.backend_name(),
                "gallery_size": reid.gallery_size(),
                "placeholder": reid.is_placeholder()}

    @app.get("/suggestions")
    async def suggestions():
        """Phase 9: open 'save this visitor?' prompts from auto-enroll clustering.

        Recomputes lazily (DBSCAN) inside suggestions(), so this is the natural
        refresh point for the UI. Runs off the event loop (clustering is CPU)."""
        ae = getattr(pipeline, "autoenroll", None)
        if ae is None or not ae.available():
            return {"suggestions": []}
        loop = asyncio.get_event_loop()
        items = await loop.run_in_executor(None, ae.suggestions)
        return {"suggestions": items}

    @app.post("/suggestions/confirm")
    async def suggestions_confirm(payload: dict = Body(...)):
        """Phase 9: promote a clustered unknown to a KNOWN face under `name`."""
        ae = getattr(pipeline, "autoenroll", None)
        if ae is None or not ae.available():
            raise HTTPException(503, "Auto-enrollment is not available.")
        cluster_id = (payload.get("cluster_id") or "").strip()
        name = (payload.get("name") or "").strip()
        if not cluster_id or not name:
            raise HTTPException(400, "cluster_id and name are required")
        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(None, lambda: ae.confirm(cluster_id, name))
        face = getattr(pipeline, "face", None)
        known_count = len(face.known_names) if face is not None else 0
        # Nudge any connected dashboards to refresh their suggestion list.
        await broadcast({"type": "suggestions_update"})
        return {"ok": bool(ok), "name": name, "known_count": known_count}

    @app.post("/suggestions/dismiss")
    async def suggestions_dismiss(payload: dict = Body(...)):
        """Phase 9: dismiss a suggestion without enrolling."""
        ae = getattr(pipeline, "autoenroll", None)
        if ae is None or not ae.available():
            raise HTTPException(503, "Auto-enrollment is not available.")
        cluster_id = (payload.get("cluster_id") or "").strip()
        if not cluster_id:
            raise HTTPException(400, "cluster_id is required")
        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(None, lambda: ae.dismiss(cluster_id))
        await broadcast({"type": "suggestions_update"})
        return {"ok": bool(ok)}

    # --- Phase 17: live captions (rolling transcription for deaf users) ------
    # POST /captions/on starts a loop: record a short chunk -> transcribe ->
    # translate -> broadcast {"type": "caption", ...} -> repeat, until
    # /captions/off or an idle timeout. Extends the one-shot /hear_visitor
    # into a real conversation view. Explicitly user-started, like Hear
    # Visitor - never automatic.
    _captions = {"on": False, "thread": None}
    _CAPTION_CHUNK_SECONDS = 4
    _CAPTION_MAX_SECONDS = 180        # hard stop: captions can't run forever

    def _caption_loop() -> None:
        started = time.monotonic()
        silent_chunks = 0
        while _captions["on"]:
            if time.monotonic() - started > _CAPTION_MAX_SECONDS:
                print("[Captions] max session length reached; stopping.")
                break
            try:
                audio = speech.record(_CAPTION_CHUNK_SECONDS)
                if audio is None:
                    break             # mic gone - end the session
                if speech.use_vad and not speech.has_speech(audio):
                    silent_chunks += 1
                    if silent_chunks >= 8:        # ~30s of silence -> stop
                        print("[Captions] long silence; stopping.")
                        break
                    continue
                silent_chunks = 0
                text, lang = speech.transcribe(audio)
                if not (text or "").strip():
                    continue
                translated = ""
                tr = getattr(pipeline, "translate", None)
                if tr is not None and getattr(pipeline, "translate_enabled",
                                              False):
                    target = getattr(tr, "user_language", "en")
                    if (lang or "") != target:
                        out = tr.translate(text, src_lang=lang,
                                           target_lang=target)
                        if out and out.strip() and out.strip() != text.strip():
                            translated = out.strip()
                broadcast_threadsafe({"type": "caption", "text": text,
                                      "translated": translated,
                                      "language": lang})
            except Exception as e:                        # pragma: no cover
                print(f"[Captions] loop error (continuing): {e}")
        _captions["on"] = False
        broadcast_threadsafe({"type": "caption_state", "on": False})

    @app.post("/captions/{action}")
    async def captions_toggle(action: str):
        """Start/stop the live caption stream ("on"/"off"). Deaf-mode two-way
        conversations: chunked transcripts arrive as "caption" WS messages."""
        if action not in ("on", "off"):
            raise HTTPException(400, "action must be 'on' or 'off'")
        if action == "off":
            _captions["on"] = False
            return {"ok": True, "on": False}
        if speech is None or not speech.available():
            raise HTTPException(
                503, "Speech recognition is not available for captions.")
        if _captions["on"]:
            return {"ok": True, "on": True}
        _captions["on"] = True
        t = threading.Thread(target=_caption_loop, daemon=True,
                             name="caption-loop")
        _captions["thread"] = t
        t.start()
        await broadcast({"type": "caption_state", "on": True})
        return {"ok": True, "on": True,
                "chunk_seconds": _CAPTION_CHUNK_SECONDS,
                "max_seconds": _CAPTION_MAX_SECONDS}

    @app.get("/captions_status")
    def captions_status():
        return {"on": bool(_captions["on"])}

    @app.get("/quick_replies")
    def quick_replies():
        """Phase 17: canned one-tap reply sentences (config.QUICK_REPLIES).
        Clients render them as buttons beside the free-text reply box."""
        return {"replies": list(getattr(cfg, "QUICK_REPLIES", []) or [])}

    @app.post("/reply")
    async def reply(payload: dict = Body(...)):
        """Two-way reply: speak a typed message at the door (Phase 4).

        `lang` (optional, Phase 17): ISO code of the typed text - a deaf user
        replying in Malayalam gets a Malayalam voice at the door."""
        text = (payload.get("text") or "").strip()
        lang = str(payload.get("lang") or "").strip().lower()
        if not text:
            raise HTTPException(400, "text is required")
        spoken = False
        if access is not None:
            spoken = bool(access.speak_text(text, lang=lang))
        elif tts is not None:
            spoken = bool(tts.speak(text, lang=lang))
        engine = tts.engine_name() if tts is not None else "none"
        return {"ok": True, "spoken": spoken, "engine": engine, "text": text}

    # --- Phase 14: phone-side speech + text/voice commands (mobile app) -------
    @app.get("/speak_audio")
    async def speak_audio(text: str = "", lang: str = ""):
        """Synthesize `text` with the natural Kokoro voice and return WAV bytes for
        the PHONE to play (mobile Blind-mode speech). Does NOT speak on the server.
        `lang` (Phase 17): ISO hint routing non-English text to a matching voice.
        Clean-JSON 503 when no synth backend is available -> the app falls back to
        the browser Web Speech API. Runs synthesis OFF the announcement worker."""
        text = (text or "").strip()
        if not text:
            raise HTTPException(400, "text is required")
        if tts is None or not hasattr(tts, "synth_wav_bytes"):
            raise HTTPException(503, "TTS synthesis is not available.")
        loop = asyncio.get_event_loop()
        wav, _sr = await loop.run_in_executor(
            None, lambda: tts.synth_wav_bytes(text, lang=lang))
        if not wav:
            raise HTTPException(
                503, "Could not synthesize audio; use the browser voice fallback.")
        return Response(content=wav, media_type="audio/wav",
                        headers={"Cache-Control": "no-store"})

    @app.post("/command")
    async def command(payload: dict = Body(...)):
        """Text/voice command from the phone. Reuses the existing voice_commands
        parser + handler (same intents as push-to-talk) and returns {intent, answer}.
        The phone speaks the answer via /speak_audio. Never 500s on an unknown
        command - it returns a helpful fallback sentence."""
        text = (payload.get("text") or "").strip()
        if not text:
            raise HTTPException(400, "text is required")
        parsed = voice_commands.parse_command(text)
        intent = parsed.get("intent", "unknown")
        cmd_args = parsed.get("args", {})
        loop = asyncio.get_event_loop()
        answer = await loop.run_in_executor(
            None, lambda: voice_commands.handle_command(
                intent, cmd_args, pipeline=pipeline, db=db, latest=latest,
                access=access))
        # Nudge open dashboards (mirrors /listen's "voice" broadcast shape).
        await broadcast({"type": "voice", "intent": intent, "answer": answer,
                         "text": text})
        return {"intent": intent, "answer": answer, "text": text}

    @app.post("/ask")
    async def ask(payload: dict = Body(...)):
        """Phase 16: free-form question about the CURRENT camera frame, answered by
        the VLM (e.g. 'what colour is their dress', 'what is he doing now').

        ADDITIVE - does not touch /command or any existing route. Returns a short,
        hedged {answer}. 503 (clean JSON, never a stack trace) when the VLM is
        disabled or has no keys, mirroring /translate. Optionally speaks the answer
        when speak=true so a Blind user hears it hands-free."""
        question = (payload.get("question") or payload.get("text") or "").strip()
        if not question:
            raise HTTPException(400, "question is required")
        vlm = getattr(pipeline, "vlm", None)
        vlm_on = bool(getattr(pipeline, "vlm_enabled", False))
        if vlm is None or not vlm_on or not vlm.available():
            raise HTTPException(
                503, "Visual question answering is not available. Set ENABLE_VLM="
                     "True and provide GITHUB_MODELS_KEYS in .env, then restart.")
        loop = asyncio.get_event_loop()
        answer = await loop.run_in_executor(
            None, lambda: voice_commands._answer_scene(
                pipeline, latest, db, question))
        # Optional hands-free readback (Blind mode) - best-effort, never blocks.
        if bool(payload.get("speak")) and access is not None and answer:
            try:
                await loop.run_in_executor(None, lambda: access.speak_text(answer))
            except Exception as e:                        # pragma: no cover
                print(f"[server] /ask speak failed: {e}")
        await broadcast({"type": "voice", "intent": "ask_scene",
                         "answer": answer, "text": question})
        return {"question": question, "answer": answer}

    def _effective_mode(device: str | None) -> str:
        """The mode a given device should use: its own override if set, else the
        household default. `device` is an opaque client-chosen id (e.g. a phone)."""
        if device:
            ov = state["device_modes"].get(device)
            if ov in _VALID_MODES:
                return ov
        return state["mode"]

    @app.get("/mode")
    def get_mode(device: str | None = None):
        # A device with an override gets it; the response says which applied so a
        # client can show "following household default" vs "this device: Deaf".
        eff = _effective_mode(device)
        return {"mode": eff, "default_mode": state["mode"],
                "device": device,
                "device_override": (device is not None
                                    and device in state["device_modes"])}

    @app.post("/mode")
    async def set_mode(payload: dict = Body(...)):
        m = payload.get("mode")
        if m not in _VALID_MODES:
            raise HTTPException(400, "mode must be blind|deaf|both")
        device = (payload.get("device") or "").strip()
        if device:
            # Per-device override — does NOT touch the household default or the
            # shared accessibility engine (which drives the door-side speaker).
            state["device_modes"][device] = m
            return {"mode": m, "device": device, "device_override": True,
                    "default_mode": state["mode"]}
        state["mode"] = m
        # Drive the accessibility engine so speaking actually toggles live.
        if access is not None:
            access.set_mode(m)
        return {"mode": m, "device_override": False, "default_mode": m}

    @app.delete("/mode")
    async def clear_device_mode(device: str | None = None):
        """Drop a device's override so it follows the household default again."""
        if device:
            state["device_modes"].pop(device, None)
        return {"mode": state["mode"], "device": device,
                "device_override": False, "default_mode": state["mode"]}

    # --- Phase 11: natural voice picker -------------------------------------
    @app.get("/voices")
    def voices():
        """List selectable voices (offline Kokoro + online edge) with availability,
        plus the currently active voice + engine. Powers the dashboard picker."""
        if tts is None:
            return {"voices": [], "current": "none", "engine": "none",
                    "available": False}
        return {"voices": tts.list_voices(),
                "current": tts.current_voice(),
                "engine": tts.engine_name(),
                "available": tts.available(),
                "backends": tts.backends()}

    @app.post("/voice")
    async def set_voice(payload: dict = Body(...)):
        """Switch the active voice live, e.g. {"id":"kokoro:af_bella"}.

        On success we SPEAK a short confirmation in the NEW voice so the user
        hears the change. Returns clean JSON either way (never a stack trace):
        an unavailable/offline target yields {ok:false, message:...} and keeps the
        current voice - it does not 500."""
        if tts is None:
            raise HTTPException(503, "TTS is not available.")
        vid = (payload.get("id") or "").strip()
        if not vid:
            raise HTTPException(400, "id is required, e.g. 'kokoro:af_heart'")
        ok, message = tts.set_voice(vid)
        spoke = False
        if ok:
            # Speak the confirmation in the newly-selected voice (worker thread).
            spoke = bool(tts.speak("Voice changed. This is how I sound now."))
        return {"ok": bool(ok), "message": message,
                "engine": tts.engine_name(), "voice": tts.current_voice(),
                "spoke": spoke}

    @app.get("/tts_status")
    def tts_status():
        """Phase 11: TTS engine + voice + per-backend availability for the pill."""
        if tts is None:
            return {"enabled": False, "available": False, "engine": "none",
                    "voice": "none", "kokoro": False, "edge": False,
                    "pyttsx3": False}
        b = tts.backends()
        return {"enabled": bool(getattr(tts, "_enabled", False)),
                "available": tts.available(),
                "engine": tts.engine_name(),
                "voice": tts.current_voice(),
                "kokoro": b["kokoro"], "edge": b["edge"],
                "pyttsx3": b["pyttsx3"]}

    # --- Phase 10: voice commands (push-to-talk) ----------------------------
    @app.post("/listen")
    async def listen(file: UploadFile = File(None)):
        """Push-to-talk voice command. Records one command from the mic (or uses
        an uploaded WAV), parses it, acts, and SPEAKS the answer. Always works
        even when the always-on wake word is off. Returns what was heard + the
        spoken reply. Clean JSON 503 when speech recognition is unavailable."""
        if speech is None or not speech.available():
            raise HTTPException(
                503, "Speech recognition is not available. Set ENABLE_SPEECH=True "
                     "in config.py (and install openai-whisper) and restart.")
        wav_bytes = None
        if file is not None:
            wav_bytes = await file.read()
            if not wav_bytes:
                wav_bytes = None
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: voice_commands.run_voice_interaction(
                speech=speech, pipeline=pipeline, db=db, latest=latest,
                access=access, seconds=wakeword_command_seconds,
                wav_bytes=wav_bytes))
        await broadcast({"type": "voice", **result})
        return JSONResponse(result)

    # --- Phase 12: two-way "Hear Visitor" (OPT-IN visitor audio) -------------
    @app.post("/hear_visitor")
    async def hear_visitor():
        """Record the VISITOR for a few seconds ON DEMAND, transcribe + translate
        it, and attach the result to the most recent visitor event.

        This is DELIBERATELY separate from the doorbell: a plain /trigger records
        NOTHING. Audio is captured only when the user presses "Hear Visitor",
        making two-way communication explicit and consent-based. It is also
        distinct from /listen, which captures the BLIND USER's own voice COMMANDS.

        Returns {transcript, translated, language, event_id}. Clean JSON 503 when
        speech recognition / the mic is unavailable; never 500s on a failure."""
        if speech is None or not speech.available():
            raise HTTPException(
                503, "Speech recognition is not available. Set ENABLE_SPEECH=True "
                     "in config.py (and install openai-whisper) and restart.")

        secs = int(visitor_listen_seconds or 6)
        loop = asyncio.get_event_loop()

        def _capture():
            audio = speech.record(secs)
            if audio is None:
                return None, "", ""          # mic unavailable / capture failed
            if speech.use_vad and not speech.has_speech(audio):
                return "", "", ""            # silence -> empty transcript, no error
            text, lang = speech.transcribe(audio)
            return "captured", (text or ""), (lang or "")

        status, text, lang = await loop.run_in_executor(None, _capture)
        if status is None:
            raise HTTPException(
                503, "Could not capture audio (no microphone available).")

        # Translate into the user's language (fail-soft: fall back to the original).
        translated = ""
        tr = getattr(pipeline, "translate", None)
        if (text and tr is not None
                and getattr(pipeline, "translate_enabled", False)):
            target = getattr(tr, "user_language", "en")
            if (lang or "") != target:
                out = await loop.run_in_executor(
                    None, lambda: tr.translate(text, src_lang=lang,
                                               target_lang=target))
                if (out and out.strip() and out.strip() != text.strip()):
                    translated = out.strip()

        # Attach to the MOST RECENT event so the dashboard card + history show what
        # the visitor said. Standalone (no event yet) still returns the transcript.
        event_id = db.latest_event_id()
        if event_id:
            fields = {"speech_transcript": text, "language_detected": lang}
            if translated:
                fields["translated_transcript"] = translated
            await loop.run_in_executor(
                None, lambda: db.update_event_fields(event_id, **fields))

        await broadcast({"type": "visitor_speech", "text": text,
                         "translated": translated, "language": lang,
                         "event_id": event_id})
        return JSONResponse({"transcript": text, "translated": translated,
                             "language": lang, "event_id": event_id})

    @app.get("/wakeword_status")
    def wakeword_status():
        """Phase 10: report the always-on wake-word listener's state."""
        enabled = bool(getattr(cfg, "ENABLE_WAKEWORD", False)) if cfg else \
            (wakeword is not None)
        if wakeword is None:
            return {"enabled": enabled, "available": False, "running": False,
                    "model": "none", "placeholder": True,
                    "reason": "openWakeWord not built (ENABLE_WAKEWORD off or "
                              "package missing). Push-to-talk via /listen still works."}
        st = wakeword.status()
        st["enabled"] = enabled
        return st

    @app.post("/wakeword/{action}")
    async def wakeword_toggle(action: str):
        """Phase 10: start/stop the always-on listener at runtime (OPT-IN).

        The dashboard toggle hits this. Off by default: an open mic is a CPU +
        privacy choice the user makes deliberately. 503 (clean JSON) when the
        detector/mic isn't available - push-to-talk still works regardless."""
        if action not in ("on", "off"):
            raise HTTPException(400, "action must be 'on' or 'off'")
        if wakeword is None or not wakeword.available():
            raise HTTPException(
                503, "Always-on wake word is not available (openWakeWord or a mic "
                     "is missing). Use the push-to-talk 'Speak a command' button.")
        if action == "on":
            ok = wakeword.start()
        else:
            wakeword.stop()
            ok = True
        return {"ok": bool(ok), "running": wakeword.running(),
                "model": wakeword.model_name}

    # --- Phase 10: central health endpoint ----------------------------------
    @app.get("/status")
    def status():
        """One-stop health for every module + config flags + torch version.

        Powers the dashboard 'System Health' panel and the boot self-check. Never
        raises: each module is probed defensively. `state` is one of
        ok | placeholder | unavailable | off, so the UI can colour it."""
        return JSONResponse(_collect_status())

    # --- Phase 17: push-token registry (docs/MOBILE_PUSH.md) -----------------
    @app.post("/register_push")
    async def register_push(request: Request):
        """Store a device push token: {"token": "...", "platform"?, "mode"?}.

        Additive scaffolding - tokens are remembered even while ENABLE_PUSH is
        off, so enabling FCM later requires no client re-registration."""
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "JSON body required")
        token = str((body or {}).get("token", "")).strip()
        if not token or len(token) > 4096:
            raise HTTPException(400, "a non-empty 'token' is required")
        platform = str(body.get("platform", "web"))[:16]
        dev_mode = str(body.get("mode", "both"))[:16]
        created = db.push_register(token, platform=platform, mode=dev_mode)
        return {"ok": True, "created": bool(created),
                "delivery_enabled": push_enabled,
                "registered": len(db.push_tokens())}

    @app.post("/unregister_push")
    async def unregister_push(request: Request):
        """Remove a device push token: {"token": "..."}."""
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "JSON body required")
        token = str((body or {}).get("token", "")).strip()
        if not token:
            raise HTTPException(400, "a non-empty 'token' is required")
        removed = db.push_unregister(token)
        return {"ok": True, "removed": bool(removed)}

    # --- Phase 10: hardware doorbell webhook (ESP32-CAM readiness) -----------
    @app.post("/ring")
    async def ring(request: Request):
        """Hardware webhook: an ESP32 (or any device) POSTs to ring the bell.

        Optionally accepts a raw JPEG body (the device's own capture); otherwise
        it uses the latest frame from the configured camera, or a blank frame if
        headless. Identical downstream path to /trigger, so it drives the exact
        same event pipeline + dashboard broadcast.

        Auth (Phase 17): when RING_HMAC_SECRET is set, the request must carry
        X-Ring-Signature: hex(HMAC_SHA256(secret, raw body)) - the ESP32 signs
        with the shared secret and never holds the user's bearer token. A valid
        bearer token is accepted as an alternative (for manual testing)."""
        try:
            body = await request.body()
        except Exception:
            body = b""
        if ring_secret:
            sig = request.headers.get("x-ring-signature", "")
            want = _hmac.new(ring_secret.encode(), body,
                             hashlib.sha256).hexdigest()
            sig_ok = bool(sig) and _hmac.compare_digest(sig.lower(), want)
            # The bearer-token alternative only exists when a token is actually
            # CONFIGURED - _token_ok() is vacuously true with auth disabled,
            # and that must not neutralise the ring signature requirement.
            token_ok = bool(auth_token) and _token_ok(request)
            if not sig_ok and not token_ok:
                raise HTTPException(401, "invalid ring signature")
        frame = None
        source = "latest-frame"
        if body:
            try:
                arr = np.frombuffer(body, dtype=np.uint8)
                img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if img is not None:
                    frame = img
                    source = "posted-jpeg"
            except Exception as e:
                print(f"[Server] /ring JPEG decode failed, using camera: {e}")
        if frame is None:
            frame = latest.get()
        if frame is None:
            print("[Server] /ring: no frame available; using blank gray frame.")
            frame = np.full((720, 1280, 3), 127, dtype=np.uint8)
            source = "blank"
        loop = asyncio.get_event_loop()
        ev = await loop.run_in_executor(
            None, lambda: pipeline.run_once(frame, trigger="ring"))
        _suppress_motion()
        payload = {"type": "event", "event": _jsonify(ev.to_dict())}
        await broadcast(payload)
        return JSONResponse({"ok": True, "frame_source": source,
                             "event": payload["event"]})

    # ------------------------------------------------------------------
    def _safe(fn, default=None):
        try:
            return fn()
        except Exception:
            return default

    def _mod(name, enabled, available, placeholder=False, detail=""):
        enabled = bool(enabled)
        available = bool(available)
        if not enabled:
            st = "off"
        elif not available:
            st = "unavailable"
        elif placeholder:
            st = "placeholder"
        else:
            st = "ok"
        return {"name": name, "enabled": enabled, "available": available,
                "placeholder": bool(placeholder), "state": st, "detail": detail}

    def _collect_status():
        torch_version = _safe(lambda: __import__("torch").__version__,
                              "not installed")
        modules = []

        face = getattr(pipeline, "face", None)
        modules.append(_mod(
            "face", getattr(pipeline, "face_enabled", False),
            face is not None and _safe(face.available, False),
            detail=(f"{len(face.known_names)} known" if face is not None else "")))

        vision = getattr(pipeline, "vision", None)
        modules.append(_mod(
            "vision", getattr(pipeline, "vision_enabled", False),
            vision is not None and _safe(vision.available, False),
            detail="YOLOv8 object detection"))

        anti = getattr(pipeline, "antispoof", None)
        anti_backend = _safe(lambda: anti.backend_name(), "") if anti else ""
        modules.append(_mod(
            "antispoof", getattr(pipeline, "antispoof_enabled", False),
            anti is not None and _safe(anti.available, False),
            placeholder=("heuristic" in (anti_backend or "").lower()),
            detail=anti_backend or "liveness check"))

        vlm = getattr(pipeline, "vlm", None)
        modules.append(_mod(
            "vlm", getattr(pipeline, "vlm_enabled", False),
            vlm is not None and _safe(vlm.available, False),
            detail="cloud scene description + OCR"))

        sp = getattr(pipeline, "speech", None)
        modules.append(_mod(
            "speech", getattr(pipeline, "speech_enabled", False),
            sp is not None and _safe(sp.available, False),
            detail=(f"Whisper '{getattr(sp, 'model_name', '')}'" if sp else "")))

        tr = getattr(pipeline, "translate", None)
        tr_backend = _safe(lambda: tr.backend_name(), "") if tr else ""
        modules.append(_mod(
            "translate", getattr(pipeline, "translate_enabled", False),
            tr is not None and _safe(tr.available, False),
            detail=tr_backend or "passthrough"))

        reid = getattr(pipeline, "reid", None)
        reid_ph = _safe(lambda: reid.is_placeholder(), False) if reid else False
        reid_backend = _safe(lambda: reid.backend_name(), "") if reid else ""
        modules.append(_mod(
            "reid", getattr(pipeline, "reid_enabled", False),
            reid is not None and _safe(reid.available, False),
            placeholder=bool(reid_ph),
            detail=(f"{reid_backend} ({_safe(reid.gallery_size, 0)} in gallery)"
                    if reid else "")))

        ae = getattr(pipeline, "autoenroll", None)
        modules.append(_mod(
            "autoenroll", getattr(pipeline, "autoenroll_enabled", False),
            ae is not None and _safe(ae.available, False),
            detail="DBSCAN face clustering"))

        # Phase 11: TTS shows the active engine + voice; a fall-back to the
        # robotic pyttsx3 (when the natural kokoro voice was requested but its
        # model is missing) is flagged as a placeholder so the panel goes amber.
        tts_engine = _safe(tts.engine_name, "none") if tts else "none"
        tts_voice = _safe(tts.current_voice, "none") if tts else "none"
        tts_fellback = bool(tts is not None and tts_engine == "pyttsx3"
                            and _safe(tts.available, False))
        modules.append(_mod(
            "tts", getattr(tts, "_enabled", tts is not None) if tts else False,
            tts is not None and _safe(tts.available, False),
            placeholder=tts_fellback,
            detail=(f"{tts_voice}"
                    + (" (robotic fallback - kokoro model missing)"
                       if tts_fellback else "")) if tts else "none"))

        ww_enabled = bool(getattr(cfg, "ENABLE_WAKEWORD", False)) if cfg else \
            (wakeword is not None)
        modules.append(_mod(
            "wakeword", ww_enabled,
            wakeword is not None and _safe(wakeword.available, False),
            # placeholder while on a pretrained phrase; a trained hey_access
            # model (scripts/train_wakeword.py) flips this to a real module.
            placeholder=(wakeword is None
                         or _safe(wakeword.is_placeholder, True)),
            detail=(f"{wakeword.model_name} "
                    f"({'running' if wakeword and wakeword.running() else 'idle'})"
                    if wakeword else "openWakeWord not built")))

        motion = getattr(app.state, "motion", None)
        modules.append(_mod(
            "motion", bool(getattr(cfg, "ENABLE_MOTION", False)) if cfg
            else (motion is not None),
            motion is not None and _safe(motion.available, False),
            detail=(f"absdiff ({'running' if motion and motion.running() else 'idle'}, "
                    f"{motion.status()['fires']} fires)"
                    if motion else "software motion trigger")))

        flags = {}
        if cfg is not None:
            for k in dir(cfg):
                if k.startswith("ENABLE_"):
                    flags[k] = bool(getattr(cfg, k))

        return {
            "app": "AccessAI",
            "phase": 10,
            "mode": state["mode"],
            "torch_version": torch_version,
            "voice_path": ("always-on+push-to-talk"
                           if (wakeword is not None
                               and _safe(wakeword.available, False))
                           else ("push-to-talk"
                                 if (speech is not None
                                     and _safe(speech.available, False))
                                 else "unavailable")),
            "wakeword_running": bool(wakeword.running()) if wakeword else False,
            "tts": {
                "engine": tts_engine,
                "voice": tts_voice,
                "backends": _safe(tts.backends, {}) if tts else {},
                "fellback_to_pyttsx3": tts_fellback,
            },
            "modules": modules,
            "flags": flags,
        }

    @app.websocket("/events")
    async def ws_events(ws: WebSocket):
        # Phase 17: the HTTP middleware doesn't see WebSocket upgrades, so the
        # token is checked here. Browsers can't set headers on a WebSocket, so
        # the query form (?token=) is the expected transport.
        if auth_token:
            supplied = ws.query_params.get("token", "")
            hdr = ws.headers.get("authorization", "")
            if hdr.lower().startswith("bearer "):
                supplied = supplied or hdr[7:]
            if not _secrets.compare_digest(supplied, auth_token):
                await ws.close(code=4401)
                return
        await ws.accept()
        async with clients_lock:
            clients.add(ws)
        try:
            while True:
                await asyncio.sleep(30)          # keep-alive ping
                await ws.send_json({"type": "ping"})
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            async with clients_lock:
                clients.discard(ws)

    return app


def _jsonify(d):
    """Recursively replace tuples with lists so JSON serialization is happy."""
    if isinstance(d, dict):
        return {k: _jsonify(v) for k, v in d.items()}
    if isinstance(d, (list, tuple)):
        return [_jsonify(x) for x in d]
    return d
