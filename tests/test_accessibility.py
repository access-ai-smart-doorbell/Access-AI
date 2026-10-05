"""PURE-logic tests for compose_announcement() (Phase 4 output layer).

This is the single place that turns a VisitorEvent into the one sentence the
system speaks / shows. Each branch of the "who" line, plus the carried-objects,
delivery, scene and speech add-ons, is pinned here. No TTS, no I/O.
"""

from accessai.accessibility import compose_announcement
from accessai.visitor_event import VisitorEvent, Identity, Person


def _ev(**kw) -> VisitorEvent:
    ev = VisitorEvent(event_id="e1", timestamp="2026-07-10T12:00:00")
    for k, v in kw.items():
        setattr(ev, k, v)
    return ev


def test_spoof_warning():
    text = compose_announcement(_ev(is_spoof=True))
    assert "Warning" in text and "photo" in text


def test_known_person():
    text = compose_announcement(
        _ev(identity=Identity(known=True, name="Alice"), visitor_count=1))
    assert text.startswith("Alice is at the door.")


def test_repeat_unknown_visitor():
    text = compose_announcement(_ev(reid_seen_count=3, visitor_count=1))
    assert "same unknown visitor" in text and "3 times" in text


def test_multiple_unknown_visitors():
    text = compose_announcement(_ev(visitor_count=2))
    assert text.startswith("2 unknown visitors are at the door.")


def test_single_unknown_visitor():
    text = compose_announcement(_ev(visitor_count=1))
    assert text.startswith("An unknown visitor is at the door.")


def test_no_one_visible():
    text = compose_announcement(_ev(visitor_count=0))
    assert "no one is clearly visible" in text


def test_carried_and_delivery_and_ocr():
    text = compose_announcement(_ev(
        visitor_count=1,
        carried_objects=["a package"],
        intent="likely delivery",
        ocr_text="FedEx 12345",
    ))
    assert "Carrying a package." in text
    assert "Likely a delivery." in text
    assert "Label reads: FedEx 12345" in text


def test_scene_and_speech_added():
    text = compose_announcement(_ev(
        visitor_count=1,
        scene_summary="A person in a blue jacket",
        speech_transcript="hello, is anyone home",
    ))
    assert "A person in a blue jacket." in text
    assert 'They said: "hello, is anyone home".' in text


def test_translated_transcript_preferred_over_original():
    text = compose_announcement(_ev(
        visitor_count=1,
        speech_transcript="hola",
        translated_transcript="hello",
    ))
    assert 'They said: "hello".' in text
    assert "hola" not in text


# --- Natural-description fixes (raw-JSON leak + robotic group roster) --------

def test_json_scene_never_spoken():
    """A truncated VLM reply once left raw JSON in scene_summary; the composed
    announcement must NEVER read JSON aloud to a blind user."""
    text = compose_announcement(_ev(
        visitor_count=1,
        scene_summary='{"people": [ {"appearance": "light-colored headscarf"',
    ))
    assert '{"' not in text and "appearance" not in text
    assert text.startswith("An unknown visitor is at the door.")


def test_group_with_scene_uses_compact_who():
    """With a good VLM scene sentence, a group is announced as a count + the
    scene — not a robotic per-person age/gender roster."""
    people = [Person(known=False, age=42, gender="man", box=(i * 10, 0, i * 10 + 5, 5))
              for i in range(3)]
    text = compose_announcement(_ev(
        people=people, extra_unknown=3, visitor_count=3,
        scene_summary="Six people appear to be sitting together indoors, "
                      "facing the camera.",
    ))
    assert "6 people are at the door." in text
    assert "Six people appear to be sitting together" in text
    assert "forties" not in text          # no per-person roster
    assert "unknown man" not in text


def test_group_with_scene_keeps_spoof_warning():
    """The compact group wording must never soften a spoof warning away."""
    people = [
        Person(known=False, is_spoof=True, box=(0, 0, 5, 5)),
        Person(known=False, box=(10, 0, 15, 5)),
    ]
    text = compose_announcement(_ev(
        people=people, visitor_count=2,
        scene_summary="Two people appear to be standing at the door.",
    ))
    assert "Warning" in text and "photo" in text


def test_group_without_scene_keeps_roster():
    """No VLM scene (offline / failed) -> the detailed detector roster still
    describes the group; nothing regresses to a bare count."""
    people = [
        Person(known=False, age=42, gender="man", box=(0, 0, 5, 5)),
        Person(known=False, age=30, gender="woman", box=(10, 0, 15, 5)),
    ]
    text = compose_announcement(_ev(people=people, visitor_count=2))
    assert "unknown man" in text and "unknown woman" in text


def test_known_in_group_with_scene_is_named():
    people = [
        Person(known=True, name="Alice", box=(0, 0, 5, 5)),
        Person(known=False, box=(10, 0, 15, 5)),
        Person(known=False, box=(20, 0, 25, 5)),
    ]
    text = compose_announcement(_ev(
        people=people, visitor_count=3,
        scene_summary="Three people appear to be standing near the gate.",
    ))
    assert text.startswith("Alice is at the door with 2 other people.")
    assert "Three people appear to be standing near the gate." in text


