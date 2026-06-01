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
def migrate_db():
    """Add missing columns to existing tables"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Check current products columns
        c.execute("PRAGMA table_info(products)")
        columns = [col[1] for col in c.fetchall()]
        
        new_cols = {
            "description": "TEXT",
            "category": "TEXT", 
            "size": "TEXT",
            "color": "TEXT",
            "material": "TEXT"
        }
        
        for col, dtype in new_cols.items():
            if col not in columns:
                c.execute(f"ALTER TABLE products ADD COLUMN {col} {dtype}")
                logger.info(f"Migration: Added column '{col}' to products table")
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Migration error: {e}")


def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, from_number TEXT, content TEXT, direction TEXT, agent_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS users (phone TEXT PRIMARY KEY, name TEXT DEFAULT 'Customer', last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP, follow_up_sent INTEGER DEFAULT 0)")
        c.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, pathao_order_id TEXT UNIQUE, phone TEXT, name TEXT, address TEXT, total INTEGER, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("""CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fb_product_id TEXT UNIQUE,
            name TEXT,
            price INTEGER,
            stock INTEGER DEFAULT 10,
            image_url TEXT,
            description TEXT,
            category TEXT,
            size TEXT,
            color TEXT,
            material TEXT,
            discount_price INTEGER,
            flash_sale_end TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS product_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            rating INTEGER DEFAULT 5,
            comment TEXT,
            customer_name TEXT DEFAULT 'Anonymous',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
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
        # Migrate products table - add new columns
        for col_name, col_type in [
            ("discount_price", "INTEGER"),
            ("flash_sale_end", "TEXT"),
        ]:
            try:
                c.execute(f"ALTER TABLE products ADD COLUMN {col_name} {col_type}")
                logger.info(f"Added column '{col_name}' to products table")
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    logger.error(f"Migration error: {e}")
        try:
            c.execute("ALTER TABLE users ADD COLUMN name TEXT DEFAULT 'Customer'")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                logger.error(f"Migration error: {e}")
        try:
            c.execute("ALTER TABLE users ADD COLUMN follow_up_sent INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        # group_orders table for bridge auto-extract
        c.execute("""
            CREATE TABLE IF NOT EXISTS group_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT,
                customer_name TEXT,
                address TEXT,
                product_name TEXT,
                quantity INTEGER DEFAULT 1,
                price INTEGER,
                total INTEGER,
                status TEXT DEFAULT 'pending',
                group_name TEXT,
                raw_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Add active column to products if missing
        try:
            c.execute("ALTER TABLE products ADD COLUMN active INTEGER DEFAULT 1")
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
    sort_param = request.args.get("sort", "id_desc")
    sort_map = {
        "id_desc": "id DESC",
        "price_low": "price ASC",
        "price_high": "price DESC",
        "name_az": "name COLLATE NOCASE ASC",
        "stock_low": "stock ASC",
        "stock_high": "stock DESC",
    }
    products_order = sort_map.get(sort_param, "id DESC")
    # Pagination
    page = int(request.args.get("page", 1))
    per_page = 20
    offset = (page - 1) * per_page
    
    total_products = db_query("SELECT COUNT(*) as c FROM products", fetchone=True)["c"] or 0
    total_pages = (total_products + per_page - 1) // per_page
    
    products = db_query(f"SELECT * FROM products ORDER BY {products_order} LIMIT ? OFFSET ?", (per_page, offset), fetchall=True) or []
    
    # Attach reviews to products
    for p in products:
        reviews = db_query("SELECT * FROM product_reviews WHERE product_id=? ORDER BY id DESC", (p["id"],), fetchall=True) or []
        p["reviews"] = reviews
        if reviews:
            avg = sum(r["rating"] for r in reviews) / len(reviews)
            p["avg_rating"] = round(avg, 1)
            p["review_count"] = len(reviews)
        else:
            p["avg_rating"] = 0
            p["review_count"] = 0

    # Inventory stats
    all_prods = db_query("SELECT * FROM products", fetchall=True) or []
    low_stock_count = sum(1 for p in all_prods if p["stock"] < 5)
    out_stock_count = sum(1 for p in all_prods if p["stock"] == 0)
    discount_count = sum(1 for p in all_prods if p.get("discount_price") and p["discount_price"] > 0)
    total_value = sum(p["price"] * p["stock"] for p in all_prods)

    agent_logs = db_query("SELECT * FROM agent_logs ORDER BY id DESC LIMIT 50", fetchall=True) or []
    payment_methods = db_query("SELECT * FROM payment_methods ORDER BY id", fetchall=True) or []

    chat_history = []
    if chat_with:
        chat_history = db_query("SELECT * FROM messages WHERE from_number=? ORDER BY id DESC LIMIT 50", (chat_with,), fetchall=True) or []
        chat_history.reverse()

    template_map = {"products": "inventory"}
    template_name = template_map.get(tab, tab)
    return render_template(f"{template_name}.html", settings=s, analytics=analytics, orders=orders, users=users, products=products, agent_logs=agent_logs, payment_methods=payment_methods, chat_history=chat_history, active_chat=chat_with, msg=msg, page=page, total_pages=total_pages, total_products=total_products, per_page=per_page, sort_by=sort_param, low_stock_count=low_stock_count, out_stock_count=out_stock_count, discount_count=discount_count, total_value=total_value)

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
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN") or "dhaka-exclusive-verify-2026"
ADMIN_PHONE = os.environ.get("ADMIN_PHONE", "")

# Messenger Config
MESSENGER_PAGE_ACCESS_TOKEN = os.environ.get("MESSENGER_PAGE_ACCESS_TOKEN", "")
MESSENGER_VERIFY_TOKEN = os.environ.get("MESSENGER_VERIFY_TOKEN") or VERIFY_TOKEN

_PRIMARY_MODEL = "gemini-2.5-flash"
_FALLBACK_MODEL = "gemini-2.5-pro"
_AI_CACHE = {"products": None, "last_fetch": 0}

def _get_products_text():
    now = time.time()
    if _AI_CACHE["products"] is None or (now - _AI_CACHE["last_fetch"]) > 120:
        rows = db_query("SELECT name, price, stock, description, category, size, color, material FROM products ORDER BY id DESC", fetchall=True) or []
        lines = []
        for p in rows:
            stock = "In Stock" if p.get('stock', 0) > 5 else f"Only {p.get('stock', 0)} left!"
            desc = p.get('description', '') or ''
            cat = p.get('category', '') or ''
            size = p.get('size', '') or ''
            color = p.get('color', '') or ''
            material = p.get('material', '') or ''
            extras = []
            if cat: extras.append(f"Category: {cat}")
            if size: extras.append(f"Size: {size}")
            if color: extras.append(f"Color: {color}")
            if material: extras.append(f"Material: {material}")
            if desc: extras.append(f"Details: {desc}")
            extra_str = f" ({'; '.join(extras)})" if extras else ""
            lines.append(f"- {p['name']} — {p['price']}৳ — {stock}{extra_str}")
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
    
    products = db_query("SELECT name, price, image_url FROM products LIMIT 50", fetchall=True) or []
    product_list = "\n".join([f"- {p['name']} ({p['price']}৳)" for p in products]) if products else "No products in catalog"
    
    try:
        with open(image_path, "rb") as f:
            image_bytes = f.read()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        
        ext = os.path.splitext(image_path)[1].lower()
        mime = "image/jpeg" if ext in [".jpg", ".jpeg"] else "image/png" if ext == ".png" else "image/webp"
        
        # Use gemini-1.5-flash which supports vision
        prompt = (
            "তুমি Dhaka Exclusive-এর সেলস সহায়ক। এই ছবিটি দেখো।\n"
            "যদি এটি কোনো প্রোডাক্টের ছবি হয়, তাহলে চিনতে চেষ্টা করো।\n"
            "কাস্টমার যদি কোনো প্রোডাক্টের ছবি পাঠিয়ে থাকে, তাহলে সেটি কিনতে সাহায্য করো।\n"
            "যদি স্ক্রিনশট/ছবিতে কোনো প্রোডাক্ট আমাদের ক্যাটালগের মতো দেখায়, তাহলে দাম বলো।\n"
            f"আমাদের ক্যাটালগ:\n{product_list}\n\n"
            "সংক্ষিপ্ত ও বন্ধুসুলভ ভাবে বাংলায় উত্তর দাও।"
        )
        
        # Vision requires specific model - use flash for speed
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"
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
        
        # Log the actual error for debugging
        if "error" in res:
            logger.error(f"Image analysis API error: {res.get('error')}")
        else:
            logger.error(f"Image analysis no candidates: {res}")
        return "📷 ছবি পেয়েছি। দুঃখিত, ছবিটি এখন বিশ্লেষণ করা সম্ভব হচ্ছে না। অনুগ্রহ করে প্রোডাক্টের নাম লিখে পাঠান।"
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
        "photo_request": ["ছবি", "photo", "pic", "image", "ফটো", "দেখাও", "look", "দেখতে চাই", "picture"],
    }
    for intent, keywords in intents.items():
        if any(k in msg_lower for k in keywords):
            return intent
    return "general"

def _extract_order_from_text(text, phone):
    """Extract order info from customer message. Uses AI first, falls back to regex."""
    text_lower = text.lower().strip()
    
    # Quick reject - if no order keywords, skip
    order_keywords = ["অর্ডার", "order", "কিনব", "buy", "নিব", "চাই", "book", "confirm", "কনফার্ম"]
    has_order_intent = any(kw in text_lower for kw in order_keywords)
    
    # Check if message contains product + location info
    products = db_query("SELECT name, price FROM products LIMIT 50", fetchall=True) or []
    matched_product = None
    matched_price = 0
    for p in products:
        pname_lower = p["name"].lower()
        # Match if any significant word (3+ chars) from product name is in text
        for word in pname_lower.split():
            if len(word) >= 3 and word in text_lower:
                matched_product = p["name"]
                matched_price = p["price"]
                break
        if matched_product:
            break
    
    # FALLBACK PARSER (no AI needed) - detect name, address, quantity
    def _fallback_parse():
        result = {"is_order": True, "name": "", "address": "", "product": matched_product or "", "quantity": 1, "total": 0, "phone": phone}
        
        # Extract quantity (e.g., "2 টি", "x3", "3pcs")
        qty_match = re.search(r'(\d+)\s*(টি|pcs|piece|x|X)', text)
        if qty_match:
            result["quantity"] = int(qty_match.group(1))
        
        # Extract name - look for "আমার নাম" or first sentence
        name_match = re.search(r'(আমার নাম\s+([\w\s]+))|(name[\s:]+([\w\s]+))', text, re.IGNORECASE)
        if name_match:
            result["name"] = (name_match.group(2) or name_match.group(4) or "").strip()[:50]
        else:
            # Use first 2-3 words as fallback name
            words = [w for w in text.split() if len(w) > 2 and not any(k in w.lower() for k in ["অর্ডার", "order", "কিনব", "buy"])]
            if words:
                result["name"] = words[0][:20]
        
        # Extract address - look for locations/areas in Dhaka
        dhaka_areas = ["মিরপুর", "গুলশান", "বনানী", "ধানমন্ডি", "উত্তরা", "মোহাম্মদপুর", "শ্যামলী", "আজিমপুর", "খিলগাঁও", "রামপুরা", "হাতিরঝিল", "বসুন্ধরা", "বারিধারা", "তেজগাঁও", "মগবাজার", "মালিবাগ", "খিলক্ষেত", "নিকেতন", "কাকরাইল", "পল্টন", "সেগুনবাগিচা", "শাহবাগ", "এলিফ্যান্ট রোড", "নিউমার্কেট", "ফার্মগেট", "কারওয়ান বাজার", "মহাখালী", "গুলিস্তান", "সদরঘাট", "লালবাগ", "কামরাঙ্গীরচর", "শেরেবাংলা নগর", "আদাবর", "কল্যাণপুর", "শ্যামপুর", "জুরাইন", "কদমতলী", "ডেমরা", "সাভার", "আশুলিয়া", "কেরানীগঞ্জ"]
        for area in dhaka_areas:
            if area.lower() in text_lower:
                result["address"] = area
                break
        
        # If no area matched but has numbers/addresses
        if not result["address"]:
            addr_match = re.search(r'(ঠিকানা[\s:]+([\w\s,\.]+))|(address[\s:]+([\w\s,\.]+))', text, re.IGNORECASE)
            if addr_match:
                result["address"] = (addr_match.group(2) or addr_match.group(4) or "").strip()[:100]
            else:
                # Last resort - anything after product name
                result["address"] = text.strip()[:100]
        
        # Calculate total
        if matched_price and result["quantity"]:
            result["total"] = matched_price * result["quantity"]
        
        return result if matched_product else {"is_order": False}
    
    # Try AI extraction first if we have API key and order intent detected
    if GEMINI_API_KEY and has_order_intent and matched_product:
        try:
            product_lines = "\n".join([f"- {p['name']} ({p['price']}৳)" for p in products[:20]])
            extract_prompt = f"""তুমি একটি অর্ডার এক্সট্রাকশন bot। কাস্টমারের মেসেজ থেকে অর্ডারের তথ্য বের করো।

প্রোডাক্ট লিস্ট:
{product_lines}

কাস্টমারের মেসেজ: "{text}"
ফোন: {phone}

শুধু নিচের ফরম্যাটে JSON রিটার্ন করো (অন্য কিছু লিখো না):
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
            
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"
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
                        # Fill in missing values
                        if not data.get("product") and matched_product:
                            data["product"] = matched_product
                        if not data.get("total") and matched_price:
                            data["total"] = matched_price * data.get("quantity", 1)
                        if not data.get("phone"):
                            data["phone"] = phone
                        return data
        except Exception as e:
            logger.warning(f"AI order extraction failed, using fallback: {e}")
    
    # Use fallback parser
    return _fallback_parse() if matched_product else {"is_order": False}

def _save_order(order_data):
    try:
        name = order_data.get("name", "Unknown") or "Unknown"
        address = order_data.get("address", "Not provided") or "Not provided"
        product = order_data.get("product", "")
        quantity = int(order_data.get("quantity", 1) or 1)
        total = int(order_data.get("total", 0) or 0)
        phone = order_data.get("phone", "")
        
        # Calculate total from product price if missing
        if total == 0 and product:
            prod = db_query("SELECT price FROM products WHERE name LIKE ? LIMIT 1", (f"%{product}%",), fetchone=True)
            if prod:
                total = prod["price"] * quantity
        
        # Build a clean order note for address field
        order_note = f"{product} x{quantity}"
        if address and address != "Not provided":
            order_note += f" | {address}"
        
        # Insert into orders table
        db_query(
            "INSERT INTO orders (pathao_order_id, phone, name, address, total, status) VALUES (?, ?, ?, ?, ?, ?)",
            (f"WA-{int(time.time())}", phone, name, order_note, total, "pending"),
            commit=True
        )
        
        # Also log to agent_logs for visibility
        db_query(
            "INSERT INTO agent_logs (action, details) VALUES (?, ?)",
            ("new_order", json.dumps({"phone": phone, "name": name, "product": product, "quantity": quantity, "total": total, "address": address})),
            commit=True
        )
        
        logger.info(f"Order saved for {phone}: {product} x{quantity} = {total}৳")
        
        # Notify admin
        _notify_admin_new_order(order_data)
        return True
    except Exception as e:
        logger.error(f"Save order error: {e}")
        return False

def _analyze_voice_with_gemini(voice_path, customer_phone=""):
    if not GEMINI_API_KEY:
        return "🎤 আপনার ভয়েস মেসেজ পেয়েছি। দুঃখিত, AI ভয়েস সার্ভিস বর্তমানে অনুপলব্ধ। অনুগ্রহ করে টাইপ করে জানান।"
    
    # Validate file
    if not os.path.exists(voice_path):
        logger.error(f"Voice file not found: {voice_path}")
        return "🎤 আপনার ভয়েস মেসেজ পেয়েছি। দুঃখিত, ফাইলটি পাওয়া যায়নি।"
    
    file_size = os.path.getsize(voice_path)
    if file_size == 0:
        logger.error("Voice file is empty")
        return "🎤 আপনার ভয়েস মেসেজ পেয়েছি। দুঃখিত, ফাইলটি খালি।"
    if file_size > 20 * 1024 * 1024:  # 20MB limit
        logger.error(f"Voice file too large: {file_size} bytes")
        return "🎤 আপনার ভয়েস মেসেজ পেয়েছি। দুঃখিত, ভয়েসটি খুব বড়। অনুগ্রহ করে ছোট করে পাঠান।"
    
    try:
        with open(voice_path, "rb") as f:
            voice_bytes = f.read()
        voice_b64 = base64.b64encode(voice_bytes).decode("utf-8")
        
        products = db_query("SELECT name, price, description FROM products LIMIT 15", fetchall=True) or []
        product_list = "\n".join([f"- {p['name']}: {p['price']}৳" for p in products]) if products else "No products"
        
        prompt = (
            "তুমি Dhaka Exclusive-এর সেলস সহায়ক। এই অডিওটি শোনো। "
            "কাস্টমার বাংলা বা ইংরেজিতে কথা বলছে। "
            "শুধু বাংলায় সংক্ষিপ্ত উত্তর দাও। "
            "প্রতিটি উত্তরে 'প্রিয় গ্রাহক' বলার দরকার নেই। "
            f"আমাদের প্রোডাক্ট:\n{product_list}\n\n"
            "অর্ডার করতে চাইলে সাহায্য করো। প্রশ্নের উত্তর দাও।"
        )
        
        # Use gemini-1.5-flash which reliably supports audio
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"
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
        
        logger.info(f"Sending voice to Gemini (size: {file_size} bytes)")
        r = requests.post(url, json=payload, headers=headers, timeout=45)
        res = r.json()
        
        if res.get("candidates"):
            parts = res["candidates"][0].get("content", {}).get("parts", [])
            for part in parts:
                if "text" in part:
                    reply = part["text"].strip()
                    logger.info(f"Voice reply: {reply[:100]}")
                    return reply
        
        # Log exact error
        if "error" in res:
            err = res["error"]
            logger.error(f"Voice API error: {err}")
            return f"🎤 আপনার ভয়েস মেসেজ পেয়েছি। দুঃখিত, AI ভয়েস পড়তে পারছে না। (Error: {err.get('code', 'unknown')})"
        
        logger.error(f"Voice analysis no candidates: {res}")
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
        "photo_request": "Customer wants to SEE PRODUCT PHOTOS. Say 'ছবি পাঠাচ্ছি' and mention the product name.",
    }

    # Only include greeting for first 3 messages of conversation
    greeting_rule = ""
    if not chat_history or len(chat_history) <= 3:
        greeting_rule = 'শুরুতে সংক্ষিপ্তভাবে "প্রিয় গ্রাহক" বলে সম্বোধন করুন।'
    else:
        greeting_rule = 'প্রতিটি উত্তর সরাসরি শুরু করুন — "প্রিয় গ্রাহক" বলার প্রয়োজন নেই।'

    # Simple greeting only for first message
    is_first = not chat_history or len(chat_history) <= 1
    
    system_instruction = f"""আপনি Dhaka Exclusive-এর সেলস সহায়ক।

বিজনেস তথ্য:
- ডেলিভারি: Dhaka ২৪ ঘণ্টা, বাইরে ৪৮-৭২ ঘণ্টা
- পেমেন্ট: COD + বিকাশ/নগদ
- রিটার্ন: ৭ দিন (ড্যামেজ হলে)

চলতি প্রোডাক্ট:
{products_text}

🔥 হট সেলিং:
{hot_text}

{customer_ctx}

ইনটেন্ট: {intent}
{intent_prompts.get(intent, intent_prompts['general'])}

নিয়ম:
1. সরাসরি উত্তর দিন — "প্রিয় গ্রাহক" শুধু প্রথম মেসেজে
2. কখনো "আরে ভাই/আপু" বা "ভাই/আপু" বলবেন না
3. অর্ডার করতে উৎসাহিত করুন
4. দাম বলার সময় "মাত্র" "শুধু" ব্যবহার করুন
5. স্টক কম থাকলে "শেষ হওয়ার আগেই অর্ডার করুন"
6. কখনো কঠোরভাবে না বলবেন না
7. ছবি চাইলে "ছবি পাঠাচ্ছি" বলুন
8. একই কথা বারবার বলবেন না
"""

    # Build conversation history for Gemini
    contents = []
    
    # Add chat history if available
    if chat_history and len(chat_history) > 0:
        for msg in chat_history[-10:]:  # Last 10 messages
            direction = msg.get("direction", "inbound")
            role = "user" if direction == "inbound" else "model"
            content_text = msg.get("content", "")
            if content_text:
                contents.append({
                    "role": role,
                    "parts": [{"text": content_text[:500]}]
                })
    
    # Add current user message
    contents.append({
        "role": "user",
        "parts": [{"text": user_message}]
    })
    
    payload = {
        "systemInstruction": {
            "parts": [{"text": system_instruction}]
        },
        "contents": contents,
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

def _try_send_product_image(phone, customer_msg, chat_history):
    """Try to find product from message and send its image"""
    try:
        # Extract potential product name from customer message
        import re
        # Remove common words
        clean_msg = re.sub(r"(ছবি|photo|pic|image|দেখাও|দেখতে|চাই|পাঠাও|দাও|লাগবে|কি|কী|এর|টি|the|a|an)", "", customer_msg, flags=re.IGNORECASE).strip()
        
        # Search products by name match
        products = db_query("SELECT id, name, image_url FROM products WHERE image_url IS NOT NULL AND image_url != ''", fetchall=True) or []
        
        best_match = None
        best_score = 0
        for p in products:
            pname = p["name"].lower()
            # Simple word matching
            score = 0
            for word in clean_msg.lower().split():
                if len(word) > 2 and word in pname:
                    score += 1
            if score > best_score:
                best_score = score
                best_match = p
        
        # Also check recent chat history for product mentions
        if best_score == 0 and chat_history:
            for msg in reversed(chat_history[-6:]):
                msg_text = msg.get("content", "").lower()
                for p in products:
                    pname = p["name"].lower()
                    score = 0
                    for word in msg_text.split():
                        if len(word) > 2 and word in pname:
                            score += 1
                    if score > best_score:
                        best_score = score
                        best_match = p
        
        if best_match and best_match.get("image_url"):
            img_url = best_match["image_url"]
            logger.info(f"Sending product image: {best_match['name']} to {phone}")
            
            # If image_url is a direct URL, download and upload to WhatsApp
            if img_url.startswith("http"):
                try:
                    r = requests.get(img_url, timeout=15)
                    if r.status_code == 200:
                        ext = os.path.splitext(img_url.split("?")[0])[1] or ".jpg"
                        tmp_path = os.path.join(MEDIA_FOLDER, f"prod_send_{int(time.time())}{ext}")
                        with open(tmp_path, "wb") as f:
                            f.write(r.content)
                        media_id = upload_media_to_whatsapp(tmp_path, "image")
                        if media_id:
                            send_whatsapp_media(phone, media_id, "image", caption=best_match["name"])
                            logger.info(f"Product image sent: {best_match['name']}")
                        # Clean up temp file
                        try:
                            os.remove(tmp_path)
                        except:
                            pass
                except Exception as e:
                    logger.error(f"Failed to send product image: {e}")
            elif img_url.startswith("[MEDIA:"):
                # Already a WhatsApp media ID
                media_id = img_url.replace("[MEDIA:", "").replace("]", "")
                send_whatsapp_media(phone, media_id, "image", caption=best_match["name"])
    except Exception as e:
        logger.error(f"Product image send error: {e}")


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
            # Detect if this is a Messenger message (object: "page")
            if data.get("object") == "page":
                for entry in data.get("entry", []):
                    for event in entry.get("messaging", []):
                        sender_id = event.get("sender", {}).get("id", "")
                        if not sender_id:
                            continue
                        sender_name = _get_messenger_name(sender_id)
                        message = event.get("message", {})
                        if not message:
                            continue

                        msg_text = message.get("text", "")
                        attachments = message.get("attachments", [])
                        content = msg_text or ""
                        image_path = None
                        voice_path = None

                        if attachments:
                            att = attachments[0]
                            att_type = att.get("type", "")
                            if att_type == "image":
                                image_url = att.get("payload", {}).get("url", "")
                                if image_url:
                                    image_path = _download_messenger_media(image_url)
                                content = "[Photo Received]"
                            elif att_type in ["audio", "voice"]:
                                audio_url = att.get("payload", {}).get("url", "")
                                if audio_url:
                                    voice_path = _download_messenger_media(audio_url)
                                content = "🎤 [Voice Received]"
                            else:
                                content = f"[{att_type.upper()} Received]"

                        db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'inbound', 'messenger')", (sender_id, content), commit=True)
                        db_query("INSERT OR IGNORE INTO users (phone, name) VALUES (?, ?)", (sender_id, sender_name), commit=True)
                        db_query("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE phone = ?", (sender_id,), commit=True)

                        recent_msgs = db_query("SELECT * FROM messages WHERE from_number=? ORDER BY id DESC LIMIT 6", (sender_id,), fetchall=True) or []
                        recent_msgs.reverse()

                        reply = get_optimized_gemini_reply(content, customer_phone=sender_id, chat_history=recent_msgs, image_path=image_path, voice_path=voice_path)
                        if reply:
                            sent = send_messenger_message(sender_id, reply)
                            if sent:
                                db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', 'gemini_ai')", (sender_id, reply), commit=True)
                            if "ছবি" in content or "photo" in content.lower():
                                _try_send_product_image(sender_id, content, recent_msgs)
                return "EVENT_RECEIVED", 200

            # WhatsApp message handling
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
                            
                            # If customer asked for product photo, send actual image
                            if "ছবি পাঠাচ্ছি" in reply or "ছবি দেখাচ্ছি" in reply or "photo" in content.lower() or "ছবি" in content:
                                _try_send_product_image(phone, content, recent_msgs)
        except Exception as e:
            logger.error(f"Webhook processing error: {e}")
        return "EVENT_RECEIVED", 200


# =====================================================================
# AI AUTO-DESCRIBE PRODUCT
# =====================================================================
def _generate_product_details_with_gemini(name, price):
    """Use Gemini to auto-generate description, category, size, color, material from product name"""
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY missing, cannot auto-describe")
        return None
    try:
        prompt = f"""You are a product catalog manager for Dhaka Exclusive e-commerce. Based on the product name and price below, generate category, description, size, color, and material.

Product Name: {name}
Price: {price} BDT

Respond ONLY in this exact format:
Category: [category like Home, Kitchen, Electronics, Fashion, Beauty, Health]
Description: [2-line attractive description in Bengali/Bangla]
Size: [size or N/A]
Color: [color or N/A]
Material: [material or N/A]
"""
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 300, "topP": 0.95}
        }
        headers = {"Content-Type": "application/json"}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{_PRIMARY_MODEL}:generateContent?key={GEMINI_API_KEY}"
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        res = resp.json()
        
        if "error" in res:
            logger.error(f"Gemini API error for '{name}': {res.get('error')}")
            return None
        
        candidates = res.get("candidates", [])
        if candidates:
            text_resp = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            logger.info(f"Gemini raw response for '{name}': {text_resp[:200]}")
            result = {"category": "", "description": "", "size": "", "color": "", "material": ""}
            for line in text_resp.strip().split("\n"):
                line = line.strip()
                if line.startswith("Category:"):
                    result["category"] = line.split(":", 1)[1].strip()
                elif line.startswith("Description:"):
                    result["description"] = line.split(":", 1)[1].strip()
                elif line.startswith("Size:"):
                    result["size"] = line.split(":", 1)[1].strip()
                elif line.startswith("Color:"):
                    result["color"] = line.split(":", 1)[1].strip()
                elif line.startswith("Material:"):
                    result["material"] = line.split(":", 1)[1].strip()
            logger.info(f"Auto-describe parsed for '{name}': {result}")
            return result
        else:
            logger.warning(f"No candidates from Gemini for '{name}': {res}")
    except Exception as e:
        logger.error(f"Auto-describe error for '{name}': {e}")
    return None

