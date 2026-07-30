// esp32cam_doorbell.ino — AccessAI hardware doorbell (Phase 17)
//
// A flashable sketch for an AI-Thinker ESP32-CAM (or XIAO ESP32S3-Sense with
// the pin map swapped) implementing the docs/HARDWARE.md bring-up:
//
//   * connects to WiFi (reconnects forever with backoff)
//   * serves the MJPEG stream the backend can use as CAMERA_SOURCE
//     (http://<esp-ip>:81/stream — one client at a time, like the stock demo)
//   * a momentary button on BUTTON_PIN  -> capture a JPEG -> POST /ring
//   * an optional PIR sensor on PIR_PIN -> same, with its own long cooldown
//   * optionally HMAC-SHA256-signs the POST body with RING_SECRET so the
//     backend (ACCESSAI_RING_SECRET in its .env) can verify the press is
//     really from this device even on an open LAN (X-Ring-Signature header)
//
// Board setup (Arduino IDE):
//   1. Boards Manager -> install "esp32" by Espressif.
//   2. Board: "AI Thinker ESP32-CAM" (or "XIAO_ESP32S3" for the Sense).
//   3. Fill in the CONFIG block below, then flash (AI-Thinker: GPIO0 to GND
//      while flashing — see docs/HARDWARE.md).
//
// Everything is fail-soft: WiFi loss, server-down, and camera hiccups are
// retried and logged over Serial at 115200; the loop never wedges.

#include <WiFi.h>
#include <HTTPClient.h>
#include "esp_camera.h"
#include "mbedtls/md.h"

// ---------------- CONFIG — fill these in ------------------------------------
const char *WIFI_SSID   = "YOUR_WIFI";
const char *WIFI_PASS   = "YOUR_PASSWORD";
// The AccessAI backend (the laptop/Pi running run.py).
const char *SERVER_BASE = "http://192.168.1.78:8000";
// OPTIONAL shared secret. "" disables signing. When set, it must equal
// ACCESSAI_RING_SECRET in the backend's .env — the sketch then sends
// X-Ring-Signature: hex(HMAC_SHA256(secret, body)) and the backend refuses
// unsigned/forged rings.
const char *RING_SECRET = "";

const int BUTTON_PIN = 13;      // momentary button to GND (INPUT_PULLUP)
const int PIR_PIN    = -1;      // HC-SR501 OUT pin, or -1 when no PIR wired
const unsigned long BUTTON_COOLDOWN_MS = 5000;    // debounce between rings
const unsigned long PIR_COOLDOWN_MS    = 30000;   // motion is noisier - longer

// ---------------- AI-Thinker ESP32-CAM pin map ------------------------------
// (For XIAO ESP32S3-Sense replace with its map from the CameraWebServer demo.)
#define PWDN_GPIO_NUM 32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 0
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27
#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 21
#define Y4_GPIO_NUM 19
#define Y3_GPIO_NUM 18
#define Y2_GPIO_NUM 5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22

unsigned long lastButtonRing = 0;
unsigned long lastPirRing = 0;

// ---------------- Camera ----------------------------------------------------
bool cameraInit() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM;
  c.pin_d3 = Y5_GPIO_NUM; c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM;
  c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM; c.pin_pclk = PCLK_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM; c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  // SVGA keeps the POST small and the backend resizes anyway; PSRAM boards
  // could go higher but face recognition gains little beyond ~800px.
  c.frame_size = psramFound() ? FRAMESIZE_SVGA : FRAMESIZE_VGA;
  c.jpeg_quality = 12;
  c.fb_count = psramFound() ? 2 : 1;
  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) {
    Serial.printf("[cam] init failed: 0x%x\n", err);
    return false;
  }
  return true;
}

// ---------------- HMAC ------------------------------------------------------
// hex(HMAC_SHA256(secret, body)) — matches the backend's verification in
// accessai/server.py::ring (X-Ring-Signature).
String hmacHex(const uint8_t *body, size_t len) {
  uint8_t out[32];
  const mbedtls_md_info_t *md = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  mbedtls_md_hmac(md, (const uint8_t *)RING_SECRET, strlen(RING_SECRET),
                  body, len, out);
  static const char hexd[] = "0123456789abcdef";
  String s;
  s.reserve(64);
  for (int i = 0; i < 32; i++) {
    s += hexd[out[i] >> 4];
    s += hexd[out[i] & 0xF];
  }
  return s;
}

