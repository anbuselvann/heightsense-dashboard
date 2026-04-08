"""
Height Estimation ML Backend
ESP8266 sends raw sensor readings → this API predicts height using trained ML model
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import numpy as np
import pickle
import os
import time
import json
from datetime import datetime
from collections import deque

app = Flask(__name__)
CORS(app)  # Allow ESP8266 and dashboard to call this API

# ---------- In-memory measurement history (last 100 readings) ----------
history = deque(maxlen=100)
latest_reading = None

# ---------- Server-side config (dashboard sets these, Arduino does NOT) ----------
server_config = {
    "sensor_height": 220,   # cm — only the dashboard can change this
    "is_head": True
}

# ---------- Load or train model ----------
MODEL_PATH = "height_model.pkl"

def extract_features(readings: list, is_head: bool = True) -> np.ndarray:
    """
    Extract ML features from raw sensor readings.
    readings: list of distance values (in sensor units, e.g. microseconds or mm)
    is_head: True if measuring over scalp hair, False if flat surface
    """
    arr = np.array([r for r in readings if r is not None], dtype=float)
    arr = arr[arr >= 0]  # Remove negatives

    if len(arr) == 0:
        return None

    n = len(arr)
    avg = np.mean(arr)
    variance = np.var(arr)
    meas_min = np.min(arr)
    meas_max = np.max(arr)
    meas_mean = np.mean(arr)
    meas_std = np.std(arr)
    meas_median = np.median(arr)

    # Key clustering features: what % of readings fall in each zone?
    pct_high = np.sum(arr > 9000) / n   # Noise / ceiling reflections
    pct_zero = np.sum(arr == 0) / n     # Failed readings
    pct_low  = np.sum((arr > 0) & (arr < 6000)) / n  # Likely real head reflections

    surface_type_num = 1 if is_head else 0

    return np.array([[avg, variance, n, meas_min, meas_max,
                      meas_mean, meas_std, meas_median,
                      pct_high, pct_zero, pct_low, surface_type_num]])


def train_model():
    """Train model from Data Set.xlsx if present, otherwise use fallback."""
    try:
        import pandas as pd
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.preprocessing import StandardScaler
        import warnings
        warnings.filterwarnings('ignore')

        xlsx_path = os.path.join(os.path.dirname(__file__), "Data Set.xlsx")
        if not os.path.exists(xlsx_path):
            print("⚠️  Data Set.xlsx not found — using fallback distance map.")
            return None, None

        df = pd.read_excel(xlsx_path, header=None)
        meta = df.iloc[3:, :4].copy()
        meta.columns = ['Expected_Distance', 'Average', 'Variance', 'Surface_Type']
        meta = meta.reset_index(drop=True)
        meta['Expected_Distance'] = pd.to_numeric(meta['Expected_Distance'], errors='coerce')
        meta['Average'] = pd.to_numeric(meta['Average'], errors='coerce')
        meta['Variance'] = pd.to_numeric(meta['Variance'], errors='coerce')

        real_data = meta[meta['Surface_Type'].isin(['h', 'f', 'F'])].copy()
        real_data = real_data.dropna(subset=['Expected_Distance'])
        original_idx = list(real_data.index)
        real_data = real_data.reset_index(drop=True)
        real_data['Surface_Type_num'] = real_data['Surface_Type'].map({'F': 0, 'f': 0, 'h': 1})

        measurements_df = df.iloc[3:, 4:].reset_index(drop=True)
        measurements_df = measurements_df.apply(pd.to_numeric, errors='coerce')
        meas_subset = measurements_df.iloc[original_idx].reset_index(drop=True)

        real_data['Meas_Count'] = meas_subset.apply(lambda row: row.dropna().count(), axis=1)
        real_data['Meas_Min'] = meas_subset.apply(lambda row: row.dropna().min() if row.dropna().count() > 0 else np.nan, axis=1)
        real_data['Meas_Max'] = meas_subset.apply(lambda row: row.dropna().max() if row.dropna().count() > 0 else np.nan, axis=1)
        real_data['Meas_Mean'] = meas_subset.apply(lambda row: row.dropna().mean() if row.dropna().count() > 0 else np.nan, axis=1)
        real_data['Meas_Std'] = meas_subset.apply(lambda row: row.dropna().std() if row.dropna().count() > 0 else np.nan, axis=1)
        real_data['Meas_Median'] = meas_subset.apply(lambda row: row.dropna().median() if row.dropna().count() > 0 else np.nan, axis=1)
        real_data['Pct_High'] = meas_subset.apply(lambda row: (row.dropna() > 9000).sum() / max(row.dropna().count(), 1), axis=1)
        real_data['Pct_Zero'] = meas_subset.apply(lambda row: (row.dropna() == 0).sum() / max(row.dropna().count(), 1), axis=1)
        real_data['Pct_Low'] = meas_subset.apply(lambda row: ((row.dropna() > 0) & (row.dropna() < 6000)).sum() / max(row.dropna().count(), 1), axis=1)
        real_data = real_data.dropna()

        features = ['Average', 'Variance', 'Meas_Count', 'Meas_Min', 'Meas_Max',
                    'Meas_Mean', 'Meas_Std', 'Meas_Median', 'Pct_High', 'Pct_Zero', 'Pct_Low', 'Surface_Type_num']
        X = real_data[features].values
        y = real_data['Expected_Distance'].values

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        model = RandomForestRegressor(n_estimators=200, max_depth=8, random_state=42)
        model.fit(X_scaled, y)

        print(f"✅ Model trained on {len(y)} samples.")
        return model, scaler

    except Exception as e:
        print(f"⚠️  Model training failed: {e}")
        return None, None


# Load or train model at startup
if os.path.exists(MODEL_PATH):
    with open(MODEL_PATH, 'rb') as f:
        model, scaler = pickle.load(f)
    print("✅ Loaded saved model.")
else:
    model, scaler = train_model()
    if model:
        with open(MODEL_PATH, 'wb') as f:
            pickle.dump((model, scaler), f)
        print("✅ Model saved to disk.")


# ===================== API ROUTES =====================

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "ok",
        "model_loaded": model is not None,
        "timestamp": datetime.now().isoformat()
    })


@app.route('/config', methods=['GET', 'POST'])
def config():
    """
    Dashboard uses this to GET or SET sensor configuration.
    Arduino does NOT call this — it only POSTs raw readings.

    POST body: { "sensor_height": 220, "is_head": true }
    GET  returns current config.
    """
    global server_config
    if request.method == 'POST':
        data = request.get_json()
        if not data:
            return jsonify({"error": "Missing JSON body"}), 400
        if 'sensor_height' in data:
            h = float(data['sensor_height'])
            if not (50 <= h <= 400):
                return jsonify({"error": "sensor_height must be between 50 and 400 cm"}), 400
            server_config['sensor_height'] = h
        if 'is_head' in data:
            server_config['is_head'] = bool(data['is_head'])
        return jsonify({"status": "ok", "config": server_config})
    else:
        return jsonify(server_config)


@app.route('/predict', methods=['POST'])
def predict():
    """
    ESP8266 POSTs raw sensor readings here.
    Sensor height and surface type come from server_config (set by dashboard).
    Arduino does NOT send sensor_height or is_head — those are dashboard-only settings.

    Expected JSON body:
    {
        "readings": [1234, 1256, 0, 11500, 1240, ...]  // raw sensor values only
    }

    Returns:
    {
        "estimated_distance": 95.3,   // sensor-to-head distance (cm)
        "estimated_height": 174.7,    // person height = sensor_height - distance (cm)
        "confidence": "high",
        "method": "ml_model",
        "raw_stats": { ... }
    }
    """
    global latest_reading

    data = request.get_json()
    if not data or 'readings' not in data:
        return jsonify({"error": "Missing 'readings' in request body"}), 400

    readings = data.get('readings', [])

    # Always use server-side config — dashboard is the only source of truth
    sensor_height = server_config['sensor_height']
    is_head       = server_config['is_head']

    if len(readings) < 5:
        return jsonify({"error": "Need at least 5 readings for prediction"}), 400

    arr = np.array([r for r in readings if r is not None and r >= 0], dtype=float)

    # --- Feature extraction ---
    avg = float(np.mean(arr))
    variance = float(np.var(arr))
    meas_min = float(np.min(arr))
    meas_max = float(np.max(arr))
    meas_median = float(np.median(arr))
    meas_std = float(np.std(arr))
    pct_high = float(np.sum(arr > 9000) / len(arr))
    pct_zero = float(np.sum(arr == 0) / len(arr))
    pct_low = float(np.sum((arr > 0) & (arr < 6000)) / len(arr))

    raw_stats = {
        "count": len(arr),
        "mean": round(avg, 2),
        "median": round(meas_median, 2),
        "std": round(meas_std, 2),
        "min": round(meas_min, 2),
        "max": round(meas_max, 2),
        "variance": round(variance, 2),
        "pct_high_noise": round(pct_high * 100, 1),
        "pct_zero": round(pct_zero * 100, 1),
        "pct_valid": round(pct_low * 100, 1)
    }

    # --- Predict ---
    if model and scaler:
        features = np.array([[avg, variance, len(arr), meas_min, meas_max,
                               avg, meas_std, meas_median,
                               pct_high, pct_zero, pct_low, 1 if is_head else 0]])
        features_scaled = scaler.transform(features)
        estimated_distance = float(model.predict(features_scaled)[0])
        method = "ml_model"
    else:
        # Fallback: use median of low-range readings (K-means style)
        low_readings = arr[(arr > 0) & (arr < 6000)]
        if len(low_readings) > 0:
            estimated_distance = float(np.median(low_readings)) / 58.0  # us to cm
        else:
            estimated_distance = avg / 58.0
        method = "fallback_median"

    # Confidence scoring
    if variance < 100 and pct_zero < 0.05:
        confidence = "high"
    elif variance < 1000 and pct_zero < 0.2:
        confidence = "medium"
    else:
        confidence = "low"

    estimated_height = max(0, sensor_height - estimated_distance)

    result = {
        "estimated_distance": round(estimated_distance, 1),
        "estimated_height": round(estimated_height, 1),
        "confidence": confidence,
        "method": method,
        "sensor_height": sensor_height,
        "is_head": is_head,
        "raw_stats": raw_stats,
        "timestamp": datetime.now().isoformat()
    }

    # Store in history
    history.appendleft(result)
    latest_reading = result

    return jsonify(result)


@app.route('/latest', methods=['GET'])
def latest():
    """Dashboard polls this to get the most recent measurement."""
    if latest_reading is None:
        return jsonify({"status": "no_data", "message": "No readings yet"})
    return jsonify(latest_reading)


@app.route('/history', methods=['GET'])
def get_history():
    """Returns the last N measurements."""
    limit = int(request.args.get('limit', 50))
    return jsonify(list(history)[:limit])


@app.route('/retrain', methods=['POST'])
def retrain():
    """Retrain the model (call this when you add new data)."""
    global model, scaler
    model, scaler = train_model()
    if model:
        with open(MODEL_PATH, 'wb') as f:
            pickle.dump((model, scaler), f)
        return jsonify({"status": "retrained"})
    return jsonify({"status": "failed", "error": "Training failed"}), 500


if __name__ == '__main__':
    print("🚀 Height Estimation API running on http://0.0.0.0:5000")
    print("   POST /predict  — Send sensor readings, get height estimate")
    print("   POST /config   — Set sensor_height and is_head (dashboard only)")
    print("   GET  /config   — Get current config")
    print("   GET  /latest   — Get latest measurement (for dashboard)")
    print("   GET  /history  — Get measurement history")
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)