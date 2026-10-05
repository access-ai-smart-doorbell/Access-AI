"""
The Visitor Event - the single data object that flows through the whole system.

This is the SPINE of AccessAI. Every AI module (present and future) WRITES into
this object; every output (voice, notification, history) READS from it.

Design rule for all 10 phases
-----------------------------
Adding a new AI capability = fill in a field that already exists here, and have
exactly one module responsible for it. You never restructure the pipeline to add
a feature. That is why this dataclass is defined in Phase 1 with fields reserved
for ALL later phases, even though Phase 1 leaves almost all of them at their
defaults.

Field -> phase that fills it:
    identity / face_box         -> Phase 2 (face recognition)
    visitor_count / objects     -> Phase 3 (object detection)
    carried_objects / intent    -> Phase 3 (context engine)
    announcement_text           -> Phase 4 (accessibility output)
    spoof_score / is_spoof      -> Phase 5 (anti-spoofing)
    scene_summary / ocr_text    -> Phase 6 (VLM + OCR)
    speech_transcript / language -> Phase 7 (speech recognition)
    translated_transcript       -> Phase 8 (translation)
    reid_id / reid_seen_count   -> Phase 9 (re-ID + auto-enroll)
    age / gender                -> Phase 12 (InsightFace genderage, approximate)
    appearance                  -> Phase 12 (VLM clothing/uniform, unknown only)
"""

from dataclasses import dataclass, field, asdict
from typing import List, Optional


@dataclass
class Identity:
    """Who the visitor is (filled by face recognition in Phase 2)."""
    known: bool = False
    name: str = "Unknown"
    confidence: float = 0.0


@dataclass
class Person:
    """ONE detected person at the door (Phase 15, multi-person).

    A doorbell frame can contain several people; the pipeline builds one Person
    per detected face and appends any face-less YOLO bodies as a count
    (extra_unknown on the event). KNOWN people are named only; UNKNOWN people
    carry a cautious, approximate description (age/gender from InsightFace,
    clothing/carried/expression from the VLM). `identity` on the event stays the
    PRIMARY person (first known, else largest) for full backward compatibility.
    """
    known: bool = False
    name: str = "Unknown"
    confidence: float = 0.0
    age: Optional[int] = None          # approximate InsightFace age, spoken as RANGE
    gender: str = ""                   # "man" | "woman" | "" (mapped from M/F)
    box: tuple = (0, 0, 0, 0)          # (x1, y1, x2, y2) face box
    is_spoof: bool = False             # this face looked like a photo/screen
    spoof_score: float = 1.0           # 1.0 = real, 0.0 = certain spoof
    appearance: str = ""               # VLM combined appearance line (legacy compat)
    expression: str = ""               # cautious mood cue, e.g. "calm"
    # New spatial/VLM fields (Phase 17+) — populated by _apply_vlm_result
    position: str = ""                 # "left" | "center" | "right" | "directly in front"
    clothing: str = ""                 # clothing type + colour, e.g. "blue T-shirt, jeans"
    action: str = ""                   # what person is doing, e.g. "walking toward camera"
    carrying: str = ""                 # held objects, e.g. "backpack, phone"
    hairstyle: str = ""                # e.g. "fade with black hair", "curly brown hair"
    hands: str = ""                    # e.g. "empty at sides", "holding phone", "in pockets"
    build: str = ""                    # e.g. "slim", "average build", "tall"
    age_group: str = ""                # e.g. "young adult", "child", "elderly"
    footwear: str = ""                 # e.g. "white sneakers", "sandals"
    accessories: str = ""             # e.g. "watch", "glasses", "cap"


@dataclass
class DetectedObject:
    """A single object detected in the scene (filled in Phase 3)."""
    label: str
    confidence: float
    box: tuple = (0, 0, 0, 0)          # (x1, y1, x2, y2) in pixel coords


