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
# REAL PATHAO API LOGIC (RE-ENGINEERED)
# =====================================================================
def get_pathao_token():
    s = get_all_settings()
    url = "https://api-hermes.pathao.com/aladdin/api/v1/issue-token"
    
    # সেটিংস থেকে কি-গুলো সংগ্রহ
    payload = {
        "client_id": s.get('pathao_client_id', '').strip(),
        "client_secret": s.get('pathao_client_secret', '').strip(),
        "username": s.get('pathao_merchant_email', '').strip(),
        "password": s.get('pathao_merchant_password', '').strip(),
        "grant_type": "password"
    }
    
    try:
        r = requests.post(url, json=payload, headers={"Accept": "application/json"}, timeout=15)
        res_data = r.json()
        if 'access_token' in res_data:
            return res_data['access_token']
        else:
            logger.error(f"Token Error: {res_data}")
            return None
    except Exception as e:
        logger.error(f"Pathao Auth Connection Error: {e}")
        return None

def pull_orders_from_pathao():
    token = get_pathao_token()
    s = get_all_settings()
    store_id = s.get('pathao_store_id', '').strip()
    
    if not token or not store_id:
        return 0

    # পাঠাও অর্ডার লিস্ট এপিআই (Hermes API V1)
    url = f"https://api-hermes.pathao.com/aladdin/api/v1/stores/{store_id}/orders"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    try:
        r = requests.get(url, headers=headers, timeout=20)
        res = r.json()
        
        # পাঠাও রেসপন্স স্ট্রাকচার অনেক সময় আলাদা হয়, তাই সাবধানে হ্যান্ডেল করা
        orders_list = res.get('data', {}).get('data', [])
        
        pulled_count = 0
        for o in orders_list:
            # ইউনিক পাঠাও আইডি
            p_id = str(o.get('consignment_id') or o.get('order_id'))
            
            # ডাটাবেসে সেভ
            success = db_query("""
                INSERT OR IGNORE INTO orders (pathao_order_id, phone, name, address, total, status) 
                VALUES (?, ?, ?, ?, ?, ?)
            """, (p_id, o.get('recipient_phone'), o.get('recipient_name'), o.get('recipient_address'), o.get('amount'), o.get('status')), commit=True)
            
            if success: pulled_count += 1
            
        return pulled_count
    except Exception as e:
        logger.error(f"Pathao Data Sync Error: {e}")
        return 0

# =====================================================================
# DASHBOARD LOGIC
# =====================================================================
def get_chart_data():
    labels, data = [], []
    for i in range(6, -1, -1):
        target = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        res = db_query("SELECT COUNT(*) as c FROM orders WHERE created_at LIKE ?", (f"{target}%",), fetchone=True)
        labels.append((datetime.now() - timedelta(days=i)).strftime('%a'))
        data.append(res['c'] if res else 0)
    return {"labels": labels, "data": data}

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
    
    tab = request.args.get("tab", "dashboard")
    chat_with = request.args.get("chat_with", "")
    msg = request.args.get("msg", "")
    
    # ড্যাশবোর্ড লোড হলে অটোমেটিক একবার ট্রাই করবে
    if tab == "dashboard" and not msg:
        pull_orders_from_pathao()

    s = get_all_settings()
    analytics = {
        "total_orders": db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)["c"] or 0,
        "total_revenue": db_query("SELECT SUM(total) as s FROM orders", fetchone=True)["s"] or 0,
        "chart_data": get_chart_data()
    }
    
    unread_chat_count = db_query("SELECT COUNT(DISTINCT from_number) as c FROM messages WHERE direction='inbound'", fetchone=True)["c"] or 0
    orders = db_query("SELECT * FROM orders ORDER BY id DESC LIMIT 100", fetchall=True) or []
    users = db_query("SELECT * FROM users ORDER BY last_active DESC LIMIT 30", fetchall=True) or []
    
    chat_history = []
    if chat_with:
        chat_history = db_query("SELECT * FROM messages WHERE from_number=? ORDER BY id DESC LIMIT 50", (chat_with,), fetchall=True) or []
        chat_history.reverse()

    return render_template(f"{tab}.html", settings=s, analytics=analytics, orders=orders, users=users, unread_chat_count=unread_chat_count, active_chat=chat_with, chat_history=chat_history, msg=msg)

@app.route("/admin/sync-pathao-status")
def sync_pathao_status():
    if not session.get("logged_in"): return redirect("/admin/login")
    new_count = pull_orders_from_pathao()
    return redirect(url_for('admin_portal', tab='dashboard', msg=f"Pathao Sync Complete! {new_count} new orders synced."))

@app.route("/admin/settings/save", methods=["POST"])
def save_settings():
    if not session.get("logged_in"): return redirect("/admin/login")
    for k, v in request.form.items():
        db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?", (k, v, v), commit=True)
    return redirect("/admin?tab=settings&msg=Updated")

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
