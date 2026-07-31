"""Security-layer tests for the FastAPI app (Phase 17).

These cover the middleware that decides who may talk to the doorbell: bearer
auth, the public-path allowlist, and the per-IP rate limiter. That logic is
worth pinning because both of its failure directions are bad and neither is
loud -- too strict locks the household out of their own door, too loose leaves
a camera on the LAN open to anyone.

`make_app` takes every collaborator by keyword, so the whole app is built here
with fakes: no camera, no models, no network. `cfg` is a plain namespace, which
is all the server reads it as.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from accessai.server import make_app, LatestFrame, _TokenBucket


class _FakeDb:
    """Only the reads the tested routes touch."""

    def recent_events(self, limit=50, q=""):
        return []

    def get_event(self, event_id):
        return None

    def known_face_counts(self):
        return []


def _client(tmp_path, **cfg_kw):
    """An app wired to fakes. cfg defaults to auth OFF, matching the shipped
    default (an open appliance) unless a test asks otherwise.

    Defaults are merged into a dict FIRST so a keyword like AUTH_TOKEN overrides
    its default instead of colliding with it -- passing it straight to
    SimpleNamespace(AUTH_TOKEN="", **cfg_kw) is a TypeError, not an override."""
    defaults = {"AUTH_TOKEN": "", "RING_HMAC_SECRET": "", "CORS_ORIGINS": ["*"],
                "RATE_PER_MIN": 12, "RATE_BURST": 4}
    cfg = SimpleNamespace(**{**defaults, **cfg_kw})
    web_dir = tmp_path / "web"
    web_dir.mkdir(exist_ok=True)
    app = make_app(pipeline=None, latest=LatestFrame(), db=_FakeDb(),
                   web_dir=str(web_dir), history_dir=str(tmp_path / "snaps"),
                   cfg=cfg)
    return TestClient(app)


# --- auth OFF (the default appliance) --------------------------------------

def test_history_is_open_when_no_token_is_configured(tmp_path):
    r = _client(tmp_path).get("/history")
    assert r.status_code == 200


# --- auth ON ---------------------------------------------------------------

def test_protected_route_401s_without_a_token(tmp_path):
    r = _client(tmp_path, AUTH_TOKEN="s3cret").get("/history")
    assert r.status_code == 401
    assert r.json()["error"] == "unauthorized"


def test_bearer_header_is_accepted(tmp_path):
    c = _client(tmp_path, AUTH_TOKEN="s3cret")
    r = c.get("/history", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200


def test_bearer_prefix_is_case_insensitive(tmp_path):
    c = _client(tmp_path, AUTH_TOKEN="s3cret")
    assert c.get("/history",
                 headers={"Authorization": "bearer s3cret"}).status_code == 200


def test_query_token_is_accepted(tmp_path):
    """The ?token= form exists for <img src> MJPEG and the WebSocket, where a
    custom header is impossible. If this breaks, the live view goes black."""
    c = _client(tmp_path, AUTH_TOKEN="s3cret")
    assert c.get("/history?token=s3cret").status_code == 200


def test_a_wrong_token_is_rejected(tmp_path):
    c = _client(tmp_path, AUTH_TOKEN="s3cret")
    assert c.get("/history?token=wrong").status_code == 401


def test_a_token_prefix_is_rejected(tmp_path):
    """Guards against a truncating/startswith comparison sneaking in."""
    c = _client(tmp_path, AUTH_TOKEN="s3cret")
    assert c.get("/history?token=s3c").status_code == 401


def test_empty_supplied_token_is_rejected(tmp_path):
    c = _client(tmp_path, AUTH_TOKEN="s3cret")
    assert c.get("/history?token=").status_code == 401


# --- the public allowlist --------------------------------------------------
# The UI shells must load WITHOUT a token so the user has somewhere to type it.

@pytest.mark.parametrize("path", ["/", "/app", "/favicon.ico"])
def test_ui_shells_stay_reachable_with_auth_on(tmp_path, path):
    c = _client(tmp_path, AUTH_TOKEN="s3cret")
    # Not 401 is the claim -- 404 is fine here (no index.html in the fake
    # web dir); what matters is that the security middleware let it through.
    assert c.get(path).status_code != 401


def test_a_data_route_is_not_treated_as_public(tmp_path):
    """The allowlist is prefix-based; make sure it can't be walked into."""
    c = _client(tmp_path, AUTH_TOKEN="s3cret")
    assert c.get("/known").status_code == 401


# --- the rate limiter (unit-level, no HTTP) --------------------------------
# _TokenBucket is pure and deterministic, so it is tested directly rather than
# by hammering an endpoint (which would make the suite slow and flaky).

def test_bucket_allows_up_to_the_burst_then_denies():
    b = _TokenBucket(per_min=12, burst=3)
    assert [b.allow("1.2.3.4") for _ in range(3)] == [True, True, True]
    assert b.allow("1.2.3.4") is False


def test_bucket_is_per_ip():
    """One noisy client must not lock out the rest of the household."""
    b = _TokenBucket(per_min=12, burst=2)
    assert b.allow("1.1.1.1") and b.allow("1.1.1.1")
    assert b.allow("1.1.1.1") is False
    assert b.allow("2.2.2.2") is True


def test_bucket_refills_over_time():
    b = _TokenBucket(per_min=60, burst=1)   # 1 token/second
    assert b.allow("1.2.3.4") is True
    assert b.allow("1.2.3.4") is False
    # Rewind this IP's last-seen timestamp by two seconds instead of sleeping,
    # so the refill maths runs for real without costing the suite two seconds.
    tokens, last = b._state["1.2.3.4"]
    b._state["1.2.3.4"] = (tokens, last - 2.0)
    assert b.allow("1.2.3.4") is True