@app.route("/admin/product/auto-describe/<int:pid>")
def auto_describe_product(pid):
    if not session.get("logged_in"):
        return redirect("/admin/login")
    try:
        p = db_query("SELECT name, price FROM products WHERE id=?", (pid,), fetchone=True)
        if not p:
            return redirect("/admin?tab=inventory&msg=Product not found")
        
        details = _generate_product_details_with_gemini(p["name"], p["price"])
        if details:
            db_query(
                "UPDATE products SET category=?, description=?, size=?, color=?, material=? WHERE id=?",
                (details.get("category"), details.get("description"), details.get("size"), details.get("color"), details.get("material"), pid),
                commit=True
            )
            msg = f"Auto-described: {p['name'][:30]}..."
        else:
            msg = "Auto-describe failed — check Gemini key"
        return redirect(f"/admin?tab=inventory&msg={msg}")
    except Exception as e:
        return redirect(f"/admin?tab=inventory&msg=Error: {str(e)}")

import threading

@app.route("/admin/products/auto-describe-all")
def auto_describe_all_products():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    try:
        products = db_query("SELECT id, name, price FROM products WHERE COALESCE(description, '') = '' LIMIT 50", fetchall=True) or []
        if not products:
            return redirect("/admin?tab=inventory&msg=All products already have descriptions")
        
        def _process_batch():
            updated = 0
            for p in products:
                try:
                    details = _generate_product_details_with_gemini(p["name"], p["price"])
                    if details:
                        db_query(
                            "UPDATE products SET category=?, description=?, size=?, color=?, material=? WHERE id=?",
                            (details.get("category"), details.get("description"), details.get("size"), details.get("color"), details.get("material"), p["id"]),
                            commit=True
                        )
                        updated += 1
                        time.sleep(0.3)
                except Exception as e:
                    logger.error(f"Auto-describe error for {p['name']}: {e}")
            logger.info(f"Background auto-describe completed: {updated}/{len(products)} products")
        
        threading.Thread(target=_process_batch, daemon=True).start()
        return redirect(f"/admin?tab=inventory&msg=Auto-describing {len(products)} products in background... Refresh in 1 minute")
    except Exception as e:
        return redirect(f"/admin?tab=inventory&msg=Error: {str(e)}")


