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

# Groq API defaults.
_GROQ_BASE_URL = "https://api.groq.com/openai/v1/chat/completions"
_GROQ_MODEL = "llama-3.1-8b-instant"


class TranslateModule:
    def __init__(self, backend="github", user_language="en",
                 language_names=None, vlm=None, groq_keys=""):
        self.backend = (backend or "none").lower()
        self.user_language = (user_language or "en").strip() or "en"
        self.language_names = dict(language_names or _DEFAULT_LANGUAGE_NAMES)
        self.vlm = vlm                      # Phase-6 VLMModule (reused for "github")

        # Groq backend keys (comma-separated string or list).
        if isinstance(groq_keys, str):
            groq_keys = groq_keys.split(",")
        self._groq_keys = [k.strip() for k in (groq_keys or []) if k and k.strip()]
        self._groq_last_good = 0

        if self.backend == "github":
            ok = bool(vlm is not None and vlm.available())
            why = "reusing VLM keys" if ok else (
                "no VLM keys available - PASSTHROUGH (shows original)")
            print(f"[TranslateModule] backend=github, target="
                  f"{self.lang_name(self.user_language)} | {why}")
        elif self.backend == "groq":
            ok = bool(self._groq_keys and _HAS_REQUESTS)
            why = (f"{len(self._groq_keys)} key(s)" if ok else
                   "no Groq keys or requests missing - PASSTHROUGH")
            print(f"[TranslateModule] backend=groq, target="
                  f"{self.lang_name(self.user_language)} | {why}")
        else:
            self.backend = "none"
            print(f"[TranslateModule] backend=none | PASSTHROUGH: translation "
                  f"disabled, original transcript shown unchanged")

    # ------------------------------------------------------------------ status
    def available(self) -> bool:
        """True when the backend can ACTUALLY translate. 'none' is a passthrough,
        so it's not 'available' even though translate() still works (returns the
        original) - lets the UI say 'showing original'."""
        if self.backend == "github":
            return bool(self.vlm is not None and self.vlm.available())
        if self.backend == "groq":
            return bool(self._groq_keys and _HAS_REQUESTS)
        return False

    def backend_name(self) -> str:
        return self.backend

    def lang_name(self, code) -> str:
        """Human-readable name for an ISO code (falls back to the code itself)."""
        code = (code or "").strip()
        return self.language_names.get(code, code or "the target language")

    def set_user_language(self, code) -> str:
        code = (code or "").strip()
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
        target = (target_lang or self.user_language or "en").strip()
        src = (src_lang or "").strip()

        # Same language => no API call (quota-saving; covers en->en).
        if src and src == target:
            return text

        try:
            if self.backend == "github":
                out = ""
                if self.vlm is not None and self.vlm.available():
                    out = self.vlm.translate_text(text, self.lang_name(target))
                return out.strip() if out and out.strip() else text
            if self.backend == "groq":
                return self._translate_groq(text, src, target) or text
            # backend "none" -> passthrough
            return text
        except Exception as e:                                # pragma: no cover
            logger.warning("translate failed (%s); using original.", e)
            return text

    # ------------------------------------------------------- Groq API (Option B)
    def _translate_groq(self, text, src, target) -> str:
        """Translate via Groq's OpenAI-compatible chat API.

        Tries each key in round-robin order. Returns the translated text, or ''
        on any failure (caller falls back to original). Torch-free, fast, free tier.
        """
        if not self._groq_keys or not _HAS_REQUESTS:
            return ""

        target_name = self.lang_name(target)
        src_hint = f" from {self.lang_name(src)}" if src else ""
        prompt = (
            f"Translate the following text{src_hint} into {target_name}. "
            f"Return ONLY the translated text, nothing else.\n\n{text}"
        )

        n = len(self._groq_keys)
        order = [(self._groq_last_good + i) % n for i in range(n)]

        for k_idx in order:
            key = self._groq_keys[k_idx]
            masked = f"...{key[-4:]}" if len(key) >= 4 else "****"
            try:
                r = _requests.post(
                    _GROQ_BASE_URL,
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"},
                    json={
                        "model": _GROQ_MODEL,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 500,
                        "temperature": 0.1,
                    },
                    timeout=15,
                )
                if r.status_code == 200:
                    content = r.json()["choices"][0]["message"]["content"]
                    self._groq_last_good = k_idx
                    return content.strip()
                if r.status_code == 429:
                    logger.info("Groq key %s: HTTP 429, trying next.", masked)
                    continue
                logger.warning("Groq key %s: HTTP %d, trying next.",
                               masked, r.status_code)
            except Exception as e:
                logger.warning("Groq key %s: error (%s), trying next.", masked, e)

        logger.warning("All Groq keys exhausted; returning original.")
        return ""
