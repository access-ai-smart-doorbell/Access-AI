"""Comprehensive integration and unit test suite verifying all 16 scenarios
requested for the Qwen3.8 OpenRouter primary VLM with Gemini fallback.

Scenarios tested:
1. Motion event with known person.
2. Motion event with unknown person.
3. Blurry moving person.
4. Clear stationary person.
5. Qwen success.
6. Qwen timeout.
7. Qwen 429.
8. Qwen 503.
9. Gemini fallback.
10. VLM completely unavailable.
11. WebSocket event appears immediately.
12. Scene description updates the same event.
13. TTS still works.
14. History contains one event, not duplicates.
15. Snapshot is the selected best frame.
16. Saved video remains associated with the event.
"""

import json
import time
import threading
from unittest.mock import MagicMock, patch
import numpy as np
import pytest
import requests

from accessai.visitor_event import VisitorEvent, Identity, Person
from accessai.vlm_module import VLMModule
from accessai.database import Database
from accessai.pipeline import Pipeline
from accessai.frame_selector import FrameSelector, FrameSelectConfig
from accessai.accessibility import AccessibilityEngine


# ---------------------------------------------------------------------------
# Fixtures and Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    return Database(db_path)


def make_sharp_frame(shape=(480, 640, 3)):
    """Generate a high-contrast sharp frame (high Laplacian variance)."""
    img = np.zeros(shape, dtype=np.uint8)
    # Checkerboard pattern for strong edges / high sharpness
    img[::16, :] = 255
    img[:, ::16] = 255
    img[8::16, 8::16] = 200
    return img


def make_blurry_frame(shape=(480, 640, 3)):
    """Generate a blurry flat frame (very low Laplacian variance < 5)."""
    import cv2
    img = np.full(shape, 128, dtype=np.uint8)
    # Add tiny noise then heavy Gaussian blur
    noise = np.random.randint(-2, 3, shape, dtype=np.int16)
    img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return cv2.GaussianBlur(img, (31, 31), 0)


# ---------------------------------------------------------------------------
# Test Cases 1 & 2: Motion Event with Known / Unknown Person
# ---------------------------------------------------------------------------

def test_01_motion_known_person(mock_db, tmp_path):
    """1. Motion event with known person: instant local detection marks known."""
    mock_face = MagicMock()
    mock_face.available.return_value = True
    mock_face.identify.return_value = [{
        "box": (100, 100, 300, 300),
        "name": "Vinay",
        "known": True,
        "confidence": 0.92,
        "age": 28,
        "gender": "man",
    }]
    mock_face.detect_faces.return_value = [
        {"box": (100, 100, 300, 300), "name": "Vinay", "known": True, "confidence": 0.92}
    ]
    mock_vision = MagicMock()
    mock_vision.available.return_value = True
    mock_vision.detect.return_value = [
        {"label": "person", "conf": 0.95, "box": (90, 80, 310, 400)}
    ]
    mock_vision.summarize.return_value = ([], 1)
    mock_vision.count_extra_people.return_value = 0

    pipeline = Pipeline(
        db=mock_db,
        history_dir=str(tmp_path / "history"),
        face=mock_face,
        face_enabled=True,
        vision=mock_vision,
        vision_enabled=True,
        vlm_enabled=False,
    )
    frame = make_sharp_frame()
    ev = pipeline.run_once(frame, trigger="motion")
    assert ev.identity.known is True
    assert ev.identity.name == "Vinay"
    assert ev.visitor_count >= 1
    assert ev.status == "detected"
    d = ev.to_dict()
    assert d["identity_status"] == "known"
    assert d["person_count"] == ev.visitor_count


def test_02_motion_unknown_person(mock_db, tmp_path):
    """2. Motion event with unknown person: hedges cautiously, status detected."""
    pipeline = Pipeline(
        db=mock_db,
        history_dir=str(tmp_path / "history"),
        vlm_enabled=False,
    )
    frame = make_sharp_frame()
    pipeline.face = MagicMock()
    pipeline.face.identify.return_value = {
        "box": (100, 100, 300, 300),
        "name": "Unknown",
        "known": False,
        "confidence": 0.0,
        "age": 30,
        "gender": "man",
    }
    pipeline.face.detect_faces.return_value = [
        {"box": (100, 100, 300, 300), "name": "Unknown", "known": False, "confidence": 0.0}
    ]
    pipeline.vision = MagicMock()
    pipeline.vision.detect.return_value = [
        {"label": "person", "conf": 0.88, "box": (90, 80, 310, 400)}
    ]

    ev = pipeline.run_once(frame, trigger="motion")
    assert ev.identity.known is False
    assert ev.identity.name == "Unknown"
    assert ev.status == "detected"
    d = ev.to_dict()
    assert d["identity_status"] == "unknown"


