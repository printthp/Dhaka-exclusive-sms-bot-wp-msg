from flask import Flask, render_template_string, request, jsonify
import ctypes
import os

app = Flask(__name__)

# সি++ ইঞ্জিন নিরাপদভাবে লোড করা
lib = None
try:
    if os.path.exists("core_engine.so"):
        lib = ctypes.CDLL(os.path.abspath("core_engine.so"))
        lib.process_business_logic.restype = ctypes.c_char_p
except Exception as e:
    print(f"Engine Load Error: {e}")

@app.route("/admin/dashboard")
def dashboard():
    return render_template_string("""
    <body style="background:#111; color:#0f0; font-family:monospace;">
        <h1>BIZ-CORE MASTER SYSTEM</h1>
        <input id="cmd" placeholder="Command...">
        <button onclick="run()">EXECUTE</button>
        <div id="res"></div>
        <script>
            async function run() {
                const cmd = document.getElementById('cmd').value;
                const r = await fetch('/api/execute', {method:'POST', body:JSON.stringify({cmd}), headers:{'Content-Type':'application/json'}});
                const data = await r.json();
                document.getElementById('res').innerText = data.status;
            }
        </script>
    </body>
    """)

@app.route("/api/execute", methods=["POST"])
def execute():
    cmd = request.json.get("cmd", "")
    if lib:
        res = lib.process_business_logic(cmd.encode())
        return jsonify({"status": res.decode()})
    return jsonify({"status": "Engine Not Found"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
