"""
AccessibilityEngine - the OUTPUT layer.

Composes the final announcement from a VisitorEvent and routes it to
the right channel (blind → TTS, deaf → visual, both → both).

Priority order for output (matches user requirements):
  1. Immediate hazards / obstacles
  2. Objects directly in path
  3. Number of people (count + known/unknown breakdown)
  4. Position and distance of each person
  5. Known / unknown identity
  6. Person's action and movement
  7. Important objects being held / used
  8. Clothing and appearance (hair, accessories, footwear)
  9. Background / environment
  10. Minor visual details

When the VLM is available (WiFi up), all fields come from the structured
JSON response.  When VLM fails (college WiFi blocking the API), this
module builds the richest possible sentence from InsightFace age/gender
and YOLO face-box positions alone so the user ALWAYS gets useful output.
"""

_VALID_MODES = ("blind", "deaf", "both")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_scene(scene) -> str:
    """Return the VLM scene sentence, or '' when it contains raw JSON."""
    s = (scene or "").strip()
    if not s:
        return ""
    if (s.startswith("{") or s.startswith("```")
            or '"people"' in s or '"appearance"' in s or '"scene"' in s):
        return ""
    return s


def _age_band(age) -> str:
    """Map approximate InsightFace age to a cautious spoken band."""
    if age is None:
        return ""
    try:
        a = int(age)
    except Exception:
        return ""
    if a < 13:   return "child"
    if a < 20:   return "teenager"
    if a < 30:   return "young adult"
    if a < 40:   return "in their thirties"
    if a < 50:   return "in their forties"
    if a < 65:   return "middle-aged"
    return "elderly"


def _age_descriptor(age) -> str:
    """Alias kept for backward compat with any direct callers."""
    return _age_band(age)


def _position_from_box(box, frame_width=1280) -> str:
    """Derive a spatial label (left / center / right) from a face bounding box.

    box is (x1, y1, x2, y2) or (x1, y1, w, h) — both work because we only
    use x1 to determine which third of the frame the face is in.
    Falls back to '' when the box is invalid.
    """
    try:
        x1 = float(box[0])
        x2 = float(box[2])
        x_center = (x1 + x2) / 2.0
        third = frame_width / 3.0
        if x_center < third:
            return "left"
        if x_center < 2 * third:
            return "center"
        return "right"
    except Exception:
        return ""


def _join_list(items) -> str:
    """Join phrases naturally: 'a', 'a and b', 'a, b, and c'."""
    items = [i for i in items if i]
    if not items:      return ""
    if len(items) == 1: return items[0]
    if len(items) == 2: return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _plural_person(n: int) -> str:
    return "person" if n == 1 else "people"


# ---------------------------------------------------------------------------
# Per-person rich description
# ---------------------------------------------------------------------------

def _describe_person_rich(p, index: int = 0, total: int = 1) -> str:
    """Build the richest possible description for ONE Person object.

    Priority: position → identity → age/gender → action →
              clothing/appearance → accessories → carrying → mood.

    Works with both VLM-enriched data (clothing, action, position fields)
    and pure InsightFace data (age, gender, box) for YOLO-only fallback.
    Returns a complete sentence.
    """
    known     = getattr(p, "known", False)
    name      = getattr(p, "name", "") or ""
    age       = _age_band(getattr(p, "age", None))
    gender    = (getattr(p, "gender", "") or "").strip().lower()
    noun      = gender if gender in ("man", "woman") else "person"

    # Spatial position — prefer VLM field, fall back to box calculation.
    position  = (getattr(p, "position", "") or "").strip().lower()
    if not position:
        box = getattr(p, "box", None)
        if box:
            position = _position_from_box(box)

    # VLM-populated fields
    clothing  = (getattr(p, "clothing",   "") or "").strip().rstrip(".")
    appearance= (getattr(p, "appearance", "") or "").strip().rstrip(".")
    action    = (getattr(p, "action",     "") or "").strip().rstrip(".")
    carrying  = (getattr(p, "carrying",   "") or "").strip().rstrip(".")
    expression= (getattr(p, "expression", "") or "").strip().rstrip(".")
    is_spoof  = getattr(p, "is_spoof", False)

    # ── Subject ──────────────────────────────────────────────────────────
    if known and name and name != "Unknown":
        subject = name
    else:
        # Build unknown descriptor: age-band + gender noun
        if age in ("child", "teenager"):
            desc = age
        elif age in ("middle-aged", "elderly"):
            desc = f"{age} {noun}".strip()
        elif age:
            desc = f"{noun} ({age})" if noun != "person" else f"person {age}"
        else:
            desc = noun
        subject = f"an unknown {desc}"

    # ── Position clause ───────────────────────────────────────────────────
    pos_clause = ""
    if position and position != "center":
        pos_clause = f"on your {position}"
    elif position == "center":
        pos_clause = "directly in front of you"

    # ── Detail clauses ────────────────────────────────────────────────────
    details = []
    # Clothing (most useful for ID)
    if clothing:
        details.append(f"wearing {clothing}")
    elif appearance and not known:
        # Use VLM appearance as fallback clothing description
        details.append(appearance)
    # Action / movement
    if action and action.lower() not in ("standing",):
        details.append(action)
    # Carried objects
    if carrying:
        details.append(f"carrying {carrying}")
    # Mood
    if expression:
        details.append(f"appears {expression}")
    # Spoof warning — always appended, never softened
    if is_spoof:
        details.append("⚠ shown as a photo — possible spoof")

    # ── Assemble sentence ─────────────────────────────────────────────────
    # "Vinay is on your left, wearing a blue shirt, carrying a phone."
    # "An unknown young man is on your right, wearing a white T-shirt."
    parts = [subject]
    if pos_clause:
        parts[0] += f" is {pos_clause}"
    else:
        parts[0] += " is at the door"

    if details:
        parts[0] += ", " + ", ".join(details)

    return parts[0].rstrip(",") + "."


