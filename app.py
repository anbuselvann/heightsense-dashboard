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
    "sensor_height": 220,
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

# ---------- ROOT (IMPORTANT for Render) ----------
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
            if not (50 <= h <= 400):
                return jsonify({"error": "Invalid height"}), 400
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

    arr = np.array([r for r in readings if r is not None and r >= 0], dtype=float)

    avg = float(np.mean(arr))
    variance = float(np.var(arr))
    meas_min = float(np.min(arr))
    meas_max = float(np.max(arr))
    meas_median = float(np.median(arr))
    meas_std = float(np.std(arr))

    pct_high = float(np.sum(arr > 9000) / len(arr))
    pct_zero = float(np.sum(arr == 0) / len(arr))
    pct_low = float(np.sum((arr > 0) & (arr < 6000)) / len(arr))

    # ---------- Prediction ----------
    if model and scaler:
        try:
            features = np.array([[avg, variance, len(arr), meas_min, meas_max,
                                  avg, meas_std, meas_median,
                                  pct_high, pct_zero, pct_low,
                                  1 if is_head else 0]])

            features_scaled = scaler.transform(features)
            estimated_distance = float(model.predict(features_scaled)[0])
            method = "ml_model"
        except Exception as e:
            print("⚠️ Prediction error:", e)
            estimated_distance = avg / 58.0
            method = "fallback"
    else:
        estimated_distance = avg / 58.0
        method = "fallback"

    # ---------- Confidence ----------
    if variance < 100 and pct_zero < 0.05:
        confidence = "high"
    elif variance < 1000:
        confidence = "medium"
    else:
        confidence = "low"

    estimated_height = max(0, sensor_height - estimated_distance)

    result = {
        "estimated_distance": round(estimated_distance, 1),
        "estimated_height": round(estimated_height, 1),
        "confidence": confidence,
        "method": method,
        "timestamp": datetime.now().isoformat()
    }

    history.appendleft(result)
    latest_reading = result

    return jsonify(result)

# ---------- Latest ----------
@app.route('/latest')
def latest():
    if latest_reading is None:
        return jsonify({"status": "no_data"})
    return jsonify(latest_reading)

# ---------- History ----------
@app.route('/history')
def get_history():
    return jsonify(list(history))

@app.route('/')
def home():
    return jsonify({"message": "Height Estimation API is running 🚀"})

# ---------- Run ----------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)