#!/usr/bin/env bash
# install_deps.sh - the ONE reproducible way to build AccessAI's environment.
#
# WHY THIS EXISTS
# ---------------
# `pip install -r requirements.txt` alone does NOT reproduce a working install,
# because one dependency cannot be expressed in a requirements file:
#
#   kokoro-onnx 0.4.9 (the natural offline voice) DECLARES numpy>=2.0.2 and
#   onnxruntime>=1.20.1. Those bounds are over-tight - it is pure-Python ONNX
#   glue and runs fine on our pinned numpy 1.26.4 / onnxruntime 1.18.1 - but pip
#   would "helpfully" upgrade both and silently break YOLO and insightface.
#   So it MUST be installed with --no-deps, which requirements.txt cannot say.
#
# Skipping that step doesn't fail loudly; it just leaves the doorbell speaking
# in the robotic espeak fallback voice. Hence this script.
#
# USAGE
#   python3 -m venv .venv && source .venv/bin/activate
#   ./scripts/install_deps.sh            # full install + verification
#   ./scripts/install_deps.sh --verify   # verification only (no installing)
set -euo pipefail

cd "$(dirname "$0")/.."
PY="${PY:-python3}"

verify() {
    echo
    echo "== Verifying the pins survived =="
    $PY - <<'PYCODE'
import sys
import torch, torchvision, numpy, onnxruntime
want = {"torch": "2.4.1", "torchvision": "0.19.1",
        "numpy": "1.26.4", "onnxruntime": "1.18.1"}
got = {"torch": torch.__version__.split("+")[0],
       "torchvision": torchvision.__version__.split("+")[0],
       "numpy": numpy.__version__,
       "onnxruntime": onnxruntime.__version__}
bad = {k: (got[k], v) for k, v in want.items() if got[k] != v}
for k, v in got.items():
    print(f"   {k:<12} {v}" + ("" if k not in bad else f"   !! expected {want[k]}"))
if bad:
    sys.exit("FAIL: the pinned stack moved. See constraints.txt.")

# The real regression test: a moved torch breaks YOLO SILENTLY (zero detections,
# no exception). Anything less than a real predict() would not catch it.
import collections
from ultralytics import YOLO
m = YOLO("yolov8n.pt")
r = m.predict("bus.jpg", verbose=False)[0]
counts = collections.Counter(m.names[int(b.cls)] for b in r.boxes)
print(f"   YOLO bus.jpg {dict(counts)}")
if counts.get("person", 0) < 3 or counts.get("bus", 0) < 1:
    sys.exit("FAIL: YOLO regressed (expected >=3 persons + 1 bus). "
             "Something moved torch - check the last install.")

# Natural voice present? Not fatal, but the whole point of step 2.
try:
    import kokoro_onnx  # noqa: F401
    print("   kokoro-onnx  present (natural offline voice available)")
except ImportError:
    print("   kokoro-onnx  MISSING -> the doorbell will use the robotic "
          "espeak fallback. Re-run without --verify.")
print("OK: environment verified.")
PYCODE
}

if [[ "${1:-}" == "--verify" ]]; then
    verify
    exit 0
fi

echo "== 1/3  Everything resolvable, under the hard constraints =="
$PY -m pip install -r requirements.txt -c constraints.txt

echo
echo "== 2/3  kokoro-onnx WITHOUT deps (its numpy/onnxruntime bounds are over-tight) =="
# Its own safe deps are already declared in requirements.txt (edge-tts,
# soundfile, colorlog, espeakng-loader, phonemizer-fork), so --no-deps leaves
# nothing missing.
$PY -m pip install --no-deps kokoro-onnx==0.4.9

echo
echo "== 3/3  Verification =="
verify

cat <<'NEXT'

Next steps:
  * Model files that are NOT pip-installable (each degrades gracefully if absent):
      models/kokoro/     - natural voice, ~336 MB, see requirements.txt for URLs
      models/antispoof/  - .venv/bin/python scripts/fetch_antispoof_models.py
      models/reid/       - .venv/bin/python scripts/fetch_reid_model.py --help
      models/wakeword/   - .venv/bin/python scripts/train_wakeword.py  (offline)
  * Optional, only if you enable FCM background push (ENABLE_PUSH):
      pip install -c constraints.txt google-auth
  * System libs:  sudo apt install -y espeak libportaudio2 ffmpeg
NEXT
