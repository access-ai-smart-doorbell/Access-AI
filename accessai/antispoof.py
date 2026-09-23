"""
AntiSpoofModule - face liveness / anti-spoofing (Phase 5), the SECURITY gate.

WHY this exists
---------------
Face recognition (Phase 2) answers "who is this face?" but NOT "is this a real,
present person or a flat photo / phone screen?". Without a liveness check, holding
up a printed photo of "Rahul" makes AccessAI announce "Rahul is at the door." For
an accessibility product a blind user trusts, that is a security failure. This
module scores a face crop as real-vs-spoof; the pipeline uses that to DOWNGRADE a
matched-but-spoofed face to Unknown before it is ever announced.

Backend selection (priority A -> B -> C, identical public interface)
--------------------------------------------------------------------
A  "silent-face-pip"   : a pip wrapper around MiniVision's Silent-Face models.
B  "onnx-minifasnet"   : the two MiniFASNet ONNX models vendored under
                         models/antispoof/, run with onnxruntime (a Phase-2 dep).
C  "heuristic"         : a laplacian-variance + colour/texture PLACEHOLDER. It is
                         NOT production-grade; it only keeps the pipeline
                         functional and testable when A and B are unavailable. It
                         logs a loud WARNING and must be replaced with real models
                         before any real deployment.

Whatever loads, the rest of AccessAI only ever calls the SAME four methods below,
so swapping a real model in later needs ZERO pipeline change.

Security contract (documented again at each method)
---------------------------------------------------
FAIL-OPEN  : if the detector is unavailable, or the box is invalid, score() returns
             1.0 (treated as real). A broken/missing liveness model must NEVER lock
             out a real known visitor. The security benefit only applies when a
             model is actually loaded.
FAIL-CLOSED: on a CONFIDENT spoof (score < min_score), the pipeline downgrades the
             identity to Unknown and the announcement becomes the photo warning.

CPU-only, Python 3.12. Mirrors every other AccessAI module: guarded imports, an
available() method, print("[AntiSpoofModule] ...") logging, never raises from
__init__.
"""

import os
import glob

import numpy as np

# --- Guarded heavy imports (never crash if a backend dep is missing) ---------
try:
    import cv2
    _HAS_CV2 = True
except Exception as e:                                   # pragma: no cover
    _HAS_CV2 = False
    print(f"[AntiSpoofModule] OpenCV not available: {e}")

try:
    import onnxruntime as ort
    _HAS_ORT = True
except Exception as e:                                   # pragma: no cover
    _HAS_ORT = False
    print(f"[AntiSpoofModule] onnxruntime not available (ONNX backend disabled): {e}")

# Option A wrapper is optional and rarely present on 3.12; probe it lazily so an
# absent package is not even a warning unless backend="pip" is forced.
try:
    import silent_face_anti_spoofing as _silent_face      # type: ignore
    _HAS_SILENTFACE = True
except Exception:                                         # pragma: no cover
    _silent_face = None
    _HAS_SILENTFACE = False


