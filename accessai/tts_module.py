"""
TTSModule - non-blocking, thread-safe, MULTI-BACKEND text-to-speech.

Phase 11 upgrade: a NATURAL, human-like voice.
----------------------------------------------
Phases 1-10 spoke through pyttsx3/espeak - robotic. This module keeps the EXACT
same public interface (speak / available / engine_name) and the SAME non-blocking
single-worker-thread + queue design, but swaps the voice for natural TTS. The
rest of the app (accessibility.py, /trigger, /reply, voice commands) does not
change how it calls TTS at all.

Three backends sit behind one interface, tried in a graceful fallback order
(requested engine -> kokoro -> edge -> pyttsx3 -> text-only):

  1. kokoro  - Kokoro-ONNX, OFFLINE, private, via onnxruntime (NOT torch). The
               default. create() returns a float32 waveform @ 24 kHz which we
               play via a short-lived OS audio subprocess (see the playback note).
  2. edge    - edge-tts, Microsoft Neural voices, ONLINE (pure async HTTP, no
               torch). Saves an MP3 which we decode with soundfile and play.
               Used only if selected, or if kokoro is unavailable.
  3. pyttsx3 - the Phase-4 espeak path, kept as a LAST RESORT so we are never
               silent-and-crash. Worst case is text-only.

TORCH SAFETY (the whole reason for the ONNX build): kokoro-onnx uses onnxruntime
(already a dependency) and edge-tts is pure HTTP. Neither imports torch, so the
pinned torch 2.4.1 - and YOLO - stay intact. The standard `kokoro` pip package
depends on torch and is deliberately NOT used.

Why a worker thread + queue?
----------------------------
Synthesis AND playback are blocking. We do both inside ONE dedicated daemon
worker thread and feed it text through a queue. speak() just enqueues and returns
immediately, so the HTTP request thread that handles /trigger or /reply never
waits on audio.

Graceful degradation (a rule for every phase)
----------------------------------------------
A missing package, a missing Kokoro model download, an offline network (edge),
or a headless box with no audio device all degrade: the announcement text still
reaches the UI; only the audio is skipped, with a single logged hint.
"""

import os
import queue
import socket
import tempfile
import threading

# --- Guarded heavy imports; each sets a capability flag (never crash) --------
try:
    from kokoro_onnx import Kokoro
    _HAS_KOKORO = True
    _KOKORO_ERR = ""
except Exception as e:                                    # pragma: no cover
    _HAS_KOKORO = False
    _KOKORO_ERR = str(e)

try:
    import edge_tts
    _HAS_EDGE = True
    _EDGE_ERR = ""
except Exception as e:                                    # pragma: no cover
    _HAS_EDGE = False
    _EDGE_ERR = str(e)

# soundfile (bundled libsndfile 1.1.0) decodes edge-tts MP3 AND writes the WAV we
# hand to the OS audio player. numpy is a core dep used for sample handling.
try:
    import numpy as np
    import soundfile as sf
    _HAS_SOUNDFILE = True
except Exception:                                         # pragma: no cover
    _HAS_SOUNDFILE = False

# Playback runs in a SHORT-LIVED SUBPROCESS (aplay/paplay/pw-play/ffplay), NOT an
# in-process PortAudio stream. WHY: sounddevice's PortAudio/ALSA backend corrupts
# the process heap ("double free or corruption" from PaUnixThread_Terminate in
# pa_linux_alsa.c) when a playback stream is torn down repeatedly inside a
# long-lived server - an intermittent, uncatchable C-level abort. A subprocess
# player isolates any ALSA instability (a child crash never takes down the
# doorbell) and mirrors how the original espeak/pyttsx3 path already shelled out.
# We write a temp WAV with soundfile and play it; a headless box with no player
# simply skips audio (the announcement text still reaches the UI).
import shutil
import subprocess

_PLAYER_CANDIDATES = [
    ("paplay", ["paplay"]),                       # PulseAudio / PipeWire-pulse
    ("pw-play", ["pw-play"]),                     # native PipeWire
    ("aplay", ["aplay", "-q"]),                   # ALSA
    ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]),
]
_PLAYER_CMD = None
for _pname, _pcmd in _PLAYER_CANDIDATES:
    if shutil.which(_pname):
        _PLAYER_CMD = _pcmd
        break
_HAS_PLAYBACK = bool(_PLAYER_CMD and _HAS_SOUNDFILE)

try:
    import pyttsx3
    _HAS_PYTTSX3 = True
