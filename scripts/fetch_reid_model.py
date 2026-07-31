#!/usr/bin/env python3
"""
fetch_reid_model.py - obtain the OSNet ONNX re-identification model.

WHAT THIS DOES
--------------
AccessAI's re-ID backend "A" (accessai/reid_module.py) auto-loads the FIRST
*.onnx it finds in models/reid/ and runs it on onnxruntime (already a Phase-2
dependency). Until such a file exists, the module falls back to the HSV
colour-histogram PLACEHOLDER, which keys mostly on clothing colour.

This script obtains an OSNet model and drops it into models/reid/ as:

    osnet_x0_25.onnx      (or osnet_x1_0.onnx with --variant x1_0)

Expected interface (what reid_module._embed_onnx feeds/reads):
    input   1x3x256x128 float32, RGB, ImageNet-normalised
    output  a flat feature vector (512 dims for OSNet) - L2-normalised by the
            caller, cosine-matched against the 24 h gallery.

TORCH SAFETY (hard constraint)
------------------------------
Same rule as fetch_antispoof_models.py: this script must NOT move torch off the
pinned 2.4.1 build. Acquisition paths, tried in order:

  1) DIRECT ONNX DOWNLOAD (torch-free): pull a pre-converted OSNet .onnx from a
     URL you supply via --url (or REID_URL env var). Nothing is installed; the
     file is validated with the EXISTING onnxruntime.

  2) LOCAL DIR (torch-free): --from-dir /path/to/dir copies + validates an
     .onnx you already have.

  3) CONVERT-FROM-TORCH (opt-in, --convert --pth <file>): converts an official
     torchreid OSNet checkpoint (e.g. osnet_x0_25 trained on Market-1501 or the
     multi-source 'MS+D+C' blend) to ONNX. The OSNet architecture is EMBEDDED
     below (adapted from KaiyangZhou/deep-person-reid, MIT licence), so no
     torchreid install is needed - the script imports only the pinned torch,
     installs NOTHING, and asserts torch==2.4.1 before and after.

     Checkpoints: https://kaiyangzhou.github.io/deep-person-reid/MODEL_ZOO
     (osnet_x0_25 ~ 0.7 MB of weights - ideal for CPU; x1_0 is the full model).

Every obtained file is LOADED with onnxruntime and given a dummy 1x3x256x128
forward pass; a file that does not produce a >=128-dim feature vector is
rejected and deleted, so a corrupt/wrong download never silently becomes the
active re-ID model.

USAGE
-----
  # Direct download (recommended) - supply a mirror URL for a pre-built onnx:
  .venv/bin/python scripts/fetch_reid_model.py --url https://.../osnet_x0_25.onnx

  # Local file you already downloaded:
  .venv/bin/python scripts/fetch_reid_model.py --from-dir ~/Downloads

  # Convert an official torchreid .pth (imports torch, pin-checked):
  .venv/bin/python scripts/fetch_reid_model.py --convert \
      --pth ~/Downloads/osnet_x0_25_msmt17_combineall.pth --variant x0_25

After it prints "OK: model validated", restart the backend; reid_module will
log "ONNX OSNet re-ID loaded" and /status will show reid as ok (no longer a
placeholder).

This script is meant to be run WITH the user (network/torch touch points), per
the project's torch-safety review rule.
"""
import argparse
import os
import shutil
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
OUT_DIR = os.path.join(PROJECT, "models", "reid")

# OSNet channel widths per variant (torchreid's standard configurations).
VARIANTS = {
    "x0_25": [16, 64, 96, 128],
    "x0_5":  [32, 128, 192, 256],
    "x0_75": [48, 192, 288, 384],
    "x1_0":  [64, 256, 384, 512],
}


def _log(msg: str) -> None:
    print(f"[fetch-reid] {msg}", flush=True)


def _download(url: str, dest: str) -> None:
    _log(f"downloading {url}")
    tmp = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": "AccessAI-fetch"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    os.replace(tmp, dest)
    _log(f"saved -> {dest} ({os.path.getsize(dest)} bytes)")