@dataclass
class VisitorEvent:
    # --- Identity of the event ---
    event_id: str
    timestamp: str
    trigger: str = "manual"            # "manual" | "doorbell" | "motion" | "wakeword"

    # --- Face pipeline (Phase 2 / Phase 5) ---
    identity: Identity = field(default_factory=Identity)
    face_box: tuple = (0, 0, 0, 0)
    spoof_score: float = 1.0           # 1.0 = real, 0.0 = certain spoof
    is_spoof: bool = False

    # --- Multi-person (Phase 15) ---
    # EVERY detected person, not just the largest. `identity` above still mirrors
    # the PRIMARY person (first known, else largest) so all Phase 2-14 code keeps
    # working. `extra_unknown` counts humans YOLO saw but whose face was not
    # detected (turned away / partially visible) - they are announced as "N other
    # people". Both default empty/0 so single-person events are unchanged.
    people: List["Person"] = field(default_factory=list)
    extra_unknown: int = 0

    # --- Vision pipeline (Phase 3 / Phase 6) ---
    visitor_count: int = 0
    detected_objects: List[DetectedObject] = field(default_factory=list)
    carried_objects: List[str] = field(default_factory=list)  # human-readable
    scene_summary: str = ""            # from VLM (Phase 6)
    hazards: str = "none"
    ocr_text: str = ""                 # raw OCR from parcels (Phase 6)

    # --- Visitor description (Phase 12) ---
    # age/gender are ADDITIVE fields filled from InsightFace's genderage model for
    # the largest face (known OR unknown). InsightFace age is APPROXIMATE (+/- a
    # few years), so it is only ever presented as a RANGE ("in their thirties"),
    # never as an exact number, and NEVER for known people. appearance is the VLM's
    # clothing / uniform / carried-object line, filled for UNKNOWN visitors only.
    age: Optional[int] = None          # approximate age of the largest face
    gender: str = ""                   # "man" | "woman" | "" (mapped from M/F)
    appearance: str = ""               # VLM clothing/uniform description (unknown)

    # --- Speech pipeline (Phase 7 / Phase 8) ---
    speech_transcript: str = ""
    language_detected: str = ""        # e.g. "hi", "en"
    translated_transcript: str = ""

    # --- Re-ID + memory (Phase 9) ---
    reid_id: Optional[str] = None      # stable id for repeat unknowns
    reid_seen_count: int = 0

    # --- Context engine output (Phase 3+) ---
    intent: str = "unknown visitor"
    confidence: float = 0.0
    announcement_text: str = ""        # final sentence for TTS + UI

    # --- Status (Phase 20) ---
    status: str = "detected"           # "detected" (instant local) -> "analyzed" (VLM complete)

    # --- Storage ---
    snapshot_path: str = ""
    video_path: str = ""               # filename of the recorded MP4 clip (Phase 19)

    def to_dict(self) -> dict:
        """Plain-dict view for JSON / DB. Nested dataclasses become dicts.
        Includes the derived `alert_kind` so every consumer (dashboard, PWA,
        Flutter, webhooks) keys channel treatment off one field."""
        d = asdict(self)
        d["alert_kind"] = alert_kind(self)
        d["status"] = getattr(self, "status", "detected") or ("analyzed" if self.scene_summary else "detected")
        d["person_count"] = self.visitor_count
        d["identity_status"] = "known" if (self.identity and self.identity.known) else "unknown"
        d["scene_description"] = self.scene_summary
        d["snapshot"] = f"/snapshot/{self.event_id}.jpg"
        return d


# Alert taxonomy: every event classifies into exactly one kind, most-urgent
# first. Clients key channel treatments off this single string - haptic
# patterns, earcons, notification urgency, smart-bulb colours - so blind, deaf,
# and deafblind users can tell WHO rang without reading or hearing a sentence.
ALERT_KINDS = ("spoof", "known", "delivery", "unknown")


def alert_kind(ev) -> str:
    """Classify a VisitorEvent (or its dict form) into one ALERT_KINDS bucket.

    Pure + tolerant of both live dataclasses and DB-rebuilt dicts, mirroring
    people_to_dicts(). Priority: spoof beats everything (it is a warning, not
    a visit); a recognised person beats intent; a delivery beats a generic
    unknown."""
    get = (lambda k, d=None: ev.get(k, d)) if isinstance(ev, dict) else \
        (lambda k, d=None: getattr(ev, k, d))
    if bool(get("is_spoof", False)):
        return "spoof"
    ident = get("identity", None)
    known = ident.get("known") if isinstance(ident, dict) else \
        getattr(ident, "known", False)
    people = get("people", None) or []
    any_known = bool(known) or any(
        (p.get("known") if isinstance(p, dict) else getattr(p, "known", False))
        for p in people)
    if any_known:
        return "known"
    if "delivery" in str(get("intent", "") or ""):
        return "delivery"
    return "unknown"


def people_to_dicts(people) -> list:
    """Serialise a list of Person (or already-dicts) to plain dicts for JSON/DB.

    Tolerant of both Person dataclasses (live pipeline) and plain dicts (rows
    rebuilt from the DB), so the same helper works on either side. Boxes stay as
    lists so json.dumps is happy.
    """
    out = []
    for p in people or []:
        if isinstance(p, Person):
            d = asdict(p)
        elif isinstance(p, dict):
            d = dict(p)
        else:                                             # pragma: no cover
            continue
        box = d.get("box")
        if isinstance(box, tuple):
            d["box"] = list(box)
        out.append(d)
    return out
