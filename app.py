from flask import Flask, request, jsonify
from flask_cors import CORS
import numpy as np
import pickle
import os
from datetime import datetime
from collections import deque

app = Flask(__name__)
CORS(app)

# ---------- Memory ----------
history = deque(maxlen=100)
latest_reading = None

# ---------- Config ----------
server_config = {
    "sensor_height": 30,   # default 30 cm for small-object demo
    "is_head": True
}

# ---------- Load Model ----------
MODEL_PATH = "height_model.pkl"
model, scaler = None, None

if os.path.exists(MODEL_PATH):
    try:
        with open(MODEL_PATH, 'rb') as f:
            model, scaler = pickle.load(f)
        print("✅ Model loaded successfully")
    except Exception as e:
        print("❌ Model load failed:", e)
else:
    print("⚠️ Model file not found, using fallback mode")

# ---------- ROOT ----------
@app.route('/')
def home():
    return jsonify({"message": "Height Estimation API is running 🚀"})

# ---------- Health ----------
@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "ok",
        "model_loaded": model is not None,
        "timestamp": datetime.now().isoformat()
    })

# ---------- Config ----------
@app.route('/config', methods=['GET', 'POST'])
def config():
    global server_config
    if request.method == 'POST':
        data = request.get_json()
        if not data:
            return jsonify({"error": "Missing JSON"}), 400

        if 'sensor_height' in data:
            h = float(data['sensor_height'])
            # FIX: lowered minimum to 5 cm to support small object measurement
            if not (5 <= h <= 400):
                return jsonify({"error": "Invalid height (must be 5–400 cm)"}), 400
            server_config['sensor_height'] = h

        if 'is_head' in data:
            server_config['is_head'] = bool(data['is_head'])

        return jsonify({"status": "ok", "config": server_config})

    return jsonify(server_config)

# ---------- Predict ----------
@app.route('/predict', methods=['POST'])
def predict():
    global latest_reading

    data = request.get_json()
    if not data or 'readings' not in data:
        return jsonify({"error": "Missing readings"}), 400

    readings = data['readings']
    if len(readings) < 5:
        return jsonify({"error": "Need at least 5 readings"}), 400

    sensor_height = server_config['sensor_height']
    is_head = server_config['is_head']

    # Filter valid readings (non-zero, non-timeout)
    arr = np.array([r for r in readings if r is not None and r > 0 and r < 30000], dtype=float)

    if len(arr) < 3:
        return jsonify({"error": "Too many invalid readings"}), 400

    avg        = float(np.mean(arr))
    variance   = float(np.var(arr))
    meas_min   = float(np.min(arr))
    meas_max   = float(np.max(arr))
    meas_median= float(np.median(arr))
    meas_std   = float(np.std(arr))

    pct_high   = float(np.sum(arr > 9000)              / len(arr) * 100)
    pct_zero   = float(np.sum(arr == 0)                / len(arr) * 100)
    pct_valid  = float(np.sum((arr > 0) & (arr < 9000))/ len(arr) * 100)

    # ---------- Distance Calculation ----------
    # HC-SR04: duration (µs) → distance (cm) = duration / 58
    # This is accurate for both short and long distances.
    # For small objects close to sensor, readings will be small µs values
    # e.g. object 10 cm away → ~580 µs duration

    if model and scaler:
        try:
            pct_high_frac = pct_high / 100.0
            pct_zero_frac = pct_zero / 100.0
            pct_low_frac  = float(np.sum((arr > 0) & (arr < 6000)) / len(arr))

            features = np.array([[avg, variance, len(arr), meas_min, meas_max,
                                   avg, meas_std, meas_median,
                                   pct_high_frac, pct_zero_frac, pct_low_frac,
                                   1 if is_head else 0]])
            features_scaled    = scaler.transform(features)
            estimated_distance = float(model.predict(features_scaled)[0])
            method             = "ml_model"
        except Exception as e:
            print("⚠️ Prediction error:", e)
            # FIX: use median (more robust than mean) and correct HC-SR04 formula
            estimated_distance = meas_median / 58.0
            method             = "median_fallback"
    else:
        # FIX: use median for robustness against outliers
        estimated_distance = meas_median / 58.0
        method             = "median_fallback"

    # FIX: clamp distance to valid range (2 cm minimum — HC-SR04 blind zone)
    estimated_distance = max(2.0, min(estimated_distance, sensor_height - 0.5))

    # ---------- Confidence ----------
    if variance < 500 and pct_zero < 5:
        confidence = "high"
    elif variance < 5000 and pct_zero < 20:
        confidence = "medium"
    else:
        confidence = "low"

    # FIX: object height = sensor height - distance to object top
    estimated_height = max(0.0, round(sensor_height - estimated_distance, 1))

    # FIX: build raw_stats and include in result so /latest serves it to dashboard
    raw_stats = {
        "mean":           round(avg, 1),
        "median":         round(meas_median, 1),
        "variance":       round(variance, 1),
        "std":            round(meas_std, 1),
        "min":            round(meas_min, 1),
        "max":            round(meas_max, 1),
        "count":          len(arr),
        "pct_valid":      round(pct_valid, 1),
        "pct_high_noise": round(pct_high, 1),
        "pct_zero":       round(pct_zero, 1)
    }

    result = {
        "estimated_distance": round(estimated_distance, 1),
        "estimated_height":   estimated_height,
        "confidence":         confidence,
        "method":             method,
        "sensor_height":      sensor_height,
        # FIX: raw_stats now stored in result → available from /latest
        "raw_stats":          raw_stats,
        "timestamp":          datetime.now().isoformat()
    }

    history.appendleft(result)
    latest_reading = result

    return jsonify(result)

# ---------- Latest ----------
@app.route('/latest')
def latest():
    if latest_reading is None:
        return jsonify({"status": "no_data"})
    # Returns full result including raw_stats
    return jsonify(latest_reading)

# ---------- History ----------
@app.route('/history')
def get_history():
    return jsonify(list(history))

# ---------- Run ----------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)