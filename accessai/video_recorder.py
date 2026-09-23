"""
EventVideoRecorder - circular-buffer video clip recorder (Phase 18).

Records short MP4 clips around every person-detection event:
  pre-roll  (10 s before detection)
  event     (person visible)
  post-roll (8 s after person disappears)

Architecture
------------
A background thread continuously pushes camera frames into a deque whose
length is pre_roll_sec × fps (the circular buffer).  When the pipeline
detects a person, EventVideoRecorder.on_person_detected() is called; it
freezes a copy of the pre-roll buffer and starts writing frames into an
OpenCV VideoWriter. When the person disappears (on_person_gone() or
post-roll timeout), it finalises the clip, saves JSON metadata, and
prunes old clips according to the retention policy.

Thread model
------------
  * push_frame(frame) - called from the camera thread, NEVER blocks.
  * on_person_detected(event) / on_person_gone() - called from the
    pipeline thread; they just flip flags and are also non-blocking.
  * A dedicated _writer_thread drains a queue and does all I/O so
    neither the camera loop nor the pipeline stall.

Fail-soft
---------
If OpenCV VideoWriter is unavailable (headless install without codecs)
the module logs a warning and is silently disabled.  The rest of the
pipeline is unaffected.
"""

import os
import json
import queue
import threading
import time
import datetime
import collections
import pathlib
import logging

logger = logging.getLogger("EventVideoRecorder")

try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False
    logger.warning("[VideoRecorder] OpenCV not available — video clips disabled.")


