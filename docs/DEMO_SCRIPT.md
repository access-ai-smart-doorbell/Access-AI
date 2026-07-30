# AccessAI — Live Demo Script (≈5–7 minutes)

A rehearsed runbook for the review panel. Goal: show the doorbell turning a
meaningless chime into *meaning*, delivered through Blind and Deaf modes. Practice
this end-to-end at least twice before the panel.

---

## 0. BEFORE THE PANEL WALKS IN (setup — 10 min)

**Environment**
- [ ] `cd ~/AccessAI && source .venv/bin/activate`
- [ ] Speakers ON and audible (this is a talking doorbell — they must HEAR it).
- [ ] Webcam clear, decent lighting (recognition degrades in poor light).
- [ ] `.env` has real `GITHUB_MODELS_KEYS` if you want live VLM scene description.
      (If offline/no keys, the system gracefully uses YOLO-only — still demoable.)
- [ ] `python3 run.py` → open `http://localhost:8000`.
- [ ] Open **GET /status** in a second tab → confirm the health panel: Face ok,
      Vision ok, Speech ok, TTS ok. This is your safety net — glance at it now.

**Enroll the "known person" (do this BEFORE the demo)**
- [ ] Stand in view → type your name in the Enroll panel → "Enroll from Live View".
      OR: `python3 enroll.py "YourName" --camera`
- [ ] Confirm `GET /known` shows your name. Test one Ring → you're recognised.

**Props to have ready**
- [ ] A parcel / cardboard box (ideally with a courier label like "BlueDart").
- [ ] A backpack or handbag.
- [ ] A printed photo of a face OR a phone showing a face (for the spoof demo).
- [ ] A second person, or a photo of a different person (for repeat-visitor).
- [ ] (Optional) a phrase to speak at the door: "I have a package for you."

**Reset history for a clean slate (optional)**
- [ ] Stop the server, delete `data/accessai.db`, restart. Fresh history list.

---

## 1. THE HOOK (30 sec — say this, don't click yet)

> "An ordinary doorbell communicates one fact — *someone is here* — through a
> sound. For a deaf person that sound is inaccessible. For a blind person it says
> nothing about *who* is outside or *why*. AccessAI replaces the chime with a
> sentence. Everything you're about to see runs **locally, on this laptop** — no
> cloud, no subscription — and the same code moves to a ₹2,700 ESP32 doorbell by
> changing one line. Let me show you."

---

## 2. KNOWN VISITOR — the core promise (45 sec)

1. Stand in front of the camera yourself.
2. Click **🔔 Ring Doorbell**.
3. The laptop SPEAKS: *"<YourName> is at the door."*
4. Point to the dashboard card: green border, your name, confidence.

> "It recognised me by face — using face embeddings, not stored photos, for
> privacy — and *spoke* my name. A blind user now knows exactly who is outside."

---

## 3. UNKNOWN VISITOR + DELIVERY — scene understanding (60 sec)

1. Have a helper (or yourself, if solo, after de-enrolling isn't needed — just use
   a parcel) hold the **parcel/backpack** in view.
2. Click **Ring**.
3. It SPEAKS something like: *"An unknown visitor is at the door. Carrying a
   package. Likely a delivery."* — and if VLM keys are set and a label is visible:
   *"Label reads: BlueDart."*
4. Point to the card: amber border (unknown), carried objects, "📦 Delivery" chip,
   scene description line.

> "No known face — so it describes the scene. YOLO detected the package; the
> context engine inferred *likely a delivery* — note it says **likely**, never
> *definitely*. Honesty about uncertainty is a trust decision for a system a blind
> user relies on. If a courier label is visible, a vision-language model reads it."

