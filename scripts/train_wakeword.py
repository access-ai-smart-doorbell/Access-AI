#!/usr/bin/env python3
"""
train_wakeword.py - train a CUSTOM "Hey Access" openWakeWord model, fully
OFFLINE, and export it to models/wakeword/hey_access.onnx.

WHY
---
Phase 10 shipped the pretrained "hey_jarvis" phrase as a loudly-logged
PLACEHOLDER because a custom wake word needs training data. This script removes
that placeholder without recording a single human voice and without any
network access: it follows the standard openWakeWord recipe (synthetic speech ->
frozen audio embeddings -> tiny classifier) using pieces the project already
has:

  positives   "Hey Access" spoken by ALL ~54 Kokoro-ONNX voices (the Phase-11
              TTS engine) at several speeds/prosodies, then augmented (gain,
              white/pink noise, babble, reverb, random placement in the window).
  negatives   confusable phrases ("hey alexa", "access granted", "hey access
              -sounding" fragments...), general sentences, plus pure
              noise/silence - same voices, same augmentations.
  features    openWakeWord's OWN frozen melspectrogram + embedding models
              (openwakeword.utils.AudioFeatures) - so our classifier sees
              exactly what the runtime detector feeds it.
  classifier  a tiny MLP on the (16, 96) embedding window -> 1 sigmoid score,
              trained with torch (pin 2.4.1 asserted, nothing installed),
              exported to ONNX opset 11 - byte-compatible with how
              openwakeword.Model loads its stock .onnx phrase models.

The result drops into models/wakeword/hey_access.onnx and wakeword_module.py
auto-loads it (WAKEWORD_MODEL in config.py points at that path, falling back to
the pretrained phrase if the file is absent - graceful degradation as always).

USAGE
-----
  .venv/bin/python scripts/train_wakeword.py            # full run (~10-15 min CPU)
  .venv/bin/python scripts/train_wakeword.py --quick    # tiny smoke run (~1 min)
  .venv/bin/python scripts/train_wakeword.py --skip-gen # reuse cached WAVs

The WAV cache lives in models/wakeword/_train_cache/ (gitignored); delete it to
regenerate from scratch. Every run ends with a STREAMING self-test: the exported
model is loaded through openwakeword.Model exactly like the live detector and
fed held-out positive/negative audio chunk-by-chunk; the script fails loudly if
the wake phrase does not score above threshold or a confusable phrase does.

Honest limitation (also logged at the end): all positives are synthetic TTS
voices. That is the accepted openWakeWord bootstrap recipe, but real-microphone
accuracy improves if you later append a few recorded "Hey Access" clips to
_train_cache/pos_extra/ and re-run with --skip-gen.
"""
import argparse
import glob
import os
import random
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
sys.path.insert(0, PROJECT)

OUT_DIR = os.path.join(PROJECT, "models", "wakeword")
CACHE = os.path.join(OUT_DIR, "_train_cache")
MODEL_OUT = os.path.join(OUT_DIR, "hey_access.onnx")

SR = 16000                 # openWakeWord's expected sample rate
WINDOW = 32000             # 2.0 s -> exactly 16 embedding frames of 96 dims
KOKORO_SR = 24000

# Positive phrase, written two ways so Kokoro produces two prosodies per voice.
POS_TEXTS = ["Hey Access", "Hey, Access!"]
POS_SPEEDS = [0.85, 1.0, 1.15]

# Negatives: confusable near-misses first (the ones that matter), then generic
# speech so the model learns "ordinary talking is not the wake word".
NEG_TEXTS = [
    # hard negatives - phonetically adjacent
    "hey alexa", "hey jarvis", "hey axis", "hey access point",
    "access", "access granted", "access denied", "hey", "hey there",
    "he acts", "success", "excess", "hey abbas", "a success story",
    "hey action", "hey acts", "may access", "they access the site",
    # generic doorbell-context speech
    "who is at the door", "package for you", "good morning",
    "I have a delivery for this address", "please open the door",
    "hello, anyone home", "the weather is nice today",
    "can you sign here please", "thank you very much",
    "I will come back tomorrow", "is this the right house",
    "turn off the lights", "what time is it now",
    "play some music please", "call my brother",
    "the quick brown fox jumps over the lazy dog",
]