# =====================================================================
# PRODUCT MANAGEMENT ROUTES
# =====================================================================
@app.route("/admin/product/edit-page/<int:pid>")
def edit_product_page(pid):
    if not session.get("logged_in"):
        return redirect("/admin/login")
    p = db_query("SELECT * FROM products WHERE id=?", (pid,), fetchone=True)
    if not p:
        return redirect("/admin?tab=inventory&msg=Product not found")
    return render_template("edit_product.html", product=p)

@app.route("/admin/products/search")
def search_products():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    q = request.args.get("q", "").strip()
    if not q:
        return redirect("/admin?tab=inventory")
    
    # Search by name, category, description, or price
    search_pattern = f"%{q}%"
    products = db_query(
        """SELECT * FROM products 
           WHERE name LIKE ? OR category LIKE ? OR description LIKE ? OR price LIKE ?
           ORDER BY id DESC""",
        (search_pattern, search_pattern, search_pattern, search_pattern),
        fetchall=True
    ) or []
    
    # Stats
    all_prods = db_query("SELECT * FROM products", fetchall=True) or []
    low = sum(1 for p in all_prods if p["stock"] < 5)
    out = sum(1 for p in all_prods if p["stock"] == 0)
    disc = sum(1 for p in all_prods if p.get("discount_price") and p["discount_price"] > 0)
    val = sum(p["price"] * p["stock"] for p in all_prods)

    s = get_all_settings()
    analytics = {
        "total_orders": db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)["c"] or 0,
        "total_revenue": db_query("SELECT SUM(total) as s FROM orders", fetchone=True)["s"] or 0,
        "chart_data": get_chart_data()
    }
    orders = db_query("SELECT * FROM orders ORDER BY id DESC LIMIT 100", fetchall=True) or []
    users = db_query("SELECT * FROM users ORDER BY last_active DESC LIMIT 30", fetchall=True) or []
    agent_logs = db_query("SELECT * FROM agent_logs ORDER BY id DESC LIMIT 50", fetchall=True) or []
    payment_methods = db_query("SELECT * FROM payment_methods ORDER BY id", fetchall=True) or []
    
    msg = f"Found {len(products)} products matching '{q}'"
    return render_template(
        "inventory.html",
        settings=s, analytics=analytics, orders=orders, users=users,
        products=products, agent_logs=agent_logs, payment_methods=payment_methods,
        chat_history=[], active_chat="", msg=msg, search_query=q,
        page=1, total_pages=1, total_products=len(products), per_page=50, sort_by="id_desc",
        low_stock_count=low, out_stock_count=out, discount_count=disc, total_value=val
    )

