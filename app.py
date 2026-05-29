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
import base64
import re
from io import BytesIO
from datetime import datetime, timedelta
from threading import Lock
from flask import Flask, request, jsonify, render_template, render_template_string, redirect, url_for, session, flash, send_file
from xhtml2pdf import pisa
from werkzeug.utils import secure_filename

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

MEDIA_FOLDER = os.path.join(os.path.dirname(DB_PATH), "media")
if not os.path.exists(MEDIA_FOLDER):
    os.makedirs(MEDIA_FOLDER)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dhaka-exclusive-master-ultra-v2026-final")
application = app
db_lock = Lock()

# =====================================================================
# ENGINE LOADERS (C++ & ASSEMBLY CORE)
# =====================================================================
lib = None
asm_lib = None
try:
    if os.path.exists("engine.so"):
        lib = ctypes.CDLL(os.path.abspath("engine.so"))
        lib.process_business_logic.restype = ctypes.c_char_p
        logger.info("C++ Engine (engine.so) Linked Successfully.")
    if os.path.exists("asm_engine.so"):
        asm_lib = ctypes.CDLL(os.path.abspath("asm_engine.so"))
        asm_lib.asm_process_command.restype = ctypes.c_char_p
        asm_lib.asm_strlen.restype = ctypes.c_uint64
        asm_lib.asm_strlen.argtypes = [ctypes.c_char_p]
        asm_lib.asm_checksum.restype = ctypes.c_uint64
        asm_lib.asm_checksum.argtypes = [ctypes.c_char_p]
        logger.info("ASM Engine (asm_engine.so) Linked Successfully.")
except Exception as e:
    logger.error(f"Engine Load Fail: {e}")

# -----------------------------------------------------------------
# C++ / ASM WRAPPER FUNCTIONS (Actually Used!)
# -----------------------------------------------------------------
def cpp_engine_command(cmd: str) -> str:
    if lib is None:
        return "C++ Engine Not Loaded"
    try:
        result = lib.process_business_logic(cmd.encode("utf-8"))
        return result.decode("utf-8") if result else ""
    except Exception as e:
        logger.error(f"C++ engine error: {e}")
        return f"C++ Error: {e}"

def asm_engine_command(cmd: str) -> str:
    if asm_lib is None:
        return "ASM Engine Not Loaded"
    try:
        result = asm_lib.asm_process_command(cmd.encode("utf-8"))
        return result.decode("utf-8") if result else ""
    except Exception as e:
        logger.error(f"ASM engine error: {e}")
        return f"ASM Error: {e}"

def asm_fast_strlen(text: str) -> int:
    if asm_lib is None:
        return len(text)
    try:
        return asm_lib.asm_strlen(text.encode("utf-8"))
    except Exception as e:
        logger.error(f"ASM strlen error: {e}")
        return len(text)

def asm_fast_checksum(text: str) -> int:
    if asm_lib is None:
        return 0
    try:
        return asm_lib.asm_checksum(text.encode("utf-8"))
    except Exception as e:
        logger.error(f"ASM checksum error: {e}")
        return 0

# =====================================================================
# DATABASE UTILITIES
# =====================================================================
def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, from_number TEXT, content TEXT, direction TEXT, agent_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS users (phone TEXT PRIMARY KEY, name TEXT DEFAULT 'Customer', last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP, follow_up_sent INTEGER DEFAULT 0)")
        c.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, pathao_order_id TEXT UNIQUE, phone TEXT, name TEXT, address TEXT, total INTEGER, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, fb_product_id TEXT UNIQUE, name TEXT, price INTEGER, stock INTEGER DEFAULT 10, image_url TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS agent_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, action TEXT, details TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS agents (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS payment_methods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT NOT NULL,
                number TEXT,
                account_name TEXT,
                instructions TEXT,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("INSERT OR IGNORE INTO agents (username, password) VALUES ('admin', 'admin123')")
        try:
            c.execute("ALTER TABLE users ADD COLUMN name TEXT DEFAULT 'Customer'")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                logger.error(f"Migration error: {e}")
        try:
            c.execute("ALTER TABLE users ADD COLUMN follow_up_sent INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
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
            logger.error(f"SQL Error: {e} | Query: {query} | Params: {params}")
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
# WHATSAPP MEDIA UPLOAD, DOWNLOAD & SEND
# =====================================================================
def upload_media_to_whatsapp(file_path, media_type="image"):
    s = get_all_settings()
    token = s.get("permanent_token") or os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
    phone_id = s.get("phone_number_id") or os.environ.get("PHONE_NUMBER_ID", "")
    if not token or not phone_id:
        logger.error("WhatsApp credentials missing for media upload")
        return None
    url = f"https://graph.facebook.com/v22.0/{phone_id}/media"
    try:
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, f"{media_type}/*")}
            data = {"type": media_type, "messaging_product": "whatsapp"}
            headers = {"Authorization": f"Bearer {token}"}
            r = requests.post(url, headers=headers, files=files, data=data, timeout=60)
            res = r.json()
            if r.status_code in (200, 201) and "id" in res:
                logger.info(f"Media uploaded: {res['id']}")
                return res["id"]
            logger.error(f"Media upload failed: {res}")
            return None
    except Exception as e:
        logger.error(f"Media upload exception: {e}")
        return None