RNG = random.Random(1234)   # deterministic dataset
NP_RNG = np.random.default_rng(1234)


def _log(msg: str) -> None:
    print(f"[train-wakeword] {msg}", flush=True)


def _assert_torch_pin() -> None:
    """Same torch-safety rule as fetch_antispoof_models.py: train with the
    pinned torch, refuse to run if the pin has drifted, install nothing."""
    import torch
    v = torch.__version__
    if not v.startswith("2.4.1"):
        raise SystemExit(
            f"TORCH SAFETY ABORT: torch is {v}, expected 2.4.1.x. "
            "Refusing to train to avoid masking a pin drift.")
    _log(f"torch pin OK: {v}")


# ---------------------------------------------------------------------------
# WAV cache helpers (16 kHz mono int16)
# ---------------------------------------------------------------------------
def _write_wav(path: str, pcm16: np.ndarray) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm16.astype(np.int16).tobytes())


def _read_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as w:
        assert w.getframerate() == SR, f"{path}: expected {SR} Hz"
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)


def _to_16k_int16(samples_24k: np.ndarray) -> np.ndarray:
    """Kokoro emits float32 @ 24 kHz; the detector wants int16 @ 16 kHz."""
    from scipy.signal import resample_poly
    x = resample_poly(samples_24k.astype(np.float64), 2, 3)   # 24k -> 16k
    peak = float(np.max(np.abs(x))) or 1.0
    x = x / peak * 0.7                                        # consistent level
    return (x * 32767.0).astype(np.int16)


# ---------------------------------------------------------------------------
# Phase A - synthesize the raw clips with Kokoro (cached; slowest part)
# ---------------------------------------------------------------------------
def synthesize(quick: bool) -> None:
    from kokoro_onnx import Kokoro
    kokoro = Kokoro(os.path.join(PROJECT, "models", "kokoro", "kokoro-v1.0.onnx"),
                    os.path.join(PROJECT, "models", "kokoro", "voices-v1.0.bin"))
    voices = sorted(kokoro.get_voices())
    if quick:
        voices = voices[:6]

    pos_dir = os.path.join(CACHE, "pos")
    neg_dir = os.path.join(CACHE, "neg")
    os.makedirs(pos_dir, exist_ok=True)
    os.makedirs(neg_dir, exist_ok=True)

    jobs = []   # (out_path, text, voice, speed)
    for v in voices:
        for ti, text in enumerate(POS_TEXTS):
            for speed in POS_SPEEDS:
                jobs.append((os.path.join(
                    pos_dir, f"{v}_t{ti}_s{int(speed*100)}.wav"), text, v, speed))
    # Each negative phrase is spoken by a deterministic subset of voices - full
    # coverage is unnecessary and would double the runtime.
    neg_voices_n = 3 if quick else 8
    for ni, text in enumerate(NEG_TEXTS):
        chosen = RNG.sample(voices, min(neg_voices_n, len(voices)))
        for v in chosen:
            jobs.append((os.path.join(
                neg_dir, f"n{ni:02d}_{v}.wav"), text, v, 1.0))

    todo = [j for j in jobs if not os.path.exists(j[0])]
    _log(f"synthesis: {len(jobs)} clips total, {len(todo)} to generate "
         f"({len(jobs) - len(todo)} cached)")
    for i, (path, text, voice, speed) in enumerate(todo):
        samples, sr = kokoro.create(text, voice=voice, speed=speed, lang="en-us")
        assert sr == KOKORO_SR
        _write_wav(path, _to_16k_int16(samples))
        if (i + 1) % 25 == 0:
            _log(f"  ...{i + 1}/{len(todo)}")
    _log("synthesis done.")


