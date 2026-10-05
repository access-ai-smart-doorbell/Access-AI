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
import time

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


# ─────────────────────────────────────────────────────────────────────────────
# VLM Prompts — AccessAI Structured Scene Description
#
# Design principle: a visually impaired user needs NAVIGATION-ORIENTED output,
# not a photographic caption. Priority order:
#   1. Immediate hazards / obstacles in the user's path
#   2. Number of people and their positions (left / center / right / near / far)
#   3. Identity (name if known, from on-device face recognition — never guessed)
#   4. Actions and movement (walking toward, standing, holding, using phone…)
#   5. Important objects being held or nearby
#   6. Clothing and visible appearance
#   7. Background / environment
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a visual scene understanding assistant for AccessAI, an assistive system
designed to help visually impaired users understand their surroundings through
a doorbell or front-door camera.

A blind user will HEAR your description spoken aloud. They rely on you to SEE for
them — be their eyes. Every visual detail you notice matters.

Do NOT use bullet points, lists, markdown, tables, or technical terms.
Write only natural spoken sentences.

PRIORITY ORDER (most important first):
1. Immediate hazards or obstacles directly in the user's path
2. Number of people visible and their positions
3. Identity — use the name provided in GROUND TRUTH if available; otherwise
   say "an unknown person". NEVER invent or guess identity.
4. Actions and movement (walking toward camera, standing, sitting, holding
   something, using a phone, entering or leaving)
