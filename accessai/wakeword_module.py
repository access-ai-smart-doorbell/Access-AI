"""
WakeWordModule - always-on wake-word detection (Phase 10, OPT-IN).

Siri / Google Assistant-grade architecture:
  * Single continuous microphone stream (zero PortAudio/ALSA device contention).
  * Dynamic Acoustic Front-End: Adaptive Gain Control (AGC) with smooth envelope
    following and soft limiting so distant room speech reaches nominal RMS.
  * Rolling circular audio buffer (history deque) to capture command pre-roll
    without clipping the user's first words ("Hey Access, [command]").
  * Instant Siri-style dual-tone earcon chime ("ba-ding!") acknowledging detection
    in the background without blocking audio capture.
  * Dynamic Voice Activity Detection (VAD) with early trailing silence cutoff (~0.7s)
    so commands process quickly without waiting out a fixed timer.
  * Decoupled execution: command audio (16 kHz mono float32) passed directly to
    the on_wake callback / Whisper, avoiding secondary microphone opens.
  * Automatic stream recovery on hardware glitches or disconnects.

Detector: openWakeWord - pure-Python, CPU-only detector running ONNX models via
onnxruntime. Model priority:
  A CUSTOM     - first *.onnx in WAKEWORD_MODEL_DIR (models/wakeword/), e.g.
                 "Hey Access" (hey_access.onnx).
  B PRETRAINED - openWakeWord's shipped phrases (hey_jarvis, alexa, ...).
"""

import collections
import glob
import os
import subprocess
import threading
import time as _time
import wave

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


def _ensure_siri_chime(output_path: str = "") -> str:
    """Generate or locate the Siri dual-tone earcon chime WAV file.

    Synthesizes an elegant, harmonic two-tone chime (587.33 Hz D5 -> 880.00 Hz A5)
    with smooth attack and exponential decay. Returns the absolute file path.
    """
    if not output_path:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_path = os.path.join(base_dir, "static", "sounds", "wake_chime.wav")

    try:
        if os.path.isfile(output_path) and os.path.getsize(output_path) > 1000:
            return output_path

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        sr = 24000
        # Tone 1: 587.33 Hz (D5) for 80 ms
        t1 = np.linspace(0, 0.08, int(sr * 0.08), endpoint=False)
        env1 = np.sin(np.pi * np.linspace(0, 1, len(t1)) ** 0.5)
        wave1 = np.sin(2 * np.pi * 587.33 * t1) * env1

        # Tone 2: 880.00 Hz (A5) for 150 ms with harmonic overtone
        t2 = np.linspace(0, 0.15, int(sr * 0.15), endpoint=False)
        attack_len = int(sr * 0.012)
        attack = np.linspace(0, 1, attack_len)
        decay = np.exp(-t2[attack_len:] * 18.0)
        env2 = np.concatenate([attack, decay])[:len(t2)]
        wave2 = (0.75 * np.sin(2 * np.pi * 880.0 * t2) +
                 0.25 * np.sin(2 * np.pi * 1760.0 * t2)) * env2

        audio = np.concatenate([wave1 * 0.55, wave2 * 0.75])
        audio_int16 = (audio * 32767 * 0.75).astype(np.int16)

        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(audio_int16.tobytes())
        return output_path
    except Exception as e:
        print(f"[WakeWord] Could not generate chime: {e}")
        return ""


