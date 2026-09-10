"""
VLMModule - cloud Vision-Language scene description + OCR (Phase 6).

Sends a single doorbell frame to a GitHub Models OpenAI-compatible Chat
Completions endpoint (vision) and gets back, in ONE call:

    * a short, conservative scene sentence  -> ev.scene_summary
    * any visible text / parcel-label text  -> ev.ocr_text

Why cloud (for now): the target dev laptop is 8 GB / CPU-only, and a good local
VLM won't fit. GitHub Models gives a free, OpenAI-compatible vision endpoint. A
heavy LOCAL VLM can be dropped in later behind this exact interface (available()
+ describe_and_read()) without touching the pipeline.

Design rules honoured here (same as every AccessAI module):
  * NEVER raise from __init__ - a missing `requests`, missing keys, or a dead
    network degrades to available()==False and empty results. The pipeline then
    simply runs on YOLO-only signals.
  * Multiple API keys with automatic FAILOVER (429 rate-limit / 401 / 403 /
    network error -> try the next key). Remembers the last good key.
  * NEVER log a full key. Keys are masked to their last 4 chars everywhere.
  * Language stays CONSERVATIVE: the prompt tells the model to say "appears to
    be" / "likely" and to never guess a person's identity.
"""

import base64
import json
import re

try:
    import requests
    _HAS_REQUESTS = True
except Exception as e:                                   # pragma: no cover
    _HAS_REQUESTS = False
    print(f"[VLMModule] 'requests' not available, VLM disabled: {e}")

try:
    import cv2
    _HAS_CV2 = True
except Exception as e:                                   # pragma: no cover
    _HAS_CV2 = False
    print(f"[VLMModule] OpenCV not available, VLM disabled: {e}")


# The single combined instruction. One call returns BOTH scene + labels as JSON
# so we spend only ONE request against the rate-limited free tier per visitor.
#
# SEMANTIC REASONING (Bug-4 upgrade): the model is framed as "a blind person's
# eyes" and told exactly WHICH observations matter at a front door — the
# Who / Where / What-doing / What-carrying / How-interacting questions — so the
# scene sentence carries real situational meaning ("appears to be a delivery:
# holding a box with an Amazon label, wearing a courier uniform") instead of a
# flat caption ("a person is standing outside"). The JSON SHAPE is unchanged so
# the pipeline parser keeps working; only the content got smarter.
_SYSTEM_PROMPT = (
    "You are the eyes of a blind person, describing their doorbell camera. "
    "Your job is to answer, from the image alone: who is there, where they are "
    "standing, what they appear to be doing, what they are carrying, and how "
    "they are interacting with the door. Report ONLY observable facts, in "
    "cautious language ('appears to be', 'likely', 'unable to determine'). "
    "HARD RULES: never state a person's identity, exact age, gender, or race — "
    "at most a broad hedged impression (e.g. 'appears to be an adult'). Never "
    "attribute emotions or intent as fact: say 'appears to be smiling', never "
    "'is happy'; describe behaviour ('standing close to the camera', 'looking "
    "around'), never judgements like 'suspicious' or 'criminal'. Never claim "
    "certainty about anything you cannot clearly see."
)
_USER_PROMPT = (
    "Look at this doorbell camera image as a blind resident's eyes. Describe "
    "EACH visible person separately. Respond with STRICT JSON only, no "
    "markdown, exactly this shape:\n"
    '{"people": [ {'
    '"appearance": "<brief, cautious phrase covering what is visible of: hair, '
    "clothing type + main colours, any uniform/company branding, accessories "
    "(cap, helmet, glasses, ID badge, mask), and broad age impression only if "
    "clear (e.g. 'appears to be an adult'); e.g. 'short dark hair, red courier "
    "uniform with a Zomato logo, wearing a helmet'>\", "
    '"carrying": "<anything this person is carrying or holding — parcel, box, '
    "envelope, food bag, tool, phone, clipboard, umbrella; empty string if "
    'nothing>", '
    '"expression": "<one cautious phrase for apparent expression, e.g. '
    "'appears to be smiling', 'neutral'; empty string if the face is not "
    'clearly visible>" } ], '
    '"appearance": "<one short, cautious sentence describing the MOST prominent '
    "person's visible clothing (type and colours), any uniform or company "
    "branding, and anything they are carrying; use 'appears to be' / 'looks "
    'like\'; empty string if no person is visible>", '
    '"scene": "<2-3 short sentences for a blind listener, in this order of '
    "importance: (1) what the person appears to be DOING and their likely "
    "purpose WITH the visible reason — e.g. 'Appears to be a delivery: holding "
    "a box with a shipping label and wearing a courier uniform' — (2) WHERE "
    "they are: near the door or far, facing the camera or turned away, at the "
    "gate, on a vehicle; (3) anything else a blind resident should know: a "
    "waiting vehicle, a second person further back, rain or darkness, an "
    'object left at the door>", '
    '"labels": "<any text visible on uniforms, clothing, boxes, vehicles, or '
    "parcel/shipping labels, verbatim — courier and shop names (Amazon, "
    'Flipkart, Swiggy, Zomato...) are especially important; empty if none>"}\n'
    "Order the \"people\" array strictly LEFT TO RIGHT as the people appear in "
    "the image, one object per person. Describe ONLY what is clearly visible: "
    "if a detail is unclear, use an empty string instead of guessing. Do NOT "
    "invent people, objects, vehicles, or brand names you cannot actually see "
    "or read. Never state identity, exact age, race, or emotion as fact; stay "
    "cautious; never say 'definitely'."
)


