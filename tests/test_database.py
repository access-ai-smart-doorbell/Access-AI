"""Storage-layer tests.

These are the first tests that touch real I/O. Every one runs against a
throwaway SQLite file in tmp_path, so they never see the developer's
`data/accessai.db` and never need a server, a camera, or a model.

They exist because the DB is where a silent mistake is most expensive: an
event that saves but doesn't come back, a search that misses the visitor you
remember, or an update path that quietly writes nothing. The pure-logic tests
elsewhere cannot catch any of that.
"""

import pytest

from accessai.database import Database
from accessai.visitor_event import VisitorEvent, Identity, Person


@pytest.fixture()
def db(tmp_path):
    """A fresh database per test — no shared state, no ordering dependence."""
    return Database(str(tmp_path / "test.db"))


def _ev(event_id="e1", timestamp="2026-07-10T12:00:00", **kw):
    """Build a VisitorEvent the way the pipeline does: construct, then fill
    fields. Mirrors tests/test_accessibility.py so the two read alike."""
    ev = VisitorEvent(event_id=event_id, timestamp=timestamp)
    for k, v in kw.items():
        setattr(ev, k, v)
    return ev


# --- save / read back ------------------------------------------------------

def test_save_then_read_back_preserves_the_announcement(db):
    db.save_event(_ev(announcement_text="Vinay is at the door.",
                      intent="known_visitor"))
    got = db.get_event("e1")
    assert got is not None
    assert got["announcement_text"] == "Vinay is at the door."
    assert got["intent"] == "known_visitor"


def test_get_event_returns_none_for_an_unknown_id(db):
    assert db.get_event("nope") is None


def test_recent_events_is_newest_first(db):
    db.save_event(_ev(event_id="old", timestamp="2026-07-10T09:00:00"))
    db.save_event(_ev(event_id="new", timestamp="2026-07-10T10:00:00"))
    ids = [e["event_id"] for e in db.recent_events()]
    assert ids == ["new", "old"]


def test_recent_events_honours_the_limit(db):
    for i in range(5):
        db.save_event(_ev(event_id=f"e{i}"))
    assert len(db.recent_events(limit=2)) == 2


def test_latest_event_id_tracks_the_last_insert(db):
    assert db.latest_event_id() is None
    db.save_event(_ev(event_id="first"))
    db.save_event(_ev(event_id="second"))
    assert db.latest_event_id() == "second"


# --- the identity round-trip ----------------------------------------------
# save_event() flattens ev.identity into three columns, and _event_row_to_dict()
# rebuilds it as a NESTED dict under "identity" (not the identity_name column
# it was stored in). Callers depend on that shape, so pin it.

def test_identity_is_flattened_and_rebuilt_as_a_nested_dict(db):
    db.save_event(_ev(identity=Identity(name="Vinay", known=True,
                                        confidence=0.91)))
    got = db.get_event("e1")
    assert got["identity"]["name"] == "Vinay"
    assert got["identity"]["known"] is True
    assert got["identity"]["confidence"] == pytest.approx(0.91)


def test_unknown_visitor_round_trips_as_not_known(db):
    db.save_event(_ev(identity=Identity(name="", known=False)))
    assert db.get_event("e1")["identity"]["known"] is False


def test_people_list_survives_the_json_round_trip(db):
    db.save_event(_ev(
        people=[Person(known=True, name="Vinay", box=(0, 0, 5, 5)),
                Person(known=False, name="", box=(6, 0, 10, 5))],
        visitor_count=2, extra_unknown=1))
    got = db.get_event("e1")
    assert len(got["people"]) == 2
    assert got["people"][0]["name"] == "Vinay"
    assert got["extra_unknown"] == 1


# --- search ----------------------------------------------------------------
# recent_events(q=...) is what the History screen's search box hits.

def test_search_matches_a_known_name(db):
    db.save_event(_ev(event_id="a", identity=Identity(name="Vinay", known=True)))
    db.save_event(_ev(event_id="b", identity=Identity(name="Rahul", known=True)))
    assert [e["event_id"] for e in db.recent_events(q="vinay")] == ["a"]