5. Important carried objects (bag, backpack, parcel, phone, umbrella, etc.)
6. Clothing — be SPECIFIC and DETAILED:
   - Type: T-shirt, polo, jersey, hoodie, jacket, kurta, saree, suit, etc.
   - Colour and pattern: solid, striped, checkered, patterned, gradient, etc.
   - Brand logos, team crests, jersey numbers/names when visible
   - Identify sports jerseys by team when recognisable (e.g. "Spain national
     team away jersey", "Manchester United home kit", "India cricket jersey")
   - Notable details: V-neck, collar, zip, buttons, shoulder stripes, etc.
7. Hairstyle — be SPECIFIC:
   - Style: fade, undercut, buzz cut, crew cut, long, shoulder-length, curly,
     wavy, straight, braids, ponytail, bun, afro, bald, receding, etc.
   - Colour if notable: black, brown, blonde, grey, dyed, highlighted, etc.
8. Facial features — be SPECIFIC:
   - Facial hair: clean-shaven, stubble, light mustache, full mustache, goatee,
     full beard, trimmed beard, etc.
   - Glasses, sunglasses, mask, piercings, etc.
9. Important nearby objects (vehicles, furniture, stairs, signs, animals)
10. Environment — be DETAILED:
    - Indoor: ceiling type (wooden, false, concrete), wall features (wallpaper,
      tiles, paint, decorations, photo frames), furniture, lighting (tube light,
      bulb, LED, natural), fans, windows, doors
    - Outdoor: weather, time of day, paving, garden, gate, vehicles, street

SPATIAL LANGUAGE — always describe position:
- Use: left / center / right / directly in front / slightly left or right
- Use: near / about [N] meters away / far in the background
- Use: approaching / moving away / standing still

RULES:
- Describe ONLY what is clearly visible. If something is unclear, omit it.
- Do NOT hallucinate people, objects, or text you cannot see.
- Do NOT call anyone dangerous, suspicious, or criminal. Unknown simply means
  the face was not recognised by the system.
- Do NOT state exact age, race, or gender as fact — use cautious language:
  "appears to be a young adult", "appears to be elderly", etc.
- Do NOT state emotions as fact: say "appears to be smiling", not "is happy".
- If there are many people, prioritise those closest to the camera.
- Keep the final description concise enough for comfortable voice output.
"""

_USER_PROMPT = (
    "Analyze this doorbell camera image. "
    "Respond with STRICT JSON ONLY — no markdown, no explanation, no prose outside the JSON.\n\n"
    "REQUIRED JSON SHAPE (fill ALL fields, use empty string if not visible):\n"
    '{\n'
    '  "people": [\n'
    '    {\n'
    '      "identity": "<name from GROUND TRUTH, or \'an unknown person\'>",\n'
    '      "position": "<left | center | right | directly in front | far background>",\n'
    '      "distance": "<estimated meters, e.g. \'about 1 meter\', or \\"\\">",\n'
    '      "action": "<standing | walking toward camera | walking away | sitting | holding [object] | using phone | entering doorway | looking down | etc.>",\n'
    '      "clothing": "<BE SPECIFIC: garment type + colour + pattern + brand/team/logo. E.g. \'white Spain national team away jersey with maroon Adidas stripes and gold V-neck trim\', \'navy blue Nike polo shirt\', \'red plaid flannel shirt\'. Include jersey numbers, sponsor logos, team crests.>",\n'
    '      "hairstyle": "<BE SPECIFIC: fade, undercut, buzz cut, crew cut, long, curly, wavy, straight, braids, ponytail, bun, afro, bald — plus colour. E.g. \'fade with textured top, black hair\', \'shoulder-length curly brown hair\'>",\n'
    '      "appearance": "<facial hair (clean-shaven, stubble, light mustache, full mustache, goatee, full beard), glasses, mask, tattoos — only clearly visible>",\n'
    '      "carrying": "<bag, backpack, parcel, phone, umbrella, bottle, keys — or \\"\\">",\n'
    '      "expression": "<smiling | calm | neutral | looking down | etc., or \\"\\">",\n'
    '      "hands": "<empty at sides | in pockets | holding phone | touching face | folded | gesturing — or \\"\\">",\n'
    '      "build": "<slim | average | stocky | tall | short — or \\"\\">",\n'
    '      "age_group": "<appears to be: child | teenager | young adult | adult | middle-aged | elderly — or \\"\\">",\n'
    '      "footwear": "<sneakers, sandals, boots, slippers, barefoot — colour if visible, or \\"\\">",\n'
    '      "accessories": "<watch, necklace, earrings, cap, headband, wristband — or \\"\\">"}\n'
    '  ],\n'
    '  "hazards": "<stairs, vehicle blocking entry, wet floor, large obstacle — or \\"\\">",\n'
    '  "objects": "<vehicles, furniture, signs, animals, bags near door with position — or \\"\\">",\n'
    '  "environment": "<indoor/outdoor, ceiling type, wall features, lighting, decorations, furniture, fan, windows — be detailed>",\n'
    '  "scene": "<3-5 spoken sentences for a blind user: Describe the person (name, DETAILED clothing with brand/team, hairstyle, facial hair, what their hands are doing, build) + their position and action + environment details (room features, ceiling, wall decorations, lighting, furniture). Be their eyes — every visual detail matters.>",\n'
    '  "labels": "<any visible text on clothing, parcels, signs, vehicles — verbatim, or \\"\\">"}\n'
    '\n\n'
    "CRITICAL RULES:\n"
    "- The \"people\" array MUST have ONE entry per visible person — NEVER leave it empty if people are visible.\n"
    "- Order people LEFT TO RIGHT as they appear in the image.\n"
    "- Use names ONLY from GROUND TRUTH — never guess identity.\n"
    "- Describe ONLY what is clearly visible. Use empty string for anything unclear.\n"
    "- NEVER call anyone suspicious, dangerous, or criminal.\n"
    "- Do NOT state exact age or race as fact — use 'appears to be young adult', etc.\n"
    "- For the \"scene\" field: ALWAYS mention clothing details (brand/team if visible), hairstyle, facial hair, and what their hands are doing. A blind user needs to visualise the person and the room."
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
                 max_image_width=768,
                 extra_models=None,
                 extra_providers=None):
        """
        Multi-model, multi-provider VLM with automatic failover.

        Failover order:
          1. Primary model (VLM_MODEL) with all keys
          2. extra_models  — additional models on the SAME base_url/keys
             (e.g. gemini-3.5-flash-lite, gemini-3.1-flash-lite)
          3. extra_providers — list of {base_url, model, keys} dicts for
             completely different API providers (OpenRouter, Together, etc.)

        Each (key, model) pair has its own independent 429 back-off so a
        quota hit on one model never blocks the others.
        """
        # Accept a list OR a comma-separated string; strip blanks either way.
        if isinstance(keys, str):
            keys = keys.split(",")
        self._keys = [k.strip() for k in (keys or []) if k and k.strip()]

        self.base_url     = (base_url or "").rstrip("/")
        self.model        = model
        self.timeout      = float(timeout)
        self.max_tokens   = int(max_tokens)
        self.temperature  = float(temperature)
        self.jpeg_quality = int(jpeg_quality)
        self.max_image_width = int(max_image_width)

        # Build the ordered list of (base_url, model, keys) providers.
        # Primary model comes first, then extra_models on same base_url,
        # then completely different providers.
        self._providers = []
        if self._keys and self.base_url:
            self._providers.append({
                "base_url": self.base_url,
                "model":    self.model,
                "keys":     self._keys,
            })
        # Extra models on same endpoint (quota spread across models).
        for m in (extra_models or []):
            m = (m or "").strip()
            if m and m != self.model and self._keys and self.base_url:
                self._providers.append({
                    "base_url": self.base_url,
                    "model":    m,
                    "keys":     self._keys,
                })
        # Extra providers (different API endpoints entirely).
        for ep in (extra_providers or []):
            ep_url  = (ep.get("base_url") or "").rstrip("/")
            ep_model= (ep.get("model")    or "").strip()
            ep_keys = ep.get("keys") or []
            if isinstance(ep_keys, str):
                ep_keys = ep_keys.split(",")
            ep_keys = [k.strip() for k in ep_keys if k and k.strip()]
            if ep_url and ep_model and ep_keys:
                self._providers.append({
                    "base_url": ep_url,
                    "model":    ep_model,
                    "keys":     ep_keys,
                })

        # Per-(provider_idx, key_idx) 429 back-off map.
        self._retry_after: dict[tuple, float] = {}
        # Remember last successful (provider_idx, key_idx) so we start there.
        self._last_good_provider = 0
        self._last_good_key      = 0
        self._last_error  = ""
        self._last_status = None
        # Track which provider/model last succeeded for logging.
        self._last_provider_name = ""

        # Legacy single-key compat attributes (used by status() and tests).
        self._last_good = 0
        self._key_retry_after: dict[int, float] = {}

        self._ready = bool(_HAS_REQUESTS and _HAS_CV2 and self._providers)
        if not self._ready:
            why = (
                "no 'requests'" if not _HAS_REQUESTS else
                "no OpenCV"     if not _HAS_CV2     else
                "no API keys / base_url"
            )
            print(f"[VLMModule] Not ready ({why}); scene/OCR will be skipped "
                  f"(fail-soft, pipeline continues on YOLO-only).")
        else:
            models_str = " → ".join(
                f"{p['model']}({len(p['keys'])}k)" for p in self._providers
            )
            print(f"[VLMModule] Ready: {len(self._providers)} provider(s) | "
                  f"failover: {models_str}")
            print(f"[VLMModule] Primary: model={self.model}, "
                  f"{len(self._keys)} key(s) {self.masked_keys()}, "
                  f"base={self.base_url}")

    # ------------------------------------------------------------------ status
    def available(self) -> bool:
        return self._ready

    def key_count(self) -> int:
        return sum(len(p["keys"]) for p in self._providers)

    def masked_keys(self):
        """Last-4-only view of primary keys, for safe logging."""
        return [f"...{k[-4:]}" if len(k) >= 4 else "****" for k in self._keys]

    def status(self) -> dict:
        return {
            "available":        self._ready,
            "model":            self.model,
            "base_url":         self.base_url,
            "provider_count":   len(self._providers),
            "key_count":        self.key_count(),
            "keys_masked":      self.masked_keys(),
            "last_good_index":  self._last_good,
            "last_status":      self._last_status,
            "last_error":       self._last_error,
            "last_provider":    self._last_provider_name,
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
    def _chat(self, data_url, facts="", event_id=""):
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
        # A crowded frame (5-6 people) needs far more JSON than one visitor.
        # max_tokens is a CAP, not a target.
        provider_name = (self._providers[self._last_good_provider]["model"]
                         if self._providers else self.model)
        disp_provider = "OpenRouter/Qwen3.8" if "qwen" in provider_name.lower() else (
            "Gemini" if "gemini" in provider_name.lower() else provider_name)
        print(f"[VLM] Provider: {disp_provider}")
        _eid = f" event={event_id}" if event_id else ""
        print(f"[VLM] Request started{_eid}")
        t0 = time.monotonic()
        result = self._post(messages, max_tokens=max(self.max_tokens, 700),
                            timeout=self.timeout)
        elapsed = time.monotonic() - t0
        if result:
            print(f"[VLM] Response received in {elapsed:.2f}s")
            print(f"[VLM] Analysis complete{_eid}")
        else:
            print(f"[VLM] All providers failed in {elapsed:.2f}s")
        return result

    def _post(self, messages, max_tokens=None, timeout=None):
        """POST a chat completion failing over across ALL providers and ALL keys.

        Failover order:
          provider 0 (primary model), all keys  →
          provider 1 (first fallback model), all keys  →
          … → last provider, all keys → None (YOLO-only)

        Each (provider_idx, key_idx) pair has its own 429 back-off so a
        quota hit on model A doesn't block model B.
        Returns the assistant message text on success, or None. Never raises.
        """
        if not self._ready or not messages:
            return None

        request_timeout = float(timeout or self.timeout)
        req_max_tokens  = int(max_tokens or self.max_tokens)
        now = time.monotonic()

        def _model_label(m):
            if "qwen" in m.lower():
                return "Qwen3.8"
            if "gemini" in m.lower():
                return "Gemini"
            return m

        for p_idx, provider in enumerate(self._providers):
            p_base  = provider["base_url"]
            p_model = provider["model"]
            p_keys  = provider["keys"]
            url     = f"{p_base}/chat/completions"
            n       = len(p_keys)

            # Start from the last key that worked for this provider.
            last_good = self._last_good_key if p_idx == self._last_good_provider else 0
            order = [(last_good + i) % n for i in range(n)]

            for k_idx in order:
                pair = (p_idx, k_idx)
                retry_at = self._retry_after.get(pair, 0.0)
                if now < retry_at:
                    remaining = int(retry_at - now)
                    print(f"[VLMModule] {p_model} key#{k_idx} in 429 back-off, "
                          f"{remaining}s remaining — skipping.")
                    continue

                key    = p_keys[k_idx]
                masked = f"...{key[-4:]}" if len(key) >= 4 else "****"
                body   = {
                    "model":       p_model,
                    "messages":    messages,
                    "max_tokens":  req_max_tokens,
                    "temperature": self.temperature,
                }
                headers = {"Authorization": f"Bearer {key}",
                           "Content-Type":  "application/json"}
                try:
                    r = requests.post(url, headers=headers, json=body,
                                      timeout=request_timeout)
                except requests.exceptions.Timeout:
                    self._last_status = None
                    self._last_error = f"{p_model}: timeout"
                    curr_name = _model_label(p_model)
                    print(f"[VLM] {curr_name} failed: Request timeout")
                    if p_idx + 1 < len(self._providers):
                        next_name = _model_label(self._providers[p_idx + 1]["model"])
                        print(f"[VLM] Falling back to {next_name}")
                    break
                except Exception as e:
                    self._last_status = None
                    self._last_error  = f"{p_model}/{masked}: network error"
                    curr_name = _model_label(p_model)
                    print(f"[VLM] {curr_name} failed: {e}")
                    if p_idx + 1 < len(self._providers):
                        next_name = _model_label(self._providers[p_idx + 1]["model"])
                        print(f"[VLM] Falling back to {next_name}")
                    continue

                self._last_status = r.status_code
                if r.status_code == 200:
                    try:
                        content = r.json()["choices"][0]["message"]["content"]
                    except Exception as e:
                        self._last_error = f"{p_model}/{masked}: bad response"
                        curr_name = _model_label(p_model)
                        print(f"[VLM] {curr_name} failed: malformed response ({e})")
                        if p_idx + 1 < len(self._providers):
                            next_name = _model_label(self._providers[p_idx + 1]["model"])
                            print(f"[VLM] Falling back to {next_name}")
                        break
                    # Strip Qwen / thinking-model <think>…</think> blocks.
                    content = re.sub(r"<think>.*?</think>", "", content,
                                     flags=re.DOTALL).strip()
                    if "<think>" in content:
                        content = content.split("<think>")[0].strip()
                    # Record winner.
                    self._last_good_provider = p_idx
                    self._last_good_key      = k_idx
                    self._last_good          = k_idx  # legacy compat
                    self._last_error         = ""
                    self._last_provider_name = p_model
                    self._retry_after.pop(pair, None)
                    self._key_retry_after.pop(k_idx, None)  # legacy compat
                    if p_model != self.model:
                        print(f"[VLM] Failover succeeded on {p_model}.")
                    return content

                if r.status_code == 429:
                    backoff = 60.0
                    retry_hdr = r.headers.get("Retry-After", "")
                    if retry_hdr.isdigit():
                        backoff = max(backoff, float(retry_hdr))
                    self._retry_after[pair] = time.monotonic() + backoff
                    self._key_retry_after[k_idx] = time.monotonic() + backoff
                    self._last_error = (f"{p_model}/{masked}: HTTP 429 "
                                        f"(back-off {int(backoff)}s)")
                    curr_name = _model_label(p_model)
                    print(f"[VLM] {curr_name} failed: HTTP 429")
                    if p_idx + 1 < len(self._providers):
                        next_name = _model_label(self._providers[p_idx + 1]["model"])
                        print(f"[VLM] Falling back to {next_name}")
                    break
                elif r.status_code in (500, 502, 503, 504, 529):
                    # Server error / overloaded — short back-off then try next.
                    self._retry_after[pair] = time.monotonic() + 10.0
                    self._last_error = f"{p_model}/{masked}: HTTP {r.status_code}"
                    curr_name = _model_label(p_model)
                    print(f"[VLM] {curr_name} failed: HTTP {r.status_code}")
                    if p_idx + 1 < len(self._providers):
                        next_name = _model_label(self._providers[p_idx + 1]["model"])
                        print(f"[VLM] Falling back to {next_name}")
                    break  # skip remaining keys for this provider — try next
                elif r.status_code == 401:
                    self._last_error = f"{p_model}/{masked}: HTTP 401 (bad key)"
                    curr_name = _model_label(p_model)
                    print(f"[VLM] {curr_name} failed: HTTP 401 (API key/config error)")
                    if p_idx + 1 < len(self._providers):
                        next_name = _model_label(self._providers[p_idx + 1]["model"])
                        print(f"[VLM] Falling back to {next_name}")
                    break  # bad key won't work on retry
                else:
                    self._last_error = f"{p_model}/{masked}: HTTP {r.status_code}"
                    curr_name = _model_label(p_model)
                    print(f"[VLM] {curr_name} failed: HTTP {r.status_code}, failing over.")
                    if p_idx + 1 < len(self._providers):
                        next_name = _model_label(self._providers[p_idx + 1]["model"])
                        print(f"[VLM] Falling back to {next_name}")

        print("[VLM] All providers/keys exhausted; YOLO-only fallback.")
        return None


    # --------------------------------------------------------------- high level
    def describe_and_read(self, frame_bgr, facts="", event_id="") -> dict:
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
        content = self._chat(self._encode(frame_bgr), facts=facts,
                             event_id=event_id)
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
            "You are a visual assistant for AccessAI, an assistive system for "
            "visually impaired users. The user has asked a question about what "
            "the doorbell camera sees RIGHT NOW. Your answer will be spoken aloud."
            "\n\n"
            "Answer ONLY the question asked — do not give a full scene description "
            "unless specifically requested. Be brief (1-3 spoken sentences) and "
            "directly address the question first. If relevant, add one closely "
            "related visible detail (e.g. asked about a parcel, mention the "
            "courier logo).\n\n"
            "SPATIAL LANGUAGE: always include position (left / center / right / "
            "directly in front / near / far) and distance estimate when relevant.\n\n"
            "RULES:\n"
            "- Use only information actually visible in the image.\n"
            "- Use cautious language: 'appears to be', 'likely', 'unable to tell'.\n"
            "- Use identity names from GROUND TRUTH if provided; never guess identity.\n"
            "- Do not state exact age, race, or gender as fact.\n"
            "- Do not state emotions as fact ('appears to be smiling', not 'is happy').\n"
            "- Never call anyone suspicious, dangerous, or criminal.\n"
            "- If the answer is not visible, say clearly that you cannot tell."
        )
        hint = ""
        g = (grounding or "").strip()
        if g:
            hint = ("GROUND TRUTH from on-device detectors (do not contradict): "
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
            "You are a visual scene understanding assistant for AccessAI, an "
            "assistive system for visually impaired users. Give a detailed spoken "
            "report of this doorbell camera view. Your output will be read aloud "
            "— write natural flowing sentences only (no lists, no headings, no "
            "markdown).\n\n"
            "Structure your report in this exact order:\n"
            "1. SCENE OPENER: Start with the total number of people and whether "
            "the scene is indoors or outdoors if clear. Example: 'There are two "
            "people in front of you outdoors.'\n"
            "2. HAZARDS FIRST: If there is any immediate obstacle or hazard "
            "(steps, bicycle, vehicle blocking entry, large object in the path), "
            "mention it right after the opener.\n"
            "3. EACH PERSON (left to right, nearest first): position (left / "
            "center / right / directly in front), identity (use name from GROUND "
            "TRUTH if provided, else 'an unknown person'), approximate distance "
            "if estimable, action (what they are doing), carried objects, "
            "clothing (type and colour), visible appearance (age group if clear, "
            "hair, glasses, beard, hat — omit what is unclear).\n"
            "4. IMPORTANT OBJECTS: vehicles, furniture, stairs, signs, animals, "
            "bags or boxes near the door. Include position and distance.\n"
            "5. ENVIRONMENT: lighting, weather, anything else a blind user "
            "should know.\n\n"
            "RULES:\n"
            "- Identity: ONLY use names given in GROUND TRUTH. Never guess.\n"
            "- Use cautious language: 'appears to be', 'likely', 'unable to tell'.\n"
            "- Do not state exact age, race, or gender as fact.\n"
            "- Do not state emotions as fact ('appears to be smiling', not 'happy').\n"
            "- Never call anyone suspicious, dangerous, or criminal.\n"
            "- Do not describe background clutter that is not relevant.\n"
            "- Skip any section where nothing is visible or relevant.\n"
            "- Aim for 4-8 sentences total — detailed but not overwhelming."
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
        """Pull {scene, appearance, labels, people, hazards, objects} out of the
        model's reply, tolerating stray markdown fences or prose around the JSON.
        Accepts both 'scene' and 'scene_summary' as the scene key.
        """
        print(f"[VLM RAW RESPONSE]\n{content}\n-------------------")
        text = (content or "").strip()
        # Strip ```json ... ``` fences if the model added them.
        if text.startswith("```"):
            text = text.strip("`")
            if text[:4].lower() == "json":
                text = text[4:]
            text = text.strip()

        scene, appearance, labels, hazards, objects_txt = "", "", "", "", ""
        people = []
        try:
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end != -1 and end > start:
                obj = json.loads(text[start:end + 1])
                # Accept both 'scene' and 'scene_summary' keys
                scene       = str(obj.get("scene", "")
                                  or obj.get("scene_summary", "") or "").strip()
                environment = str(obj.get("environment", "") or "").strip()
                appearance  = str(obj.get("appearance", "") or "").strip()
                labels      = str(obj.get("labels", "")
                                  or obj.get("ocr_text", "") or "").strip()
                hazards     = str(obj.get("hazards",  "") or "").strip()
                objects_txt = str(obj.get("objects",  "") or "").strip()
                people      = VLMModule._parse_people(obj.get("people"))
                # If environment is provided and not part of scene, incorporate it
                if environment and environment.lower() not in scene.lower():
                    if scene:
                        scene = f"{scene.rstrip('.')} Environment: {environment.rstrip('.')}."
                    else:
                        scene = environment
            elif start == -1:
                # Plain prose reply — treat entire text as scene summary.
                scene = text
            # else: JSON opened but never closed (truncated) — salvage below.
        except Exception:
            pass   # malformed JSON — salvage below

        # If JSON parsing yielded nothing useful, fish fields out with regex.
        if "{" in text:
            if not scene:
                scene       = VLMModule._salvage_field(text, "scene") \
                              or VLMModule._salvage_field(text, "scene_summary")
            if not appearance:
                appearance  = VLMModule._salvage_field(text, "appearance")
            if not labels:
                labels      = VLMModule._salvage_field(text, "labels") \
                              or VLMModule._salvage_field(text, "ocr_text")
            if not hazards:
                hazards     = VLMModule._salvage_field(text, "hazards")
            if not objects_txt:
                objects_txt = VLMModule._salvage_field(text, "objects")
            if not people and '"people"' in text:
                p_blocks = re.findall(r'\{\s*"(?:identity|position|clothing|hairstyle|appearance)"[^{}]*\}', text, re.DOTALL)
                for pb in p_blocks:
                    try:
                        p_obj = json.loads(pb)
                        if isinstance(p_obj, dict):
                            people.extend(VLMModule._parse_people([p_obj]))
                    except Exception:
                        pass

        return {
            "scene_summary": scene,
            "appearance":    appearance,
            "ocr_text":      labels,
            "people":        people,
            "hazards":       hazards,
            "objects":       objects_txt,
        }

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

        Each entry carries the full set of new spatial, clothing, and body fields:
        identity, position, distance, action, clothing, hairstyle, appearance,
        hands, build, age_group, footwear, accessories, carrying, expression.
        Unknown/missing fields default to empty string.
        Anything malformed is dropped defensively.
        """
        out = []
        if not isinstance(raw, list):
            return out
        for item in raw:
            if not isinstance(item, dict):
                continue
            out.append({
                # Spatial / identity fields
                "identity":    str(item.get("identity",    "") or "").strip(),
                "position":    str(item.get("position",    "") or "").strip(),
                "distance":    str(item.get("distance",    "") or "").strip(),
                "action":      str(item.get("action",      "") or "").strip(),
                "clothing":    str(item.get("clothing",    "") or "").strip(),
                "hairstyle":   str(item.get("hairstyle",   "") or "").strip(),
                "hands":       str(item.get("hands",       "") or "").strip(),
                "build":       str(item.get("build",       "") or "").strip(),
                "age_group":   str(item.get("age_group",   "") or "").strip(),
                "footwear":    str(item.get("footwear",    "") or "").strip(),
                "accessories": str(item.get("accessories", "") or "").strip(),
                # Legacy fields kept for backward compat with downstream code
                "appearance":  str(item.get("appearance",  "") or "").strip(),
                "carrying":    str(item.get("carrying",    "") or "").strip(),
                "expression":  str(item.get("expression",  "") or "").strip(),
            })
        return out
