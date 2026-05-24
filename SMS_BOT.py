from flask import Flask, render_template_string
import ctypes
import os

app = Flask(__name__)
application = app

# ১. সি++ ইঞ্জিন নিরাপদভাবে লোড করা
lib = None
try:
    if os.path.exists("engine.so"):
        lib = ctypes.CDLL(os.path.abspath("engine.so"))
except Exception as e:
    print(f"Engine Load Error: {e}")

# ২. হোম রাউট
@app.route("/")
def index():
    return "System is Online"

# ৩. ড্যাশবোর্ড রাউট (শুধু একটিই থাকবে)
DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<body style="background:#1a1a1a; color:#fff; font-family:sans-serif; padding:20px;">
    <h1>Master Control Dashboard</h1>
    <div style="background:#333; padding:20px; border-radius:10px;">
        <h3>System Status: ACTIVE</h3>
    </div>
</body>
</html>
"""

@app.route("/admin/dashboard")
def dashboard():
    return render_template_string(DASHBOARD_HTML)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
