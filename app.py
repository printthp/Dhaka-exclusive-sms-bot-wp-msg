import os
import sys
import json
import sqlite3
import logging
import ctypes
import time
import requests
import random
from io import BytesIO
from datetime import datetime, timedelta
from threading import Lock
from flask import Flask, request, jsonify, render_template, render_template_string, redirect, url_for, session, flash, send_file
from xhtml2pdf import pisa 

# =====================================================================
# SYSTEM & LOGGING SETUP
# =====================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dhaka-exclusive-master-2026")
application = app

DB_FILE = "bot_v7_ultimate.db"
db_lock = Lock()

# =====================================================================
# ENGINE LOADERS
# =====================================================================
lib = None
asm_lib = None
try:
    if os.path.exists("engine.so"):
        lib = ctypes.CDLL(os.path.abspath("engine.so"))
        lib.process_business_logic.restype = ctypes.c_char_p
except: pass

# =====================================================================
# DATABASE UTILITIES
# =====================================================================
def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, from_number TEXT, content TEXT, direction TEXT, agent_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS users (phone TEXT PRIMARY KEY, last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, pathao_order_id TEXT UNIQUE, phone TEXT, name TEXT, address TEXT, total INTEGER, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS agent_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, action TEXT, details TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS agents (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)")
        c.execute("INSERT OR IGNORE INTO agents (username, password) VALUES ('admin', 'admin123')")
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
# FEATURE: FIXED PATHAO SYNC (With Better Debugging)
# =====================================================================
def get_pathao_token():
    s = get_all_settings()
    url = "https://api-hermes.pathao.com/aladdin/api/v1/issue-token"
    
    # ইনপুট ডাটা ক্লিন করা (অতিরিক্ত স্পেস থাকলে রিমুভ করবে)
    payload = {
        "client_id": s.get('pathao_client_id', '').strip(),
        "client_secret": s.get('pathao_client_secret', '').strip(),
        "username": s.get('pathao_merchant_email', '').strip(),
        "password": s.get('pathao_merchant_password', '').strip(),
        "grant_type": "password"
    }
    
    try:
        r = requests.post(url, json=payload, headers={"Accept": "application/json"}, timeout=15)
        res = r.json()
        if 'access_token' in res:
            return res['access_token']
        else:
            # যদি ভুল হয়, তবে ফ্লাস্ক এর সাহায্যে আমরা আপনাকে মেসেজ দিব
            logger.error(f"AUTH FAILED: {res.get('message')}")
            return f"Error: {res.get('message')}"
    except Exception as e:
        return f"Error: {str(e)}"

def pull_orders_from_pathao():
    token_status = get_pathao_token()
    if isinstance(token_status, str) and token_status.startswith("Error"):
        return token_status # এরর মেসেজ রিটার্ন করবে

    token = token_status
    s = get_all_settings()
    store_id = s.get('pathao_store_id', '').strip()
    
    if not store_id: return "Error: Store ID missing"

    url = f"https://api-hermes.pathao.com/aladdin/api/v1/stores/{store_id}/orders"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    
    try:
        r = requests.get(url, headers=headers, timeout=20)
        orders_list = r.json().get('data', {}).get('data', [])
        pulled = 0
        for o in orders_list:
            p_id = str(o.get('consignment_id') or o.get('order_id'))
            success = db_query("INSERT OR IGNORE INTO orders (pathao_order_id, phone, name, address, total, status) VALUES (?,?,?,?,?,?)", 
                               (p_id, o.get('recipient_phone'), o.get('recipient_name'), o.get('recipient_address'), o.get('amount'), o.get('status')), commit=True)
            if success: pulled += 1
        return pulled
    except Exception as e:
        return f"Error: {str(e)}"

# =====================================================================
# ROUTES
# =====================================================================
@app.route("/")
def index(): return redirect("/admin")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        u, p = request.form.get("username"), request.form.get("password")
        auth = db_query("SELECT * FROM agents WHERE username=? AND password=?", (u, p), fetchone=True)
        if auth:
            session["logged_in"], session["username"] = True, auth["username"]
            return redirect("/admin")
    return render_template_string('<body style="background:#0f172a;color:white;display:flex;justify-content:center;align-items:center;height:100vh;font-family:sans-serif;"><form method="POST" style="background:#1e293b;padding:40px;border-radius:20px;text-align:center;"><h2>DHAKA PRO ACCESS</h2><input name="username" placeholder="User" required style="width:100%;padding:10px;margin:10px 0;"><br><input name="password" type="password" placeholder="Pass" required style="width:100%;padding:10px;margin:10px 0;"><br><button style="width:100%;padding:10px;background:#6366f1;color:white;border:none;margin-top:20px;">LOGIN</button></form></body>')

@app.route("/admin")
def admin_portal():
    if not session.get("logged_in"): return redirect("/admin/login")
    tab, msg = request.args.get("tab", "dashboard"), request.args.get("msg", "")
    s = get_all_settings()
    
    analytics = {"total_orders": db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)["c"] or 0,
                 "total_revenue": db_query("SELECT SUM(total) as s FROM orders", fetchone=True)["s"] or 0,
                 "chart_data": {"labels": ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"], "data": [0,0,0,0,0,0,0]}}
    
    orders = db_query("SELECT * FROM orders ORDER BY id DESC LIMIT 50", fetchall=True) or []
    users = db_query("SELECT * FROM users ORDER BY last_active DESC LIMIT 30", fetchall=True) or []
    
    return render_template(f"{tab}.html", settings=s, analytics=analytics, orders=orders, users=users, msg=msg)

@app.route("/admin/sync-pathao-status")
def sync_pathao_status():
    result = pull_orders_from_pathao()
    if isinstance(result, str) and result.startswith("Error"):
        return redirect(url_for('admin_portal', tab='dashboard', msg=f"ব্যর্থ: {result}"))
    return redirect(url_for('admin_portal', tab='dashboard', msg=f"সফল: {result}টি নতুন অর্ডার সিঙ্ক হয়েছে।"))

@app.route("/admin/sync-facebook-trigger")
def sync_fb():
    db_query("INSERT INTO agent_logs (username, action, details) VALUES (?, 'FB_SYNC', 'Manual Trigger')", (session.get("username"),), commit=True)
    return redirect(url_for('admin_portal', tab='inventory', msg="Facebook Sync Started!"))

@app.route("/admin/settings/save", methods=["POST"])
def save_settings():
    for k, v in request.form.items():
        db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?", (k, v, v), commit=True)
    return redirect("/admin?tab=settings&msg=Updated Successfully")

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