except Exception:                                         # pragma: no cover
    _HAS_PYTTSX3 = False

# Drop announcements if more than this many are already waiting (flood guard).
_MAX_QUEUE = 8

# --- Earcons (Phase 17 accessibility) ---------------------------------------
# A half-second identity chime played BEFORE the spoken sentence, one per
# alert kind, so a blind user classifies the visitor the instant audio starts
# - without waiting for the sentence. Pure numpy synthesis at first use; no
# asset files. Patterns are deliberately distinct in contour AND rhythm
# (contour alone is lost on users with pitch-perception differences):
#   known    - rising major third, two notes            (friendly "ta-da")
#   delivery - three quick even mid taps                ("knock knock knock")
#   unknown  - single long neutral mid tone             (plain doorbell-ish)
#   spoof    - falling tritone, two long low notes      (unmistakably "wrong")
_EARCON_SR = 24000
_EARCON_SPECS = {
    "known":    [(659.3, 0.14, 0.02), (830.6, 0.22, 0.16)],
    "delivery": [(523.3, 0.10, 0.02), (523.3, 0.10, 0.16), (523.3, 0.10, 0.30)],
    "unknown":  [(440.0, 0.35, 0.02)],
    "spoof":    [(466.2, 0.28, 0.02), (329.6, 0.34, 0.30)],
}


def _synth_earcon(kind: str):
    """Render one earcon to a float32 sample array (None for unknown kinds)."""
    import numpy as _np
    spec = _EARCON_SPECS.get(kind)
    if not spec:
        return None
    total = max(off + dur for _f, dur, off in spec) + 0.08
    out = _np.zeros(int(total * _EARCON_SR), dtype=_np.float32)
    for freq, dur, off in spec:
        n = int(dur * _EARCON_SR)
        t = _np.arange(n) / _EARCON_SR
        tone = _np.sin(2 * _np.pi * freq * t)
        # 12ms attack / exponential release envelope - no clicks.
        env = _np.minimum(1.0, t / 0.012) * _np.exp(-t / (dur * 0.55))
        i = int(off * _EARCON_SR)
        out[i:i + n] += (tone * env * 0.45).astype(_np.float32)
    peak = float(_np.max(_np.abs(out))) or 1.0
    return out / peak * 0.6

# Kokoro model file candidates (v1.0 has af_heart/af_bella/...; v0.19 fallback).
_KOKORO_MODEL_NAMES = ["kokoro-v1.0.onnx", "kokoro-v0_19.onnx", "kokoro.onnx"]
_KOKORO_VOICES_NAMES = ["voices-v1.0.bin", "voices-v1.0.json", "voices.bin",
                        "voices.json"]

_VALID_ENGINES = ("kokoro", "edge", "pyttsx3")