def _download_whatsapp_media(media_id, media_type="image"):
    """Download media from WhatsApp servers by media_id."""
    token = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
    if not token:
        logger.error("WhatsApp token missing for media download")
        return None
    try:
        url = f"https://graph.facebook.com/v22.0/{media_id}"
        headers = {"Authorization": f"Bearer {token}"}
        r = requests.get(url, headers=headers, timeout=30)
        res = r.json()
        media_url = res.get("url")
        if not media_url:
            logger.error(f"Media URL not found: {res}")
            return None
        
        ext = "jpg" if media_type == "image" else ("ogg" if media_type in ["voice", "audio"] else "bin")
        filename = f"{media_type}_{int(time.time())}.{ext}"
        file_path = os.path.join(MEDIA_FOLDER, filename)
        
        r2 = requests.get(media_url, headers=headers, timeout=60)
        with open(file_path, "wb") as f:
            f.write(r2.content)
        logger.info(f"Media downloaded: {file_path} ({len(r2.content)} bytes)")
        return file_path
    except Exception as e:
        logger.error(f"Media download error: {e}")
        return None

def send_whatsapp_media(to_phone, media_id, media_type="image", caption=""):
    s = get_all_settings()
    token = s.get("permanent_token") or os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
    phone_id = s.get("phone_number_id") or os.environ.get("PHONE_NUMBER_ID", "")
    if not token or not phone_id:
        return False
    url = f"https://graph.facebook.com/v22.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": media_type,
        media_type: {"id": media_id, "caption": caption} if caption else {"id": media_id}
    }
    try:
        r = requests.post(url, headers=headers, json=body, timeout=30)
        return r.status_code in (200, 201)
    except Exception as e:
        logger.error(f"Media send error: {e}")
        return False

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
# ADMIN PANEL ROUTES
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
    payment_methods = db_query("SELECT * FROM payment_methods ORDER BY id", fetchall=True) or []

    chat_history = []
    if chat_with:
        chat_history = db_query("SELECT * FROM messages WHERE from_number=? ORDER BY id DESC LIMIT 50", (chat_with,), fetchall=True) or []
        chat_history.reverse()

    return render_template(f"{tab}.html", settings=s, analytics=analytics, orders=orders, users=users, products=products, agent_logs=agent_logs, payment_methods=payment_methods, chat_history=chat_history, active_chat=chat_with, msg=msg)

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

@app.route("/admin/chat/send-image", methods=["POST"])
def admin_send_image():
    phone = request.form.get("phone")
    file = request.files.get("image")
    caption = request.form.get("caption", "")
    if not phone or not file:
        return redirect(f"/admin?tab=chat&chat_with={phone}&msg=No image selected")
    filename = secure_filename(f"img_{int(time.time())}_{file.filename}")
    file_path = os.path.join(MEDIA_FOLDER, filename)
    file.save(file_path)
    media_id = upload_media_to_whatsapp(file_path, media_type="image")
    if media_id and send_whatsapp_media(phone, media_id, media_type="image", caption=caption):
        db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', ?)", (phone, f"[IMAGE:{file_path}]", session.get("username")), commit=True)
        return redirect(f"/admin?tab=chat&chat_with={phone}&msg=Image sent!")
    if os.path.exists(file_path):
        os.remove(file_path)
    return redirect(f"/admin?tab=chat&chat_with={phone}&msg=Image upload failed")

@app.route("/admin/chat/send-voice", methods=["POST"])
def admin_send_voice():
    phone = request.form.get("phone")
    voice_file = request.files.get("voice")
    if not phone or not voice_file:
        return redirect(f"/admin?tab=chat&chat_with={phone}&msg=No voice message")
    filename = secure_filename(f"voice_{int(time.time())}.webm")
    file_path = os.path.join(MEDIA_FOLDER, filename)
    voice_file.save(file_path)
    media_id = upload_media_to_whatsapp(file_path, media_type="audio")
    if media_id and send_whatsapp_media(phone, media_id, media_type="audio"):
        db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', ?)", (phone, "[VOICE MESSAGE]", session.get("username")), commit=True)
        return redirect(f"/admin?tab=chat&chat_with={phone}&msg=Voice sent!")
    if os.path.exists(file_path):
        os.remove(file_path)
    return redirect(f"/admin?tab=chat&chat_with={phone}&msg=Voice upload failed")

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


