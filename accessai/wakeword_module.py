"""
WakeWordModule - always-on wake-word detection (Phase 10, OPT-IN).

An always-listening microphone that fires a callback when it hears the wake
phrase, making the doorbell fully hands-free for a blind user. It is the ONLY
new "always on" component in the project and is therefore off by default
(WAKEWORD_ALWAYS_ON=False): an open mic costs CPU and is a privacy decision the
user must make deliberately. Push-to-talk (/listen) always works without it.

Detector: openWakeWord - a pure-Python, CPU-only detector that runs small ONNX
models via onnxruntime (already a project dependency). Model priority (the same
auto-upgrade pattern as antispoof / re-ID - drop a file in a dir, zero code
change):
  A CUSTOM   - the first *.onnx in WAKEWORD_MODEL_DIR (models/wakeword/), e.g.
               the "Hey Access" model trained by scripts/train_wakeword.py.
               When present the module is NO LONGER a placeholder.
  B PRETRAINED - openWakeWord's shipped phrases (hey_jarvis, alexa, ...),
               auto-downloaded on first use. Used as a loudly-logged PLACEHOLDER
               phrase until the custom model exists.

Follows the house module contract exactly:
  * guarded imports with _HAS_* flags; NEVER raise from __init__
  * available() reports whether it can actually run
  * a daemon thread owns the mic; start()/stop() are idempotent
  * on any failure it logs a hint and becomes a no-op - the app never crashes
"""

import glob
import os
import threading
import time as _time

import numpy as np

# --- Guarded heavy imports (never crash if a piece is missing) ---------------
try:
    from openwakeword.model import Model as _OWWModel
    import openwakeword as _oww
    _HAS_OWW = True
except Exception as e:                                   # pragma: no cover
    _HAS_OWW = False
    print(f"[WakeWord] openWakeWord unavailable, always-on disabled: {e}")

try:
    import sounddevice as _sd
    _HAS_MIC = True
except Exception as e:                                   # pragma: no cover
    _HAS_MIC = False
    print(f"[WakeWord] sounddevice unavailable, live mic disabled: {e}")


# openWakeWord expects 16 kHz int16 audio fed in ~80 ms chunks.
_SAMPLE_RATE = 16000
_CHUNK = 1280            # 80 ms at 16 kHz


