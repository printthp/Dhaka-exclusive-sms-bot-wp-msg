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
from threading import Lock
from flask import Flask, request, jsonify, render_template, render_template_string, redirect, url_for, session, flash, send_file
from xhtml2pdf import pisa  # PDF ইনভয়েসের জন্য

# =====================================================================
# SYSTEM SETUP & IMPORTS FIX
# =====================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dhaka-exclusive-erp-2026")
application = app

DB_FILE = "bot_v7_ultimate.db"
db_lock = Lock()

# =====================================================================
# DATABASE & INIT
# =====================================================================
def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, from_number TEXT, content TEXT, direction TEXT, agent_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS users (phone TEXT PRIMARY KEY, name TEXT DEFAULT 'Customer', last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
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

# =====================================================================
# FEATURE 1: AUTOMATIC PDF INVOICE GENERATOR
# =====================================================================
@app.route("/admin/order/invoice/<int:order_id>")
def download_invoice(order_id):
    if not session.get("logged_in"): return redirect("/admin/login")
    order = db_query("SELECT * FROM orders WHERE id=?", (order_id,), fetchone=True)
    if not order: return "Order Not Found", 404
    
    settings = {r['key']: r['value'] for r in db_query("SELECT * FROM settings", fetchall=True)}
    
    html_content = f"""
    <html><head><style>
        body {{ font-family: Arial, sans-serif; color: #333; }}
        .header {{ text-align: center; border-bottom: 2px solid #6366f1; padding-bottom: 10px; }}
        .details {{ margin-top: 20px; }}
        .footer {{ margin-top: 50px; font-size: 10px; color: #777; text-align: center; }}
    </style></head>
    <body>
        <div class="header"><h1>{settings.get('business_name', 'Dhaka Exclusive')}</h1><p>অফিসিয়াল মানি রিসিট</p></div>
        <div class="details">
            <p><strong>অর্ডার আইডি:</strong> #{order['id']}</p>
            <p><strong>নাম:</strong> {order['name']}</p>
            <p><strong>ফোন:</strong> {order['phone']}</p>
            <p><strong>ঠিকানা:</strong> {order['address']}</p>
            <hr>
            <h3>মোট প্রদেয় বিল: {order['total']}৳</h3>
        </div>
        <div class="footer"><p>আমাদের সাথে কেনাকাটা করার জন্য ধন্যবাদ!</p></div>
    </body></html>
    """
    
    pdf_out = BytesIO()
    pisa.CreatePDF(BytesIO(html_content.encode("UTF-8")), dest=pdf_out)
    pdf_out.seek(0)
    return send_file(pdf_out, as_attachment=True, download_name=f"Invoice_{order_id}.pdf", mimetype='application/pdf')

# =====================================================================
# FEATURE 2: CHAT QUEUE & DELETE MANAGEMENT
# =====================================================================
@app.route("/admin/chat/delete/<phone>")
def delete_chat(phone):
    if not session.get("logged_in"): return redirect("/admin/login")
    db_query("DELETE FROM messages WHERE from_number=?", (phone,), commit=True)
    db_query("DELETE FROM users WHERE phone=?", (phone,), commit=True)
    return redirect("/admin?tab=chat&msg=Chat Deleted")

# =====================================================================
# FEATURE 3: FACEBOOK CATALOG SYNC (DUMMY INTEGRATION)
# =====================================================================
@app.route("/admin/sync-facebook-trigger")
def sync_fb():
    if not session.get("logged_in"): return redirect("/admin/login")
    # এখানে মেটা এপিআই কল করার লজিক বসবে
    # আপাতত একটি সাকসেস মেসেজ এবং ডাটাবেস লগ রাখছি
    db_query("INSERT INTO agent_logs (username, action, details) VALUES (?, 'SYNC_FB', 'Manual Meta Sync Triggered')", (session.get("username"),), commit=True)
    return redirect("/admin?tab=inventory&msg=Facebook Catalog Sync Complete")

# =====================================================================
# FEATURE 4: DAILY/MONTHLY GRAPH DATA
# =====================================================================
def get_chart_data():
    days = []
    values = []
    for i in range(6, -1, -1):
        date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        count = db_query("SELECT COUNT(*) as c FROM orders WHERE created_at LIKE ?", (f"{date}%",), fetchone=True)['c']
        days.append(date)
        values.append(count)
    return {"labels": days, "data": values}

# =====================================================================
# WEB ROUTES
# =====================================================================

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        u, p = request.form.get("username"), request.form.get("password")
        agent = db_query("SELECT * FROM agents WHERE username=? AND password=?", (u, p), fetchone=True)
        if agent:
            session["logged_in"] = True
            session["username"] = agent["username"]
            return redirect("/admin")
    return render_template_string("""
        <body style="background:#0f172a; color:white; font-family:sans-serif; display:flex; justify-content:center; align-items:center; height:100vh;">
            <form method="POST" style="background:#1e293b; padding:40px; border-radius:30px; width:300px; text-align:center;">
                <h2 style="color:#6366f1">ADMIN PRO</h2>
                <input name="username" placeholder="Username" required style="width:100%; padding:15px; margin:10px 0; border:none; border-radius:10px; background:#0f172a; color:white;">
                <input name="password" type="password" placeholder="Password" required style="width:100%; padding:15px; margin:10px 0; border:none; border-radius:10px; background:#0f172a; color:white;">
                <button style="width:100%; padding:15px; background:#6366f1; color:white; border:none; border-radius:12px; font-weight:bold; cursor:pointer;">LOGIN</button>
            </form>
        </body>
    """)

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
    agent_logs = db_query("SELECT * FROM agent_logs ORDER BY id DESC LIMIT 40", fetchall=True) or []
    chat_history = db_query("SELECT * FROM messages WHERE from_number=? ORDER BY id DESC LIMIT 40", (chat_with,), fetchall=True) or []
    chat_history.reverse()

    return render_template(f"{tab}.html", settings=s, analytics=analytics, orders=orders, users=users, products=products, agent_logs=agent_logs, unread_chat_count=unread_chat_count, active_chat=chat_with, chat_history=chat_history)

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
