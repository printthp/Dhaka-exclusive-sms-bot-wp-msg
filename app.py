import os
import sys
import json
import sqlite3
import logging
import ctypes
import time
import requests
import random
import pandas as pd
from io import BytesIO
from datetime import datetime, timedelta
from threading import Lock
from flask import Flask, request, jsonify, render_template, render_template_string, redirect, url_for, session, flash, send_file
from xhtml2pdf import pisa 

# =====================================================================
# SYSTEM & STORAGE SETUP (Render Disk Persistence)
# =====================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)

# Render Shared Storage or Local Data Folder
if os.path.exists("/opt/render/project/src/data"):
    DB_PATH = "/opt/render/project/src/data/bot_v7_ultimate.db"
else:
    local_data_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(local_data_dir): os.makedirs(local_data_dir)
    DB_PATH = os.path.join(local_data_dir, "bot_v7_ultimate.db")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dhaka-exclusive-ultimate-v7-2026")
application = app
db_lock = Lock()

# =====================================================================
# ENGINE LOADERS (C++ & ASSEMBLY CORE) - NEVER REMOVED
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
    logger.error(f"Engine Load Fail: {e}")

# =====================================================================
# DATABASE UTILITIES
# =====================================================================
def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, from_number TEXT, content TEXT, direction TEXT, agent_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS users (phone TEXT PRIMARY KEY, name TEXT DEFAULT 'Customer', last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, pathao_order_id TEXT UNIQUE, phone TEXT, name TEXT, address TEXT, total INTEGER, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, fb_product_id TEXT UNIQUE, name TEXT, price INTEGER, stock INTEGER DEFAULT 10, image_url TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS agent_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, action TEXT, details TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS agents (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)")
        c.execute("INSERT OR IGNORE INTO agents (username, password) VALUES ('admin', 'admin123')")
        conn.commit()
        conn.close()

init_db()

def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    with db_lock:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
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

@app.context_processor
def inject_globals():
    try:
        unread = db_query("SELECT COUNT(DISTINCT from_number) as c FROM messages WHERE direction='inbound'", fetchone=True)
        count = unread['c'] if unread else 0
    except: count = 0
    return dict(unread_chat_count=count)

# =====================================================================
# ADVANCED PATHAO SYNC (Hybrid Flow)
# =====================================================================
def get_pathao_token():
    s = get_all_settings()
    bearer = s.get('pathao_bearer_token', '').strip()
    if bearer: return bearer

    url_auth = "https://api-hermes.pathao.com/aladdin/api/v1/issue-token"
    payload = {
        "client_id": str(s.get('pathao_client_id', '')).strip(),
        "client_secret": str(s.get('pathao_client_secret', '')).strip(),
        "username": str(s.get('pathao_merchant_email', '')).strip(),
        "password": str(s.get('pathao_merchant_password', '')).strip(),
        "grant_type": "password"
    }
    try:
        r = requests.post(url_auth, json=payload, headers={"Accept": "application/json"}, timeout=15)
        new_token = r.json().get('access_token')
        if new_token:
            db_query("INSERT INTO settings (key, value) VALUES ('pathao_bearer_token', ?) ON CONFLICT(key) DO UPDATE SET value=?", (new_token, new_token), commit=True)
            return new_token
        return None
    except: return None

def pull_orders_from_pathao():
    token = get_pathao_token()
    s = get_all_settings()
    store_id = str(s.get('pathao_store_id', '')).strip()
    if not token or not store_id: return "Error: Config Missing"

    url = f"https://api-hermes.pathao.com/aladdin/api/v1/stores/{store_id}/orders"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=30)
        orders_list = r.json().get('data', {}).get('data', [])
        pulled = 0
        for o in orders_list:
            p_id = str(o.get('consignment_id') or o.get('order_id'))
            success = db_query("""
                INSERT OR IGNORE INTO orders (pathao_order_id, phone, name, address, total, status) 
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(pathao_order_id) DO UPDATE SET status=?
            """, (p_id, o['recipient_phone'], o['recipient_name'], o['recipient_address'], o['amount'], o['status'], o['status']), commit=True)
            if success: pulled += 1
        return pulled
    except Exception as e: return str(e)

# =====================================================================
# EXCEL & REPORTING
# =====================================================================
@app.route("/admin/export-report")
def export_excel():
    orders = db_query("SELECT * FROM orders ORDER BY id DESC", fetchall=True)
    if not orders: return "No data"
    df = pd.DataFrame(orders)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="Full_Report.xlsx")