class WakeWordModule:
    def __init__(self, model: str = "hey_jarvis", threshold: float = 0.5,
                 on_wake=None, cooldown: float = 6.0,
                 inference_framework: str = "onnx",
                 model_dir: str = ""):
        self.model_name = model
        self.threshold = float(threshold)
        self.cooldown = float(cooldown)
        self._on_wake = on_wake
        self._inference_framework = inference_framework
        self._custom = False            # True once a trained model is loaded

        self._model = None
        self._thread = None
        self._stop = threading.Event()
        self._running = False
        self._last_fire = 0.0
        self._has_oww = _HAS_OWW
        self._has_mic = _HAS_MIC

        if not _HAS_OWW:
            return

        # Model priority A: a CUSTOM trained model (e.g. "Hey Access" from
        # scripts/train_wakeword.py) sitting in model_dir. openwakeword.Model
        # accepts a file path directly and names it after the file stem.
        custom_path = self._find_custom(model_dir)
        if custom_path:
            try:
                self._model = _OWWModel(
                    wakeword_models=[custom_path],
                    inference_framework=self._inference_framework,
                )
                self.model_name = os.path.splitext(
                    os.path.basename(custom_path))[0]
                self._custom = True
                print(f"[WakeWord] Ready: CUSTOM model '{self.model_name}' "
                      f"({custom_path}) threshold={self.threshold} - say "
                      f"'{self.model_name.replace('_', ' ')}'.")
                return
            except Exception as e:                        # pragma: no cover
                print(f"[WakeWord] Custom model load failed ({e}); "
                      "falling back to the pretrained placeholder phrase.")
                self._model = None

        # Model priority B: a pretrained openWakeWord phrase as a PLACEHOLDER.
        try:
            # Ensure the pretrained model is present (one-time download to the
            # openwakeword cache). Safe to call repeatedly.
            try:
                _oww.utils.download_models([self.model_name])
            except Exception as e:                        # pragma: no cover
                print(f"[WakeWord] model pre-download note: {e}")
            self._model = _OWWModel(
                wakeword_models=[self.model_name],
                inference_framework=self._inference_framework,
            )
            print(f"[WakeWord] Ready: model='{self.model_name}' "
                  f"threshold={self.threshold} (PLACEHOLDER pretrained phrase; "
                  f"run scripts/train_wakeword.py to build 'Hey Access').")
        except Exception as e:                            # pragma: no cover
            self._model = None
            print(f"[WakeWord] Model load failed, always-on disabled: {e}")

    # ------------------------------------------------------------------ status
    @staticmethod
    def _find_custom(model_dir: str):
        """First *.onnx in model_dir, or None. Mirrors ReidModule._find_model."""
        if not model_dir or not os.path.isdir(model_dir):
            return None
        hits = sorted(glob.glob(os.path.join(model_dir, "*.onnx")))
        return hits[0] if hits else None

    def available(self) -> bool:
        """True only if the detector AND a mic are usable."""
        return bool(self._has_oww and self._has_mic and self._model is not None)

    def is_placeholder(self) -> bool:
        """False once a custom-trained model (e.g. hey_access) is active."""
        return not self._custom

    def running(self) -> bool:
        return self._running

    def model_name_str(self) -> str:
        return self.model_name if self.available() else "none"

    def status(self) -> dict:
        return {
            "available": self.available(),
            "running": self._running,
            "model": self.model_name,
            "threshold": self.threshold,
            "has_detector": self._has_oww,
            "has_mic": self._has_mic,
            "placeholder": self.is_placeholder(),
        }

    def set_on_wake(self, on_wake) -> None:
        self._on_wake = on_wake

    # ------------------------------------------------------------------ control
    def start(self) -> bool:
        """Start the always-listening daemon thread. Idempotent; never raises."""
        if not self.available():
            print("[WakeWord] start() ignored - detector or mic unavailable.")
            return False
        if self._running:
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="wakeword-listener")
        # Set the flag BEFORE starting the thread: _loop clears it if the mic
        # fails to open, and setting it after would race that clear - /status
        # would then report a listener that is actually dead.
        self._running = True
        self._thread.start()
        print(f"[WakeWord] Listening for '{self.model_name}'...")
        return True

    def stop(self) -> None:
        """Signal the listener thread to exit. Idempotent."""
        self._stop.set()
        self._running = False

    # ------------------------------------------------------------------ worker
    def _loop(self) -> None:
        """Own the mic, score every chunk, fire on_wake past the threshold."""
        try:
            stream = _sd.InputStream(samplerate=_SAMPLE_RATE, channels=1,
                                     dtype="int16", blocksize=_CHUNK)
        except Exception as e:                            # pragma: no cover
            print(f"[WakeWord] Could not open mic stream, stopping: {e}")
            self._running = False
            return

        with stream:
            while not self._stop.is_set():
                try:
                    data, _overflow = stream.read(_CHUNK)
                    frame = np.asarray(data, dtype=np.int16).reshape(-1)
                    scores = self._model.predict(frame)
                    score = self._score_for_model(scores)
                    if score >= self.threshold and self._cooled_down():
                        self._fire(score)
                except Exception as e:                    # pragma: no cover
                    print(f"[WakeWord] listen loop error (continuing): {e}")
                    _time.sleep(0.1)

    def _score_for_model(self, scores: dict) -> float:
        """Pull our model's score out of openWakeWord's result dict."""
        if not scores:
            return 0.0
        if self.model_name in scores:
            return float(scores[self.model_name])
        # Fall back to the best score across whatever keys were returned.
        try:
            return float(max(scores.values()))
        except Exception:                                 # pragma: no cover
            return 0.0

    def _cooled_down(self) -> bool:
        return (_time.monotonic() - self._last_fire) >= self.cooldown

    def _fire(self, score: float) -> None:
        self._last_fire = _time.monotonic()
        print(f"[WakeWord] Wake detected (score={score:.2f}) -> capturing command.")
        if self._on_wake is None:
            return
        try:
            self._on_wake()
        except Exception as e:                            # pragma: no cover
            print(f"[WakeWord] on_wake handler error: {e}")
