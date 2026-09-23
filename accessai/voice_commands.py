"""
Voice commands - hands-free Blind Mode (Phase 10).

Two clean halves, deliberately split so the logic stays unit-testable:

  parse_command(text)  -> PURE. Maps a transcript to an intent + args. No I/O,
                          no config import, no globals. This is what the pytest
                          suite exercises; it never touches a camera, a mic, the
                          DB, or the TTS engine.

  handle_command(...)  -> the ONLY place with side effects. Given an intent it
                          reads the latest frame / DB and returns the SPOKEN
                          answer string. It reuses the SAME pipeline, database
                          and accessibility engine the doorbell already uses - it
                          never duplicates perception or announcement logic.

  run_voice_interaction(...) -> the glue used by BOTH entry points (the /listen
                          push-to-talk route AND the always-on wake callback):
                          capture -> transcribe (Phase-7 SpeechModule) -> parse
                          -> handle -> speak (Phase-4 TTS). Returns a small dict
                          describing what happened, for the dashboard + logs.

Design rule (same as every phase): degrade, never crash. An empty transcript,
a missing frame, or a None module all resolve to a polite spoken sentence.
"""

import datetime as _dt
import logging

import numpy as np

logger = logging.getLogger("voice_commands")


# ---------------------------------------------------------------------------
# PART 1 - PURE parsing (unit-tested; no side effects, no imports beyond stdlib)
# ---------------------------------------------------------------------------
# Each intent is a list of trigger phrases. Order matters: we test the most
# specific intents first so "how many visitors today" doesn't get swallowed by
# the broader "visitor" phrasing of `recent`. Matching is lowercase substring -
# forgiving of Whisper's punctuation and filler words.
_INTENT_PHRASES = [
    ("count_today", [
        # Phase 16: dropped the bare "how many" trigger - it swallowed scene
        # questions like "how many bags is she holding" into a visitor count.
        # Count intents now require a visitor/people/today context word so object
        # questions correctly fall through to ask_scene.
        "how many visitors", "how many people", "how many came", "how many today",
        "count today", "count of visitors", "visitors today",
        "number of visitors", "number of people",
    ]),
    ("set_mode", [
        "blind mode", "deaf mode", "both mode", "switch to", "change mode",
        "set mode", "change to", "mode to",
    ]),
    ("recent", [
        "recent visitor", "recent visitors", "last visitor", "who came",
        "who has come", "who visited", "visitor history", "history",
        "previous visitor", "who was here", "earlier",
    ]),
    ("open_camera", [
        "open camera", "open the camera", "show camera", "show the camera",
        "live view", "live camera", "camera view", "show me the camera",
    ]),
    ("who_is_there", [
        "who is at the door", "who's at the door", "who is there",
        "who's there", "whos there", "who is at the front",
        "is someone there", "is anyone there", "who is it",
    ]),
    # Level 2 of the semantic reasoning engine: the on-request FULL report.
    # Checked BEFORE analyze_now so "describe in detail" / "describe everything"
    # beat the plain "describe" trigger below.
    ("full_details", [
        "full detail", "more detail", "in detail", "give me the details",
        "give me details", "tell me more", "tell me everything",
        "describe everything", "everything you see", "full description",
        "detailed description", "full report", "detailed report",
    ]),
    ("analyze_now", [
        "analyze", "analyse", "what do you see", "what's at the door",
        "whats at the door", "check the door", "look at the door", "scan",
        "describe", "look outside", "what is happening",
    ]),
]

# Words that pick a mode for the set_mode intent.
_MODE_WORDS = {
    "blind": "blind",
    "deaf": "deaf",
    "both": "both",
    "everything": "both",
    "all": "both",
}


def parse_command(text: str) -> dict:
    """Map a spoken command to {"intent", "args", "raw"}. PURE - safe to unit-test.

    Returns intent == "unknown" when nothing matches, so the caller can speak a
    helpful fallback. `args` carries any extracted parameters (e.g. the target
    mode for set_mode); it is always a dict.
    """
    raw = text or ""
    t = raw.lower().strip()
    if not t:
        return {"intent": "unknown", "args": {}, "raw": raw}

    for intent, phrases in _INTENT_PHRASES:
        if any(p in t for p in phrases):
            args = {}
            if intent == "set_mode":
                mode = _extract_mode(t)
                if mode is None:
                    # "switch mode" with no valid target -> not actionable.
                    continue
                args["mode"] = mode
            return {"intent": intent, "args": args, "raw": raw}

    # Phase 16: free-form scene question. AFTER the specific intents above so
    # "who is at the door" / "what do you see" still route to their handlers; a
    # generic question ("what colour is their dress", "what is he doing now") now
    # falls through to the VLM instead of a canned "didn't catch that". Kept PURE:
    # a string test only - the frame/VLM/DB access lives in handle_command.
    if _looks_like_question(t):
        return {"intent": "ask_scene", "args": {"question": raw.strip()},
                "raw": raw}

    return {"intent": "unknown", "args": {}, "raw": raw}


