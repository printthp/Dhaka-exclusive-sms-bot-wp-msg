import os, sys, json, sqlite3, logging, ctypes, time, requests, random, pandas as pd
from io import BytesIO
from datetime import datetime, timedelta
from threading import Lock
from flask import Flask, request, jsonify, render_template, render_template_string, redirect, url_for, session, flash, send_file
from xhtml2pdf import pisa
from werkzeug.utils import secure_filename

# আলাদা ফাইল থেকে AI ইম্পোর্ট করা
try:
    from gemini_engine import get_gemini_reply
except ImportError:
    logger.error("gemini_engine.py not found! Please create the file.")

# =====================================================================
# SYSTEM & STORAGE SETUP
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
app.secret_key = os.environ.get("SECRET_KEY", "dhaka-exclusive-master-ultra-v2026-final")
application = app
db_lock = Lock()

# =====================================================================
# ENGINE LOADERS (C++ & ASSEMBLY)
# =====================================================================
lib, asm_lib = None, None
try:
    if os.path.exists("engine.so"):
        lib = ctypes.CDLL(os.path.abspath("engine.so"))
        lib.process_business_logic.restype = ctypes.c_char_p
    if os.path.exists("asm_engine.so"):
        asm_lib = ctypes.CDLL(os.path.abspath("asm_engine.so"))
        asm_lib.asm_process_command.restype = ctypes.c_char_p
except Exception as e: logger.error(f"Engine Load Fail: {e}")

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
        try: c.execute("ALTER TABLE users ADD COLUMN name TEXT DEFAULT 'Customer'")
        except: pass
        conn.commit(); conn.close()

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
        except Exception as e: logger.error(f"SQL Error: {e}"); return None
        finally: conn.close()

def get_all_settings():
    rows = db_query("SELECT key, value FROM settings", fetchall=True) or []
    return {r["key"]: r["value"] for r in rows}

@app.context_processor
def inject_globals():
    unread = db_query("SELECT COUNT(DISTINCT from_number) as c FROM messages WHERE direction='inbound'", fetchone=True)
    return dict(unread_chat_count=unread['c'] if unread else 0)

# =====================================================================
# WHATSAPP API & MEDIA
# =====================================================================
MEDIA_FOLDER = os.path.join(os.path.dirname(DB_PATH), "media")
if not os.path.exists(MEDIA_FOLDER): os.makedirs(MEDIA_FOLDER)

