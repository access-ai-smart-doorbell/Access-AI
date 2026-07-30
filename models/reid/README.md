# models/reid/ — OSNet re-identification model

Drop an OSNet ONNX file here (e.g. `osnet_x0_25.onnx`) and
`accessai/reid_module.py` auto-loads it on the next start — zero code change.
Until then the module uses the HSV colour-histogram placeholder.

Obtain the model with the helper script (download, local copy, or convert an
official torchreid checkpoint — torch pin 2.4.1 is asserted, nothing installed):

```bash
# from a mirror URL of a pre-converted onnx:
.venv/bin/python scripts/fetch_reid_model.py --url https://.../osnet_x0_25.onnx

# or convert an official checkpoint from
# https://kaiyangzhou.github.io/deep-person-reid/MODEL_ZOO :
.venv/bin/python scripts/fetch_reid_model.py --convert \
    --pth ~/Downloads/osnet_x0_25_msmt17_combineall.pth --variant x0_25
```

Expected interface (validated by the script):
input `1x3x256x128` float32 RGB (ImageNet-normalised) → flat ≥128-dim feature
vector (OSNet: 512).

After the script prints `OK: model validated`, tune `REID_MATCH_THRESHOLD` in
`config.py` — OSNet cosine similarities for the same person typically sit well
above 0.5, whereas the histogram placeholder needed 0.75.
