# AccessAI — Mobile App (Flutter)

**Status: built and complete (Phases 16–17). Not yet run on a physical Android
device** — see the handoff checklist at the end. The app lives in `mobile/` (34
Dart files, Riverpod) and is a pure *client*: it added no server-side work,
because the backend was already a clean HTTP + WebSocket API.

The mobile app is where the accessibility payoff lands: a blind user's phone
speaks announcements and takes voice commands anywhere in the house; a deaf
user's phone **vibrates + flashes + shows big text** the instant someone rings.

This document is both the spec the app was built against and the guide to
finishing it on real hardware.

---

## Why the API is already mobile-ready

- **Everything is HTTP/JSON** over one FastAPI server (default `:8000`).
- **Live push** is a single WebSocket (`/events`) that broadcasts both visitor
  events and voice-command results — no polling needed.
- **The live camera** is plain **MJPEG** (`/video`), which Flutter renders with
  an `Image.network` / `Mjpeg` widget.
- **Voice commands** already accept an **uploaded WAV** (`POST /listen`), so the
  phone records audio and the server does the Whisper transcription — no ML on
  the phone.
- The server already emits a **central health** snapshot (`GET /status`) the app
  can show as a status screen.

No new endpoints are needed for a first-class app.

---

## Endpoint → screen map

| Flutter screen / action | Endpoint | Notes |
|---|---|---|
| **Live view** | `GET /video` | MJPEG stream widget |
| **Ring / trigger analysis** | `POST /trigger` (or `POST /ring`) | returns the event JSON |
| **Live event push** | `WS /events` | `{type:"event", event:{…}}` and `{type:"voice", …}` |
| **History list** | `GET /history` | array of event dicts |
| **Event detail** | `GET /event/{id}` | one event |
| **Snapshot image** | `GET /snapshot/{id}` | JPEG |
| **Speak a command (push-to-talk)** | `POST /listen` (multipart WAV) | server transcribes + acts + speaks; returns `{command, intent, answer, spoke}` |
| **Toggle always-listening** | `POST /wakeword/on` \| `/wakeword/off` | opt-in |
| **Wake status pill** | `GET /wakeword_status` | |
| **Mode switch (Blind/Deaf/Both)** | `POST /mode` | |
| **Language switch** | `POST /user_language` | 11 languages |
| **Reply to visitor (Deaf two-way)** | `POST /reply` | text → spoken at the door |
| **Enroll a face** | `POST /enroll` | |
| **Frequent-visitor suggestions** | `GET /suggestions`, `POST /suggestions/{confirm\|dismiss}` | auto-enroll |
| **System health screen** | `GET /status` | per-module state + flags + torch version |

---

## The event payload (what the app renders)

Events arrive over `/events` and from `/history`. The app should read these
fields (all already on the `VisitorEvent`):

```jsonc
{
  "type": "event",
  "event": {
    "event_id": "…",
    "timestamp": "2026-07-10T12:00:00",
    "identity": { "known": true, "name": "Rahul", "confidence": 0.72 },
    "is_spoof": false,
    "visitor_count": 1,
    "carried_objects": ["a package"],
    "scene_summary": "A person in a blue uniform holding a box.",
    "ocr_text": "FEDEX 4821",
    "speech_transcript": "package for you",
    "translated_transcript": "package for you",
    "language_detected": "en",
    "reid_id": "v_1a2b3c4d",
    "reid_seen_count": 3,
    "intent": "likely delivery",
    "announcement_text": "Rahul is at the door. Carrying a package. Likely a delivery. They said: \"package for you\".",
    "snapshot_path": "…"
  }
}
```

**`announcement_text` is the one field the UI must always surface** — it is the
final, mode-appropriate sentence the system composed. Everything else is for
richer display (badges, chips, the snapshot).

Voice-command results arrive as:
```jsonc
{ "type": "voice", "command": "who is at the door",
  "intent": "who_is_there", "answer": "Rahul is at the door…", "spoke": true }
```

---

## Accessibility behaviour the app must implement

This is the point of the app — mirror what the web dashboard does, using native
phone capabilities:

- **Blind Mode**
  - Speak `announcement_text` with the phone's TTS (`flutter_tts`) on every
    incoming event. (The server also speaks on the host; the app gives the user
    audio *on their person*.)
  - Offer push-to-talk: record a few seconds of audio, `POST /listen` as a WAV,
    speak the returned `answer`. Optionally wire the phone's own wake word or a
    persistent notification action to start recording.
- **Deaf Mode**
  - On each event: **vibrate** (`HapticFeedback` / `vibration` package), **flash
    the screen / torch**, and show `announcement_text` in **large, high-contrast
    text**. Never rely on sound.
  - Provide the reply box → `POST /reply` (text is spoken at the door).
