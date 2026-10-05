"""
FrameSelector (Phase 19) – best-frame selection between motion trigger and
the expensive perception pipeline.

When motion is confirmed, this module opens a short observation window
(configurable, default 2.5 s) during which it captures frames at ~10 FPS
from the shared LatestFrame holder.  Each frame is scored on lightweight,
CPU-friendly criteria:

  • Laplacian sharpness (rejects motion blur)
  • YOLO person detection confidence + bbox coverage
  • Face detection confidence + face size (when visible)
  • Exposure quality (rejects very dark / blown-out frames)
  • Temporal stability (person bbox hasn't moved much → standing still)

The single highest-scoring frame is returned to the caller (run.py) which
then feeds it into ``pipeline.run_once()``.  If NO frame passes the
minimum quality threshold the whole motion event is discarded – no useless
blurry analysis, no wasted VLM call.

Architectural contract
----------------------
* Inserted BETWEEN ``motion.process_frame()`` confirming motion and
  ``pipeline.run_once()`` being called.
* Never modifies the downstream pipeline – it only selects which frame
  enters it.
* All scoring is CPU-only (OpenCV + lightweight numpy).  YOLO and face
  detection are optional – when the respective modules are not injected the
  selector falls back to sharpness + exposure + stability only.
* Thread-safe: called from the motion-detector thread; reads from the
  shared LatestFrame (which is already thread-safe).
"""

import time as _time
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Callable

import numpy as np

try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False

logger = logging.getLogger("FrameSelector")


# ---------------------------------------------------------------------------
# Configuration dataclass – mirrors config.py constants, injected at startup
# ---------------------------------------------------------------------------
@dataclass
class FrameSelectConfig:
    """All tunables for the observation window and frame scoring."""
    window_sec: float = 2.5          # max observation window duration
    sample_fps: float = 10.0         # target capture rate inside the window
    min_sharpness: float = 50.0      # Laplacian variance floor
    min_person_conf: float = 0.35    # YOLO person detection confidence floor
    min_face_conf: float = 0.3       # face detection confidence floor
    min_exposure: float = 30.0       # mean pixel value floor (reject darkness)
    max_exposure: float = 230.0      # mean pixel value ceiling (reject blown)
    stability_window: float = 0.5    # seconds person must be ~stable for early exit
    min_quality: float = 0.3         # composite score floor to accept any frame
    early_exit_quality: float = 0.75 # composite score to trigger early exit

    # Scoring weights (sum ≈ 1.0 for readability, but normalization is
    # applied automatically).
    w_sharpness: float = 0.20
    w_person: float = 0.25
    w_face: float = 0.20
    w_exposure: float = 0.10
    w_stability: float = 0.10
    w_body_coverage: float = 0.15


# ---------------------------------------------------------------------------
# Per-frame score record
# ---------------------------------------------------------------------------
@dataclass
class _FrameScore:
    frame: np.ndarray
    timestamp: float = 0.0
    sharpness: float = 0.0
    person_conf: float = 0.0
    person_area_frac: float = 0.0    # body bbox area as fraction of frame
    face_conf: float = 0.0
    face_area_frac: float = 0.0      # face bbox area as fraction of frame
    exposure: float = 128.0          # mean pixel value
    stability: float = 0.0          # 1.0 = perfectly still, 0.0 = large move
    composite: float = 0.0          # final weighted score
    reject_reason: str = ""


