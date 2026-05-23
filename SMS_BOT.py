import os
import sys
import json
import sqlite3
import logging
import time
import requests
from threading import Thread, Lock
from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = "dhaka_exclusive_secret_key_2026"
DB_FILE = "bot_v7_ultimate.db"
db_lock = Lock()

# --- Database Utility ---
def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        try:
            c.execute(query, params)
            if commit: conn.commit(); return True
            if fetchone: row = c.fetchone(); return dict(row) if row else None
            if fetchall: rows = c.fetchall(); return [dict(r) for r in rows]
            return None
        except Exception as e:
            logger.error(f"DB Error: {e}")
            return None
        finally:
            conn.close()

def init_db():
    queries = [
        "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, msg_id TEXT UNIQUE, from_number TEXT, content TEXT, msg_type TEXT, direction TEXT, agent_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS sessions (phone TEXT PRIMARY KEY, state TEXT DEFAULT 'idle', context TEXT DEFAULT '{}', last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP, bot_paused INTEGER DEFAULT 0)",
        "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, name TEXT, address TEXT, product_id INTEGER, quantity INTEGER, total INTEGER, delivery_fee INTEGER, pathao_consignment_id TEXT, status TEXT DEFAULT 'pending', agent_name TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, fb_product_id TEXT UNIQUE, name TEXT, price INTEGER, description TEXT, stock INTEGER, active INTEGER, image_url TEXT)",
        "CREATE TABLE IF NOT EXISTS users (phone TEXT PRIMARY KEY, name TEXT, last_active TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)",
        "CREATE TABLE IF NOT EXISTS agents (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT, role TEXT)",
        "CREATE TABLE IF NOT EXISTS complaints (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, complaint_text TEXT, status TEXT, resolved_by TEXT, resolution_notes TEXT, created_at TIMESTAMP)"
    ]
    for q in queries: db_query(q, commit=True)
    db_query("INSERT OR IGNORE INTO agents (username, password, role) VALUES ('admin', 'admin123', 'admin')", commit=True)

init_db()

# --- Core Logic Functions ---
def get_all_settings():
    rows = db_query("SELECT key, value FROM settings", fetchall=True) or []
    return {r["key"]: r["value"] for r in rows}

def send_whatsapp(to, payload_type, content, extra=None, agent="system"):
    s = get_all_settings()
    token, phone_id = s.get("permanent_token"), s.get("phone_number_id")
    if not token: return False
    
    url = f"https://graph.facebook.com/v21.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"messaging_product": "whatsapp", "to": to, "type": payload_type}
    if payload_type == "text": body["text"] = {"body": content}
    elif payload_type == "interactive": body["interactive"] = content
    
    r = requests.post(url, json=body, headers=headers)
    if r.status_code in [200, 201]:
        db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', ?)", (to, str(content), agent), commit=True)
        return True
    return False

# --- Webhook & State Machine ---
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}
    try:
        msg = data["entry"][0]["changes"][0]["value"]["messages"][0]
        Thread(target=process_webhook_async, args=(msg, msg.get("from"))).start()
    except: pass
    return "EVENT_RECEIVED", 200

def process_webhook_async(msg, from_number):
    body_text = msg.get("text", {}).get("body", "").strip()
    # এখানে আপনার আগের স্টেট মেশিন লজিক (selecting_product, awaiting_name, ইত্যাদি) বসিয়ে নিন।
    # প্রতিটি ডাটাবেজ লাইনের বদলে শুধু db_query ব্যবহার করবেন।
    # উদাহরণ: db_query("UPDATE sessions SET state='...' WHERE phone=?", (new_state, from_number), commit=True)
    pass

# --- Admin Routes ---
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        u, p = request.form.get("username"), request.form.get("password")
        account = db_query("SELECT * FROM agents WHERE username=? AND password=?", (u, p), fetchone=True)
        if account:
            session["logged_in"] = True
            session["username"] = account["username"]
            return redirect("/admin")
    return render_template_string(LOGIN_HTML)

# --- Server Start ---
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