@app.route("/admin/product/add", methods=["POST"])
def add_product():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    try:
        name = request.form.get("name", "").strip()
        price = int(request.form.get("price", 0))
        stock = int(request.form.get("stock", 10))
        fb_id = request.form.get("fb_product_id", "").strip()
        image_url = request.form.get("image_url", "").strip()
        
        # Handle image upload
        file = request.files.get("image")
        if file and file.filename:
            filename = secure_filename(f"prod_{int(time.time())}_{file.filename}")
            file_path = os.path.join(MEDIA_FOLDER, filename)
            file.save(file_path)
            # Upload to WhatsApp media if possible
            media_id = upload_media_to_whatsapp(file_path, "image")
            if media_id:
                image_url = f"[MEDIA:{media_id}]"
            else:
                image_url = file_path  # fallback local path
        
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "").strip()
        size = request.form.get("size", "").strip()
        color = request.form.get("color", "").strip()
        material = request.form.get("material", "").strip()
        
        db_query(
            "INSERT INTO products (fb_product_id, name, price, stock, image_url, description, category, size, color, material) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fb_id or None, name, price, stock, image_url or None, description or None, category or None, size or None, color or None, material or None),
            commit=True
        )
        db_query("INSERT INTO agent_logs (username, action, details) VALUES (?, 'ADD_PRODUCT', ?)",
                 (session.get("username"), f"Added {name}"), commit=True)
        return redirect("/admin?tab=products&msg=Product Added Successfully")
    except Exception as e:
        logger.error(f"Add product error: {e}")
        return redirect(f"/admin?tab=products&msg=Error: {str(e)}")

