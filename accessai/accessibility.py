"""
AccessibilityEngine - the OUTPUT layer (Phase 4).

This is now the single place that:
  1. COMPOSES the final announcement sentence from a VisitorEvent, and
  2. ROUTES it to the right channel based on the accessibility mode:
       - "blind" -> speak it (TTS)
       - "deaf"  -> visual only (big text + flash + vibrate, done in the browser)
       - "both"  -> speak AND visual

It supersedes context_engine.compose_interim_announcement(), which now survives
only as the fallback used when no AccessibilityEngine is injected (e.g. unit
tests, or ENABLE_TTS wiring absent).

Language stays CONSERVATIVE across every phase: "likely", "appears to be",
never "definitely". Most branches below read fields that later phases fill
(spoof P5, reid P9, ocr/scene P6, speech P7/P8); they are inert until then, so
this function is written once and simply lights up as those phases land.
"""

_VALID_MODES = ("blind", "deaf", "both")


def _clean_scene(scene) -> str:
    """The VLM scene sentence, or "" when it isn't speakable prose.

    If the VLM reply was truncated/malformed, raw JSON can end up stored in
    scene_summary. Reading '{"people": [ {"appearance": ...' aloud to a blind
    user is worse than saying nothing, so anything that still looks like JSON
    is dropped here — the announcement then stands on the detector facts alone.
    """
    s = (scene or "").strip()
    if not s:
        return ""
    if s.startswith("{") or s.startswith("```") or '"people"' in s \
            or '"appearance"' in s or '"scene"' in s:
        return ""
    return s


def _age_descriptor(age) -> str:
    """Map an APPROXIMATE integer age to a cautious RANGE phrase (never exact).

    InsightFace age is only accurate to within several years, so we deliberately
    speak a band ("in their thirties") and never a number. Unknown age -> "".
    """
    if age is None:
        return ""
    try:
        a = int(age)
    except Exception:                                     # pragma: no cover
        return ""
    if a < 13:
        return "child"
    if a < 20:
        return "teenager"
    if a < 30:
        return "in their twenties"
    if a < 40:
        return "in their thirties"
    if a < 50:
        return "in their forties"
    if a < 65:
        return "middle-aged"
    return "elderly"


def _person_desc(ev) -> str:
    """Cautious 'age + gender' descriptor for an UNKNOWN visitor, e.g.
    'man in their thirties', 'woman in their twenties', 'teenager', 'child',
    'elderly woman'. Empty string when neither age nor gender is known.

    Both age and gender are approximate InsightFace estimates, so the wording
    stays cautious. Returned WITHOUT any leading article or 'unknown' qualifier so
    it can be slotted into several sentence shapes ('An unknown ...', 'The same
    unknown ...').
    """
    gender = (getattr(ev, "gender", "") or "").strip().lower()
    noun = gender if gender in ("man", "woman") else ""
    age = _age_descriptor(getattr(ev, "age", None))

    if age in ("child", "teenager"):
        return age                                        # gender-neutral, cautious
    if age in ("middle-aged", "elderly"):
        return f"{age} {noun}".strip() if noun else f"{age} person"
    if age:                                               # "in their thirties" style
        return f"{noun} {age}" if noun else f"visitor {age}"
    return noun                                           # "man" / "woman" / ""


def _unknown_who(ev) -> str:
    """Subject line for a SINGLE unknown visitor, folding in approximate age +
    gender (e.g. 'An unknown man in their thirties is at the door.'). Falls back to
    'An unknown visitor is at the door.' when neither age nor gender is available.
    """
    desc = _person_desc(ev)
    if not desc:
        return "An unknown visitor is at the door."
    # The article always precedes "unknown" (a vowel), so it is always "An".
    return f"An unknown {desc} is at the door."


# ----------------------------------------------------------------------------
# Multi-person announcement (Phase 15).
#
# Once MORE THAN ONE subject is present (several faces, or faces plus face-less
# people counted by YOLO), compose_announcement switches to _multi_who below.
# Single-subject scenes still take the original single-person path verbatim, so
# every earlier phase's wording is a byte-for-byte non-regression.
# ----------------------------------------------------------------------------