class AntiSpoofModule:
    """Liveness scorer with a swappable backend and a fail-open safety default."""

    def __init__(self, model_dir: str, min_score: float = 0.55,
                 backend: str = "auto"):
        self.model_dir = model_dir
        self.min_score = float(min_score)
        self._backend_pref = (backend or "auto").lower()
        self._backend_name = "none"     # set by whichever backend initialises
        self._ready = False
        self._sessions = []             # onnxruntime sessions (Option B)
        self._sf = None                 # pip predictor (Option A)
        self._warned_failopen = False   # log the fail-open path only once

        try:
            self._init_backend()
        except Exception as e:                            # pragma: no cover
            # Never raise out of __init__: degrade to unavailable (fail-open).
            self._ready = False
            self._backend_name = "none"
            print(f"[AntiSpoofModule] Init failed, liveness disabled "
                  f"(fail-open): {e}")

    # ------------------------------------------------------------------
    # Backend selection: honour an explicit override, else auto A -> B -> C.
    # ------------------------------------------------------------------
    def _init_backend(self) -> None:
        pref = self._backend_pref

        want_pip = pref in ("auto", "pip", "silent-face", "silent-face-pip")
        want_onnx = pref in ("auto", "onnx", "onnx-minifasnet")
        want_heur = pref in ("auto", "heuristic")

        if want_pip and self._try_pip():
            return
        if want_onnx and self._try_onnx():
            return
        if want_heur and self._try_heuristic():
            return

        # An explicit backend was requested but could not load: stay unavailable
        # (fail-open) rather than silently falling back to something else.
        print(f"[AntiSpoofModule] Requested backend '{pref}' unavailable; "
              "liveness disabled (fail-open, real visitors still announced).")
        self._ready = False
        self._backend_name = "none"

    def _try_pip(self) -> bool:
        """Option A: a maintained pip wrapper around Silent-Face."""
        if not _HAS_SILENTFACE:
            return False
        try:
            # The wrapper API varies; we only require a callable that scores a
            # face crop. Kept behind a thin adapter so the rest is backend-blind.
            self._sf = _silent_face
            self._backend_name = "silent-face-pip"
            self._ready = True
            print("[AntiSpoofModule] Backend: silent-face-pip (Option A).")
            return True
        except Exception as e:                            # pragma: no cover
            print(f"[AntiSpoofModule] silent-face pip init failed: {e}")
            return False

    def _try_onnx(self) -> bool:
        """Option B: vendored MiniFASNet ONNX models via onnxruntime."""
        if not (_HAS_ORT and _HAS_CV2):
            return False
        if not os.path.isdir(self.model_dir):
            return False
        model_paths = sorted(glob.glob(os.path.join(self.model_dir, "*.onnx")))
        if not model_paths:
            return False
        try:
            self._sessions = []
            for p in model_paths:
                sess = ort.InferenceSession(
                    p, providers=["CPUExecutionProvider"])
                inp = sess.get_inputs()[0]
                # MiniFASNet expects 80x80; read H,W from the model when static.
                shape = inp.shape
                h = shape[2] if isinstance(shape[2], int) else 80
                w = shape[3] if isinstance(shape[3], int) else 80
                fname = os.path.basename(p)
                self._sessions.append({
                    "sess": sess, "name": inp.name, "h": h, "w": w,
                    "file": fname, "scale": self._scale_from_name(fname),
                })
            self._backend_name = "onnx-minifasnet"
            self._ready = True
            files = ", ".join(s["file"] for s in self._sessions)
            print(f"[AntiSpoofModule] Backend: onnx-minifasnet (Option B) - "
                  f"{len(self._sessions)} model(s): {files}.")
            return True
        except Exception as e:                            # pragma: no cover
            print(f"[AntiSpoofModule] ONNX MiniFASNet load failed: {e}")
            self._sessions = []
            return False

    def _try_heuristic(self) -> bool:
        """Option C: laplacian/texture PLACEHOLDER. Loudly flagged."""
        if not _HAS_CV2:
            return False
        self._backend_name = "heuristic"
        self._ready = True
        print("[AntiSpoofModule] WARNING: using heuristic placeholder, replace "
              "with MiniFASNet models for real security. This is NOT "
              "production-grade liveness (Option C).")
        return True

    # ------------------------------------------------------------------
    def available(self) -> bool:
        return bool(self._ready)

    def backend_name(self) -> str:
        return self._backend_name

    def is_heuristic(self) -> bool:
        """True when the active backend is the placeholder heuristic.

        The UI should show an amber warning when this returns True — the heuristic
        is NOT production-grade and can be trivially bypassed with a quality print.
        """
        return self._backend_name == "heuristic"

    # ------------------------------------------------------------------
    def score(self, frame_bgr, box) -> float:
        """Return the 'real person' probability in [0,1] for the face at `box`.

        FAIL-OPEN: if the module is unavailable, or `box` is missing / zero-area /
        out of frame, we return 1.0 (treated as a real person) and log the reason
        ONCE. Rationale: a broken or missing liveness model must not lock out real
        known users - the security gain only exists while a model is loaded.
        """
        if not self.available() or frame_bgr is None:
            self._warn_failopen("module unavailable")
            return 1.0

        crop = self._crop_face(frame_bgr, box)
        if crop is None:
            self._warn_failopen("invalid/empty face box")
            return 1.0

        try:
            if self._backend_name == "onnx-minifasnet":
                # ONNX path crops the ORIGINAL frame at each model's own scale
                # (2.7x / 4.0x), so it needs frame+box, not the padded crop.
                return self._score_onnx(frame_bgr, box)
            if self._backend_name == "silent-face-pip":
                return self._score_pip(frame_bgr, box, crop)
            if self._backend_name == "heuristic":
                if not getattr(self, "_warned_heuristic", False):
                    self._warned_heuristic = True
                    print("[AntiSpoofModule] ⚠ HEURISTIC liveness is active — this is "
                          "NOT real security. A quality photo/screen WILL bypass it. "
                          "Drop MiniFASNet ONNX models into models/antispoof/ to fix.")
                return self._score_heuristic(crop)
        except Exception as e:                            # pragma: no cover
            # Any inference error is fail-open, not a crash.
            self._warn_failopen(f"scoring error: {e}")
            return 1.0
        return 1.0

    def is_live(self, score: float) -> bool:
        """True if `score` clears the liveness threshold (>= min_score)."""
        return float(score) >= self.min_score

    # ------------------------------------------------------------------
    # Helpers (all preprocessing lives inside this module)
    # ------------------------------------------------------------------
    def _warn_failopen(self, reason: str) -> None:
        if not self._warned_failopen:
            self._warned_failopen = True
            print(f"[AntiSpoofModule] Fail-open (returning score 1.0): {reason}. "
                  "Real visitors are still announced; this is logged once.")

    def _crop_face(self, frame_bgr, box, pad: float = 0.15):
        """Crop the face box with a little padding, clamped to frame bounds.

        Returns a BGR crop, or None if the box is invalid / zero-area.
        """
        try:
            x1, y1, x2, y2 = (int(v) for v in box)
        except Exception:
            return None
        if x2 <= x1 or y2 <= y1:
            return None
        h, w = frame_bgr.shape[:2]
        bw, bh = x2 - x1, y2 - y1
        px, py = int(bw * pad), int(bh * pad)
        x1 = max(0, x1 - px); y1 = max(0, y1 - py)
        x2 = min(w, x2 + px); y2 = min(h, y2 + py)
        if x2 <= x1 or y2 <= y1:
            return None
        crop = frame_bgr[y1:y2, x1:x2]
        if crop is None or crop.size == 0:
            return None
        return crop

    def _score_onnx(self, frame_bgr, box) -> float:
        """Run the MiniFASNet ONNX model(s) and return the averaged 'real' prob.

        Silent-Face (MiniVision) ships TWO MiniFASNets, each trained on a DIFFERENT
        crop scale encoded in its filename (e.g. `2.7_80x80_MiniFASNetV2` uses a
        2.7x box crop, `4_0_0_80x80_MiniFASNetV1SE` uses 4.0x). Reproducing that
        per-model scale crop is essential: feeding both the same padded crop (the
        old bug) gives garbage scores. Preprocessing matches the reference:
        crop-at-scale -> resize to the model's HxW -> RAW 0-255 float, BGR order
        kept, NCHW. CRITICAL: MiniVision's ToTensor (src/data_io/functional.py)
        has its `.div(255)` COMMENTED OUT, so the models were trained on raw
        0-255 pixel values - NOT normalised to [0,1]. Dividing by 255 here makes
        every face (real included) collapse to class 2/spoof. Their softmax
        outputs are AVERAGED; class 1 = real, {0,2} = spoof. Returns the
        probability mass on the 'real' class.
        """
        try:
            x1, y1, x2, y2 = (int(v) for v in box)
        except Exception:
            return 1.0
        bbox = (x1, y1, x2 - x1, y2 - y1)          # x, y, w, h
        total = None
        for s in self._sessions:
            crop = self._crop_with_scale(frame_bgr, bbox, s.get("scale", 2.7))
            if crop is None or crop.size == 0:
                continue
            face = cv2.resize(crop, (s["w"], s["h"]))
            # HWC BGR uint8 -> NCHW float32, RAW 0-255 (NO /255). MiniVision's
            # ToTensor has its .div(255) commented out, so the weights expect
            # 0-255; BGR order kept to match how the reference reads with cv2.
            blob = face.astype(np.float32).transpose(2, 0, 1)[None, ...]
            out = s["sess"].run(None, {s["name"]: blob})[0]
            probs = self._softmax(np.asarray(out).reshape(-1))
            total = probs if total is None else (total + probs)
        if total is None:
            return 1.0
        total = total / float(len(self._sessions))
        real_idx = 1 if total.shape[0] > 1 else 0
        return float(total[real_idx])

    @staticmethod
    def _scale_from_name(fname: str) -> float:
        """Parse the crop scale MiniVision encodes in the model filename.

        Reference names: `2.7_80x80_MiniFASNetV2.onnx` -> 2.7 and
        `4_0_0_80x80_MiniFASNetV1SE.onnx` -> 4.0 (the leading `4_0` is '4.0'; the
        original torch files use `4_0_0_80x80_...`). Falls back to 2.7 (the more
        common scale) when the name doesn't encode one."""
        base = os.path.basename(fname or "").lower()
        head = base.split("_80x80")[0] if "_80x80" in base else base
        # Try dotted form first ("2.7"), then underscore form ("4_0" -> "4.0").
        try:
            if "." in head:
                return float(head.split("_")[0])
            parts = [p for p in head.split("_") if p.isdigit()]
            if len(parts) >= 2:
                return float(f"{parts[0]}.{parts[1]}")
            if len(parts) == 1:
                return float(parts[0])
        except Exception:
            pass
        return 2.7

    @staticmethod
    def _crop_with_scale(frame_bgr, bbox, scale: float):
        """Crop `frame_bgr` around `bbox` (x,y,w,h) at MiniFASNet's `scale`, exactly
        as the Silent-Face reference `CropImage.get_new_box` does: expand the box by
        `scale` about its centre, clamped to the frame, then slice."""
        src_h, src_w = frame_bgr.shape[:2]
        x, y, bw, bh = bbox
        if bw <= 0 or bh <= 0:
            return None
        # The reference caps the scale so the expanded box still fits the frame.
        scale = min((src_h - 1) / float(bh), (src_w - 1) / float(bw), float(scale))
        new_w = bw * scale
        new_h = bh * scale
        cx, cy = x + bw / 2.0, y + bh / 2.0
        left_top_x = cx - new_w / 2.0
        left_top_y = cy - new_h / 2.0
        right_bottom_x = cx + new_w / 2.0
        right_bottom_y = cy + new_h / 2.0
        # Shift the window back inside the frame rather than just clipping (keeps
        # the target size), matching the reference's boundary handling.
        if left_top_x < 0:
            right_bottom_x -= left_top_x
            left_top_x = 0
        if left_top_y < 0:
            right_bottom_y -= left_top_y
            left_top_y = 0
        if right_bottom_x > src_w:
            left_top_x -= (right_bottom_x - src_w)
            right_bottom_x = src_w
        if right_bottom_y > src_h:
            left_top_y -= (right_bottom_y - src_h)
            right_bottom_y = src_h
        x1 = max(0, int(left_top_x)); y1 = max(0, int(left_top_y))
        x2 = min(src_w, int(right_bottom_x)); y2 = min(src_h, int(right_bottom_y))
        if x2 <= x1 or y2 <= y1:
            return None
        return frame_bgr[y1:y2, x1:x2]

    def _score_pip(self, frame_bgr, box, crop) -> float:
        """Adapter for the Option-A pip wrapper. Falls back to heuristic scoring
        if the wrapper's surface isn't what we expect (kept defensive because the
        package API is not standardised)."""
        pred = getattr(self._sf, "predict", None) or getattr(self._sf, "analyze", None)
        if callable(pred):
            res = pred(crop)
            # Accept a float score, or a dict/obj exposing a 'real' probability.
            if isinstance(res, (int, float)):
                return float(res)
            if isinstance(res, dict):
                for k in ("real", "score", "liveness", "prob"):
                    if k in res:
                        return float(res[k])
        return self._score_heuristic(crop)

    def _score_heuristic(self, crop) -> float:
        """PLACEHOLDER liveness in [0,1] - NOT production security.

        Intuition (only): a live face through a camera has rich, BROADBAND texture
        and colour variance; a printed photo or a phone/tablet screen adds tell-tale
        artefacts - a narrow high-frequency band (screen pixel grid / print
        halftone = moire) and a narrower colour gamut. We combine:
          * focus  : normalised Laplacian variance (is there any real detail?),
          * colour : saturation spread (screens/prints narrow the gamut),
          * screen : a PENALTY when energy concentrates in a narrow high-frequency
                     band, the signature of a pixel grid / halftone (moire).
        This is a stand-in so the pipeline is testable; the real fix is dropping the
        MiniFASNet ONNX models into models/antispoof/ (Option B, no code change).

        CALIBRATION - fail-open bias is preserved (a genuinely detailed, artefact-
        free face still scores high so a real visitor is never locked out), but the
        moire penalty now pulls obvious screens/prints below the threshold, which
        the old pure-focus score never did. Phase 16: divisor raised 20 -> 55 so a
        modestly textured face no longer instantly saturates, giving the penalty
        room to act.
        """
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        # Squash focus into ~[0,1]. Larger divisor than before so the score is not
        # pinned at 1.0 for every photo; a flat frame still stays low.
        focus = 1.0 - np.exp(-lap_var / 55.0)
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        sat_std = float(hsv[..., 1].std()) / 48.0
        colour = min(1.0, sat_std)
        screen = self._screen_artefact_score(gray)   # 0 = none .. 1 = strong moire
        score = 0.65 * focus + 0.20 * colour - 0.45 * screen
        return float(max(0.0, min(1.0, score)))

    @staticmethod
    def _screen_artefact_score(gray) -> float:
        """Detect the periodic high-frequency signature of a screen/print (moire).

        A live scene's spectrum falls off smoothly; a display's pixel grid or a
        print's halftone dot pattern injects a concentrated PEAK in the high-freq
        band. We FFT the (windowed) crop, look at the outer/high-frequency ring, and
        return how PEAKY it is (max vs mean energy) mapped to [0,1]. Returns 0 on any
        error (fail toward 'live'). Cheap: a single 128x128 FFT."""
        try:
            g = cv2.resize(gray, (128, 128)).astype(np.float32)
            g -= g.mean()
            # Hann window to suppress edge-wrap spectral leakage.
            win = np.hanning(128)
            g *= np.outer(win, win)
            mag = np.abs(np.fft.fftshift(np.fft.fft2(g)))
            cy, cx = 64, 64
            yy, xx = np.ogrid[:128, :128]
            r = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
            ring = (r > 32) & (r < 60)          # high-frequency band, excl. DC
            band = mag[ring]
            if band.size == 0:
                return 0.0
            mean = float(band.mean()) + 1e-6
            peak = float(band.max())
            ratio = peak / mean                 # smooth spectra ~ low; grids ~ high
            # Map ~[8 .. 40] onto [0..1]; below 8 => no artefact.
            return float(max(0.0, min(1.0, (ratio - 8.0) / 32.0)))
        except Exception:                                 # pragma: no cover
            return 0.0

    @staticmethod
    def _softmax(x: np.ndarray) -> np.ndarray:
        x = x - np.max(x)
        e = np.exp(x)
        return e / (np.sum(e) + 1e-9)
