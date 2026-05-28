import os
import sys
import json
import sqlite3
import logging
import ctypes
import time
import requests
from io import BytesIO
from datetime import datetime, timedelta
from threading import Thread, Lock
from flask import Flask, request, jsonify, render_template, render_template_string, redirect, url_for, session, flash, send_file
from xhtml2pdf import pisa 

# =====================================================================
# SYSTEM & LOGGING SETUP
# =====================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dhaka-exclusive-pro-ultimate-2026")
application = app

DB_FILE = "bot_v7_ultimate.db"
db_lock = Lock()

# =====================================================================
# ENGINE LOADERS (C++ & ASM)
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
        c.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, name TEXT, address TEXT, total INTEGER, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, fb_product_id TEXT UNIQUE, name TEXT, price INTEGER, stock INTEGER DEFAULT 10, image_url TEXT)")
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
        except Exception as e:
            logger.error(f"DB Error: {e}")
            return None
        finally: conn.close()

def get_all_settings():
    rows = db_query("SELECT key, value FROM settings", fetchall=True) or []
    return {r["key"]: r["value"] for r in rows}

def get_chart_data():
    labels, data = [], []
    for i in range(6, -1, -1):
        target = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        res = db_query("SELECT COUNT(*) as c FROM orders WHERE created_at LIKE ?", (f"{target}%",), fetchone=True)
        labels.append((datetime.now() - timedelta(days=i)).strftime('%a'))
        data.append(res['c'] if res else 0)
    return {"labels": labels, "data": data}

# =====================================================================
# WHATSAPP & AI LOGIC
# =====================================================================
def send_whatsapp(to, content):
    s = get_all_settings()
    token = s.get("permanent_token")
    phone_id = s.get("phone_number_id")
    if not token or not phone_id: return False
    url = f"https://graph.facebook.com/v21.0/{phone_id}/messages"
    body = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": content}}
    try:
        r = requests.post(url, json=body, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        return r.status_code in [200, 201]
    except: return False

# =====================================================================
# ADMIN PANEL ROUTES
# =====================================================================

@app.route("/")
def index(): return redirect("/admin")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        u, p = request.form.get("username"), request.form.get("password")
        agent = db_query("SELECT * FROM agents WHERE username=? AND password=?", (u, p), fetchone=True)
        if agent:
            session["logged_in"], session["username"] = True, agent["username"]
            return redirect("/admin")
    return render_template_string("""<body style="background:#0f172a;color:white;display:flex;justify-content:center;align-items:center;height:100vh;font-family:sans-serif;"><form method="POST" style="background:#1e293b;padding:40px;border-radius:20px;text-align:center;"><h2>ADMIN LOGIN</h2><input name="username" placeholder="User" style="width:100%;padding:10px;margin:10px 0;border-radius:10px;border:none;"><br><input name="password" type="password" placeholder="Pass" style="width:100%;padding:10px;margin:10px 0;border-radius:10px;border:none;"><br><button style="width:100%;padding:10px;background:#6366f1;color:white;border:none;border-radius:10px;margin-top:20px;">LOGIN</button></form></body>""")

@app.route("/admin")
def admin_portal():
    if not session.get("logged_in"): return redirect("/admin/login")
    tab = request.args.get("tab", "dashboard")
    chat_with = request.args.get("chat_with", "")
    s = get_all_settings()
    analytics = {
        "total_orders": db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)["c"] or 0,
        "total_revenue": db_query("SELECT SUM(total) as s FROM orders", fetchone=True)["s"] or 0,
        "chart_data": get_chart_data()
    }
    unread_chat_count = db_query("SELECT COUNT(DISTINCT from_number) as c FROM messages WHERE direction='inbound'", fetchone=True)["c"] or 0
    orders = db_query("SELECT * FROM orders ORDER BY id DESC LIMIT 50", fetchall=True) or []
    users = db_query("SELECT * FROM users ORDER BY last_active DESC LIMIT 30", fetchall=True) or []
    products = db_query("SELECT * FROM products ORDER BY id DESC LIMIT 30", fetchall=True) or []
    agent_logs = db_query("SELECT * FROM agent_logs ORDER BY id DESC LIMIT 50", fetchall=True) or []
    chat_history = db_query("SELECT * FROM messages WHERE from_number=? ORDER BY id DESC LIMIT 40", (chat_with,), fetchall=True) or []
    chat_history.reverse()

    return render_template(f"{tab}.html", settings=s, analytics=analytics, orders=orders, users=users, products=products, agent_logs=agent_logs, unread_chat_count=unread_chat_count, active_chat=chat_with, chat_history=chat_history)

@app.route("/admin/chat/send", methods=["POST"])
def admin_send_message():
    if not session.get("logged_in"): return redirect("/admin/login")
    phone, msg = request.form.get("phone"), request.form.get("message")
    if phone and msg:
        send_whatsapp(phone, msg)
        db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', ?)", (phone, msg, session.get("username")), commit=True)
    return redirect(f"/admin?tab=chat&chat_with={phone}")

@app.route("/admin/agents/add", methods=["POST"])
def add_agent():
    if not session.get("logged_in") or session.get("username") != 'admin': return redirect("/admin?tab=agents&msg=Denied")
    u, p = request.form.get("username"), request.form.get("password")
    if u and p: db_query("INSERT OR IGNORE INTO agents (username, password) VALUES (?, ?)", (u, p), commit=True)
    return redirect("/admin?tab=agents&msg=Success")

@app.route("/admin/settings/save", methods=["POST"])
def save_settings():
    if not session.get("logged_in"): return redirect("/admin/login")
    for k, v in request.form.items():
        db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?", (k, v, v), commit=True)
    return redirect("/admin?tab=settings&msg=Updated")

@app.route("/invoice/<int:order_id>")
def download_invoice(order_id):
    order = db_query("SELECT * FROM orders WHERE id=?", (order_id,), fetchone=True)
    if not order: return "Not Found", 404
    html = f"<html><body style='padding:50px;'><h1>Money Receipt</h1><hr><p>Customer: {order['name']}</p><p>Bill: {order['total']} BDT</p></body></html>"
    pdf_out = BytesIO()
    pisa.CreatePDF(BytesIO(html.encode("UTF-8")), dest=pdf_out)
    pdf_out.seek(0)
    return send_file(pdf_out, as_attachment=True, download_name=f"Order_{order_id}.pdf", mimetype='application/pdf')

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
