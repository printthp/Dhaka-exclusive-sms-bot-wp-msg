import os
import sqlite3
import logging
import ctypes
import google.generativeai as genai
from flask import Flask, request, jsonify

# Flask App Initialize
app = Flask(__name__)
application = app

# হাইব্রিড ইঞ্জিন লোডার (নিরাপদ পদ্ধতি)
lib_path = os.path.join(os.getcwd(), "business_engine.so")
hybrid_engine = None

if os.path.exists(lib_path):
    try:
        hybrid_engine = ctypes.CDLL(lib_path)
        # C++ ফাংশন সিগনেচার ডিফাইন করা (অ্যারর এড়াতে)
        hybrid_engine.process_business_logic.argtypes = [ctypes.c_int]
        hybrid_engine.process_business_logic.restype = ctypes.c_int
        logging.info("Hybrid Engine loaded successfully.")
    except Exception as e:
        logging.error(f"Engine failed to load: {e}")
else:
    logging.warning(f"Engine file not found at {lib_path}. Falling back to Python mode.")

@app.route("/execute", methods=["POST"])
def execute():
    data = request.json.get("value", 0)
    
    # হাইব্রিড মোড (C++ ও অ্যাসেম্বলি)
    if hybrid_engine:
        try:
            result = hybrid_engine.process_business_logic(data)
            return jsonify({"result": int(result), "status": "hybrid_mode"})
        except Exception as e:
            return jsonify({"error": str(e), "status": "error"}), 500
    
    # পাইথন ফলব্যাক মোড
    return jsonify({"result": data * 2, "status": "python_fallback"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
