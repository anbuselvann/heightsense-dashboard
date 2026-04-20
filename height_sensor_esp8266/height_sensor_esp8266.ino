/*
 * Human / Object Height Estimation — ESP8266 Firmware + LCD Display
 * 
 * FIXED:
 * - Default sensor height set to 30 cm for small-object demo
 * - Reduced BATCH_INTERVAL to 3s for faster updates
 * - Added raw µs value and computed cm to serial for debugging
 * - Increased HTTP timeout for Render cold starts
 * 
 * NOTE: sensor_height and is_head are configured via the web dashboard only.
 * This firmware only sends raw sensor readings to the server.
 *
 * WIRING:
 *   Ultrasonic
 *     VCC  → Vin (5V)
 *     GND  → GND
 *     TRIG → D5 (GPIO14)
 *     ECHO → D6 (GPIO12) via voltage divider
 *   LCD (I2C)
 *     VCC  → Vin
 *     GND  → GND
 *     SDA  → D2 (GPIO4)
 *     SCL  → D1 (GPIO5)
 */

#include <ESP8266WiFi.h>
#include <WiFiClientSecure.h>
#include <ESP8266HTTPClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

// ============================================================
// LCD CONFIG
// ============================================================
LiquidCrystal_I2C lcd(0x27, 16, 2);  // Change to 0x3F if 0x27 doesn't work

// ============================================================
// WIFI & SERVER CONFIG — Edit before flashing
// ============================================================
const char* WIFI_SSID     = "ASN";
const char* WIFI_PASSWORD = "anbu1234";
const char* SERVER_URL    = "https://heightsense-dashboard.onrender.com/predict";

// ============================================================
// PINS
// ============================================================
const int TRIG_PIN = 14;  // D5
const int ECHO_PIN = 12;  // D6
const int LED_PIN  = 2;   // Built-in LED (active LOW)

// ============================================================
// MEASUREMENT CONFIG
// FIX: reduced batch size & interval for faster demo updates
// ============================================================
const int  NUM_READINGS      = 100;    // was 200 — faster batch fill
const int  READING_DELAY_MS  = 10;
const long BATCH_INTERVAL_MS = 3000;   // was 5000 — update every 3s

// HC-SR04 effective range: 2 cm – 400 cm
// At 30 cm sensor height, object at 0 cm → ~1740 µs
// Phone (~8 mm tall) → sensor reads ~(30 - 0.8) = 29.2 cm → ~1694 µs
const int MIN_VALID_US = 116;   // ~2 cm minimum (HC-SR04 blind zone)
const int MAX_VALID_US = 23200; // ~400 cm maximum

// ============================================================
// GLOBALS
// ============================================================
long readings[NUM_READINGS];
int  readingCount  = 0;
unsigned long lastBatchTime = 0;

WiFiClientSecure wifiClient;

// ============================================================
// SETUP
// ============================================================
void setup() {
  Serial.begin(115200);
  delay(200);

  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);  // HIGH = off for active-LOW LED

  // LCD init
  Wire.begin(4, 5);  // SDA=D2(4), SCL=D1(5)
  lcd.init();
  lcd.backlight();
  lcd.setCursor(0, 0); lcd.print("HeightSense v2");
  lcd.setCursor(0, 1); lcd.print("Initializing...");
  delay(1500);
  lcd.clear();

  Serial.println("\n==================================");
  Serial.println(" HeightSense v2 — Small Object Mode");
  Serial.println(" Sensor height: set via dashboard");
  Serial.println("==================================");

  connectWiFi();
  wifiClient.setInsecure();  // OK for IoT — skip SSL cert verification
}

// ============================================================
// MAIN LOOP
// ============================================================
void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
    wifiClient.setInsecure();
  }

  long dist = takeSingleReading();

  // Debug every reading to Serial
  float distCm = dist / 58.0;
  Serial.printf("RAW: %ld µs  →  %.1f cm\n", dist, distCm);

  readings[readingCount % NUM_READINGS] = dist;
  readingCount++;

  unsigned long now = millis();
  if (now - lastBatchTime >= BATCH_INTERVAL_MS) {
    int batchSize = min(readingCount, NUM_READINGS);

    if (batchSize >= 10) {  // FIX: lowered minimum from 20 to 10
      lastBatchTime = now;

      // Show live reading on LCD while waiting for server
      float liveCm = dist / 58.0;
      lcd.clear();
      lcd.setCursor(0, 0); lcd.print("Measuring...");
      lcd.setCursor(0, 1);
      lcd.print("D:");
      lcd.print(liveCm, 1);
      lcd.print("cm");

      sendBatchToServer(readings, batchSize);
      readingCount = 0;
    }
  }

  delay(READING_DELAY_MS);
}