class TTSModule:
    def __init__(self, enabled: bool = True, engine: str = "kokoro",
                 voice: str = "af_heart", model_dir: str = "",
                 speed: float = 1.0, edge_voice: str = "en-US-AriaNeural",
                 rate: int = 165, volume: float = 1.0, lang: str = "en-us",
                 voice_choices=None, lang_voices=None):
        self._enabled = enabled
        self._model_dir = model_dir
        self._speed = float(speed)
        self._lang = lang or "en-us"
        self._rate = rate
        self._volume = volume
        self._edge_default = edge_voice or "en-US-AriaNeural"
        self._voice_choices = list(voice_choices or [])
        # Phase 17: ISO code -> edge-tts voice for non-English announcements.
        self._lang_voices = dict(lang_voices or {})

        # Active voice state, protected by _lock. set_voice() mutates these for
        # FUTURE utterances; the worker reads them (under the lock) per item, so a
        # live voice switch is thread-safe without touching the worker thread.
        self._lock = threading.Lock()
        # Serialises the Kokoro instance across the worker thread AND the Phase-14
        # /speak_audio synth path (synth_wav_bytes), so the two never call
        # create() on the same model at once. Held only for the brief synth call.
        self._synth_lock = threading.Lock()
        self._active_engine = None       # "kokoro" | "edge" | "pyttsx3" | "none"
        self._active_voice = ""          # backend-specific voice id

        # Queue items are (earcon_kind, text) tuples; earcon_kind may be "".
        self._queue: "queue.Queue" = queue.Queue()
        self._earcons: dict = {}     # alert kind -> cached sample array
        self._alive = False
        self._worker = None
        self._warned_no_audio = False

        # Lazy singletons OWNED by the worker thread (never touched elsewhere).
        self._kokoro = None              # Kokoro instance (~310MB, lazy-loaded)
        self._kokoro_loaded = False
        self._kokoro_failed = False
        self._pyttsx3_engine = None
        self._pyttsx3_failed = False

        if not enabled:
            print("[TTSModule] Disabled via config (ENABLE_TTS=False); text only.")
            self._active_engine = "none"
            return

        # Resolve the starting backend: requested -> kokoro -> edge -> pyttsx3.
        eng, v = self._resolve(engine, voice, self._edge_default)
        self._active_engine = eng
        self._active_voice = v
        if eng == "none":
            print("[TTSModule] No TTS backend available (kokoro/edge/pyttsx3 all "
                  "missing); spoken output disabled, text still shown in the UI.")
            if not _HAS_KOKORO:
                print(f"[TTSModule]   kokoro-onnx not importable: {_KOKORO_ERR}")
            return

        self._alive = True
        self._worker = threading.Thread(target=self._run, daemon=True,
                                        name="tts-worker")
        self._worker.start()
        b = self.backends()
        print(f"[TTSModule] active: {eng}:{v}  "
              f"(kokoro={b['kokoro']} edge={b['edge']} pyttsx3={b['pyttsx3']}, "
              f"playback={_HAS_PLAYBACK})")
        if eng == "pyttsx3" and (engine == "kokoro"):
            print("[TTSModule] NOTE: fell back to pyttsx3 (robotic) - Kokoro model "
                  f"files not found in {model_dir}. See requirements.txt for the "
                  "download URLs to get the natural offline voice.")

    # ------------------------------------------------------------------
    # Backend availability + resolution
    # ------------------------------------------------------------------
    def _kokoro_files(self):
        """Return (model_path, voices_path) if BOTH exist in model_dir, else None."""
        d = self._model_dir
        if not d or not os.path.isdir(d):
            return None
        model = voices = None
        for n in _KOKORO_MODEL_NAMES:
            p = os.path.join(d, n)
            if os.path.exists(p):
                model = p
                break
        for n in _KOKORO_VOICES_NAMES:
            p = os.path.join(d, n)
            if os.path.exists(p):
                voices = p
                break
        return (model, voices) if (model and voices) else None

    def _backend_usable(self, name: str) -> bool:
        """Can this backend produce speech right now (package + assets present)?

        Note: kokoro/edge synthesize even on a headless box; playback failure is a
        per-utterance runtime skip, not an availability failure.
        """
        if name == "kokoro":
            return bool(_HAS_KOKORO and self._kokoro_files() is not None)
        if name == "edge":
            return bool(_HAS_EDGE and _HAS_SOUNDFILE)
        if name == "pyttsx3":
            return bool(_HAS_PYTTSX3 and not self._pyttsx3_failed)
        return False

    def _resolve(self, req_engine: str, req_voice: str, edge_voice: str):
        """Pick the first usable backend in requested -> kokoro -> edge -> pyttsx3.

        Keeps the requested voice if we land on the requested engine; otherwise
        uses that engine's sensible default.
        """
        defaults = {"kokoro": "af_heart", "edge": edge_voice, "pyttsx3": ""}
        order = []
        for e in (req_engine, "kokoro", "edge", "pyttsx3"):
            if e and e not in order:
                order.append(e)
        for name in order:
            if self._backend_usable(name):
                if name == req_engine and req_voice:
                    return (name, req_voice)
                return (name, defaults.get(name, ""))
        return ("none", "")

    # ------------------------------------------------------------------
    # Worker thread: own the backends, drain the queue forever
    # ------------------------------------------------------------------
    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:                              # pragma: no cover
                break
            earcon, text, lang = item
            with self._lock:
                engine = self._active_engine
                voice = self._active_voice
            try:
                if earcon:
                    self._play_earcon(earcon)
                # Phase 17: a non-English lang hint with a mapped edge voice
                # overrides the active voice for THIS utterance only, so the
                # words are spoken by a voice that has their phonemes. Failure
                # (offline etc.) falls through to the normal cascade below.
                lang_voice = self._lang_voices.get(lang) if lang else None
                if lang_voice and lang not in ("en",):
                    if self._speak_edge(lang_voice, text):
                        continue
                    print(f"[TTSModule] '{lang}' voice {lang_voice} failed "
                          "(offline?); speaking with the default voice.")
                self._synth_and_play(engine, voice, text)
            except Exception as e:                        # pragma: no cover
                # One failing utterance must NEVER kill the worker thread.
                print(f"[TTSModule] speak failed on {engine}:{voice}: {e}")

    def _play_earcon(self, kind: str) -> None:
        """Play the identity chime for `kind` (cached synth, best-effort)."""
        try:
            if kind not in self._earcons:
                self._earcons[kind] = _synth_earcon(kind)
            samples = self._earcons[kind]
            if samples is not None:
                self._play(samples, _EARCON_SR)
        except Exception as e:                            # pragma: no cover
            print(f"[TTSModule] earcon '{kind}' failed (continuing): {e}")

    def _synth_and_play(self, engine: str, voice: str, text: str) -> None:
        """Dispatch to the active backend, cascading on synth failure."""
        if engine == "kokoro":
            if self._speak_kokoro(voice, text):
                return
            if self._speak_edge(self._edge_default, text):
                return
            self._speak_pyttsx3(text)
        elif engine == "edge":
            if self._speak_edge(voice, text):
                return
            if self._speak_kokoro("af_heart", text):
                return
            self._speak_pyttsx3(text)
        else:
            self._speak_pyttsx3(text)

    # --- Kokoro (offline, primary) ------------------------------------
    def _ensure_kokoro(self) -> bool:
        """Lazy-load the Kokoro model. MUST be called under _synth_lock (the worker
        and the /speak_audio path both use it). Returns True when self._kokoro is
        ready; sets _kokoro_failed on a hard failure so we don't retry forever."""
        if self._kokoro_loaded:
            return True
        if not _HAS_KOKORO or self._kokoro_failed:
            return False
        files = self._kokoro_files()
        if files is None:
            print(f"[TTSModule] Kokoro model files not found in "
                  f"{self._model_dir}; cannot use the offline voice.")
            self._kokoro_failed = True
            return False
        try:
            print("[TTSModule] loading Kokoro voice (first run loads the "
                  "~310MB model; downloaded once per install)...")
            self._kokoro = Kokoro(files[0], files[1])
            self._kokoro_loaded = True
            print("[TTSModule] Kokoro loaded.")
            return True
        except Exception as e:
            print(f"[TTSModule] Kokoro load failed: {e}")
            self._kokoro_failed = True
            return False

    def _speak_kokoro(self, voice: str, text: str) -> bool:
        with self._synth_lock:
            if not self._ensure_kokoro():
                return False
            try:
                samples, sr = self._kokoro.create(
                    text, voice=voice or "af_heart", speed=self._speed,
                    lang=self._lang)
            except Exception as e:
                print(f"[TTSModule] Kokoro synth failed for voice '{voice}': {e}")
                return False
        # Playback happens OUTSIDE the synth lock so it never blocks /speak_audio.
        return self._play(samples, sr)

    # --- Phase 14: synth to WAV bytes for the mobile app (/speak_audio) --------
    def synth_wav_bytes(self, text: str, lang: str = ""):
        """Synthesize `text` to WAV bytes WITHOUT playing it on the server.

        Powers GET /speak_audio so the mobile app can speak the announcement on the
        PHONE in the natural Kokoro voice. Prefers Kokoro (offline, private); falls
        back to edge-tts (online) if that's the active engine. `lang` (Phase 17)
        routes a non-English text to its LANGUAGE_VOICES edge voice first, so a
        translated announcement reaches the phone in a matching voice. Returns
        (wav_bytes, sample_rate), or (None, 0) when no backend can synthesize (the
        caller then lets the phone fall back to the browser voice). Never raises."""
        text = (text or "").strip()
        if not text or not _HAS_SOUNDFILE:
            return None, 0
        with self._lock:
            engine, voice = self._active_engine, self._active_voice
        # Phase 17: a mapped non-English language goes to its edge voice first
        # (Kokoro has no Indic phonemes - it would mangle the words).
        lang = (lang or "").lower()
        lang_voice = self._lang_voices.get(lang) if lang and lang != "en" else None
        if lang_voice:
            out = self._edge_wav_bytes(lang_voice, text)
            if out is not None:
                return out
        # Prefer Kokoro (offline, natural, private) whenever its model is present.
        if self._backend_usable("kokoro"):
            kv = voice if (engine == "kokoro" and voice) else "af_heart"
            with self._synth_lock:
                if self._ensure_kokoro():
                    try:
                        samples, sr = self._kokoro.create(
                            text, voice=kv, speed=self._speed, lang=self._lang)
                        return self._samples_to_wav(samples, sr), int(sr)
                    except Exception as e:                # pragma: no cover
                        print(f"[TTSModule] /speak_audio kokoro synth failed: {e}")
        # Fallback: edge-tts (online neural) -> decode its mp3 to WAV bytes.
        if self._backend_usable("edge"):
            ev = voice if (engine == "edge" and voice) else self._edge_default
            out = self._edge_wav_bytes(ev, text)
            if out is not None:
                return out
        return None, 0

    def _samples_to_wav(self, samples, sr) -> bytes:
        """Encode a float waveform to 16-bit PCM WAV bytes (in-memory, no file)."""
        import io
        buf = io.BytesIO()
        sf.write(buf, samples, int(sr), format="WAV", subtype="PCM_16")
        return buf.getvalue()

    def _edge_wav_bytes(self, voice: str, text: str):
        """edge-tts -> temp mp3 -> (wav_bytes, sr). None on any failure / offline."""
        if not (_HAS_EDGE and _HAS_SOUNDFILE):
            return None
        import asyncio
        tmp_path = None
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp_path = tmp.name
            tmp.close()

            async def _gen():
                comm = edge_tts.Communicate(text, voice or self._edge_default)
                await comm.save(tmp_path)

            asyncio.run(_gen())                      # no running loop off the worker
            data, sr = sf.read(tmp_path, dtype="float32")
            return self._samples_to_wav(data, sr), int(sr)
        except Exception as e:                            # pragma: no cover
            print(f"[TTSModule] /speak_audio edge synth failed (offline?): {e}")
            return None
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:                        # pragma: no cover
                    pass

    # --- edge-tts (online neural, fallback) ---------------------------
    def _speak_edge(self, voice: str, text: str) -> bool:
        if not (_HAS_EDGE and _HAS_SOUNDFILE):
            return False
        import asyncio
        tmp_path = None
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp_path = tmp.name
            tmp.close()

            async def _gen():
                comm = edge_tts.Communicate(text, voice or self._edge_default)
                await comm.save(tmp_path)

            asyncio.run(_gen())                      # no running loop in worker
            data, sr = sf.read(tmp_path, dtype="float32")
            return self._play(data, sr)
        except Exception as e:
            # Offline / HTTP failure / decode error -> let the caller cascade.
            print(f"[TTSModule] edge-tts failed for voice '{voice}' "
                  f"(offline?): {e}")
            return False
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:                        # pragma: no cover
                    pass

    # --- pyttsx3 (espeak, last resort) --------------------------------
    def _speak_pyttsx3(self, text: str) -> bool:
        if not _HAS_PYTTSX3 or self._pyttsx3_failed:
            return False
        if self._pyttsx3_engine is None:
            try:
                self._pyttsx3_engine = pyttsx3.init()
                self._pyttsx3_engine.setProperty("rate", self._rate)
                self._pyttsx3_engine.setProperty("volume", self._volume)
            except Exception as e:
                print(f"[TTSModule] pyttsx3 init failed: {e}")
                self._pyttsx3_failed = True
                return False
        try:
            self._pyttsx3_engine.say(text)
            self._pyttsx3_engine.runAndWait()
            return True
        except Exception as e:                            # pragma: no cover
            print(f"[TTSModule] pyttsx3 speak failed: {e}")
            return False

    # --- Playback (headless-safe, crash-isolated subprocess) ----------
    def _play(self, samples, sr) -> bool:
        """Write samples to a temp WAV and play it via a short-lived OS player
        subprocess (never an in-process PortAudio stream - see the import note).

        Returns True when SYNTHESIS succeeded, even if there is no player/device -
        a headless box should NOT cascade to pyttsx3 (it would fail to play too);
        the text is already shown in the UI. A player crash stays in the child."""
        if not _HAS_PLAYBACK or not _HAS_SOUNDFILE:
            return True
        wav_path = None
        try:
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            wav_path = tmp.name
            tmp.close()
            # PCM_16 WAV is the most universally playable across all players.
            sf.write(wav_path, samples, int(sr), subtype="PCM_16")
            subprocess.run(_PLAYER_CMD + [wav_path],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,
                           timeout=60, check=False)
        except Exception as e:
            if not self._warned_no_audio:
                print(f"[TTSModule] audio playback unavailable (headless?): {e}. "
                      "Speech is synthesized but not played; text still shown.")
                self._warned_no_audio = True
        finally:
            if wav_path and os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except Exception:                        # pragma: no cover
                    pass
        return True

    # ------------------------------------------------------------------
    # Public interface (UNCHANGED from Phase 4, plus additive voice methods)
    # ------------------------------------------------------------------
    def available(self) -> bool:
        return bool(self._enabled and self._active_engine not in (None, "none"))

    def engine_name(self) -> str:
        return self._active_engine if self.available() else "none"

    def warm(self) -> None:
        """Load the Kokoro model NOW instead of on the first utterance, so the
        first doorbell announcement starts speaking immediately (loading ~310MB
        lazily used to add seconds to the FIRST alert). Safe from a background
        thread; takes the same _synth_lock as every synth path; never raises."""
        if self._active_engine != "kokoro":
            return
        try:
            with self._synth_lock:
                self._ensure_kokoro()
        except Exception as e:                            # pragma: no cover
            print(f"[TTSModule] warm-up failed (will retry on use): {e}")

    def current_voice(self) -> str:
        """e.g. 'kokoro:af_heart'  (or just the engine when no voice id applies)."""
        if not self.available():
            return "none"
        with self._lock:
            eng, v = self._active_engine, self._active_voice
        return f"{eng}:{v}" if v else eng

    def backends(self) -> dict:
        """Which backends are usable right now - powers /tts_status + /status."""
        return {"kokoro": self._backend_usable("kokoro"),
                "edge": self._backend_usable("edge"),
                "pyttsx3": self._backend_usable("pyttsx3")}

    def list_voices(self) -> list:
        """VOICE_CHOICES annotated with per-voice availability (offline vs online)."""
        b = self.backends()
        out = []
        for c in self._voice_choices:
            vid = c.get("id", "")
            eng = vid.split(":", 1)[0] if ":" in vid else ""
            out.append({**c, "engine": eng,
                        "available": bool(b.get(eng, False))})
        return out

    def set_voice(self, voice_id: str):
        """Switch the active backend + voice for FUTURE utterances (thread-safe).

        Returns (ok, message). On failure the current voice is kept untouched:
          - malformed id                 -> (False, reason)
          - unknown engine               -> (False, reason)
          - engine package/model missing -> (False, reason)
          - edge chosen but offline      -> (False, reason)  [we probe first]
        """
        if not voice_id or ":" not in voice_id:
            return (False, f"invalid voice id '{voice_id}' "
                           f"(expected 'engine:voice')")
        eng, v = voice_id.split(":", 1)
        eng, v = eng.strip(), v.strip()
        if eng not in _VALID_ENGINES:
            return (False, f"unknown engine '{eng}'")
        if not self._backend_usable(eng):
            why = ("model not downloaded" if eng == "kokoro"
                   else ("package/decoder missing" if eng == "edge"
                         else "package missing"))
            return (False, f"{eng} voice unavailable ({why}); keeping "
                           f"{self.current_voice()}.")
        if eng == "edge" and not self._edge_reachable():
            return (False, "edge voices need internet and the Microsoft service "
                           f"is not reachable right now; keeping "
                           f"{self.current_voice()}.")
        with self._lock:
            self._active_engine = eng
            self._active_voice = v
        return (True, f"voice set to {eng}:{v}")

    def speak(self, text: str, earcon: str = "", lang: str = "") -> bool:
        """Queue text for speaking. Returns True if queued, else False.

        `earcon` (optional): an alert-kind name ("known"/"delivery"/"unknown"/
        "spoof") whose half-second identity chime plays before the sentence,
        so a blind user classifies the visitor before the words start.

        `lang` (optional, Phase 17): ISO code of the TEXT's language. A
        non-English code with a LANGUAGE_VOICES mapping routes this one
        utterance to the matching edge-tts voice (so Malayalam text is spoken
        by a Malayalam voice, not English phonemes); offline or unmapped ->
        the normal cascade speaks it anyway.

        Never blocks and never raises. Drops the request if TTS is unavailable or
        the queue is flooded (stale announcements aren't worth backing up)."""
        if not text or not self.available():
            return False
        if self._queue.qsize() >= _MAX_QUEUE:
            print("[TTSModule] Queue full; dropping announcement.")
            return False
        try:
            self._queue.put_nowait((earcon, text, lang or ""))
            return True
        except Exception:                                 # pragma: no cover
            return False

    # ------------------------------------------------------------------
    @staticmethod
    def _edge_reachable(timeout: float = 3.0) -> bool:
        """Quick TCP probe of the edge-tts endpoint so set_voice() can decline
        an online voice gracefully instead of failing mid-utterance."""
        try:
            s = socket.create_connection(("speech.platform.bing.com", 443),
                                         timeout)
            s.close()
            return True
        except Exception:
            return False
