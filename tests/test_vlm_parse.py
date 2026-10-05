"""PURE-logic tests for VLMModule._parse / _salvage_field.

The parser is the last line of defence between the cloud model's raw reply and
what gets SPOKEN to a blind user. The key guarantee pinned here: raw or
truncated JSON must never come back as scene_summary — a broken reply is either
salvaged into its human-readable fields or dropped to "". No network, no cv2.
"""

from accessai.vlm_module import VLMModule


def test_parse_good_json():
    out = VLMModule._parse(
        '{"people": [{"appearance": "red shirt", "carrying": "a box", '
        '"expression": "neutral"}], '
        '"appearance": "A person in a red shirt.", '
        '"scene": "Appears to be a delivery: holding a box.", '
        '"labels": "Amazon"}')
    assert out["scene_summary"] == "Appears to be a delivery: holding a box."
    assert out["appearance"] == "A person in a red shirt."
    assert out["ocr_text"] == "Amazon"
    assert len(out["people"]) == 1
    p0 = out["people"][0]
    assert p0["appearance"] == "red shirt"
    assert p0["carrying"] == "a box"
    assert p0["expression"] == "neutral"


def test_parse_fenced_json():
    out = VLMModule._parse(
        '```json\n{"scene": "One person at the door.", "appearance": "", '
        '"labels": "", "people": []}\n```')
    assert out["scene_summary"] == "One person at the door."


def test_parse_plain_prose_is_scene():
    out = VLMModule._parse("A person appears to be standing at the door.")
    assert out["scene_summary"] == ("A person appears to be standing at the "
                                    "door.")


def test_parse_truncated_json_salvages_never_leaks():
    """The exact failure the user hit: a 6-person reply cut off mid-string.
    The parser must salvage the readable fields and NEVER return raw JSON."""
    truncated = (
        '{ "people": [ { "appearance": "light-colored headscarf, appears to '
        'be an adult", "carrying": "", "expression": "appears to be smiling" '
        '} ], "appearance": "A person in a light-colored headscarf, sitting '
        'on the far left'                     # <- cut mid-string, no close
    )
    out = VLMModule._parse(truncated)
    assert '{"' not in out["scene_summary"] and '{ "' not in out["scene_summary"]
    assert out["appearance"].startswith("A person in a light-colored headscarf")


def test_parse_truncated_json_with_scene_present():
    truncated = ('{"scene": "Appears to be a group of people sitting '
                 'together.", "appearance": "A person in a headsc')
    out = VLMModule._parse(truncated)
    assert out["scene_summary"] == ("Appears to be a group of people sitting "
                                    "together.")
    assert out["appearance"] == "A person in a headsc"


def test_salvage_field_missing_key_is_empty():
    assert VLMModule._salvage_field('{"scene": "x"}', "labels") == ""


def test_parse_rich_vlm_fields():
    raw_json = '''{
      "people": [
        {
          "identity": "Vinay",
          "position": "center",
          "distance": "about 1 meter",
          "action": "standing looking at camera",
          "clothing": "white Spain national team away jersey with maroon stripes",
          "hairstyle": "fade with black hair",
          "appearance": "light stubble and mustache",
          "hands": "empty at sides",
          "build": "slim build",
          "age_group": "young adult",
          "footwear": "white sneakers",
          "accessories": "silver watch",
          "carrying": "",
          "expression": "calm"
        }
      ],
      "hazards": "none",
      "objects": "none",
      "environment": "indoor hallway with ceiling lighting and plain white wall",
      "scene": "Vinay is standing directly in front of the camera.",
      "labels": ""
    }'''
    out = VLMModule._parse(raw_json)
    assert len(out["people"]) == 1
    p = out["people"][0]
    assert p["identity"] == "Vinay"
    assert p["clothing"] == "white Spain national team away jersey with maroon stripes"
    assert p["hairstyle"] == "fade with black hair"
    assert p["appearance"] == "light stubble and mustache"
    assert p["hands"] == "empty at sides"
    assert p["build"] == "slim build"
    assert p["age_group"] == "young adult"
    assert p["footwear"] == "white sneakers"
    assert p["accessories"] == "silver watch"
    assert "indoor hallway" in out["scene_summary"]