// ============================================================
// ULTRASONIC READING
// Returns duration in microseconds. 0 = timeout/no echo.
// ============================================================
long takeSingleReading() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  // Timeout at 30000 µs = ~516 cm, safe for 30 cm setup
  long duration = pulseIn(ECHO_PIN, HIGH, 30000);
  return duration;
}

// ============================================================
// SEND BATCH TO SERVER
// Only sends raw readings — sensor_height managed by dashboard
// ============================================================
void sendBatchToServer(long* data, int count) {
  if (count == 0) return;

  Serial.printf("\n📡 Sending batch of %d readings to server...\n", count);
  digitalWrite(LED_PIN, LOW);  // LED on

  lcd.clear();
  lcd.setCursor(0, 0); lcd.print("Uploading...");
  lcd.setCursor(0, 1); lcd.print(String(count) + " readings");

  // Build JSON — only raw readings, no config
  DynamicJsonDocument doc(NUM_READINGS * 10 + 128);
  JsonArray arr = doc.createNestedArray("readings");
  for (int i = 0; i < count; i++) {
    arr.add(data[i]);
  }

  String payload;
  serializeJson(doc, payload);

  Serial.print("Payload preview: ");
  Serial.println(payload.substring(0, 80));

  HTTPClient http;
  http.begin(wifiClient, SERVER_URL);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(30000);  // 30s for Render cold starts

  int httpCode = http.POST(payload);
  Serial.printf("HTTP response code: %d\n", httpCode);

  if (httpCode == 200) {
    String response = http.getString();
    Serial.println("Server response: " + response);

    DynamicJsonDocument resp(512);
    DeserializationError err = deserializeJson(resp, response);

    if (!err) {
      float height   = resp["estimated_height"] | 0.0f;
      float distance = resp["estimated_distance"] | 0.0f;
      const char* conf   = resp["confidence"]  | "?";
      const char* method = resp["method"]      | "?";

      Serial.println("✅ Result:");
      Serial.printf("   Height:     %.1f cm\n", height);
      Serial.printf("   Distance:   %.1f cm\n", distance);
      Serial.printf("   Confidence: %s\n", conf);
      Serial.printf("   Method:     %s\n", method);

      // LCD: show height
      lcd.clear();
      lcd.setCursor(0, 0); lcd.print("Height:");
      lcd.setCursor(0, 1);
      lcd.print(height, 1);
      lcd.print(" cm");
      delay(2500);

      // LCD: show distance + confidence
      lcd.clear();
      lcd.setCursor(0, 0);
      lcd.print("D:");
      lcd.print(distance, 1);
      lcd.print("cm ");
      lcd.print(conf);
      lcd.setCursor(0, 1);
      lcd.print(method[0] == 'm' ? "ML Model" : "Median");
      delay(2500);

      lcd.clear();
      lcd.setCursor(0, 0); lcd.print("Ready...");
    } else {
      Serial.println("⚠️ JSON parse error: " + String(err.c_str()));
      lcd.clear();
      lcd.setCursor(0, 0); lcd.print("Parse Error");
      delay(1500);
      lcd.clear();
      lcd.setCursor(0, 0); lcd.print("Ready...");
    }

  } else {
    Serial.printf("❌ HTTP Error: %d — %s\n", httpCode, http.errorToString(httpCode).c_str());
    lcd.clear();
    lcd.setCursor(0, 0); lcd.print("HTTP Error:");
    lcd.setCursor(0, 1); lcd.print(httpCode);
    delay(2000);
    lcd.clear();
    lcd.setCursor(0, 0); lcd.print("Ready...");
  }

  http.end();
  digitalWrite(LED_PIN, HIGH);  // LED off
}

// ============================================================
// WIFI CONNECTION
// ============================================================
void connectWiFi() {
  Serial.printf("\n🔌 Connecting to: %s\n", WIFI_SSID);

  lcd.clear();
  lcd.setCursor(0, 0); lcd.print("WiFi...");
  lcd.setCursor(0, 1); lcd.print(WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 40) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n✅ WiFi connected! IP: " + WiFi.localIP().toString());
    lcd.clear();
    lcd.setCursor(0, 0); lcd.print("Connected!");
    lcd.setCursor(0, 1); lcd.print(WiFi.localIP());
    delay(2000);
    lcd.clear();
    lcd.setCursor(0, 0); lcd.print("Ready...");
  } else {
    Serial.println("\n⚠️ WiFi failed. Retrying next loop...");
    lcd.clear();
    lcd.setCursor(0, 0); lcd.print("WiFi Failed");
    lcd.setCursor(0, 1); lcd.print("Will retry...");
    delay(2000);
    lcd.clear();
  }
}