def _facts_preamble(facts: str) -> str:
    """Wrap on-device detector facts as an authoritative ground-truth preamble.

    The local detectors (face recognition + YOLO) are far more reliable than the
    VLM at COUNTING and IDENTIFYING people, so we hand the model those counts as
    ground truth to stop it hallucinating extra people / swapping identities.
    Returns "" when there is nothing to ground on (caller then sends the base
    prompt unchanged)."""
    facts = (facts or "").strip()
    if not facts:
        return ""
    return (
        "GROUND TRUTH from on-device detectors (trust this over your own count; "
        "do NOT contradict it and do NOT invent extra people): "
        f"{facts}\n\n"
    )


class VLMModule:
    def __init__(self, keys, *, base_url, model="gpt-4o-mini", timeout=20,
                 max_tokens=300, temperature=0.2, jpeg_quality=80,
                 max_image_width=768):
        # Accept a list OR a comma-separated string; strip blanks either way.
        if isinstance(keys, str):
            keys = keys.split(",")
        self._keys = [k.strip() for k in (keys or []) if k and k.strip()]

        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.timeout = float(timeout)
        self.max_tokens = int(max_tokens)
        self.temperature = float(temperature)
        self.jpeg_quality = int(jpeg_quality)
        self.max_image_width = int(max_image_width)

        # Failover bookkeeping (safe to expose via /vlm_status).
        self._last_good = 0
        self._last_error = ""
        self._last_status = None

        self._ready = bool(_HAS_REQUESTS and _HAS_CV2 and self._keys
                           and self.base_url)
        if not self._ready:
            why = (
                "no 'requests'" if not _HAS_REQUESTS else
                "no OpenCV" if not _HAS_CV2 else
                "no API keys (set GROQ_API_KEY or OPENAI_API_KEY in .env)" if not self._keys
                else "no base_url"
            )
            print(f"[VLMModule] Not ready ({why}); scene/OCR will be skipped "
                  f"(fail-soft, pipeline continues on YOLO-only).")
        else:
            print(f"[VLMModule] Ready: model={self.model}, "
                  f"{len(self._keys)} key(s) {self.masked_keys()}, "
                  f"base={self.base_url}")

    # ------------------------------------------------------------------ status
    def available(self) -> bool:
        return self._ready

    def key_count(self) -> int:
        return len(self._keys)

    def masked_keys(self):
        """Last-4-only view of every key, for safe logging / status."""
        return [f"...{k[-4:]}" if len(k) >= 4 else "****" for k in self._keys]

    def status(self) -> dict:
        return {
            "available": self._ready,
            "model": self.model,
            "base_url": self.base_url,
            "key_count": self.key_count(),
            "keys_masked": self.masked_keys(),
            "last_good_index": self._last_good,
            "last_status": self._last_status,
            "last_error": self._last_error,
        }

    # ------------------------------------------------------------------ encode
    def _encode(self, frame_bgr):
        """Resize + JPEG-encode a BGR frame into a base64 data URL, or None."""
        if frame_bgr is None or not _HAS_CV2:
            return None
        try:
            img = frame_bgr
            h, w = img.shape[:2]
            if w > self.max_image_width:
                scale = self.max_image_width / float(w)
                img = cv2.resize(img, (self.max_image_width, int(h * scale)),
                                 interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", img,
                                   [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
            if not ok:
                return None
            b64 = base64.b64encode(buf.tobytes()).decode("ascii")
            return f"data:image/jpeg;base64,{b64}"
        except Exception as e:                            # pragma: no cover
            self._last_error = f"encode failed: {e}"
            return None

    # -------------------------------------------------------------------- chat
    def _chat(self, data_url, facts=""):
        """POST one VISION chat completion (system+user+image), failing over.

        `facts` is an optional ground-truth string from the on-device detectors
        (person count, known names, YOLO objects); when present it is prepended to
        the user prompt so the model does not re-count or re-identify people.

        Returns the assistant message text on success, or None if EVERY key
        failed. Never raises.
        """
        if data_url is None:
            return None
        user_text = _facts_preamble(facts) + _USER_PROMPT
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ]
        # A crowded frame (5-6 people) needs far more JSON than one visitor -
        # a 300-token cap truncated it mid-string, the parse failed, and raw
        # JSON leaked into the spoken announcement. max_tokens is a CAP, not a
        # target, so the headroom costs nothing on a normal one-person ring.
        # The longer worst-case reply also needs more time than the default
        # 12s before we declare the key dead and fail over pointlessly.
        return self._post(messages, max_tokens=max(self.max_tokens, 700),
                          timeout=max(self.timeout, 20))

    def _post(self, messages, max_tokens=None, timeout=None):
        """POST a chat completion with the given `messages`, failing over across
        keys. Works for BOTH vision (image content) and text-only payloads.

        `max_tokens` / `timeout` override the defaults (the Level-2 detailed
        visitor report needs more room AND more time than the one-line scene
        call - 500 tokens can take longer than the default 12s to generate).

        Returns the assistant message text on success, or None if EVERY key
        failed. Never raises. Rotates key order so we start from the last-good.
        """
        if not self._ready or not messages:
            return None

        request_timeout = float(timeout or self.timeout)
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": int(max_tokens or self.max_tokens),
            "temperature": self.temperature,
        }
        url = f"{self.base_url}/chat/completions"

        n = len(self._keys)
        # Start at the last key that worked, then wrap around the rest.
        order = [(self._last_good + i) % n for i in range(n)]
        for idx in order:
            key = self._keys[idx]
            masked = f"...{key[-4:]}" if len(key) >= 4 else "****"
            headers = {"Authorization": f"Bearer {key}",
                       "Content-Type": "application/json"}
            try:
                r = requests.post(url, headers=headers, json=body,
                                  timeout=request_timeout)
            except Exception as e:                        # network / timeout
                self._last_status = None
                self._last_error = f"key {masked}: network error: {e}"
                print(f"[VLMModule] key {masked} network error, failing over: {e}")
                continue

            self._last_status = r.status_code
            if r.status_code == 200:
                try:
                    content = r.json()["choices"][0]["message"]["content"]
                except Exception as e:                    # pragma: no cover
                    self._last_error = f"key {masked}: bad response shape: {e}"
                    print(f"[VLMModule] key {masked} returned an unexpected "
                          f"body, failing over: {e}")
                    continue
                # Strip Qwen-style <think>...</think> reasoning blocks. Some
                # models (e.g. qwen3.6) prepend internal chain-of-thought
                # wrapped in these tags; leaving them in breaks JSON parsing
                # and leaks raw reasoning into spoken announcements. Handle
                # both closed tags and unclosed ones (truncated by max_tokens).
                content = re.sub(r"<think>.*?</think>", "", content,
                                 flags=re.DOTALL).strip()
                # If <think> was opened but never closed (truncated), drop it.
                if "<think>" in content:
                    content = content.split("<think>")[0].strip()
                self._last_good = idx           # remember the winner
                self._last_error = ""
                return content

            # 429 rate-limit / 401 / 403 auth / 5xx -> try the next key.
            self._last_error = f"key {masked}: HTTP {r.status_code}"
            print(f"[VLMModule] key {masked} HTTP {r.status_code}, failing over.")

        print("[VLMModule] All keys failed; returning empty (YOLO-only fallback).")
        return None

    # --------------------------------------------------------------- high level
    def describe_and_read(self, frame_bgr, facts="") -> dict:
        """PREFERRED entry point: one call ->
        {scene_summary, appearance, ocr_text, people}.

        Always returns a dict; on any failure all fields are ""/[] so callers can
        write them onto the event unconditionally. `appearance` (Phase 12) is the
        cautious clothing/uniform/carried line for the primary UNKNOWN visitor;
        `people` (Phase 15) is a LIST of per-person {appearance, carrying,
        expression} dicts so a whole GROUP can be described from the one call.

        `facts` (Phase 16 correctness): optional ground-truth from the on-device
        detectors (e.g. "2 people (1 known: Alex, 1 unknown); objects: backpack").
        Passing it stops the VLM inventing extra people or swapping identities.
        """
        empty = {"scene_summary": "", "appearance": "", "ocr_text": "",
                 "people": []}
        content = self._chat(self._encode(frame_bgr), facts=facts)
        if not content:
            return empty
        return self._parse(content)

    # --------------------------------------------------------- free-form Q&A
    def answer_question(self, frame_bgr, question, grounding="") -> str:
        """Phase 16: answer a free-form spoken question about the CURRENT frame.

        Reuses the SAME image encoding + multi-key failover as describe_and_read;
        adds NO dependency and does NOT touch torch. `grounding` is an optional
        ground-truth hint (person count / known names / detected objects) that the
        model must not contradict. Returns a short hedged answer, or "" on any
        failure (the caller then speaks a polite fallback). Never raises.
        """
        question = (question or "").strip()
        if not self._ready or not question:
            return ""
        data_url = self._encode(frame_bgr)
        if data_url is None:
            return ""
        system = (
            "You are the eyes of a blind or deaf resident, answering their "
            "question about what their doorbell camera sees RIGHT NOW. Answer "
            "using ONLY what is actually visible in the image. Be brief (one "
            "or two spoken-style sentences) and directly address the question "
            "first, then add at most one closely-related visible detail if it "
            "helps (e.g. asked about a parcel, mention the courier logo on it). "
            "Stay cautious: 'appears to be' / 'likely' / 'unable to determine'. "
            "Never state a person's identity, exact age, gender, or race; never "
            "state emotion or intent as fact — 'appears to be smiling', never "
            "'is happy'; never call anyone suspicious. If the answer is not "
            "visible in the image, say plainly that you cannot tell, rather "
            "than guessing."
        )
        hint = ""
        g = (grounding or "").strip()
        if g:
            hint = ("Known facts from on-device detectors (do not contradict): "
                    f"{g}\n\n")
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "text", "text": hint + "Question: " + question},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ]
        out = self._post(messages)
        return (out or "").strip()

    def detailed_report(self, frame_bgr, facts="") -> str:
        """Level-2 of the semantic reasoning engine: a full spoken visitor
        report, on request ("hey access, give me the details").

        Level 1 is the instant pipeline alert (who + headline, 2-3 s). Level 3
        is answer_question (interactive follow-ups). This sits between them: one
        structured walk through everything observable — people, position,
        activity, carried objects, clothing/uniform, delivery clues, vehicle,
        context — as flowing sentences ready for TTS, not JSON. Same encoding,
        keys and failover as every other call; returns "" on any failure.
        """
        if not self._ready:
            return ""
        data_url = self._encode(frame_bgr)
        if data_url is None:
            return ""
        system = (
            "You are the eyes of a blind resident, giving a detailed spoken "
            "report of their doorbell camera view. Write 4-7 short spoken-style "
            "sentences (no lists, no headings, no markdown) covering, in this "
            "order, whatever is actually visible: how many people and where "
            "they are standing (close to the door / at the gate / far away, "
            "facing the camera or turned away); what each appears to be doing; "
            "what they are carrying; clothing and any uniform, logo or company "
            "branding (courier names like Amazon, Flipkart, Swiggy, Zomato are "
            "especially important); accessories such as a helmet, cap, ID badge "
            "or mask; any vehicle waiting; any readable text on parcels or "
            "clothing; and the surroundings (darkness, rain, an object left at "
            "the door). Finish with ONE sentence of overall impression WITH its "
            "visible reason, e.g. 'Overall this appears to be a food delivery, "
            "because of the insulated bag and the Swiggy logo.' Skip anything "
            "not visible — never pad or invent. Stay cautious: 'appears to be' "
            "/ 'likely' / 'unable to determine'. Never state identity, exact "
            "age, gender, or race; at most 'appears to be an adult'. Never "
            "state emotion or intent as fact — 'appears to be smiling', never "
            "'is happy'; never call anyone suspicious or dangerous."
        )
        user_text = (_facts_preamble(facts)
                     + "Give the detailed spoken report of this doorbell view.")
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]},
        ]
        # The report legitimately needs more room AND time than the scene call.
        out = self._post(messages, max_tokens=max(self.max_tokens, 500),
                         timeout=max(self.timeout, 25))
        return (out or "").strip()

    def describe_scene(self, frame_bgr) -> str:
        """Scene sentence only (thin view over the combined call)."""
        return self.describe_and_read(frame_bgr).get("scene_summary", "")

    def read_labels(self, frame_bgr) -> str:
        """Visible label/OCR text only (thin view over the combined call)."""
        return self.describe_and_read(frame_bgr).get("ocr_text", "")

    # --------------------------------------------------------- text translation
    def translate_text(self, text, target_language_name) -> str:
        """Phase 8: translate `text` into `target_language_name` via a TEXT-ONLY
        chat completion, reusing the SAME multi-key failover as vision calls.

        Returns the translation on success, or "" on any failure (the caller then
        falls back to the original text). Never raises. Adds NO new dependency and
        does NOT touch torch - the whole point of reusing the Phase-6 keys.
        """
        text = (text or "").strip()
        if not self._ready or not text or not target_language_name:
            return ""
        messages = [
            {"role": "system", "content": (
                "You are a translation engine. Translate the user's message into "
                f"{target_language_name}. Output ONLY the translated text, with no "
                "quotes, notes, or explanation.")},
            {"role": "user", "content": text},
        ]
        out = self._post(messages)
        return (out or "").strip()

    # ------------------------------------------------------------------ parse
    @staticmethod
    def _parse(content: str) -> dict:
        """Pull {scene, appearance, labels, people} out of the model's reply,
        tolerating stray markdown fences or prose around the JSON."""
        text = (content or "").strip()
        # Strip ```json ... ``` fences if the model added them.
        if text.startswith("```"):
            text = text.strip("`")
            if text[:4].lower() == "json":
                text = text[4:]
            text = text.strip()
        scene, appearance, labels, people = "", "", "", []
        try:
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end != -1 and end > start:
                obj = json.loads(text[start:end + 1])
                scene = str(obj.get("scene", "") or "").strip()
                appearance = str(obj.get("appearance", "") or "").strip()
                labels = str(obj.get("labels", "") or "").strip()
                people = VLMModule._parse_people(obj.get("people"))
            elif start == -1:
                scene = text          # plain prose -> treat whole reply as scene
            # else: JSON started but never closed (truncated) -> salvage below
        except Exception:
            pass                      # malformed JSON -> salvage below
        if not scene and not appearance and text.find("{") != -1:
            # The reply was (broken) JSON. NEVER speak raw JSON to a blind
            # user - fish the human-readable fields out with a regex instead.
            scene = VLMModule._salvage_field(text, "scene")
            appearance = appearance or VLMModule._salvage_field(text,
                                                                "appearance")
            labels = labels or VLMModule._salvage_field(text, "labels")
        return {"scene_summary": scene, "appearance": appearance,
                "ocr_text": labels, "people": people}

    @staticmethod
    def _salvage_field(text: str, key: str) -> str:
        """Best-effort extraction of one string field from broken/truncated
        JSON. Matches `"key": "value..."` even when the closing quote or brace
        never arrived. Takes the LAST occurrence — per-person entries inside
        "people" reuse the "appearance" key, and the top-level field we want
        comes after that array. Returns "" when the key isn't found."""
        hits = re.findall(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)' % re.escape(key),
                          text)
        if not hits:
            return ""
        val = hits[-1]
        if "\\" in val:
            try:
                val = val.encode().decode("unicode_escape")
            except Exception:
                pass
        return val.strip()

    @staticmethod
    def _parse_people(raw) -> list:
        """Normalise the model's "people" array into a clean list of dicts.

        Each entry becomes {appearance, carrying, expression} of stripped strings.
        Anything malformed is dropped defensively so a bad element can never break
        the whole parse (the group description just falls back to face-only data).
        """
        out = []
        if not isinstance(raw, list):
            return out
        for item in raw:
            if not isinstance(item, dict):
                continue
            out.append({
                "appearance": str(item.get("appearance", "") or "").strip(),
                "carrying": str(item.get("carrying", "") or "").strip(),
                "expression": str(item.get("expression", "") or "").strip(),
            })
        return out
