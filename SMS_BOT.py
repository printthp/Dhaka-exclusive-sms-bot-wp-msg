from flask import Flask, render_template_string
import ctypes
import os

app = Flask(__name__)
application = app

# সি++ ইঞ্জিন লোড করা
lib = ctypes.CDLL(os.path.abspath("engine.so"))

@app.route("/admin/dashboard")
def dashboard():
    # ড্যাশবোর্ড থেকে সি++ ইঞ্জিন কল করা হচ্ছে
    calc_result = lib.process_engine(10) 
    return render_template_string("""
        <h1>Admin Control Center</h1>
        <p>C++ Engine Output: {{ result }}</p>
    """, result=calc_result)

@app.route("/")
def index():
    return "System is Online"

@app.route("/admin/dashboard")
def admin_dashboard():
    return "<h1>Admin Control Center Active</h1>"

if __name__ == "__main__":
    app.run()
