from flask import Flask, render_template_string, request, jsonify
import ctypes
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# Required for Gunicorn on Render
application = app

# Load C++ engine with fallback paths
lib = None
try:
    so_candidates = ["engine.so", "core_engine.so"]
    for candidate in so_candidates:
        if os.path.exists(candidate):
            lib = ctypes.CDLL(os.path.abspath(candidate))
            lib.process_business_logic.restype = ctypes.c_char_p
            print(f"Engine loaded: {candidate}")
            break
    if not lib:
        print("Engine Load Error: No .so file found (tried engine.so, core_engine.so)")
except Exception as e:
    print(f"Engine Load Error: {e}")

# Simple token auth for admin endpoints
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "change-me-in-production")

def require_token():
    token = request.headers.get("X-Admin-Token", "")
    if token != ADMIN_TOKEN:
        return jsonify({"error": "Unauthorized"}), 401
    return None

@app.route("/")
def index():
    return "<h2>Service Running</h2><a href='/admin/dashboard'>Admin Panel</a>"

@app.route("/admin/dashboard")
def dashboard():
    return render_template_string("""
    <body style="background:#111; color:#0f0; font-family:monospace;">
        <h1>BIZ-CORE MASTER SYSTEM</h1>
        <p>Send X-Admin-Token header via API client. Token is set in env var ADMIN_TOKEN.</p>
        <input id="cmd" placeholder="Command...">
        <button onclick="run()">EXECUTE</button>
        <div id="res"></div>
        <script>
            async function run() {
                const cmd = document.getElementById('cmd').value;
                const token = prompt('Enter Admin Token:');
                const r = await fetch('/api/execute', {
                    method: 'POST',
                    body: JSON.stringify({cmd}),
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Admin-Token': token
                    }
                });
                const data = await r.json();
                document.getElementById('res').innerText = JSON.stringify(data, null, 2);
            }
        </script>
    </body>
    """)

@app.route("/api/execute", methods=["POST"])
def execute():
    auth = require_token()
    if auth:
        return auth

    data = request.get_json(silent=True) or {}
    cmd = data.get("cmd", "").strip()

    if not cmd:
        return jsonify({"error": "No command provided"}), 400

    # Whitelist or sanitize if the C engine exposes dangerous functions
    # For now we pass through but log it
    if not lib:
        return jsonify({"status": "Engine Not Found"}), 503

    try:
        res = lib.process_business_logic(cmd.encode("utf-8"))
        if res:
            return jsonify({"status": res.decode("utf-8", errors="replace")})
        return jsonify({"status": "No response from engine"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/health")
def health():
    return jsonify({"status": "ok", "engine_loaded": lib is not None})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