# ---------------------------------------------------------------------------
# Phase B - windowing + augmentation (pure numpy, fast)
# ---------------------------------------------------------------------------
def _pink_noise(n: int) -> np.ndarray:
    """1/f-shaped noise - closer to real room noise than white."""
    white = NP_RNG.standard_normal(n)
    spec = np.fft.rfft(white)
    f = np.arange(1, spec.shape[0] + 1)
    spec = spec / np.sqrt(f)
    x = np.fft.irfft(spec, n)
    return x / (np.max(np.abs(x)) or 1.0)


def _reverb(x: np.ndarray) -> np.ndarray:
    """A cheap exponential-decay impulse response - 'hallway' feel."""
    ir_len = int(SR * 0.12)
    ir = np.exp(-np.linspace(0, 8, ir_len)) * NP_RNG.uniform(0.3, 0.8)
    ir[0] = 1.0
    y = np.convolve(x, ir)[: len(x)]
    return y / (np.max(np.abs(y)) or 1.0) * (np.max(np.abs(x)) or 1.0)


def _place_in_window(clip: np.ndarray) -> np.ndarray:
    """Drop the utterance at a random offset inside the fixed 2 s window, so
    the classifier tolerates the phrase not being perfectly aligned - which is
    exactly what the streaming detector sees."""
    out = np.zeros(WINDOW, dtype=np.float32)
    c = clip.astype(np.float32)
    if len(c) >= WINDOW:
        start = RNG.randint(0, len(c) - WINDOW)
        out[:] = c[start:start + WINDOW]
    else:
        off = RNG.randint(0, WINDOW - len(c))
        out[off:off + len(c)] = c
    return out


def _augment(clip16: np.ndarray, babble_pool: list, n_variants: int) -> list:
    """Return n_variants windows: the clean placement plus noisy/reverbed ones."""
    outs = []
    base = clip16.astype(np.float32)
    for k in range(n_variants):
        x = _place_in_window(base)
        if k > 0:                                   # variant 0 stays clean
            x = x * RNG.uniform(0.4, 1.2)           # gain
            snr_db = RNG.uniform(5, 25)
            sig_pow = float(np.mean(x ** 2)) or 1.0
            noise_kind = RNG.random()
            if noise_kind < 0.4:
                noise = NP_RNG.standard_normal(WINDOW)
            elif noise_kind < 0.8:
                noise = _pink_noise(WINDOW)
            else:                                   # babble: other speech, low
                noise = np.zeros(WINDOW, dtype=np.float32)
                for b in RNG.sample(babble_pool, min(3, len(babble_pool))):
                    noise += _place_in_window(b.astype(np.float32))
                noise /= 3.0
            noise_pow = float(np.mean(noise ** 2)) or 1.0
            noise = noise * np.sqrt(sig_pow / noise_pow / (10 ** (snr_db / 10)))
            x = x + noise
            if RNG.random() < 0.3:
                x = _reverb(x)
        peak = float(np.max(np.abs(x))) or 1.0
        if peak > 32000:
            x = x / peak * 32000
        outs.append(x.astype(np.int16))
    return outs


