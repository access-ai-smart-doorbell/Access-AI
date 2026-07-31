"""
Re-Identification module (Phase 9) - recognise a REPEAT unknown visitor by
appearance, so the doorbell can say "The same unknown visitor has come 3 times
today."

WHY appearance re-ID (and not the face model)
----------------------------------------------
Face recognition (Phase 2) answers "is this a KNOWN, enrolled person?". Re-ID
answers a different question: "have I seen THIS stranger before, recently?" - even
with no face (a courier facing away, a hooded visitor). It works on the whole
BODY crop, not the face, and it is deliberately short-lived (a 24h gallery), so a
one-off stranger is forgotten and never accumulates across days.

Backend priority (identical interface, so a stronger model drops in with ZERO
pipeline change - the same pattern as the Phase-5 anti-spoof heuristic):
  A "onnx"      - OSNet exported to ONNX, run via onnxruntime (torch-FREE). Used
                  automatically when a .onnx sits in REID_MODEL_DIR.
  C "histogram" - PLACEHOLDER: an L2-normalised HSV colour histogram of the body
                  crop (global 8x8x8 + upper/lower-body 4x4x4 for coarse spatial
                  layout). Torch-free, dependency-free, ALWAYS works - but keys
                  mostly on clothing colour, so it is NOT production-grade. Logged
                  LOUDLY as a placeholder.

Everything degrades: no cv2 / no crop / empty gallery => the event proceeds with
reid_id None and reid_seen_count 0, exactly as Phase 8. Never raises.
"""

import os
import glob
import uuid

import numpy as np

try:
    import cv2
    _HAS_CV2 = True
except Exception as e:                                    # pragma: no cover
    _HAS_CV2 = False
    print(f"[ReidModule] OpenCV not available, re-ID disabled: {e}")

try:
    import onnxruntime as ort
    _HAS_ORT = True
except Exception:                                         # pragma: no cover
    _HAS_ORT = False