def _describe_unknown_person(p) -> str:
    """Compact inline description for use inside multi-person roster."""
    return _describe_person_rich(p)


def _known_detail_sentence(p) -> str:
    """Full detail sentence for a single known person (used in single-person path)."""
    return _describe_person_rich(p)


# ---------------------------------------------------------------------------
# Multi-person builder
# ---------------------------------------------------------------------------

def _multi_who(ev, compact=False) -> list:
    """Build the WHO sentences for a scene with MORE THAN ONE subject.

    Output format (user requirement):
      "There are 3 people at the door. 2 are known: Vinay and Suhaib.
       1 unknown person on your right — a young man in a blue T-shirt."

    `compact=True` is ignored — we always produce the full count + breakdown
    because it matches the user's explicit priority-order spec.
    """
    if getattr(ev, "is_spoof", False):
        return ["Warning. The faces shown to the camera appear to be photos."]

    people    = list(getattr(ev, "people", []) or [])
    extra     = int(getattr(ev, "extra_unknown", 0) or 0)
    total     = len(people) + extra

    known_people   = [p for p in people
                      if getattr(p, "known", False)
                      and not getattr(p, "is_spoof", False)]
    unknown_people = [p for p in people
                      if not getattr(p, "known", False)
                      or getattr(p, "is_spoof", False)]

    # Deduplicate known by name
    seen, known_unique = set(), []
    for p in known_people:
        if p.name and p.name not in seen:
            seen.add(p.name)
            known_unique.append(p)

    known_count   = len(known_unique)
    unknown_count = len(unknown_people) + extra

    sentences = []

    # ── Line 1: count summary ────────────────────────────────────────────
    summary = f"There {'is' if total == 1 else 'are'} {total} {_plural_person(total)} at the door."
    if known_count and unknown_count:
        known_names = _join_list([p.name for p in known_unique])
        summary += (f" {known_count} known ({known_names}),"
                    f" {unknown_count} unknown.")
    elif known_count:
        known_names = _join_list([p.name for p in known_unique])
        summary += f" All known: {known_names}."
    elif unknown_count:
        summary += f" All unknown."
    sentences.append(summary)

    # ── Line 2+: per-person rich detail ──────────────────────────────────
    # Sort left-to-right by face box x position
    all_people = sorted(people, key=lambda p: (getattr(p, "box", (0,)) or (0,))[0])
    CAP = 4   # max spoken descriptions to keep audio short
    for i, p in enumerate(all_people[:CAP]):
        sentence = _describe_person_rich(p, index=i, total=total)
        sentences.append(sentence)

    if extra > 0:
        sentences.append(
            f"{extra} additional {'person' if extra == 1 else 'people'} "
            f"{'is' if extra == 1 else 'are'} present but not clearly visible."
        )

    # Spoof warnings
    spoofed = sum(1 for p in unknown_people if getattr(p, "is_spoof", False))
    if spoofed == 1:
        sentences.append("Warning. One face appears to be a photo — possible spoof.")
    elif spoofed > 1:
        sentences.append(f"Warning. {spoofed} faces appear to be photos — possible spoofs.")

    return sentences


# ---------------------------------------------------------------------------
# Main composer
# ---------------------------------------------------------------------------