*(If no keys/offline: "Running fully offline right now, so it's using the local
object detector — the cloud scene description is optional and degrades gracefully.")*

---

## 4. THE SECURITY MOMENT — anti-spoofing (45 sec)

1. Hold up a **printed photo / phone screen** showing your (the known) face.
2. Click **Ring**.
3. It does NOT say your name. It SPEAKS a warning: *"Warning. A face was shown to
   the camera but it appears to be a photo."* Card shows a red **⚠ SPOOF** badge.

> "Without a liveness check, someone holding my photo makes the system announce
> *'<Name> is at the door'* — a security failure. AccessAI detects the flat photo
> and downgrades it to Unknown."

**Honesty line (say it — panels reward this):**
> "Liveness here is the production MiniFASNet ONNX pair — it started as a
> lightweight heuristic placeholder, and upgrading was literally dropping two
> model files into a folder, no code change. Our `/status` panel flags exactly
> which components are production-grade versus demo-stage; today only re-ID is
> still on its placeholder."

---

## 5. THE DEAF-MODE CONVERSATION — the standout feature (60 sec)

1. Switch **Mode → Deaf** (top selector).
2. Click **Ring** → the card flashes, big text appears, (on a phone) it vibrates.
   No sound needed.
3. Speak a phrase at the mic — or use the visitor-speech path: *"I have a package
   for you."* → the card shows the transcribed words as a caption.
4. Type a reply in the reply box: *"Please leave it at the gate."* → the LAPTOP
   SPEAKS it aloud.

> "A deaf user is alerted visually, *reads* what the visitor said as a caption,
> and *types* a reply that is spoken at the door. That two-way exchange — the thing
> an ordinary doorbell can never do for a deaf person — is closed here."

---

## 6. MEMORY — repeat visitor (30 sec)

1. Mode back to **Both**.
2. Show the **same** unknown person/photo a second time. Click **Ring**.
3. It SPEAKS: *"The same unknown visitor has come 2 times today."* Card shows a
   **🔁 Seen 2×** badge.

> "It remembers unknown visitors by appearance. *'A stranger has come to your door
> three times today'* is a genuine safety signal — and if someone recurs often, the
> system offers to save them as a known contact automatically."

---

## 7. HANDS-FREE VOICE — Blind Mode UX (30 sec)

1. Click the **🎙 Voice Command** button (push-to-talk).
2. Say: *"Who is at the door?"*
3. It transcribes, runs the pipeline, and SPEAKS the answer.

> "A blind user never touches the screen. *'Hey Access, who's at the door?'* — and
> it answers. Fully hands-free."

---

## 8. THE CLOSER — architecture + honesty (30 sec)

Show the **GET /status** health panel.

> "One data object — the *Visitor Event* — flows through every module: face,
> objects, liveness, speech, translation, memory. That's why we built this in ten
> clean phases without rewrites, and why each capability is independently testable
> — 64 automated tests pass. This panel shows every module's health honestly:
> anti-spoof and the wake word started as flagged placeholders and have been
> upgraded in place — real MiniFASNet liveness models, and a custom 'Hey Access'
> wake word trained offline from our own TTS voices. Only re-ID still shows its
> documented drop-in path. It's private by design, runs on a laptop today for
> under ₹5,000, and moves to embedded hardware by changing one line. Thank you."

---

## RECOVERY PLAN (if something misbehaves live)

| Problem | Do this |
|---|---|
| Face not recognised | Lighting — step into better light; lower `FACE_MATCH_THRESHOLD` was pre-set. Re-enroll if needed. |
| No sound | Check speakers; `POST /reply {"text":"test"}` to verify TTS; check `/status` TTS=ok. |
| Ring feels slow | Speech recording adds ~5s. Say "it's capturing audio now" — or set `SPEECH_SECONDS=3`. |
| VLM error / offline | Say "running fully offline" — YOLO-only description still works. Don't apologise. |
| Camera frozen | The camera thread auto-reconnects; wait 2s, or restart `run.py`. |
| Something crashes | Restart `python3 run.py` — it boots in seconds. Keep talking while it does. |

**Golden rule:** if a feature glitches, *name it as a known limitation and move on*.
The `/status` honesty framing means an imperfect moment reinforces your credibility
instead of undermining it.

---

## ONE-LINE FEATURE MAP (cheat sheet to keep beside you)

| # | Feature | Button/Action | It should SAY / SHOW |
|---|---|---|---|
| 2 | Known face | Ring (you) | "<Name> is at the door." (green) |
| 3 | Delivery | Ring (w/ parcel) | "...Carrying a package. Likely a delivery." (📦) |
| 4 | Spoof | Ring (photo) | "...appears to be a photo." (⚠ red) |
| 5 | Deaf 2-way | Mode=Deaf, Ring, type reply | flash + caption; reply spoken |
| 6 | Repeat visitor | Ring same stranger ×2 | "same unknown visitor...2 times today" (🔁) |
| 7 | Voice command | 🎙 button, "who's at the door" | spoken answer |
| 8 | Health/honesty | open /status | green/amber module panel |
