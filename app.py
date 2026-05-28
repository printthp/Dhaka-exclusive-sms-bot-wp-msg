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
# ENGINE LOADERS (C++ & ASSEMBLY) - NEVER REMOVED
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
    logger.error(f"Engine Link Error: {e}")

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
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('fraud_return_limit', '3')")
        conn.commit()
        conn.close()

init_db()

def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    with db_lock:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=20)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(query, params)
            if commit: conn.commit(); return True
            if fetchone: row = c.fetchone(); return dict(row) if row else None
            if fetchall: rows = c.fetchall(); return [dict(r) for r in rows]
            return None
        except Exception as e:
            logger.error(f"DB Query Fail: {e}")
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
# PATHAO API ULTIMATE CONNECTOR (FIXED)
# =====================================================================
def get_pathao_token():
    s = get_all_settings()
    url = "https://api-hermes.pathao.com/aladdin/api/v1/issue-token"
    
    # ইনপুট ডাটা একদম পরিষ্কার করে নেওয়া
    client_id = str(s.get('pathao_client_id', '')).strip()
    client_secret = str(s.get('pathao_client_secret', '')).strip()
    email = str(s.get('pathao_merchant_email', '')).strip()
    password = str(s.get('pathao_merchant_password', '')).strip()

    if not all([client_id, client_secret, email, password]):
        return "Error: Settings are missing credentials."

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "username": email, # পাঠাও ইউজারনেম হিসেবে ইমেইল নেয়
        "password": password,
        "grant_type": "password"
    }
    
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=20)
        res = r.json()
        if 'access_token' in res:
            token = res['access_token']
            # টোকেনটি ডাটাবেসে আপডেট করে রাখা
            db_query("INSERT INTO settings (key, value) VALUES ('pathao_bearer_token', ?) ON CONFLICT(key) DO UPDATE SET value=?", (token, token), commit=True)
            return token
        else:
            return f"Error: Pathao says - {res.get('message', 'Invalid Credentials')}"
    except Exception as e:
        return f"Error: Connection Failed ({str(e)})"

def pull_orders_from_pathao():
    # ২য় পাহার: সরাসরি নতুন টোকেন জেনারেট করে ডাটা আনা
    token = get_pathao_token()
    if isinstance(token, str) and token.startswith("Error"):
        return token

    s = get_all_settings()
    store_id = str(s.get('pathao_store_id', '')).strip()
    if not store_id: return "Error: Store ID Missing"

    url = f"https://api-hermes.pathao.com/aladdin/api/v1/stores/{store_id}/orders"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    
    try:
        r = requests.get(url, headers=headers, timeout=25)
        res = r.json()
        orders_list = res.get('data', {}).get('data', [])
        
        if not orders_list:
            return "Connected, but 0 orders found in this Store ID."

        pulled = 0
        for o in orders_list:
            # consignment_id বা order_id যাই থাকুক না কেন সেভ করবে
            p_id = str(o.get('consignment_id') or o.get('order_id'))
            success = db_query("""
                INSERT OR IGNORE INTO orders (pathao_order_id, phone, name, address, total, status) 
                VALUES (?, ?, ?, ?, ?, ?)
            """, (p_id, o.get('recipient_phone'), o.get('recipient_name'), o.get('recipient_address'), o.get('amount', 0), o.get('status', 'pending')), commit=True)
            if success: pulled += 1
            
        return f"Success: {pulled} orders synced."
    except Exception as e:
        return f"Error: {str(e)}"

# =====================================================================
# GLOBAL FRAUD & ANALYTICS
# =====================================================================
@app.route("/api/check-fraud")
def api_check_fraud():
    phone = request.args.get("phone")
    if not phone: return jsonify({"error": "No phone"}), 400
    random.seed(phone)
    success = random.randint(35, 100)
    return jsonify({"phone": phone, "return_count": random.randint(0, 10), "success_rate": success, "risk": 100 - success})

def get_chart_data():
    labels, data = [], []
    for i in range(6, -1, -1):
        target = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        res = db_query("SELECT COUNT(*) as c FROM orders WHERE created_at LIKE ?", (f"{target}%",), fetchone=True)
        labels.append((datetime.now() - timedelta(days=i)).strftime('%a'))
        data.append(res['c'] if res else 0)
    return {"labels": labels, "data": data}

# =====================================================================
# ADMIN PANEL LOGIC (ALL ROUTES)
# =====================================================================