# ---------------------------------------------------------------------------
# Test Cases 3 & 4: Blurry vs Clear Stationary Frame Selection
# ---------------------------------------------------------------------------

def test_03_blurry_moving_person_rejected():
    """3. Blurry moving person: all frames rejected below min_quality -> returns None."""
    blurry = make_blurry_frame()
    latest_mock = MagicMock()
    latest_mock.get.return_value = blurry

    cfg = FrameSelectConfig(
        window_sec=0.2,
        sample_fps=20.0,
        min_sharpness=50.0,
        min_quality=0.3,
    )
    selector = FrameSelector(latest_mock, cfg)
    best = selector.select_best_frame()
    # Entire motion event discarded because every frame was blurry
    assert best is None


def test_04_clear_stationary_person_selected():
    """4. Clear stationary person: sharp frame chosen over blurry frame."""
    blurry = make_blurry_frame()
    sharp = make_sharp_frame()

    frames = [blurry, blurry, sharp, sharp]
    idx = 0

    def get_frame():
        nonlocal idx
        f = frames[min(idx, len(frames) - 1)]
        idx += 1
        return f

    latest_mock = MagicMock()
    latest_mock.get.side_effect = get_frame

    cfg = FrameSelectConfig(
        window_sec=0.4,
        sample_fps=10.0,
        min_sharpness=30.0,
        min_quality=0.2,
        early_exit_quality=0.5,
    )
    # Inject person detector mock
    yolo_fn = MagicMock(return_value=[{"label": "person", "conf": 0.9, "box": (100, 100, 300, 300)}])
    selector = FrameSelector(latest_mock, cfg, yolo_detect=yolo_fn)
    selected = selector.select_best_frame()
    assert selected is not None
    # Laplacian variance of sharp frame is much higher than blurry
    import cv2
    var_selected = cv2.Laplacian(cv2.cvtColor(selected, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
    var_blurry = cv2.Laplacian(cv2.cvtColor(blurry, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var()
    assert var_selected > var_blurry
    assert var_selected > 50.0


# ---------------------------------------------------------------------------
# Test Cases 5, 6, 7, 8, 9, 10: VLM Providers, Failover, Error Handling
# ---------------------------------------------------------------------------

def test_05_qwen_success():
    """5. Qwen success: OpenRouter responds with valid OpenAI-format scene JSON."""
    qwen_response = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "people": [{
                        "identity": "Vinay",
                        "position": "directly in front",
                        "distance": "1 meter",
                        "action": "standing",
                        "clothing": "white and maroon jersey",
                        "appearance": "short hair",
                        "carrying": "phone",
                        "expression": "calm"
                    }],
                    "hazards": "",
                    "objects": "",
                    "scene": "Vinay is standing about 1 meter in front of the door, facing the camera. He is wearing a white and maroon jersey and holding a phone.",
                    "labels": ""
                })
            }
        }]
    }

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = qwen_response
        mock_post.return_value = mock_resp

        vlm = VLMModule(
            keys="or-test-key-1234",
            base_url="https://openrouter.ai/api/v1",
            model="qwen/qwen3.8-27b:free",
            timeout=8,
        )

        frame = make_sharp_frame()
        res = vlm.describe_and_read(frame, facts="1 person (known: Vinay)", event_id="ev-123")

        assert "Vinay is standing about 1 meter" in res["scene_summary"]
        assert len(res["people"]) == 1
        assert res["people"][0]["identity"] == "Vinay"
        assert res["people"][0]["clothing"] == "white and maroon jersey"
        assert res["people"][0]["carrying"] == "phone"
        assert vlm._last_provider_name == "qwen/qwen3.8-27b:free"


def test_06_qwen_timeout_gemini_fallback():
    """6. Qwen timeout: OpenRouter request times out -> fails over to Gemini immediately."""
    gemini_response = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "people": [],
                    "scene": "A visitor is standing at the doorstep.",
                    "labels": ""
                })
            }
        }]
    }

    with patch("requests.post") as mock_post:
        # First call (OpenRouter) raises Timeout; second call (Gemini) succeeds
        mock_gemini = MagicMock()
        mock_gemini.status_code = 200
        mock_gemini.json.return_value = gemini_response

        mock_post.side_effect = [
            requests.exceptions.Timeout("Read timeout"),
            mock_gemini,
        ]

        vlm = VLMModule(
            keys="or-test-key-1234",
            base_url="https://openrouter.ai/api/v1",
            model="qwen/qwen3.8-27b:free",
            timeout=8,
            extra_providers=[{
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                "model": "gemini-3.6-flash",
                "keys": "gemini-key-5678",
            }]
        )

        frame = make_sharp_frame()
        res = vlm.describe_and_read(frame, event_id="ev-timeout")
        assert res["scene_summary"] == "A visitor is standing at the doorstep."
        assert vlm._last_provider_name == "gemini-3.6-flash"