def compose_announcement(ev) -> str:
    """Turn a VisitorEvent into a structured, priority-ordered announcement.

    Priority:
      1. Hazards/obstacles (VLM hazards field or YOLO)
      2. Objects in path
      3. Number of people + known/unknown count
      4. Per-person: position → identity → action → clothing → carrying
      5. VLM scene summary (environment/background)
      6. OCR text (signage, parcels)
      7. What the visitor said (speech)
    """
    known  = ev.identity.known
    people = list(getattr(ev, "people", []) or [])
    extra  = int(getattr(ev, "extra_unknown", 0) or 0)
    multi  = (len(people) + extra) > 1

    parts = []

    # ── 1. Hazards ────────────────────────────────────────────────────────
    hazards = _clean_scene(getattr(ev, "hazards", "") or "")
    if hazards and hazards.lower() not in ("none", "no hazards", ""):
        h = hazards.rstrip(".")
        parts.append(f"Warning: {h}.")

    # ── 2 & 3. Person count + who ─────────────────────────────────────────
    if getattr(ev, "is_spoof", False):
        parts.append("Warning. A face was shown to the camera but appears to be a photo.")

    elif multi:
        who_sentences = _multi_who(ev)
        parts.extend(who_sentences)

    else:
        # Single-subject path
        if known:
            parts.append(f"{ev.identity.name} is at the door.")
            # Rich detail sentence
            single = list(getattr(ev, "people", []) or [])
            if single:
                detail = _describe_person_rich(single[0])
                # Avoid duplicating "is at the door" if no extra detail
                if detail and detail != f"{ev.identity.name} is at the door.":
                    parts.append(detail)
        elif getattr(ev, "reid_seen_count", 0) >= 2:
            desc = _person_desc_from_ev(ev)
            subject = f"unknown {desc}" if desc else "unknown visitor"
            parts.append(f"The same {subject} has come {ev.reid_seen_count} times today.")
            single = list(getattr(ev, "people", []) or [])
            if single:
                parts.append(_describe_person_rich(single[0]))
        elif ev.visitor_count >= 1:
            single = list(getattr(ev, "people", []) or [])
            if single:
                parts.append(_describe_person_rich(single[0]))
            else:
                desc = _person_desc_from_ev(ev)
                if desc:
                    parts.append(f"An unknown {desc} is at the door.")
                else:
                    parts.append("An unknown visitor is at the door.")
        else:
            parts.append("The doorbell rang but no one is clearly visible.")

    # ── 4. VLM scene / environment ────────────────────────────────────────
    scene = _clean_scene(getattr(ev, "scene_summary", ""))
    if scene:
        parts.append(scene if scene.endswith(".") else scene + ".")

    # ── 5. Carried objects (YOLO) ─────────────────────────────────────────
    if ev.carried_objects and not multi:
        # In multi-person mode, carrying is already per-person
        parts.append(f"Carrying {', '.join(ev.carried_objects)}.")

    # ── 6. Delivery / OCR ────────────────────────────────────────────────
    if ev.intent == "likely delivery":
        ocr = (ev.ocr_text or "").strip()
        if ocr:
            parts.append(f"Likely a delivery. Label reads: {ocr[:80]}.")
        else:
            parts.append("Likely a delivery.")

    # ── 7. What the visitor said ──────────────────────────────────────────
    said = (getattr(ev, "translated_transcript", "")
            or getattr(ev, "speech_transcript", "") or "").strip()
    if said:
        parts.append(f'They said: "{said}".')

    return " ".join(parts) if parts else "Someone is at the door."


def _person_desc_from_ev(ev) -> str:
    """Cautious age+gender string from the event-level fields (single-person)."""
    gender = (getattr(ev, "gender", "") or "").strip().lower()
    noun   = gender if gender in ("man", "woman") else ""
    age    = _age_band(getattr(ev, "age", None))
    if age in ("child", "teenager"):
        return age
    if age in ("middle-aged", "elderly"):
        return f"{age} {noun}".strip() if noun else f"{age} person"
    if age:
        return f"{noun} {age}" if noun else f"visitor {age}"
    return noun


def _person_desc(ev) -> str:
    """Alias for backward compat."""
    return _person_desc_from_ev(ev)


def _unknown_who(ev) -> str:
    desc = _person_desc_from_ev(ev)
    if not desc:
        return "An unknown visitor is at the door."
    return f"An unknown {desc} is at the door."


# ---------------------------------------------------------------------------
# AccessibilityEngine class
# ---------------------------------------------------------------------------

class AccessibilityEngine:
    """Composes announcements and delivers them per accessibility mode."""

    def __init__(self, tts, mode: str = "both"):
        self.tts  = tts
        self.mode = mode if mode in _VALID_MODES else "both"

    def deliver(self, ev, speak: bool = True) -> str:
        """Compose the announcement and speak/display it.

        `speak=False` suppresses audio but still composes and stores the text
        (used during cooldown periods). Visual delivery (deaf/both) is handled
        by the server broadcasting the event over WebSocket.
        Returns the composed text.
        """
        text = compose_announcement(ev)
        ev.announcement_text = text
        if speak and self.mode in ("blind", "both"):
            from .visitor_event import alert_kind
            self.tts.speak(text, earcon=alert_kind(ev))
        return text

    def set_mode(self, mode: str) -> str:
        if mode not in _VALID_MODES:
            raise ValueError("mode must be blind|deaf|both")
        self.mode = mode
        return self.mode

    def speak_text(self, text: str, lang: str = "") -> bool:
        """Speak an arbitrary sentence (e.g. reply to a voice command).

        Always speaks regardless of mode — a reply is an explicit user action.
        `lang` hints the language so a translated sentence uses a matching voice.
        """
        return self.tts.speak(text, lang=lang)
