import os
import sqlite3
import logging
import ctypes
from flask import Flask, request, jsonify, session, redirect, url_for

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)
app.secret_key = "dhaka_exclusive_secret_key_2026"
application = app
DB_FILE = "bot_v7_ultimate.db"

# Engine Loader
lib_path = os.path.join(os.getcwd(), "business_engine.so")
hybrid_engine = None

if os.path.exists(lib_path):
    try:
        hybrid_engine = ctypes.CDLL(lib_path)
        hybrid_engine.process_business_logic.argtypes = [ctypes.c_int]
        hybrid_engine.process_business_logic.restype = ctypes.c_int
        logging.info("Hybrid Engine loaded successfully.")
    except Exception as e:
        logging.error(f"Engine failed: {e}")

@app.route("/execute", methods=["POST"])
def execute():
    data = request.json.get("value", 0)
    if hybrid_engine:
        try:
            result = hybrid_engine.process_business_logic(data)
            return jsonify({"result": int(result), "status": "hybrid_mode"})
        except:
            return jsonify({"error": "Engine failure"}), 500
    return jsonify({"result": data * 2, "status": "python_fallback"})

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        # পাসওয়ার্ড চেক
        user = c.execute("SELECT * FROM agents WHERE username=? AND password=?", (username, password)).fetchone()
        conn.close()
        if user:
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        return "ভুল ইউজারনেম বা পাসওয়ার্ড!", 401
    return '''<form method="post">Username: <input type="text" name="username"><br>Password: <input type="password" name="password"><br><input type="submit" value="Login"></form>'''

@app.route("/dashboard")
def dashboard():
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    return "স্বাগতম! আপনি এখন আপনার প্রফেশনাল ড্যাশবোর্ডে আছেন।"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
