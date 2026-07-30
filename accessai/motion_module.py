"""
MotionModule (Phase 17) - a SOFTWARE motion trigger, no PIR hardware needed.

VisitorEvent reserved trigger='motion' since Phase 1; this module finally
generates it. A daemon thread samples the shared LatestFrame a few times a
second and scores inter-frame change with classic OpenCV background
subtraction (grayscale -> blur -> absdiff -> threshold -> changed-area
fraction). When the changed fraction crosses MOTION_MIN_AREA for
MOTION_CONSECUTIVE consecutive samples (one noisy frame - a car headlight,
a compression artifact - must not ring the bell), it fires the SAME pipeline
the doorbell uses, tagged trigger="motion", then sleeps for MOTION_COOLDOWN.

Why absdiff and not the YOLO person detector: this check runs continuously,
so it must be nearly free (sub-millisecond per sample on a laptop). The heavy
perception only runs on the frames that motion (or the bell) selects - the
same economy a hardware PIR provides, in software.

House module contract: guarded construction, available(), start()/stop()
idempotent, a daemon thread, never raises out of the loop.
"""

import threading
import time as _time

import numpy as np

try:
    import cv2
    _HAS_CV2 = True
except Exception as e:                                    # pragma: no cover
    _HAS_CV2 = False
    print(f"[Motion] OpenCV unavailable, motion trigger disabled: {e}")


class MotionModule:
    def __init__(self, latest, on_motion=None, min_area: float = 0.02,
                 consecutive: int = 3, cooldown: float = 30.0,
                 interval: float = 0.3, warmup: float = 5.0):
        """
        latest      - the shared LatestFrame holder (accessai.server.LatestFrame)
        on_motion   - callback fired (from this thread) when motion is confirmed
        min_area    - fraction of pixels [0..1] that must change to count
        consecutive - samples in a row above min_area before firing
        cooldown    - seconds to ignore motion after a fire (or a doorbell ring)
        interval    - seconds between samples (~3/s default)
        warmup      - seconds after start() before the first fire is allowed
                      (lets exposure settle so boot doesn't self-trigger)
        """
        self._latest = latest
        self._on_motion = on_motion
        self.min_area = float(min_area)
        self.consecutive = max(1, int(consecutive))
        self.cooldown = float(cooldown)
        self.interval = max(0.05, float(interval))
        self.warmup = float(warmup)

        self._prev_gray = None
        self._hits = 0
        self._last_fire = 0.0
        self._started_at = 0.0
        self._last_score = 0.0          # debug: last changed-area fraction
        self._fires = 0
        self._thread = None
        self._stop = threading.Event()
        self._running = False

    # ------------------------------------------------------------------ status
    def available(self) -> bool:
        return _HAS_CV2 and self._latest is not None

    def running(self) -> bool:
        return self._running

    def status(self) -> dict:
        return {
            "available": self.available(),
            "running": self._running,
            "min_area": self.min_area,
            "consecutive": self.consecutive,
            "cooldown": self.cooldown,
            "last_score": round(self._last_score, 4),
            "fires": self._fires,
        }

    def set_on_motion(self, cb) -> None:
        self._on_motion = cb

    def suppress(self) -> None:
        """Push the cooldown out from NOW - called after a doorbell/manual
        trigger so motion doesn't immediately re-announce the same visitor."""
        self._last_fire = _time.monotonic()

    # ------------------------------------------------------------------ control
    def start(self) -> bool:
        if not self.available():
            print("[Motion] start() ignored - OpenCV or frame source missing.")
            return False
        if self._running:
            return True
        self._stop.clear()
        self._started_at = _time.monotonic()
        self._running = True     # set BEFORE the thread (mirrors WakeWordModule)
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="motion-detector")
        self._thread.start()
        print(f"[Motion] Watching for motion (>= {self.min_area:.0%} of frame, "
              f"{self.consecutive} consecutive samples, "
              f"{self.cooldown:.0f}s cooldown).")
        return True

    def stop(self) -> None:
        self._stop.set()
        self._running = False

    # ------------------------------------------------------------------ core
    def process_frame(self, frame_bgr) -> bool:
        """Score one frame; True when motion is CONFIRMED (hits threshold met).

        Pure w.r.t. the callback (doesn't fire it) so tests can drive synthetic
        frames straight through the real detection logic."""
        if frame_bgr is None:
            return False
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)
        # Sample at a modest fixed width: motion doesn't need full resolution
        # and this keeps the absdiff cost flat regardless of camera size.
        h, w = gray.shape
        if w > 480:
            gray = cv2.resize(gray, (480, int(h * 480 / w)))
        if self._prev_gray is None or self._prev_gray.shape != gray.shape:
            self._prev_gray = gray
            return False
        diff = cv2.absdiff(self._prev_gray, gray)
        self._prev_gray = gray
        changed = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)[1]
        self._last_score = float(np.count_nonzero(changed)) / changed.size
        if self._last_score >= self.min_area:
            self._hits += 1
        else:
            self._hits = 0
        return self._hits >= self.consecutive

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                frame = self._latest.get()
                confirmed = self.process_frame(frame)
                now = _time.monotonic()
                in_warmup = (now - self._started_at) < self.warmup
                in_cooldown = (now - self._last_fire) < self.cooldown
                if confirmed and not in_warmup and not in_cooldown:
                    self._last_fire = now
                    self._hits = 0
                    self._fires += 1
                    print(f"[Motion] Motion confirmed "
                          f"(area={self._last_score:.1%}) -> pipeline.")
                    if self._on_motion is not None:
                        try:
                            self._on_motion()
                        except Exception as e:            # pragma: no cover
                            print(f"[Motion] on_motion handler error: {e}")
            except Exception as e:                        # pragma: no cover
                print(f"[Motion] loop error (continuing): {e}")
            _time.sleep(self.interval)