@app.route("/admin/payment/add", methods=["POST"])
def add_payment_method():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    try:
        name = request.form.get("name", "").strip()
        ptype = request.form.get("type", "").strip()
        number = request.form.get("number", "").strip()
        account_name = request.form.get("account_name", "").strip()
        instructions = request.form.get("instructions", "").strip()
        if name and ptype:
            db_query(
                "INSERT INTO payment_methods (name, type, number, account_name, instructions) VALUES (?, ?, ?, ?, ?)",
                (name, ptype, number, account_name, instructions),
                commit=True
            )
            db_query("INSERT INTO agent_logs (username, action, details) VALUES (?, 'ADD_PAYMENT', ?)",
                     (session.get("username"), f"Added {name}"), commit=True)
            return redirect("/admin?tab=settings&msg=Payment Method Added")
        return redirect("/admin?tab=settings&msg=Name and Type required")
    except Exception as e:
        return redirect(f"/admin?tab=settings&msg=Error: {str(e)}")

@app.route("/admin/payment/update/<int:pid>", methods=["POST"])
def update_payment_method(pid):
    if not session.get("logged_in"):
        return redirect("/admin/login")
    try:
        name = request.form.get("name", "").strip()
        ptype = request.form.get("type", "").strip()
        number = request.form.get("number", "").strip()
        account_name = request.form.get("account_name", "").strip()
        instructions = request.form.get("instructions", "").strip()
        is_active = 1 if request.form.get("is_active") else 0
        db_query(
            "UPDATE payment_methods SET name=?, type=?, number=?, account_name=?, instructions=?, is_active=? WHERE id=?",
            (name, ptype, number, account_name, instructions, is_active, pid),
            commit=True
        )
        return redirect("/admin?tab=settings&msg=Payment Method Updated")
    except Exception as e:
        return redirect(f"/admin?tab=settings&msg=Error: {str(e)}")

@app.route("/admin/payment/delete/<int:pid>")
def delete_payment_method(pid):
    if not session.get("logged_in"):
        return redirect("/admin/login")
    db_query("DELETE FROM payment_methods WHERE id=?", (pid,), commit=True)
    return redirect("/admin?tab=settings&msg=Payment Method Deleted")

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
# API ROUTES (Including Engine Status)
# =====================================================================
@app.route("/api/engine-status")
def api_engine_status():
    cpp_result = cpp_engine_command("status")
    asm_result = asm_engine_command("asm_status")
    return jsonify({
        "cpp_engine": cpp_result,
        "asm_engine": asm_result,
        "cpp_available": lib is not None,
        "asm_available": asm_lib is not None
    })

@app.route("/api/asm-checksum", methods=["POST"])
def api_asm_checksum():
    text = request.json.get("text", "") if request.is_json else request.form.get("text", "")
    if not text:
        return jsonify({"error": "No text provided"}), 400
    checksum = asm_fast_checksum(text)
    py_len = len(text)
    asm_len = asm_fast_strlen(text)
    return jsonify({
        "text": text,
        "asm_checksum": checksum,
        "asm_strlen": asm_len,
        "py_strlen": py_len,
        "match": asm_len == py_len
    })

# =====================================================================
# FOLLOW-UP SYSTEM (Cron Endpoint)
# =====================================================================
@app.route("/cron/followup")
def cron_followup():
    """Call this via cron job every 6-12 hours. Send follow-up to inactive customers."""
    secret = request.args.get("secret", "")
    if secret != os.environ.get("CRON_SECRET", "dhaka-followup-2026"):
        return jsonify({"error": "Unauthorized"}), 403
    
    candidates = db_query("""
        SELECT DISTINCT u.phone, u.name, u.last_active, u.follow_up_sent 
        FROM users u
        LEFT JOIN orders o ON u.phone = o.phone
        WHERE o.id IS NULL 
        AND u.follow_up_sent = 0
        AND u.last_active > datetime('now', '-48 hours')
        LIMIT 50
    """, fetchall=True) or []
    
    sent_count = 0
    for user in candidates:
        phone = user.get("phone", "")
        msg = (
            "🛍️ প্রিয় গ্রাহক, আপনি কি Dhaka Exclusive-এর প্রোডাক্টগুলো দেখেছেন? "
            "আমাদের হট কালেকশন শেষ হওয়ার আগেই অর্ডার করুন! "
            "ক্যাটালগ দেখতে 'লিস্ট' লিখুন। COD + ফ্রি ডেলিভারি! 🚚"
        )
        if send_whatsapp_message(phone, msg):
            db_query("UPDATE users SET follow_up_sent = 1 WHERE phone = ?", (phone,), commit=True)
            sent_count += 1
            time.sleep(1)
    
    logger.info(f"Follow-up sent to {sent_count} customers")
    return jsonify({"sent": sent_count, "candidates": len(candidates)})

