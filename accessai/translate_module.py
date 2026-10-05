"""
TranslateModule - multi-language translation of the visitor's speech (Phase 8).

The visitor (from Phase-7 Whisper) may speak Hindi, Malayalam, Tamil, etc., while
the blind/deaf user consumes a single chosen language (config.USER_LANGUAGE). This
module translates visitor -> user so the announcement is spoken (Blind) and
captioned (Deaf) in a language the user actually understands. The result lands in
ev.translated_transcript, which accessibility.compose_announcement already prefers
over the raw transcript.

Backends (priority, all behind the same interface):
  A "github"  - PREFERRED, torch-safe. Reuses the Phase-6 VLMModule's
                OpenAI-compatible chat endpoint + multi-key FAILOVER for a
                text-only translation call. No new dependency, never moves torch.
  B "groq"    - Groq cloud LLM (free tier, fast). Uses the OpenAI-compatible
                Groq API for text-only translation. Torch-free, adds no heavy
                dependency. Set GROQ_API_KEY in .env.
  C "none"    - passthrough: translate() returns the original text unchanged.

Design rules (same as every AccessAI module):
  * NEVER raise from __init__ or translate() - degrade to passthrough on anything
    missing (no keys, no model, dead network) so the doorbell never breaks.
  * Same-language (src == target) => return the original with NO API call (saves
    the free-tier quota; also covers English->English).
  * translate() ALWAYS returns a string.
"""

import logging

logger = logging.getLogger("TranslateModule")

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

# A small, sensible default so the module is usable standalone (run.py passes the
# full config.LANGUAGE_NAMES in). Maps ISO code -> human name for the prompt.
_DEFAULT_LANGUAGE_NAMES = {
    "en": "English", "hi": "Hindi", "ml": "Malayalam", "ta": "Tamil",
    "te": "Telugu", "kn": "Kannada", "bn": "Bengali", "mr": "Marathi",
    "gu": "Gujarati", "pa": "Punjabi", "ur": "Urdu",
}

# Groq API defaults — fast, accurate, torch-free.
_GROQ_BASE_URL = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_MODELS = ["qwen/qwen3.8-27b", "openai/gpt-oss-20b", "openai/gpt-oss-120b"]
_GROQ_MODEL = _GROQ_MODELS[0]