def _validate_onnx(path: str) -> bool:
    """Load with the EXISTING onnxruntime and run one dummy 1x3x256x128 pass.
    Accept only a flat feature vector of >=128 dims (OSNet gives 512)."""
    try:
        import numpy as np
        import onnxruntime as ort
    except Exception as e:
        _log(f"cannot import onnxruntime/numpy to validate ({e}); "
             "leaving file in place UNVALIDATED.")
        return True  # don't delete on a tooling gap; user can re-run validation
    try:
        sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
        inp = sess.get_inputs()[0]
        shape = inp.shape
        h = shape[2] if isinstance(shape[2], int) else 256
        w = shape[3] if isinstance(shape[3], int) else 128
        if (h, w) != (256, 128):
            _log(f"REJECT {os.path.basename(path)}: input is {h}x{w}, "
                 "reid_module feeds 256x128")
            return False
        # Batch: several public OSNet exports bake a FIXED batch (the mirrored
        # osnet_x0_25_msmt17.onnx wants exactly 16) rather than a dynamic dim.
        # reid_module tiles its single crop to match, so honour it here too -
        # rejecting the file would strand the user on the colour histogram.
        n = shape[0] if isinstance(shape[0], int) and shape[0] > 1 else 1
        dummy = np.zeros((n, 3, h, w), dtype=np.float32)
        out = sess.run(None, {inp.name: dummy})[0]
        vec = np.asarray(out)[0].reshape(-1) if n > 1 else \
            np.asarray(out).reshape(-1)
        if vec.shape[0] >= 128:
            batch_note = f", fixed batch {n} (tiled at inference)" if n > 1 else ""
            _log(f"validated {os.path.basename(path)}: "
                 f"{vec.shape[0]}-dim feature OK{batch_note}")
            return True
        _log(f"REJECT {os.path.basename(path)}: output size {vec.shape[0]} "
             "(want a >=128-dim feature vector)")
        return False
    except Exception as e:
        _log(f"REJECT {os.path.basename(path)}: failed to load/run ({e})")
        return False


def _assert_torch_pin() -> None:
    """Refuse to run the torch conversion path unless torch is still 2.4.1."""
    import torch
    v = torch.__version__
    if not v.startswith("2.4.1"):
        raise SystemExit(
            f"TORCH SAFETY ABORT: torch is {v}, expected 2.4.1.x. "
            "Refusing to touch models to avoid masking a pin drift.")
    _log(f"torch pin OK: {v}")


