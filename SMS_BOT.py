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
  @app.route("/admin/dashboard")
def admin_dashboard():
    return "<h1>Admin Panel Active - System Connected</h1>"

@app.route("/")
def index():
    return "System is Online"

if __name__ == "__main__":
    app.run()