def _join_list(items) -> str:
    """Join phrases naturally: 'a', 'a and b', 'a, b, and c'."""
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _plural_person(n: int) -> str:
    return "person" if n == 1 else "people"


def _describe_unknown_person(p) -> str:
    """Cautious description of ONE unknown Person object (not an event): an
    approximate age band + gender, then clothing / carried objects and apparent
    mood when the VLM supplied them, and a spoof caution when the face was a photo.

    Everything stays hedged ('appears', 'unknown') and never invents detail: an
    empty field simply drops its clause. Reads Person fields age/gender/appearance/
    expression/is_spoof (gender already mapped to 'man'/'woman' in the pipeline).
    """
    gender = (getattr(p, "gender", "") or "").strip().lower()
    noun = gender if gender in ("man", "woman") else ""
    age = _age_descriptor(getattr(p, "age", None))

    if age in ("child", "teenager"):
        base = age                                        # gender-neutral, cautious
    elif age in ("middle-aged", "elderly"):
        base = f"{age} {noun}".strip() if noun else f"{age} person"
    elif age:                                             # "in their thirties" style
        base = f"{noun} {age}" if noun else f"person {age}"
    else:
        base = noun or "visitor"

    parts = [base]
    appearance = (getattr(p, "appearance", "") or "").strip().rstrip(".")
    if appearance:
        parts.append(appearance)
    expression = (getattr(p, "expression", "") or "").strip().rstrip(".")
    if expression:
        parts.append(f"appears {expression}")
    if getattr(p, "is_spoof", False):
        parts.append("shown to the camera as a photo, a possible spoof")
    return "an unknown " + ", ".join(parts)


def _known_detail_sentence(p) -> str:
    """A clean, separate follow-up sentence describing ONE known person's cautious
    VLM clothing / mood line, e.g. 'Mom is wearing a red coat and appears happy.'
    Returns '' when the VLM added nothing (then the name in the main sentence
    stands alone). Age/gender is deliberately never included for a known person.
    """
    clauses = []
    appearance = (getattr(p, "appearance", "") or "").strip().rstrip(".")
    if appearance:
        clauses.append(f"is wearing {appearance}")
    expression = (getattr(p, "expression", "") or "").strip().rstrip(".")
    if expression:
        clauses.append(f"appears {expression}")
    if not clauses:
        return ""
    return f"{p.name} " + " and ".join(clauses) + "."