# ---------------------------------------------------------------------------
# Embedded OSNet (feature-extraction mode) - adapted from
# KaiyangZhou/deep-person-reid (MIT licence), trimmed to inference essentials so
# NO torchreid install is required for --convert.
# ---------------------------------------------------------------------------
def _build_osnet(variant: str):
    import torch
    from torch import nn
    import torch.nn.functional as F

    class ConvLayer(nn.Module):
        def __init__(self, cin, cout, k, s=1, p=0, groups=1, IN=False):
            super().__init__()
            self.conv = nn.Conv2d(cin, cout, k, stride=s, padding=p,
                                  bias=False, groups=groups)
            self.bn = nn.InstanceNorm2d(cout, affine=True) if IN \
                else nn.BatchNorm2d(cout)
            self.relu = nn.ReLU(inplace=True)

        def forward(self, x):
            return self.relu(self.bn(self.conv(x)))

    class Conv1x1(ConvLayer):
        def __init__(self, cin, cout, s=1, groups=1):
            super().__init__(cin, cout, 1, s=s, p=0, groups=groups)

    class Conv1x1Linear(nn.Module):
        def __init__(self, cin, cout, s=1):
            super().__init__()
            self.conv = nn.Conv2d(cin, cout, 1, stride=s, padding=0, bias=False)
            self.bn = nn.BatchNorm2d(cout)

        def forward(self, x):
            return self.bn(self.conv(x))

    class LightConv3x3(nn.Module):
        def __init__(self, cin, cout):
            super().__init__()
            self.conv1 = nn.Conv2d(cin, cout, 1, stride=1, padding=0, bias=False)
            self.conv2 = nn.Conv2d(cout, cout, 3, stride=1, padding=1,
                                   bias=False, groups=cout)
            self.bn = nn.BatchNorm2d(cout)
            self.relu = nn.ReLU(inplace=True)

        def forward(self, x):
            return self.relu(self.bn(self.conv2(self.conv1(x))))

    class ChannelGate(nn.Module):
        def __init__(self, cin, num_gates=None, gate_activation="sigmoid",
                     reduction=16, layer_norm=False):
            super().__init__()
            num_gates = num_gates or cin
            self.global_avgpool = nn.AdaptiveAvgPool2d(1)
            self.fc1 = nn.Conv2d(cin, cin // reduction, 1, bias=True, padding=0)
            self.norm1 = nn.LayerNorm((cin // reduction, 1, 1)) \
                if layer_norm else None
            self.relu = nn.ReLU(inplace=True)
            self.fc2 = nn.Conv2d(cin // reduction, num_gates, 1, bias=True,
                                 padding=0)
            self.gate_activation = nn.Sigmoid()

        def forward(self, x):
            inp = x
            x = self.global_avgpool(x)
            x = self.fc1(x)
            if self.norm1 is not None:
                x = self.norm1(x)
            x = self.relu(x)
            x = self.gate_activation(self.fc2(x))
            return inp * x

    class OSBlock(nn.Module):
        def __init__(self, cin, cout, IN=False, bottleneck_reduction=4):
            super().__init__()
            mid = cout // bottleneck_reduction
            self.conv1 = Conv1x1(cin, mid)
            self.conv2a = LightConv3x3(mid, mid)
            self.conv2b = nn.Sequential(LightConv3x3(mid, mid),
                                        LightConv3x3(mid, mid))
            self.conv2c = nn.Sequential(LightConv3x3(mid, mid),
                                        LightConv3x3(mid, mid),
                                        LightConv3x3(mid, mid))
            self.conv2d = nn.Sequential(LightConv3x3(mid, mid),
                                        LightConv3x3(mid, mid),
                                        LightConv3x3(mid, mid),
                                        LightConv3x3(mid, mid))
            self.gate = ChannelGate(mid)
            self.conv3 = Conv1x1Linear(mid, cout)
            self.downsample = Conv1x1Linear(cin, cout) if cin != cout else None
            self.IN = nn.InstanceNorm2d(cout, affine=True) if IN else None

        def forward(self, x):
            identity = x
            x1 = self.conv1(x)
            x2 = (self.gate(self.conv2a(x1)) + self.gate(self.conv2b(x1))
                  + self.gate(self.conv2c(x1)) + self.gate(self.conv2d(x1)))
            x3 = self.conv3(x2)
            if self.downsample is not None:
                identity = self.downsample(identity)
            out = x3 + identity
            if self.IN is not None:
                out = self.IN(out)
            return F.relu(out)

    class OSNet(nn.Module):
        """OSNet in feature-extraction mode: forward() returns the 512-dim
        embedding (the classifier head is not built)."""

        def __init__(self, channels, feature_dim=512):
            super().__init__()
            self.conv1 = ConvLayer(3, channels[0], 7, s=2, p=3)
            self.maxpool = nn.MaxPool2d(3, stride=2, padding=1)
            self.conv2 = nn.Sequential(
                OSBlock(channels[0], channels[1]),
                OSBlock(channels[1], channels[1]),
                nn.Sequential(Conv1x1(channels[1], channels[1]),
                              nn.AvgPool2d(2, stride=2)))
            self.conv3 = nn.Sequential(
                OSBlock(channels[1], channels[2]),
                OSBlock(channels[2], channels[2]),
                nn.Sequential(Conv1x1(channels[2], channels[2]),
                              nn.AvgPool2d(2, stride=2)))
            self.conv4 = nn.Sequential(OSBlock(channels[2], channels[3]),
                                       OSBlock(channels[3], channels[3]))
            self.conv5 = Conv1x1(channels[3], channels[3])
            self.global_avgpool = nn.AdaptiveAvgPool2d(1)
            self.fc = nn.Sequential(
                nn.Linear(channels[3], feature_dim),
                nn.BatchNorm1d(feature_dim),
                nn.ReLU(inplace=True))

        def forward(self, x):
            x = self.maxpool(self.conv1(x))
            x = self.conv5(self.conv4(self.conv3(self.conv2(x))))
            x = self.global_avgpool(x).flatten(1)
            return self.fc(x)

    return OSNet(VARIANTS[variant])


def _convert_from_pth(pth_path: str, dest: str, variant: str) -> None:
    """Convert an official torchreid OSNet .pth to ONNX. Imports the pinned
    torch, installs nothing; the pin is asserted before AND after."""
    _assert_torch_pin()
    import torch

    model = _build_osnet(variant)
    state = torch.load(pth_path, map_location="cpu", weights_only=False)
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    state = {k.replace("module.", ""): v for k, v in state.items()}
    # The checkpoint carries a classifier head over training identities; we
    # export feature-extraction mode, so that head is expected-missing.
    dropped = [k for k in state if k.startswith("classifier.")]
    state = {k: v for k, v in state.items() if not k.startswith("classifier.")}
    missing, unexpected = model.load_state_dict(state, strict=False)
    if unexpected:
        raise SystemExit(f"checkpoint does not match OSNet-{variant}: "
                         f"unexpected keys {unexpected[:5]}...")
    if missing:
        raise SystemExit(f"checkpoint is missing OSNet-{variant} weights: "
                         f"{missing[:5]}... (wrong --variant?)")
    if dropped:
        _log(f"dropped {len(dropped)} classifier-head tensors (train-time only)")
    model.eval()
    dummy = torch.zeros(1, 3, 256, 128)
    torch.onnx.export(model, dummy, dest,
                      input_names=["input"], output_names=["features"],
                      opset_version=11, dynamic_axes=None)
    _assert_torch_pin()   # export must not have dragged torch anywhere
    _log(f"converted {os.path.basename(pth_path)} -> {dest}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("REID_URL"),
                    help="URL to a pre-converted OSNet .onnx")
    ap.add_argument("--from-dir",
                    help="Local dir already containing an OSNet .onnx")
    ap.add_argument("--convert", action="store_true",
                    help="Convert from a torchreid .pth (imports torch, "
                         "pin-checked; architecture is embedded - no installs)")
    ap.add_argument("--pth", help="Path to the OSNet .pth (with --convert)")
    ap.add_argument("--variant", default="x0_25", choices=sorted(VARIANTS),
                    help="OSNet width (default x0_25 - lightest, ideal on CPU)")
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    dest = os.path.join(args.out_dir, f"osnet_{args.variant}.onnx")

    if args.convert:
        if not args.pth:
            ap.error("--convert requires --pth")
        _convert_from_pth(args.pth, dest, args.variant)
    elif args.from_dir:
        hits = [f for f in sorted(os.listdir(args.from_dir))
                if f.endswith(".onnx")]
        if not hits:
            raise SystemExit(f"no .onnx in {args.from_dir}")
        src = os.path.join(args.from_dir, hits[0])
        _log(f"copying {src}")
        shutil.copy2(src, dest)
    elif args.url:
        _download(args.url, dest)
    else:
        ap.error("supply --url, --from-dir, or --convert --pth (see --help; "
                 "checkpoints at kaiyangzhou.github.io/deep-person-reid/MODEL_ZOO)")

    if not _validate_onnx(dest):
        os.remove(dest)
        _log("model REJECTED and removed - reid_module keeps the histogram "
             "placeholder. Check the source file / --variant and retry.")
        return 1

    _log(f"OK: model validated -> {dest}")
    _log("Restart the backend; reid_module will log 'ONNX OSNet re-ID loaded' "
         "and /status will show reid as ok (no longer a placeholder).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