class EventVideoRecorder:
    """Circular-buffer event video recorder.

    Parameters
    ----------
    clips_dir : str
        Directory to save video clips and metadata JSON files.
    fps : int
        Target frame-rate for the saved clip (should match camera fps).
    pre_roll_sec : int
        Seconds of footage BEFORE detection to include in every clip.
    post_roll_sec : int
        Seconds of footage AFTER the person disappears to include.
    min_event_sec : float
        Minimum person-visible duration to trigger a save.  Very brief
        transient detections (e.g. a shadow) below this are discarded.
    retain_days : int
        Clips older than this many days are deleted automatically on startup
        and on each new clip save.
    max_clips : int
        Hard upper limit on total saved clips regardless of age.
    """

    def __init__(
        self,
        clips_dir: str = "data/clips",
        fps: int = 15,
        pre_roll_sec: int = 10,
        post_roll_sec: int = 8,
        min_event_sec: float = 1.0,
        retain_days: int = 7,
        max_clips: int = 500,
    ):
        self._clips_dir = pathlib.Path(clips_dir)
        self._clips_dir.mkdir(parents=True, exist_ok=True)
        self._fps = int(fps)
        self._pre_frames = int(pre_roll_sec * fps)
        self._post_roll_sec = float(post_roll_sec)
        self._min_event_sec = float(min_event_sec)
        self._retain_days = int(retain_days)
        self._max_clips = int(max_clips)

        # Circular frame buffer: maxlen keeps the last `pre_frames` frames.
        self._ring: collections.deque = collections.deque(maxlen=self._pre_frames)

        # Writer thread communication
        self._write_q: queue.Queue = queue.Queue(maxsize=2048)
        self._enabled = _HAS_CV2

        # Recording state (guarded by _state_lock)
        self._state_lock = threading.Lock()
        self._recording = False
        self._event_meta: dict = {}
        self._person_present = False
        self._person_gone_at: float = 0.0
        self._event_start: float = 0.0

        # Frame size, filled on first push_frame call.
        self._frame_h: int = 0
        self._frame_w: int = 0

        if self._enabled:
            t = threading.Thread(target=self._writer_loop, daemon=True,
                                 name="VideoRecorder-writer")
            t.start()

            # Watchdog: finalises recording if post-roll expires without a
            # new detection (person walked away, no explicit on_person_gone).
            w = threading.Thread(target=self._watchdog_loop, daemon=True,
                                 name="VideoRecorder-watchdog")
            w.start()

            self._prune_old_clips()
            logger.info("[VideoRecorder] Ready — clips → %s | pre=%ds post=%ds",
                        self._clips_dir, pre_roll_sec, post_roll_sec)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def push_frame(self, frame) -> None:
        """Called from the camera thread for EVERY frame.

        Always feeds the circular pre-roll buffer. When recording is active,
        also enqueues the frame for the writer thread. Never blocks.
        """
        if not self._enabled or frame is None:
            return
        if self._frame_h == 0 and frame is not None:
            self._frame_h, self._frame_w = frame.shape[:2]

        self._ring.append(frame)
        with self._state_lock:
            if self._recording:
                try:
                    self._write_q.put_nowait(("frame", frame))
                except queue.Full:
                    pass  # drop frame rather than block camera thread

    def on_person_detected(self, event_meta: dict) -> None:
        """Call from the pipeline when a person is detected.

        `event_meta` should contain at minimum:
            event_id, timestamp, person (name or "Unknown"), recognized (bool)

        Safe to call repeatedly while the person is still visible — it only
        starts a new recording if one isn't already active.
        """
        if not self._enabled:
            return
        with self._state_lock:
            self._person_present = True
            self._person_gone_at = 0.0
            if self._recording:
                return  # already recording this event
            # Start a new recording: flush pre-roll into the writer queue.
            self._recording = True
            self._event_start = time.monotonic()
            self._event_meta = dict(event_meta)
            preroll = list(self._ring)  # snapshot pre-roll frames

        # Send pre-roll frames to writer (outside lock to keep it short)
        for f in preroll:
            try:
                self._write_q.put_nowait(("frame", f))
            except queue.Full:
                break
        logger.debug("[VideoRecorder] Recording started for %s",
                     event_meta.get("person", "?"))

    def on_person_gone(self) -> None:
        """Call when the person is no longer detected in the current frame.

        Starts the post-roll countdown.  The recording is finalised by the
        watchdog after post_roll_sec seconds if the person doesn't return.
        """
        if not self._enabled:
            return
        with self._state_lock:
            if not self._recording:
                return
            self._person_present = False
            self._person_gone_at = time.monotonic()

    def available(self) -> bool:
        return self._enabled

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _finish_recording(self) -> None:
        """Signal the writer thread to close the current clip."""
        with self._state_lock:
            if not self._recording:
                return
            elapsed = time.monotonic() - self._event_start
            meta = dict(self._event_meta)
            self._recording = False
            self._person_present = False
            self._person_gone_at = 0.0
            self._event_start = 0.0
            self._event_meta = {}

        if elapsed < self._min_event_sec:
            # Too brief — discard rather than save a ~10-second pre-only clip
            # for a detection that lasted less than the threshold.
            try:
                self._write_q.put_nowait(("discard", None))
            except queue.Full as e:
                logger.debug("Discard enqueue failed: %s", e)
            logger.debug("[VideoRecorder] Event too brief (%.1fs), discarding.", elapsed)
            return

        meta["duration_sec"] = round(elapsed, 1)
        try:
            self._write_q.put_nowait(("finish", meta))
        except queue.Full as e:
            logger.debug("Finish enqueue failed: %s", e)

    def _writer_loop(self) -> None:
        """Background thread: owns the VideoWriter and all disk I/O."""
        writer = None
        out_path = None

        while True:
            try:
                cmd, payload = self._write_q.get(timeout=1.0)
            except queue.Empty:
                continue

            if cmd == "frame":
                frame = payload
                if writer is None:
                    # Lazily create the writer on the first frame so we have
                    # the actual frame dimensions.
                    h, w = frame.shape[:2]
                    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    out_path = self._clips_dir / f"event_{ts}.mp4"
                    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                    writer = cv2.VideoWriter(str(out_path), fourcc,
                                            self._fps, (w, h))
                    if not writer.isOpened():
                        logger.warning("[VideoRecorder] VideoWriter failed to open.")
                        writer = None
                        out_path = None
                if writer is not None:
                    writer.write(frame)

            elif cmd == "finish":
                meta = payload
                if writer is not None:
                    writer.release()
                    writer = None
                    # Save metadata JSON alongside the clip.
                    if out_path and out_path.exists():
                        meta["video"] = out_path.name
                        meta_path = out_path.with_suffix(".json")
                        try:
                            meta_path.write_text(
                                json.dumps(meta, indent=2, default=str))
                        except Exception as e:
                            logger.warning("[VideoRecorder] JSON write failed: %s", e)
                        logger.info("[VideoRecorder] Saved: %s (%.1fs, %s)",
                                    out_path.name,
                                    meta.get("duration_sec", 0),
                                    meta.get("person", "?"))
                    self._prune_old_clips()
                    out_path = None

            elif cmd == "discard":
                if writer is not None:
                    writer.release()
                    writer = None
                    if out_path and out_path.exists():
                        try:
                            out_path.unlink()
                        except Exception as e:
                            logger.debug("Discard unlink failed: %s", e)
                    out_path = None

    def _watchdog_loop(self) -> None:
        """Periodically checks if post-roll has expired and finalises clip."""
        while True:
            time.sleep(0.5)
            with self._state_lock:
                if not self._recording:
                    continue
                gone_at = self._person_gone_at
                present = self._person_present

            if not present and gone_at > 0:
                elapsed_since_gone = time.monotonic() - gone_at
                if elapsed_since_gone >= self._post_roll_sec:
                    self._finish_recording()

    def _prune_old_clips(self) -> None:
        """Delete clips older than retain_days and enforce max_clips limit."""
        try:
            clips = sorted(self._clips_dir.glob("event_*.mp4"),
                           key=lambda p: p.stat().st_mtime)
            cutoff = time.time() - self._retain_days * 86400
            for clip in clips:
                try:
                    if clip.stat().st_mtime < cutoff:
                        clip.unlink(missing_ok=True)
                        clip.with_suffix(".json").unlink(missing_ok=True)
                except Exception as e:
                    logger.debug("Prune unlink failed: %s", e)
            # Re-scan after age pruning
            clips = sorted(self._clips_dir.glob("event_*.mp4"),
                           key=lambda p: p.stat().st_mtime)
            while len(clips) > self._max_clips:
                oldest = clips.pop(0)
                oldest.unlink(missing_ok=True)
                oldest.with_suffix(".json").unlink(missing_ok=True)
        except Exception as e:
            logger.warning("[VideoRecorder] Prune error: %s", e)