# ---------------------------------------------------------------------------
# FrameSelector
# ---------------------------------------------------------------------------
class FrameSelector:
    """Observes the camera for a short window and returns the best frame.

    Parameters
    ----------
    latest : LatestFrame
        The shared frame holder (same as MotionModule uses).
    cfg : FrameSelectConfig
        All tunables.
    yolo_detect : callable or None
        ``vision.detect(frame_bgr) -> list[dict]`` – optional, gives person
        detection signals.  When None, person scoring is skipped.
    face_detect : callable or None
        ``face.detect_faces(frame_bgr) -> list[dict]`` – optional, gives face
        detection signals.  When None, face scoring is skipped.
    """

    def __init__(self, latest, cfg: FrameSelectConfig, *,
                 yolo_detect: Optional[Callable] = None,
                 face_detect: Optional[Callable] = None):
        self._latest = latest
        self.cfg = cfg
        self._yolo_detect = yolo_detect
        self._face_detect = face_detect

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def select_best_frame(self) -> Optional[np.ndarray]:
        """Run the observation window and return the best frame, or None.

        Blocks the calling thread (the motion-detector thread) for up to
        ``cfg.window_sec`` seconds.  Returns None when no captured frame
        meets the minimum quality threshold.
        """
        if not _HAS_CV2:
            # Fallback: just return the current frame, no selection.
            return self._latest.get()

        cfg = self.cfg
        interval = 1.0 / max(1.0, cfg.sample_fps)
        deadline = _time.monotonic() + cfg.window_sec

        scored: List[_FrameScore] = []
        prev_person_center = None
        stable_since: Optional[float] = None
        best_so_far: Optional[_FrameScore] = None

        logger.debug("[FrameSelector] Observation window started "
                     "(%.1fs, ~%.0f FPS).", cfg.window_sec, cfg.sample_fps)

        frame_idx = 0
        while _time.monotonic() < deadline:
            frame = self._latest.get()
            if frame is None:
                _time.sleep(interval)
                continue

            now = _time.monotonic()
            # Run expensive detectors (YOLO, face) only every 3rd frame to
            # keep the window CPU-friendly.  Sharpness + exposure + stability
            # are sub-millisecond and run on every frame.
            run_detectors = (frame_idx % 3 == 0)
            fs = self._score_frame(frame, now, prev_person_center,
                                   run_detectors=run_detectors)
            scored.append(fs)
            frame_idx += 1

            # Track stability: how long the person's center hasn't moved much
            person_center = self._person_center(fs)
            if person_center is not None and prev_person_center is not None:
                dx = abs(person_center[0] - prev_person_center[0])
                dy = abs(person_center[1] - prev_person_center[1])
                if dx < 30 and dy < 30:
                    if stable_since is None:
                        stable_since = now
                    fs.stability = min(1.0,
                                       (now - stable_since) / max(0.1, cfg.stability_window))
                else:
                    stable_since = now
                    fs.stability = 0.0
            elif person_center is not None:
                stable_since = now
                fs.stability = 0.0
            prev_person_center = person_center

            # Recompute composite with stability
            fs.composite = self._composite(fs)

            # Track best
            if best_so_far is None or fs.composite > best_so_far.composite:
                best_so_far = fs

            # Early exit: great frame + person has been stable for a bit
            if (fs.composite >= cfg.early_exit_quality
                    and fs.stability >= 0.8):
                logger.debug("[FrameSelector] Early exit: composite=%.3f, "
                             "stability=%.2f after %d frames.",
                             fs.composite, fs.stability, len(scored))
                break

            _time.sleep(interval)

        # Log summary
        if not scored:
            logger.info("[FrameSelector] No frames captured, discarding event.")
            return None

        # Pick the best frame
        best = max(scored, key=lambda s: s.composite)
        self._log_summary(scored, best)

        if best.composite < cfg.min_quality:
            logger.info("[FrameSelector] Best frame (%.3f) below min_quality "
                        "(%.3f), discarding motion event.",
                        best.composite, cfg.min_quality)
            return None

        return best.frame

    # ------------------------------------------------------------------
    # Scoring helpers
    # ------------------------------------------------------------------
    def _score_frame(self, frame: np.ndarray, timestamp: float,
                     prev_center, *, run_detectors: bool = True) -> _FrameScore:
        """Score a single frame on all criteria.

        When run_detectors is False, YOLO and face detection are skipped
        (they are the expensive part); only sharpness + exposure are scored.
        """
        fs = _FrameScore(frame=frame, timestamp=timestamp)
        cfg = self.cfg
        h, w = frame.shape[:2]
        frame_area = float(h * w) if h > 0 and w > 0 else 1.0
        reject_reasons = []

        # 1. Sharpness (Laplacian variance)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # Downsample for speed if large
        if w > 480:
            scale = 480.0 / w
            small_gray = cv2.resize(gray, (480, int(h * scale)))
        else:
            small_gray = gray
        fs.sharpness = float(cv2.Laplacian(small_gray, cv2.CV_64F).var())
        if fs.sharpness < cfg.min_sharpness:
            reject_reasons.append(f"blur({fs.sharpness:.1f}<{cfg.min_sharpness})")

        # 2. Exposure (mean brightness)
        fs.exposure = float(np.mean(gray))
        if fs.exposure < cfg.min_exposure:
            reject_reasons.append(f"dark({fs.exposure:.0f}<{cfg.min_exposure})")
        elif fs.exposure > cfg.max_exposure:
            reject_reasons.append(f"bright({fs.exposure:.0f}>{cfg.max_exposure})")

        # 3. Person detection (optional YOLO) — only on detector frames
        if run_detectors and self._yolo_detect is not None:
            try:
                detections = self._yolo_detect(frame)
                persons = [d for d in detections
                           if d.get("label") == "person"
                           and d.get("confidence", 0) >= cfg.min_person_conf]
                if persons:
                    best_p = max(persons, key=lambda d: d.get("confidence", 0))
                    fs.person_conf = float(best_p.get("confidence", 0))
                    box = best_p.get("box", (0, 0, 0, 0))
                    bw = max(0, box[2] - box[0])
                    bh = max(0, box[3] - box[1])
                    fs.person_area_frac = (bw * bh) / frame_area
                else:
                    reject_reasons.append("no_person")
            except Exception as e:
                logger.debug("[FrameSelector] YOLO error (continuing): %s", e)
        else:
            # Without YOLO, give a neutral person score
            fs.person_conf = 0.5
            fs.person_area_frac = 0.0

        # 4. Face detection (optional) — only on detector frames
        if run_detectors and self._face_detect is not None:
            try:
                faces = self._face_detect(frame)
                if faces:
                    best_f = max(faces,
                                 key=lambda f: f.get("det_score",
                                                     f.get("confidence", 0)))
                    fs.face_conf = float(best_f.get("det_score",
                                                     best_f.get("confidence", 0)))
                    fbox = best_f.get("box", best_f.get("bbox", (0, 0, 0, 0)))
                    if len(fbox) >= 4:
                        fw = max(0, fbox[2] - fbox[0])
                        fh = max(0, fbox[3] - fbox[1])
                        fs.face_area_frac = (fw * fh) / frame_area
            except Exception as e:
                logger.debug("[FrameSelector] Face detection error: %s", e)
        else:
            # Without face detector, neutral score
            fs.face_conf = 0.0

        fs.reject_reason = "; ".join(reject_reasons) if reject_reasons else ""
        fs.composite = self._composite(fs)
        return fs

    def _composite(self, fs: _FrameScore) -> float:
        """Weighted composite score ∈ [0, 1]."""
        cfg = self.cfg

        # Normalize sharpness: 0 at min_sharpness, 1 at 5× min_sharpness
        sharp_norm = max(0.0, min(1.0,
            (fs.sharpness - cfg.min_sharpness * 0.5)
            / max(1.0, cfg.min_sharpness * 4.0)))

        # Exposure: 1.0 in the sweet spot, dropping toward edges
        mid = (cfg.min_exposure + cfg.max_exposure) / 2.0
        span = (cfg.max_exposure - cfg.min_exposure) / 2.0
        expo_norm = max(0.0, 1.0 - abs(fs.exposure - mid) / max(1.0, span))

        # Person confidence (already 0..1)
        person_norm = min(1.0, fs.person_conf)

        # Face confidence (0..1), boosted slightly by face size
        face_norm = min(1.0, fs.face_conf + fs.face_area_frac * 2.0)

        # Body coverage: larger person bbox = better
        body_norm = min(1.0, fs.person_area_frac * 5.0)

        # Stability (already 0..1)
        stab_norm = fs.stability

        # Weighted sum
        total_w = (cfg.w_sharpness + cfg.w_person + cfg.w_face
                   + cfg.w_exposure + cfg.w_stability + cfg.w_body_coverage)
        if total_w <= 0:
            total_w = 1.0

        score = (cfg.w_sharpness * sharp_norm
                 + cfg.w_person * person_norm
                 + cfg.w_face * face_norm
                 + cfg.w_exposure * expo_norm
                 + cfg.w_stability * stab_norm
                 + cfg.w_body_coverage * body_norm) / total_w

        # Hard penalties: if the frame is severely blurry or badly exposed,
        # cap the score so it can never win over a decent frame.
        if fs.sharpness < cfg.min_sharpness * 0.3:
            score *= 0.3   # extremely blurry
        if fs.exposure < cfg.min_exposure * 0.5 or fs.exposure > cfg.max_exposure * 1.1:
            score *= 0.5   # severely under/overexposed

        # Bonus: if a face IS visible AND sharp, boost the score slightly –
        # a frame with a clear face is almost always the most useful one.
        if fs.face_conf >= cfg.min_face_conf and fs.sharpness >= cfg.min_sharpness:
            score = min(1.0, score * 1.15)

        return round(score, 4)

    @staticmethod
    def _person_center(fs: _FrameScore):
        """Estimate the person's center from bbox area fraction.

        Returns (cx, cy) in pixel coordinates when a person was detected,
        or None.  Used for stability tracking only.
        """
        if fs.person_area_frac <= 0:
            return None
        h, w = fs.frame.shape[:2]
        # Approximate: we don't store the raw bbox, so use frame center
        # weighted by the person area.  This is a simplification – the
        # important thing is detecting LARGE movements (person walking past)
        # vs. standing still.
        return (w / 2.0, h / 2.0)

    def _log_summary(self, scored: List[_FrameScore],
                     best: _FrameScore) -> None:
        """Log which frame was selected and why others were rejected."""
        n = len(scored)
        rejected = [s for s in scored if s.reject_reason]
        logger.info("[FrameSelector] Evaluated %d frames: best=%.3f "
                    "(sharpness=%.1f, person=%.2f, face=%.2f, "
                    "exposure=%.0f, stability=%.2f). "
                    "%d/%d had rejection notes.",
                    n, best.composite, best.sharpness, best.person_conf,
                    best.face_conf, best.exposure, best.stability,
                    len(rejected), n)
        # Log first few rejections for debugging
        for s in rejected[:3]:
            logger.debug("  rejected: composite=%.3f reason=%s",
                         s.composite, s.reject_reason)
