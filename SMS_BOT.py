from flask import Flask, request, jsonify
import os

app = Flask(__name__)
application = app

@app.route("/")
def index():
    return "Bot is Active"

@app.route("/admin/dashboard")
def admin_dashboard():
    return "Admin Panel is Active"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
