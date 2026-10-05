"""Tests for the wake-word model resolution (Phase 10 placeholder removal).

WakeWordModule._find_custom is PURE path logic - these tests never load
openWakeWord models, open a mic, or touch the network.
"""

import os

from accessai.wakeword_module import WakeWordModule


def test_find_custom_missing_dir_returns_none(tmp_path):
    assert WakeWordModule._find_custom(str(tmp_path / "nope")) is None
    assert WakeWordModule._find_custom("") is None


def test_find_custom_empty_dir_returns_none(tmp_path):
    assert WakeWordModule._find_custom(str(tmp_path)) is None


def test_find_custom_picks_first_onnx_sorted(tmp_path):
    (tmp_path / "zeta.onnx").write_bytes(b"x")
    (tmp_path / "hey_access.onnx").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")
    hit = WakeWordModule._find_custom(str(tmp_path))
    assert os.path.basename(hit) == "hey_access.onnx"


def test_find_custom_ignores_non_onnx(tmp_path):
    (tmp_path / "model.tflite").write_bytes(b"x")
    assert WakeWordModule._find_custom(str(tmp_path)) is None


def test_ensure_siri_chime_creates_wav(tmp_path):
    import wave
    from accessai.wakeword_module import _ensure_siri_chime
    target = str(tmp_path / "test_chime.wav")
    path = _ensure_siri_chime(target)
    assert path == target
    assert os.path.isfile(path)
    with wave.open(path, "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 24000
        frames = wf.readframes(wf.getnframes())
        assert len(frames) > 1000


def test_adaptive_gain_controller_boosts_quiet_speech():
    import numpy as np
    from accessai.wakeword_module import _AdaptiveGainController
    agc = _AdaptiveGainController(target_rms=3000.0, max_gain=6.0)
    # 200 RMS sine wave (quiet speech)
    t = np.linspace(0, 0.08, 1280, endpoint=False)
    quiet_chunk = (200.0 * np.sqrt(2) * np.sin(2 * np.pi * 300 * t)).astype(np.int16)
    
    # Process several chunks to allow gain to adapt smoothly
    boosted = None
    for _ in range(10):
        boosted = agc.process(quiet_chunk)
    
    orig_rms = np.sqrt(np.mean(quiet_chunk.astype(np.float32) ** 2))
    boosted_rms = np.sqrt(np.mean(boosted.astype(np.float32) ** 2))
    assert boosted_rms > orig_rms * 2.0
    assert np.max(np.abs(boosted)) <= 32767


def test_adaptive_gain_controller_preserves_bounds_no_overflow():
    import numpy as np
    from accessai.wakeword_module import _AdaptiveGainController
    agc = _AdaptiveGainController(target_rms=3000.0, max_gain=8.0)
    # Extremely loud signal
    loud_chunk = np.full(1280, 30000, dtype=np.int16)
    boosted = agc.process(loud_chunk)
    assert np.all(boosted <= 32767)
    assert np.all(boosted >= -32767)


def test_fire_handles_audio_arg_and_no_arg_callbacks():
    received = []

    def callback_with_audio(audio=None):
        received.append(("with_audio", audio))

    def callback_no_args():
        received.append(("no_args", None))

    # Fake audio
    import numpy as np
    test_audio = np.zeros(16000, dtype=np.float32)

    mod = WakeWordModule.__new__(WakeWordModule)
    mod._on_wake = callback_with_audio
    mod._fire(test_audio)
    assert len(received) == 1
    assert received[0][0] == "with_audio"
    assert received[0][1] is test_audio

    mod._on_wake = callback_no_args
    mod._fire(test_audio)
    assert len(received) == 2
    assert received[1][0] == "no_args"

