"""Render the figures for the AccessAI project report.

Run with the project venv (matplotlib lives there):
    .venv/bin/python docs/report/diagrams.py
PNGs are written to docs/report/figures/.
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "figures")
os.makedirs(OUT, exist_ok=True)

# ---------------------------------------------------------------- palette
INK = "#1c2431"
MUTED = "#5b6675"
LINE = "#93a1b3"
FILL = {
    "edge":   ("#e8f0fb", "#3f76bd"),   # door unit / capture
    "core":   ("#e6f2ea", "#3e8f62"),   # server core
    "ai":     ("#f3ebf9", "#7d55ab"),   # ML modules
    "out":    ("#fdeee4", "#c8703a"),   # accessibility output
    "store":  ("#eceff3", "#68758a"),   # persistence
    "client": ("#fdf3dc", "#b48a2a"),   # UI clients
    "warn":   ("#fbe6e6", "#b34a4a"),   # security / spoof
    "plain":  ("#f7f8fa", "#93a1b3"),
}
FONT = "DejaVu Sans"
plt.rcParams["font.family"] = FONT


def box(ax, x, y, w, h, text, kind="plain", fs=8.5, bold=False, radius=0.012):
    fc, ec = FILL[kind]
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h, boxstyle=f"round,pad=0.004,rounding_size={radius}",
        fc=fc, ec=ec, lw=1.15, zorder=2))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=fs, color=INK, zorder=3,
            fontweight="bold" if bold else "normal", linespacing=1.45)


def arrow(ax, p, q, style="-|>", color=None, lw=1.15, ls="-", rad=0.0, z=1):
    ax.add_patch(FancyArrowPatch(
        p, q, arrowstyle=style, mutation_scale=11, lw=lw, ls=ls,
        color=color or LINE, zorder=z,
        connectionstyle=f"arc3,rad={rad}", shrinkA=1, shrinkB=1))


def label(ax, x, y, text, fs=7.4, color=None, ha="center", style="normal", bold=False):
    ax.text(x, y, text, ha=ha, va="center", fontsize=fs,
            color=color or MUTED, style=style,
            fontweight="bold" if bold else "normal", zorder=4)


def canvas(w, h):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    return fig, ax


def save(fig, name):
    path = os.path.join(OUT, name)
    fig.savefig(path, dpi=210, bbox_inches="tight", facecolor="white",
                pad_inches=0.06)
    plt.close(fig)
    print("wrote", name)


# ============================================================ FIG 1
def fig_tiers():
    """Three-tier deployment architecture."""
    fig, ax = canvas(9.6, 6.4)

    ax.add_patch(Rectangle((0.005, 0.665), 0.99, 0.325, fc="#fafbfd",
                           ec="#dde3ea", lw=1, zorder=0))
    ax.add_patch(Rectangle((0.005, 0.225), 0.99, 0.415, fc="#fafbfd",
                           ec="#dde3ea", lw=1, zorder=0))
    ax.add_patch(Rectangle((0.005, 0.015), 0.99, 0.185, fc="#fafbfd",
                           ec="#dde3ea", lw=1, zorder=0))
    label(ax, 0.018, 0.966, "TIER 1  ·  CAPTURE", 7.6, "#8895a7", ha="left", bold=True)
    label(ax, 0.018, 0.616, "TIER 2  ·  AI PROCESSING SERVER  (Python 3.12, CPU-only)",
          7.6, "#8895a7", ha="left", bold=True)
    label(ax, 0.018, 0.176, "TIER 3  ·  ACCESSIBLE CLIENTS", 7.6, "#8895a7",
          ha="left", bold=True)

    # ---- Tier 1
    box(ax, 0.05, 0.755, 0.20, 0.175, "ESP32-CAM /\nESP32-S3-Sense\nMJPEG :81/stream", "edge", 8)
    box(ax, 0.29, 0.755, 0.18, 0.175, "Laptop webcam\n(development)", "edge", 8)
    box(ax, 0.51, 0.755, 0.17, 0.175, "Push button\n→ POST /ring", "edge", 8)
    box(ax, 0.72, 0.755, 0.22, 0.175, "PIR / frame-diff\nmotion trigger", "edge", 8)
    label(ax, 0.5, 0.718, "one-line swap:   CAMERA_SOURCE = 0   →   \"http://<ip>:81/stream\"",
          7.2, "#3f76bd", style="italic")

    arrow(ax, (0.15, 0.755), (0.15, 0.575), lw=1.3)
    arrow(ax, (0.59, 0.755), (0.59, 0.575), lw=1.3)
    label(ax, 0.315, 0.672, "Home Wi-Fi / LAN", 7.2)

    # ---- Tier 2 : orchestration row
    box(ax, 0.035, 0.485, 0.17, 0.088, "camera.py\ncapture", "core", 8)
    box(ax, 0.225, 0.485, 0.20, 0.088, "pipeline.py\n13-stage orchestrator", "core", 8, bold=True)
    box(ax, 0.445, 0.485, 0.17, 0.088, "context_engine\nintent cascade", "core", 8)
    box(ax, 0.635, 0.485, 0.17, 0.088, "accessibility\nannouncement", "out", 8)
    box(ax, 0.825, 0.485, 0.16, 0.088, "server.py\nFastAPI + WS", "core", 8)

    # ---- Tier 2 : perception / ML row
    ai = [("face_module\nArcFace 512-d", 0.035), ("vision_module\nYOLOv8n", 0.175),
          ("antispoof\nMiniFASNet", 0.315), ("vlm_module\nGPT-4o", 0.455),
          ("speech\nWhisper + VAD", 0.595), ("reid +\nauto-enroll", 0.735),
          ("tts / wakeword\nKokoro", 0.862)]
    for t, x in ai:
        w = 0.128 if x < 0.86 else 0.123
        box(ax, x, 0.363, w, 0.082, t, "ai", 7.1)
    for x in (0.12, 0.325, 0.53, 0.72):
        arrow(ax, (x, 0.485), (x, 0.447), lw=1.0, style="<|-|>")

    label(ax, 0.5, 0.334,
          "every heavy module sits behind an ENABLE_* flag  ·  missing dependency → degrade, never crash",
          7.0, "#7d55ab", style="italic")

    # ---- Tier 2 : persistence
    box(ax, 0.315, 0.245, 0.37, 0.058,
        "SQLite  ·  events · known_faces · reid_gallery · clusters", "store", 7.4)
    arrow(ax, (0.50, 0.363), (0.50, 0.305), lw=1.0)

    # ---- Tier 3
    box(ax, 0.06, 0.038, 0.24, 0.115, "Web dashboard + PWA\nlive MJPEG · history · voice", "client", 8)
    box(ax, 0.38, 0.038, 0.24, 0.115, "Flutter app (Android)\nRiverpod · background alerts", "client", 8)
    box(ax, 0.70, 0.038, 0.24, 0.115, "Blind Mode → speech\nDeaf Mode → flash + text", "out", 8)

    arrow(ax, (0.905, 0.485), (0.82, 0.158), lw=1.2, rad=0.08)
    arrow(ax, (0.315, 0.262), (0.18, 0.158), lw=1.0, rad=-0.12)
    arrow(ax, (0.55, 0.245), (0.50, 0.158), lw=1.0, rad=-0.12)
    label(ax, 0.735, 0.205, "REST  ·  WebSocket /events  ·  MJPEG /video", 7.2)
    save(fig, "fig01_tiers.png")


# ============================================================ FIG 2
def fig_pipeline():
    """The 13 ordered pipeline stages with the concurrency fork."""
    fig, ax = canvas(8.6, 9.6)

    stages = [
        ("0", "Construct VisitorEvent\nevt_<date>_<uuid6> · trigger", "core"),
        ("1", "", "ai"),
        ("2", "Build Person[] — name, conf, age, gender, box", "ai"),
        ("3", "Per-face anti-spoof · MiniFASNet\nspoof + known → downgrade to Unknown", "warn"),
        ("4", "Event-level mirror\nface_box ← largest · identity ← first live known", "core"),
        ("5", "Fold in objects · count_extra_people()\nvisitor_count = faces + faceless bodies", "ai"),
        ("6", "VLM gate → defer decision\nvlm_wanted ∧ async → enrich later", "ai"),
        ("7", "Speech · Silero VAD → Whisper", "ai"),
        ("8", "Translate transcript → user language", "ai"),
        ("9", "Memory · Re-ID match + auto-enroll buffer", "ai"),
        ("10", "Intent cascade · infer_intent()", "core"),
        ("11", "Compose announcement + cooldown gate\nspeak (Blind) / flash + text (Deaf)", "out"),
        ("12", "Save snapshot JPEG → persist event row", "store"),
    ]
    top, h, gap = 0.985, 0.048, 0.009
    y = top
    ys = []
    FORK_H = 0.088
    for i, (num, text, kind) in enumerate(stages):
        if num == "1":
            hh = FORK_H
        else:
            hh = h if "\n" not in text else h * 1.2
        box(ax, 0.115, y - hh, 0.70, hh, text, kind, 8.2)
        ax.text(0.085, y - hh / 2, num, ha="center", va="center", fontsize=9,
                color=MUTED, fontweight="bold")
        ys.append((y, y - hh))
        y -= hh + gap

    for i in range(len(ys) - 1):
        arrow(ax, (0.465, ys[i][1]), (0.465, ys[i + 1][0]), lw=1.1)

    # stage 1 interior: the two concurrent perception calls
    y1t, y1b = ys[1]
    label(ax, 0.465, y1t - 0.016, "PERCEPTION — 2-worker thread pool, both branches concurrent",
          7.5, "#7d55ab", bold=True)
    box(ax, 0.135, y1b + 0.012, 0.31, 0.046,
        "face.identify(frame)\nSCRFD detect → ArcFace 512-d", "plain", 7.2)
    box(ax, 0.485, y1b + 0.012, 0.31, 0.046,
        "vision.detect(frame)\nYOLOv8n · 80 COCO classes", "plain", 7.2)
    label(ax, 0.905, (y1t + y1b) / 2, "GIL released\ninside ONNX /\ntorch native", 6.8, "#7d55ab")

    # async enrich branch
    y6 = (ys[6][0] + ys[6][1]) / 2
    y12b = ys[12][1]
    box(ax, 0.845, y12b - 0.075, 0.145, 0.062,
        "vlm-enrich\nthread\n(daemon)", "ai", 7.2)
    arrow(ax, (0.815, y6), (0.917, y6), lw=1.0, ls="--", color="#7d55ab")
    ax.add_patch(FancyArrowPatch((0.917, y6), (0.917, y12b - 0.013),
                                 arrowstyle="-|>", mutation_scale=11, lw=1.0,
                                 ls="--", color="#7d55ab", zorder=1))
    label(ax, 0.917, y6 + 0.018, "defer", 6.8, "#7d55ab")

    box(ax, 0.115, y12b - 0.075, 0.68, 0.062,
        "RETURN event  →  WebSocket broadcast  →  dashboard · phone · TTS", "out", 8.3, bold=True)
    arrow(ax, (0.465, y12b), (0.465, y12b - 0.013), lw=1.2)
    arrow(ax, (0.845, y12b - 0.044), (0.80, y12b - 0.044), lw=1.0, ls="--", color="#7d55ab")
    label(ax, 0.917, y12b - 0.098, "second, richer\nbroadcast", 6.8, "#7d55ab")

    ax.text(0.028, 0.50,
            "run_once() holds a threading.Lock — concurrent /trigger, /ring and wake-word runs serialise",
            rotation=90, ha="center", va="center", fontsize=7.2, color="#3e8f62")
    save(fig, "fig02_pipeline.png")


# ============================================================ FIG 3
def fig_intent():
    """Rule cascade in context_engine.infer_intent."""
    fig, ax = canvas(8.8, 5.0)
    label(ax, 0.5, 0.965, "infer_intent(ev) — deterministic first-match-wins cascade",
          9, INK, bold=True)

    rows = [
        ("is_spoof", "possible spoof attempt", "0.60", "warn"),
        ("identity.known", "known visitor", "0.90", "core"),
        ("parcel object  AND  courier text (OCR)", "likely delivery", "0.85", "out"),
        ("parcel object only", "likely delivery", "0.65", "out"),
        ("visitor_count == 0  AND  no objects", "no visitor detected", "0.30", "plain"),
        ("— fallback —", "unknown visitor", "0.50", "plain"),
    ]
    y = 0.85
    for i, (cond, intent, conf, kind) in enumerate(rows):
        box(ax, 0.055, y - 0.085, 0.40, 0.085, cond, "plain", 8.2)
        box(ax, 0.53, y - 0.085, 0.30, 0.085, intent, kind, 8.4, bold=True)
        box(ax, 0.855, y - 0.085, 0.095, 0.085, conf, "plain", 8.4)
        arrow(ax, (0.455, y - 0.0425), (0.528, y - 0.0425), lw=1.0)
        ax.text(0.032, y - 0.0425, str(i + 1), ha="center", va="center",
                fontsize=8.5, color=MUTED, fontweight="bold")
        if i < len(rows) - 1:
            arrow(ax, (0.255, y - 0.085), (0.255, y - 0.115), lw=1.0)
            label(ax, 0.30, y - 0.10, "no", 6.8)
        y -= 0.115
    label(ax, 0.905, 0.895, "confidence", 7.2, MUTED)
    label(ax, 0.5, 0.075,
          "No training data, no classifier — the cascade is auditable: every announcement\n"
          "traces to one branch.  Courier text alone never yields \"delivery\" (read only inside the parcel branch).",
          7.6, MUTED, style="italic")
    save(fig, "fig03_intent.png")


# ============================================================ FIG 4
def fig_event_spine():
    """VisitorEvent as the shared data spine."""
    fig, ax = canvas(9.6, 5.4)
    label(ax, 0.5, 0.975, "The VisitorEvent spine — every module writes a field, every output reads one",
          9, INK, bold=True)

    writers = [
        ("face_module", "identity · face_box · age · gender", "ai"),
        ("antispoof", "is_spoof · spoof_score", "warn"),
        ("vision_module", "detected_objects · carried · extra_unknown", "ai"),
        ("vlm_module", "scene_summary · appearance · ocr_text", "ai"),
        ("speech_module", "speech_transcript · language_detected", "ai"),
        ("translate", "translated_transcript", "ai"),
        ("reid_module", "reid_id · reid_seen_count", "ai"),
        ("context_engine", "intent · confidence", "core"),
    ]
    y = 0.885
    for name, fields, kind in writers:
        box(ax, 0.015, y - 0.072, 0.135, 0.072, name, kind, 7.4)
        label(ax, 0.163, y - 0.036, fields, 6.4, MUTED, ha="left")
        arrow(ax, (0.415, y - 0.036), (0.462, y - 0.036), lw=0.95)
        y -= 0.098

    box(ax, 0.465, 0.145, 0.185, 0.755, "", "core")
    label(ax, 0.5575, 0.855, "VisitorEvent", 10, INK, bold=True)
    label(ax, 0.5575, 0.805, "30 fields · 3 dataclasses", 6.8, MUTED)
    ax.plot([0.495, 0.620], [0.775, 0.775], color=FILL["core"][1], lw=0.9, zorder=4)
    for i, f in enumerate(["event_id", "timestamp", "trigger", "identity",
                           "people[ ]", "visitor_count", "carried_objects",
                           "intent · confidence", "announcement_text",
                           "snapshot_path"]):
        label(ax, 0.5575, 0.735 - i * 0.055, f, 7.2, INK)

    readers = [
        ("accessibility", "compose_announcement()", "out"),
        ("tts_module", "speak · Blind Mode", "out"),
        ("web / PWA", "event card + flash", "client"),
        ("Flutter app", "alert overlay", "client"),
        ("database", "events table row", "store"),
        ("alert_kind()", "spoof · known · delivery", "plain"),
    ]
    y = 0.855
    for name, what, kind in readers:
        box(ax, 0.715, y - 0.082, 0.145, 0.082, name, kind, 7.4)
        label(ax, 0.870, y - 0.041, what, 6.4, MUTED, ha="left")
        arrow(ax, (0.655, y - 0.041), (0.710, y - 0.041), lw=0.95)
        y -= 0.115
    label(ax, 0.5, 0.055,
          "Adding a capability = add a field + one writer.  The pipeline is never restructured.",
          7.8, "#3e8f62", style="italic")
    save(fig, "fig04_spine.png")


# ============================================================ FIG 5
def fig_erd():
    """SQLite entity-relationship diagram."""
    fig, ax = canvas(9.0, 5.2)
    label(ax, 0.5, 0.97, "Persistence schema — SQLite via SQLAlchemy (data/accessai.db)",
          9, INK, bold=True)

    def table(x, y, w, title, cols, kind, note=""):
        hh = 0.052 + 0.032 * len(cols)
        ax.add_patch(FancyBboxPatch((x, y - hh), w, hh,
                                    boxstyle="round,pad=0.004,rounding_size=0.008",
                                    fc="white", ec=FILL[kind][1], lw=1.2, zorder=2))
        ax.add_patch(Rectangle((x, y - 0.042), w, 0.042,
                               fc=FILL[kind][0], ec=FILL[kind][1], lw=1.2, zorder=3))
        ax.text(x + w / 2, y - 0.021, title, ha="center", va="center",
                fontsize=8.2, color=INK, fontweight="bold", zorder=4)
        yy = y - 0.058
        for c in cols:
            ax.text(x + 0.012, yy, c, ha="left", va="center", fontsize=6.9,
                    color=MUTED, zorder=4)
            yy -= 0.032
        if note:
            ax.text(x + w / 2, y - hh - 0.022, note, ha="center", va="center",
                    fontsize=6.6, color=FILL[kind][1], style="italic", zorder=4)
        return (x, y, w, hh)

    table(0.30, 0.90, 0.40, "events   (37 rows)", [
        "id  PK          event_id  UNIQUE IDX", "timestamp  IDX      trigger",
        "identity_name / _known / _conf", "is_spoof · spoof_score · visitor_count",
        "carried_objects · scene_summary · ocr_text",
        "speech_transcript · language_detected",
        "translated_transcript · reid_id · seen_count",
        "intent · confidence · announcement_text",
        "snapshot_path · age · gender · appearance",
        "people (JSON TEXT) · extra_unknown",
    ], "core", "27 columns · 5 added by idempotent ALTER TABLE migration")

    table(0.025, 0.40, 0.235, "known_faces  (3)", [
        "id  PK", "name  IDX", "source_path", "embedding  BLOB", "created_at",
    ], "ai", "ArcFace 512-d float32")

    table(0.285, 0.40, 0.215, "reid_gallery  (2)", [
        "id  PK", "reid_id  IDX", "embedding  BLOB", "last_seen", "seen_count",
    ], "ai", "appearance signature")

    table(0.525, 0.40, 0.245, "unknown_face_clusters (24)", [
        "id  PK", "cluster_id  IDX", "embedding  BLOB", "event_id", "created_at", "suggested",
    ], "ai", "DBSCAN input buffer")

    table(0.795, 0.40, 0.18, "push_tokens (0)", [
        "id  PK", "token  UNIQUE", "platform", "mode", "created_at / last_seen",
    ], "client", "phone alerts")

    for x in (0.14, 0.39, 0.645, 0.885):
        arrow(ax, (x, 0.40), (x, 0.365), lw=0.9, ls=":")
    arrow(ax, (0.70, 0.62), (0.70, 0.405), lw=1.0, ls="--", rad=-0.35)
    label(ax, 0.815, 0.505, "event_id references", 6.8)
    label(ax, 0.5, 0.055,
          "Embeddings are stored as raw float32 BLOBs — never the source photograph.\n"
          "All writes are best-effort: a DB failure never blocks the live announcement path.",
          7.5, MUTED, style="italic")
    save(fig, "fig05_erd.png")


# ============================================================ FIG 6
def fig_counting():
    """count_extra_people: the multi-person reconciliation algorithm."""
    fig, ax = canvas(9.0, 4.2)
    label(ax, 0.5, 0.955, "Head-count reconciliation — vision_module.count_extra_people()",
          9, INK, bold=True)

    box(ax, 0.03, 0.72, 0.20, 0.115, "YOLOv8n person boxes\nconf ≥ 0.40", "ai", 8)
    box(ax, 0.28, 0.72, 0.19, 0.115, "Gate 1\nconf ≥ 0.60\n(stricter)", "warn", 8)
    box(ax, 0.52, 0.72, 0.20, 0.115, "Gate 2\nIoU < 0.55 vs\nboxes already kept", "warn", 8)
    box(ax, 0.77, 0.72, 0.20, 0.115, "Gate 3\nbox must NOT contain\na recognised face centre", "warn", 8)
    for a, b in ((0.23, 0.28), (0.47, 0.52), (0.72, 0.77)):
        arrow(ax, (a, 0.7775), (b, 0.7775), lw=1.1)

    box(ax, 0.30, 0.50, 0.40, 0.10,
        "extra_unknown  =  surviving faceless bodies", "core", 8.6, bold=True)
    arrow(ax, (0.87, 0.72), (0.70, 0.605), lw=1.1, rad=0.2)

    box(ax, 0.14, 0.32, 0.24, 0.085, "recognised faces\nlen(ev.people)", "ai", 8)
    box(ax, 0.62, 0.32, 0.24, 0.085, "extra_unknown", "ai", 8)
    box(ax, 0.32, 0.14, 0.36, 0.09, "visitor_count  =  faces  +  extra_unknown", "out", 8.8, bold=True)
    arrow(ax, (0.26, 0.32), (0.42, 0.232), lw=1.1, rad=-0.15)
    arrow(ax, (0.74, 0.32), (0.58, 0.232), lw=1.1, rad=0.15)
    arrow(ax, (0.50, 0.50), (0.74, 0.407), lw=1.0, rad=0.15)

    label(ax, 0.5, 0.055,
          "Replaces the naive  person_count − face_count,  which mis-counted one visitor as two\n"
          "when a weak duplicate box (conf 0.47) survived. Precision is favoured over recall.",
          7.5, MUTED, style="italic")
    save(fig, "fig06_counting.png")


# ============================================================ FIG 7
def fig_phases():
    """17-phase incremental build timeline."""
    fig, ax = canvas(9.4, 4.6)
    label(ax, 0.5, 0.965, "Incremental delivery — 17 phases, each ending in a demoable system",
          9, INK, bold=True)

    phases = [
        (1, "Foundation · VisitorEvent · FastAPI · SQLite", "core"),
        (2, "Face recognition · InsightFace ArcFace", "ai"),
        (3, "YOLOv8 objects + intent cascade", "ai"),
        (4, "Accessibility output · TTS · Blind/Deaf routing", "out"),
        (5, "Anti-spoofing · MiniFASNet liveness", "warn"),
        (6, "VLM scene description + parcel OCR", "ai"),
        (7, "Speech recognition · Whisper + VAD", "ai"),
        (8, "Translation · 11 languages", "ai"),
        (9, "Re-ID + DBSCAN auto-enrollment", "ai"),
        (10, "Wake word + voice commands + hardening", "out"),
        (11, "Natural voice · Kokoro-ONNX neural TTS", "out"),
        (12, "Speed · instant announce, async enrich", "core"),
        (13, "Photo enrollment from the browser", "client"),
        (14, "Mobile PWA · installable dashboard", "client"),
        (15, "Multi-person scenes · per-person boxes", "ai"),
        (16, "Native Flutter app", "client"),
        (17, "LAN hardening · auth · HMAC · motion", "warn"),
    ]
    x0, y0 = 0.075, 0.885
    for i, (n, text, kind) in enumerate(phases):
        col = i // 9
        row = i % 9
        x = x0 + col * 0.485
        y = y0 - row * 0.093
        box(ax, x, y - 0.072, 0.415, 0.072, text, kind, 7.6)
        ax.text(x - 0.028, y - 0.036, str(n), ha="center", va="center",
                fontsize=8.2, color=MUTED, fontweight="bold")
        if row < 8 and i != 16:
            arrow(ax, (x + 0.03, y - 0.072), (x + 0.03, y - 0.093), lw=0.9)
    arrow(ax, (0.49, 0.14), (0.545, 0.85), lw=0.9, ls="--", rad=-0.35)
    label(ax, 0.5, 0.045,
          "Rule held across every phase: heavy features sit behind ENABLE_* flags, so the base app always runs.",
          7.5, "#3e8f62", style="italic")
    save(fig, "fig07_phases.png")


def fig_tts_chain():
    """TTS three-tier fallback + earcon vocabulary."""
    fig, ax = canvas(9.4, 4.9)
    label(ax, 0.5, 0.965, "Speech output — three-tier fallback chain and the earcon vocabulary",
          9, INK, bold=True)

    tiers = [
        ("TIER A — Kokoro-ONNX", "offline neural · 24 kHz\nkokoro-v1.0.onnx (310 MB)\nvoice af_heart",
         "requires model + voices\nfile on disk", "ai"),
        ("TIER B — edge-tts", "cloud neural\nen-US-AriaNeural\n+ 10 Indic voices",
         "requires network;\n403 on some networks", "client"),
        ("TIER C — pyttsx3", "espeak, always available\nrate 165 wpm",
         "terminal fallback —\nnever silent", "plain"),
    ]
    x = 0.035
    for title, body, note, kind in tiers:
        box(ax, x, 0.60, 0.27, 0.235, f"{title}\n\n{body}", kind, 7.8)
        label(ax, x + 0.135, 0.565, note, 6.8, MUTED, style="italic")
        x += 0.315
    arrow(ax, (0.305, 0.717), (0.348, 0.717), lw=1.3)
    arrow(ax, (0.620, 0.717), (0.663, 0.717), lw=1.3)
    label(ax, 0.327, 0.752, "fail", 6.8, "#b34a4a")
    label(ax, 0.642, 0.752, "fail", 6.8, "#b34a4a")

    box(ax, 0.035, 0.435, 0.905, 0.075,
        "Playback is a SUBPROCESS — paplay → pw-play → aplay → ffplay\n"
        "(PortAudio/ALSA teardown corrupts the process heap in a long-lived server)",
        "warn", 7.6)

    label(ax, 0.5, 0.385, "Earcons — synthesised in numpy at 24 kHz, no asset files",
          8.2, INK, bold=True)
    ear = [
        ("known", "E5 → G#5  rising third", "659 Hz · 831 Hz", "core"),
        ("delivery", "three even C5 taps", "523 Hz × 3", "out"),
        ("unknown", "one plain A4", "440 Hz", "plain"),
        ("spoof", "A#4 → E4  falling tritone", "466 Hz · 330 Hz", "warn"),
    ]
    x = 0.035
    for name, shape, freq, kind in ear:
        box(ax, x, 0.205, 0.215, 0.14, f"{name}\n\n{shape}\n{freq}", kind, 7.4)
        x += 0.235
    label(ax, 0.5, 0.15,
          "Distinct in BOTH contour and rhythm — contour alone is lost on users with pitch-perception differences.",
          7.4, "#3e8f62", style="italic")
    label(ax, 0.5, 0.075,
          "The Deaf channel mirrors the same rhythms as vibration patterns:  known [150,80,300] · delivery [120,90,120,90,120]\n"
          "· unknown [400] · spoof [600,200,600,200,600]  — tactile and audio channels stay congruent.",
          7.2, MUTED)
    save(fig, "fig08_tts.png")


def fig_wakeword_training():
    """Offline synthetic wake-word training pipeline."""
    fig, ax = canvas(9.4, 5.6)
    label(ax, 0.5, 0.968,
          "Offline \"Hey Access\" wake-word training — no human recordings, no network",
          9, INK, bold=True)

    box(ax, 0.03, 0.80, 0.28, 0.115,
        "A · SYNTHESISE\nKokoro TTS, 54 voices\n× 2 texts × 3 speeds", "ai", 7.8)
    label(ax, 0.17, 0.772, "324 positive clips", 7.0, "#7d55ab")

    box(ax, 0.36, 0.80, 0.28, 0.115,
        "34 NEGATIVE phrases\n18 hard: hey axis · he acts\n16 context: package for you", "warn", 7.8)
    label(ax, 0.50, 0.772, "272 negative clips", 7.0, "#b34a4a")

    box(ax, 0.69, 0.80, 0.28, 0.115,
        "300 NOISE windows\nwhite · pink · silence\n(what the mic mostly hears)", "plain", 7.8)

    for x in (0.17, 0.50, 0.83):
        arrow(ax, (x, 0.80), (x, 0.735), lw=1.1)

    box(ax, 0.03, 0.615, 0.94, 0.115,
        "B · WINDOW + AUGMENT   —   2.0 s window @ 16 kHz, random offset (streaming detector sees misalignment)\n"
        "gain 0.4–1.2 · SNR 5–25 dB · noise 40 % white / 40 % pink / 20 % babble · reverb p=0.3 · variant 0 always clean",
        "core", 7.6)
    arrow(ax, (0.50, 0.615), (0.50, 0.555), lw=1.2)

    box(ax, 0.20, 0.435, 0.60, 0.115,
        "C · FEATURE EXTRACTION — openWakeWord's OWN frozen models\n"
        "melspectrogram → embedding →  (16, 96) per window\n"
        "training features are byte-identical to inference", "ai", 7.8)
    label(ax, 0.885, 0.492, "2,684 windows\n48 % pos / 52 % neg", 7.0, MUTED)
    arrow(ax, (0.50, 0.435), (0.50, 0.375), lw=1.2)

    box(ax, 0.23, 0.245, 0.54, 0.125,
        "D · CLASSIFIER HEAD\nFlatten(1536) → Linear 128 → ReLU → Dropout 0.3\n"
        "→ Linear 64 → ReLU → Linear 1 → Sigmoid\nAdam lr 1e-3 · BCE · 40 epochs · batch 128 · 85/15 split", "core", 7.6)
    label(ax, 0.855, 0.307, "≈205 k params", 7.0, MUTED)
    arrow(ax, (0.50, 0.245), (0.50, 0.185), lw=1.2)

    box(ax, 0.15, 0.075, 0.70, 0.105,
        "E · EXPORT + STREAMING SELF-TEST   →   hey_access.onnx  (821 KB, opset 11)\n"
        "reloaded through openwakeword.Model — the SAME loader the live detector uses\n"
        "held-out speed 0.95 (never trained) · positives must score ≥ 0.5, negatives < 0.5", "out", 7.6)
    label(ax, 0.5, 0.022,
          "Honest limitation: all positives are synthetic TTS, so self-test scores are not an estimate of field accuracy.",
          7.2, "#b34a4a", style="italic")
    save(fig, "fig09_wakeword_training.png")


def fig_security():
    """Phase 17 request lifecycle: auth, rate limit, HMAC."""
    fig, ax = canvas(9.6, 5.8)
    label(ax, 0.5, 0.972, "Request lifecycle — Phase 17 LAN hardening", 9, INK, bold=True)

    box(ax, 0.335, 0.885, 0.29, 0.055, "incoming HTTP request", "plain", 8.0)
    arrow(ax, (0.48, 0.885), (0.48, 0.845), lw=1.2)

    box(ax, 0.295, 0.765, 0.37, 0.078,
        "public path?\n/ · /app · /static/* · /app/*", "plain", 7.6)
    arrow(ax, (0.665, 0.804), (0.775, 0.804), lw=1.1)
    box(ax, 0.78, 0.772, 0.115, 0.064, "serve\nshell", "client", 7.4)
    label(ax, 0.72, 0.826, "yes", 6.8, "#3e8f62")
    arrow(ax, (0.48, 0.765), (0.48, 0.722), lw=1.2)
    label(ax, 0.513, 0.744, "no", 6.8)

    box(ax, 0.295, 0.638, 0.37, 0.084,
        "bearer token check\ncompare_digest(supplied, AUTH_TOKEN)\nheader OR ?token=", "warn", 7.2)
    arrow(ax, (0.665, 0.680), (0.775, 0.680), lw=1.1, color="#b34a4a")
    box(ax, 0.78, 0.648, 0.115, 0.064, "401", "warn", 7.4)
    arrow(ax, (0.48, 0.638), (0.48, 0.595), lw=1.2)

    box(ax, 0.295, 0.511, 0.37, 0.084,
        "expensive route? token bucket per IP\n/trigger /ring /ask /listen /hear_visitor\n12 per min · burst 4",
        "warn", 7.2)
    arrow(ax, (0.665, 0.553), (0.775, 0.553), lw=1.1, color="#b34a4a")
    box(ax, 0.78, 0.521, 0.115, 0.064, "429", "warn", 7.4)
    arrow(ax, (0.48, 0.511), (0.48, 0.468), lw=1.2)

    box(ax, 0.295, 0.392, 0.37, 0.076,
        "route handler\npipeline · db · broadcast", "core", 7.8)

    # HMAC side-path (left)
    box(ax, 0.012, 0.470, 0.255, 0.252,
        "/ring  —  HMAC path\n\nESP32 signs the raw body:\nX-Ring-Signature =\nHMAC-SHA256(secret, body)\n\n"
        "verified INSIDE the endpoint —\nmiddleware cannot read the body\nwithout consuming it\n\n"
        "the device never holds the\nuser's bearer token", "edge", 6.9)
    arrow(ax, (0.267, 0.520), (0.293, 0.445), lw=1.1, ls="--", color="#3f76bd")

    # WS (right, sits below the 429 chip so the two never collide)
    box(ax, 0.715, 0.372, 0.275, 0.128,
        "WebSocket /events — auth is checked INSIDE\nthe handler; HTTP middleware never observes\n"
        "upgrades.  Failure → close 4401.  Ping 30 s.", "client", 6.9)

    label(ax, 0.5, 0.335, "Known gaps — stated honestly", 8.2, "#b34a4a", bold=True)
    gaps = [
        "No replay protection on /ring — the signature covers only the body. A captured signed POST replays\n"
        "indefinitely. Fix: fold an X-Ring-Timestamp into the signed payload + a short window + a nonce cache.",
        "Shipped default is an OPEN appliance — AUTH_TOKEN = \"\" and CORS_ORIGINS = [\"*\"].",
        "Per-device mode overrides are unbounded and in-memory only;  /video has no concurrent-client cap.",
    ]
    y = 0.278
    for g in gaps:
        ax.text(0.045, y, "•", fontsize=8, color="#b34a4a", va="top")
        ax.text(0.068, y, g, fontsize=7.0, color=MUTED, va="top", linespacing=1.55)
        y -= 0.072 if "\n" in g else 0.046
    save(fig, "fig10_security.png")


def fig_antispoof():
    """Anti-spoof dual backend and the fail-open / fail-closed contract."""
    fig, ax = canvas(9.2, 4.6)
    label(ax, 0.5, 0.962, "Liveness detection — dual backend, asymmetric failure policy",
          9, INK, bold=True)

    box(ax, 0.055, 0.79, 0.245, 0.085, "face crop from\nStage-2 Person box", "plain", 7.8)
    arrow(ax, (0.30, 0.832), (0.355, 0.832), lw=1.1)

    box(ax, 0.36, 0.735, 0.29, 0.145,
        "BACKEND B — MiniFASNet ONNX\n2.7_80x80_MiniFASNetV2\n4_0_0_80x80_MiniFASNetV1SE\n"
        "crop scale parsed FROM THE FILENAME", "ai", 7.4)
    box(ax, 0.685, 0.735, 0.26, 0.145,
        "BACKEND C — heuristic\n0.65·focus + 0.20·colour\n− 0.45·moiré\nscore ceiling 0.85", "plain", 7.4)
    label(ax, 0.505, 0.712, "raw 0–255 BGR, NO /255 — the reference ToTensor has div(255) commented out",
          6.8, "#b34a4a", style="italic")
    label(ax, 0.815, 0.712, "128×128 FFT ring mask\ndetects screen pixel grids", 6.8, MUTED, style="italic")

    arrow(ax, (0.505, 0.735), (0.505, 0.665), lw=1.1)
    arrow(ax, (0.815, 0.735), (0.815, 0.665), lw=1.1, ls="--")
    label(ax, 0.883, 0.70, "if no .onnx", 6.6, MUTED)

    box(ax, 0.335, 0.575, 0.34, 0.088, "spoof_score ∈ [0, 1]\nis_live = score ≥ 0.55", "core", 8.2, bold=True)
    arrow(ax, (0.505, 0.575), (0.505, 0.51), lw=1.2)

    box(ax, 0.055, 0.375, 0.42, 0.13,
        "FAIL-OPEN\nmodule unavailable · invalid box · any exception\n→ score = 1.0, treated as REAL\n\n"
        "a genuine visitor is never locked out", "core", 7.6)
    box(ax, 0.525, 0.375, 0.42, 0.13,
        "FAIL-CLOSED\nconfident spoof on a KNOWN face\n→ p.known = False, p.name = \"Unknown\"\n\n"
        "a held-up photo loses its name", "warn", 7.6)
    arrow(ax, (0.44, 0.51), (0.30, 0.508), lw=1.0, rad=0.15)
    arrow(ax, (0.57, 0.51), (0.72, 0.508), lw=1.0, rad=-0.15)

    box(ax, 0.20, 0.245, 0.60, 0.075,
        "ev.is_spoof  =  all(p.is_spoof for p in people)", "core", 8.4, bold=True)
    label(ax, 0.5, 0.212,
          "The EVENT is a spoof only when every face is. A photo held beside a real visitor strips the photo's name\n"
          "but keeps the event live.", 7.2, MUTED, style="italic")

    box(ax, 0.11, 0.055, 0.78, 0.105,
        "Downstream consequences of ev.is_spoof:\n"
        "blocks the VLM entirely  ·  blocks re-ID and auto-enroll  ·  short-circuits intent to (\"possible spoof attempt\", 0.60)",
        "warn", 7.6)
    save(fig, "fig11_antispoof.png")


def fig_memory():
    """Re-ID gallery + DBSCAN auto-enrollment."""
    fig, ax = canvas(9.4, 5.0)
    label(ax, 0.5, 0.965, "Visitor memory — appearance re-ID and DBSCAN auto-enrollment",
          9, INK, bold=True)

    label(ax, 0.245, 0.905, "A ·  RE-IDENTIFICATION  (unknown, non-spoof visitors)", 8, INK, bold=True)
    box(ax, 0.02, 0.755, 0.20, 0.115, "largest YOLO\nperson box\n→ body crop", "plain", 7.4)
    box(ax, 0.245, 0.755, 0.22, 0.115,
        "OSNet x0_25 ONNX\n256×128 RGB, /255\nImageNet norm → 512-d", "ai", 7.4)
    box(ax, 0.02, 0.615, 0.445, 0.105,
        "fallback: HSV colour histogram, 640-d — keys on clothing colour", "plain", 7.2)
    arrow(ax, (0.22, 0.812), (0.242, 0.812), lw=1.1)

    box(ax, 0.02, 0.455, 0.445, 0.125,
        "cosine vs gallery  ·  threshold 0.90\nmatch → reuse reid_id, seen_count += 1\nelse → mint v_<uuid8>, seen_count = 1",
        "core", 7.6)
    arrow(ax, (0.2425, 0.615), (0.2425, 0.583), lw=1.1)
    label(ax, 0.2425, 0.418,
          "0.90, not 0.75: OSNet features come after a final ReLU, so every\n"
          "dimension is ≥ 0 and unrelated crops sit near 0.60 cosine.",
          6.9, "#b34a4a", style="italic")

    box(ax, 0.02, 0.275, 0.445, 0.085,
        "gallery TTL 24 h  ·  max 500 rows, oldest evicted first", "store", 7.4)

    ax.plot([0.487, 0.487], [0.07, 0.90], color="#dde3ea", lw=1.2)

    label(ax, 0.755, 0.905, "B ·  AUTO-ENROLLMENT  (frequent unknown faces)", 8, INK, bold=True)
    box(ax, 0.515, 0.755, 0.22, 0.115,
        "ArcFace embedding\nof the largest face\n→ cluster buffer", "ai", 7.4)
    box(ax, 0.76, 0.755, 0.225, 0.115,
        "re-cluster every\n3 additions\n(DBSCAN is O(n²))", "plain", 7.4)
    arrow(ax, (0.735, 0.812), (0.757, 0.812), lw=1.1)

    box(ax, 0.515, 0.575, 0.47, 0.14,
        "DBSCAN(eps = 0.35, min_samples = 3,\nmetric = \"cosine\")\n\n"
        "sklearn cosine distance = 1 − similarity, so eps 0.35\n"
        "means neighbours at similarity ≥ 0.65 — stricter than\nthe 0.45 recognition threshold", "core", 7.0)
    arrow(ax, (0.75, 0.755), (0.75, 0.718), lw=1.1)

    box(ax, 0.515, 0.425, 0.47, 0.085,
        "cluster id anchored on the earliest member row → stable across runs", "plain", 7.2)
    box(ax, 0.515, 0.29, 0.47, 0.105,
        "≥ 5 sightings  →  suggest \"Save this visitor?\"\nconfirm → renormalised MEAN embedding enrolled under a name",
        "out", 7.4)
    arrow(ax, (0.75, 0.425), (0.75, 0.398), lw=1.1)

    label(ax, 0.75, 0.238, "suggested state machine:  0 open → 1 prompted → 2 resolved", 7.0, MUTED)

    box(ax, 0.06, 0.055, 0.88, 0.115,
        "Why DBSCAN and not k-means:  the number of distinct strangers is unknown in advance, and a one-off caller\n"
        "must remain NOISE (label −1) rather than be forced into a group. Noise is the correct answer for most visitors.",
        "core", 7.6)
    save(fig, "fig12_memory.png")


def fig_threads():
    """Concurrency / thread model."""
    fig, ax = canvas(9.2, 4.8)
    label(ax, 0.5, 0.965, "Concurrency model — threads, locks and the serialisation boundary",
          9, INK, bold=True)

    box(ax, 0.30, 0.845, 0.40, 0.07, "uvicorn event loop  (asyncio)", "core", 8.2, bold=True)

    producers = [
        ("camera-loop\ncapture thread\nreconnect 2 s backoff", 0.02, "edge"),
        ("motion-detector\n~3 samples/s\nframe differencing", 0.205, "edge"),
        ("wakeword-listener\n80 ms frames\nalways-on mic", 0.39, "ai"),
        ("tts-worker\nqueue, max 8\nspeaks serially", 0.575, "out"),
        ("caption loop\n180 s hard stop", 0.76, "ai"),
    ]
    for t, x, k in producers:
        w = 0.175 if x < 0.75 else 0.22
        box(ax, x, 0.615, w, 0.115, t, k, 7.2)

    for x in (0.107, 0.2925, 0.4775, 0.6625):
        arrow(ax, (x, 0.73), (x, 0.845), lw=1.0, ls="--", rad=0.0)
    label(ax, 0.90, 0.78, "broadcast_threadsafe()\nrun_coroutine_threadsafe", 6.8, "#3e8f62")

    box(ax, 0.055, 0.425, 0.89, 0.115,
        "pipeline.run_once()  —  guarded by a single threading.Lock\n"
        "/trigger, /ring, motion and the wake word all arrive on different threads; the pipeline mutates shared\n"
        "cooldown state and speaks through one TTS engine, so WHOLE RUNS SERIALISE.",
        "warn", 7.6)
    for x in (0.2, 0.5, 0.8):
        arrow(ax, (x, 0.615), (x, 0.542), lw=1.0)

    box(ax, 0.075, 0.268, 0.38, 0.105,
        "inside a run — 2-worker pool\nface.identify ∥ vision.detect\nboth release the GIL in native code", "ai", 7.2)
    box(ax, 0.545, 0.268, 0.38, 0.105,
        "after a run — daemon thread\nvlm-enrich-<event_id>\none per deferred event, unbounded", "ai", 7.2)
    arrow(ax, (0.30, 0.425), (0.265, 0.378), lw=1.0, rad=0.1)
    arrow(ax, (0.70, 0.425), (0.735, 0.378), lw=1.0, rad=-0.1)

    label(ax, 0.5, 0.222, "Latency consequence — the two-phase announcement", 8.0, INK, bold=True)
    box(ax, 0.075, 0.075, 0.85, 0.125,
        "t ≈ 2–3 s     local detectors only  →  announcement spoken and event persisted immediately\n\n"
        "t ≈ 8–20 s   VLM returns  →  event updated, second richer broadcast, optional re-speak",
        "out", 7.8)
    save(fig, "fig13_threads.png")


def fig_announcement():
    """compose_announcement decision tree."""
    fig, ax = canvas(9.2, 5.6)
    label(ax, 0.5, 0.972, "compose_announcement() — subject selection and the shared tail",
          9, INK, bold=True)

    box(ax, 0.335, 0.885, 0.33, 0.062,
        "subjects = len(people) + extra_unknown", "core", 8.2, bold=True)
    arrow(ax, (0.42, 0.885), (0.25, 0.845), lw=1.1, rad=0.12)
    arrow(ax, (0.58, 0.885), (0.75, 0.845), lw=1.1, rad=-0.12)
    label(ax, 0.285, 0.862, "= 1", 7.0, MUTED)
    label(ax, 0.715, 0.862, "> 1", 7.0, MUTED)

    label(ax, 0.25, 0.822, "SINGLE SUBJECT — priority cascade", 7.8, INK, bold=True)
    single = [
        ("is_spoof", "\"Warning. A face was shown to the camera\nbut it appears to be a photo.\"", "warn"),
        ("known", "\"{name} is at the door.\"\nage/gender never spoken for a known person", "core"),
        ("reid ≥ 2", "\"The same unknown {desc} has come\n{n} times today.\"", "ai"),
        ("count > 1", "\"{n} unknown visitors are at the door.\"", "plain"),
        ("count = 1", "\"An unknown {desc} is at the door.\"", "plain"),
        ("else", "\"The doorbell rang but no one\nis clearly visible.\"", "plain"),
    ]
    y = 0.795
    for cond, text, kind in single:
        box(ax, 0.025, y - 0.082, 0.10, 0.082, cond, kind, 7.0)
        box(ax, 0.135, y - 0.082, 0.33, 0.082, text, "plain", 6.9)
        y -= 0.095

    label(ax, 0.75, 0.822, "MULTI-SUBJECT", 7.8, INK, bold=True)
    box(ax, 0.515, 0.695, 0.47, 0.082,
        "all faces spoofed →  \"Warning. The faces shown to the\ncamera appear to be photos.\"", "warn", 6.9)
    box(ax, 0.515, 0.575, 0.47, 0.105,
        "COMPACT  (a clean VLM scene sentence exists)\n\"Alex is at the door with 4 other people.\"\n"
        "avoids a robotic per-person age/gender roster", "core", 6.9)
    box(ax, 0.515, 0.425, 0.47, 0.135,
        "FULL roster  ·  cap 3 described unknowns\n\"Alex is at the door, along with an unknown man\nin their thirties, and 2 other people.\"\n"
        "+ one detail sentence per known person", "plain", 6.9)
    label(ax, 0.75, 0.398,
          "a spoofed known face is demoted to unknown before the roster is built", 6.7, "#b34a4a", style="italic")

    arrow(ax, (0.25, 0.225), (0.25, 0.19), lw=1.1)
    arrow(ax, (0.75, 0.425), (0.75, 0.19), lw=1.1)

    box(ax, 0.055, 0.075, 0.89, 0.115,
        "SHARED TAIL — appended identically to both paths\n"
        "1. \"Carrying {objects}.\"        2. \"Likely a delivery. Label reads: {ocr[:60]}.\"        "
        "3. They said: \"{translated or original}\".", "out", 7.6)
    label(ax, 0.5, 0.030,
          "A JSON guard rejects any scene string starting with { or containing \"people\" / \"appearance\".\n"
          "Reading raw JSON aloud to a blind user is worse than saying nothing.",
          7.0, "#3e8f62", style="italic")
    save(fig, "fig14_announcement.png")


if __name__ == "__main__":
    fig_tiers()
    fig_pipeline()
    fig_intent()
    fig_event_spine()
    fig_erd()
    fig_counting()
    fig_phases()
    fig_tts_chain()
    fig_wakeword_training()
    fig_security()
    fig_antispoof()
    fig_memory()
    fig_threads()
    fig_announcement()
    print("\nall figures done ->", OUT)
