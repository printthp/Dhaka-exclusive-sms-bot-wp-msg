
from flask import Flask, render_template_string, request, jsonify
import ctypes
import os
app = Flask(__name__)
application = app



# গ্লোবাল ভেরিয়েবল
core = None
# ফাইলটি আছে কি না চেক করুন
if os.path.exists("core_engine.so"):
    try:
        core = ctypes.CDLL(os.path.abspath("core_engine.so"))
        core.process_business_logic.restype = ctypes.c_char_p
    except Exception as e:
        print(f"Engine Load Error: {e}")



# ২. হোম রাউট
@app.route("/")
def index():
    return "System is Online"





#=======================================
# ৩. ড্যাশবোর্ড রাউট (শুধু একটিই থাকবে)
#=======================================
app.route("/admin/dashboard")
def dashboard():
    return """
    <html>
    <body style="background:#111; color:#0f0; font-family:monospace; padding:30px;">
        <h1>BIZ-CORE MASTER SYSTEM</h1>
        <input id="cmd" placeholder="Enter Business Command..." style="width:300px;">
        <button onclick="run()">EXECUTE</button>
        <div id="res" style="margin-top:20px;"></div>
        <script>
            async function run() {
                const cmd = document.getElementById('cmd').value;
                const r = await fetch('/api/execute', {method:'POST', body:JSON.stringify({cmd}), headers:{'Content-Type':'application/json'}});
                document.getElementById('res').innerText = (await r.json()).status;
            }
        </script>
    </body>
    </html>
    """

@app.route("/api/execute", methods=["POST"])
def execute():
    cmd = request.json.get("cmd")
    result = core.process_business_logic(cmd.encode())
    return jsonify({"status": result.decode()})
#=======================================
# ৩. ড্যাশবোর্ড রাউট (শুধু একটিই থাকবে)
#=======================================





if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