def build_dataset(quick: bool):
    pos_files = sorted(glob.glob(os.path.join(CACHE, "pos", "*.wav")))
    neg_files = sorted(glob.glob(os.path.join(CACHE, "neg", "*.wav")))
    # Optional real-microphone positives the user recorded themselves.
    extra = sorted(glob.glob(os.path.join(CACHE, "pos_extra", "*.wav")))
    if extra:
        _log(f"including {len(extra)} user-recorded positive clips")
    if not pos_files or not neg_files:
        raise SystemExit("no cached clips - run without --skip-gen first")

    neg_raw = [_read_wav(f) for f in neg_files]
    n_var = 2 if quick else 4

    X, y = [], []
    for f in pos_files + extra:
        for w in _augment(_read_wav(f), neg_raw, n_var):
            X.append(w)
            y.append(1)
    for raw in neg_raw:
        for w in _augment(raw, neg_raw, n_var):
            X.append(w)
            y.append(0)
    # Pure noise / silence negatives - the mic is mostly hearing this.
    n_noise = 40 if quick else 300
    for i in range(n_noise):
        kind = i % 3
        if kind == 0:
            w = (NP_RNG.standard_normal(WINDOW) * RNG.uniform(50, 3000))
        elif kind == 1:
            w = _pink_noise(WINDOW) * RNG.uniform(500, 8000)
        else:
            w = np.zeros(WINDOW)
        X.append(w.astype(np.int16))
        y.append(0)

    X = np.stack(X)
    y = np.array(y, dtype=np.float32)
    _log(f"dataset: {len(y)} windows ({int(y.sum())} positive, "
         f"{int((1 - y).sum())} negative)")
    return X, y


# ---------------------------------------------------------------------------
# Phase C - openWakeWord features (frozen melspec + embedding models)
# ---------------------------------------------------------------------------
def extract_features(X: np.ndarray) -> np.ndarray:
    from openwakeword.utils import AudioFeatures
    af = AudioFeatures(inference_framework="onnx")
    _log("extracting openWakeWord embeddings (this feeds the same features the "
         "live detector computes)...")
    feats = af.embed_clips(X, batch_size=64)
    assert feats.shape[1:] == (16, 96), f"unexpected feature shape {feats.shape}"
    return feats.astype(np.float32)


# ---------------------------------------------------------------------------
# Phase D - tiny classifier (torch, pinned) + Phase E export/verify
# ---------------------------------------------------------------------------
def train_and_export(feats: np.ndarray, y: np.ndarray, quick: bool) -> None:
    _assert_torch_pin()
    import torch
    import torch.nn as nn

    torch.manual_seed(1234)
    idx = NP_RNG.permutation(len(y))
    split = int(len(y) * 0.85)
    tr, va = idx[:split], idx[split:]

    model = nn.Sequential(                       # mirrors the stock oww heads:
        nn.Flatten(),                            # (B,16,96) -> (B,1536)
        nn.Linear(16 * 96, 128), nn.ReLU(), nn.Dropout(0.3),
        nn.Linear(128, 64), nn.ReLU(),
        nn.Linear(64, 1), nn.Sigmoid(),          # -> (B,1) wake score
    )
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    lossf = nn.BCELoss()

    Xt = torch.from_numpy(feats)
    yt = torch.from_numpy(y).unsqueeze(1)
    epochs = 10 if quick else 40
    bs = 128
    for ep in range(epochs):
        model.train()
        perm = torch.from_numpy(NP_RNG.permutation(tr).copy())
        tot = 0.0
        for i in range(0, len(perm), bs):
            b = perm[i:i + bs]
            opt.zero_grad()
            loss = lossf(model(Xt[b]), yt[b])
            loss.backward()
            opt.step()
            tot += float(loss) * len(b)
        if (ep + 1) % 5 == 0 or ep == epochs - 1:
            model.eval()
            with torch.no_grad():
                pv = model(Xt[va])
                pred = (pv > 0.5).float()
                acc = float((pred == yt[va]).float().mean())
                pos_mask = yt[va] > 0.5
                recall = float(pred[pos_mask].mean()) if pos_mask.any() else 0.0
                fpr = float(pred[~pos_mask].mean()) if (~pos_mask).any() else 0.0
            _log(f"epoch {ep + 1:3d}/{epochs} loss={tot / len(tr):.4f} "
                 f"val_acc={acc:.3f} recall={recall:.3f} fpr={fpr:.3f}")

    # Export exactly like the stock openWakeWord phrase models: input (B,16,96)
    # float32 -> output (B,1) sigmoid. openwakeword.Model reads shape[1] of the
    # input (16) to size its sliding feature window - keep it static.
    model.eval()
    os.makedirs(OUT_DIR, exist_ok=True)
    dummy = torch.zeros(1, 16, 96)
    torch.onnx.export(model, dummy, MODEL_OUT,
                      input_names=["input"], output_names=["output"],
                      opset_version=11,
                      dynamic_axes={"input": {0: "batch"},
                                    "output": {0: "batch"}})
    _assert_torch_pin()
    _log(f"exported -> {MODEL_OUT} ({os.path.getsize(MODEL_OUT)} bytes)")