def test_search_is_case_insensitive(db):
    db.save_event(_ev(announcement_text="A DELIVERY is at the door."))
    assert len(db.recent_events(q="delivery")) == 1


def test_search_matches_a_name_inside_the_people_json(db):
    """Group events store companions only in the people JSON blob — searching
    for someone who was never the primary identity must still find the visit."""
    db.save_event(_ev(people=[Person(known=True, name="Meera", box=(0, 0, 5, 5))]))
    assert len(db.recent_events(q="Meera")) == 1


def test_search_matches_a_date_prefix(db):
    db.save_event(_ev(event_id="jul", timestamp="2026-07-10T12:00:00"))
    db.save_event(_ev(event_id="aug", timestamp="2026-08-02T12:00:00"))
    assert [e["event_id"] for e in db.recent_events(q="2026-08")] == ["aug"]


def test_empty_search_returns_everything(db):
    db.save_event(_ev(event_id="a"))
    db.save_event(_ev(event_id="b"))
    assert len(db.recent_events(q="   ")) == 2


def test_search_with_no_match_returns_empty(db):
    db.save_event(_ev(identity=Identity(name="Vinay", known=True)))
    assert db.recent_events(q="zzzz") == []


# --- additive updates ------------------------------------------------------
# The background VLM enrich and /hear_visitor both write through this path.

def test_update_event_fields_fills_the_enriched_scene(db):
    db.save_event(_ev(announcement_text="Someone is at the door."))
    out = db.update_event_fields(
        "e1", scene_summary="A man holding a parcel.",
        announcement_text="Someone is at the door holding a parcel.")
    assert out is not None
    assert out["scene_summary"] == "A man holding a parcel."
    assert db.get_event("e1")["announcement_text"].endswith("a parcel.")


def test_update_event_fields_ignores_columns_outside_the_whitelist(db):
    """Only _UPDATABLE columns may be written. A stray key must be dropped,
    not raise and not corrupt the row.

    snapshot_path is the interesting case: it is a REAL column deliberately left
    out of the whitelist, so a background enrich cannot repoint a stored event at
    an arbitrary file on disk. (event_id needs no test here -- it is a positional
    parameter of update_event_fields, so it cannot be smuggled in as a field.)"""
    db.save_event(_ev(event_id="e1", snapshot_path="data/snapshots/e1.jpg"))
    db.update_event_fields("e1", snapshot_path="/etc/passwd",
                           not_a_column="x", scene_summary="ok")
    got = db.get_event("e1")
    assert got["scene_summary"] == "ok"                     # whitelisted: written
    assert got["snapshot_path"] == "data/snapshots/e1.jpg"   # not whitelisted


def test_update_event_fields_returns_none_for_unknown_id(db):
    assert db.update_event_fields("ghost", scene_summary="x") is None


def test_update_event_fields_returns_none_when_nothing_to_write(db):
    db.save_event(_ev())
    assert db.update_event_fields("e1") is None


# --- deletion --------------------------------------------------------------
# Both return snapshot paths so the server can unlink the .jpg files too.

def test_delete_event_returns_its_snapshot_path(db):
    db.save_event(_ev(snapshot_path="data/snapshots/e1.jpg"))
    assert db.delete_event("e1") == "data/snapshots/e1.jpg"
    assert db.get_event("e1") is None


def test_delete_event_returns_none_for_unknown_id(db):
    assert db.delete_event("ghost") is None


def test_clear_events_removes_all_and_returns_paths(db):
    db.save_event(_ev(event_id="a", snapshot_path="a.jpg"))
    db.save_event(_ev(event_id="b", snapshot_path="b.jpg"))
    db.save_event(_ev(event_id="c"))          # no snapshot
    paths = db.clear_events()
    assert sorted(paths) == ["a.jpg", "b.jpg"]
    assert db.recent_events() == []


def test_clearing_events_leaves_known_faces_alone(db):
    """Deleting visit history must not un-enroll the household."""
    db.known_face_add("Vinay", source_path="x.jpg")
    db.save_event(_ev())
    db.clear_events()
    assert any(r["name"] == "Vinay" for r in db.known_face_counts())
