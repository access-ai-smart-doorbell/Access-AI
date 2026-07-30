# firmware/ — ESP32-CAM doorbell sketch

`esp32cam_doorbell.ino` is the flashable counterpart to
[docs/HARDWARE.md](../docs/HARDWARE.md): WiFi + MJPEG stream on port 81, a
doorbell button that captures a JPEG and `POST`s it to the backend's `/ring`
webhook, and an optional PIR motion input.

## Flash it

1. Arduino IDE → Boards Manager → install **esp32** (Espressif).
2. Board: **AI Thinker ESP32-CAM** (S3-Sense works too — swap the pin map,
   see the comment at the top of the sketch).
3. Edit the CONFIG block: `WIFI_SSID`, `WIFI_PASS`, `SERVER_BASE`
   (your laptop's `http://<ip>:8000`), and optionally `RING_SECRET`.
4. AI-Thinker: ground **GPIO0** while flashing, then remove and reset
   (full jumper dance in docs/HARDWARE.md). S3-Sense flashes over USB-C.
5. Open Serial Monitor at 115200 — the sketch prints its IP and the exact
   `CAMERA_SOURCE` line to paste into `config.py`.

## Wiring

- **Button** — momentary switch between `BUTTON_PIN` (default GPIO13) and GND
  (the sketch uses `INPUT_PULLUP`, so no external resistor needed).
- **PIR (optional)** — HC-SR501 `OUT` → `PIR_PIN` (set it to a free GPIO;
  default `-1` = disabled), VCC→5V, GND→GND. Motion posts `/ring` with a
  30 s cooldown.

## Authenticated rings (recommended)

Set the same random string in both places:

- sketch: `RING_SECRET = "some-long-random-string"`
- backend `.env`: `ACCESSAI_RING_SECRET=some-long-random-string`

The sketch then signs every POST body with HMAC-SHA256
(`X-Ring-Signature` header) and the backend rejects unsigned or forged
rings — nobody on your WiFi can fake a doorbell press. This works alongside
`ACCESSAI_TOKEN` (the ESP32 never holds the user's bearer token).
