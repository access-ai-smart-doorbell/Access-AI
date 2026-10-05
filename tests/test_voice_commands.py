"""Tests for the Phase-10 voice command layer.

parse_command() is PURE (the bulk of these tests). handle_command() is the I/O
half; we exercise it with tiny fakes for db / pipeline / access so no camera,
mic, DB or TTS is ever touched.
"""

import numpy as np

from accessai import voice_commands as vc


# --------------------------------------------------------------------------
# parse_command - PURE
# --------------------------------------------------------------------------
def test_who_is_there_variants():
    for text in ["Who is at the door?", "who's there", "is anyone there"]:
        assert vc.parse_command(text)["intent"] == "who_is_there"


def test_recent_variants():
    for text in ["recent visitors", "who came earlier", "show me the history"]:
        assert vc.parse_command(text)["intent"] == "recent"


def test_analyze_variants():
    for text in ["analyze the door", "what do you see", "scan the entrance"]:
        assert vc.parse_command(text)["intent"] == "analyze_now"


def test_open_camera_variants():
    for text in ["open the camera", "show camera", "go to live view"]:
        assert vc.parse_command(text)["intent"] == "open_camera"


def test_count_today_variants():
    for text in ["how many visitors today", "count today", "number of visitors"]:
        assert vc.parse_command(text)["intent"] == "count_today"


def test_set_mode_extracts_mode():
    r = vc.parse_command("switch to blind mode")
    assert r["intent"] == "set_mode" and r["args"]["mode"] == "blind"

    r = vc.parse_command("change to deaf mode")
    assert r["intent"] == "set_mode" and r["args"]["mode"] == "deaf"

    # "everything" and "all" both normalise to "both".
    r = vc.parse_command("set mode to everything")
    assert r["intent"] == "set_mode" and r["args"]["mode"] == "both"


def test_set_mode_without_valid_target_falls_through():
    # "change mode" with no blind/deaf/both word is not actionable -> unknown.
    assert vc.parse_command("change mode please")["intent"] == "unknown"


def test_empty_and_gibberish_are_unknown():
    assert vc.parse_command("")["intent"] == "unknown"
    assert vc.parse_command("   ")["intent"] == "unknown"
    assert vc.parse_command("banana helicopter")["intent"] == "unknown"


def test_parse_command_shape():
    r = vc.parse_command("who is there")
    assert set(r.keys()) == {"intent", "args", "raw"}
    assert isinstance(r["args"], dict)
    assert r["raw"] == "who is there"


# --------------------------------------------------------------------------
# Phase 16 - free-form scene questions fall through to ask_scene
# --------------------------------------------------------------------------
def test_free_form_questions_route_to_ask_scene():
    # Free-form VISUAL questions that don't hit a dedicated intent trigger fall
    # through to the VLM. NB: "describe ..." intentionally routes to analyze_now
    # (the full pipeline description), so it is NOT in this list - see
    # test_describe_prefers_full_analysis below.
    for text in ["what colour is their dress", "what is he doing now",
                 "how many bags is she holding?", "what are they wearing",
                 "what is the person holding", "is it raining outside?"]:
        r = vc.parse_command(text)
        assert r["intent"] == "ask_scene", f"{text!r} -> {r['intent']}"
        assert r["args"]["question"] == text.strip()


def test_describe_prefers_full_analysis():
    # "describe" is a deliberate analyze_now trigger: it runs the full perception
    # pipeline (richer than a one-shot VLM answer), so it must win over ask_scene.
    assert vc.parse_command("describe the person at the door")["intent"] \
        == "analyze_now"


def test_specific_intents_still_win_over_ask_scene():
    # These read like questions but MUST keep their dedicated intents (ordering:
    # the specific table is matched before the ask_scene question fall-through).
    assert vc.parse_command("who is at the door?")["intent"] == "who_is_there"
    assert vc.parse_command("how many people are here")["intent"] == "count_today"
    assert vc.parse_command("what do you see?")["intent"] == "analyze_now"
    assert vc.parse_command("can you open the camera")["intent"] == "open_camera"


def test_non_questions_stay_unknown():
    # No question mark, no opener word -> still unknown (unchanged behaviour).
    assert vc.parse_command("banana helicopter")["intent"] == "unknown"
    assert vc.parse_command("the weather today")["intent"] == "unknown"


# --------------------------------------------------------------------------
# Semantic reasoning engine Level 2 - the on-request FULL report
# --------------------------------------------------------------------------
def test_full_details_variants():
    for text in ["give me the details", "tell me everything",
                 "describe everything", "full report",
                 "describe the visitor in detail"]:
        assert vc.parse_command(text)["intent"] == "full_details", text