# --- The "along with and" regression (seen live on a real motion event) -----
# A known person plus face-less companions left the describable-unknown list
# empty while the other-count was > 0, and the roster glued the connector on
# anyway: "Vinay is at the door, along with and 1 other person."

def test_known_with_faceless_others_has_no_dangling_and():
    """No VLM scene -> detailed roster. The companions have no faces at all,
    so there is nothing to describe and the sentence must read cleanly."""
    text = compose_announcement(_ev(
        people=[Person(known=True, name="Vinay", box=(0, 0, 5, 5))],
        extra_unknown=1, visitor_count=2))
    assert "along with and" not in text
    assert ", along with 1 other person." in text


def test_known_with_many_faceless_others_pluralises():
    text = compose_announcement(_ev(
        people=[Person(known=True, name="Vinay", box=(0, 0, 5, 5))],
        extra_unknown=4, visitor_count=5))
    assert "along with and" not in text
    assert ", along with 4 other people." in text


def test_known_with_described_and_faceless_others_keeps_and():
    """When there IS someone to describe, the connector is still correct."""
    text = compose_announcement(_ev(
        people=[Person(known=True, name="Vinay", box=(0, 0, 5, 5)),
                Person(known=False, age=42, gender="man", box=(10, 0, 15, 5))],
        extra_unknown=2, visitor_count=4))
    assert "along with and" not in text
    assert "along with an unknown man" in text
    assert "and 2 other people." in text


def test_known_with_only_described_others_has_no_other_clause():
    text = compose_announcement(_ev(
        people=[Person(known=True, name="Vinay", box=(0, 0, 5, 5)),
                Person(known=False, age=42, gender="man", box=(10, 0, 15, 5))],
        visitor_count=2))
    assert "along with an unknown man" in text
    assert "other person" not in text and "other people" not in text


# --------------------------------------------------------------------------
# alert_kind (Phase 17) - the per-event alert taxonomy every client keys on
# --------------------------------------------------------------------------
from accessai.visitor_event import alert_kind, ALERT_KINDS, Person


def test_alert_kind_priority_spoof_wins():
    ev = _ev(identity=Identity(known=True, name="Rahul"),
             is_spoof=True, intent="likely delivery")
    assert alert_kind(ev) == "spoof"


def test_alert_kind_known_beats_delivery():
    ev = _ev(identity=Identity(known=True, name="Rahul"),
             intent="likely delivery")
    assert alert_kind(ev) == "known"


def test_alert_kind_person_level_known():
    ev = _ev(people=[Person(known=False), Person(known=True, name="Priya")])
    assert alert_kind(ev) == "known"


def test_alert_kind_delivery_then_unknown():
    ev = _ev(intent="likely delivery")
    assert alert_kind(ev) == "delivery"
    ev.intent = "unknown visitor"
    assert alert_kind(ev) == "unknown"


def test_alert_kind_dict_form_matches_dataclass():
    ev = _ev(identity=Identity(known=True, name="Rahul"))
    assert alert_kind(ev.to_dict()) == alert_kind(ev) == "known"
    assert ev.to_dict()["alert_kind"] == "known"


def test_alert_kind_always_in_taxonomy():
    for ev in (_ev(identity=Identity(known=True, name="A")),
               _ev(intent="likely delivery"), _ev()):
        assert alert_kind(ev) in ALERT_KINDS


def test_no_appears_appears_stutter_and_capitalization():
    """Ensure 'appears calm' doesn't produce 'appears appears calm' and sentence is capitalized."""
    p = Person(known=False, age=25, gender="woman", expression="appears calm", clothing="blue shirt")
    ev = _ev(people=[p], visitor_count=1)
    text = compose_announcement(ev)
    assert "appears appears" not in text
    assert "appears calm" in text
    assert text[0].isupper()


def test_single_person_with_scene_includes_rich_details():
    """When a VLM scene is present, single known or unknown person includes rich person details (clothing, etc.)."""
    p = Person(known=True, name="Vinay", clothing="white jersey with number 19", box=(0, 0, 10, 10))
    ev = _ev(
        identity=Identity(known=True, name="Vinay"),
        people=[p],
        visitor_count=1,
        scene_summary="There is one person, Vinay, lying down on the right side.",
    )
    text = compose_announcement(ev)
    assert "Vinay" in text
    assert "white jersey with number 19" in text
    assert "lying down on the right side." in text


def test_known_person_full_rich_description():
    """Verify hairstyle, facial features, clothing, and hands are all spoken."""
    p = Person(
        known=True,
        name="Vinay",
        position="center",
        clothing="white Spain national team away jersey with maroon stripes",
        hairstyle="fade haircut",
        appearance="light stubble and mustache",
        hands="empty at sides",
    )
    ev = _ev(
        identity=Identity(known=True, name="Vinay"),
        people=[p],
        visitor_count=1,
    )
    text = compose_announcement(ev)
    assert "Vinay is directly in front of you" in text
    assert "wearing white Spain national team away jersey" in text
    assert "with a fade haircut" in text
    assert "with light stubble and mustache" in text
    assert "hands empty at sides" in text