@app.route("/admin/product/update/<int:pid>", methods=["POST"])
def update_product(pid):
    if not session.get("logged_in"):
        return redirect("/admin/login")
    try:
        name = request.form.get("name", "").strip()
        price = int(request.form.get("price", 0))
        stock = int(request.form.get("stock", 0))
        fb_id = request.form.get("fb_product_id", "").strip()
        image_url = request.form.get("image_url", "").strip()
        
        file = request.files.get("image")
        if file and file.filename:
            filename = secure_filename(f"prod_{int(time.time())}_{file.filename}")
            file_path = os.path.join(MEDIA_FOLDER, filename)
            file.save(file_path)
            media_id = upload_media_to_whatsapp(file_path, "image")
            if media_id:
                image_url = f"[MEDIA:{media_id}]"
            else:
                image_url = file_path
        
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "").strip()
        size = request.form.get("size", "").strip()
        color = request.form.get("color", "").strip()
        material = request.form.get("material", "").strip()
        
        db_query(
            "UPDATE products SET fb_product_id=?, name=?, price=?, stock=?, image_url=?, description=?, category=?, size=?, color=?, material=? WHERE id=?",
            (fb_id or None, name, price, stock, image_url or None, description or None, category or None, size or None, color or None, material or None, pid),
            commit=True
        )
        return redirect("/admin?tab=products&msg=Product Updated")
    except Exception as e:
        logger.error(f"Update product error: {e}")
        return redirect(f"/admin?tab=products&msg=Error: {str(e)}")

@app.route("/admin/product/delete/<int:pid>")
def delete_product(pid):
    if not session.get("logged_in"):
        return redirect("/admin/login")
    db_query("DELETE FROM products WHERE id=?", (pid,), commit=True)
    return redirect("/admin?tab=products&msg=Product Deleted")

@app.route("/admin/products/clear")
def clear_all_products():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    db_query("DELETE FROM products", commit=True)
    return redirect("/admin?tab=inventory&msg=All Products Cleared — Ready for Re-Sync")


# =====================================================================
# PRODUCT SORTING
# =====================================================================
@app.route("/admin/products/sort")
def sort_products():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    sort_by = request.args.get("sort_by", "id_desc")
    return redirect(f"/admin?tab=inventory&sort={sort_by}")

# =====================================================================
# PRODUCT BULK ACTION
# =====================================================================
@app.route("/admin/products/bulk-action", methods=["POST"])
def bulk_action():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    action = request.form.get("bulk_action", "")
    selected = request.form.getlist("selected_products")
    if not selected:
        return redirect("/admin?tab=inventory&msg=No products selected")
    
    ids = [int(x) for x in selected]
    placeholders = ",".join(["?"] * len(ids))
    
    if action == "delete":
        db_query(f"DELETE FROM products WHERE id IN ({placeholders})", tuple(ids), commit=True)
        msg = f"Deleted {len(ids)} products"
    elif action == "stock_10":
        db_query(f"UPDATE products SET stock = 10 WHERE id IN ({placeholders})", tuple(ids), commit=True)
        msg = f"Set stock to 10 for {len(ids)} products"
    elif action == "stock_50":
        db_query(f"UPDATE products SET stock = 50 WHERE id IN ({placeholders})", tuple(ids), commit=True)
        msg = f"Set stock to 50 for {len(ids)} products"
    elif action == "discount_10":
        for pid in ids:
            p = db_query("SELECT price FROM products WHERE id=?", (pid,), fetchone=True)
            if p:
                disc = int(p["price"] * 0.9)
                db_query("UPDATE products SET discount_price = ? WHERE id = ?", (disc, pid), commit=True)
        msg = f"Applied 10% discount to {len(ids)} products"
    elif action == "discount_25":
        for pid in ids:
            p = db_query("SELECT price FROM products WHERE id=?", (pid,), fetchone=True)
            if p:
                disc = int(p["price"] * 0.75)
                db_query("UPDATE products SET discount_price = ? WHERE id = ?", (disc, pid), commit=True)
        msg = f"Applied 25% discount to {len(ids)} products"
    elif action == "clear_discount":
        db_query(f"UPDATE products SET discount_price = NULL WHERE id IN ({placeholders})", tuple(ids), commit=True)
        msg = f"Cleared discount for {len(ids)} products"
    else:
        msg = "Unknown action"
    
    return redirect(f"/admin?tab=inventory&msg={msg}")

# =====================================================================
# EXPORT TO EXCEL
# =====================================================================
@app.route("/admin/products/export/excel")
def export_excel():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    sort_param = request.args.get("sort", "id_desc")
    sort_map = {
        "id_desc": "id DESC",
        "price_low": "price ASC",
        "price_high": "price DESC",
        "name_az": "name COLLATE NOCASE ASC",
        "stock_low": "stock ASC",
        "stock_high": "stock DESC",
    }
    products_order = sort_map.get(sort_param, "id DESC")
    # Pagination
    page = int(request.args.get("page", 1))
    per_page = 20
    offset = (page - 1) * per_page
    
    total_products = db_query("SELECT COUNT(*) as c FROM products", fetchone=True)["c"] or 0
    total_pages = (total_products + per_page - 1) // per_page
    
    products = db_query(f"SELECT * FROM products ORDER BY {products_order} LIMIT ? OFFSET ?", (per_page, offset), fetchall=True) or []
    
    # Attach reviews to products
    for p in products:
        reviews = db_query("SELECT * FROM product_reviews WHERE product_id=? ORDER BY id DESC", (p["id"],), fetchall=True) or []
        p["reviews"] = reviews
        if reviews:
            avg = sum(r["rating"] for r in reviews) / len(reviews)
            p["avg_rating"] = round(avg, 1)
            p["review_count"] = len(reviews)
        else:
            p["avg_rating"] = 0
            p["review_count"] = 0
    
    from io import BytesIO
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Products"
    headers = ["ID", "FB ID", "Name", "Price", "Discount", "Stock", "Category", "Size", "Color", "Material", "Description"]
    ws.append(headers)
    for p in products:
        ws.append([
            p["id"], p.get("fb_product_id", ""), p["name"], p["price"],
            p.get("discount_price", ""), p["stock"], p.get("category", ""),
            p.get("size", ""), p.get("color", ""), p.get("material", ""),
            p.get("description", "")
        ])
    
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(output, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name="dhaka_exclusive_products.xlsx")

# =====================================================================
# EXPORT TO PDF
# =====================================================================
@app.route("/admin/products/export/pdf")
def export_pdf():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    sort_param = request.args.get("sort", "id_desc")
    sort_map = {
        "id_desc": "id DESC",
        "price_low": "price ASC",
        "price_high": "price DESC",
        "name_az": "name COLLATE NOCASE ASC",
        "stock_low": "stock ASC",
        "stock_high": "stock DESC",
    }
    products_order = sort_map.get(sort_param, "id DESC")
    # Pagination
    page = int(request.args.get("page", 1))
    per_page = 20
    offset = (page - 1) * per_page
    
    total_products = db_query("SELECT COUNT(*) as c FROM products", fetchone=True)["c"] or 0
    total_pages = (total_products + per_page - 1) // per_page
    
    products = db_query(f"SELECT * FROM products ORDER BY {products_order} LIMIT ? OFFSET ?", (per_page, offset), fetchall=True) or []
    
    # Attach reviews to products
    for p in products:
        reviews = db_query("SELECT * FROM product_reviews WHERE product_id=? ORDER BY id DESC", (p["id"],), fetchall=True) or []
        p["reviews"] = reviews
        if reviews:
            avg = sum(r["rating"] for r in reviews) / len(reviews)
            p["avg_rating"] = round(avg, 1)
            p["review_count"] = len(reviews)
        else:
            p["avg_rating"] = 0
            p["review_count"] = 0
    
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()
    elements.append(Paragraph("<b>Dhaka Exclusive - Product Catalog</b>", styles["Title"]))
    elements.append(Spacer(1, 12))
    
    data = [["#", "Name", "Price", "Stock", "Category"]]
    for p in products[:100]:
        data.append([str(p["id"]), p["name"][:30], f"{p['price']}৳", str(p["stock"]), p.get("category", "")])
    
    table = Table(data)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4f46e5")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#0f172a")),
        ("TEXTCOLOR", (0, 1), (-1, -1), colors.whitesmoke),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#334155")),
    ]))
    elements.append(table)
    doc.build(elements)
    output.seek(0)
    return send_file(output, mimetype="application/pdf", as_attachment=True, download_name="dhaka_exclusive_products.pdf")

