"""
Face module - detects and recognises known people using InsightFace.

WHY a dedicated face model (and not the VLM added later)
--------------------------------------------------------
Vision-language models are weak at "WHO is this exact person". A dedicated
embedding model (ArcFace, shipped inside InsightFace's `buffalo_l` pack) maps a
face to a 512-D L2-normalised vector where the same person's photos land close
together. Matching is then just cosine similarity (a dot product on normed
vectors): same-person pairs usually score > 0.5, different people < 0.3, so a
threshold of ~0.45 is a safe, tunable default.

Enrollment layout:
    data/known_faces/<PersonName>/<something>.jpg   (name = folder), OR
    data/known_faces/<PersonName>.jpg               (name = filename stem)

Robustness pattern (same as every AccessAI module): a guarded import, an
available() method, and print("[FaceModule] ...") logging. If InsightFace fails
to import or load, the module degrades to "no faces" and NEVER crashes the app.

First run downloads buffalo_l (~300 MB) to ~/.insightface/. This is one-time.
"""

import os
import glob
import time

import numpy as np

try:
    import cv2
    from insightface.app import FaceAnalysis
    _HAS_IF = True
    _IMPORT_ERR = None
except Exception as e:                                # pragma: no cover
    _HAS_IF = False
    _IMPORT_ERR = e
    print(f"[FaceModule] InsightFace/OpenCV not available: {e}\n"
          "             Recognition will be disabled (app still runs). "
          "Install with: pip install insightface==0.7.3 onnxruntime==1.18.1")


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity. For L2-normalised embeddings this is just a dot."""
    return float(np.dot(a, b))


def is_safe_person_name(name: str) -> bool:
    """Reject names that could escape known_faces/ when used as a folder/file.

    A person's name becomes a directory (and part of a URL), so anything with a
    path separator, a parent reference, or a null byte is refused. This is the
    single path-traversal guard reused by every entry point that turns a name
    into a filesystem path (enroll / delete / photo lookup) AND by the server
    routes before they touch disk.
    """
    if not name:
        return False
    if name in (".", ".."):
        return False
    if ".." in name:
        return False
    for ch in ("/", "\\", "\x00"):
        if ch in name:
            return False
    return True


class FaceModule:
    def __init__(self, known_dir, threshold: float = 0.42,
                 model_name: str = "buffalo_l", det_size=(640, 640),
                 ctx_id: int = -1, min_det_score: float = 0.5, db=None):
        self.known_dir = known_dir
        self.threshold = threshold
        self.model_name = model_name
        self.det_size = det_size
        self.ctx_id = ctx_id
        self.min_det_score = min_det_score
        # Phase 13: optional Database handle so upload-enrollment can DURABLY
        # persist each embedding to the known_faces table (best-effort; a DB
        # failure never blocks the in-memory enroll). None in tests / standalone.
        self.db = db

        self.app = None
        self.known_embeddings: list[np.ndarray] = []   # 512-D, L2-normalised
        self.known_names: list[str] = []               # aligned with embeddings

        if not _HAS_IF:
            return

        try:
            print(f"[FaceModule] Loading InsightFace '{model_name}' on "
                  f"{'CPU' if ctx_id < 0 else f'GPU{ctx_id}'} "
                  "(first run downloads ~300 MB to ~/.insightface/)...")
            # Phase 12: also load the genderage sub-model (it ships INSIDE the
            # buffalo_l pack - genderage.onnx, ~1 MB - so this adds NO download and
            # NO new dependency). Each detected face then also exposes an
            # approximate .age (int) and .sex ("M"/"F"), used to enrich the
            # announcement. Recognition + detection behave exactly as before.
            self.app = FaceAnalysis(
                name=model_name,
                allowed_modules=["detection", "recognition", "genderage"],
            )
            self.app.prepare(ctx_id=ctx_id, det_size=det_size)
            self.load_known()
        except Exception as e:                          # pragma: no cover
            # Graceful degradation: never raise out of __init__.
            self.app = None
            print(f"[FaceModule] Failed to initialise ({e}). "
                  "Recognition disabled; the app will still run.")

    # ------------------------------------------------------------------
    def available(self) -> bool:
        return _HAS_IF and self.app is not None

    # ------------------------------------------------------------------
    # Loading known faces from disk
    # ------------------------------------------------------------------
    def load_known(self) -> None:
        self.known_embeddings = []
        self.known_names = []
        if not os.path.isdir(self.known_dir):
            print(f"[FaceModule] No known_faces dir at {self.known_dir}")
            return

        pattern = os.path.join(self.known_dir, "**", "*.*")
        for path in sorted(glob.glob(pattern, recursive=True)):
            if not path.lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            name = self._name_for_path(path)
            img = cv2.imread(path)
            if img is None:
                print(f"[FaceModule] Could not read {path}, skipping.")
                continue
            emb = self._largest_embedding(img)
            if emb is None:
                print(f"[FaceModule] No face found in {path}, skipping.")
                continue
            self.known_embeddings.append(emb)
            self.known_names.append(name)

        unique = sorted(set(self.known_names))
        print(f"[FaceModule] Loaded {len(self.known_embeddings)} known "
              f"face(s) for {len(unique)} people: {unique}")

    def _name_for_path(self, path: str) -> str:
        """Person name = immediate parent folder, else the file stem."""
        parent = os.path.basename(os.path.dirname(path))
        if parent and os.path.normpath(os.path.dirname(path)) != \
                os.path.normpath(self.known_dir):
            return parent
        return os.path.splitext(os.path.basename(path))[0]

    def _largest_embedding(self, image_bgr):
        """Return the normed embedding of the largest face, or None."""
        faces = self.app.get(image_bgr)
        if not faces:
            return None
        f = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) *
                                      (x.bbox[3] - x.bbox[1]))
        return f.normed_embedding

    # ------------------------------------------------------------------
    # Identification
    # ------------------------------------------------------------------
    def identify(self, frame_bgr) -> list[dict]:
        """
        Detect every face and match against known embeddings.

        Returns a list of:
            {"name", "confidence" (cosine sim), "box" (x1,y1,x2,y2), "det_score",
             "age" (int|None, APPROXIMATE), "gender" ("M"|"F"|"")}
        The age/gender keys are ADDITIVE (Phase 12): every pre-existing key is
        unchanged, so callers from Phases 2-11 keep working untouched. age/gender
        come from InsightFace's genderage sub-model and are only meaningful when it
        loaded; both degrade to None/"" otherwise. Faces below min_det_score are
        ignored. List may be empty.
        """
        if not self.available():
            return []

        faces = self.app.get(frame_bgr)
        results: list[dict] = []
        for f in faces:
            if float(f.det_score) < self.min_det_score:
                continue
            emb = f.normed_embedding

            name, best_sim = "Unknown", 0.0
            if self.known_embeddings:
                sims = [_cosine_sim(emb, k) for k in self.known_embeddings]
                i = int(np.argmax(sims))
                best_sim = sims[i]
                if best_sim >= self.threshold:
                    name = self.known_names[i]

            box = tuple(int(v) for v in f.bbox.astype(int).tolist())
            results.append({
                "name": name,
                "confidence": round(float(best_sim), 3),
                "box": box,
                "det_score": float(f.det_score),
                "age": self._face_age(f),
                "gender": self._face_gender(f),
            })
        return results

    # ------------------------------------------------------------------
    @staticmethod
    def _face_age(f):
        """Approximate integer age from the genderage model, or None.

        InsightFace exposes `.age` only when the genderage module loaded; it is a
        float we round to an int. Defensive: any missing attribute / bad value
        yields None so identify() never raises.
        """
        try:
            age = getattr(f, "age", None)
            if age is None:
                return None
            age = int(round(float(age)))
            return age if 0 <= age <= 120 else None
        except Exception:                                 # pragma: no cover
            return None

    @staticmethod
    def _face_gender(f):
        """"M" / "F" from the genderage model, or "" if unavailable.

        Prefers the string `.sex`; falls back to the integer `.gender`
        (1 = male, 0 = female) that older InsightFace builds expose.
        """
        try:
            sex = getattr(f, "sex", None)
            if isinstance(sex, str) and sex.upper() in ("M", "F"):
                return sex.upper()
            g = getattr(f, "gender", None)
            if g is None:
                return ""
            return "M" if int(g) == 1 else "F"
        except Exception:                                 # pragma: no cover
            return ""

    # ------------------------------------------------------------------
    # Enrollment
    # ------------------------------------------------------------------
    def enroll_from_image(self, name: str, image_bgr,
                          timestamp: str | None = None) -> tuple[bool, str]:
        """
        Enroll `name` from one image. Requires at least one detectable face
        (largest is used if several). Saves the source photo under
        known_faces/<name>/ AND appends the embedding to the in-memory lists so
        recognition works immediately, without a restart.
        """
        if not self.available():
            return False, "Face recognition is not available (module not loaded)."
        name = (name or "").strip()
        if not name:
            return False, "Name is empty."

        faces = self.app.get(image_bgr)
        if not faces:
            return False, "No face detected."
        if len(faces) > 1:
            print(f"[FaceModule] {len(faces)} faces in enrollment image for "
                  f"'{name}'; using the largest.")
        f = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) *
                                      (x.bbox[3] - x.bbox[1]))
        emb = f.normed_embedding

        # Persist the source image (dev convenience). A production build would
        # store ONLY the embedding (the known_faces DB table exists for that).
        person_dir = os.path.join(self.known_dir, name)
        try:
            os.makedirs(person_dir, exist_ok=True)
            ts = timestamp or time.strftime("%Y%m%d_%H%M%S")
            out_path = os.path.join(person_dir, f"{ts}.jpg")
            cv2.imwrite(out_path, image_bgr)
        except Exception as e:                            # pragma: no cover
            # Saving the file is best-effort; in-memory enrollment still works.
            print(f"[FaceModule] Could not save enrollment image: {e}")

        # In-memory enrollment -> recognised immediately.
        self.known_embeddings.append(emb)
        self.known_names.append(name)
        return True, f"Enrolled {name}."

    # ------------------------------------------------------------------
    def known_summary(self) -> list[dict]:
        """[{name, count}] over unique enrolled people (for the UI)."""
        counts: dict[str, int] = {}
        for n in self.known_names:
            counts[n] = counts.get(n, 0) + 1
        return [{"name": n, "count": c} for n, c in sorted(counts.items())]

    # ------------------------------------------------------------------
    # Phase 13: upload-photo enrollment + management.
    #
    # Register known people from IMAGE FILES (not just the live camera): each
    # photo becomes another embedding under the SAME name, which improves match
    # reliability. Everything reuses the same detect -> largest face ->
    # normed_embedding path as enroll_from_image() and load_known(); no new
    # InsightFace logic. In-memory enrollment happens FIRST (recognised with no
    # restart); disk + DB persistence are best-effort and never block it.
    # ------------------------------------------------------------------
    def enroll_from_files(self, name: str, images: list) -> dict:
        """Enroll `name` from a LIST of BGR images (e.g. decoded uploads).

        Per image: no face -> skipped with a reason; several faces -> the LARGEST
        is used and a warning is noted; one face -> used. Each accepted photo is
        saved under known_faces/<name>/, its embedding appended to the in-memory
        gallery (recognised immediately), and persisted to the known_faces DB
        table (best-effort). Returns:
            {name, added, skipped:[...], warnings:[...], known_count,
             photos_for_name}
        """
        name = (name or "").strip()
        result = {"name": name, "added": 0, "skipped": [], "warnings": [],
                  "known_count": len(self.known_names), "photos_for_name": 0}
        if not self.available():
            result["skipped"].append("Face recognition is not available.")
            return result
        if not name:
            result["skipped"].append("Name is empty.")
            return result
        if not is_safe_person_name(name):
            result["skipped"].append("Invalid name (no '/', '\\', or '..').")
            return result

        person_dir = os.path.join(self.known_dir, name)

        base_ts = time.strftime("%Y%m%d_%H%M%S")
        for idx, image in enumerate(images):
            tag = f"photo {idx + 1}"
            if image is None:
                result["skipped"].append(f"{tag}: not a readable image")
                continue
            try:
                faces = self.app.get(image)
            except Exception as e:                        # pragma: no cover
                result["skipped"].append(f"{tag}: detection failed ({e})")
                continue
            if not faces:
                result["skipped"].append(f"{tag}: no face detected")
                continue
            if len(faces) > 1:
                result["warnings"].append(
                    f"{tag}: {len(faces)} faces found, used the largest")
            f = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) *
                                          (x.bbox[3] - x.bbox[1]))
            emb = f.normed_embedding

            # Save the source photo. The folder is created lazily (only once we
            # have a real face to store) so an all-skipped upload leaves no
            # phantom person behind. The filename probes for a free slot so two
            # uploads landing in the same wall-clock second can't overwrite each
            # other (idx alone resets to 0 on every separate request).
            out_path = ""
            try:
                os.makedirs(person_dir, exist_ok=True)
                out_path = self._free_photo_path(person_dir, base_ts)
                cv2.imwrite(out_path, image)
            except Exception as e:                        # pragma: no cover
                print(f"[FaceModule] Could not save upload for '{name}': {e}")
                out_path = ""

            # In-memory enrollment FIRST -> recognised on the next trigger.
            self.known_embeddings.append(emb)
            self.known_names.append(name)
            result["added"] += 1

            # Durable DB copy (best-effort; must not block the in-memory enroll).
            if self.db is not None:
                try:
                    self.db.known_face_add(
                        name, out_path,
                        np.asarray(emb, dtype=np.float32).tobytes())
                except Exception as e:                    # pragma: no cover
                    print(f"[FaceModule] DB persist failed for '{name}': {e}")

        result["known_count"] = len(self.known_names)
        result["photos_for_name"] = self._photos_on_disk(name)
        return result

    def remove_person(self, name: str) -> dict:
        """Delete a person everywhere: in-memory gallery, disk photos, DB rows.

        Removes all embeddings/names for `name`, the known_faces/<name>/ folder
        (and any loose <name>.jpg/.jpeg/.png), and their known_faces DB rows.
        Path-safe: an unsafe name is refused before any filesystem access.
        Returns {removed, ok, message, known_count}.
        """
        import shutil
        name = (name or "").strip()
        result = {"removed": name, "ok": False, "message": "",
                  "known_count": len(self.known_names)}
        if not name or not is_safe_person_name(name):
            result["message"] = "Invalid name."
            return result

        # In-memory: drop every embedding/name pair for this person.
        kept_emb, kept_names = [], []
        for emb, nm in zip(self.known_embeddings, self.known_names):
            if nm == name:
                continue
            kept_emb.append(emb)
            kept_names.append(nm)
        removed_mem = len(self.known_names) - len(kept_names)
        self.known_embeddings, self.known_names = kept_emb, kept_names

        # Disk: the person folder + any loose <name>.<ext> at the top level.
        person_dir = os.path.join(self.known_dir, name)
        try:
            if os.path.isdir(person_dir):
                shutil.rmtree(person_dir)
        except Exception as e:                            # pragma: no cover
            print(f"[FaceModule] Could not delete folder for '{name}': {e}")
        for ext in (".jpg", ".jpeg", ".png"):
            loose = os.path.join(self.known_dir, name + ext)
            try:
                if os.path.isfile(loose):
                    os.remove(loose)
            except Exception as e:                        # pragma: no cover
                print(f"[FaceModule] Could not delete file {loose}: {e}")

        # Durable DB rows (best-effort).
        if self.db is not None:
            try:
                self.db.known_face_delete(name)
            except Exception as e:                        # pragma: no cover
                print(f"[FaceModule] DB delete failed for '{name}': {e}")

        result["known_count"] = len(self.known_names)
        result["ok"] = True
        result["message"] = f"Removed {name} ({removed_mem} embedding(s))."
        return result

    def list_people(self) -> list[dict]:
        """[{name, photos, count, sample}] over everyone known (disk + memory).

        `photos` (== `count`, kept for backward-compat with the old /known
        payload) is how many source images are stored for the person; `sample`
        is the /known_photo URL when at least one photo exists, else None. People
        present only in the in-memory gallery (e.g. Phase-9 auto-enrolled without
        a photo) are still listed with photos=0.
        """
        names = set(self.known_names)
        if os.path.isdir(self.known_dir):
            for entry in os.listdir(self.known_dir):
                full = os.path.join(self.known_dir, entry)
                if os.path.isdir(full):
                    names.add(entry)
                elif entry.lower().endswith((".jpg", ".jpeg", ".png")):
                    names.add(os.path.splitext(entry)[0])

        people = []
        for name in sorted(names):
            photos = self._photos_on_disk(name)
            people.append({
                "name": name,
                "photos": photos,
                "count": photos,                          # backward-compat alias
                "sample": (f"/known_photo/{name}" if photos > 0 else None),
            })
        return people

    def sample_photo_path(self, name: str):
        """Absolute path of a representative saved photo for `name`, or None.

        Powers the GET /known_photo/<name> thumbnail. Path-safe: an unsafe name
        returns None without touching disk.
        """
        name = (name or "").strip()
        if not is_safe_person_name(name):
            return None
        person_dir = os.path.join(self.known_dir, name)
        if os.path.isdir(person_dir):
            for e in sorted(os.listdir(person_dir)):
                if e.lower().endswith((".jpg", ".jpeg", ".png")):
                    return os.path.join(person_dir, e)
        for ext in (".jpg", ".jpeg", ".png"):
            loose = os.path.join(self.known_dir, name + ext)
            if os.path.isfile(loose):
                return loose
        return None

    def _free_photo_path(self, person_dir: str, base_ts: str) -> str:
        """Return a not-yet-existing `<base_ts>_<n>.jpg` path inside person_dir.

        Probes n = 1, 2, 3, ... until a free slot is found, so photos enrolled in
        the same wall-clock second (across separate uploads) never overwrite.
        """
        n = 1
        while True:
            p = os.path.join(person_dir, f"{base_ts}_{n}.jpg")
            if not os.path.exists(p):
                return p
            n += 1

    def _photos_on_disk(self, name: str) -> int:
        """Count stored source images for `name` (folder files + loose file)."""
        count = 0
        person_dir = os.path.join(self.known_dir, name)
        if os.path.isdir(person_dir):
            for e in os.listdir(person_dir):
                if e.lower().endswith((".jpg", ".jpeg", ".png")):
                    count += 1
        for ext in (".jpg", ".jpeg", ".png"):
            if os.path.isfile(os.path.join(self.known_dir, name + ext)):
                count += 1
        return count

    # ------------------------------------------------------------------
    # Phase 9 hooks (auto-enrollment). Both are ADDITIVE: identify() and its
    # return shape are untouched, so Phases 2-8 behave exactly as before.
    # ------------------------------------------------------------------
    def embed_largest_face(self, frame_bgr):
        """Return (normed_embedding, bbox) of the LARGEST face, or (None, zeros).

        Used by auto-enrollment to collect UNKNOWN face embeddings for clustering.
        Does not consult the known gallery and does not alter identify().
        """
        if not self.available():
            return None, (0, 0, 0, 0)
        try:
            faces = self.app.get(frame_bgr)
        except Exception as e:                            # pragma: no cover
            print(f"[FaceModule] embed_largest_face detection failed: {e}")
            return None, (0, 0, 0, 0)
        if not faces:
            return None, (0, 0, 0, 0)
        f = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) *
                                      (x.bbox[3] - x.bbox[1]))
        box = tuple(int(v) for v in f.bbox.astype(int).tolist())
        return f.normed_embedding, box

    def enroll_embedding(self, name: str, embedding) -> bool:
        """Promote a raw ArcFace embedding to a KNOWN face in memory.

        Auto-enrollment uses this to save a clustered unknown WITHOUT a photo:
        the embedding is L2-normalised and appended to the in-memory gallery, so
        the person is recognised on the very next trigger (no restart). We keep
        the same append-to-lists model that enroll_from_image() uses.
        """
        if not self.available():
            return False
        name = (name or "").strip()
        if not name or embedding is None:
            return False
        emb = np.asarray(embedding, dtype=np.float32)
        norm = float(np.linalg.norm(emb))
        if norm > 0:
            emb = emb / norm
        self.known_embeddings.append(emb)
        self.known_names.append(name)
        print(f"[FaceModule] Auto-enrolled '{name}' from a clustered embedding "
              "(no photo).")
        return True