// ---------------- Ring ------------------------------------------------------
void ringDoorbell(const char *why) {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.printf("[ring] skipped (%s): WiFi down\n", why);
    return;
  }
  camera_fb_t *fb = esp_camera_fb_get();   // fresh frame; NULL is still OK
  HTTPClient http;
  http.begin(String(SERVER_BASE) + "/ring");
  http.setTimeout(15000);
  http.addHeader("Content-Type", "image/jpeg");
  const uint8_t *body = fb ? fb->buf : (const uint8_t *)"";
  size_t len = fb ? fb->len : 0;
  if (strlen(RING_SECRET) > 0) {
    http.addHeader("X-Ring-Signature", hmacHex(body, len));
  }
  int code = http.POST(const_cast<uint8_t *>(body), len);
  Serial.printf("[ring] %s -> HTTP %d (%u bytes)\n", why, code, (unsigned)len);
  http.end();
  if (fb) esp_camera_fb_return(fb);
}

// ---------------- WiFi ------------------------------------------------------
void wifiConnect() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("[wifi] connecting");
  for (int i = 0; i < 60 && WiFi.status() != WL_CONNECTED; i++) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("[wifi] connected: %s\n", WiFi.localIP().toString().c_str());
    Serial.printf("[wifi] set CAMERA_SOURCE = \"http://%s:81/stream\" in "
                  "config.py to use this camera\n",
                  WiFi.localIP().toString().c_str());
  } else {
    Serial.println("[wifi] FAILED - retrying in loop()");
  }
}

// ---------------- MJPEG stream server (port 81) ------------------------------
// Minimal single-client multipart stream, enough for OpenCV's VideoCapture on
// the backend. The stock CameraWebServer example can replace this for extras.
WiFiServer streamServer(81);

void handleStreamClient() {
  WiFiClient client = streamServer.accept();
  if (!client) return;
  Serial.println("[stream] client connected");
  client.print("HTTP/1.1 200 OK\r\n"
               "Content-Type: multipart/x-mixed-replace; boundary=frame\r\n\r\n");
  while (client.connected()) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) { delay(100); continue; }
    client.printf("--frame\r\nContent-Type: image/jpeg\r\n"
                  "Content-Length: %u\r\n\r\n", fb->len);
    client.write(fb->buf, fb->len);
    client.print("\r\n");
    esp_camera_fb_return(fb);
    // ~10 fps is plenty for the doorbell and keeps the ESP cool.
    delay(100);
    // Let a queued button press interrupt a long stream session.
    if (digitalRead(BUTTON_PIN) == LOW) break;
  }
  client.stop();
  Serial.println("[stream] client disconnected");
}

// ---------------- Arduino ---------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\n[AccessAI] ESP32-CAM doorbell booting");
  pinMode(BUTTON_PIN, INPUT_PULLUP);       // button wires BUTTON_PIN -> GND
  if (PIR_PIN >= 0) pinMode(PIR_PIN, INPUT);
  if (!cameraInit()) {
    Serial.println("[cam] running without camera - /ring will send no image");
  }
  wifiConnect();
  streamServer.begin();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    delay(2000);
    wifiConnect();
    return;
  }

  // Doorbell button (active LOW with INPUT_PULLUP) + cooldown debounce.
  if (digitalRead(BUTTON_PIN) == LOW &&
      millis() - lastButtonRing > BUTTON_COOLDOWN_MS) {
    lastButtonRing = millis();
    ringDoorbell("button");
  }

  // Optional PIR motion (active HIGH) with its own longer cooldown.
  if (PIR_PIN >= 0 && digitalRead(PIR_PIN) == HIGH &&
      millis() - lastPirRing > PIR_COOLDOWN_MS) {
    lastPirRing = millis();
    ringDoorbell("motion");
  }

  handleStreamClient();
  delay(20);
}
