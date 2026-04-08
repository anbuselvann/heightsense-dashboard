/*
 * Human Height Estimation — ESP8266 Firmware + LCD Display
 * 
 * NOTE: sensor_height and is_head are configured via the web dashboard only.
 * This firmware only sends raw sensor readings to the server.
 */

 /*
 Ultrasonic
VCC → Vin (5V)
GND → GND
TRIG → D5
ECHO → D6 (via voltage divider)
LCD
VCC → Vin
GND → GND
SDA → D2
SCL → D1
 */

#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

// ============================================================
// LCD CONFIG
// ============================================================
LiquidCrystal_I2C lcd(0x27, 16, 2);  // Change to 0x3F if needed

// ============================================================
// CONFIG — Edit these before flashing
// ============================================================
const char* WIFI_SSID     = "ASN";
const char* WIFI_PASSWORD = "anbu1234";
const char* SERVER_URL    = "http://10.151.114.91:5000/predict";
\
// ⚠️  DO NOT set sensor_height or surface type here.
//     Use the web dashboard to configure these values.
//     The server will apply whatever the dashboard has set.

// ============================================================
// PINS
// ============================================================
const int TRIG_PIN = 14;  // D5
const int ECHO_PIN = 12;  // D6
const int LED_PIN  = 2;   // Built-in LED

// ============================================================
// MEASUREMENT CONFIG
// ============================================================
const int   NUM_READINGS       = 200;
const int   READING_DELAY_MS   = 10;
const int   BATCH_INTERVAL_MS  = 5000;

// ============================================================
// GLOBALS
// ============================================================
long readings[NUM_READINGS];
int  readingCount = 0;
unsigned long lastBatchTime = 0;

WiFiClient wifiClient;

// ============================================================
// SETUP
// ============================================================
void setup() {
  Serial.begin(115200);
  delay(100);

  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);

  // LCD INIT
  lcd.init();
  lcd.backlight();
  lcd.setCursor(0, 0);
  lcd.print("Height System");
  lcd.setCursor(0, 1);
  lcd.print("Initializing...");
  delay(2000);
  lcd.clear();

  Serial.println("\n==================================");
  Serial.println(" Height Estimation System v1.0");
  Serial.println("==================================");
  Serial.println(" Config via dashboard only.");

  connectWiFi();
}

// ============================================================
// MAIN LOOP
// ============================================================
void loop() {

  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }

  long dist = takeSingleReading();
  readings[readingCount % NUM_READINGS] = dist;
  readingCount++;

  unsigned long now = millis();
  if (now - lastBatchTime >= BATCH_INTERVAL_MS) {

    int batchSize = min(readingCount, NUM_READINGS);

    if (batchSize < 20) {   // 👈 minimum required
      return;               // skip sending
    }

    lastBatchTime = now;
    sendBatchToServer(readings, batchSize);
    readingCount = 0;
  }

  delay(READING_DELAY_MS);
}

// ============================================================
// ULTRASONIC READING
// ============================================================
long takeSingleReading() {

  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);

  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  long duration = pulseIn(ECHO_PIN, HIGH, 30000);

  return duration;
}

// ============================================================
// SEND DATA TO SERVER
// Only sends raw readings — no sensor_height, no is_head.
// Those are managed by the dashboard via /config endpoint.
// ============================================================
void sendBatchToServer(long* data, int count) {

  if (count == 0) return;

  Serial.printf("\n📡 Sending batch of %d readings...\n", count);
  digitalWrite(LED_PIN, LOW);

  // LCD status
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Sending Data...");

  DynamicJsonDocument doc(NUM_READINGS * 8 + 64);
  JsonArray arr = doc.createNestedArray("readings");

  for (int i = 0; i < count; i++) {
    arr.add(data[i]);
  }

  // ✅ sensor_height and is_head are intentionally omitted here.
  // The server uses the values last set by the dashboard (/config endpoint).

  String payload;
  serializeJson(doc, payload);

  HTTPClient http;
  http.begin(wifiClient, SERVER_URL);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(10000);

  int httpCode = http.POST(payload);

  if (httpCode == 200) {

    String response = http.getString();

    DynamicJsonDocument resp(512);
    deserializeJson(resp, response);

    float height     = resp["estimated_height"];
    float distance   = resp["estimated_distance"];
    const char* conf = resp["confidence"];

    Serial.println("✅ Prediction received:");
    Serial.printf("Height: %.1f cm\n", height);

    // ================= LCD DISPLAY =================
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("Height:");

    lcd.setCursor(0, 1);
    lcd.print(height);
    lcd.print(" cm");

    delay(3000);

    // Show confidence
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("Confidence:");
    lcd.setCursor(0, 1);
    lcd.print(conf);

    delay(3000);
  }
  else {
    Serial.printf("❌ HTTP Error: %d\n", httpCode);

    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("HTTP Error");
    lcd.setCursor(0, 1);
    lcd.print(httpCode);
    delay(2000);
  }

  http.end();
  digitalWrite(LED_PIN, HIGH);
}

// ============================================================
// WIFI CONNECTION
// ============================================================
void connectWiFi() {

  Serial.printf("\n🔌 Connecting to WiFi: %s", WIFI_SSID);

  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Connecting WiFi");

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  int attempts = 0;

  while (WiFi.status() != WL_CONNECTED && attempts < 30) {
    delay(500);
    Serial.print(".");
    lcd.setCursor(0, 1);
    lcd.print("Attempt: ");
    lcd.print(attempts);
    attempts++;
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n✅ WiFi connected!");

    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("WiFi Connected");
    lcd.setCursor(0, 1);
    lcd.print(WiFi.localIP());

    delay(2000);
    lcd.clear();
  }
  else {
    Serial.println("\n⚠️ WiFi failed");

    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print("WiFi Failed");
    delay(2000);
  }
}