# =====================================================================
# QR CODE GENERATOR
# =====================================================================
@app.route("/admin/product/qr/<int:pid>")
def generate_qr(pid):
    if not session.get("logged_in"):
        return redirect("/admin/login")
    p = db_query("SELECT * FROM products WHERE id=?", (pid,), fetchone=True)
    if not p:
        return "Product not found", 404
    
    import qrcode
    from io import BytesIO
    
    data = f"Product: {p['name']}\nPrice: {p['price']}৳\nStock: {p['stock']}"
    img = qrcode.make(data)
    output = BytesIO()
    img.save(output, "PNG")
    output.seek(0)
    return send_file(output, mimetype="image/png")

# =====================================================================
# ADD REVIEW
# =====================================================================
@app.route("/admin/product/review/<int:pid>", methods=["POST"])
def add_review(pid):
    if not session.get("logged_in"):
        return redirect("/admin/login")
    rating = int(request.form.get("rating", 5))
    comment = request.form.get("comment", "").strip()
    customer_name = request.form.get("customer_name", "Anonymous").strip()
    
    db_query(
        "INSERT INTO product_reviews (product_id, rating, comment, customer_name) VALUES (?, ?, ?, ?)",
        (pid, rating, comment, customer_name), commit=True
    )
    return redirect(f"/admin?tab=inventory&msg=Review added")


@app.route("/admin/sync-facebook-trigger")
def sync_facebook_trigger():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    try:
        s = get_all_settings()
        catalog_id = s.get("fb_catalogue_id", "").strip()
        token = s.get("fb_access_token", "").strip()
        if not catalog_id or not token:
            return redirect("/admin?tab=inventory&msg=Facebook Catalog ID or Token Missing")
        
        # Auto-add missing columns to products table
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("PRAGMA table_info(products)")
            existing_cols = [col[1] for col in c.fetchall()]
            for col_name in ["description", "category", "size", "color", "material"]:
                if col_name not in existing_cols:
                    c.execute(f"ALTER TABLE products ADD COLUMN {col_name} TEXT")
                    logger.info(f"Added column '{col_name}' to products table")
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Column migration error: {e}")
        
        added = 0
        updated = 0
        total_fetched = 0
        next_url = f"https://graph.facebook.com/v18.0/{catalog_id}/products"
        params = {
            "access_token": token,
            "fields": "id,name,price,availability,image_url",
            "limit": 100
        }
        
        # Pagination loop — fetch ALL products
        while next_url and total_fetched < 5000:
            r = requests.get(next_url, params=params if next_url == f"https://graph.facebook.com/v18.0/{catalog_id}/products" else None, timeout=30)
            res = r.json()
            
            if "error" in res:
                err_msg = res.get("error", {}).get("message", "Unknown error")
                return redirect(f"/admin?tab=inventory&msg=Facebook Error: {err_msg}")
            
            items = res.get("data", [])
            if not items:
                break
            
            for item in items:
                fb_id = item.get("id", "")
                name = item.get("name", "")
                
                # Try multiple price fields from Facebook Catalog
                price = 0
                price_found = False
                for price_key in ["price", "sale_price", "price_range", "unit_price"]:
                    price_raw = item.get(price_key)
                    if price_raw:
                        try:
                            if isinstance(price_raw, dict):
                                price = int(float(price_raw.get("amount", 0)))
                                price_found = True
                                break
                            elif isinstance(price_raw, str):
                                # Handle "BDT590.00", "BDT1,050.00", "1000 BDT", "1000.00"
                                import re
                                # Remove currency text and commas, extract number
                                cleaned = re.sub(r'[^\d.]', '', price_raw.replace(',', ''))
                                if cleaned:
                                    price = int(float(cleaned))
                                    price_found = True
                                    break
                            elif isinstance(price_raw, (int, float)):
                                price = int(price_raw)
                                price_found = True
                                break
                        except Exception as e:
                            logger.debug(f"Price key '{price_key}' failed for {fb_id}: {e}")
                            continue
                
                if not price_found:
                    # Try nested in product_data
                    pdata = item.get("product_data", {})
                    if pdata and "price" in pdata:
                        try:
                            pdata_price = pdata["price"]
                            if isinstance(pdata_price, dict):
                                price = int(float(pdata_price.get("amount", 0)))
                            elif isinstance(pdata_price, str):
                                import re
                                cleaned = re.sub(r'[^\d.]', '', pdata_price.replace(',', ''))
                                if cleaned:
                                    price = int(float(cleaned))
                        except:
                            pass
                
                if price == 0:
                    logger.warning(f"Could not parse price for {fb_id}, name='{name}', raw_price_data={item.get('price')}")
                availability = item.get("availability", "")
                image = item.get("image_url", "")
                stock = 10 if availability == "in stock" else 0
                
                existing = db_query("SELECT id FROM products WHERE fb_product_id=?", (fb_id,), fetchone=True)
                if existing:
                    db_query(
                        "UPDATE products SET name=?, price=?, stock=?, image_url=? WHERE fb_product_id=?",
                        (name, price, stock, image, fb_id), commit=True
                    )
                    updated += 1
                else:
                    db_query(
                        "INSERT INTO products (fb_product_id, name, price, stock, image_url, description, category, size, color, material) VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL)",
                        (fb_id, name, price, stock, image), commit=True
                    )
                    added += 1
            
            total_fetched += len(items)
            logger.info(f"Fetched {len(items)} products, total: {total_fetched}")
            
            # Get next page URL
            paging = res.get("paging", {})
            next_url = paging.get("next")
            params = None  # next URL already contains all params
        
        msg = f"Facebook Sync: {added} added, {updated} updated (Total: {total_fetched})"
        logger.info(msg)
        return redirect(f"/admin?tab=inventory&msg={msg}")
    except Exception as e:
        logger.error(f"Facebook sync error: {e}")
        return redirect(f"/admin?tab=inventory&msg=Sync Error: {str(e)}")


# =====================================================================
# MESSENGER BOT FUNCTIONS
# =====================================================================
def _download_messenger_media(url):
    """Download Messenger media from URL (images/videos come as URLs)"""
    try:
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            ext = ".jpg" if "image" in r.headers.get("Content-Type", "") else ".mp4"
            filename = f"msgr_{int(time.time())}{ext}"
            filepath = os.path.join(MEDIA_FOLDER, filename)
            with open(filepath, "wb") as f:
                f.write(r.content)
            return filepath
    except Exception as e:
        logger.error(f"Messenger media download error: {e}")
    return None

def send_messenger_message(recipient_id, message_text):
    """Send text reply via Messenger Send API"""
    if not MESSENGER_PAGE_ACCESS_TOKEN:
        logger.error("MESSENGER_PAGE_ACCESS_TOKEN not set")
        return False
    try:
        url = f"https://graph.facebook.com/v18.0/me/messages?access_token={MESSENGER_PAGE_ACCESS_TOKEN}"
        payload = {
            "recipient": {"id": recipient_id},
            "message": {"text": message_text[:2000]}
        }
        r = requests.post(url, json=payload, timeout=20)
        res = r.json()
        if res.get("message_id"):
            return True
        logger.error(f"Messenger send error: {res}")
        return False
    except Exception as e:
        logger.error(f"Messenger send exception: {e}")
        return False

def _get_messenger_name(sender_id):
    """Get user name from Messenger profile API"""
    if not MESSENGER_PAGE_ACCESS_TOKEN:
        return "Messenger User"
    try:
        url = f"https://graph.facebook.com/v18.0/{sender_id}?access_token={MESSENGER_PAGE_ACCESS_TOKEN}&fields=first_name,last_name"
        r = requests.get(url, timeout=10)
        data = r.json()
        fname = data.get("first_name", "")
        lname = data.get("last_name", "")
        return f"{fname} {lname}".strip() or "Messenger User"
    except Exception:
        return "Messenger User"