class ReidModule:
    def __init__(self, backend="auto", model_dir="", match_threshold=0.75,
                 ttl_hours=24.0, max_gallery=500, db=None):
        self.match_threshold = float(match_threshold)
        self.ttl_hours = float(ttl_hours)
        self.max_gallery = int(max_gallery)
        self.db = db
        self.model_dir = model_dir

        self._sess = None
        self._inp = None
        self._backend = "histogram"     # safe default; may upgrade to onnx below
        self._last_sim = 0.0            # debug: similarity of the last match

        want = (backend or "auto").lower()
        model_path = self._find_model(model_dir) if want in ("auto", "onnx") else None

        if want in ("auto", "onnx") and model_path and _HAS_ORT:
            try:
                self._sess = ort.InferenceSession(
                    model_path, providers=["CPUExecutionProvider"])
                inp0 = self._sess.get_inputs()[0]
                self._inp = inp0.name
                # Some public OSNet exports bake a FIXED batch size into the
                # graph (the widely mirrored osnet_x0_25_msmt17.onnx wants
                # exactly 16) instead of leaving the dim dynamic. We only ever
                # embed one crop, so remember the required batch and tile the
                # single image up to it in _embed_onnx, taking row 0 back.
                # Rejecting such a file would mean falling back to the colour
                # histogram, which is far worse than one wasted forward pass.
                self._batch = 1
                try:
                    dim0 = inp0.shape[0]
                    if isinstance(dim0, int) and dim0 > 1:
                        self._batch = dim0
                except Exception:                         # pragma: no cover
                    pass
                self._backend = "onnx"
                extra = (f" (fixed batch {self._batch}; one crop is tiled)"
                         if self._batch > 1 else "")
                print(f"[ReidModule] ONNX OSNet re-ID loaded: {model_path}{extra}")
            except Exception as e:                        # pragma: no cover
                print(f"[ReidModule] ONNX load failed ({e}); "
                      "falling back to histogram placeholder.")
                self._backend = "histogram"
        elif want == "onnx" and not model_path:
            print(f"[ReidModule] backend='onnx' but no .onnx in {model_dir}; "
                  "using histogram placeholder instead.")

        if self._backend == "histogram":
            print("[ReidModule] WARNING: using HSV-histogram PLACEHOLDER for "
                  "re-ID (keys on clothing colour, NOT production-grade). Drop an "
                  "OSNet .onnx into REID_MODEL_DIR to upgrade - zero code change.")

    # ------------------------------------------------------------------
    @staticmethod
    def _find_model(model_dir):
        if not model_dir or not os.path.isdir(model_dir):
            return None
        hits = sorted(glob.glob(os.path.join(model_dir, "*.onnx")))
        return hits[0] if hits else None

    def available(self) -> bool:
        # The histogram backend always works when OpenCV is present; the onnx
        # backend needs a loaded session.
        if not _HAS_CV2:
            return False
        return self._backend == "histogram" or self._sess is not None

    def backend_name(self) -> str:
        return "onnx" if self._backend == "onnx" else "histogram (placeholder)"

    def is_placeholder(self) -> bool:
        return self._backend != "onnx"

    def gallery_size(self) -> int:
        try:
            return self.db.reid_count() if self.db is not None else 0
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------
    def embed(self, frame_bgr, person_box=None):
        """Return an L2-normalised appearance vector for the body crop, or None."""
        if not _HAS_CV2 or frame_bgr is None:
            return None
        crop = self._crop(frame_bgr, person_box)
        if crop is None or crop.size == 0:
            return None
        try:
            if self._backend == "onnx" and self._sess is not None:
                return self._embed_onnx(crop)
            return self._embed_histogram(crop)
        except Exception as e:                            # pragma: no cover
            print(f"[ReidModule] embed failed: {e}")
            return None

    @staticmethod
    def _crop(frame_bgr, person_box):
        """Crop the body box; fall back to the whole frame when there's no box."""
        h, w = frame_bgr.shape[:2]
        if person_box:
            x1, y1, x2, y2 = [int(v) for v in person_box]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if x2 - x1 >= 8 and y2 - y1 >= 8:
                return frame_bgr[y1:y2, x1:x2]
        return frame_bgr

    def _embed_histogram(self, crop):
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        h = hsv.shape[0]

        def _hist(region, bins):
            hh = cv2.calcHist([region], [0, 1, 2], None, bins,
                              [0, 180, 0, 256, 0, 256])
            return hh.flatten()

        # Global colour + coarse spatial layout: upper body (torso/clothing) vs
        # lower body, which carry most of the discriminative colour signal.
        mid = max(1, h // 2)
        vec = np.concatenate([
            _hist(hsv, [8, 8, 8]),          # 512 dims - overall appearance
            _hist(hsv[:mid], [4, 4, 4]),    #  64 dims - upper half
            _hist(hsv[mid:], [4, 4, 4]),    #  64 dims - lower half
        ]).astype(np.float32)
        n = float(np.linalg.norm(vec))
        return vec / n if n > 0 else vec

    def _embed_onnx(self, crop):
        img = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, (128, 256))               # OSNet input: 256x128 (HxW)
        img = img.astype(np.float32) / 255.0
        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        img = (img - mean) / std
        img = np.transpose(img, (2, 0, 1))[None, ...]   # NCHW
        batch = getattr(self, "_batch", 1)
        if batch > 1:                                   # fixed-batch export
            img = np.repeat(img, batch, axis=0)
        out = self._sess.run(None, {self._inp: img})[0][0]
        v = np.asarray(out, dtype=np.float32).flatten()
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else v

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------
    def identify(self, embedding, now_iso):
        """Match against the recent gallery. Returns (reid_id, seen_count).

        Best cosine >= threshold => reuse that id and bump its count. Otherwise
        mint a fresh 'v_<hex>' id with count 1. Old / overflowing entries are
        evicted so the gallery stays a 24h working set.
        """
        if embedding is None:
            return None, 0
        gallery = []
        if self.db is not None:
            try:
                gallery = self.db.reid_recent(now_iso, self.ttl_hours)
            except Exception as e:                        # pragma: no cover
                print(f"[ReidModule] gallery read failed: {e}")
                gallery = []

        best_id, best_sim = None, -1.0
        for g in gallery:
            gv = np.frombuffer(g["embedding"], dtype=np.float32)
            if gv.shape != embedding.shape:
                continue
            sim = float(np.dot(gv, embedding))            # both L2-normalised
            if sim > best_sim:
                best_sim, best_id = sim, g["reid_id"]

        if best_id is not None and best_sim >= self.match_threshold:
            self._last_sim = best_sim
            count = 2
            if self.db is not None:
                count = self.db.reid_touch(best_id, now_iso) or 2
            return best_id, count

        # New stranger.
        self._last_sim = best_sim if best_id is not None else 0.0
        rid = "v_" + uuid.uuid4().hex[:8]
        if self.db is not None:
            try:
                self.db.reid_add(rid, embedding.astype(np.float32).tobytes(),
                                 now_iso, seen_count=1)
                self.db.reid_evict(now_iso, self.ttl_hours, self.max_gallery)
            except Exception as e:                        # pragma: no cover
                print(f"[ReidModule] gallery write failed: {e}")
        return rid, 1

    def process(self, frame_bgr, ev, now_iso, person_box=None) -> None:
        """Fill ev.reid_id + ev.reid_seen_count for an unknown visitor."""
        emb = self.embed(frame_bgr, person_box)
        if emb is None:
            return
        rid, count = self.identify(emb, now_iso)
        if rid is not None:
            ev.reid_id = rid
            ev.reid_seen_count = int(count)
