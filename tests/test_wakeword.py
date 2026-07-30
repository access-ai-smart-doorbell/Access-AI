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
