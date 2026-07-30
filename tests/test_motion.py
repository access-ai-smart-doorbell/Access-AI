"""Tests for the software motion trigger (Phase 17).

process_frame() is driven with SYNTHETIC frames straight through the real
detection logic - no camera, no thread, no sleep.
"""

import numpy as np

from accessai.motion_module import MotionModule


class _Latest:
    def __init__(self, frame=None):
        self.frame = frame

    def get(self):
        return self.frame


def _frame(color=0):
    return np.full((240, 320, 3), color, dtype=np.uint8)


def _frame_with_box(color=0, box=255, size=100):
    f = _frame(color)
    f[20:20 + size, 20:20 + size] = box
    return f


def _module(**kw):
    kw.setdefault("min_area", 0.02)
    kw.setdefault("consecutive", 3)
    return MotionModule(_Latest(), **kw)


def test_first_frame_never_fires():
    m = _module()
    assert m.process_frame(_frame()) is False        # primes the background


def test_static_scene_never_fires():
    m = _module()
    for _ in range(10):
        assert m.process_frame(_frame(90)) is False


def test_motion_fires_after_consecutive_hits():
    m = _module(consecutive=3)
    m.process_frame(_frame())                        # prime
    # Alternate two very different frames: every subsequent diff is large.
    frames = [_frame_with_box(), _frame(), _frame_with_box(), _frame()]
    results = [m.process_frame(f) for f in frames]
    assert results[:2] == [False, False]             # hits 1, 2
    assert results[2] is True                        # hit 3 -> confirmed


def test_single_noisy_frame_does_not_fire():
    m = _module(consecutive=3)
    m.process_frame(_frame())
    assert m.process_frame(_frame_with_box()) is False   # one hit only
    # Back to static: the streak must RESET, not accumulate.
    still = _frame_with_box()
    m.process_frame(still)
    for _ in range(5):
        assert m.process_frame(still) is False
    assert m.process_frame(still) is False


def test_small_change_below_area_threshold_ignored():
    m = _module(min_area=0.05, consecutive=1)
    m.process_frame(_frame())
    tiny = _frame()
    tiny[0:10, 0:10] = 255                            # ~0.13% of the frame
    assert m.process_frame(tiny) is False


def test_none_frame_is_safe():
    m = _module()
    assert m.process_frame(None) is False


def test_resolution_change_reprimes_without_firing():
    m = _module(consecutive=1)
    m.process_frame(_frame())
    big = np.zeros((480, 640, 3), dtype=np.uint8)
    assert m.process_frame(big) is False              # new shape -> re-prime


def test_suppress_and_status():
    m = _module()
    m.suppress()
    st = m.status()
    assert st["available"] in (True, False)
    assert st["fires"] == 0 and st["running"] is False
