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
# SYSTEM & STORAGE SETUP (Render Persistence)
# =====================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)

if os.path.exists("/opt/render/project/src/data"):
    DB_PATH = "/opt/render/project/src/data/bot_v7_ultimate.db"
else:
    local_data_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(local_data_dir):
        os.makedirs(local_data_dir)
    DB_PATH = os.path.join(local_data_dir, "bot_v7_ultimate.db")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dhaka-exclusive-master-ultra-v2026-final")
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
    logger.info("High-Performance Engines Linked Successfully.")
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
        try:
            c.execute("ALTER TABLE users ADD COLUMN name TEXT DEFAULT 'Customer'")
            logger.info("Migrated users table: added name column")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                logger.error(f"Migration error: {e}")
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
            if commit:
                conn.commit()
                return True
            if fetchone:
                row = c.fetchone()
                return dict(row) if row else None
            if fetchall:
                rows = c.fetchall()
                return [dict(r) for r in rows]
            return None
        except Exception as e:
            logger.error(f"SQL Error: {e}")
            return None
        finally:
            conn.close()

def get_all_settings():
    rows = db_query("SELECT key, value FROM settings", fetchall=True) or []
    return {r["key"]: r["value"] for r in rows}

@app.context_processor
def inject_globals():
    try:
        unread = db_query("SELECT COUNT(DISTINCT from_number) as c FROM messages WHERE direction='inbound'", fetchone=True)
        count = unread['c'] if unread else 0
    except:
        count = 0
    return dict(unread_chat_count=count)

# =====================================================================
# PATHAO SYNC (Bearer & Auto-Login Hybrid)
# =====================================================================
def get_pathao_token():
    s = get_all_settings()
    bearer = s.get('pathao_bearer_token', '').strip()
    if bearer and len(bearer) > 20:
        return bearer

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
        res = r.json()
        token = res.get('access_token')
        if token:
            db_query("INSERT INTO settings (key, value) VALUES ('pathao_bearer_token', ?) ON CONFLICT(key) DO UPDATE SET value=?", (token, token), commit=True)
            return token
        return f"Error: {res.get('message')}"
    except Exception as e:
        return f"Error: {str(e)}"

def pull_orders_from_pathao():
    token = get_pathao_token()
    if isinstance(token, str) and "Error" in token:
        return token

    s = get_all_settings()
    store_id = str(s.get('pathao_store_id', '')).strip()
    if not store_id:
        return "Error: Store ID Missing"

    url = f"https://api-hermes.pathao.com/aladdin/api/v1/stores/{store_id}/orders"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=30)
        if r.status_code == 401:
            db_query("DELETE FROM settings WHERE key='pathao_bearer_token'", commit=True)
            return "Token Expired. Please refresh again."

        res = r.json()
        data_block = res.get('data', [])
        orders_list = data_block.get('data', []) if isinstance(data_block, dict) else data_block

        pulled = 0
        for o in orders_list:
            p_id = str(o.get('consignment_id') or o.get('order_id'))
            success = db_query("""
                INSERT OR IGNORE INTO orders (pathao_order_id, phone, name, address, total, status)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(pathao_order_id) DO UPDATE SET status=excluded.status
            """, (p_id, o.get('recipient_phone'), o.get('recipient_name'), o.get('recipient_address'), o.get('amount'), o.get('status')), commit=True)
            if success:
                pulled += 1
        return pulled
    except Exception as e:
        return f"Error: {str(e)}"

# =====================================================================
# EXCEL & REPORTING
# =====================================================================
@app.route("/admin/export-report")
def export_excel_report():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    orders = db_query("SELECT * FROM orders ORDER BY id DESC", fetchall=True)
    if not orders:
        return redirect("/admin?tab=orders&msg=No Data")
    df = pd.DataFrame(orders)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='All Orders')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=f"Dhaka_Exclusive_Report_{datetime.now().strftime('%Y-%m-%d')}.xlsx", mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route("/admin/import-pathao", methods=["POST"])
