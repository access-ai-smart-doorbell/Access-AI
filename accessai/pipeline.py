"""
Pipeline - the one place that turns a camera frame into a Visitor Event.

Each phase adds a step here, but the SHAPE never changes: take a frame, fill in
a VisitorEvent, save a snapshot, persist it, return it. Modules are injected via
the constructor (all optional, default None) so later phases wire in AI without
touching call sites.

Phase 1 does NO AI: it just creates an event, writes "Someone is at the door.",
saves a snapshot, and stores it.
"""

import os
import json
import uuid
import threading
import time as _time
import datetime as _dt
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

try:
    import cv2
    _HAS_CV2 = True
except Exception as e:                                # pragma: no cover
    _HAS_CV2 = False
    print(f"[Pipeline] OpenCV not available, snapshots disabled: {e}")

from .visitor_event import (VisitorEvent, Identity, DetectedObject, Person,
                            people_to_dicts)
from .context_engine import (infer_intent, compose_interim_announcement,
                             _COURIER_KEYWORDS)
from .accessibility import compose_announcement
from .database import Database


# Fallback parcel labels if run.py doesn't pass config.PARCEL_LABELS (keeps the
# module importable/testable standalone). Mirrors config.PARCEL_LABELS.
_DEFAULT_PARCEL_LABELS = {"backpack", "handbag", "suitcase", "book"}


def map_sex(sex: str) -> str:
    """InsightFace 'M'/'F' -> spoken 'man'/'woman' ('' when unknown)."""
    return {"M": "man", "F": "woman"}.get((sex or "").upper(), "")


