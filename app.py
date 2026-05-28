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
# SYSTEM & STORAGE SETUP (Render Persistent Disk)
# =====================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)

# Render-এ ডাটাবেস ফাইলটি সুরক্ষিত রাখার জন্য পাথ সেট করা
# যদি Render Disk ব্যবহার করেন তবে পাথ হবে: /opt/render/project/src/data/bot.db
DB_DIR = os.path.join(os.getcwd(), "data")
if not os.path.exists(DB_DIR): os.makedirs(DB_DIR)
DB_PATH = os.path.join(DB_DIR, "bot_v7_ultimate.db")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dhaka-exclusive-master-2026")
application = app
db_lock = Lock()

# =====================================================================
# DATABASE UTILITIES
# =====================================================================
def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
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
        conn = sqlite3.connect(DB_PATH)
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
# CONTEXT PROCESSOR (FIXES UndefinedError IN ALL TABS)
# =====================================================================
@app.context_processor
def inject_global_vars():
    # এটি নিশ্চিত করবে যে unread_chat_count সব ট্যাবে অটোমেটিক পৌঁছে যাবে
    unread = db_query("SELECT COUNT(DISTINCT from_number) as c FROM messages WHERE direction='inbound'", fetchone=True)
    return dict(
        unread_chat_count=unread['c'] if unread else 0,
        current_time=datetime.now()
    )

# =====================================================================
# PATHAO SYNC LOGIC (With Strict Credential Cleaning)
# =====================================================================
def pull_orders_from_pathao():
    s = get_all_settings()
    url_auth = "https://api-hermes.pathao.com/aladdin/api/v1/issue-token"
    
    payload = {
        "client_id": str(s.get('pathao_client_id', '')).strip(),
        "client_secret": str(s.get('pathao_client_secret', '')).strip(),
        "username": str(s.get('pathao_merchant_email', '')).strip(),
        "password": str(s.get('pathao_merchant_password', '')).strip(),
        "grant_type": "password"
    }
    
    try:
        r_auth = requests.post(url_auth, json=payload, headers={"Accept": "application/json"}, timeout=10)
        res_auth = r_auth.json()
        token = res_auth.get('access_token')
        store_id = str(s.get('pathao_store_id', '')).strip()

        if token and store_id:
            url_orders = f"https://api-hermes.pathao.com/aladdin/api/v1/stores/{store_id}/orders"
            r_orders = requests.get(url_orders, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=15)
            orders_list = r_orders.json().get('data', {}).get('data', [])
            
            pulled = 0
            for o in orders_list:
                p_id = str(o.get('consignment_id'))
                success = db_query("INSERT OR IGNORE INTO orders (pathao_order_id, phone, name, address, total, status) VALUES (?,?,?,?,?,?)", 
                                   (p_id, o.get('recipient_phone'), o.get('recipient_name'), o.get('recipient_address'), o.get('amount'), o.get('status')), commit=True)
                if success: pulled += 1
            return pulled
        return f"Error: {res_auth.get('message', 'Auth Failed')}"
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
            return redirect("/admin?tab=dashboard")
    return render_template_string('<body style="background:#0f172a;color:white;display:flex;justify-content:center;align-items:center;height:100vh;font-family:sans-serif;"><form method="POST" style="background:#1e293b;padding:40px;border-radius:20px;text-align:center;"><h2>ADMIN PANEL</h2><input name="username" placeholder="User" required style="width:100%;padding:10px;margin:10px 0;"><br><input name="password" type="password" placeholder="Pass" required style="width:100%;padding:10px;margin:10px 0;"><br><button style="width:100%;padding:10px;background:#6366f1;color:white;border:none;margin-top:20px;cursor:pointer;">LOGIN</button></form></body>')

@app.route("/admin")
def admin_portal():
    if not session.get("logged_in"): return redirect("/admin/login")
    tab, msg = request.args.get("tab", "dashboard"), request.args.get("msg", "")
    s = get_all_settings()
    
    # Analytics Data
    analytics = {
        "total_orders": db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)["c"] or 0,
        "total_revenue": db_query("SELECT SUM(total) as s FROM orders", fetchone=True)["s"] or 0,
        "chart_data": {"labels": ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"], "data": [0,0,0,0,0,0,0]}
    }
    
    orders = db_query("SELECT * FROM orders ORDER BY id DESC LIMIT 50", fetchall=True) or []
    users = db_query("SELECT * FROM users ORDER BY last_active DESC LIMIT 30", fetchall=True) or []
    
    return render_template(f"{tab}.html", settings=s, analytics=analytics, orders=orders, users=users, msg=msg)

@app.route("/admin/sync-pathao-status")
def sync_pathao_status():
    res = pull_orders_from_pathao()
    return redirect(url_for('admin_portal', tab='dashboard', msg=f"Process Result: {res}"))

@app.route("/admin/db-backup")
def download_db_backup():
    if not session.get("logged_in"): return "Denied"
    return send_file(DB_PATH, as_attachment=True, download_name=f"Backup_{datetime.now().strftime('%Y-%m-%d')}.db")

@app.route("/admin/settings/save", methods=["POST"])
def save_settings():
    for k, v in request.form.items():
        db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?", (k, v, v), commit=True)
    return redirect("/admin?tab=settings&msg=Settings Updated")

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