class TranslateModule:
    def __init__(self, backend="auto", user_language="en",
                 language_names=None, vlm=None, groq_keys=""):
        req_backend = (backend or "auto").lower()
        self.user_language = (user_language or "en").strip() or "en"
        self.language_names = dict(language_names or _DEFAULT_LANGUAGE_NAMES)
        self.vlm = vlm                      # Phase-6 VLMModule (reused for "github")

        # Groq backend keys (comma-separated string or list).
        if isinstance(groq_keys, str):
            groq_keys = groq_keys.split(",")
        self._groq_keys = [k.strip() for k in (groq_keys or []) if k and k.strip()]
        self._groq_last_good = 0
        self._groq_last_model = 0

        # Backend selection (with auto-resolution):
        has_groq = bool(self._groq_keys and _HAS_REQUESTS)
        has_vlm  = bool(vlm is not None and vlm.available())

        if req_backend == "groq" and has_groq:
            self.backend = "groq"
        elif req_backend == "github" and has_vlm:
            self.backend = "github"
        elif req_backend in ("auto", "groq", "github"):
            # Prefer Groq for translation (20ms latency, excellent multilingual)
            if has_groq:
                self.backend = "groq"
            elif has_vlm:
                self.backend = "github"
            else:
                self.backend = "none"
        else:
            self.backend = "none"

        if self.backend == "groq":
            print(f"[TranslateModule] backend=groq ({_GROQ_MODEL}), target="
                  f"{self.lang_name(self.user_language)} | {len(self._groq_keys)} key(s)")
        elif self.backend == "github":
            print(f"[TranslateModule] backend=github, target="
                  f"{self.lang_name(self.user_language)} | reusing VLM keys")
        else:
            print(f"[TranslateModule] backend=none | PASSTHROUGH: translation "
                  f"disabled, original transcript shown unchanged")

    # ------------------------------------------------------------------ status
    def available(self) -> bool:
        """True when the backend can ACTUALLY translate."""
        if self._groq_keys and _HAS_REQUESTS:
            return True
        if self.vlm is not None and self.vlm.available():
            return True
        return False

    def backend_name(self) -> str:
        return self.backend

    def lang_name(self, code) -> str:
        """Human-readable name for an ISO code (falls back to the code itself)."""
        code = (code or "").strip().lower()
        if "-" in code:
            code = code.split("-")[0]
        return self.language_names.get(code, code or "the target language")

    def set_user_language(self, code) -> str:
        code = (code or "").strip().lower()
        if code:
            self.user_language = code
        return self.user_language

    def status(self) -> dict:
        return {
            "backend": self.backend,
            "available": self.available(),
            "user_language": self.user_language,
            "user_language_name": self.lang_name(self.user_language),
        }

    # --------------------------------------------------------------- translate
    def translate(self, text, src_lang="", target_lang=None) -> str:
        """Translate `text` from src_lang into target_lang (default user_language).

        Returns a STRING always. Empty in -> empty out. Same-language or any
        failure -> the original text unchanged. Never raises.
        """
        text = (text or "").strip()
        if not text:
            return ""
        target = (target_lang or self.user_language or "en").strip().lower()
        src = (src_lang or "").strip().lower()

        # Normalize locale tags like 'en-US' -> 'en'
        if "-" in target:
            target = target.split("-")[0]
        if "-" in src:
            src = src.split("-")[0]

        # Same language => no API call (quota-saving; covers en->en).
        if src and src == target:
            return text

        try:
            # 1. Primary backend
            out = ""
            if self.backend == "groq" or (self._groq_keys and _HAS_REQUESTS):
                out = self._translate_groq(text, src, target)
            elif self.backend == "github" and self.vlm is not None and self.vlm.available():
                out = self.vlm.translate_text(text, self.lang_name(target))

            # 2. Fallback to secondary backend if primary returned empty
            if not out:
                if self.vlm is not None and self.vlm.available():
                    out = self.vlm.translate_text(text, self.lang_name(target))
                elif self._groq_keys and _HAS_REQUESTS:
                    out = self._translate_groq(text, src, target)

            out = (out or "").strip()
            # Clean enclosing quotes if added by the LLM
            if (out.startswith('"') and out.endswith('"')) or (out.startswith("'") and out.endswith("'")):
                out = out[1:-1].strip()
            return out if out else text
        except Exception as e:                                # pragma: no cover
            logger.warning("translate failed (%s); using original.", e)
            return text

    # ------------------------------------------------------- Groq API (Option B)
    def _translate_groq(self, text, src, target) -> str:
        """Translate via Groq's OpenAI-compatible chat API with multi-key and model failover.

        Tries each key and model in order. Returns the translated text, or ''
        on any failure (caller falls back to original). Torch-free, ultra-fast.
        """
        if not self._groq_keys or not _HAS_REQUESTS:
            return ""

        target_name = self.lang_name(target)
        src_hint = f" from {self.lang_name(src)}" if src else ""
        prompt = (
            f"Translate the following text{src_hint} into {target_name}. "
            "Keep the phrasing natural, fluent, and conversational for audio speech. "
            f"Output ONLY the translated text, with no notes, quotes, or explanations.\n\n{text}"
        )

        n = len(self._groq_keys)
        order = [(self._groq_last_good + i) % n for i in range(n)]

        # Try models in priority order
        for model in _GROQ_MODELS:
            for k_idx in order:
                key = self._groq_keys[k_idx]
                masked = f"...{key[-4:]}" if len(key) >= 4 else "****"
                try:
                    r = _requests.post(
                        _GROQ_BASE_URL,
                        headers={"Authorization": f"Bearer {key}",
                                 "Content-Type": "application/json"},
                        json={
                            "model": model,
                            "messages": [{"role": "user", "content": prompt}],
                            "max_tokens": 500,
                            "temperature": 0.1,
                        },
                        timeout=8,
                    )
                    if r.status_code == 200:
                        content = r.json()["choices"][0]["message"]["content"]
                        self._groq_last_good = k_idx
                        return content.strip()
                    if r.status_code == 429:
                        logger.info("Groq key %s: HTTP 429, trying next key.", masked)
                        continue
                    if r.status_code == 404:
                        # Model not available on this tier, break to next model
                        break
                    logger.warning("Groq key %s model %s: HTTP %d, trying next.",
                                   masked, model, r.status_code)
                except Exception as e:
                    logger.warning("Groq key %s: error (%s), trying next.", masked, e)

        return ""
