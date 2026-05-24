from flask import Flask, render_template_string
import os

app = Flask(__name__)
application = app

@app.route("/")
def index():
    return "System is Online"

@app.route("/admin/dashboard")
def admin_dashboard():
    return "<h1>Admin Control Center Active</h1>"

if __name__ == "__main__":
    app.run()