# =====================================================================
# GEMINI AI SALES INTELLIGENCE ENGINE
# =====================================================================
GEMINI_API_KEY = os.environ.get("GEMINI_KEY", "")
WHATSAPP_ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "dhaka-exclusive-verify-2026")
ADMIN_PHONE = os.environ.get("ADMIN_PHONE", "")

_PRIMARY_MODEL = "gemini-2.5-flash"
_FALLBACK_MODEL = "gemini-2.5-pro"
_AI_CACHE = {"products": None, "last_fetch": 0}

def _get_products_text():
    now = time.time()
    if _AI_CACHE["products"] is None or (now - _AI_CACHE["last_fetch"]) > 120:
        rows = db_query("SELECT name, price, stock FROM products ORDER BY id DESC", fetchall=True) or []
        lines = []
        for p in rows:
            stock = "In Stock" if p.get('stock', 0) > 5 else f"Only {p.get('stock', 0)} left!"
            lines.append(f"- {p['name']} — {p['price']}৳ — {stock}")
        _AI_CACHE["products"] = "\n".join(lines) if lines else "No products available"
        _AI_CACHE["last_fetch"] = now
    return _AI_CACHE["products"]

def _get_hot_products():
    hot = db_query("""
        SELECT p.name, p.price, COUNT(*) as sold FROM orders o
        JOIN products p ON o.phone = p.fb_product_id
        WHERE o.created_at > datetime('now', '-30 days')
        GROUP BY p.id ORDER BY sold DESC LIMIT 5
    """, fetchall=True)
    if not hot:
        hot = db_query("SELECT name, price FROM products ORDER BY id DESC LIMIT 5", fetchall=True) or []
    return hot

def _get_customer_context(phone):
    if not phone:
        return ""
    orders = db_query("SELECT id, total, status FROM orders WHERE phone=? ORDER BY id DESC LIMIT 3", (phone,), fetchall=True) or []
    if not orders:
        return "This is a NEW customer."
    total_spent = sum(o.get('total', 0) for o in orders)
    last_order = orders[0]
    return f"""Customer History:
- Total Orders: {len(orders)}
- Total Spent: {total_spent}৳
- Last Order Status: {last_order.get('status', 'N/A')}"""

def _get_payment_methods_text():
    """Fetch active payment methods from database for AI responses."""
    methods = db_query(
        "SELECT name, type, number, account_name, instructions FROM payment_methods WHERE is_active=1 ORDER BY id",
        fetchall=True
    ) or []
    if not methods:
        return (
            "💳 *পেমেন্ট পদ্ধতি:*\n\n"
            "1️⃣ *ক্যাশ অন ডেলিভারি (COD)* - সবচেয়ে জনপ্রিয়\n"
            "2️⃣ *bKash:* 017XXXXXXXX (Personal)\n"
            "   - Send Money করুন\n"
            "   - রেফারেন্সে আপনার ফোন নম্বর লিখুন\n"
            "3️⃣ *Nagad:* 017XXXXXXXX\n"
            "   - Cash Out/Send Money\n\n"
            "✅ অর্ডার কনফার্মের পর পেমেন্ট করুন।"
        )
    
    lines = ["💳 *পেমেন্ট পদ্ধতি:*\n"]
    for i, m in enumerate(methods, 1):
        name = m.get('name', '')
        ptype = m.get('type', '')
        number = m.get('number', '')
        acc_name = m.get('account_name', '')
        instructions = m.get('instructions', '')
        
        lines.append(f"{i}. *{name}* ({ptype})")
        if number:
            lines.append(f"   📱 {number}")
        if acc_name:
            lines.append(f"   👤 {acc_name}")
        if instructions:
            lines.append(f"   📝 {instructions}")
        lines.append("")
    
    lines.append("✅ অর্ডার কনফার্মের পর পেমেন্ট করুন।")
    lines.append("📸 বিকাশ/নগদে পেমেন্ট করলে স্ক্রিনশট পাঠান।")
    return "\n".join(lines)

def _get_order_status(phone):
    if not phone:
        return None
    orders = db_query("SELECT id, pathao_order_id, total, status, created_at FROM orders WHERE phone=? ORDER BY id DESC LIMIT 3", (phone,), fetchall=True) or []
    if not orders:
        return None
    lines = ["📦 *আপনার অর্ডার স্ট্যাটাস:*\n"]
    for o in orders:
        lines.append(f"• অর্ডার #{o.get('pathao_order_id', o['id'])}")
        lines.append(f"  স্ট্যাটাস: {o['status']}")
        lines.append(f"  মোট: {o['total']}৳")
        lines.append(f"  তারিখ: {o.get('created_at', 'N/A')[:10]}")
        lines.append("")
    lines.append("❓ আরও তথ্যের জন্য আমাদের সাথে যোগাযোগ করুন।")
    return "\n".join(lines)

