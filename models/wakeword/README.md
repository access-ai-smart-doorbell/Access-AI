# models/wakeword/ — custom "Hey Access" wake word model

`accessai/wakeword_module.py` auto-loads the first `*.onnx` in this directory
(model priority A). Until one exists it falls back to the pretrained
openWakeWord phrase in `config.WAKEWORD_MODEL` (`hey_jarvis`) as a
loudly-logged placeholder.

Train the custom model fully OFFLINE — no recordings, no network:

```bash
.venv/bin/python scripts/train_wakeword.py          # ~10–15 min on CPU
```

The script synthesizes "Hey Access" with all ~54 Kokoro voices (the Phase-11
TTS), augments with noise/reverb/babble, extracts openWakeWord's frozen audio
embeddings, trains a tiny classifier (pinned torch 2.4.1, nothing installed),
exports `hey_access.onnx`, and streaming-verifies it through the exact
`openwakeword.Model` loader the live detector uses.

`_train_cache/` holds the generated WAVs (gitignored). To improve real-mic
robustness, record a few 16 kHz mono "Hey Access" WAVs into
`_train_cache/pos_extra/` and re-run with `--skip-gen`.

Tune `WAKEWORD_THRESHOLD` in `config.py` if you get false wakes (raise) or
missed wakes (lower).