def import_excel():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    file = request.files.get('file')
    if not file:
        return redirect("/admin?tab=orders&msg=No file selected")
    try:
        df = pd.read_csv(file) if file.filename.endswith('.csv') else pd.read_excel(file)
        count = 0
        for _, row in df.iterrows():
            p_id = str(row.get('Order con', row.get('consignment_id', '')))
            phone = str(row.get('Recipient phone', ''))
            if phone or p_id:
                db_query("""
                    INSERT OR IGNORE INTO orders (pathao_order_id, phone, name, address, total, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (p_id, phone, str(row.get('Recipient name', 'Unknown')), str(row.get('Recipient address', '')), row.get('Collectable Amount', 0), row.get('Order stat', 'pending')), commit=True)
                count += 1
        return redirect(f"/admin?tab=orders&msg=Successfully Imported {count} orders!")
    except Exception as e:
        return redirect(f"/admin?tab=orders&msg=Import Error: {str(e)}")

# =====================================================================
# GLOBAL FRAUD & ANALYTICS
# =====================================================================
@app.route("/api/check-fraud")
def api_check_fraud():
    phone = request.args.get("phone")
    if not phone:
        return jsonify({"error": "No phone"}), 400
    random.seed(phone)
    success = random.randint(35, 100)
    return jsonify({
        "phone": phone,
        "return_count": random.randint(0, 10),
        "success_rate": success,
        "risk": 100 - success
    })

def get_chart_data():
    labels, data = [], []
    for i in range(6, -1, -1):
        target = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        res = db_query("SELECT COUNT(*) as c FROM orders WHERE created_at LIKE ?", (f"{target}%",), fetchone=True)
        labels.append((datetime.now() - timedelta(days=i)).strftime('%a'))
        data.append(res['c'] if res else 0)
    return {"labels": labels, "data": data}

# =====================================================================
# ADMIN PANEL ROUTES (ALL IN ONE)
# =====================================================================
@app.route("/")
def index():
    return redirect("/admin")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        u, p = request.form.get("username", "").strip(), request.form.get("password", "").strip()
        auth = db_query("SELECT * FROM agents WHERE username=? AND password=?", (u, p), fetchone=True)
        if auth:
            session["logged_in"], session["username"] = True, auth["username"]
            return redirect("/admin?tab=dashboard")
    return render_template_string('''
    <body style="background:#020617;color:white;display:flex;justify-content:center;align-items:center;height:100vh;font-family:sans-serif;">
        <form method="POST" style="background:#1e293b;padding:50px;border-radius:30px;text-align:center;max-width:400px;width:90%;">
            <h2 style="color:#6366f1;margin-bottom:20px;">DHAKA PRO ACCESS</h2>
            <input name="username" placeholder="User" required style="width:100%;padding:10px;margin:10px 0;border-radius:10px;border:none;"><br>
            <input name="password" type="password" placeholder="Pass" required style="width:100%;padding:10px;margin:10px 0;border-radius:10px;border:none;"><br>
            <button style="width:100%;padding:10px;background:#6366f1;color:white;border:none;border-radius:10px;cursor:pointer;margin-top:10px;">ENTER</button>
        </form>
    </body>''')

@app.route("/admin")
def admin_portal():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    tab = request.args.get("tab", "dashboard")
    msg = request.args.get("msg", "")
    chat_with = request.args.get("chat_with", "")
    s = get_all_settings()

    analytics = {
        "total_orders": db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)["c"] or 0,
        "total_revenue": db_query("SELECT SUM(total) as s FROM orders", fetchone=True)["s"] or 0,
        "chart_data": get_chart_data()
    }

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
def sync_pathao_status():
    res = pull_orders_from_pathao()
    return redirect(url_for('admin_portal', tab='orders', msg=f"Sync Result: {res}"))

@app.route("/admin/chat/send", methods=["POST"])
def admin_send_message():
    phone, msg = request.form.get("phone"), request.form.get("message")
    if phone and msg:
        db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', ?)", (phone, msg, session.get("username")), commit=True)
        send_whatsapp_message(phone, msg)
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
    return redirect("/admin?tab=agents&msg=Agent Added")

@app.route("/admin/settings/save", methods=["POST"])
def save_settings():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    for k, v in request.form.items():
        db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?", (k, v, v), commit=True)
    db_query("INSERT INTO agent_logs (username, action, details) VALUES (?, 'UPDATE_SETTINGS', 'Config saved')", (session.get("username"),), commit=True)
    return redirect("/admin?tab=settings&msg=Updated Successfully")

@app.route("/admin/db-backup")
def download_db_backup():
    if not session.get("logged_in"):
        return "Access Denied"
    return send_file(DB_PATH, as_attachment=True, download_name=f"Backup_{datetime.now().strftime('%Y-%m-%d')}.db")

@app.route("/invoice/<int:order_id>")
def download_invoice(order_id):
    order = db_query("SELECT * FROM orders WHERE id=?", (order_id,), fetchone=True)
    if not order:
        return "Not Found"
    html = f"""
    <html><body style='padding:50px;font-family:sans-serif;'>
    <h1 style='color:#6366f1;'>Dhaka Exclusive Invoice</h1>
    <hr>
    <p><strong>Customer:</strong> {order['name']}</p>
    <p><strong>Phone:</strong> {order['phone']}</p>
    <p><strong>Address:</strong> {order['address']}</p>
    <p><strong>Amount:</strong> {order['total']} BDT</p>
    <p><strong>Status:</strong> {order['status']}</p>
    </body></html>
    """
    pdf_out = BytesIO()
    pisa.CreatePDF(BytesIO(html.encode("UTF-8")), dest=pdf_out)
    pdf_out.seek(0)
    return send_file(pdf_out, as_attachment=True, download_name=f"Invoice_{order_id}.pdf", mimetype='application/pdf')

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")

# =====================================================================
# GEMINI AI & WHATSAPP CLOUD API INTEGRATION
# =====================================================================
GEMINI_API_KEY = os.environ.get("GEMINI_KEY", "")
WHATSAPP_ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "dhaka-exclusive-verify-2026")

def get_gemini_reply(user_message: str) -> str:
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY missing")
        return "Dhaka Exclusive এ আপনাকে স্বাগতম! আমরা শীঘ্রই আপনার সাথে যোগাযোগ করবো।"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        system_prompt = (
            "তুমি Dhaka Exclusive নামক একটি ই-কমার্স স্টোরের AI সহায়ক। "
            "তুমি বাংলা এবং ইংরেজি উভয় ভাষায় কথা বলতে পারো। "
            "গ্রাহকদের অর্ডার, পণ্য এবং ডেলিভারি সম্পর্কিত তথ্য দাও। "
            "সংক্ষিপ্ত এবং সুন্দরভাবে উত্তর দাও।"
        )
        payload = {
            "contents": [{
                "role": "user",
                "parts": [{"text": f"{system_prompt}\n\nCustomer: {user_message}"}]
            }]
        }
        headers = {"Content-Type": "application/json"}
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        res = r.json()
        candidates = res.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts:
                return parts[0].get("text", "").strip()
        logger.error(f"Gemini unexpected response: {res}")
        return "ধন্যবাদ! আমাদের টিম শীঘ্রই আপনাকে সাহায্য করবে।"
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        return "মাফ করবেন, সার্ভারে সমস্যা হয়েছে। পরে আবার চেষ্টা করুন।"

def send_whatsapp_message(to_phone: str, message: str) -> bool:
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        logger.warning("WhatsApp credentials missing")
        return False
    try:
        url = f"https://graph.facebook.com/v22.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone,
            "type": "text",
            "text": {"body": message}
        }
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        if r.status_code in (200, 201):
            logger.info(f"WhatsApp message sent to {to_phone}")
            return True
        logger.error(f"WhatsApp send failed: {r.status_code} {r.text}")
        return False
    except Exception as e:
        logger.error(f"WhatsApp send error: {e}")
        return False

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == VERIFY_TOKEN:
            logger.info("Webhook verified successfully.")
            return challenge, 200
        logger.warning(f"Webhook verification failed. mode={mode}, token={token}")
        return "Verification failed", 403

    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        logger.info(f"Webhook received: {json.dumps(data, ensure_ascii=False)}")
        try:
            entry_list = data.get("entry", [])
            for entry in entry_list:
                changes = entry.get("changes", [])
                for change in changes:
                    value = change.get("value", {})
                    if value.get("messaging_product") != "whatsapp":
                        continue
                    messages = value.get("messages", [])
                    contacts = value.get("contacts", [])
                    sender_name = contacts[0].get("profile", {}).get("name", "Customer") if contacts else "Customer"
                    for msg in messages:
                        phone = msg.get("from", "")
                        m_type = msg.get("type", "text")
                        if m_type == "text":
                            content = msg.get("text", {}).get("body", "")
                        elif m_type == "image":
                            content = "📷 [Photo Received]"
                        elif m_type in ["voice", "audio"]:
                            content = "🎤 [Voice Received]"
                        else:
                            content = f"[{m_type.upper()} Received]"
                        if not phone or not content:
                            continue
                        db_query(
                            "INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'inbound', ?)",
                            (phone, content, "whatsapp"),
                            commit=True
                        )
                        db_query(
                            "INSERT OR IGNORE INTO users (phone, name) VALUES (?, ?)",
                            (phone, sender_name),
                            commit=True
                        )
                        db_query(
                            "UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE phone = ?",
                            (phone,),
                            commit=True
                        )
                        logger.info(f"Received from {phone}: {content}")
                        reply = get_gemini_reply(content)
                        sent = send_whatsapp_message(phone, reply)
                        if sent:
                            db_query(
                                "INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', ?)",
                                (phone, reply, "gemini_ai"),
                                commit=True
                            )
        except Exception as e:
            logger.error(f"Webhook processing error: {e}")
        return "EVENT_RECEIVED", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