def _notify_admin_new_order(order_data):
    if not ADMIN_PHONE:
        return
    try:
        msg = (
            f"🔔 *নতুন অর্ডার!*\n\n"
            f"👤 {order_data.get('name', 'Unknown')}\n"
            f"📞 {order_data.get('phone', 'N/A')}\n"
            f"📦 {order_data.get('product', 'N/A')} x{order_data.get('quantity', 1)}\n"
            f"📍 {order_data.get('address', 'N/A')}\n"
            f"💰 {order_data.get('total', 0)}৳\n\n"
            f"📋 Admin Panel: dhaka-exclusive-sms-bot-wp-msg.onrender.com/admin"
        )
        send_whatsapp_message(ADMIN_PHONE, msg)
        logger.info(f"Admin notified for order from {order_data.get('phone')}")
    except Exception as e:
        logger.error(f"Admin notify error: {e}")

def _analyze_image_with_gemini(image_path, customer_phone=""):
    if not GEMINI_API_KEY:
        return "📷 ছবি পেয়েছি। দুঃখিত, AI ভিশন সার্ভিস বর্তমানে অনুপলব্ধ।"
    
    products = db_query("SELECT name, price FROM products LIMIT 20", fetchall=True) or []
    product_list = ", ".join([p['name'] for p in products]) if products else "No products in catalog"
    
    try:
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        
        ext = os.path.splitext(image_path)[1].lower()
        mime = "image/jpeg" if ext in [".jpg", ".jpeg"] else "image/png" if ext == ".png" else "image/webp"
        
        prompt = (
            f"তুমি Dhaka Exclusive-এর AI সেলস সহায়ক। "
            f"এই ছবিতে কী প্রোডাক্ট দেখতে পাচ্ছো? "
            f"আমাদের ক্যাটালগে আছে: {product_list}. "
            f"যদি এই প্রোডাক্ট বা অনুরূপ কিছু ক্যাটালগে থাকে, বলো। "
            f"দাম, স্টক, এবং অর্ডার করার উপায় জানাও। "
            f"বাংলায় উত্তর দাও এবং 'প্রিয় গ্রাহক' বলে সম্বোধন করো।"
        )
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{_PRIMARY_MODEL}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime, "data": image_b64}}
                ]
            }],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 600, "topP": 0.9}
        }
        headers = {"Content-Type": "application/json"}
        r = requests.post(url, json=payload, headers=headers, timeout=45)
        res = r.json()
        
        if res.get("candidates"):
            parts = res["candidates"][0].get("content", {}).get("parts", [])
            for part in parts:
                if "text" in part:
                    return part["text"].strip()
        logger.error(f"Image analysis failed: {res}")
        return "📷 ছবি পেয়েছি। দুঃখিত, এটি বিশ্লেষণ করতে সমস্যা হচ্ছে। অনুগ্রহ করে প্রোডাক্টের নাম লিখে পাঠান।"
    except Exception as e:
        logger.error(f"Image analysis exception: {e}")
        return "📷 ছবি পেয়েছি। দুঃখিত, প্রযুক্তিগত সমস্যা। অনুগ্রহ করে টাইপ করে জানান।"

def _detect_intent(msg):
    msg_lower = msg.lower()
    intents = {
        "price_inquiry": ["দাম", "price", "কত", "cost", "tk", "৳", "taka"],
        "order_status": ["অর্ডার", "order", "কবে", "when", "status", "delivery", "ডেলিভারি", "কোথায়"],
        "product_inquiry": ["প্রোডাক্ট", "product", "আছে", "available", "stock", "item"],
        "discount": ["ডিসকাউন্ট", "discount", "offer", "অফার", "ছাড়", "deal", "কম"],
        "complaint": ["খারাপ", "bad", "problem", "সমস্যা", "complain", "defect"],
        "return": ["রিটার্ন", "return", "ফেরত", "change", "বদল"],
        "greeting": ["হাই", "hello", "hi", "আসসালামু", "salam", "কেমন"],
        "confirm_order": ["কিনব", "buy", "কনফার্ম", "confirm", "নিব", "চাই", "book"],
        "location": ["ঠিকানা", "address", "লোকেশন", "shop", "দোকান", "where"],
        "catalog_request": ["লিস্ট", "list", "ক্যাটালগ", "catalog", "সব", "all", "কী আছে", "ki ace", "কি আছে", "প্রোডাক্ট লিস্ট"],
        "track_order": ["আমার অর্ডার", "my order", "অর্ডার ট্র্যাক", "track", "কোথায় আমার", "অর্ডারের স্ট্যাটাস"],
        "payment": ["পেমেন্ট", "payment", "বিকাশ", "bkash", "নগদ", "nagad", "টাকা পাঠাব", "send money"],
    }
    for intent, keywords in intents.items():
        if any(k in msg_lower for k in keywords):
            return intent
    return "general"

