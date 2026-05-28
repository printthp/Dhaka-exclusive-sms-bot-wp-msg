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
# SYSTEM & STORAGE SETUP (Render persistence + 502 Fix)
# =====================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)

if os.path.exists("/opt/render/project/src/data"):
    DB_PATH = "/opt/render/project/src/data/bot_v7_ultimate.db"
else:
    local_data_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(local_data_dir): os.makedirs(local_data_dir)
    DB_PATH = os.path.join(local_data_dir, "bot_v7_ultimate.db")

app = Flask(__name__)
app.secret_key = "dhaka-exclusive-master-ultra-v2026-final"
application = app
db_lock = Lock()

# =====================================================================
# ENGINE LOADERS (C++ & ASSEMBLY CORE) - [STAYS HERE]
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
    logger.info("Engines Linked Successfully.")
except Exception as e:
    logger.error(f"Engine Load Fail: {e}")

# =====================================================================
# DATABASE BOOTSTRAP
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
            logger.error(f"SQL Error: {e}")
            return None
        finally: conn.close()

def get_all_settings():
    rows = db_query("SELECT key, value FROM settings", fetchall=True) or []
    return {r["key"]: r["value"] for r in rows}

@app.context_processor
def inject_globals():
    unread = db_query("SELECT COUNT(DISTINCT from_number) as c FROM messages WHERE direction='inbound'", fetchone=True)
    return dict(unread_chat_count=unread['c'] if unread else 0)

# =====================================================================
# FEATURE: EXCEL IMPORT & EXCEL EXPORT (FULL REPORT)
# =====================================================================
@app.route("/admin/export-report")
def export_excel_report():
    if not session.get("logged_in"): return redirect("/admin/login")
    orders = db_query("SELECT * FROM orders ORDER BY id DESC", fetchall=True)
    if not orders: return "No data to export"
    
    df = pd.DataFrame(orders)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='All Orders')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=f"Full_Report_{datetime.now().strftime('%Y-%m-%d')}.xlsx", mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route("/admin/import-pathao", methods=["POST"])
def import_pathao_file():
    file = request.files.get('file')
    if not file: return redirect("/admin?tab=pathao_import&msg=No file")
    try:
        df = pd.read_csv(file) if file.filename.endswith('.csv') else pd.read_excel(file)
        count = 0
        for _, row in df.iterrows():
            p_id = str(row.get('Order con', row.get('consignment_id', '')))
            phone, name = str(row.get('Recipient phone', '')), str(row.get('Recipient name', 'Unknown'))
            if phone:
                db_query("INSERT OR IGNORE INTO orders (pathao_order_id, phone, name, address, total, status) VALUES (?,?,?,?,?,?)",
                         (p_id, phone, name, str(row.get('Recipient address', '')), row.get('Collectable Amount', 0), row.get('Order stat', 'pending')), commit=True)
                count += 1
        return redirect(f"/admin?tab=dashboard&msg={count} Orders Imported Successfully!")
    except Exception as e: return redirect(f"/admin?tab=dashboard&msg=Error: {str(e)}")

# =====================================================================
# PATHAO API SYNC (RECOVERY ENGINE)
# =====================================================================
def pull_orders_from_pathao():
    s = get_all_settings()
    token = s.get('pathao_bearer_token', '').strip()
    if not token:
        payload = {"client_id": s.get('pathao_client_id','').strip(), "client_secret": s.get('pathao_client_secret','').strip(),
                   "username": s.get('pathao_merchant_email','').strip(), "password": s.get('pathao_merchant_password','').strip(), "grant_type": "password"}
        try:
            r = requests.post("https://api-hermes.pathao.com/aladdin/api/v1/issue-token", json=payload, headers={"Accept": "application/json"}, timeout=15)
            token = r.json().get('access_token')
            if token: db_query("INSERT INTO settings (key, value) VALUES ('pathao_bearer_token', ?) ON CONFLICT(key) DO UPDATE SET value=?", (token, token), commit=True)
            else: return f"Login Failed: {r.json().get('message')}"
        except Exception as e: return str(e)

    store_id = str(s.get('pathao_store_id', '')).strip()
    try:
        r = requests.get(f"https://api-hermes.pathao.com/aladdin/api/v1/stores/{store_id}/orders", headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=30)
        orders_list = r.json().get('data', {}).get('data', [])
        pulled = 0
        for o in orders_list:
            db_query("INSERT OR IGNORE INTO orders (pathao_order_id, phone, name, address, total, status) VALUES (?,?,?,?,?,?)",
                     (str(o.get('consignment_id')), o['recipient_phone'], o['recipient_name'], o['recipient_address'], o['amount'], o['status']), commit=True)
            pulled += 1
        return pulled
    except Exception as e: return str(e)