def streaming_selftest() -> bool:
    """Load the exported model through openwakeword.Model - the SAME loader the
    live WakeWordModule uses - and stream held-out audio through it."""
    from openwakeword.model import Model as OWWModel
    m = OWWModel(wakeword_models=[MODEL_OUT], inference_framework="onnx")

    def stream_score(pcm16: np.ndarray) -> float:
        m.reset()
        best = 0.0
        for i in range(0, len(pcm16) - 1280, 1280):
            s = m.predict(pcm16[i:i + 1280])
            best = max(best, float(s.get("hey_access", 0.0)))
        return best

    from kokoro_onnx import Kokoro
    kokoro = Kokoro(os.path.join(PROJECT, "models", "kokoro", "kokoro-v1.0.onnx"),
                    os.path.join(PROJECT, "models", "kokoro", "voices-v1.0.bin"))

    ok = True
    # Held-out condition: a speed (0.95) never used in training.
    for voice in ["af_heart", "am_michael", "bf_emma", "hm_omega"]:
        samples, _ = kokoro.create("Hey Access", voice=voice, speed=0.95,
                                   lang="en-us")
        clip = _to_16k_int16(samples)
        padded = np.concatenate([np.zeros(SR, np.int16), clip,
                                 np.zeros(SR, np.int16)])
        sc = stream_score(padded)
        _log(f"  self-test POSITIVE '{voice}': score={sc:.3f} "
             f"{'OK' if sc >= 0.5 else 'FAIL'}")
        ok &= sc >= 0.5
    for text in ["hey alexa", "package for you", "access granted"]:
        samples, _ = kokoro.create(text, voice="af_bella", speed=0.95,
                                   lang="en-us")
        clip = _to_16k_int16(samples)
        padded = np.concatenate([np.zeros(SR, np.int16), clip,
                                 np.zeros(SR, np.int16)])
        sc = stream_score(padded)
        _log(f"  self-test NEGATIVE '{text}': score={sc:.3f} "
             f"{'OK' if sc < 0.5 else 'FAIL'}")
        ok &= sc < 0.5
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true",
                    help="tiny smoke run (few voices/epochs) to verify the "
                         "pipeline; NOT a deployable model")
    ap.add_argument("--skip-gen", action="store_true",
                    help="reuse cached WAVs in models/wakeword/_train_cache/")
    args = ap.parse_args()

    os.makedirs(CACHE, exist_ok=True)
    if not args.skip_gen:
        synthesize(args.quick)
    X, y = build_dataset(args.quick)
    feats = extract_features(X)
    train_and_export(feats, y, args.quick)

    ok = streaming_selftest()
    if not ok:
        _log("SELF-TEST FAILED - hey_access.onnx left in place for inspection, "
             "but do NOT deploy it. Re-run without --quick, or add real "
             "recordings to _train_cache/pos_extra/ and re-run with --skip-gen.")
        return 1

    _log("OK: models/wakeword/hey_access.onnx trained + streaming-verified. "
         "Restart the backend; WakeWordModule will auto-load it and /status "
         "will show wakeword as ok (no longer a placeholder).")
    _log("NOTE: positives are synthetic (Kokoro voices) - the accepted "
         "openWakeWord bootstrap. For extra real-mic robustness, drop a few "
         "recorded 'Hey Access' WAVs (16 kHz mono) into "
         "models/wakeword/_train_cache/pos_extra/ and re-run with --skip-gen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