# Question openers that signal a free-form scene query the VLM should answer.
_QUESTION_WORDS = (
    "what", "where", "why", "how", "which", "whose", "when", "is ", "are ",
    "does ", "do ", "can ", "could ", "was ", "were ", "colour", "color",
    "wearing", "holding", "carrying", "doing", "describe",
    # natural phrasings from the hands-free wake flow ("hey access, ...")
    "tell me", "did ", "has ", "have ", "who ", "am i", "should i",
)


def _looks_like_question(t: str) -> bool:
    """True when the (lowercased, stripped) text reads like a free-form question.

    A question mark, or one of the recognised opener words, is enough. PURE - no
    I/O. Deliberately generous: a false positive just sends the utterance to the
    VLM (a hedged 'I cannot tell' at worst), which is friendlier than the old
    canned fallback."""
    t = (t or "").strip()
    if not t:
        return False
    if t.endswith("?"):
        return True
    return any(t == w.strip() or t.startswith(w) for w in _QUESTION_WORDS)


def _extract_mode(t: str):
    """First recognised mode word in the text, or None."""
    for word, mode in _MODE_WORDS.items():
        if word in t:
            return mode
    return None


# ---------------------------------------------------------------------------
# PART 2 - the I/O half: act on an intent, return the spoken answer
# ---------------------------------------------------------------------------
def handle_command(intent: str, args: dict, *, pipeline=None, db=None,
                   latest=None, access=None) -> str:
    """Execute one parsed command and return the sentence to speak.

    This is the ONLY function here that touches the world. Every branch returns
    a plain string; nothing raises. It reuses the existing pipeline (perception
    + announcement), database, and accessibility engine - never a second copy.
    """
    args = args or {}
    try:
        if intent in ("who_is_there", "analyze_now"):
            return _analyze_current_frame(pipeline, latest)

        if intent == "ask_scene":
            return _answer_scene(pipeline, latest, db, args.get("question", ""))

        if intent == "full_details":
            return _full_details(pipeline, latest, db)

        if intent == "recent":
            return _describe_recent(db)

        if intent == "count_today":
            return _count_today(db)

        if intent == "open_camera":
            # The spoken answer confirms; the dashboard reacts to the broadcast
            # (type "voice", intent "open_camera") by focusing the live view.
            return "Opening the live camera view on the dashboard."

        if intent == "set_mode":
            return _set_mode(access, args.get("mode"))

        # Unknown / unparsed.
        return ("Sorry, I didn't catch that. You can ask who is at the door, "
                "for recent visitors, how many visitors today, or to analyze "
                "the door.")
    except Exception as e:                                # pragma: no cover
        print(f"[VoiceCommands] handle_command error: {e}")
        return "Sorry, something went wrong handling that command."


def _fallback_frame():
    """A neutral gray frame so analysis never crashes when no camera frame exists."""
    return np.zeros((720, 1280, 3), dtype=np.uint8)


# A tiny silent clip handed to the pipeline so its Phase-7 speech step does NOT
# re-open the mic while answering a voice command (we already used the mic for
# the command itself). has_speech() returns False on silence -> no transcription.
_SILENT_AUDIO = np.zeros(1600, dtype=np.float32)


def _analyze_current_frame(pipeline, latest) -> str:
    """Run the real pipeline on the current frame and speak its announcement."""
    if pipeline is None:
        return "The camera pipeline is not available right now."
    frame = None
    if latest is not None:
        try:
            frame = latest.get()
        except Exception as e:
            logger.debug("latest.get() failed: %s", e)
            frame = None
    if frame is None:
        frame = _fallback_frame()
    ev = pipeline.run_once(frame, trigger="voice", audio=_SILENT_AUDIO)
    text = (getattr(ev, "announcement_text", "") or "").strip()
    return text or "I looked at the door but could not tell who is there."