def _play_chime_async(chime_path: str) -> None:
    """Play earcon chime in background without blocking audio capture."""
    if not chime_path or not os.path.isfile(chime_path):
        return

    def _play_worker():
        for player in ["paplay", "pw-play", "aplay"]:
            try:
                proc = subprocess.Popen(
                    [player, chime_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                proc.wait(timeout=1.0)
                return
            except (FileNotFoundError, subprocess.SubprocessError):
                continue
            except Exception:
                return

    threading.Thread(target=_play_worker, daemon=True, name="siri-chime").start()


class _AdaptiveGainController:
    """Soft AGC: boosts low-level room speech towards nominal RMS (~3200).

    Tracks background noise floor with slow exponential decay, and smooths gain
    transitions so voice volume is boosted without clicking or blowing up noise.
    """
    def __init__(self, target_rms: float = 3200.0, max_gain: float = 6.5, min_gain: float = 1.0):
        self.target_rms = float(target_rms)
        self.max_gain = float(max_gain)
        self.min_gain = float(min_gain)
        self.current_gain = 1.8
        self.noise_floor = 120.0

    def process(self, chunk: np.ndarray) -> np.ndarray:
        float_chunk = chunk.astype(np.float32)
        rms = float(np.sqrt(np.mean(float_chunk ** 2) + 1e-6))

        # Track noise floor during quiet periods
        if rms < self.noise_floor * 1.5:
            self.noise_floor = 0.98 * self.noise_floor + 0.02 * rms
        else:
            self.noise_floor = 0.999 * self.noise_floor + 0.001 * rms
        self.noise_floor = max(40.0, min(self.noise_floor, 800.0))

        # Only apply significant gain if signal is above noise floor
        if rms > self.noise_floor * 1.3:
            desired_gain = self.target_rms / max(rms, 200.0)
            desired_gain = min(self.max_gain, max(self.min_gain, desired_gain))
            alpha = 0.25 if desired_gain > self.current_gain else 0.08
            self.current_gain = (1 - alpha) * self.current_gain + alpha * desired_gain
        else:
            self.current_gain = 0.95 * self.current_gain + 0.05 * 1.5

        boosted = float_chunk * self.current_gain
        np.clip(boosted, -32767.0, 32767.0, out=boosted)
        return boosted.astype(np.int16)


class WakeWordModule:
    def __init__(self, model: str = "hey_jarvis", threshold: float = 0.40,
                 on_wake=None, cooldown: float = 3.5,
                 inference_framework: str = "onnx",
                 model_dir: str = "",
                 command_seconds: float = 4.5):
        self.model_name = model
        self.threshold = float(threshold)
        self.cooldown = float(cooldown)
        self.command_seconds = float(command_seconds)
        self._on_wake = on_wake
        self._inference_framework = inference_framework
        self._custom = False

        self._model = None
        self._thread = None
        self._stop = threading.Event()
        self._running = False
        self._last_fire = 0.0
        self._has_oww = _HAS_OWW
        self._has_mic = _HAS_MIC

        self._chime_path = _ensure_siri_chime()
        self._agc = _AdaptiveGainController()

        if not _HAS_OWW:
            return

        # Model priority A: CUSTOM trained model in model_dir
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
                      "falling back to pretrained placeholder phrase.")
                self._model = None

        # Model priority B: pretrained openWakeWord phrase as PLACEHOLDER
        try:
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
                  f"custom model 'hey_access.onnx' not found).")
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
        """Start always-listening daemon thread. Idempotent; never raises."""
        if not self.available():
            print("[WakeWord] start() ignored - detector or mic unavailable.")
            return False
        if self._running:
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="wakeword-listener")
        self._running = True
        self._thread.start()
        print(f"[WakeWord] Listening continuously for '{self.model_name}'...")
        return True

    def stop(self) -> None:
        """Signal the listener thread to exit. Idempotent."""
        self._stop.set()
        self._running = False

    # ------------------------------------------------------------------ worker
    def _loop(self) -> None:
        """Continuous streaming microphone loop with state machine.

        Never drops or re-opens the capture stream on wake detection. Seamlessly
        transitions from STATE_LISTENING to STATE_RECORDING_COMMAND with pre-roll
        audio buffer and dynamic VAD cutoff.
        """
        while not self._stop.is_set():
            try:
                stream = _sd.InputStream(samplerate=_SAMPLE_RATE, channels=1,
                                         dtype="int16", blocksize=_CHUNK)
            except Exception as e:                        # pragma: no cover
                print(f"[WakeWord] Could not open mic stream, retrying in 2s: {e}")
                self._running = False
                _time.sleep(2.0)
                if not self._stop.is_set():
                    self._running = True
                    continue
                return

            try:
                with stream:
                    # History ring buffer keeps last ~2.0s (25 chunks @ 80ms)
                    history = collections.deque(maxlen=25)
                    state = "listening"
                    command_chunks = []
                    has_spoken = False
                    consecutive_silence = 0
                    initial_silence_chunks = 0
                    max_command_chunks = max(15, int(self.command_seconds / 0.08))
                    max_initial_silence = max(10, int(2.5 / 0.08))

                    while not self._stop.is_set():
                        data, overflow = stream.read(_CHUNK)
                        if overflow:
                            pass
                        frame = np.asarray(data, dtype=np.int16).reshape(-1)
                        if frame.size != _CHUNK:
                            continue

                        if state == "listening":
                            history.append(frame)
                            boosted = self._agc.process(frame)
                            scores = self._model.predict(boosted)
                            score = self._score_for_model(scores)

                            if score >= self.threshold and self._cooled_down():
                                self._last_fire = _time.monotonic()
                                print(f"[WakeWord] Wake detected ('{self.model_name}', "
                                      f"score={score:.2f}) -> capturing command.")
                                _play_chime_async(self._chime_path)

                                # Transition to recording command immediately
                                state = "recording"
                                # Pre-roll: keep last ~0.64s (8 chunks) so initial words aren't cut
                                command_chunks = list(history)[-8:]
                                has_spoken = False
                                consecutive_silence = 0
                                initial_silence_chunks = 0

                        elif state == "recording":
                            command_chunks.append(frame)
                            # Energy-based VAD for command capture
                            f_chunk = frame.astype(np.float32)
                            c_rms = float(np.sqrt(np.mean(f_chunk ** 2) + 1e-6))
                            speech_thresh = max(350.0, self._agc.noise_floor * 2.2)

                            if c_rms >= speech_thresh:
                                has_spoken = True
                                consecutive_silence = 0
                            else:
                                if has_spoken:
                                    consecutive_silence += 1
                                else:
                                    initial_silence_chunks += 1

                            # End-of-speech conditions:
                            # 1. Spoke and then stopped: 0.72s of silence (~9 chunks)
                            # 2. Never spoke and 2.5s elapsed (~31 chunks)
                            # 3. Maximum command time elapsed
                            speech_finished = (has_spoken and consecutive_silence >= 9)
                            speech_timeout = (not has_spoken and initial_silence_chunks >= max_initial_silence)
                            max_reached = (len(command_chunks) >= max_command_chunks)

                            if speech_finished or speech_timeout or max_reached:
                                if has_spoken or len(command_chunks) >= 12:
                                    # Assemble float32 mono audio array for Whisper
                                    raw_audio = np.concatenate(command_chunks)
                                    audio_f32 = (raw_audio.astype(np.float32) / 32768.0).reshape(-1)
                                    # Dispatch execution in background thread
                                    t = threading.Thread(target=self._fire, args=(audio_f32,),
                                                         daemon=True, name="wakeword-dispatch")
                                    t.start()
                                else:
                                    print("[WakeWord] No speech heard following wake phrase.")

                                # Reset internal detector embeddings and return to listening
                                try:
                                    self._model.reset()
                                except Exception:
                                    pass
                                history.clear()
                                command_chunks = []
                                state = "listening"

            except Exception as e:                        # pragma: no cover
                if not self._stop.is_set():
                    print(f"[WakeWord] Stream interrupted ({e}), re-establishing in 0.5s...")
                    _time.sleep(0.5)

        self._running = False

    def _score_for_model(self, scores: dict) -> float:
        """Pull our model's score out of openWakeWord's result dict."""
        if not scores:
            return 0.0
        if self.model_name in scores:
            return float(scores[self.model_name])
        try:
            return float(max(scores.values()))
        except Exception:                                 # pragma: no cover
            return 0.0

    def _cooled_down(self) -> bool:
        return (_time.monotonic() - self._last_fire) >= self.cooldown

    def _fire(self, audio=None) -> None:
        """Invoke on_wake callback with captured audio."""
        if self._on_wake is None:
            return
        try:
            try:
                self._on_wake(audio=audio)
            except TypeError:
                try:
                    self._on_wake(audio)
                except TypeError:
                    self._on_wake()
        except Exception as e:                            # pragma: no cover
            print(f"[WakeWord] on_wake handler error: {e}")