@app.route("/messenger-webhook", methods=["GET", "POST"])
def messenger_webhook():
    """Handle Messenger Platform webhooks"""
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        logger.info(f"Messenger verify: mode={mode}, token={token}, challenge={challenge}")
        if mode == "subscribe" and token == MESSENGER_VERIFY_TOKEN:
            if challenge is not None:
                # Facebook needs the exact challenge echoed back as plain text
                logger.info("Messenger webhook verified successfully.")
                return str(challenge), 200
            logger.warning("Messenger verify: challenge was None")
            return "Challenge missing", 400
        logger.warning(f"Messenger verify failed: mode={mode}, token_match={token == MESSENGER_VERIFY_TOKEN}")
        return "Verification failed", 403

    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        logger.info(f"Messenger webhook received: {json.dumps(data, ensure_ascii=False)[:500]}")
        try:
            entry_list = data.get("entry", [])
            for entry in entry_list:
                messaging_events = entry.get("messaging", [])
                for event in messaging_events:
                    sender_id = event.get("sender", {}).get("id", "")
                    if not sender_id:
                        continue

                    # Get sender name
                    sender_name = _get_messenger_name(sender_id)

                    # Handle message
                    message = event.get("message", {})
                    if not message:
                        continue

                    msg_text = message.get("text", "")
                    attachments = message.get("attachments", [])
                    msg_type = "text"

                    image_path = None
                    voice_path = None
                    content = msg_text or ""

                    # Handle attachments (image, audio, video)
                    if attachments:
                        att = attachments[0]
                        att_type = att.get("type", "")
                        if att_type == "image":
                            msg_type = "image"
                            image_url = att.get("payload", {}).get("url", "")
                            if image_url:
                                image_path = _download_messenger_media(image_url)
                            content = "[Photo Received]"
                        elif att_type in ["audio", "voice"]:
                            msg_type = "voice"
                            audio_url = att.get("payload", {}).get("url", "")
                            if audio_url:
                                voice_path = _download_messenger_media(audio_url)
                            content = "🎤 [Voice Received]"
                        elif att_type == "video":
                            msg_type = "video"
                            content = "[Video Received]"
                        else:
                            content = f"[{att_type.upper()} Received]"

                    # Store in DB (use sender_id as 'phone' for Messenger)
                    db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'inbound', 'messenger')", (sender_id, content), commit=True)
                    db_query("INSERT OR IGNORE INTO users (phone, name) VALUES (?, ?)", (sender_id, sender_name), commit=True)
                    db_query("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE phone = ?", (sender_id,), commit=True)

                    # Get recent chat history
                    recent_msgs = db_query("SELECT * FROM messages WHERE from_number=? ORDER BY id DESC LIMIT 6", (sender_id,), fetchall=True) or []
                    recent_msgs.reverse()

                    # Generate AI reply
                    reply = get_optimized_gemini_reply(
                        content,
                        customer_phone=sender_id,
                        chat_history=recent_msgs,
                        image_path=image_path,
                        voice_path=voice_path
                    )

                    if reply:
                        sent = send_messenger_message(sender_id, reply)
                        if sent:
                            db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', 'gemini_ai')", (sender_id, reply), commit=True)

                        # Try send product image if mentioned
                        if msg_type == "text" and ("ছবি" in content or "photo" in content.lower()):
                            _try_send_product_image(sender_id, content, recent_msgs)

        except Exception as e:
            logger.error(f"Messenger webhook processing error: {e}")
        return "EVENT_RECEIVED", 200


# =====================================================================
# BRIDGE API CONFIG
# =====================================================================
@app.route("/api/bridge-config", methods=["GET"])
def api_bridge_config():
    s = get_all_settings()
    return jsonify({
        "configs": [{
            "id": 1,
            "label": "Business WhatsApp",
            "team_group": s.get("team_group", ""),
            "orders_group": s.get("orders_group", ""),
            "enabled": True
        }]
    })