def _current_frame(latest):
    """The newest camera frame, or None when the camera has produced nothing.

    Callers that MUST send something (legacy analyze path) fall back to
    _fallback_frame() themselves; the VLM paths below instead tell the user the
    camera has no image - answering a visual question from a blank gray frame
    only ever produces convincing nonsense (Bug 2)."""
    if latest is None:
        return None
    try:
        return latest.get()
    except Exception as e:
        logger.debug("latest.get() failed: %s", e)
        return None


def _check_vlm(pipeline):
    """The pipeline's ready VLM, or None when visual answering is unavailable."""
    if pipeline is None:
        return None
    vlm = getattr(pipeline, "vlm", None)
    if vlm is None or not getattr(pipeline, "vlm_enabled", False):
        return None
    return vlm if vlm.available() else None


def _answer_scene(pipeline, latest, db, question: str) -> str:
    """Phase 16: answer a free-form question about the CURRENT frame via the VLM.

    Reuses the pipeline's VLM (same keys/failover, no duplication) and grounds the
    answer with the most recent event's known facts. Degrades to a polite spoken
    sentence when the VLM is unavailable - never raises, never fabricates."""
    question = (question or "").strip()
    if not question:
        return "What would you like to know about the door?"
    if pipeline is None:
        return "The camera is not available right now."
    vlm = _check_vlm(pipeline)
    if vlm is None:
        return ("I can't answer questions about the view right now. Visual "
                "descriptions are not available.")
    # Bug 2: answer from the LIVE camera frame - and say so when there isn't
    # one, instead of quietly analysing a blank gray frame.
    frame = _current_frame(latest)
    if frame is None:
        return ("The camera has no image right now, so I can't answer that. "
                "Please check that the camera is connected.")
    grounding = _recent_grounding(db)
    try:
        answer = vlm.answer_question(frame, question, grounding=grounding)
    except Exception as e:                                # pragma: no cover
        print(f"[VoiceCommands] answer_question failed: {e}")
        answer = ""
    answer = (answer or "").strip()
    return answer or ("I looked, but I couldn't tell from what's visible at the "
                      "door right now.")


def _full_details(pipeline, latest, db) -> str:
    """Level 2 of the semantic reasoning engine: the on-request FULL report.

    Level 1 (the instant short alert) already fired when the visitor arrived;
    this runs when the user asks for the details, walking the live frame through
    vlm.detailed_report() with the same detector grounding as ask_scene. Level 3
    (follow-up questions) keeps flowing through _answer_scene."""
    if pipeline is None:
        return "The camera is not available right now."
    vlm = _check_vlm(pipeline)
    if vlm is None:
        return ("Detailed visual descriptions are not available right now.")
    frame = _current_frame(latest)
    if frame is None:
        return ("The camera has no image right now, so I can't describe the "
                "scene. Please check that the camera is connected.")
    facts = _recent_grounding(db)
    try:
        report = vlm.detailed_report(frame, facts=facts)
    except Exception as e:                                # pragma: no cover
        print(f"[VoiceCommands] detailed_report failed: {e}")
        report = ""
    report = (report or "").strip()
    return report or ("I looked closely, but I couldn't make out any clear "
                      "details at the door right now.")


def _recent_grounding(db, max_age_seconds: int = 180) -> str:
    """A short ground-truth hint (identity + objects) from the last stored event,
    so the VLM's answer doesn't contradict the on-device detectors. '' if none.

    Only a FRESH event grounds the answer (default 3 minutes): the question is
    about the LIVE frame, and detector facts from this morning's courier would
    push the VLM to describe a person who is no longer there (Bug 2)."""
    if db is None:
        return ""
    try:
        rows = db.recent_events(limit=1)
    except Exception as e:                                     # pragma: no cover
        logger.debug("recent_events() failed: %s", e)
        return ""
    if not rows:
        return ""
    ev = rows[0]
    try:
        then = _dt.datetime.fromisoformat(ev.get("timestamp", ""))
        if (_dt.datetime.now() - then).total_seconds() > max_age_seconds:
            return ""
    except Exception as e:
        logger.debug("isoformat parse failed: %s", e)
        return ""
    bits = []
    ident = ev.get("identity") or {}
    if ident.get("known") and ident.get("name"):
        bits.append(f"a known visitor named {ident['name']}")
    objs = ev.get("detected_objects") or ev.get("carried_objects") or []
    labels = []
    for o in objs:
        if isinstance(o, dict):
            labels.append(str(o.get("label", "")).strip())
        elif isinstance(o, str):
            labels.append(o.strip())
    labels = [x for x in labels if x]
    if labels:
        bits.append("detected objects: " + ", ".join(labels[:6]))
    return "; ".join(bits)