def _multi_who(ev, compact=False):
    """Build the WHO sentence(s) for a scene with MORE THAN ONE subject.

    KNOWN, live people are NAMED (never age/gender) plus a cautious clothing/mood
    line when the VLM supplied one (Phase 16). UNKNOWN people
    (including any spoofed known face, demoted upstream) are each described
    cautiously, capped at the first few so speech stays short; the remainder plus
    any face-less people counted by YOLO fold into an 'N other people' tail.
    Returns a list of sentence strings.

    `compact=True` (VLM scene available): the per-person age/gender roster reads
    like a robot taking attendance and the scene sentence describes the group far
    more naturally, so the WHO line shrinks to names + a total count ("Alex is at
    the door with 4 other people.") and lets the scene do the describing. Any
    spoofed face still gets its own explicit warning sentence — that must never
    be softened away.
    """
    # Every visible face was a photo -> a single clear spoof warning, no roster.
    if getattr(ev, "is_spoof", False):
        return ["Warning. The faces shown to the camera appear to be photos."]

    people = list(getattr(ev, "people", []) or [])
    extra = int(getattr(ev, "extra_unknown", 0) or 0)

    known, unknown = [], []
    for p in people:
        if getattr(p, "known", False) and not getattr(p, "is_spoof", False):
            known.append(p)
        else:
            unknown.append(p)

    known_unique = []                                     # dedup by name, keep order
    seen_names = set()
    for p in known:
        if p.name and p.name not in seen_names:
            seen_names.add(p.name)
            known_unique.append(p)
    names = [p.name for p in known_unique]

    if compact:
        total = len(people) + extra
        unknown_count = total - len(known)
        sentences = []
        if names:
            verb = "is" if len(names) == 1 else "are"
            s = f"{_join_list(names)} {verb} at the door"
            if unknown_count > 0:
                s += (f" with {unknown_count} other "
                      f"{_plural_person(unknown_count)}")
            sentences.append(s + ".")
        else:
            sentences.append(f"{total} people are at the door.")
        spoofed = sum(1 for p in unknown if getattr(p, "is_spoof", False))
        if spoofed == 1:
            sentences.append("Warning. One face shown to the camera appears "
                             "to be a photo, a possible spoof.")
        elif spoofed > 1:
            sentences.append(f"Warning. {spoofed} of the faces shown to the "
                             "camera appear to be photos, possible spoofs.")
        return sentences

    CAP = 3                                               # cap SPOKEN descriptions
    described = [_describe_unknown_person(p) for p in unknown[:CAP]]
    others = (len(unknown) - len(described)) + extra      # undescribed + face-less

    def _others_clause():
        return f"{others} other {_plural_person(others)}"

    sentences = []
    if names:
        # The main sentence NAMES the known people (clean, no description collides
        # with the verb); each one's clothing/mood follows as its own sentence.
        verb = "is" if len(names) == 1 else "are"
        s = f"{_join_list(names)} {verb} at the door"
        # Build the "along with ..." tail as ONE string first. Composing it
        # in-place used to emit "along with and 1 other person" whenever the
        # unknowns had no describable faces (described empty, others > 0),
        # because _join_list([]) is "" and the "and" was appended regardless.
        tail = _join_list(described)
        if others:
            tail = f"{tail}, and {_others_clause()}" if tail else _others_clause()
        if tail:
            s += ", along with " + tail
        s += "."
        sentences.append(s)
        for p in known_unique:                            # Phase 16 per-known detail
            detail = _known_detail_sentence(p)
            if detail:
                sentences.append(detail)
    elif described:
        verb = "is" if (len(described) == 1 and not others) else "are"
        s = f"There {verb} " + _join_list(described)
        if others:
            s += f", and {_others_clause()}"
        sentences.append(s + " at the door.")
    elif others:
        sentences.append(f"{others} {_plural_person(others)} are at the door, "
                         "faces not clearly visible.")
    else:
        sentences.append("Someone is at the door.")

    return sentences


