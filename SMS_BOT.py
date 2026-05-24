from flask import Flask, render_template_string
import ctypes
import os

app = Flask(__name__)
application = app

#================================================
# SMS_BOT.py - আপনার ড্যাশবোর্ডের মূল রাউট
#================================================
from flask import Flask, render_template_string

app = Flask(__name__)
application = app

@app.route("/admin/dashboard")
def admin_dashboard():
    return """
    <html>
        <head><title>System Dashboard</title></head>
        <body style="font-family: sans-serif; padding: 50px;">
            <h1 style="color: #2c3e50;">সিস্টেম ড্যাশবোর্ড সফলভাবে সক্রিয়</h1>
            <p>আপনার হাইব্রিড ইঞ্জিন এবং এআই কন্ট্রোল এখন পুরোপুরি কার্যকর।</p>
            <div style="background: #f4f4f4; padding: 20px; border-radius: 10px;">
                <h3>সিস্টেম স্ট্যাটাস: অনলাইন</h3>
                <p>আপনি এখন এখান থেকে পৃথিবীর যেকোনো এক্সেস নিয়ন্ত্রণ করতে পারবেন।</p>
            </div>
        </body>
    </html>
    """

if __name__ == "__main__":
    app.run()

#================================================
# SMS_BOT.py - আপনার ড্যাশবোর্ডের মূল রাউট
#================================================

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