def _describe_recent(db) -> str:
    """Summarise the most recent stored visitor event."""
    if db is None:
        return "No visitor history is available."
    rows = db.recent_events(limit=1)
    if not rows:
        return "There are no visitors in the history yet."
    ev = rows[0]
    ann = (ev.get("announcement_text") or "").strip()
    when = _time_phrase(ev.get("timestamp", ""))
    if ann:
        return f"The most recent visitor, {when}: {ann}"
    who = _who_label(ev)
    return f"The most recent visitor {when} was {who}."


def _count_today(db) -> str:
    """Count events whose timestamp falls on today's date."""
    if db is None:
        return "No visitor history is available."
    today = _dt.datetime.now().date().isoformat()
    rows = db.recent_events(limit=500)
    n = sum(1 for r in rows if (r.get("timestamp") or "").startswith(today))
    if n == 0:
        return "No visitors have come to the door today."
    if n == 1:
        return "There has been one visitor at the door today."
    return f"There have been {n} visitors at the door today."


def _set_mode(access, mode) -> str:
    if access is None:
        return "Accessibility mode control is not available."
    if mode not in ("blind", "deaf", "both"):
        return ("I couldn't tell which mode you wanted. Say blind mode, deaf "
                "mode, or both mode.")
    try:
        access.set_mode(mode)
    except Exception as e:                                     # pragma: no cover
        logger.debug("set_mode failed: %s", e)
        return "Sorry, I couldn't change the mode."
    return f"Accessibility mode set to {mode}."


# ---------------------------------------------------------------------------
# PART 3 - end-to-end interaction, shared by /listen and the wake callback
# ---------------------------------------------------------------------------
def run_voice_interaction(*, speech=None, pipeline=None, db=None, latest=None,
                          access=None, seconds=4, wav_bytes=None) -> dict:
    """Capture a command, parse it, act, and speak the answer.

    Used by BOTH the push-to-talk /listen route (which may hand in an uploaded
    WAV via `wav_bytes`) and the always-on wake callback (which records live).
    Returns a small dict for the dashboard/logs; never raises.
    """
    text, lang = "", ""
    try:
        if wav_bytes is not None and speech is not None and speech.available():
            text, lang = speech.transcribe_wav(wav_bytes)
        elif speech is not None and speech.available():
            text, lang = speech.listen_and_transcribe(seconds)
    except Exception as e:                                # pragma: no cover
        print(f"[VoiceCommands] capture/transcribe failed: {e}")
        text, lang = "", ""

    parsed = parse_command(text)
    answer = handle_command(parsed["intent"], parsed["args"], pipeline=pipeline,
                            db=db, latest=latest, access=access)

    spoke = False
    if access is not None and answer:
        try:
            spoke = bool(access.speak_text(answer))
        except Exception as e:                            # pragma: no cover
            print(f"[VoiceCommands] speak failed: {e}")
            spoke = False

    return {
        "command": text or "",
        "language": lang or "",
        "intent": parsed["intent"],
        "args": parsed["args"],
        "answer": answer,
        "spoke": spoke,
    }


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _who_label(ev: dict) -> str:
    if ev.get("is_spoof"):
        return "a possible spoof attempt"
    ident = ev.get("identity") or {}
    if ident.get("known"):
        return ident.get("name", "a known person")
    return "an unknown visitor"


def _time_phrase(ts: str) -> str:
    """A short, human 'x minutes ago' phrase from an ISO timestamp."""
    try:
        then = _dt.datetime.fromisoformat(ts)
    except Exception as e:
        logger.debug("isoformat parse failed: %s", e)
        return "recently"
    delta = _dt.datetime.now() - then
    secs = int(delta.total_seconds())
    if secs < 0:
        return "just now"
    if secs < 60:
        return "just now"
    mins = secs // 60
    if mins < 60:
        return f"{mins} minute{'s' if mins != 1 else ''} ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"
