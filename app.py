import os
import sys
import json
import sqlite3
import logging
import ctypes
import time
import requests
from datetime import datetime
from threading import Thread, Lock
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, flash

# =====================================================================
# SYSTEM LOGGING & SETUP
# =====================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dhaka-exclusive-pro-ultimate-2026")
application = app

# =====================================================================
# C++ & ASSEMBLY ENGINE LOADERS
# =====================================================================
lib = None
asm_lib = None

try:
    if os.path.exists("engine.so"):
        lib = ctypes.CDLL(os.path.abspath("engine.so"))
        lib.process_business_logic.restype = ctypes.c_char_p
        logger.info("C++ Engine Loaded Successfully.")
    
    if os.path.exists("asm_engine.so"):
        asm_lib = ctypes.CDLL(os.path.abspath("asm_engine.so"))
        asm_lib.asm_process_command.restype = ctypes.c_char_p
        logger.info("Assembly Engine Loaded Successfully.")
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
        c.execute("CREATE TABLE IF NOT EXISTS agent_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, action TEXT, details TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS agents (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)")
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
        finally: conn.close()

def get_all_settings():
    rows = db_query("SELECT key, value FROM settings", fetchall=True) or []
    return {r["key"]: r["value"] for r in rows}

# =====================================================================
# WEB ROUTES
# =====================================================================

@app.route("/")
def index():
    return redirect("/admin")

@app.route("/health")
def health():
    return jsonify({
        "status": "online",
        "cpp_engine": lib is not None,
        "asm_engine": asm_lib is not None,
        "version": "7.0.0-PRO",
        "time": datetime.now().isoformat()
    })

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        u, p = request.form.get("username"), request.form.get("password")
        agent = db_query("SELECT * FROM agents WHERE username=? AND password=?", (u, p), fetchone=True)
        if agent:
            session["logged_in"] = True
            session["username"] = agent["username"]
            return redirect("/admin")
        flash("ভুল ইউজারনেম বা পাসওয়ার্ড!", "error")
    return render_template_string("""
        <body style="background:#0f172a; color:white; font-family:sans-serif; display:flex; justify-content:center; align-items:center; height:100vh;">
            <form method="POST" style="background:#1e293b; padding:40px; border-radius:30px; width:300px; text-align:center; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);">
                <h2 style="color:#6366f1; margin-bottom:20px;">ADMIN ACCESS</h2>
                <input name="username" placeholder="User" style="width:100%; padding:15px; margin:10px 0; border-radius:12px; border:none; background:#0f172a; color:white;">
                <input name="password" type="password" placeholder="Pass" style="width:100%; padding:15px; margin:10px 0; border-radius:12px; border:none; background:#0f172a; color:white;">
                <button style="width:100%; padding:15px; background:#6366f1; color:white; border:none; border-radius:12px; font-weight:bold; cursor:pointer; margin-top:20px;">LOGIN</button>
            </form>
        </body>
    """)
@app.route("/admin")
def admin_portal():
    if not session.get("logged_in"): 
        return redirect("/admin/login")
    
    # ইউজার ইন্টারফেস ট্যাব সিলেক্ট করা (URL থেকে ট্যাব ভ্যালু নিবে)
    tab = request.args.get("tab", "dashboard")
    chat_with = request.args.get("chat_with", "")
    
    # ১. সেটিংস ডাটা
    s = get_all_settings()
    
    # ২. অ্যানালিটিক্স ডাটা (অর্ডারের সংখ্যা ও মোট টাকা)
    analytics = {
        "total_orders": db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)["c"] or 0,
        "total_revenue": db_query("SELECT SUM(total) as s FROM orders", fetchone=True)["s"] or 0
    }
    
    # ৩. নোটিফিকেশন কাউন্ট (লাইভ চ্যাট ও কমপ্লেইন)
    unread_chat_count = db_query("SELECT COUNT(*) as c FROM messages WHERE direction='inbound'", fetchone=True)["c"] or 0
    pending_complaints_count = db_query("SELECT COUNT(*) as c FROM complaints WHERE status='pending'", fetchone=True)["c"] or 0
    
    # ৪. ডাটাবেস থেকে সকল রেকর্ড সংগ্রহ
    orders = db_query("SELECT * FROM orders ORDER BY id DESC", fetchall=True) or []
    users = db_query("SELECT * FROM users ORDER BY last_active DESC", fetchall=True) or []
    products = db_query("SELECT * FROM products ORDER BY id DESC", fetchall=True) or []
    agent_logs = db_query("SELECT * FROM agent_logs ORDER BY id DESC LIMIT 50", fetchall=True) or []
    
    # ৫. চ্যাট হিস্ট্রি (যদি কোনো ইউজার সিলেক্ট করা থাকে)
    chat_history = db_query("SELECT * FROM messages WHERE from_number = ? ORDER BY id ASC", (chat_with,), fetchall=True) or [] if chat_with else []

    # ৬. ফলাফল রেন্ডার (এটাই মেইন পার্ট)
    try:
        return render_template(f"{tab}.html", 
                               settings=s, 
                               analytics=analytics, 
                               orders=orders, 
                               users=users, 
                               products=products, 
                               agent_logs=agent_logs, 
                               unread_chat_count=unread_chat_count,
                               pending_complaints_count=pending_complaints_count,
                               active_chat=chat_with,
                               chat_history=chat_history)
    except Exception as e:
        logger.error(f"Template rendering error: {e}")
        return f"<h1>Error: '{tab}.html' ফাইলটি 'templates' ফোল্ডারে পাওয়া যায়নি।</h1>"

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
