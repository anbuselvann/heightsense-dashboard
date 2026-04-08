# 🧠 HeightSense — ML-Assisted Human Height Estimation
### Under Dense Scalp Hair Conditions via Ultrasonic Sensor

---

## System Architecture

```
[HC-SR04 Ultrasonic Sensor]
         │
    [ESP8266 NodeMCU]
         │  WiFi HTTP POST (200 readings every 5s)
         ▼
[Python Flask Backend — app.py]
    • Loads / trains ML model (Random Forest)
    • Extracts features from raw readings
    • Predicts sensor-to-head distance
    • Returns height estimate + confidence
         │
         │  HTTP GET /latest  (polled by browser)
         ▼
[Web Dashboard — index.html]
    • Live height display with confidence
    • Signal quality visualization
    • Measurement history
```

---

## Quick Start

### 1. Backend (Run on your PC)

```bash
cd backend/
pip install -r requirements.txt

# Put "Data Set.xlsx" in the backend/ folder (to train the model)
cp "path/to/Data Set.xlsx" .

python app.py
# Server starts at http://0.0.0.0:5000
```

### 2. Dashboard (Open in browser)

```
Open dashboard/index.html in any browser.
Enter your PC's IP (e.g. http://192.168.1.100:5000) and click CONNECT.
```

### 3. ESP8266 Firmware

**Install Libraries (Arduino IDE):**
- `ArduinoJson` (v6)
- `ESP8266WiFi` (built in with ESP8266 board package)
- `ESP8266HTTPClient` (built in)

**Edit firmware/height_sensor_esp8266.ino:**
```cpp
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* SERVER_URL    = "http://YOUR_PC_IP:5000/predict";
const float SENSOR_HEIGHT_CM = 220.0;  // Measure this physically!
```

**Flash to ESP8266 and open Serial Monitor @ 115200 baud.**

---

## Hardware Wiring

```
HC-SR04          ESP8266 (NodeMCU)
────────         ─────────────────
VCC      →       3.3V (use voltage divider on ECHO!)
GND      →       GND
TRIG     →       D5 (GPIO14)
ECHO     →       D6 (GPIO12)  ← via 1kΩ + 2kΩ voltage divider

Voltage divider for ECHO pin (5V → 3.3V):
  ECHO ──[1kΩ]──┬── D6 (GPIO12)
                │
               [2kΩ]
                │
               GND
```

---

## API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Check server + model status |
| `/predict` | POST | Send raw readings, get height |
| `/latest` | GET | Get most recent measurement |
| `/history?limit=N` | GET | Get last N measurements |
| `/retrain` | POST | Retrain model from dataset |

### POST /predict  (ESP8266 sends this)
```json
{
  "readings": [1234, 1256, 0, 11500, 1240, ...],
  "sensor_height": 220,
  "is_head": true
}
```

### Response
```json
{
  "estimated_distance": 95.3,
  "estimated_height": 174.7,
  "confidence": "high",
  "method": "ml_model",
  "raw_stats": {
    "count": 200,
    "mean": 4523.1,
    "median": 4412.0,
    "std": 2103.4,
    "pct_valid": 68.5,
    "pct_high_noise": 24.0,
    "pct_zero": 7.5
  },
  "timestamp": "2026-03-04T12:00:00"
}
```

---

## How the ML Works

The sensor fires 200 ultrasonic pulses per session. Due to dense hair:
- ~60-70% of readings land in the **real head reflection zone** (low µs values)
- ~20-30% bounce off ceiling/shoulders (high µs, >9000)
- ~5-10% are **failed reads** (zero)

The model uses these features:
| Feature | Why it matters |
|---|---|
| `Pct_Low` | % of readings in valid range — strongest predictor |
| `Meas_Median` | Robust central tendency (ignores outliers) |
| `Pct_High` | High noise ratio = hair interference present |
| `Variance` | High variance = more hair scattering |

Model: **Random Forest (200 trees)** trained on your 52-experiment dataset.
Current MAE: ~19cm on cross-validation. More data = better accuracy.

---

## Improving Accuracy

1. **Collect more data** — Add more experiments to `Data Set.xlsx` and call `POST /retrain`
2. **Calibrate sensor height** — Measure `SENSOR_HEIGHT_CM` precisely with a tape measure
3. **Temperature compensation** — Speed of sound changes with temperature; add a DHT11 sensor
4. **Multiple sensors** — Average readings from 2-3 sensors for better reliability