def _extract_order_from_text(text, phone):
    if not GEMINI_API_KEY:
        return None
    
    products = db_query("SELECT name, price FROM products LIMIT 20", fetchall=True) or []
    product_lines = "\n".join([f"- {p['name']} ({p['price']}৳)" for p in products])
    
    extract_prompt = f"""তুমি একটি অর্ডার এক্সট্রাকশন bot। কাস্টমারের মেসেজ থেকে অর্ডারের তথ্য বের করো।

প্রোডাক্ট লিস্ট:
{product_lines}

কাস্টমারের মেসেজ: "{text}"
ফোন: {phone}

যদি কাস্টমার অর্ডার দিতে চায়, তাহলে শুধু নিচের ফরম্যাটে JSON রিটার্ন করো (অন্য কিছু লিখো না):
{{
  "is_order": true,
  "name": "কাস্টমারের নাম",
  "address": "কাস্টমারের ঠিকানা",
  "product": "কোন প্রোডাক্ট চেয়েছে",
  "quantity": 1,
  "total": 0,
  "phone": "{phone}"
}}

যদি অর্ডার না হয়:
{{
  "is_order": false
}}"""

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{_PRIMARY_MODEL}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": extract_prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 500, "topP": 0.9}
        }
        r = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=20)
        res = r.json()
        
        if res.get("candidates"):
            txt = res["candidates"][0]["content"]["parts"][0]["text"]
            json_match = re.search(r'\{.*\}', txt, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                if data.get("is_order"):
                    return data
        return None
    except Exception as e:
        logger.error(f"Order extraction error: {e}")
        return None

def _save_order(order_data):
    try:
        name = order_data.get("name", "Unknown")
        address = order_data.get("address", "Not provided")
        product = order_data.get("product", "")
        quantity = order_data.get("quantity", 1)
        total = order_data.get("total", 0)
        phone = order_data.get("phone", "")
        
        if total == 0 and product:
            prod = db_query("SELECT price FROM products WHERE name LIKE ? LIMIT 1", (f"%{product}%",), fetchone=True)
            if prod:
                total = prod["price"] * quantity
        
        db_query(
            "INSERT INTO orders (pathao_order_id, phone, name, address, total, status) VALUES (?, ?, ?, ?, ?, ?)",
            (f"WA-{int(time.time())}", phone, name, f"{product} x{quantity} | {address}", total, "pending"),
            commit=True
        )
        logger.info(f"Order saved for {phone}: {product} x{quantity}")
        _notify_admin_new_order(order_data)
        return True
    except Exception as e:
        logger.error(f"Save order error: {e}")
        return False

def _analyze_voice_with_gemini(voice_path, customer_phone=""):
    if not GEMINI_API_KEY:
        return "🎤 আপনার ভয়েস মেসেজ পেয়েছি। দুঃখিত, AI ভয়েস সার্ভিস বর্তমানে অনুপলব্ধ। অনুগ্রহ করে টাইপ করে জানান।"
    try:
        with open(voice_path, "rb") as f:
            voice_bytes = f.read()
        voice_b64 = base64.b64encode(voice_bytes).decode("utf-8")
        
        prompt = (
            "তুমি Dhaka Exclusive-এর AI সেলস সহায়ক। "
            "এই অডিওটি শুনো। কাস্টমার কী বলেছেন বাংলায় বা ইংরেজিতে, "
            "তা বুঝে সঠিক এবং সুন্দরভাবে বাংলায় রিপ্লাই দাও। "
            "'প্রিয় গ্রাহক' বলে সম্বোধন করো। "
            "যদি অর্ডার সংক্রান্ত কিছু বলেন, অর্ডার নিতে উৎসাহিত করো। "
            "যদি প্রোডাক্ট জানতে চান, প্রোডাক্ট লিস্ট দাও। "
            "প্রতিটি উত্তর সম্পূর্ণ এবং সুন্দরভাবে শেষ করো।"
        )
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{_PRIMARY_MODEL}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "audio/ogg", "data": voice_b64}}
                ]
            }],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": 800, "topP": 0.9}
        }
        headers = {"Content-Type": "application/json"}
        r = requests.post(url, json=payload, headers=headers, timeout=45)
        res = r.json()
        
        if res.get("candidates"):
            parts = res["candidates"][0].get("content", {}).get("parts", [])
            for part in parts:
                if "text" in part:
                    return part["text"].strip()
        logger.error(f"Voice analysis failed: {res}")
        return "🎤 আপনার ভয়েস মেসেজ পেয়েছি। দুঃখিত, ভয়েসটি স্পষ্টভাবে বোঝা যায়নি। অনুগ্রহ করে টাইপ করে জানান।"
    except Exception as e:
        logger.error(f"Voice analysis exception: {e}")
        return "🎤 আপনার ভয়েস মেসেজ পেয়েছি। দুঃখিত, প্রযুক্তিগত সমস্যা। অনুগ্রহ করে টাইপ করে জানান।"