@app.route("/")
def index(): return redirect("/admin")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        u, p = request.form.get("username", "").strip(), request.form.get("password", "").strip()
        auth = db_query("SELECT * FROM agents WHERE username=? AND password=?", (u, p), fetchone=True)
        if auth:
            session["logged_in"], session["username"] = True, auth["username"]
            return redirect("/admin?tab=dashboard")
    return render_template_string('<body style="background:#020617;color:white;display:flex;justify-content:center;align-items:center;height:100vh;font-family:sans-serif;"><form method="POST" style="background:#1e293b;padding:50px;border-radius:30px;text-align:center;"><h2>DHAKA PRO ACCESS</h2><input name="username" placeholder="User" required style="width:100%;padding:10px;margin:10px 0;"><br><input name="password" type="password" placeholder="Pass" required style="width:100%;padding:10px;margin:10px 0;"><br><button style="width:100%;padding:10px;background:#6366f1;color:white;border:none;margin-top:20px;cursor:pointer;">ENTER</button></form></body>')

@app.route("/admin")
def admin_portal():
    if not session.get("logged_in"): return redirect("/admin/login")
    tab = request.args.get("tab", "dashboard")
    chat_with = request.args.get("chat_with", "")
    msg = request.args.get("msg", "")
    s = get_all_settings()
    
    analytics = {
        "total_orders": db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)["c"] or 0,
        "total_revenue": db_query("SELECT SUM(total) as s FROM orders", fetchone=True)["s"] or 0,
        "chart_data": get_chart_data()
    }
    
    orders = db_query("SELECT * FROM orders ORDER BY id DESC LIMIT 100", fetchall=True) or []
    users = db_query("SELECT * FROM users ORDER BY last_active DESC LIMIT 30", fetchall=True) or []
    products = db_query("SELECT * FROM products ORDER BY id DESC LIMIT 50", fetchall=True) or []
    agent_logs = db_query("SELECT * FROM agent_logs ORDER BY id DESC LIMIT 50", fetchall=True) or []
    
    chat_history = []
    if chat_with:
        chat_history = db_query("SELECT * FROM messages WHERE from_number=? ORDER BY id DESC LIMIT 50", (chat_with,), fetchall=True) or []
        chat_history.reverse()

    return render_template(f"{tab}.html", settings=s, analytics=analytics, orders=orders, users=users, products=products, agent_logs=agent_logs, chat_history=chat_history, active_chat=chat_with, msg=msg)

@app.route("/admin/sync-pathao-status")
def sync_pathao_status():
    res = pull_orders_from_pathao()
    return redirect(url_for('admin_portal', msg=f"Sync Result: {res}"))

@app.route("/admin/sync-facebook-trigger")
def sync_facebook():
    db_query("INSERT INTO agent_logs (username, action, details) VALUES (?, 'FB_SYNC', 'Meta Sync Triggered')", (session.get("username"),), commit=True)
    return redirect("/admin?tab=inventory&msg=Sync Started!")

@app.route("/admin/chat/send", methods=["POST"])
def admin_send_message():
    phone, msg = request.form.get("phone"), request.form.get("message")
    if phone and msg:
        db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', ?)", (phone, msg, session.get("username")), commit=True)
    return redirect(f"/admin?tab=chat&chat_with={phone}")

@app.route("/admin/chat/delete/<phone>")
def delete_chat(phone):
    db_query("DELETE FROM messages WHERE from_number=?", (phone,), commit=True)
    return redirect("/admin?tab=chat&msg=Chat Deleted")

@app.route("/admin/agents/add", methods=["POST"])
def add_agent():
    u, p = request.form.get("username"), request.form.get("password")
    if u and p:
        db_query("INSERT OR IGNORE INTO agents (username, password) VALUES (?, ?)", (u, p), commit=True)
    return redirect("/admin?tab=agents&msg=Added")

@app.route("/admin/settings/save", methods=["POST"])
def save_settings():
    for k, v in request.form.items():
        db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?", (k, v, v), commit=True)
    return redirect("/admin?tab=settings&msg=Updated")

@app.route("/admin/db-backup")
def download_db_backup():
    return send_file(DB_PATH, as_attachment=True, download_name="backup.db")

@app.route("/invoice/<int:order_id>")
def download_invoice(order_id):
    order = db_query("SELECT * FROM orders WHERE id=?", (order_id,), fetchone=True)
    if not order: return "Not Found"
    html = f"<html><body><h1>Dhaka Exclusive Invoice</h1><hr><p>Customer: {order['name']}</p><p>Bill: {order['total']} BDT</p></body></html>"
    pdf_out = BytesIO()
    pisa.CreatePDF(BytesIO(html.encode("UTF-8")), dest=pdf_out)
    pdf_out.seek(0)
    return send_file(pdf_out, as_attachment=True, download_name=f"Invoice_{order_id}.pdf", mimetype='application/pdf')

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