- **Both**: do both.

The app should honour the server's current mode from `GET /mode` and let the
user change it via `POST /mode`.

---

## Connectivity & config

- The app needs the **host base URL** (e.g. `http://192.168.1.10:8000`) — a
  settings field. On the same LAN this is direct; for remote access the user puts
  the host behind a reverse proxy / VPN (out of scope here).
- **Auth (Phase 17):** if `ACCESSAI_TOKEN` is set on the server, paste it into
  Settings → Access token. The app sends it as `Authorization: Bearer …` on HTTP
  calls, and as `?token=…` for the MJPEG stream and the WebSocket, neither of
  which can set a header.
- Use a WebSocket auto-reconnect (exponential backoff) for `/events`, mirroring
  how the web dashboard reconnects.
- All endpoints already return **clean JSON on error** (never a stack trace), so
  the app can surface a friendly message on any non-200.

---

## Background alerts (Phase 17) — how it works

A doorbell that only works while you are staring at the app is not a doorbell.
This is the part of the app with real architectural weight, so the reasoning is
recorded here rather than left in the code.

**LAN-only, no Firebase.** The phone holds the `/events` WebSocket open itself.
Nothing is relayed through a push service, so nothing about who visits your home
leaves your network.

**Why a foreground service.** Android freezes a backgrounded process within a
minute or two, which kills the socket. A foreground service
(`flutter_foreground_task`) is the sanctioned way to keep the process alive.
Android's price for that is a **mandatory persistent notification** — the
"AccessAI is listening" notice. That cost is unavoidable, so it is named in the
Settings toggle's subtitle instead of being quietly imposed.

**Why the socket stays in the main isolate.** The service *could* run its own
isolate, but that isolate would need a duplicate API config, auth token, TTS
engine, and Riverpod graph — and it would race the UI isolate into two
notifications for one visitor. The service therefore does nothing but keep the
main isolate alive. This is deliberate; see the class doc in
`mobile/lib/services/background_alert_service.dart`.

**Routing.** `nav_shell.dart` observes the app lifecycle. Foregrounded, an event
takes over the screen as before. Backgrounded, it goes to the notification shade
on a max-importance doorbell channel, which is what actually rings and vibrates
with the app closed. A later VLM enrichment replaces the same notification id
with `onlyAlertOnce`, so a fuller description does not buzz twice for one visitor.

**Default:** ON. Being told someone is at the door is the product.

---

## On-device handoff checklist

Everything below is verified by construction only. No Android device was attached
during development, so these are the steps that still need a real phone.

**Build**
1. `cd mobile && flutter pub get`
2. `flutter build apk --release`
3. Confirm the **merged** manifest kept the typed service — Android 14+ rejects an
   untyped one at runtime:
   ```
   grep foregroundServiceType \
     build/app/intermediates/merged_manifests/release/AndroidManifest.xml
   ```
   Expect `android:foregroundServiceType="dataSync"`. If it is missing, add it
   with a `tools:node="merge"` override rather than hand-declaring a fresh
   `<service>` — `flutter_foreground_task` supplies its own, and a mismatched
   class name fails the merge.

**First run**
4. Enter the host URL in Settings; the health screen should go green.
5. Grant the notification permission when prompted. If it is denied, the
   background-alerts toggle refuses to switch on and says why — that is intended,
   not a bug: a foreground service with no postable notification would show the
   mandatory notice and then alert nothing.
6. Accept the battery-optimisation exemption prompt. On aggressive OEM skins
   (MIUI in particular) an unexempted service is killed within minutes, and the
   symptom is the worst possible one — alerts that work in testing and stop
   silently later.

**The tests that matter**
7. App **fully closed**, ring the doorbell → the phone rings and vibrates, and
   the notification carries `announcement_text`.
8. Wait for the VLM enrichment → the *same* notification updates with the fuller
   description, without a second buzz.
9. Reboot the phone, do not open the app, ring → still alerts (the boot receiver
   restarted the service).
10. Leave it overnight, ring in the morning → still alerts. This is the one that
    catches OEM battery killers, and it cannot be shortened.
11. **Deaf Mode:** confirm vibration and flash fire with the screen off, and that
    nothing depends on hearing the notification sound.
12. **Blind Mode:** confirm the phone speaks the announcement and that
    push-to-talk round-trips through `POST /listen`.

**With auth enabled**
13. Paste the `ACCESSAI_TOKEN` into Settings → Access token. Confirm the MJPEG
    live view still renders and the WebSocket still connects: both authenticate
    via `?token=` rather than a header, so they are the two paths most likely to
    break when auth goes on.