def send_whatsapp_message(to_phone, message):
    s = get_all_settings(); token = s.get("permanent_token", ""); phone_id = s.get("phone_number_id", "")
    if not token or not phone_id: return False
    url = f"https://graph.facebook.com/v22.0/{phone_id}/messages"
    try:
        r = requests.post(url, json={"messaging_product": "whatsapp", "to": to_phone, "type": "text", "text": {"body": message}}, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        return r.status_code in (200, 201)
    except: return False

def upload_media_to_whatsapp(file_path, media_type="image"):
    s = get_all_settings(); token = s.get("permanent_token", ""); phone_id = s.get("phone_number_id", "")
    if not token or not phone_id: return None
    try:
        with open(file_path, "rb") as f:
            r = requests.post(f"https://graph.facebook.com/v22.0/{phone_id}/media", headers={"Authorization": f"Bearer {token}"}, files={"file": (os.path.basename(file_path), f, f"{media_type}/*")}, data={"type": media_type, "messaging_product": "whatsapp"}, timeout=60)
            return r.json().get("id") if r.status_code in (200, 201) else None
    except: return None

def send_whatsapp_media(to_phone, media_id, media_type="image", caption=""):
    s = get_all_settings(); token = s.get("permanent_token", ""); phone_id = s.get("phone_number_id", "")
    body = {"messaging_product": "whatsapp", "to": to_phone, "type": media_type, media_type: {"id": media_id}}
    if caption and media_type == "image": body["image"]["caption"] = caption
    try:
        r = requests.post(f"https://graph.facebook.com/v22.0/{phone_id}/messages", json=body, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        return r.status_code in (200, 201)
    except: return False

# =====================================================================
# PATHAO SYNC
# =====================================================================
def get_pathao_token():
    s = get_all_settings(); bearer = s.get('pathao_bearer_token', '').strip()
    if bearer and len(bearer) > 20: return bearer
    url = "https://api-hermes.pathao.com/aladdin/api/v1/issue-token"
    payload = {"client_id": s.get('pathao_client_id',''), "client_secret": s.get('pathao_client_secret',''), "username": s.get('pathao_merchant_email',''), "password": s.get('pathao_merchant_password',''), "grant_type": "password"}
    try:
        r = requests.post(url, json=payload, headers={"Accept": "application/json"}, timeout=15).json()
        token = r.get('access_token')
        if token: db_query("INSERT INTO settings (key, value) VALUES ('pathao_bearer_token', ?) ON CONFLICT(key) DO UPDATE SET value=?", (token, token), commit=True)
        return token
    except: return None

# =====================================================================
# ROUTES
# =====================================================================
@app.route("/")
def index(): return redirect("/admin")

@app.route("/admin")
def admin_portal():
    if not session.get("logged_in"): return redirect("/admin/login")
    tab, msg, phone = request.args.get("tab", "dashboard"), request.args.get("msg", ""), request.args.get("chat_with", "")
    orders = db_query("SELECT * FROM orders ORDER BY id DESC LIMIT 100", fetchall=True) or []
    users = db_query("SELECT * FROM users ORDER BY last_active DESC LIMIT 30", fetchall=True) or []
    products = db_query("SELECT * FROM products ORDER BY id DESC", fetchall=True) or []
    history = []
    if phone: history = db_query("SELECT * FROM messages WHERE from_number=? ORDER BY id DESC LIMIT 50", (phone,), fetchall=True) or []; history.reverse()
    return render_template(f"{tab}.html", settings=get_all_settings(), orders=orders, users=users, products=products, chat_history=history, active_chat=phone, msg=msg)

@app.route("/admin/chat/send", methods=["POST"])
def admin_send_message():
    p, m = request.form.get("phone"), request.form.get("message")
    if p and m:
        db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', ?)", (p, m, session.get("username")), commit=True)
        send_whatsapp_message(p, m)
    return redirect(f"/admin?tab=chat&chat_with={p}")

@app.route("/admin/chat/send-image", methods=["POST"])
def admin_send_image():
    p, img = request.form.get("phone"), request.files.get("image")
    if p and img:
        fn = secure_filename(f"{int(time.time())}_{img.filename}"); fp = os.path.join(MEDIA_FOLDER, fn); img.save(fp)
        mid = upload_media_to_whatsapp(fp, "image")
        if mid and send_whatsapp_media(p, mid, "image", request.form.get("caption", "")):
            db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', ?)", (p, f"[IMAGE:{fn}]", session.get("username")), commit=True)
    return redirect(f"/admin?tab=chat&chat_with={p}")

@app.route("/admin/chat/send-voice", methods=["POST"])
def admin_send_voice():
    p, v = request.form.get("phone"), request.files.get("voice")
    if p and v:
        fn = secure_filename(f"{int(time.time())}.webm"); fp = os.path.join(MEDIA_FOLDER, fn); v.save(fp)
        mid = upload_media_to_whatsapp(fp, "audio")
        if mid and send_whatsapp_media(p, mid, "audio"):
            db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', ?)", (p, "[VOICE MESSAGE]", session.get("username")), commit=True)
    return redirect(f"/admin?tab=chat&chat_with={p}")

@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    if request.method == "POST":
        u, p = request.form.get("username", "").strip(), request.form.get("password", "").strip()
        auth = db_query("SELECT * FROM agents WHERE username=? AND password=?", (u,p), fetchone=True)
        if auth: session["logged_in"], session["username"] = True, auth["username"]; return redirect("/admin?tab=dashboard")
    return render_template_string('<body style="background:#020617;color:white;display:flex;justify-content:center;align-items:center;height:100vh;font-family:sans-serif;"><form method="POST" style="background:#1e293b;padding:50px;border-radius:30px;text-align:center;max-width:400px;width:90%;"><h2 style="color:#6366f1;">DHAKA PRO ACCESS</h2><input name="username" placeholder="User" required style="width:100%;padding:10px;margin:10px 0;border-radius:10px;"><br><input name="password" type="password" placeholder="Pass" required style="width:100%;padding:10px;margin:10px 0;border-radius:10px;"><br><button style="width:100%;padding:10px;background:#6366f1;color:white;border:none;border-radius:10px;cursor:pointer;">ENTER</button></form></body>')

@app.route("/admin/logout")
def admin_logout(): session.clear(); return redirect("/admin/login")

@app.route("/admin/settings/save", methods=["POST"])
def save_settings():
    if not session.get("logged_in"): return redirect("/admin/login")
    for k, v in request.form.items(): db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?", (k, v, v), commit=True)
    return redirect("/admin?tab=settings&msg=Updated")

# =====================================================================
# WHATSAPP WEBHOOK
# =====================================================================
@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        s = get_all_settings()
        if request.args.get("hub.verify_token") == s.get("verify_token", "dhaka-exclusive-verify-2026"): return request.args.get("hub.challenge"), 200
        return "Fail", 403
    
    data = request.get_json(force=True, silent=True) or {}
    try:
        val = data['entry'][0]['changes'][0]['value']
        if 'messages' in val:
            msg = val['messages'][0]; p, body = msg['from'], msg.get('text', {}).get('body', '[Media Receive]')
            db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'inbound', 'whatsapp')", (p, body), commit=True)
            db_query("INSERT OR IGNORE INTO users (phone) VALUES (?)", (p,), commit=True)
            db_query("UPDATE users SET last_active=CURRENT_TIMESTAMP WHERE phone=?", (p,), commit=True)
            
            # AI Respond using separate file logic
            s = get_all_settings()
            reply = get_gemini_reply(body, p, db_query, s)
            if send_whatsapp_message(p, reply):
                db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', 'gemini_ai')", (p, reply), commit=True)
    except Exception as e: logger.error(f"Webhook Error: {e}")
    return "EVENT_RECEIVED", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