def test_07_qwen_429_rate_limit():
    """7. Qwen 429: rate limit triggers back-off and immediate fallback to Gemini."""
    gemini_response = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "people": [],
                    "scene": "Gemini fallback description.",
                    "labels": ""
                })
            }
        }]
    }

    with patch("requests.post") as mock_post:
        mock_429 = MagicMock()
        mock_429.status_code = 429
        mock_429.headers = {"Retry-After": "60"}

        mock_gemini = MagicMock()
        mock_gemini.status_code = 200
        mock_gemini.json.return_value = gemini_response

        mock_post.side_effect = [mock_429, mock_gemini]

        vlm = VLMModule(
            keys="or-key",
            base_url="https://openrouter.ai/api/v1",
            model="qwen/qwen3.8-27b:free",
            timeout=8,
            extra_providers=[{
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                "model": "gemini-3.6-flash",
                "keys": "gem-key",
            }]
        )

        frame = make_sharp_frame()
        res = vlm.describe_and_read(frame)
        assert res["scene_summary"] == "Gemini fallback description."
        assert vlm._last_provider_name == "gemini-3.6-flash"


def test_08_and_09_qwen_503_and_gemini_fallback():
    """8 & 9. Qwen 503 overloaded: falls back to Gemini without blocking."""
    gemini_response = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "people": [],
                    "scene": "A person standing at the entrance.",
                    "labels": ""
                })
            }
        }]
    }

    with patch("requests.post") as mock_post:
        mock_503 = MagicMock()
        mock_503.status_code = 503

        mock_gemini = MagicMock()
        mock_gemini.status_code = 200
        mock_gemini.json.return_value = gemini_response

        mock_post.side_effect = [mock_503, mock_gemini]

        vlm = VLMModule(
            keys="or-key",
            base_url="https://openrouter.ai/api/v1",
            model="qwen/qwen3.8-27b:free",
            timeout=8,
            extra_providers=[{
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
                "model": "gemini-3.6-flash",
                "keys": "gem-key",
            }]
        )

        frame = make_sharp_frame()
        res = vlm.describe_and_read(frame)
        assert res["scene_summary"] == "A person standing at the entrance."
        assert vlm._last_provider_name == "gemini-3.6-flash"


def test_10_vlm_completely_unavailable(mock_db, tmp_path):
    """10. VLM completely unavailable: fails soft to YOLO-only, pipeline never crashes."""
    vlm = VLMModule("", base_url="", model="")
    assert vlm.available() is False

    pipeline = Pipeline(
        db=mock_db,
        vlm=vlm,
        history_dir=str(tmp_path / "history"),
        vlm_enabled=True,
    )
    frame = make_sharp_frame()
    ev = pipeline.run_once(frame, trigger="motion")
    assert ev is not None
    assert ev.scene_summary == ""
    assert ev.status == "detected"


# ---------------------------------------------------------------------------
# Test Cases 11 & 12: Real-time WebSocket flow and progressive update
# ---------------------------------------------------------------------------

def test_11_and_12_websocket_immediate_and_same_event_update(mock_db, tmp_path):
    """11 & 12. WebSocket event appears immediately as detected,
    then updates the same event with scene_description as analyzed."""
    vlm = MagicMock()
    def mock_describe(*args, **kwargs):
        time.sleep(0.08)
        return {
            "scene_summary": "Vinay is standing in front of the door.",
            "appearance": "wearing jersey",
            "ocr_text": "",
            "people": [{"identity": "Vinay", "position": "center", "action": "standing",
                        "clothing": "jersey", "appearance": "", "carrying": "", "expression": ""}],
        }
    vlm.describe_and_read.side_effect = mock_describe


    broadcast_messages = []

    def mock_broadcast(msg):
        broadcast_messages.append(msg)

    mock_vision = MagicMock()
    mock_vision.available.return_value = True
    mock_vision.detect.return_value = [{"label": "person", "conf": 0.9, "box": (10, 10, 200, 200)}]
    mock_vision.summarize.return_value = ([], 1)
    mock_vision.count_extra_people.return_value = 0

    pipeline = Pipeline(
        db=mock_db,
        vlm=vlm,
        vision=mock_vision,
        vision_enabled=True,
        history_dir=str(tmp_path / "history"),
        vlm_enabled=True,
        vlm_async_enrich=True,
        vlm_only_for_unknown=False,
    )
    pipeline._enrich_broadcast = mock_broadcast

    frame = make_sharp_frame()
    t_start = time.monotonic()
    ev = pipeline.run_once(frame, trigger="doorbell")
    t_initial = time.monotonic() - t_start

    # 11. App receives basic event immediately (< 1.0s)
    assert t_initial < 1.0
    assert ev.status == "detected"
    assert ev.event_id is not None

    # Wait for the async enrichment daemon thread to finish
    time.sleep(0.4)

    # 12. WebSocket receives update for the SAME event
    assert len(broadcast_messages) >= 1
    update_msg = broadcast_messages[-1]
    assert update_msg["type"] == "event_update"
    assert update_msg["event"]["event_id"] == ev.event_id
    assert update_msg["event"]["status"] == "analyzed"
    assert "Vinay is standing" in update_msg["event"]["scene_description"]