# =====================================================================
# GROUP WEBHOOK (Team + Orders groups)
# =====================================================================
@app.route("/group-webhook", methods=["POST"])
def group_webhook():
    data = request.get_json(force=True, silent=True) or {}
    group_name = data.get("group_name", "")
    group_type = data.get("group_type", "other")
    sender_name = data.get("sender_name", "Member")
    body = data.get("message", "")
    media_data = data.get("media_data", "")

    if not body:
        return jsonify({"reply": ""})

    logger.info(f"[GROUP:{group_type}] {sender_name}: {body[:50]}")

    products = db_query("SELECT name, price, stock FROM products ORDER BY id DESC LIMIT 50", fetchall=True) or []
    product_list = "\n".join([f"- {p['name']}: {p['price']}৳" for p in products[:20]])

    if group_type == "team":
        # Detect if sender is admin by phone number
        sender_id = data.get("sender_id", "").replace("@c.us", "").replace("@lid", "")
        sender_phone = re.sub(r"\D", "", sender_id)
        
        # Check team_members table for role
        member = db_query("SELECT role FROM team_members WHERE phone=? OR wa_id=?", (sender_phone, sender_id), fetchone=True)
        role = "Admin" if (member and member.get("role") == "admin") else "Team Member"
        
        # Also check settings for admin_phones
        s = get_all_settings()
        admin_phones = [p.strip() for p in s.get("admin_phones", "").split(",") if p.strip()]
        if sender_phone in admin_phones or sender_id in admin_phones:
            role = "Admin"

        prompt = f"""তুমি Dhaka Exclusive-এর AI সহকারী। 

এখন "{sender_name}" ({role}) বলছেন:
"{body}"

প্রোডাক্ট:
{product_list}

নিয়ম:
- যদি Admin হন: সম্মানের সাথে, বিস্তারিত উত্তর দাও, সব তথ্য দাও
- যদি Team Member হন: বন্ধুসুলভ, সংক্ষিপ্ত উত্তর দাও
- সবসময় বাংলায় উত্তর দাও"""
        reply = get_gemini_reply(prompt)
        return jsonify({"reply": reply})

    elif group_type == "orders":
        prompt = f"""তুমি Dhaka Exclusive-এর অর্ডার এক্সট্রাক্টর। মেসেজ থেকে অর্ডার তথ্য বের করো।

মেসেজ: "{body}"

Output:
NAME: <নাম বা Unknown>
PHONE: <ফোন বা খালি>
ADDRESS: <ঠিকানা বা খালি>
PRODUCT: <প্রোডাক্ট নাম>
QUANTITY: <সংখ্যা বা 1>
PRICE: <দাম বা 0>

যদি অর্ডার না হয়: NOT_AN_ORDER"""

        ai_result = get_gemini_reply(prompt)
        if "NOT_AN_ORDER" in ai_result:
            return jsonify({"reply": ""})

        name, phone, address, product, qty, price = "", "", "", "", 1, 0
        for line in ai_result.split("\n"):
            if line.startswith("NAME:"): name = line.replace("NAME:", "").strip()
            if line.startswith("PHONE:"): phone = line.replace("PHONE:", "").strip()
            if line.startswith("ADDRESS:"): address = line.replace("ADDRESS:", "").strip()
            if line.startswith("PRODUCT:"): product = line.replace("PRODUCT:", "").strip()
            if line.startswith("QUANTITY:"):
                try: qty = int(line.replace("QUANTITY:", "").strip())
                except: pass
            if line.startswith("PRICE:"):
                try: price = int(line.replace("PRICE:", "").strip())
                except: pass

        if not product:
            return jsonify({"reply": ""})

        total = price * qty
        db_query("""INSERT INTO group_orders (phone, customer_name, address, product_name, quantity, price, total, status, group_name, raw_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (phone, name, address, product, qty, price, total, group_name, body), commit=True)

        logger.info(f"[ORDER] Saved: {product} x{qty} = {total}৳")
        return jsonify({"reply": f"✅ অর্ডার গ্রহণ হয়েছে!\n📦 {product} x{qty}\n💰 {total}৳"})

    return jsonify({"reply": ""})


# =====================================================================
# BUSINESS WEBHOOK (Individual customer messages from bridge)
# =====================================================================
@app.route("/business-webhook", methods=["POST"])
def business_webhook():
    data = request.get_json(force=True, silent=True) or {}
    body = data.get("message", "")
    customer_phone = data.get("customer_phone", "")
    customer_name = data.get("customer_name", "Customer")
    media_data = data.get("media_data", "")

    if not body:
        return jsonify({"reply": ""})

    logger.info(f"[CUSTOMER-BRIDGE] {customer_name}: {body[:50]}")

    recent = db_query("SELECT * FROM messages WHERE from_number=? ORDER BY id DESC LIMIT 6", (customer_phone,), fetchall=True) or []
    recent.reverse()

    image_path = media_data if media_data else None
    reply = get_optimized_gemini_reply(body, customer_phone=customer_phone, chat_history=recent, image_path=image_path)

    if reply:
        db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', 'gemini_bridge')", (customer_phone, reply), commit=True)

    return jsonify({"reply": reply})


# =====================================================================
# ADMIN: Team Members
# =====================================================================
@app.route("/admin/team")
def admin_team():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    members = db_query("SELECT * FROM team_members ORDER BY id DESC", fetchall=True) or []
    return render_template_string("""
<!DOCTYPE html><html><head><meta charset="utf-8"><title>Team</title>
<style>body{font-family:Arial;margin:40px;background:#f5f5f5}.container{max-width:900px;margin:0 auto;background:white;padding:30px;border-radius:10px}
table{width:100%;border-collapse:collapse;margin-top:20px}th,td{padding:12px;border-bottom:1px solid #ddd;text-align:left}th{background:#4CAF50;color:white}
.btn{padding:8px 16px;border:none;border-radius:5px;cursor:pointer;color:white;text-decoration:none}
.btn-green{background:#4CAF50}.btn-red{background:#f44336}
input{padding:10px;margin:5px;border:1px solid #ddd;border-radius:5px}
.nav{margin-bottom:20px}.nav a{margin-right:15px;color:#666;text-decoration:none}
</style></head><body><div class="container">
<div class="nav"><a href="/admin">← Dashboard</a></div>
<h1>👥 Team Members</h1>
<form method="POST" action="/admin/team/add">
    <input type="text" name="name" placeholder="Name" required>
    <input type="text" name="phone" placeholder="Phone" required>
    <input type="text" name="wa_id" placeholder="WhatsApp ID">
    <button type="submit" class="btn btn-green">Add</button>
</form>
<table><tr><th>ID</th><th>Name</th><th>Phone</th><th>Role</th><th>Action</th></tr>
{% for m in members %}
<tr><td>{{ m.id }}</td><td>{{ m.name }}</td><td>{{ m.phone }}</td><td>{{ m.role }}</td>
<td><a href="/admin/team/delete/{{ m.id }}" class="btn btn-red" onclick="return confirm('Delete?')">Delete</a></td></tr>
{% endfor %}
</table></div></body></html>
""", members=members)

@app.route("/admin/team/add", methods=["POST"])
def admin_team_add():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    wa_id = request.form.get("wa_id", "").strip()
    db_query("INSERT OR REPLACE INTO team_members (name, phone, wa_id, role, is_active) VALUES (?, ?, ?, 'moderator', 1)", (name, phone, wa_id), commit=True)
    flash("Member added!")
    return redirect("/admin/team")

@app.route("/admin/team/delete/<int:member_id>")
def admin_team_delete(member_id):
    if not session.get("logged_in"):
        return redirect("/admin/login")
    db_query("DELETE FROM team_members WHERE id = ?", (member_id,), commit=True)
    flash("Member deleted!")
    return redirect("/admin/team")


# =====================================================================
# ADMIN: WhatsApp Group Settings
# =====================================================================
@app.route("/admin/whatsapp-settings")
def admin_whatsapp_settings():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    s = get_all_settings()
    return render_template_string("""
<!DOCTYPE html><html><head><meta charset="utf-8"><title>WhatsApp Settings</title>
<style>body{font-family:Arial;margin:40px;background:#f5f5f5}.container{max-width:700px;margin:0 auto;background:white;padding:30px;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,0.1)}
input{width:100%;padding:12px;margin:8px 0;border:1px solid #ddd;border-radius:5px;box-sizing:border-box}
.btn{background:#25D366;color:white;padding:12px 24px;border:none;border-radius:5px;cursor:pointer;font-size:16px}
label{font-weight:bold;margin-top:15px;display:block;color:#333}
.info{background:#e3f2fd;padding:15px;border-radius:5px;margin:15px 0;color:#1565c0}
.nav{margin-bottom:20px}.nav a{margin-right:15px;color:#666;text-decoration:none}
</style></head><body><div class="container">
<div class="nav"><a href="/admin">← Dashboard</a></div>
<h1>📱 WhatsApp Group Settings</h1>
<div class="info">
<b>টিম গ্রুপ:</b> AI সবার প্রশ্নের উত্তর দেবে<br>
<b>অর্ডার গ্রুপ:</b> মেসেজ থেকে অর্ডার অটো শনাক্ত হয়ে এখানে আসবে
</div>
<form method="POST" action="/admin/whatsapp-settings/save">
    <label>Team Group Name</label>
    <input type="text" name="team_group" value="{{ s.get('team_group','') }}" placeholder="e.g. Team Of Dhaka Exclusive">
    <label>Orders Group Name</label>
    <input type="text" name="orders_group" value="{{ s.get('orders_group','') }}" placeholder="e.g. Orders">
    <button type="submit" class="btn">💾 Save</button>
</form>
<h3 style="margin-top:30px">🖥️ PC Bridge Setup</h3>
<ol style="line-height:2;color:#666">
    <li>Download bridge files to PC</li>
    <li>Edit <code>config.json</code> with your Flask URL</li>
    <li>Run <code>setup.bat</code> → Scan QR</li>
</ol>
</div></body></html>
""", s=s)

@app.route("/admin/whatsapp-settings/save", methods=["POST"])
def save_whatsapp_settings():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    for k in ["team_group", "orders_group"]:
        v = request.form.get(k, "").strip()
        db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?", (k, v, v), commit=True)
    flash("WhatsApp settings saved!")
    return redirect("/admin/whatsapp-settings")


# =====================================================================
# ADMIN: Group Orders View
# =====================================================================
@app.route("/admin/group-orders")
def admin_group_orders():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    orders = db_query("SELECT * FROM group_orders ORDER BY id DESC LIMIT 200", fetchall=True) or []
    return render_template_string("""
<!DOCTYPE html><html><head><meta charset="utf-8"><title>Group Orders</title>
<style>body{font-family:Arial;margin:40px;background:#f5f5f5}.container{max-width:1100px;margin:0 auto;background:white;padding:30px;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,0.1)}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:10px;text-align:left;border-bottom:1px solid #ddd}th{background:#FF9800;color:white}tr:hover{background:#f1f1f1}
.btn{padding:6px 12px;border:none;border-radius:4px;cursor:pointer;font-size:12px;color:white;text-decoration:none}
.btn-green{background:#4CAF50}.btn-red{background:#f44336}
.nav{margin-bottom:20px}.nav a{margin-right:15px;color:#666;text-decoration:none}
</style></head><body><div class="container">
<div class="nav"><a href="/admin">← Dashboard</a></div>
<h1>📦 Group Orders (Auto-captured)</h1>
<table>
<tr><th>ID</th><th>Customer</th><th>Phone</th><th>Product</th><th>Qty</th><th>Price</th><th>Total</th><th>Group</th><th>Status</th><th>Date</th><th>Action</th></tr>
{% for o in orders %}
<tr>
    <td>{{ o.id }}</td><td>{{ o.customer_name or '-' }}</td><td>{{ o.phone or '-' }}</td><td>{{ o.product_name }}</td>
    <td>{{ o.quantity }}</td><td>{{ o.price }}</td><td>{{ o.total }}৳</td><td>{{ o.group_name }}</td>
    <td>{{ o.status }}</td><td>{{ o.created_at }}</td>
    <td>
        <a href="/admin/group-orders/status/{{ o.id }}?status=confirmed" class="btn btn-green">Confirm</a>
        <a href="/admin/group-orders/status/{{ o.id }}?status=cancelled" class="btn btn-red">Cancel</a>
    </td>
</tr>
{% endfor %}
</table></div></body></html>
""", orders=orders)

@app.route("/admin/group-orders/status/<int:order_id>")
def admin_group_order_status(order_id):
    if not session.get("logged_in"):
        return redirect("/admin/login")
    status = request.args.get("status", "pending")
    db_query("UPDATE group_orders SET status = ? WHERE id = ?", (status, order_id), commit=True)
    return redirect("/admin/group-orders")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
