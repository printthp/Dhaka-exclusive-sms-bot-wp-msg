import os
import sys
import json
import sqlite3
import logging
import ctypes
import time
from datetime import datetime
from threading import Lock

# ফ্লাস্ক এর প্রয়োজনীয় সব টুলস এখানে দেওয়া হলো
from flask import Flask, request, jsonify, render_template, render_template_string, redirect, url_for, session, flash

# ... বাকী সব কোড নিচের মতো থাকবে
# =====================================================================
# SYSTEM LOGGING & SETUP
# =====================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dhaka-exclusive-pro-ultimate-2026")
application = app

# =====================================================================
# ENGINE LOADER (C++ & ASM)
# =====================================================================
lib = None
asm_lib = None
try:
    if os.path.exists("engine.so"):
        lib = ctypes.CDLL(os.path.abspath("engine.so"))
        lib.process_business_logic.restype = ctypes.c_char_p
    if os.path.exists("asm_engine.so"):
        asm_lib = ctypes.CDLL(os.path.abspath("asm_engine.so"))
        asm_lib.asm_process_command.restype = ctypes.c_char_p
except Exception as e:
    logger.error(f"Engine Loading Error: {e}")

# =====================================================================
# DATABASE UTILITIES
# =====================================================================
DB_FILE = "bot_v7_ultimate.db"
db_lock = Lock()

def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, from_number TEXT, content TEXT, direction TEXT, agent_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS users (phone TEXT PRIMARY KEY, last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, name TEXT, address TEXT, total INTEGER, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, price INTEGER, stock INTEGER, image_url TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS agent_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, action TEXT, details TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS agents (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)")
        # Default Admin Account
        c.execute("INSERT OR IGNORE INTO agents (username, password) VALUES ('admin', 'admin123')")
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('business_name', 'Dhaka Exclusive')")
        conn.commit()
        conn.close()

init_db()

def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
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
        finally: conn.close()

def get_all_settings():
    rows = db_query("SELECT key, value FROM settings", fetchall=True) or []
    return {r["key"]: r["value"] for r in rows}

# =====================================================================
# ADMIN PANEL ROUTES
# =====================================================================

@app.route("/")
def index():
    return redirect("/admin")

@app.route("/health")
def health():
    return jsonify({"status": "online", "engine": lib is not None, "time": datetime.now().isoformat()})

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "").strip()
        agent = db_query("SELECT * FROM agents WHERE username=? AND password=?", (u, p), fetchone=True)
        if agent:
            session["logged_in"] = True
            session["username"] = agent["username"]
            return redirect("/admin")
        flash("ভুল ইউজারনেম বা পাসওয়ার্ড!", "error")
    return render_template_string("""
        <body style="background:#0f172a; color:white; font-family:sans-serif; display:flex; justify-content:center; align-items:center; height:100vh; margin:0;">
            <form method="POST" style="background:#1e293b; padding:40px; border-radius:30px; width:300px; text-align:center; box-shadow:0 10px 30px rgba(0,0,0,0.5);">
                <h2 style="color:#6366f1; margin-bottom:25px; font-weight:900;">ADMIN LOGIN</h2>
                <input name="username" placeholder="Username" required style="width:100%; padding:14px; margin:10px 0; border-radius:12px; border:none; background:#0f172a; color:white; outline:none;">
                <input name="password" type="password" placeholder="Password" required style="width:100%; padding:14px; margin:10px 0; border-radius:12px; border:none; background:#0f172a; color:white; outline:none;">
                <button style="width:100%; padding:14px; background:#6366f1; color:white; border:none; border-radius:12px; font-weight:bold; cursor:pointer; margin-top:20px;">LOGIN</button>
            </form>
        </body>
    """)

@app.route("/admin")
def admin_portal():
    if not session.get("logged_in"): 
        return redirect("/admin/login")
    
    tab = request.args.get("tab", "dashboard")
    chat_with = request.args.get("chat_with", "")
    msg = request.args.get("msg", "")

    s = get_all_settings()
    
    # Analytics
    analytics = {
        "total_orders": db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)["c"] or 0,
        "total_revenue": db_query("SELECT SUM(total) as s FROM orders", fetchone=True)["s"] or 0,
        "total_users": db_query("SELECT COUNT(*) as c FROM users", fetchone=True)["c"] or 0
    }
    
    unread_chat_count = db_query("SELECT COUNT(*) as c FROM messages WHERE direction='inbound'", fetchone=True)["c"] or 0
    
    # Data list with proper limits for performance
    orders = db_query("SELECT * FROM orders ORDER BY id DESC LIMIT 50", fetchall=True) or []
    users = db_query("SELECT * FROM users ORDER BY last_active DESC LIMIT 30", fetchall=True) or []
    products = db_query("SELECT * FROM products ORDER BY id DESC LIMIT 30", fetchall=True) or []
    agent_logs = db_query("SELECT * FROM agent_logs ORDER BY id DESC LIMIT 50", fetchall=True) or []

    # Chat History
    chat_history = []
    if chat_with:
        chat_history = db_query("SELECT * FROM messages WHERE from_number = ? ORDER BY id DESC LIMIT 50", (chat_with,), fetchall=True) or []
        chat_history.reverse()

    try:
        return render_template(f"{tab}.html", 
                               settings=s, analytics=analytics, orders=orders, 
                               users=users, products=products, 
                               agent_logs=agent_logs, 
                               unread_chat_count=unread_chat_count,
                               active_chat=chat_with, chat_history=chat_history,
                               msg=msg)
    except:
        return f"<h1>Error: Template '{tab}.html' not found in templates folder.</h1>"

@app.route("/admin/agents/add", methods=["POST"])
def add_agent():
    if not session.get("logged_in") or session.get("username") != 'admin':
        return redirect("/admin?tab=agents&msg=Access Denied")
    
    u = request.form.get("username", "").strip()
    p = request.form.get("password", "").strip()
    
    if u and p:
        db_query("INSERT OR IGNORE INTO agents (username, password) VALUES (?, ?)", (u, p), commit=True)
        db_query("INSERT INTO agent_logs (username, action, details) VALUES (?, 'AGENT_CREATED', ?)", 
                 (session.get("username"), f"New agent: {u}"), commit=True)
        return redirect("/admin?tab=agents&msg=Agent Added Successfully")
    return redirect("/admin?tab=agents&msg=Invalid Data")

@app.route("/admin/chat/send", methods=["POST"])
def admin_send_message():
    if not session.get("logged_in"): return redirect("/admin/login")
    phone = request.form.get("phone")
    msg = request.form.get("message")
    if phone and msg:
        db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', ?)",
                 (phone, msg, session.get("username")), commit=True)
        db_query("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE phone = ?", (phone,), commit=True)
    return redirect(f"/admin?tab=chat&chat_with={phone}")

@app.route("/admin/settings/save", methods=["POST"])
def save_settings():
    if not session.get("logged_in"): return redirect("/admin/login")
    for k, v in request.form.items():
        db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?", (k, v, v), commit=True)
    return redirect("/admin?tab=settings&msg=Settings Updated")

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")

# =====================================================================
# START SERVER
# =====================================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
