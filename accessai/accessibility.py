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

    Priority: position → identity → age/gender/build → hairstyle →
              appearance (facial hair, glasses) → clothing → accessories →
              hands → carrying → footwear → action → mood.

    Works with both VLM-enriched data (clothing, hairstyle, hands, etc.)
    and pure InsightFace data (age, gender, box) for YOLO-only fallback.
    Returns a complete sentence.
    """
    known      = getattr(p, "known", False)
    name       = getattr(p, "name", "") or ""
    age_grp    = (getattr(p, "age_group", "") or "").strip().lower()
    age_bnd    = _age_band(getattr(p, "age", None))
    age        = age_grp or age_bnd
    gender     = (getattr(p, "gender", "") or "").strip().lower()
    noun       = gender if gender in ("man", "woman") else "person"
    build      = (getattr(p, "build", "") or "").strip()

    # Spatial position — prefer VLM field, fall back to box calculation.
    position   = (getattr(p, "position", "") or "").strip().lower()
    if not position:
        box = getattr(p, "box", None)
        if box:
            position = _position_from_box(box)

    # VLM-populated fields
    clothing    = (getattr(p, "clothing",    "") or "").strip().rstrip(".")
    hairstyle   = (getattr(p, "hairstyle",   "") or "").strip().rstrip(".")
    appearance  = (getattr(p, "appearance",  "") or "").strip().rstrip(".")
    hands       = (getattr(p, "hands",       "") or "").strip().rstrip(".")
    accessories = (getattr(p, "accessories", "") or "").strip().rstrip(".")
    footwear    = (getattr(p, "footwear",    "") or "").strip().rstrip(".")
    action      = (getattr(p, "action",      "") or "").strip().rstrip(".")
    carrying    = (getattr(p, "carrying",    "") or "").strip().rstrip(".")
    expression  = (getattr(p, "expression",  "") or "").strip().rstrip(".")
    is_spoof    = getattr(p, "is_spoof", False)

    # ── Subject ──────────────────────────────────────────────────────────
    if known and name and name != "Unknown":
        subject = name
    else:
        # Build unknown descriptor: age-band/group + gender noun
        if age in ("child", "teenager"):
            desc = age
        elif age in ("young adult", "middle-aged", "elderly"):
            desc = f"{age} {noun}".strip() if noun != "person" else age
        elif age:
            desc = f"{noun} ({age})" if noun != "person" else f"person {age}"
        else:
            desc = noun if noun != "person" else "visitor"
        subject = f"an unknown {desc}"

    # ── Position clause ───────────────────────────────────────────────────
    pos_clause = ""
    if position and position not in ("center", "directly in front", "front"):
        pos_clause = f"on your {position}"
    elif position in ("center", "directly in front", "front"):
        pos_clause = "directly in front of you"

    # ── Detail clauses ────────────────────────────────────────────────────
    details = []

    # Build / notable physique
    if build and build.lower() not in ("average", "average build"):
        details.append(f"{build}")

    # Clothing (most useful visual identifier)
    if clothing:
        if clothing.lower().startswith("wearing "):
            details.append(clothing)
        else:
            details.append(f"wearing {clothing}")
    elif appearance and not known:
        details.append(appearance)

    # Hairstyle
    if hairstyle:
        hs = hairstyle.strip()
        if hs.lower().startswith("with "):
            details.append(hs)
        elif hs.lower().startswith(("fade", "undercut", "buzz cut", "crew cut", "ponytail", "bun")):
            details.append(f"with a {hs} haircut" if not (hs.lower().endswith("haircut") or hs.lower().endswith("hair")) else f"with a {hs}")
        elif not hs.lower().startswith(("a ", "short", "long", "curly", "straight", "black", "brown")):
            details.append(f"with a {hs}")
        else:
            details.append(f"with {hs}")

    # Facial features (facial hair, glasses, etc.)
    if appearance and clothing:
        parts_app = [pt.strip() for pt in appearance.split(",") if pt.strip()]
        new_app_parts = []
        for pt in parts_app:
            pt_low = pt.lower()
            if any(k in pt_low for k in ("wearing", "hair:", "carrying", "hands:")):
                continue
            if clothing and pt_low in clothing.lower():
                continue
            if hairstyle and pt_low in hairstyle.lower():
                continue
            new_app_parts.append(pt)
        if new_app_parts:
            app_str = ", ".join(new_app_parts)
            if app_str.lower().startswith(("with ", "has ")):
                details.append(app_str)
            else:
                details.append(f"with {app_str}")

    # Accessories
    if accessories:
        acc = accessories.strip()
        if acc.lower().startswith("wearing "):
            details.append(acc)
        else:
            details.append(f"wearing {acc}")

    # Hands
    if hands:
        h = hands.strip()
        if h.lower() in ("empty", "empty at sides", "empty at their sides", "empty at his sides", "empty at her sides"):
            details.append("hands empty at sides")
        elif h.lower().startswith(("holding", "touching", "gesturing", "in pockets", "folded")):
            details.append(f"hands {h}")
        else:
            details.append(f"hands are {h}")

    # Carried objects
    if carrying and (not hands or carrying.lower() not in hands.lower()):
        details.append(f"carrying {carrying}")

    # Footwear
    if footwear:
        fw = footwear.strip()
        if fw.lower().startswith("wearing "):
            details.append(fw)
        else:
            details.append(f"wearing {fw}")

    # Action / movement
    if action and action.lower() not in ("standing", "standing still", ""):
        details.append(action)

    # Mood / expression
    if expression:
        exp = expression.strip()
        if exp.lower().startswith("appears "):
            details.append(exp)
        elif exp.lower().startswith("appears to be "):
            details.append(exp)
        else:
            details.append(f"appears {exp}")

    # Spoof warning — always appended, never softened
    if is_spoof:
        details.append("⚠ shown as a photo — possible spoof")

    # ── Assemble sentence ─────────────────────────────────────────────────
    parts = [subject]
    if pos_clause:
        parts[0] += f" is {pos_clause}"
    else:
        parts[0] += " is at the door"

    if details:
        parts[0] += ", " + ", ".join(details)

    sentence = parts[0].rstrip(",") + "."
    if sentence and sentence[0].islower():
        sentence = sentence[0].upper() + sentence[1:]
    return sentence


def _describe_unknown_person(p) -> str:
    """Compact description of one unknown person: age band + gender."""
    gender = (getattr(p, "gender", "") or "").strip().lower()
    noun = gender if gender in ("man", "woman") else ""
    age = _age_band(getattr(p, "age", None))

    if age in ("child", "teenager"):
        base = age
    elif age in ("middle-aged", "elderly"):
        base = f"{age} {noun}".strip() if noun else f"{age} person"
    elif age:
        base = f"{noun} {age}" if noun else f"person {age}"
    else:
        base = noun or "visitor"

    return f"an unknown {base}"


def _known_detail_sentence(p) -> str:
    """Full detail sentence for a single known person (used in single-person path)."""
    return _describe_person_rich(p)


# ---------------------------------------------------------------------------
# Multi-person builder
# ---------------------------------------------------------------------------

def _multi_who(ev, compact=False) -> list:
    """Build the WHO sentences for a scene with MORE THAN ONE subject."""
    if getattr(ev, "is_spoof", False):
        return ["Warning. The faces shown to the camera appear to be photos."]

    people    = list(getattr(ev, "people", []) or [])
    extra     = int(getattr(ev, "extra_unknown", 0) or 0)
    total     = max(len(people) + extra, getattr(ev, "visitor_count", 0))

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

    names = [p.name for p in known_unique]
    unknown_count = total - len(known_unique)
    sentences = []

    if compact:
        if names:
            verb = "is" if len(names) == 1 else "are"
            s = f"{_join_list(names)} {verb} at the door"
            if unknown_count > 0:
                s += f" with {unknown_count} other {_plural_person(unknown_count)}"
            sentences.append(s + ".")
        else:
            sentences.append(f"{total} people are at the door.")
        spoofed = sum(1 for p in unknown_people if getattr(p, "is_spoof", False))
        if spoofed == 1:
            sentences.append("Warning. One face shown to the camera appears to be a photo, a possible spoof.")
        elif spoofed > 1:
            sentences.append(f"Warning. {spoofed} of the faces shown to the camera appear to be photos, possible spoofs.")
        return sentences

    if names:
        verb = "is" if len(names) == 1 else "are"
        s = f"{_join_list(names)} {verb} at the door"
        described = [_describe_unknown_person(p) for p in unknown_people[:3]]
        others = (len(unknown_people) - len(described)) + extra

        def _others_clause():
            return f"{others} other {_plural_person(others)}"

        tail = _join_list(described)
        if others:
            tail = f"{tail}, and {_others_clause()}" if tail else _others_clause()
        if tail:
            s += ", along with " + tail
        s += "."
        sentences.append(s)
    else:
        described = [_describe_unknown_person(p) for p in unknown_people[:3]]
        others = (len(unknown_people) - len(described)) + extra
        if described:
            verb = "is" if (len(described) == 1 and not others) else "are"
            s = f"There {verb} " + _join_list(described)
            if others:
                s += f", and {others} other {_plural_person(others)}"
            sentences.append(s + " at the door.")
        elif others:
            sentences.append(f"{others} {_plural_person(others)} are at the door.")
        else:
            sentences.append(f"{total} people are at the door.")

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
      3. Number of people + known/unknown count + full rich person description
      4. VLM scene summary (environment/background)
      5. OCR text (signage, parcels)
      6. What the visitor said (speech)
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

    # ── 2 & 3. Person count + who + rich description ─────────────────────
    scene = _clean_scene(getattr(ev, "scene_summary", ""))
    compact = bool(scene)

    if getattr(ev, "is_spoof", False):
        parts.append("Warning. A face was shown to the camera but appears to be a photo.")

    elif multi:
        who_sentences = _multi_who(ev, compact=compact)
        parts.extend(who_sentences)

    else:
        # Single-subject path
        single = list(getattr(ev, "people", []) or [])
        scene_describes_person = bool(
            scene and len(scene) > 40
            and any(w in scene.lower() for w in ("wearing", "jersey", "shirt", "jacket", "top", "dress", "kurta", "suit", "hair", "fade", "standing", "sitting", "walking"))
        )

        if known:
            if single:
                p0 = single[0]
                # If scene already describes the person in detail, avoid duplicating the full clothing/hair sentence
                if scene_describes_person:
                    pos = getattr(p0, "position", "").strip().lower()
                    pos_clause = f" on your {pos}" if pos and pos not in ("center", "directly in front", "front") else (" directly in front of you" if pos in ("center", "directly in front", "front") else "")
                    parts.append(f"{ev.identity.name} is{pos_clause or ' at the door'}.")
                else:
                    detail = _describe_person_rich(p0)
                    parts.append(detail if detail else f"{ev.identity.name} is at the door.")
            else:
                parts.append(f"{ev.identity.name} is at the door.")
            if single and getattr(single[0], "is_spoof", False):
                parts.append("Warning. A face was shown to the camera but appears to be a photo.")
        elif getattr(ev, "reid_seen_count", 0) >= 2:
            desc = _person_desc_from_ev(ev)
            subject = f"unknown {desc}" if desc else "unknown visitor"
            parts.append(f"The same {subject} has come {ev.reid_seen_count} times today.")
            if single and not scene_describes_person:
                detail = _describe_person_rich(single[0])
                if detail:
                    parts.append(detail)
        elif ev.visitor_count > 1:
            parts.append(f"{ev.visitor_count} unknown visitors are at the door.")
        elif ev.visitor_count == 1:
            if single:
                if scene_describes_person:
                    desc = _person_desc_from_ev(ev)
                    subj = f"An unknown {desc}" if desc else "An unknown visitor"
                    parts.append(f"{subj} is at the door.")
                else:
                    detail = _describe_person_rich(single[0])
                    if detail:
                        parts.append(detail)
                    else:
                        desc = _person_desc_from_ev(ev)
                        subj = f"An unknown {desc}" if desc else "An unknown visitor"
                        parts.append(f"{subj} is at the door.")
            else:
                desc = _person_desc_from_ev(ev)
                subj = f"An unknown {desc}" if desc else "An unknown visitor"
                parts.append(f"{subj} is at the door.")
        else:
            parts.append("The doorbell rang but no one is clearly visible.")

    # ── 4. VLM scene / environment ────────────────────────────────────────
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

    def __init__(self, tts, mode: str = "both", user_lang: str = "en"):
        self.tts  = tts
        self.mode = mode if mode in _VALID_MODES else "both"
        # The user's chosen language (ISO code). When non-English, deliver()
        # passes it to TTS so the announcement is spoken with a matching
        # neural voice (e.g. Hindi → hi-IN-SwaraNeural) instead of reading
        # non-English text with Kokoro's English phonemes.
        self.user_lang = (user_lang or "en").strip().lower()

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
            # compose_announcement produces English text; pipeline handles
            # translation for non-English target languages.
            self.tts.speak(text, earcon=alert_kind(ev), lang="en")
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
        hint = lang or self.user_lang or "en"
        return self.tts.speak(text, lang=hint)