class Pipeline:
    def __init__(
        self,
        db: Database,
        history_dir: str,
        *,
        # Injected in later phases - kept here so call sites never change.
        face=None, face_enabled: bool = False,          # Phase 2
        vision=None, vision_enabled: bool = False,       # Phase 3
        antispoof=None, antispoof_enabled: bool = False, # Phase 5
        vlm=None, vlm_enabled: bool = False,             # Phase 6
        ocr=None, ocr_enabled: bool = False,             # Phase 6
        speech=None, speech_enabled: bool = False,       # Phase 7
        translate=None, translate_enabled: bool = False, # Phase 8
        reid=None, reid_enabled: bool = False,           # Phase 9
        autoenroll=None, autoenroll_enabled: bool = False,  # Phase 9
        access=None,                                     # Phase 4
        parcel_labels=None,                              # Phase 3
        courier_keywords=None,                           # Phase 6
        vlm_only_for_unknown: bool = True,               # Phase 6
        vlm_async_enrich: bool = True,                   # Phase 12
        vlm_enrich_speak: bool = True,                   # Phase 12
        vlm_enrich_speak_full: bool = True,              # Phase 12
        speech_capture_on_trigger: bool = True,          # Phase 7
        translate_announcement: bool = False,            # Phase 8
        cooldown: float = 0.0,                           # Phase 4
    ):
        self.db = db
        self.history_dir = history_dir

        # Store every injected module + flag as an attribute, even if unused now.
        self.face, self.face_enabled = face, face_enabled
        self.vision, self.vision_enabled = vision, vision_enabled
        self.antispoof, self.antispoof_enabled = antispoof, antispoof_enabled
        self.vlm, self.vlm_enabled = vlm, vlm_enabled
        self.ocr, self.ocr_enabled = ocr, ocr_enabled
        self.speech, self.speech_enabled = speech, speech_enabled
        self.translate, self.translate_enabled = translate, translate_enabled
        self.reid, self.reid_enabled = reid, reid_enabled
        self.autoenroll, self.autoenroll_enabled = autoenroll, autoenroll_enabled
        self.access = access
        # Phase 3: which COCO labels count as "carried parcels/bags".
        self._parcel_labels = parcel_labels or set(_DEFAULT_PARCEL_LABELS)
        # Phase 6: courier keywords injected here so context_engine stays pure.
        self._courier_keywords = courier_keywords or _COURIER_KEYWORDS
        # Phase 6: when True, the cloud VLM is skipped for KNOWN faces.
        self.vlm_only_for_unknown = vlm_only_for_unknown
        # Phase 12 (SPEED): when True, an UNKNOWN visitor is announced IMMEDIATELY
        # from fast local signals (face age/gender + YOLO + intent); the slow cloud
        # VLM then runs in a background thread and UPDATES the stored event +
        # dashboard when it returns (it does NOT re-speak). When False, the VLM call
        # is made inline (the visitor waits for it) exactly as in Phase 6.
        self.vlm_async_enrich = vlm_async_enrich
        # Phase 12 (RICHNESS): when True, the background enrich SPEAKS the details
        # the VLM just added (the appearance + scene sentences) as a short follow-up
        # utterance - so a Blind user actually HEARS the full description, not only
        # the fast first line. Only the NEWLY-added delta is spoken (never the whole
        # announcement again), and only in blind/both mode. When False, the enrich
        # updates the screen silently (the original Phase-12 behaviour).
        self.vlm_enrich_speak = vlm_enrich_speak
        # Phase 12 (RICHNESS, follow-up mode): when True, that follow-up speaks the
        # WHOLE recomposed announcement (who + appearance + scene) instead of only
        # the delta, and the INSTANT first line is never suppressed by the cooldown -
        # so the user hears an immediate alert AND, moments later, the full details.
        # The "who" opening is heard twice by design. When False, delta-only.
        self.vlm_enrich_speak_full = vlm_enrich_speak_full
        # Injected by run.py AFTER make_app so a late VLM result can be pushed to
        # the browser. Signature: broadcast(payload_dict) -> None, thread-safe. When
        # None (e.g. standalone use / tests) the enrich still updates the DB.
        self._enrich_broadcast = None
        # Long-lived 2-worker pool for the concurrent face+vision step (SPEED:
        # created on first ring, reused for every later one).
        self._percept_pool = None
        # Phase 7: when True, /trigger auto-records mic audio for transcription.
        self.speech_capture_on_trigger = speech_capture_on_trigger
        # Phase 8: when True, translate the WHOLE announcement (not just the
        # transcript) into the user's language and re-speak it. Default off.
        self.translate_announcement = translate_announcement
        # Phase 4: announcement cooldown - don't re-speak identical announcements
        # within `cooldown` seconds (the event is still saved either way).
        self._cooldown = float(cooldown)
        self._last_spoken_text = ""
        self._last_spoken_at = 0.0
        # Concurrency: /trigger, /ring, and the wake-word thread can all reach
        # run_once at the same time (each on its own executor thread). One lock
        # serializes whole runs - the pipeline mutates shared state (cooldown
        # fields, percept pool) and speaks through a single TTS engine, so two
        # interleaved runs would race each other and talk over themselves.
        self._run_lock = threading.Lock()

    # ------------------------------------------------------------------
    def run_once(self, frame_bgr, trigger: str = "manual",
                 audio=None) -> VisitorEvent:
        """Turn one frame (and optional audio) into a stored VisitorEvent.

        `audio` is an optional pre-captured 16 kHz mono float32 numpy array (e.g.
        a decoded WAV upload). When it's None and speech_capture_on_trigger is
        set, the speech step records live from the mic - which BLOCKS for
        SPEECH_SECONDS, so the announcement arrives after the recording window.
        The server runs this in an executor to keep the event loop responsive.
        Concurrent callers queue on the run lock and execute one at a time.
        """
        with self._run_lock:
            return self._run_once_locked(frame_bgr, trigger, audio)

    def _run_once_locked(self, frame_bgr, trigger, audio) -> VisitorEvent:
        ev = VisitorEvent(
            event_id=self._new_event_id(),
            timestamp=_dt.datetime.now().isoformat(timespec="seconds"),
            trigger=trigger,
        )

        # --- Perception steps (each filled by a later phase) ---
        # Phase 12 (SPEED): face recognition and object detection are INDEPENDENT
        # (each reads only the frame) and both release the GIL inside native
        # onnxruntime / torch inference, so we run them CONCURRENTLY on a 2-worker
        # pool and gather the results. Anti-spoof still runs AFTER the face step
        # (it needs the face box) and the VLM after that (it needs the identity).
        run_face = (self.face_enabled and self.face is not None
                    and self.face.available())
        run_vision = (self.vision_enabled and self.vision is not None
                      and self.vision.available())
        face_results, vision_detections = None, None
        if run_face and run_vision:
            # SPEED: reuse one long-lived pool instead of building + tearing
            # down a ThreadPoolExecutor (two OS threads) on every ring.
            if self._percept_pool is None:
                self._percept_pool = ThreadPoolExecutor(
                    max_workers=2, thread_name_prefix="percept")
            fut_face = self._percept_pool.submit(self.face.identify, frame_bgr)
            fut_vision = self._percept_pool.submit(self.vision.detect, frame_bgr)
            face_results = fut_face.result()
            vision_detections = fut_vision.result()
        elif run_face:
            face_results = self.face.identify(frame_bgr)
        elif run_vision:
            vision_detections = self.vision.detect(frame_bgr)

        # Phase 2 + Phase 15: face recognition for EVERY detected face (not just
        # the largest). Build one Person per face - name/confidence/box plus the
        # approximate age + gender from InsightFace's genderage model. Phase 5
        # liveness runs PER FACE below; the event-level identity/age/gender/face_box
        # are then mirrored from the PRIMARY person (first known, else largest) so
        # every Phase 2-14 call site keeps working unchanged. Age is only ever
        # spoken as a RANGE and NEVER for known people (accessibility enforces it).
        people: list[Person] = []
        if face_results:
            for f in face_results:
                people.append(Person(
                    known=(f["name"] != "Unknown"),
                    name=f["name"],
                    confidence=float(f.get("confidence", 0.0)),
                    age=f.get("age"),
                    gender=map_sex(f.get("gender", "")),
                    box=tuple(f["box"]),
                ))
            ev.visitor_count = len(people)

        # Phase 5 (per-face): liveness / anti-spoofing on EACH face. FAIL-OPEN: an
        # unavailable detector returns score 1.0 (is_spoof False) so real visitors
        # are never locked out. FAIL-CLOSED: a confident spoof downgrades THAT
        # person to Unknown, so a flat photo of a known person held up next to a
        # real visitor is stripped of its name while the real person is still
        # recognised. The event-level is_spoof is set only when EVERY face is a
        # spoof (the classic single-photo case -> photo warning); a mix of a real
        # person + a photo keeps the event live and cautions per-face instead.
        antispoof_on = (self.antispoof_enabled and self.antispoof is not None
                        and self.antispoof.available())
        for p in people:
            if antispoof_on and p.box != (0, 0, 0, 0):
                p.spoof_score = float(self.antispoof.score(frame_bgr, p.box))
                p.is_spoof = not self.antispoof.is_live(p.spoof_score)
                if p.is_spoof and p.known:
                    p.known = False
                    p.name = "Unknown"      # keep p.confidence for the record

        ev.people = people

        # Event-level mirror for backward compatibility (Phase 2-14 read these):
        #   identity/age/gender  -> the PRIMARY person (first LIVE known, else the
        #                           largest face); face_box -> the largest face.
        if people:
            largest = max(people, key=lambda p: (p.box[2] - p.box[0]) *
                                                 (p.box[3] - p.box[1]))
            ev.face_box = tuple(largest.box)
            live_known = [p for p in people if p.known and not p.is_spoof]
            primary = live_known[0] if live_known else largest
            ev.identity = Identity(known=primary.known, name=primary.name,
                                   confidence=primary.confidence)
            ev.age, ev.gender = primary.age, primary.gender
            ev.spoof_score = float(primary.spoof_score)
            # Whole event is a spoof only when NO real person is present at all.
            ev.is_spoof = all(p.is_spoof for p in people)

        # Phase 3: object detection -> ev.detected_objects, ev.carried_objects,
        # and reconcile ev.visitor_count with the people YOLO actually sees.
        # (Phase 12: detection already ran, concurrently with face, just above.)
        face_count = ev.visitor_count  # from Phase 2 (number of faces)
        if vision_detections is not None:
            detections = vision_detections
            ev.detected_objects = [
                DetectedObject(label=d["label"], confidence=d["confidence"],
                               box=tuple(d["box"])) for d in detections
            ]
            carried, person_count = self.vision.summarize(
                detections, self._parcel_labels)
            ev.carried_objects = carried
            # Phase 15 (fixed): an "extra" visitor is a YOLO body with NO
            # recognised face. Counting person_count - face_count naively turned
            # every weak/duplicate person box into a phantom visitor (e.g. a 0.47
            # box overlapping a known person -> a bogus "1 other person"). Instead
            # count only high-confidence bodies that don't already wrap a detected
            # face, then total = recognised faces + those genuine extras.
            face_boxes = [p.box for p in ev.people]
            ev.extra_unknown = self.vision.count_extra_people(detections, face_boxes)
            ev.visitor_count = face_count + ev.extra_unknown

        # Phase 6: VLM scene description + OCR -> ev.scene_summary, ev.ocr_text.
        # Called ONLY for UNKNOWN visitors: a matched known face skips the cloud
        # round-trip entirely (saves latency + free-tier quota, and keeps known
        # people's images off a third-party API). ONE combined call fills both
        # the scene sentence and any parcel-label text. FAIL-SOFT: if the module
        # is unavailable or every API key fails, both fields stay "" and the
        # pipeline continues on YOLO-only signals - it never blocks or crashes.
        # A "something is actually there" guard avoids spending a call on an
        # empty frame (nobody + no objects).
        # Phase 12 (SPEED): the VLM is the slowest step (a cloud round-trip). When
        # vlm_async_enrich is on, we DEFER it for unknown visitors - the fast local
        # announcement is spoken now, and _enrich_async() runs the VLM in the
        # background and updates the event + dashboard when it returns. Otherwise
        # the call is made INLINE here, exactly as in Phase 6.
        # Phase 15/16: the VLM describes EVERY person at the door. When
        # vlm_only_for_unknown is True it runs only when at least one visitor is
        # unknown (an unrecognised face OR a YOLO body with no face); a known-only
        # scene then skips the cloud round-trip (Phase 6/12 latency+privacy win).
        # Phase 16 flips the default to False so KNOWN people are described too
        # (name + clothing/mood + scene, never age/gender) - this DOES send their
        # frame to the cloud VLM, which is the accepted trade-off for the richer
        # announcement.
        has_unknown = (any(not p.known for p in ev.people)
                       or ev.extra_unknown > 0)
        skip_known = self.vlm_only_for_unknown and not has_unknown
        something_present = (ev.visitor_count > 0 or bool(ev.detected_objects)
                             or ev.face_box != (0, 0, 0, 0))
        vlm_wanted = (self.vlm_enabled and self.vlm is not None
                      and self.vlm.available() and not ev.is_spoof
                      and not skip_known and something_present)
        # Defer whenever the VLM will run: the fast local announcement (name, or
        # age/gender for unknowns) is spoken NOW and the VLM description - for
        # known and unknown alike - follows in the background enrich. A LOCAL
        # variable (not instance state): it belongs to this run only, and as a
        # field it raced concurrent runs before the run lock existed.
        defer_vlm = bool(vlm_wanted and self.vlm_async_enrich)
        if vlm_wanted and not defer_vlm:
            result = self.vlm.describe_and_read(
                frame_bgr, facts=self._vlm_facts(ev))
            self._apply_vlm_result(ev, result)

        # Phase 7: speech recognition -> ev.speech_transcript, ev.language_detected.
        # Either an audio array was handed in (a decoded WAV upload) or we record
        # live from the mic on trigger. VAD gates transcription so we never feed
        # Whisper silence (it hallucinates words from noise). FAIL-SOFT: no mic /
        # no libs / no speech => empty transcript and the event proceeds exactly
        # as Phase 6. Runs for KNOWN and UNKNOWN alike (unlike the VLM).
        # Phase 12 (UX): the doorbell must NEVER eavesdrop. A plain /trigger does
        # ZERO audio work - speech is transcribed ONLY from an explicitly supplied
        # audio array (the opt-in two-way "Hear Visitor" button -> POST
        # /hear_visitor). The old auto-record-on-trigger branch is intentionally
        # gone; speech_capture_on_trigger is retained only for backward config
        # compatibility and is treated as False for the doorbell path.
        if (self.speech_enabled and self.speech is not None
                and self.speech.available() and audio is not None):
            text, lang = "", ""
            if (not self.speech.use_vad) or self.speech.has_speech(audio):
                text, lang = self.speech.transcribe(audio)
            ev.speech_transcript = text or ""
            ev.language_detected = lang or ""

        # Phase 8: translation -> ev.translated_transcript.
        # Translate the visitor's transcript into the USER'S language so the
        # announcement (Blind) + caption (Deaf) are understandable. The target
        # language comes from the injected TranslateModule (pipeline stays
        # config-free). Same-language => no API call (the module short-circuits).
        # FAIL-SOFT: no translator / same language / failure => the field stays
        # empty and accessibility falls back to the raw speech_transcript.
        if (self.translate_enabled and self.translate is not None
                and ev.speech_transcript):
            src = ev.language_detected or ""
            target = getattr(self.translate, "user_language", "en")
            if src != target:
                translated = self.translate.translate(
                    ev.speech_transcript, src_lang=src, target_lang=target)
                # Only store a genuine, changed translation; otherwise leave it
                # empty so compose_announcement uses the original transcript.
                if (translated and translated.strip()
                        and translated.strip() != ev.speech_transcript.strip()):
                    ev.translated_transcript = translated

        # Phase 9: memory layer - visitor re-ID + auto-enroll. Runs for UNKNOWN,
        # non-spoof visitors ONLY: a known face is already identified (and its
        # image stays off any gallery for privacy), and a suspected spoof is never
        # memorised. Re-ID fills reid_id + reid_seen_count (accessibility already
        # speaks "The same unknown visitor has come N times today." for count>=2).
        # Auto-enroll stashes the unknown FACE embedding so DBSCAN can later
        # suggest saving a frequent visitor. FAIL-SOFT: any error is logged and
        # the event proceeds exactly as Phase 8.
        is_unknown_person = ((not ev.identity.known) and ev.visitor_count > 0
                             and not ev.is_spoof)
        if is_unknown_person:
            now_iso = ev.timestamp
            if (self.reid_enabled and self.reid is not None
                    and self.reid.available()):
                person_box = self._largest_person_box(ev)
                try:
                    self.reid.process(frame_bgr, ev, now_iso,
                                      person_box=person_box)
                except Exception as e:                    # pragma: no cover
                    print(f"[Pipeline] Re-ID skipped: {e}")
            if (self.autoenroll_enabled and self.autoenroll is not None
                    and self.autoenroll.available()
                    and self.face is not None and self.face.available()):
                try:
                    emb, _box = self.face.embed_largest_face(frame_bgr)
                    if emb is not None:
                        self.autoenroll.add_unknown_face(
                            emb, ev.event_id, now_iso)
                except Exception as e:                    # pragma: no cover
                    print(f"[Pipeline] Auto-enroll skipped: {e}")

        # Phase 3: context engine fuses all signals into a conservative intent.
        # Courier keywords are injected (Phase 6) so the engine stays pure.
        ev.intent, ev.confidence = infer_intent(ev, self._courier_keywords)

        # Phase 4: hand the event to the accessibility engine, which OWNS
        # announcement composition and speaks it (Blind/both). A cooldown avoids
        # double-speaking an identical announcement fired within EVENT_COOLDOWN
        # seconds - the event is still saved, only the audio is suppressed.
        if self.access is not None:
            text = compose_announcement(ev)      # peek final text for cooldown
            now = _time.monotonic()
            # Phase 12 (follow-up mode): when this UNKNOWN visitor's full
            # description is being deferred to the background enrich, the instant
            # first line is the ONLY thing spoken now - so it must never be
            # suppressed by the cooldown, otherwise a repeat unknown hears nothing
            # until the (delayed) follow-up. The full details follow moments later.
            instant_alert = defer_vlm
            in_cooldown = (
                not instant_alert
                and self._cooldown > 0
                and text == self._last_spoken_text
                and (now - self._last_spoken_at) < self._cooldown
            )
            want_audio = not in_cooldown
            # Phase 8 (optional, default off): translate the WHOLE announcement
            # into the user's language and speak ONLY that. Compose silently first
            # so we don't speak the English version and then the translation.
            do_whole = (self.translate_announcement and self.translate is not None
                        and self.translate_enabled)
            self.access.deliver(ev, speak=want_audio and not do_whole)
            if do_whole:
                target = getattr(self.translate, "user_language", "en")
                whole = self.translate.translate(ev.announcement_text,
                                                 src_lang="en", target_lang=target)
                translated = bool(
                    whole and whole.strip()
                    and whole.strip() != ev.announcement_text.strip())
                if translated:
                    ev.announcement_text = whole
                if want_audio and getattr(self.access, "mode", "both") in (
                        "blind", "both"):
                    # Phase 17: hint the text's language so a per-language edge
                    # voice speaks it (Malayalam words in a Malayalam voice).
                    self.access.speak_text(
                        ev.announcement_text,
                        lang=target if translated else "")
            if not in_cooldown:
                self._last_spoken_text = text
                self._last_spoken_at = now
        else:
            # No accessibility engine injected: fall back to the context engine's
            # interim sentence (text only, no audio).
            ev.announcement_text = compose_interim_announcement(ev)

        # --- Snapshot + persist ---
        ev.snapshot_path = self._save_snapshot(frame_bgr, ev)
        self.db.save_event(ev)

        # Phase 12 (SPEED): the fast announcement is already spoken + saved. Now,
        # for a deferred unknown visitor, run the slow VLM in the BACKGROUND and
        # update the stored event + dashboard when it returns. This never blocks
        # run_once, so the doorbell stays fast; the enrich thread is best-effort.
        if defer_vlm:
            self._enrich_async(ev, frame_bgr)
        return ev

    # ------------------------------------------------------------------
    @staticmethod
    def _vlm_facts(ev: VisitorEvent) -> str:
        """Build a ground-truth string from the on-device detectors for the VLM.

        The local face + YOLO detectors are far more reliable at COUNTING and
        IDENTIFYING people than the cloud VLM (which, ungrounded, invents extra
        people and swaps clothing between them). We hand the model the exact
        person count, how many are known-by-name vs unknown, and the detected /
        carried objects so it describes exactly those people and stops
        hallucinating. Pure/read-only - no model calls, safe for the async path.
        """
        total = int(ev.visitor_count or 0)
        known_names = [p.name for p in ev.people if p.known and p.name
                       and p.name != "Unknown"]
        faces = len(ev.people)
        unknown = max(0, total - len(known_names))
        parts = []
        if total > 0:
            head = f"{total} " + ("person" if total == 1 else "people")
            detail = []
            if known_names:
                detail.append("known: " + ", ".join(known_names))
            if unknown > 0:
                detail.append(f"{unknown} unknown")
            if detail:
                head += " (" + "; ".join(detail) + ")"
            parts.append(head)
        else:
            parts.append("no people detected")
        # extra bodies with no face (already folded into visitor_count) - note them
        # so the model knows some 'people' may be turned away / faceless.
        if ev.extra_unknown and ev.extra_unknown > 0 and faces < total:
            parts.append(f"{ev.extra_unknown} of them have no clearly visible face")
        objs = list(ev.carried_objects or [])
        if objs:
            parts.append("objects being carried: " + ", ".join(objs))
        return "; ".join(parts)

    def _apply_vlm_result(self, ev: VisitorEvent, result) -> dict:
        """Fold one VLM result onto the event and return the changed DB fields.

        Sets the EVENT-LEVEL scene_summary / appearance (the primary person's
        cautious line, back-compat) / ocr_text, AND distributes the per-person
        `people` descriptions to the UNKNOWN Person entries in order (clothing +
        carried -> Person.appearance, mood -> Person.expression). Returns a dict of
        changed event-level fields (including the re-serialised `people` when any
        person description changed) so the async path can persist just the delta.
        Used by BOTH the inline and the background-enrich VLM paths so they stay
        in lockstep. Fully defensive: missing keys degrade to "" / [].
        """
        if not result:
            return {}
        scene = (result.get("scene_summary", "") or "").strip()
        appearance = (result.get("appearance", "") or "").strip()
        ocr = ((result.get("ocr_text", "") or "").strip()
               if self.ocr_enabled else "")
        vlm_people = result.get("people") or []

        # Distribute per-person descriptions to EVERY person (known + unknown),
        # ordered left-to-right so they line up with the VLM's stated left-to-right
        # people[] ordering (Phase 16 - known people get a clothing/mood line too).
        #
        # ALIGNMENT SAFETY (Phase 16 fix): the VLM's people[] and our detected
        # faces can disagree in length/order - the VLM counts faceless bodies that
        # never appear in ev.people, and its "left to right" need not match the
        # face-box order. A blind zip() then filled face #1's clothing from the
        # VLM's description of a DIFFERENT person. We now attribute per-person ONLY
        # when the counts line up exactly; otherwise we keep the trustworthy
        # event-level scene/appearance below and leave per-person to face-only data
        # rather than risk a wrong-person description. Grounding the prompt with the
        # true count (see _vlm_facts) makes the exact-match case the common one.
        ordered_people = sorted(ev.people, key=lambda p: p.box[0])
        changed_people = False
        if vlm_people and len(vlm_people) == len(ordered_people):
            for p, d in zip(ordered_people, vlm_people):
                appear = (d.get("appearance", "") or "").strip()
                carrying = (d.get("carrying", "") or "").strip()
                expr = (d.get("expression", "") or "").strip()
                bits = []
                if appear:
                    bits.append(appear)
                if carrying:
                    bits.append(f"carrying {carrying}")
                new_appearance = ", ".join(bits)
                if new_appearance != p.appearance or expr != p.expression:
                    changed_people = True
                p.appearance = new_appearance
                p.expression = expr
        elif vlm_people and len(ordered_people) == 1:
            # One face but the VLM split the scene into several entries: attribute
            # the most prominent (first) description to our single person.
            d = vlm_people[0]
            p = ordered_people[0]
            appear = (d.get("appearance", "") or "").strip()
            carrying = (d.get("carrying", "") or "").strip()
            expr = (d.get("expression", "") or "").strip()
            bits = []
            if appear:
                bits.append(appear)
            if carrying:
                bits.append(f"carrying {carrying}")
            new_appearance = ", ".join(bits)
            if new_appearance != p.appearance or expr != p.expression:
                changed_people = True
            p.appearance = new_appearance
            p.expression = expr

        fields: dict = {}
        if scene:
            ev.scene_summary = scene
            fields["scene_summary"] = scene
        if appearance:
            ev.appearance = appearance
            fields["appearance"] = appearance
        if ocr:
            ev.ocr_text = ocr
            fields["ocr_text"] = ocr
        if changed_people:
            fields["people"] = json.dumps(people_to_dicts(ev.people))
        return fields

    # ------------------------------------------------------------------
    def _enrich_async(self, ev: VisitorEvent, frame_bgr) -> None:
        """Run the cloud VLM in a daemon thread and fold its result into `ev`.

        Only reached for UNKNOWN visitors when vlm_async_enrich is on. Updates
        ev.appearance / ev.scene_summary / ev.ocr_text, re-runs intent (the OCR /
        scene text may reveal a courier), recomposes the announcement TEXT, persists
        the changes to the SAME event id, and pushes an `event_update` to the
        browser if a broadcast callback was wired. When vlm_enrich_speak is on (and
        the mode wants audio), it also SPEAKS the newly-added appearance + scene as
        a short follow-up utterance so a Blind user hears the full description - not
        just the fast first line. Fully fail-soft: any error is logged and the
        already-stored fast event stands.
        """
        event_id = ev.event_id

        facts = self._vlm_facts(ev)

        def worker():
            try:
                result = self.vlm.describe_and_read(frame_bgr, facts=facts)
            except Exception as e:                        # pragma: no cover
                print(f"[Pipeline] Async VLM enrich failed for {event_id}: {e}")
                return
            if not result:
                return
            # Phase 15: fold scene/appearance/ocr onto the event AND distribute the
            # per-person descriptions to the UNKNOWN people. Returns the changed
            # EVENT-LEVEL fields (incl. the re-serialised people list) for the DB.
            fields = self._apply_vlm_result(ev, result)
            if not fields:
                return

            # New OCR / scene text may now read like a courier -> re-infer intent.
            ev.intent, ev.confidence = infer_intent(ev, self._courier_keywords)
            fields["intent"] = ev.intent
            fields["confidence"] = ev.confidence
            # Recompose the richer sentence (screen + stored text always get it).
            ev.announcement_text = compose_announcement(ev)
            fields["announcement_text"] = ev.announcement_text

            try:
                self.db.update_event_fields(event_id, **fields)
            except Exception as e:                        # pragma: no cover
                print(f"[Pipeline] Enrich DB update failed for {event_id}: {e}")

            cb = self._enrich_broadcast
            if cb is not None:
                try:
                    cb({"type": "event_update", "event": ev.to_dict()})
                except Exception as e:                    # pragma: no cover
                    print(f"[Pipeline] Enrich broadcast failed for {event_id}: {e}")

            # Phase 12 (RICHNESS): speak the details the VLM just added so a Blind
            # user HEARS them. Deaf mode gets it on-screen only (the broadcast
            # above), never as audio.
            #   - vlm_enrich_speak_full=True (follow-up mode): speak the WHOLE
            #     recomposed announcement (who + appearance + scene) so the full
            #     description is heard in one utterance. The "who" opening was
            #     already spoken instantly, so it is heard twice - accepted so the
            #     complete details land together and read naturally.
            #   - False: speak ONLY the delta (appearance + scene), a "looking
            #     closer..." follow-up that never repeats the fast first line.
            if self.vlm_enrich_speak and self.access is not None:
                mode = getattr(self.access, "mode", "both")
                if mode in ("blind", "both"):
                    if self.vlm_enrich_speak_full:
                        to_speak = (ev.announcement_text or "").strip()
                    else:
                        delta_bits = []
                        appearance = (ev.appearance or "").strip()
                        scene = (ev.scene_summary or "").strip()
                        if appearance:
                            delta_bits.append(appearance if appearance.endswith(".")
                                              else appearance + ".")
                        if scene:
                            delta_bits.append(scene if scene.endswith(".")
                                              else scene + ".")
                        to_speak = " ".join(delta_bits).strip()
                    if to_speak:
                        try:
                            self.access.speak_text(to_speak)
                        except Exception as e:            # pragma: no cover
                            print(f"[Pipeline] Enrich speak failed "
                                  f"for {event_id}: {e}")

        threading.Thread(target=worker, daemon=True,
                         name=f"vlm-enrich-{event_id}").start()

    # ------------------------------------------------------------------
    @staticmethod
    def _largest_person_box(ev):
        """Largest YOLO 'person' box (x1,y1,x2,y2) for the Re-ID body crop, or None.

        Falls back to None (whole-frame crop) when no person was detected - e.g. a
        visitor seen only as a face, or object detection disabled.
        """
        best, best_area = None, 0
        for o in ev.detected_objects or []:
            if getattr(o, "label", "") != "person":
                continue
            x1, y1, x2, y2 = o.box
            area = max(0, x2 - x1) * max(0, y2 - y1)
            if area > best_area:
                best_area = area
                best = (int(x1), int(y1), int(x2), int(y2))
        return best

    # ------------------------------------------------------------------
    @staticmethod
    def _new_event_id() -> str:
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"evt_{ts}_{uuid.uuid4().hex[:6]}"

    def _save_snapshot(self, frame_bgr, ev: VisitorEvent) -> str:
        if frame_bgr is None or not _HAS_CV2:
            return ""
        path = os.path.join(self.history_dir, f"{ev.event_id}.jpg")
        try:
            cv2.imwrite(path, frame_bgr)
        except Exception as e:                            # pragma: no cover
            print(f"[Pipeline] Snapshot save failed: {e}")
            return ""
        return path
