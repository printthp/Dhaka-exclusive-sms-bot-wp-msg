import os
import requests
import sqlite3
import json
import logging
import pandas as pd
from datetime import datetime, timedelta
from threading import Lock
from flask import Flask, request, jsonify, render_template, redirect, url_for, session, send_file, render_template_string
from io import BytesIO
from xhtml2pdf import pisa

# =====================================================================
# SYSTEM SETUP
# =====================================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Render Shared Disk Path
if os.path.exists("/opt/render/project/src/data"):
    DB_PATH = "/opt/render/project/src/data/bot_v7_ultimate.db"
else:
    local_data_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(local_data_dir): os.makedirs(local_data_dir)
    DB_PATH = os.path.join(local_data_dir, "bot_v7_ultimate.db")

app = Flask(__name__)
app.secret_key = "dhaka-exclusive-master-ultra-v2026"
db_lock = Lock()

# =====================================================================
# DATABASE & CORE ENGINE (Includes your C++ Loaders)
# =====================================================================
def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, from_number TEXT, content TEXT, direction TEXT, agent_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, pathao_order_id TEXT UNIQUE, phone TEXT, name TEXT, address TEXT, total INTEGER, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
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
            logger.error(f"Error: {e}")
            return None
        finally: conn.close()

def get_all_settings():
    rows = db_query("SELECT key, value FROM settings", fetchall=True) or []
    return {r["key"]: r["value"] for r in rows}

@app.context_processor
def inject_global_vars():
    unread = db_query("SELECT COUNT(DISTINCT from_number) as c FROM messages WHERE direction='inbound'", fetchone=True)
    return dict(unread_chat_count=unread['c'] if unread else 0)

# =====================================================================
# POWERFUL PATHAO PULL METHOD (DEEP SCAN)
# =====================================================================
def deep_scan_pathao():
    s = get_all_settings()
    token = s.get('pathao_bearer_token', '').strip()
    store_id = str(s.get('pathao_store_id', '')).strip()

    if not token or not store_id: return "Error: Settings missing."

    # পাঠাও এপিআই-এর একাধিক তারিখের ডাটা চেক করার জন্য ইউআরএল
    url = f"https://api-hermes.pathao.com/aladdin/api/v1/stores/{store_id}/orders"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    
    try:
        r = requests.get(url, headers=headers, timeout=25)
        res = r.json()
        
        # ডাটা ফরম্যাট অনুযায়ী লিস্ট ধরা
        orders_data = res.get('data', {}).get('data', []) if isinstance(res.get('data'), dict) else res.get('data', [])
        
        if not orders_data:
            return "API connected, but no orders returned from Pathao."

        import_count = 0
        for o in orders_data:
            p_id = str(o.get('consignment_id') or o.get('order_id'))
            success = db_query("""
                INSERT OR IGNORE INTO orders (pathao_order_id, phone, name, address, total, status)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (p_id, o.get('recipient_phone'), o.get('recipient_name'), o.get('recipient_address'), o.get('amount', 0), o.get('status')), commit=True)
            if success: import_count += 1
            
        return f"Successfully imported {import_count} orders."
    except Exception as e:
        return f"Sync Error: {str(e)}"

# =====================================================================
# FEATURES: EXCEL & PDF
# =====================================================================
@app.route("/admin/export-report")
def export_report():
    if not session.get("logged_in"): return redirect("/admin/login")
    orders = db_query("SELECT * FROM orders ORDER BY id DESC", fetchall=True)
    if not orders: return "No data"
    df = pd.DataFrame(orders)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=f"Report_{datetime.now().strftime('%Y-%m-%d')}.xlsx")

# =====================================================================
# ADMIN PANEL
# =====================================================================
@app.route("/admin")
def admin_portal():
    if not session.get("logged_in"): return redirect("/admin/login")
    tab, msg = request.args.get("tab", "dashboard"), request.args.get("msg", "")
    s = get_all_settings()
    
    analytics = {
        "total_orders": db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)['c'] or 0,
        "total_revenue": db_query("SELECT SUM(total) as s FROM orders", fetchone=True)['s'] or 0,
        "chart_data": {"labels": ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"], "data": [0,0,0,0,0,0,0]}
    }
    
    orders = db_query("SELECT * FROM orders ORDER BY id DESC LIMIT 100", fetchall=True)
    users = db_query("SELECT * FROM users ORDER BY last_active DESC LIMIT 30", fetchall=True)
    
    return render_template(f"{tab}.html", settings=s, analytics=analytics, orders=orders, users=users, msg=msg)

@app.route("/admin/sync-pathao-status")
def sync_pathao_now():
    res = deep_scan_pathao()
    return redirect(url_for('admin_portal', msg=res))

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("username") == "admin" and request.form.get("password") == "admin123":
            session["logged_in"] = True; return redirect("/admin")
    return render_template_string('<body style="background:#020617;color:white;display:flex;justify-content:center;align-items:center;height:100vh;"><form method="POST" style="background:#1e293b;padding:50px;border-radius:30px;"><h2>DHAKA PRO LOGIN</h2><input name="username" placeholder="User" required style="margin:10px 0;"><br><input name="password" type="password" placeholder="Pass" required style="margin:10px 0;"><br><button style="background:#6366f1;color:white;border:none;padding:10px 20px;border-radius:10px;">ENTER</button></form></body>')

@app.route("/admin/settings/save", methods=["POST"])
def save_settings():
    for k, v in request.form.items():
        db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?", (k, v, v), commit=True)
    return redirect("/admin?tab=settings&msg=Updated")

@app.route("/admin/db-backup")
def db_backup():
    return send_file(DB_PATH, as_attachment=True, download_name="backup.db")

@app.route("/admin/logout")
def admin_logout():
    session.clear(); return redirect("/admin/login")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