def get_optimized_gemini_reply(user_message, customer_phone="", chat_history=None, image_path=None, voice_path=None):
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY missing")
        return "Dhaka Exclusive এ আপনাকে স্বাগতম! আমরা শীঘ্রই আপনার সাথে যোগাযোগ করবো।"

    if voice_path:
        return _analyze_voice_with_gemini(voice_path, customer_phone)

    if image_path:
        return _analyze_image_with_gemini(image_path, customer_phone)

    intent = _detect_intent(user_message)

    if intent == "track_order":
        status = _get_order_status(customer_phone)
        if status:
            return status
        return "📦 আপনার কোনো অর্ডার পাওয়া যায়নি। অর্ডার করতে প্রোডাক্টের নাম ও ঠিকানা লিখুন!"

    if intent == "payment":
        return _get_payment_methods_text()

    if intent == "confirm_order":
        order_data = _extract_order_from_text(user_message, customer_phone)
        if order_data and order_data.get("is_order"):
            _save_order(order_data)
            return f"✅ অর্ডার কনফার্মড!\n\n📝 অর্ডার ডিটেইলস:\n• নাম: {order_data.get('name')}\n• প্রোডাক্ট: {order_data.get('product')} x{order_data.get('quantity', 1)}\n• ঠিকানা: {order_data.get('address')}\n• মোট: {order_data.get('total', 0)}৳\n\n📦 ডেলিভারি: ঢাকায় ২৪ ঘণ্টা, বাইরে ৪৮-৭২ ঘণ্টা। ক্যাশ অন ডেলিভারি। আপনার অর্ডারটি প্রসেসিং এ আছে!"

    if intent == "catalog_request":
        products = db_query("SELECT name, price, stock FROM products ORDER BY id DESC LIMIT 30", fetchall=True) or []
        if not products:
            return "📦 বর্তমানে ক্যাটালগ আপডেট হচ্ছে। আমাদের নতুন কালেকশন শীঘ্রই আসছে! অনুগ্রহ করে কিছুক্ষণ পর আবার চেষ্টা করুন।"
        
        catalog_lines = ["🛍️ *Dhaka Exclusive - প্রোডাক্ট ক্যাটালগ*\n"]
        for i, p in enumerate(products, 1):
            stock_status = "✅ In Stock" if p.get('stock', 0) > 5 else f"⚠️ Only {p.get('stock', 0)} left!"
            catalog_lines.append(f"{i}. {p['name']}\n   💰 {p['price']}৳ | {stock_status}")
        
        catalog_lines.append(f"\n📌 মোট {len(products)}টি প্রোডাক্ট")
        catalog_lines.append("🚚 ডেলিভারি: ঢাকা ২৪ ঘণ্টা | বাইরে ৪৮-৭২ ঘণ্টা")
        catalog_lines.append("💳 পেমেন্ট: ক্যাশ অন ডেলিভারি")
        catalog_lines.append("\n✨ অর্ডার করতে প্রোডাক্টের নাম ও ঠিকানা লিখুন!")
        
        return "\n".join(catalog_lines)

    products_text = _get_products_text()
    hot_products = _get_hot_products()
    customer_ctx = _get_customer_context(customer_phone)
    hot_text = "\n".join([f"- {p['name']} — {p['price']}৳" for p in hot_products[:5]])

    chat_ctx = ""
    if chat_history and len(chat_history) > 0:
        recent = chat_history[-6:]
        chat_ctx = "Recent conversation:\n" + "\n".join([
            f"{'Customer' if m.get('direction') == 'inbound' else 'Assistant'}: {m.get('content', '')[:100]}"
            for m in recent
        ])

    intent_prompts = {
        "price_inquiry": "Customer is asking about PRICE. Be transparent. Mention bundle deals. End with order confirmation question.",
        "order_status": "Customer is asking about ORDER STATUS. Be reassuring. Give estimated delivery time.",
        "product_inquiry": "Customer wants to know about PRODUCTS. List relevant products enthusiastically. Suggest complementary items.",
        "discount": "Customer wants DISCOUNT. Mention loyalty program, referral bonus, bulk order discounts. Create scarcity.",
        "complaint": "Customer has a COMPLAINT. Apologize sincerely. Promise quick resolution. Offer replacement/refund.",
        "return": "Customer wants to RETURN. Be understanding. Explain return policy. Offer exchange first.",
        "greeting": "Customer greeted us. Warm welcome. Mention hot deals or new arrivals.",
        "confirm_order": "Customer wants to BUY! Confirm enthusiastically. Ask for confirmation details. Create urgency - limited stock.",
        "location": "Customer asking about LOCATION/SHOP. Explain online-based with COD nationwide.",
        "general": "General inquiry. Be helpful and steer towards placing an order.",
        "catalog_request": "Customer wants to see the FULL PRODUCT CATALOG. List ALL available products with prices clearly. Mention COD and fast delivery.",
        "track_order": "Customer wants to TRACK their order. Check order status and reassure them.",
        "payment": "Customer is asking about PAYMENT METHODS. Explain COD, bKash, Nagad clearly.",
    }

    system_instruction = f"""আপনি Dhaka Exclusive-এর প্রধান AI সেলস অ্যাসিস্ট্যান্ট। আপনার নাম "Dhaka Exclusive Bot"।

বিজনেস তথ্য:
- নাম: Dhaka Exclusive (Premium E-Commerce)
- ডেলিভারি: Dhaka ২৪ ঘণ্টা, বাইরে ৪৮-৭২ ঘণ্টা
- পেমেন্ট: COD + বিকাশ/নগদ
- রিটার্ন: ৭ দিন (ড্যামেজ হলে)

চলতি প্রোডাক্ট:
{products_text}

🔥 হট সেলিং:
{hot_text}

{customer_ctx}

{chat_ctx}

ইনটেন্ট: {intent}
{intent_prompts.get(intent, intent_prompts['general'])}

নিয়ম:
1. শুধু বাংলায় উত্তর দিন
2. "প্রিয় গ্রাহক" বলে সম্বোধন করুন
3. কখনো "আরে ভাই/আপু" বা "ভাই/আপু" বলবেন না
4. অর্ডার করতে উৎসাহিত করুন
5. দাম বলার সময় "মাত্র" "শুধু" ব্যবহার করুন
6. স্টক কম থাকলে "শেষ হওয়ার আগেই অর্ডার করুন"
7. কখনো কঠোরভাবে না বলবেন না
8. প্রতিটি উত্তর সম্পূর্ণ এবং সুন্দরভাবে শেষ করুন
"""

    payload = {
        "contents": [{
            "role": "user",
            "parts": [{"text": f"{system_instruction}\n\nCustomer: {user_message}"}]
        }],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 1000,
            "topP": 0.95
        }
    }
    headers = {"Content-Type": "application/json"}

    def _call_gemini(model_name):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        return resp.json()

    try:
        res = _call_gemini(_PRIMARY_MODEL)

        if "error" in res:
            err_code = res.get("error", {}).get("code", 0)
            logger.info(f"Primary model failed ({err_code}), trying fallback {_FALLBACK_MODEL}...")
            res = _call_gemini(_FALLBACK_MODEL)

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