def test_plain_describe_still_analyzes():
    # The bare "describe" trigger must keep routing to the full local pipeline;
    # only the explicit detail phrasings above go to the Level-2 VLM report.
    assert vc.parse_command("describe the person at the door")["intent"] \
        == "analyze_now"


def test_handle_full_details_uses_vlm_report():
    class _FakeVLM:
        def available(self):
            return True

        def detailed_report(self, frame, facts=""):
            return "One person appears to be at the door holding a box."

    class _FakeVLMPipeline:
        vlm = _FakeVLM()
        vlm_enabled = True

    out = vc.handle_command("full_details", {}, pipeline=_FakeVLMPipeline(),
                            latest=_FakeLatest(), db=_FakeDB([]))
    assert "holding a box" in out


def test_handle_full_details_no_frame_says_so():
    class _FakeVLM:
        def available(self):
            return True

        def detailed_report(self, frame, facts=""):   # pragma: no cover
            raise AssertionError("must not be called without a frame")

    class _FakeVLMPipeline:
        vlm = _FakeVLM()
        vlm_enabled = True

    class _EmptyLatest:
        def get(self):
            return None

    out = vc.handle_command("full_details", {}, pipeline=_FakeVLMPipeline(),
                            latest=_EmptyLatest(), db=_FakeDB([]))
    assert "no image" in out.lower()


# --------------------------------------------------------------------------
# handle_command - the I/O half, driven by fakes
# --------------------------------------------------------------------------
class _FakeEvent:
    announcement_text = "Alice is at the door."


class _FakePipeline:
    def __init__(self):
        self.calls = []

    def run_once(self, frame, trigger="manual", audio=None):
        self.calls.append(trigger)
        return _FakeEvent()


class _FakeLatest:
    def get(self):
        return np.zeros((10, 10, 3), dtype=np.uint8)


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def recent_events(self, limit=50):
        return self._rows[:limit]


class _FakeAccess:
    def __init__(self):
        self.mode = "both"
        self.spoken = []

    def set_mode(self, mode):
        self.mode = mode
        return mode

    def speak_text(self, text, lang=""):
        self.spoken.append(text)
        return True


def test_handle_analyze_runs_pipeline():
    pipe = _FakePipeline()
    out = vc.handle_command("who_is_there", {}, pipeline=pipe,
                            latest=_FakeLatest())
    assert out == "Alice is at the door."
    assert pipe.calls == ["voice"]      # ran under the "voice" trigger


def test_handle_recent_summarises_last_event():
    db = _FakeDB([{"announcement_text": "A delivery is at the door.",
                   "timestamp": "2026-07-10T11:59:00"}])
    out = vc.handle_command("recent", {}, db=db)
    assert "A delivery is at the door." in out


def test_handle_recent_empty_history():
    out = vc.handle_command("recent", {}, db=_FakeDB([]))
    assert "no visitors" in out.lower()


def test_handle_count_today():
    import datetime as dt
    today = dt.datetime.now().date().isoformat()
    rows = [
        {"timestamp": f"{today}T09:00:00"},
        {"timestamp": f"{today}T10:00:00"},
        {"timestamp": "2020-01-01T10:00:00"},   # not today
    ]
    out = vc.handle_command("count_today", {}, db=_FakeDB(rows))
    assert "2 visitors" in out


def test_handle_set_mode_calls_access():
    access = _FakeAccess()
    out = vc.handle_command("set_mode", {"mode": "blind"}, access=access)
    assert access.mode == "blind"
    assert "blind" in out.lower()


def test_handle_open_camera_is_a_confirmation():
    out = vc.handle_command("open_camera", {})
    assert "camera" in out.lower()


def test_handle_unknown_is_helpful():
    out = vc.handle_command("unknown", {})
    assert "who is at the door" in out.lower()


def test_run_voice_interaction_end_to_end_with_wav():
    """Full glue path using an uploaded WAV, driven entirely by fakes."""
    class _FakeSpeech:
        def available(self):
            return True

        def transcribe_wav(self, wav_bytes):
            return ("how many visitors today", "en")

    import datetime as dt
    today = dt.datetime.now().date().isoformat()
    db = _FakeDB([{"timestamp": f"{today}T09:00:00"}])
    access = _FakeAccess()

    result = vc.run_voice_interaction(speech=_FakeSpeech(), db=db,
                                      access=access, wav_bytes=b"RIFF....")
    assert result["intent"] == "count_today"
    assert result["language"] == "en"
    assert result["spoke"] is True
    assert access.spoken and "visitor" in access.spoken[0].lower()