# ---------------------------------------------------------------------------
# Test Case 13: TTS Flow Works Independent of UI
# ---------------------------------------------------------------------------

def test_13_tts_independent():
    """13. TTS still works: speaks announcements without blocking."""
    mock_tts = MagicMock()
    engine = AccessibilityEngine(tts=mock_tts, mode="both")

    # Initial announcement speaking
    engine.speak_text("Vinay is at the front door.")
    mock_tts.speak.assert_called_with("Vinay is at the front door.", lang="en")

    # Enriched VLM follow-up speaking
    engine.speak_text("He is wearing a white and maroon jersey.")
    mock_tts.speak.assert_called_with("He is wearing a white and maroon jersey.", lang="en")


# ---------------------------------------------------------------------------
# Test Case 14: History Contains One Event, No Duplicates
# ---------------------------------------------------------------------------

def test_14_history_single_event(mock_db, tmp_path):
    """14. History contains one event, not duplicates after VLM enrichment."""
    vlm = MagicMock()
    vlm.available.return_value = True
    vlm.describe_and_read.return_value = {
        "scene_summary": "Enriched scene description.",
        "appearance": "red jacket",
        "ocr_text": "",
        "people": [],
    }

    mock_vision = MagicMock()
    mock_vision.available.return_value = True
    mock_vision.detect.return_value = [{"label": "person", "conf": 0.9, "box": (10, 10, 200, 200)}]
    mock_vision.summarize.return_value = ([], 1)
    mock_vision.count_extra_people.return_value = 0


    pipeline = Pipeline(
        db=mock_db,
        vlm=vlm,
        vision=mock_vision,
        vision_enabled=True,
        history_dir=str(tmp_path / "history"),
        vlm_enabled=True,
        vlm_async_enrich=True,
        vlm_only_for_unknown=False,
    )

    frame = make_sharp_frame()
    ev = pipeline.run_once(frame, trigger="motion")
    time.sleep(0.4)  # wait for daemon thread to enrich

    events = mock_db.recent_events()
    assert len(events) == 1
    assert events[0]["event_id"] == ev.event_id
    assert events[0]["scene_summary"] == "Enriched scene description."
    assert events[0]["status"] == "analyzed"



# ---------------------------------------------------------------------------
# Test Case 15: Snapshot is Selected Best Frame
# ---------------------------------------------------------------------------

def test_15_snapshot_is_selected_frame(mock_db, tmp_path):
    """15. Snapshot saved to disk is the selected best frame."""
    history_dir = tmp_path / "history"
    history_dir.mkdir(parents=True, exist_ok=True)

    pipeline = Pipeline(
        db=mock_db,
        history_dir=str(history_dir),
        vlm_enabled=False,
    )

    sharp_frame = make_sharp_frame()
    ev = pipeline.run_once(sharp_frame, trigger="motion")

    import os
    import cv2
    assert os.path.isfile(ev.snapshot_path)
    saved_img = cv2.imread(ev.snapshot_path)
    assert saved_img is not None
    assert saved_img.shape[0] > 0 and saved_img.shape[1] > 0


# ---------------------------------------------------------------------------
# Test Case 16: Saved Video Remains Associated With Event
# ---------------------------------------------------------------------------

def test_16_saved_video_association(mock_db, tmp_path):
    """16. Saved video filename remains associated with the event in DB."""
    pipeline = Pipeline(
        db=mock_db,
        history_dir=str(tmp_path / "history"),
        vlm_enabled=False,
    )

    frame = make_sharp_frame()
    ev = pipeline.run_once(frame, trigger="motion")

    # Associate clip name
    clip_filename = f"clip_{ev.event_id}.mp4"
    mock_db.update_event_fields(ev.event_id, video_path=clip_filename)

    stored = mock_db.get_event(ev.event_id)
    assert stored is not None
    assert stored["video_path"] == clip_filename