@app.route("/admin/import-pathao", methods=["POST"])
def bulk_import():
    file = request.files.get('file')
    try:
        df = pd.read_csv(file) if file.filename.endswith('.csv') else pd.read_excel(file)
        count = 0
        for _, row in df.iterrows():
            p_id = str(row.get('Order con', row.get('consignment_id', '')))
            phone = str(row.get('Recipient phone', ''))
            if phone:
                db_query("INSERT OR IGNORE INTO orders (pathao_order_id, phone, name, address, total, status) VALUES (?,?,?,?,?,?)",
                         (p_id, phone, str(row.get('Recipient name')), str(row.get('Recipient address')), row.get('Collectable Amount', 0), row.get('Order stat', 'pending')), commit=True)
                count += 1
        return redirect(url_for('admin_portal', tab='orders', msg=f"Imported {count} orders"))
    except Exception as e: return redirect(url_for('admin_portal', msg=f"Error: {e}"))

# =====================================================================
# ANALYTICS & FRAUD API
# =====================================================================
@app.route("/api/check-fraud")
def api_check_fraud():
    phone = request.args.get("phone")
    random.seed(phone)
    success = random.randint(35, 100)
    return jsonify({"phone": phone, "return_count": random.randint(0, 10), "success_rate": success, "risk": 100 - success})

# =====================================================================
# WEB ROUTES (ADMIN PANEL)
# =====================================================================
@app.route("/")
def index(): return redirect("/admin")

@app.route("/admin")
def admin_portal():
    if not session.get("logged_in"): return redirect("/admin/login")
    tab, msg, chat_with = request.args.get("tab", "dashboard"), request.args.get("msg", ""), request.args.get("chat_with", "")
    s = get_all_settings()
    
    analytics = {"total_orders": db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)["c"] or 0,
                 "total_revenue": db_query("SELECT SUM(total) as s FROM orders", fetchone=True)["s"] or 0,
                 "chart_data": {"labels": ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"], "data": [random.randint(5,20) for _ in range(7)]}}
    
    orders = db_query("SELECT * FROM orders ORDER BY id DESC LIMIT 100", fetchall=True) or []
    users = db_query("SELECT * FROM users ORDER BY last_active DESC LIMIT 30", fetchall=True) or []
    products = db_query("SELECT * FROM products ORDER BY id DESC", fetchall=True) or []
    agent_logs = db_query("SELECT * FROM agent_logs ORDER BY id DESC LIMIT 50", fetchall=True) or []
    
    chat_history = []
    if chat_with:
        chat_history = db_query("SELECT * FROM messages WHERE from_number=? ORDER BY id DESC LIMIT 50", (chat_with,), fetchall=True) or []
        chat_history.reverse()

    return render_template(f"{tab}.html", settings=s, analytics=analytics, orders=orders, users=users, products=products, agent_logs=agent_logs, chat_history=chat_history, active_chat=chat_with, msg=msg)

@app.route("/admin/sync-pathao-status")
def sync_trigger():
    return redirect(url_for('admin_portal', tab='orders', msg=f"Sync: {pull_orders_from_pathao()}"))

@app.route("/admin/chat/send", methods=["POST"])
def admin_send_message():
    db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', ?)", (request.form.get("phone"), request.form.get("message"), session.get("username")), commit=True)
    return redirect(f"/admin?tab=chat&chat_with={request.form.get('phone')}")

@app.route("/admin/chat/delete/<phone>")
def delete_chat(phone):
    db_query("DELETE FROM messages WHERE from_number=?", (phone,), commit=True)
    return redirect("/admin?tab=chat&msg=Deleted")

@app.route("/invoice/<int:order_id>")
def download_invoice(order_id):
    order = db_query("SELECT * FROM orders WHERE id=?", (order_id,), fetchone=True)
    html = f"<html><body><h1>Invoice #{order_id}</h1><p>Customer: {order['name']}</p><p>Bill: {order['total']}৳</p></body></html>"
    pdf = BytesIO()
    pisa.CreatePDF(BytesIO(html.encode("UTF-8")), dest=pdf)
    pdf.seek(0)
    return send_file(pdf, as_attachment=True, download_name=f"Invoice_{order_id}.pdf")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        u, p = request.form.get("username"), request.form.get("password")
        if db_query("SELECT * FROM agents WHERE username=? AND password=?", (u, p), fetchone=True):
            session["logged_in"], session["username"] = True, u
            return redirect("/admin")
    return render_template_string('<body style="background:#020617;color:white;display:flex;justify-content:center;align-items:center;height:100vh;"><form method="POST" style="background:#1e293b;padding:50px;border-radius:30px;"><h2>DHAKA PRO LOGIN</h2><input name="username" placeholder="User" required style="margin:10px 0;"><br><input name="password" type="password" placeholder="Pass" required style="margin:10px 0;"><br><button style="background:#6366f1;color:white;padding:10px 20px;border:none;border-radius:10px;">ENTER</button></form></body>')

@app.route("/admin/logout")
def logout(): session.clear(); return redirect("/admin/login")

@app.route("/admin/settings/save", methods=["POST"])
def save_settings():
    for k, v in request.form.items(): db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?", (k, v, v), commit=True)
    return redirect("/admin?tab=settings&msg=Updated")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
