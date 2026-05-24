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
@app.route("/admin/dashboard")
def admin_dashboard():
    # ইঞ্জিনের রেজাল্ট বের করা
    result = "N/A (Engine not loaded)"
    if lib:
        try:
            # ধরুন সি++ এর ফাংশনটির নাম process_engine
            result = lib.process_engine(10)
        except Exception as e:
            result = f"Error: {e}"
            
    return render_template_string("""
        <html>
            <body style="font-family: sans-serif; padding: 50px;">
                <h1>Admin Panel Active - System Connected</h1>
                <div style="background: #e0e0e0; padding: 20px;">
                    <h3>C++ Engine Output: {{ result }}</h3>
                </div>
            </body>
        </html>
    """, result=result)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