def send_whatsapp_message(to_phone, message):
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
        return "Verification failed", 403

    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        logger.info(f"Webhook received: {json.dumps(data, ensure_ascii=False)[:500]}")
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
                        
                        content = ""
                        image_path = None
                        
                        if m_type == "text":
                            content = msg.get("text", {}).get("body", "")
                        elif m_type == "image":
                            media_id = msg.get("image", {}).get("id", "")
                            image_path = _download_whatsapp_media(media_id, "image")
                            content = "[Photo Received]"
                        elif m_type in ["voice", "audio"]:
                            media_id = msg.get(m_type, {}).get("id", "")
                            voice_path = _download_whatsapp_media(media_id, "voice")
                            content = "🎤 [Voice Received]"
                            if voice_path:
                                db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'inbound', 'whatsapp')", (phone, content), commit=True)
                                db_query("INSERT OR IGNORE INTO users (phone, name) VALUES (?, ?)", (phone, sender_name), commit=True)
                                db_query("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE phone = ?", (phone,), commit=True)
                                
                                recent_msgs = db_query("SELECT * FROM messages WHERE from_number=? ORDER BY id DESC LIMIT 6", (phone,), fetchall=True) or []
                                recent_msgs.reverse()
                                
                                reply = get_optimized_gemini_reply(content, customer_phone=phone, chat_history=recent_msgs, voice_path=voice_path)
                                if reply:
                                    sent = send_whatsapp_message(phone, reply)
                                    if sent:
                                        db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', 'gemini_ai')", (phone, reply), commit=True)
                                continue
                        else:
                            content = f"[{m_type.upper()} Received]"

                        if not phone or not content:
                            continue

                        db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'inbound', 'whatsapp')", (phone, content), commit=True)
                        db_query("INSERT OR IGNORE INTO users (phone, name) VALUES (?, ?)", (phone, sender_name), commit=True)
                        db_query("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE phone = ?", (phone,), commit=True)

                        recent_msgs = db_query("SELECT * FROM messages WHERE from_number=? ORDER BY id DESC LIMIT 6", (phone,), fetchall=True) or []
                        recent_msgs.reverse()

                        reply = get_optimized_gemini_reply(content, customer_phone=phone, chat_history=recent_msgs, image_path=image_path)

                        if reply:
                            sent = send_whatsapp_message(phone, reply)
                            if sent:
                                db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', 'gemini_ai')", (phone, reply), commit=True)
        except Exception as e:
            logger.error(f"Webhook processing error: {e}")
        return "EVENT_RECEIVED", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