def compose_announcement(ev) -> str:
    """Turn a VisitorEvent into one or two natural, conservative sentences.

    Reads (spine fields, most inert until their phase):
      is_spoof (P5), identity (P2), reid_seen_count (P9), visitor_count (P3),
      carried_objects/intent (P3), ocr_text/scene_summary (P6),
      translated_transcript/speech_transcript (P7/P8),
      age/gender/appearance (P12).

    KNOWN visitors keep the simple '<Name> is at the door.' - approximate age and
    gender are NEVER spoken for a recognised person. The richer age/gender +
    appearance (clothing/uniform) + carried-object description is UNKNOWN-only.
    """
    known = ev.identity.known

    # Count the subjects: every detected face plus any face-less people YOLO saw.
    # With MORE THAN ONE subject we enumerate them (Phase 15); a single subject
    # (or none) keeps the original single-person wording exactly.
    people = list(getattr(ev, "people", []) or [])
    extra = int(getattr(ev, "extra_unknown", 0) or 0)
    multi = (len(people) + extra) > 1

    if multi:
        scene = _clean_scene(getattr(ev, "scene_summary", ""))
        parts = _multi_who(ev, compact=bool(scene))
        # Per-person appearance/carried is already folded into each description
        # above; only the WHOLE-scene sentence is still worth adding once.
        if scene:
            parts.append(scene if scene.endswith(".") else scene + ".")
        return _finish_announcement(ev, parts)

    # ---- single-subject path (unchanged from earlier phases) ----
    # --- Who / what is the primary subject line ---
    if getattr(ev, "is_spoof", False):
        who = ("Warning. A face was shown to the camera but it appears to be a "
               "photo.")
    elif known:
        who = f"{ev.identity.name} is at the door."      # simple, no age/gender
    elif getattr(ev, "reid_seen_count", 0) >= 2:
        desc = _person_desc(ev)                          # folds in age + gender
        subject = f"unknown {desc}" if desc else "unknown visitor"
        who = (f"The same {subject} has come "
               f"{ev.reid_seen_count} times today.")
    elif ev.visitor_count > 1:
        who = f"{ev.visitor_count} unknown visitors are at the door."
    elif ev.visitor_count == 1:
        who = _unknown_who(ev)                            # folds in age + gender
    else:
        who = "The doorbell rang but no one is clearly visible."

    parts = [who]

    if known:
        # Phase 16: a KNOWN visitor gets ONE clean detail sentence (clothing + mood
        # from the VLM, never age/gender), e.g. "Mom is wearing a red coat and
        # appears happy." Empty -> the name sentence stands alone (fast path).
        single = list(getattr(ev, "people", []) or [])
        detail = _known_detail_sentence(single[0]) if single else ""
        if detail:
            parts.append(detail)
    else:
        # --- Appearance: clothing / uniform / branding (P12 VLM, unknown) ---
        # Unchanged wording for unknown singles (exact non-regression).
        appearance = (getattr(ev, "appearance", "") or "").strip()
        if appearance:
            parts.append(appearance if appearance.endswith(".") else appearance + ".")

    # --- VLM scene description (P6; Phase 16: for known visitors too) ---
    scene = _clean_scene(getattr(ev, "scene_summary", ""))
    if scene:
        parts.append(scene if scene.endswith(".") else scene + ".")

    return _finish_announcement(ev, parts)


def _finish_announcement(ev, parts) -> str:
    """Append the shared tail (carried objects, delivery/OCR, speech) that reads
    the same whether one person or a whole group is at the door, then join."""
    # --- Carried objects (P3 YOLO) ---
    if ev.carried_objects:
        parts.append(f"Carrying {', '.join(ev.carried_objects)}.")

    # --- Delivery, refined by OCR when available (P6) ---
    if ev.intent == "likely delivery":
        ocr = (ev.ocr_text or "").strip()
        if ocr:
            parts.append(f"Likely a delivery. Label reads: {ocr[:60]}.")
        else:
            parts.append("Likely a delivery.")

    # --- What the visitor said (P7 speech / P8 translation) ---
    said = (getattr(ev, "translated_transcript", "")
            or getattr(ev, "speech_transcript", "") or "").strip()
    if said:
        parts.append(f'They said: "{said}".')

    return " ".join(parts) if parts else "Someone is at the door."


class AccessibilityEngine:
    """Composes announcements and delivers them per accessibility mode."""

    def __init__(self, tts, mode: str = "both"):
        self.tts = tts
        self.mode = mode if mode in _VALID_MODES else "both"

    # ------------------------------------------------------------------
    def deliver(self, ev, speak: bool = True) -> str:
        """Compose the announcement onto ev and speak it if the mode wants audio.

        `speak` lets the caller suppress audio (e.g. the pipeline's cooldown) while
        still composing + storing the text. Deaf/both VISUAL delivery is handled by
        the server broadcasting the event over WebSocket; the browser renders big
        text + flash + vibrate. Returns the composed text.

        Phase 17: a per-kind identity earcon (known/delivery/unknown/spoof)
        leads the sentence, so a blind user classifies the visitor the moment
        audio starts."""
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
        """Speak an arbitrary sentence (used by the two-way /reply route).

        Speaks regardless of mode: a reply is an explicit user action, not an
        automatic announcement. `lang` (Phase 17) hints the text's language so
        a translated sentence is voiced by a matching per-language voice.
        Returns whether it was actually queued/spoken."""
        return self.tts.speak(text, lang=lang)