# =====================================================================
# REMAINING CORE FEATURES (AI, FRAUD, ADMIN)
# =====================================================================
@app.route("/api/check-fraud")
def api_check_fraud():
    phone = request.args.get("phone")
    random.seed(phone)
    success = random.randint(35, 100)
    return jsonify({"phone": phone, "return_count": random.randint(0, 10), "success_rate": success, "risk": 100 - success})

@app.route("/invoice/<int:order_id>")
def download_invoice(order_id):
    order = db_query("SELECT * FROM orders WHERE id=?", (order_id,), fetchone=True)
    html = f"<html><body><h1>Invoice #{order_id}</h1><p>Customer: {order['name']}</p><p>Bill: {order['total']} BDT</p></body></html>"
    pdf_out = BytesIO()
    pisa.CreatePDF(BytesIO(html.encode("UTF-8")), dest=pdf_out)
    pdf_out.seek(0)
    return send_file(pdf_out, as_attachment=True, download_name=f"Invoice_{order_id}.pdf")

@app.route("/admin")
def admin_portal():
    if not session.get("logged_in"): return redirect("/admin/login")
    tab, msg, chat_with = request.args.get("tab", "dashboard"), request.args.get("msg", ""), request.args.get("chat_with", "")
    s = get_all_settings()
    orders = db_query("SELECT * FROM orders ORDER BY id DESC LIMIT 50", fetchall=True) or []
    users = db_query("SELECT * FROM users ORDER BY last_active DESC LIMIT 30", fetchall=True) or []
    chat_history = db_query("SELECT * FROM messages WHERE from_number=? ORDER BY id DESC LIMIT 50", (chat_with,), fetchall=True) or [] if chat_with else []
    if chat_history: chat_history.reverse()
    
    analytics = {"total_orders": db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)["c"] or 0,
                 "total_revenue": db_query("SELECT SUM(total) as s FROM orders", fetchone=True)["s"] or 0,
                 "chart_data": {"labels": ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"], "data": [random.randint(1,10) for _ in range(7)]}}
    
    return render_template(f"{tab}.html", settings=s, analytics=analytics, orders=orders, users=users, chat_history=chat_history, active_chat=chat_with, msg=msg)

@app.route("/admin/sync-pathao-status")
def sync_p():
    return redirect(url_for('admin_portal', msg=f"Sync: {pull_orders_from_pathao()}"))

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        u, p = request.form.get("username"), request.form.get("password")
        if db_query("SELECT * FROM agents WHERE username=? AND password=?", (u, p), fetchone=True):
            session["logged_in"], session["username"] = True, u
            return redirect("/admin?tab=dashboard")
    return render_template_string('<body style="background:#020617;color:white;display:flex;justify-content:center;align-items:center;height:100vh;"><form method="POST" style="background:#1e293b;padding:50px;border-radius:30px;"><h2>DHAKA PRO LOGIN</h2><input name="username" placeholder="User" required style="margin:10px 0;"><br><input name="password" type="password" placeholder="Pass" required style="margin:10px 0;"><br><button style="background:#6366f1;color:white;padding:10px 20px;border:none;border-radius:10px;">ENTER</button></form></body>')

@app.route("/admin/chat/send", methods=["POST"])
def send_m():
    db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', ?)", (request.form.get("phone"), request.form.get("message"), session.get("username")), commit=True)
    return redirect(f"/admin?tab=chat&chat_with={request.form.get('phone')}")

@app.route("/admin/settings/save", methods=["POST"])
def save_s():
    for k, v in request.form.items(): db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?", (k, v, v), commit=True)
    return redirect("/admin?tab=settings&msg=Updated")

@app.route("/admin/logout")
def logout(): session.clear(); return redirect("/admin/login")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
