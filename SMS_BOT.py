from flask import Flask, render_template_string
import ctypes
import os

app = Flask(__name__)
application = app

# সি++ ইঞ্জিন লোড করা (নিরাপদ উপায়)
lib = None
if os.path.exists("engine.so"):
    try:
        lib = ctypes.CDLL(os.path.abspath("engine.so"))
    except Exception as e:
        print(f"Error loading engine.so: {e}")

@app.route("/")
def index():
    return "System is Online"

@app.route("/admin/dashboard")
def admin_dashboard():
    # যদি সি++ ইঞ্জিন কাজ করে, তবে ডাটা দেখাবে, না হলে সাধারণ মেসেজ দিবে
    result = "N/A (Engine not loaded)"
    if lib:
        try:
            result = lib.process_engine(10)
        except:
            result = "Error executing engine"
            
    return render_template_string("""
        <h1>Admin Panel Active - System Connected</h1>
        <p>C++ Engine Output: {{ result }}</p>
    """, result=result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
