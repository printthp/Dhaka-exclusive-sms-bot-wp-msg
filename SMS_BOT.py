import os
import sys
import json
import re
import sqlite3
import time
import hmac
import hashlib
import logging
import functools
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session
from threading import Thread, Lock
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
application = app

PERMANENT_TOKEN = os.environ.get("PERMANENT_TOKEN", "EAANtSb24BiwBRREXu8HztnpOLtamcKIvi09Qb24LiYax45S4aoYtFEVKEQZAxigfO2wbGf6RgHh51IURbQzKKrzPhkcprLxHpZBfOwxZAVCscdVOpjbapbS9sOLCIqZBM8tZAtSRRaVVYSTZBjUkkPZAQaLABSnG6cQcgQcwqZBC5I5yrB4cXgoUPDlzzn7HzUwsMAZDZD")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "1039959469208417")
GEMINI_KEY = os.environ.get("GEMINI_KEY", "AIzaSyCRZIRWSoenfhA33qr7rkzoa56Byun0IWU")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "dhakaex0020")
APP_SECRET = os.environ.get("APP_SECRET", "378c339366554565e35bc64dc6601a56")
ADMIN_NUMBERS_STR = os.environ.get("ADMIN_NUMBERS", "")
ADMIN_NUMBERS = [n.strip() for n in ADMIN_NUMBERS_STR.split(",") if n.strip()]
PATHAO_BASE_URL = os.environ.get("PATHAO_BASE_URL", "https://api-hermes.pathao.com")
PATHAO_STORE_ID = os.environ.get("PATHAO_STORE_ID", "333358")
PATHAO_CLIENT_ID = os.environ.get("PATHAO_CLIENT_ID", "openOlRa7A")
PATHAO_CLIENT_SECRET = os.environ.get("PATHAO_CLIENT_SECRET", "7clJGfV1jh5njQEuR5yepVXZ9nYAjGORhNCOjgzG")
PATHAO_MERCHANT_EMAIL = os.environ.get("PATHAO_MERCHANT_EMAIL", "cocid1000006@gmail.com")
PATHAO_MERCHANT_PASSWORD = os.environ.get("PATHAO_MERCHANT_PASSWORD", "trustedaA@2")
BUSINESS_NAME = os.environ.get("BUSINESS_NAME", "Dhaka Exclusive")
BUSINESS_HOURS = os.environ.get("BUSINESS_HOURS", "09:00-21:00")

DB_FILE = "bot_v3.db"
db_lock = Lock()

def init_db():
    try:
        with db_lock:
            conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            c = conn.cursor()
            tables = [
                "CREATE TABLE IF NOT EXISTS messages (msg_id TEXT PRIMARY KEY, from_number TEXT, content TEXT, msg_type TEXT DEFAULT 'text', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
                "CREATE TABLE IF NOT EXISTS sessions (phone TEXT PRIMARY KEY, state TEXT DEFAULT 'idle', context TEXT DEFAULT '{}', last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
                "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, name TEXT, address TEXT, city_id INTEGER DEFAULT 1, zone_id INTEGER DEFAULT 1, area_id INTEGER DEFAULT 1, product_id INTEGER, quantity INTEGER DEFAULT 1, price INTEGER, delivery_charge INTEGER DEFAULT 80, discount INTEGER DEFAULT 0, total INTEGER, payment_method TEXT DEFAULT 'cod', payment_status TEXT DEFAULT 'pending', pathao_consignment_id TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
                "CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, price INTEGER, description TEXT, stock INTEGER DEFAULT 0, active INTEGER DEFAULT 1, image_url TEXT DEFAULT '')",
                "CREATE TABLE IF NOT EXISTS users (phone TEXT PRIMARY KEY, name TEXT, language TEXT DEFAULT 'bn', first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP, total_orders INTEGER DEFAULT 0, total_spent INTEGER DEFAULT 0)",
                "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
                "CREATE TABLE IF NOT EXISTS pathao_webhook_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT, consignment_id TEXT, order_id TEXT, status TEXT, raw_payload TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
                "CREATE TABLE IF NOT EXISTS call_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, name TEXT, reason TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            ]
            for t in tables:
                c.execute(t)
            defaults = [("business_name", BUSINESS_NAME), ("logo_url", ""), ("primary_color", "#667eea"), ("header_color", "#1f2937"), ("accent_color", "#10b981")]
            for k, v in defaults:
                c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
            conn.commit()
            conn.close()
            logger.info("Database initialized successfully with call_requests support")
    except Exception as e:
        logger.error("Database init failed: %s", e)
        raise

try:
    init_db()
except Exception as e:
    logger.critical("Cannot start without database: %s", e)
    sys.exit(1)

def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        try:
            c.execute(query, params)
            if commit: conn.commit(); conn.close(); return True
            if fetchone: row = c.fetchone(); conn.close(); return dict(row) if row else None
            if fetchall: rows = c.fetchall(); conn.close(); return [dict(r) for r in rows]
            conn.close(); return None
        except Exception as e:
            logger.error("DB Error: %s | Query: %s", e, query)
            conn.close()
            raise

def format_phone(num):
    num = str(num).strip().replace(" ", "").replace("-", "").replace("+", "")
    if num.startswith("01") and len(num) == 11: num = "88" + num
    return num

def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        au = os.environ.get("ADMIN_PANEL_USER", "admin")
        ap = os.environ.get("ADMIN_PANEL_PASS", "admin123")
        if not auth or auth.username != au or auth.password != ap:
            return ('<h3>অননুমোদিত</h3>', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})
        return f(*args, **kwargs)
    return decorated

def get_setting(key, default=""):
    row = db_query("SELECT value FROM settings WHERE key = ?", (key,), fetchone=True)
    return row["value"] if row else default

def set_setting(key, value):
    db_query("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (key, value), commit=True)

def get_all_settings():
    rows = db_query("SELECT key, value FROM settings", fetchall=True) or []
    return {r["key"]: r["value"] for r in rows}

# =====================================================================
# PATHAO API
# =====================================================================
def get_pathao_token():
    s = get_all_settings()
    cid = s.get("pathao_client_id") or PATHAO_CLIENT_ID
    csec = s.get("pathao_client_secret") or PATHAO_CLIENT_SECRET
    email = s.get("pathao_merchant_email") or PATHAO_MERCHANT_EMAIL
    pwd = s.get("pathao_merchant_password") or PATHAO_MERCHANT_PASSWORD
    base = s.get("pathao_base_url") or PATHAO_BASE_URL
    if not all([cid, csec, email, pwd]): return None, "Pathao credentials missing"
    try:
        r = requests.post(f"{base}/aladdin/api/v1/issue-token", json={"client_id": cid, "client_secret": csec, "username": email, "password": pwd, "grant_type": "password"}, headers={"content-type": "application/json"}, timeout=15)
        d = r.json()
        if r.status_code == 200:
            token = d.get("token") or d.get("access_token") or d.get("data", {}).get("token")
            if token: return str(token), None
        return None, d.get("message", "Token failed")
    except Exception as e: return None, str(e)

def track_pathao_order(key):
    token, err = get_pathao_token()
    if not token: return f"Token Error: {err}"
    key = str(key).strip().replace("+", "")
    s = get_all_settings()
    base = s.get("pathao_base_url") or PATHAO_BASE_URL
    try:
        r = requests.get(f"{base}/aladdin/api/v1/orders/{key}/tracking", headers={"authorization": f"Bearer {token}", "accept": "application/json"}, timeout=15)
        d = r.json()
        if r.status_code == 200 and d.get("status") == 200:
            st = d.get("data", {}).get("order_status", "unknown").lower()
            mp = {"pending": "পেন্ডিং", "picked": "কুরিয়ারে হস্তান্তরিত", "in_transit": "ডেলিভারির পথে", "delivered": "ডেলিভারি সম্পন্ন", "cancelled": "বাতিল", "returned": "রিটার্ন"}
            return mp.get(st, st.upper())
        return "অর্ডার পাওয়া যায়নি।"
    except: return "ট্র্যাকিং ত্রুটি।"

def create_pathao_order(name, phone, address, city_id=1, zone_id=1, area_id=1, item_desc="", cod_amount=0):
    token, err = get_pathao_token()
    if not token: return False, err
    s = get_all_settings()
    base = s.get("pathao_base_url") or PATHAO_BASE_URL
    store = s.get("pathao_store_id") or PATHAO_STORE_ID
    phone = format_phone(phone)
    try:
        r = requests.post(f"{base}/aladdin/api/v1/orders", json={"store_id": int(store) if store else 0, "recipient_name": name, "recipient_phone": phone, "recipient_address": address, "recipient_city": int(city_id), "recipient_zone": int(zone_id), "recipient_area": int(area_id), "delivery_type": 48, "item_type": 2, "special_instruction": "WhatsApp Bot Order", "item_quantity": 1, "amount_to_collect": int(cod_amount), "item_description": item_desc}, headers={"authorization": f"Bearer {token}", "content-type": "application/json"}, timeout=15)
        d = r.json()
        if r.status_code == 200 and d.get("status") == 200: return True, d.get("data", {}).get("consignment_id")
        return False, d.get("message", r.text)
    except Exception as e: return False, str(e)

def get_pathao_cities():
    token, _ = get_pathao_token()
    if not token: return []
    try:
        s = get_all_settings()
        base = s.get("pathao_base_url") or PATHAO_BASE_URL
        r = requests.get(f"{base}/aladdin/api/v1/countries/1/city-list", headers={"authorization": f"Bearer {token}", "accept": "application/json"}, timeout=10)
        return r.json().get("data", {}).get("data", [])
    except: return []

def get_pathao_zones(cid):
    token, _ = get_pathao_token()
    if not token: return []
    try:
        s = get_all_settings()
        base = s.get("pathao_base_url") or PATHAO_BASE_URL
        r = requests.get(f"{base}/aladdin/api/v1/cities/{cid}/zone-list", headers={"authorization": f"Bearer {token}", "accept": "application/json"}, timeout=10)
        return r.json().get("data", {}).get("data", [])
    except: return []

def get_pathao_areas(zid):
    token, _ = get_pathao_token()
    if not token: return []
    try:
        s = get_all_settings()
        base = s.get("pathao_base_url") or PATHAO_BASE_URL
        r = requests.get(f"{base}/aladdin/api/v1/zones/{zid}/area-list", headers={"authorization": f"Bearer {token}", "accept": "application/json"}, timeout=10)
        return r.json().get("data", {}).get("data", [])
    except: return []

# =====================================================================
# WHATSAPP
# =====================================================================
def send_text(to, body):
    if not PERMANENT_TOKEN or not PHONE_NUMBER_ID: return False
    try:
        r = requests.post(f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages", json={"messaging_product": "whatsapp", "to": format_phone(to), "type": "text", "text": {"body": body}}, headers={"Authorization": f"Bearer {PERMANENT_TOKEN}", "Content-Type": "application/json"}, timeout=15)
        return r.status_code in (200, 201)
    except: return False

def send_buttons(to, body, buttons):
    if not PERMANENT_TOKEN or not PHONE_NUMBER_ID: return False
    try:
        r = requests.post(f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages", json={"messaging_product": "whatsapp", "to": format_phone(to), "type": "interactive", "interactive": {"type": "button", "body": {"text": body}, "action": {"buttons text": [{"type": "reply", "reply": {"id": b["id"], "title": b["title"]}} for b in buttons[:3]]}}}, headers={"Authorization": f"Bearer {PERMANENT_TOKEN}", "Content-Type": "application/json"}, timeout=15)
        return r.status_code in (200, 201)
    except: return False

def send_list_menu(to, body, btntext, sections):
    if not PERMANENT_TOKEN or not PHONE_NUMBER_ID: return False
    try:
        r = requests.post(f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages", json={"messaging_product": "whatsapp", "to": format_phone(to), "type": "interactive", "interactive": {"type": "list", "body": {"text": body}, "action": {"button": btntext, "sections": sections}}}, headers={"Authorization": f"Bearer {PERMANENT_TOKEN}", "Content-Type": "application/json"}, timeout=15)
        return r.status_code in (200, 201)
    except: return False

def send_image(to, image_url, caption=""):
    if not PERMANENT_TOKEN or not PHONE_NUMBER_ID: return False
    try:
        r = requests.post(f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages", json={"messaging_product": "whatsapp", "to": format_phone(to), "type": "image", "image": {"link": image_url, "caption": caption}}, headers={"Authorization": f"Bearer {PERMANENT_TOKEN}", "Content-Type": "application/json"}, timeout=15)
        return r.status_code in (200, 201)
    except: return False

# =====================================================================
# HELPERS
# =====================================================================
def get_products():
    try: return db_query("SELECT * FROM products WHERE active = 1 ORDER BY id DESC", fetchall=True)
    except: return []

def get_product_by_id(pid):
    try: return db_query("SELECT * FROM products WHERE id = ?", (pid,), fetchone=True)
    except: return None

def format_catalog():
    p = get_products()
    if not p: return "কোনো প্রোডাক্ট আপডেট হয়নি।"
    return "\n".join([f"🔹 {x['name']} — {x['price']}৳" for x in p])

def get_session(phone):
    try: return db_query("SELECT * FROM sessions WHERE phone = ?", (phone,), fetchone=True)
    except: return None

def set_session(phone, state, ctx=None):
    try:
        c = json.dumps(ctx or {}, ensure_ascii=False)
        e = get_session(phone)
        if e: db_query("UPDATE sessions SET state = ?, context = ?, last_active = CURRENT_TIMESTAMP WHERE phone = ?", (state, c, phone), commit=True)
        else: db_query("INSERT INTO sessions (phone, state, context) VALUES (?, ?, ?)", (phone, state, c), commit=True)
    except: pass

def get_context(phone):
    try:
        s = get_session(phone)
        return json.loads(s["context"]) if s and s.get("context") else {}
    except: return {}

def update_context(phone, key, value):
    try:
        s = get_session(phone)
        ctx = json.loads(s["context"]) if s and s.get("context") else {}
        ctx[key] = value
        set_session(phone, s["state"] if s else "idle", ctx)
    except: pass

def ensure_user(phone):
    try:
        u = db_query("SELECT * FROM users WHERE phone = ?", (phone,), fetchone=True)
        if not u: db_query("INSERT OR IGNORE INTO users (phone) VALUES (?)", (phone,), commit=True)
        else: db_query("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE phone = ?", (phone,), commit=True)
    except: pass

def log_message(msg_id, phone, content, msg_type="text"):
    try: db_query("INSERT OR IGNORE INTO messages (msg_id, from_number, content, msg_type) VALUES (?, ?, ?, ?)", (msg_id, phone, content, msg_type), commit=True)
    except: pass

def is_rate_limited(phone):
    try:
        ago = (datetime.utcnow() - timedelta(minutes=1)).isoformat()
        c = db_query("SELECT COUNT(*) as cnt FROM messages WHERE from_number = ? AND created_at > ?", (phone, ago), fetchone=True)
        return c and c["cnt"] >= 10
    except: return False

def verify_meta_signature(payload, signature):
    if not APP_SECRET: return True
    if not signature: return False
    sha_name, signature_val = signature.split('=')
    if sha_name != 'sha256': return False
    mac = hmac.new(APP_SECRET.encode('utf-8'), payload, hashlib.sha256)
    return hmac.compare_digest(mac.hexdigest(), signature_val)

# =====================================================================
# AI
# =====================================================================
genai_available = False
client = None
MODEL_NAME = "gemini-2.5-flash"
try:
    from google import genai
    from google.genai import types
    if GEMINI_KEY:
        client = genai.Client(api_key=GEMINI_KEY)
        genai_available = True
        logger.info("Gemini loaded")
    else: logger.warning("GEMINI_KEY missing")
except Exception as e: logger.error("Gemini import failed: %s", e)

def get_ai_answer(user_query, session_context=None):
    if not genai_available or not client: return "দুঃখিত প্রিয় গ্রাহক, এখন AI সার্ভিস অফলাইন।"
    try:
        si = "You are the AI sales assistant for 'Dhaka Exclusive' (Bangladesh).\n1. NEVER say 'নমস্কার'. ALWAYS 'প্রিয় গ্রাহক'.\n2. Short, polite, Bengali replies. Taka only.\n3. You CAN track orders and take orders.\n\nPRODUCTS:\n" + format_catalog()
        cfg = types.GenerateContentConfig(system_instruction=si, temperature=0.15, max_output_tokens=500)
        return client.models.generate_content(model=MODEL_NAME, contents=user_query, config=cfg).text
    except: return "দুঃখিত প্রিয় গ্রাহক, সিস্টেম ব্যস্ত।"

# =====================================================================
# MAIN PROCESSOR
# =====================================================================
def process_webhook_async(msg, from_number):
    msg_type = msg.get("type")
    msg_id = msg.get("id")
    try:
        if db_query("SELECT 1 FROM messages WHERE msg_id = ?", (msg_id,), fetchone=True): return
    except: pass
    log_message(msg_id, from_number, str(msg), msg_type)
    ensure_user(from_number)
    try:
        if is_rate_limited(from_number):
            send_text(from_number, "প্রিয় গ্রাহক, অনেক মেসেজ পাঠিয়েছেন। কিছুক্ষণ অপেক্ষা করুন।")
            return
    except: pass
    if msg_type in ["audio", "voice"]:
        send_text(from_number, "প্রিয় গ্রাহক, ভয়েস মেসেজ সাপোর্টেড নয়।")
        return
    if msg_type == "image":
        cap = msg.get("image", {}).get("caption", "").lower()
        if any(k in cap for k in ["কত", "দাম", "কিনব", "চাই", "price"]): send_text(from_number, "📸 প্রোডাক্ট ছবি পেয়েছি! আমাদের ক্যাটালগ দেখতে 'কিনব' লিখুন।")
        elif any(k in cap for k in ["পেমেন্ট", "টাকা", "bkash", "nagad", "paid"]): send_text(from_number, "💳 পেমেন্ট রিসিপ্ট পেয়েছি! আপনার অর্ডার আইডি দিন।")
        else: send_text(from_number, "📸 ছবি পেয়েছি! প্রোডাক্ট কিনতে চাইলে 'কিনব' লিখুন।")
        return
    if msg_type != "text":
        send_text(from_number, "প্রিয় গ্রাহক, শুধু টেক্সট বুঝি।")
        return
    user_text = msg["text"]["body"].strip()
    session = get_session(from_number)
    state = session["state"] if session else "idle"
    context = get_context(from_number)

    if any(k in user_text.lower() for k in ["কল চাই", "call", "ফোন", "phone", "প্রতিনিধি", "agent", "কথা বলতে চাই", "মানুষের সাথে"]):
        user = db_query("SELECT name FROM users WHERE phone = ?", (from_number,), fetchone=True)
        name = user["name"] if user else "অজানা"
        db_query("INSERT INTO call_requests (phone, name, reason, status) VALUES (?, ?, ?, ?)", (from_number, name, user_text[:100], "pending"), commit=True)
        send_text(from_number, "📞 প্রিয় গ্রাহক, আপনার কল রিকোয়েস্ট গ্রহণ করা হয়েছে!\n\nআমাদের প্রতিনিধি শীঘ্রই আপনাকে কল ব্যাক করবেন (সাধারণত ১০-৩০ মিনিটের মধ্যে)।\n\nজরুরি প্রয়োজনে সরাসরি কল করুন:\n📲 ০১৭৪২২৬৭৮৫৬")
        for admin in ADMIN_NUMBERS:
            send_text(admin, f"📞 নতুন কল রিকোয়েস্ট!\n👤 {name}\n📱 {from_number}\n📝 কারণ: {user_text[:80]}\n\nএডমিন প্যানেলে দেখুন: /admin")
        return

    if user_text.lower().startswith("admin:"):
        if from_number not in ADMIN_NUMBERS:
            send_text(from_number, "দুঃখিত, এই কমান্ড শুধু অ্যাডমিনের জন্য।")
            return
        cmd = user_text[6:].strip()
        if cmd.lower().startswith("help"):
            send_text(from_number, "🔧 অ্যাডমিন কমান্ড:\nadmin:stats\nadmin:broadcast মেসেজ\nadmin:calls")
            return
        if cmd.lower().startswith("calls"):
            calls = db_query("SELECT * FROM call_requests WHERE status = 'pending' ORDER BY created_at DESC LIMIT 5", fetchall=True) or []
            if not calls:
                send_text(from_number, "📞 কোনো পেন্ডিং কল রিকোয়েস্ট নেই।")
                return
            msg = "📞 পেন্ডিং কল রিকোয়েস্ট:\n"
            for c in calls:
                msg += f"\n👤 {c['name'] or 'N/A'}\n📱 {c['phone']}\n📝 {c['reason'] or 'N/A'}\n⏰ {c['created_at']}\n---"
            send_text(from_number, msg)
            return
        send_text(from_number, "অজানা কমান্ড। admin:help লিখুন।")
        return

    if any(k in user_text.lower() for k in ["তুমি কি কি পারো", "what can you do", "কি কি পারো", "তোমার কাজ কি"]):
        send_text(from_number, "🙋‍♂️ প্রিয় গ্রাহক, আমি আপনাকে সাহায্য করতে পারি:\n\n1️⃣ 🛒 প্রোডাক্ট অর্ডার করতে\n2️⃣ 📦 আপনার অর্ডার ট্র্যাক করতে\n3️⃣ 💰 প্রোডাক্টের দাম ও তথ্য জানতে\n\nকীভাবে সাহায্য করতে পারি?")
        return

    if any(k in user_text.lower() for k in ["অর্ডার কোথায়", "আমার অর্ডার", "ট্র্যাক", "track", "কোথায় আছে", "ডেলিভারি কোথায়"]):
        orders = db_query("SELECT * FROM orders WHERE phone = ? ORDER BY created_at DESC LIMIT 1", (from_number,), fetchone=True)
        if orders and orders.get("pathao_consignment_id"):
            live = track_pathao_order(orders["pathao_consignment_id"])
            send_text(from_number, f"📦 আপনার সর্বশেষ অর্ডার (#{orders['id']}):\n\n📌 স্ট্যাটাস: {live}\n🆔 Tracking: {orders['pathao_consignment_id']}")
        else:
            send_text(from_number, "📦 অর্ডার ট্র্যাক করতে আপনার ফোন নম্বর বা Tracking ID দিন:\n(যেমন: 01712XXXXXX)")
        return

    clean = user_text.replace(" ", "").replace("+", "").strip()
    if clean.isdigit() and (len(clean) == 11 or len(clean) == 13) and clean.startswith(("01", "8801")):
        live = track_pathao_order(clean)
        send_text(from_number, f"প্রিয় গ্রাহক, আপনার অর্ডারের অবস্থা:\n\n📌 {live}")
        return

    if state == "idle" and any(k in user_text.lower() for k in ["কিনব", "অর্ডার", "চাই", "buy", "order"]):
        products = get_products()
        if products:
            sections = [{"title": "আমাদের প্রোডাক্ট", "rows": [{"id": f"product_{p['id']}", "title": p['name'][:24], "description": f"{p['price']}৳ | স্টক: {p['stock']}"} for p in products[:10]]}]
            set_session(from_number, "selecting_product", {})
            send_list_menu(from_number, "কোন প্রোডাক্ট কিনতে চান?", "প্রোডাক্ট", sections)
            return

    if state == "selecting_product":
        if user_text.startswith("product_"):
            pid = int(user_text.replace("product_", ""))
            product = get_product_by_id(pid)
            if product:
                ctx = {"product_id": pid, "product_name": product["name"], "price": product["price"]}
                set_session(from_number, "selecting_qty", ctx)
                if product.get("image_url"): send_image(from_number, product["image_url"], f"🔹 {product['name']}\n💰 {product['price']}৳")
                send_buttons(from_number, f"🔹 {product['name']}\n💰 {product['price']}৳\n\nকতটি চান?", [{"id": "qty_1", "title": "১টি"}, {"id": "qty_2", "title": "২টি"}, {"id": "qty_3", "title": "৩টি"}])
                return
        send_text(from_number, "অনুগ্রহ করে লিস্ট থেকে প্রোডাক্ট বাছাই করুন।")
        return

    if state == "selecting_qty":
        qm = {"qty_1": 1, "qty_2": 2, "qty_3": 3, "1": 1, "2": 2, "3": 3, "১": 1, "২": 2, "৩": 3}
        qty = qm.get(user_text, 1)
        ctx = get_context(from_number)
        ctx["quantity"] = qty; ctx["subtotal"] = ctx["price"] * qty
        set_session(from_number, "awaiting_name", ctx)
        send_text(from_number, f"✅ {qty}টি '{ctx['product_name']}'। আপনার সম্পূর্ণ নাম:")
        return

    if state == "awaiting_name":
        update_context(from_number, "name", user_text)
        set_session(from_number, "awaiting_phone", get_context(from_number))
        send_text(from_number, "ধন্যবাদ! এখন ১১ সংখ্যার মোবাইল নম্বর (যেমন: 01712XXXXXX):")
        return

    if state == "awaiting_phone":
        c = user_text.replace(" ", "").replace("+", "").replace("-", "")
        if not (c.startswith("01") and len(c) == 11):
            send_text(from_number, "❌ সঠিক বাংলাদেশি নম্বর দিন (যেমন: 01712XXXXXX):")
            return
        update_context(from_number, "phone", c)
        set_session(from_number, "awaiting_address", get_context(from_number))
        send_text(from_number, "অসাধারণ! সম্পূর্ণ ডেলিভারি ঠিকানা:")
        return

    if state == "awaiting_address":
        update_context(from_number, "address", user_text)
        ctx = get_context(from_number)
        cities = get_pathao_cities()
        if cities:
            sections = [{"title": "শহর", "rows": [{"id": f"city_{c['city_id']}", "title": c['city_name'][:24]} for c in cities[:10]]}]
            set_session(from_number, "selecting_city", ctx)
            send_list_menu(from_number, "ডেলিভারির জন্য শহর বাছাই করুন:", "শহর", sections)
            return
        ctx["city_id"] = 1
        set_session(from_number, "selecting_payment", ctx)
        send_payment_options(from_number, ctx)
        return

    if state == "selecting_city":
        if user_text.startswith("city_"):
            cid = int(user_text.replace("city_", ""))
            ctx = get_context(from_number)
            ctx["city_id"] = cid
            zones = get_pathao_zones(cid)
            if zones:
                sections = [{"title": "জোন", "rows": [{"id": f"zone_{z['zone_id']}", "title": z['zone_name'][:24]} for z in zones[:10]]}]
                set_session(from_number, "selecting_zone", ctx)
                send_list_menu(from_number, "জোন বাছাই করুন:", "জোন", sections)
                return
            ctx["zone_id"] = 1; ctx["area_id"] = 1
            set_session(from_number, "selecting_payment", ctx)
            send_payment_options(from_number, ctx)
            return
        send_text(from_number, "অনুগ্রহ করে লিস্ট থেকে শহর বাছাই করুন।")
        return

    if state == "selecting_zone":
        if user_text.startswith("zone_"):
            zid = int(user_text.replace("zone_", ""))
            ctx = get_context(from_number)
            ctx["zone_id"] = zid
            areas = get_pathao_areas(zid)
            if areas:
                sections = [{"title": "এরিয়া", "rows": [{"id": f"area_{a['area_id']}", "title": a['area_name'][:24]} for a in areas[:10]]}]
                set_session(from_number, "selecting_area", ctx)
                send_list_menu(from_number, "এরিয়া বাছাই করুন:", "এরিয়া", sections)
                return
            ctx["area_id"] = 1
            set_session(from_number, "selecting_payment", ctx)
            send_payment_options(from_number, ctx)
            return
        send_text(from_number, "অনুগ্রহ করে লিস্ট থেকে জোন বাছাই করুন।")
        return

    if state == "selecting_area":
        if user_text.startswith("area_"):
            aid = int(user_text.replace("area_", ""))
            ctx = get_context(from_number)
            ctx["area_id"] = aid
            set_session(from_number, "selecting_payment", ctx)
            send_payment_options(from_number, ctx)
            return
        send_text(from_number, "অনুগ্রহ করে লিস্ট থেকে এরিয়া বাছাই করুন।")
        return

    if state == "selecting_payment":
        ctx = get_context(from_number)
        ctx["payment_method"] = "cod"; ctx["delivery_charge"] = 80; ctx["total"] = ctx["subtotal"] + 80
        set_session(from_number, "awaiting_confirmation", ctx)
        summary = f"📦 ফাইনাল অর্ডার\n━━━━━━━━━━━━━━\n🔹 {ctx['product_name']} x {ctx['quantity']}\n💰 প্রাইস: {ctx['subtotal']}৳\n🚚 ডেলিভারি: {ctx['delivery_charge']}৳\n━━━━━━━━━━━━━━\n💵 মোট: {ctx['total']}৳\n👤 {ctx['name']}\n📞 {ctx['phone']}\n📍 {ctx['address']}\n\nঅর্ডার কনফার্ম করতে 'হ্যাঁ' লিখুন।"
        send_buttons(from_number, summary, [{"id": "confirm_yes", "title": "✅ হ্যাঁ"}, {"id": "confirm_no", "title": "❌ না"}])
        return

    if state == "awaiting_confirmation":
        if user_text in ["হ্যাঁ", "yes", "confirm_yes", "✅ হ্যাঁ"]:
            ctx = get_context(from_number)
            cod = ctx["total"] if ctx.get("payment_method") == "cod" else 0
            success, result = create_pathao_order(name=ctx.get("name"), phone=ctx.get("phone"), address=ctx.get("address"), city_id=ctx.get("city_id", 1), zone_id=ctx.get("zone_id", 1), area_id=ctx.get("area_id", 1), item_desc=f"{ctx['product_name']} x{ctx['quantity']}", cod_amount=cod)
            if success:
                db_query("INSERT INTO orders (phone, name, address, city_id, zone_id, area_id, product_id, quantity, price, delivery_charge, discount, total, payment_method, pathao_consignment_id, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (ctx.get("phone"), ctx.get("name"), ctx.get("address"), ctx.get("city_id", 1), ctx.get("zone_id", 1), ctx.get("area_id", 1), ctx.get("product_id"), ctx.get("quantity"), ctx.get("subtotal"), ctx.get("delivery_charge", 80), ctx.get("discount", 0), ctx.get("total"), ctx.get("payment_method", "cod"), str(result), "created"), commit=True)
                db_query("UPDATE users SET total_orders = total_orders + 1, total_spent = total_spent + ? WHERE phone = ?", (ctx.get("total", 0), from_number), commit=True)
                send_text(from_number, f"🎉 অর্ডার সফল!\n📦 Tracking: {result}\n🚚 পাঠাও কুরিয়ার আসবে।\nধন্যবাদ প্রিয় গ্রাহক! 🙏")
            else:
                db_query("INSERT INTO orders (phone, name, address, city_id, zone_id, area_id, product_id, quantity, price, delivery_charge, discount, total, payment_method, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (ctx.get("phone"), ctx.get("name"), ctx.get("address"), ctx.get("city_id", 1), ctx.get("zone_id", 1), ctx.get("area_id", 1), ctx.get("product_id"), ctx.get("quantity"), ctx.get("subtotal"), ctx.get("delivery_charge", 80), ctx.get("discount", 0), ctx.get("total"), ctx.get("payment_method", "cod"), "manual_pending"), commit=True)
                send_text(from_number, f"⚠️ কুরিয়ার API ত্রুটি: {result}\nঅর্ডার ম্যানুয়ালি নোট। প্রতিনিধি কল করে কনফার্ম করবেন।")
            set_session(from_number, "idle", {})
            return
        else:
            send_text(from_number, "অর্ডার বাতিল। আপনাকে কীভাবে সাহায্য করতে পারি?")
            set_session(from_number, "idle", {})
            return

    ai_response = get_ai_answer(user_text, context)
    if any(k in user_text.lower() for k in ["কিনব", "অর্ডার", "চাই", "buy", "order", "দাম"]):
        products = get_products()
        if products:
            sections = [{"title": "প্রোداشت", "rows": [{"id": f"product_{p['id']}", "title": p['name'][:24], "description": f"{p['price']}৳"} for p in products[:10]]}]
            set_session(from_number, "selecting_product", {})
            send_list_menu(from_number, "কোন প্রোডাক্টটি দেখতে চান?", "প্রোডাক্ট", sections)
            return
    send_text(from_number, ai_response)

def send_payment_options(to, ctx):
    sub = ctx.get("subtotal", 0)
    send_buttons(to, f"💰 সাবটোটাল: {sub}৳\n\nপেমেন্ট মেথড:", [{"id": "pay_cod", "title": "💵 COD"}, {"id": "pay_bkash", "title": "📱 bKash"}, {"id": "pay_nagad", "title": "💳 Nagad"}])


# =====================================================================
# UPDATED ADMIN PANEL VIEWS & DASHBOARD UI HTML
# =====================================================================

ADMIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ settings.get('business_name', 'Admin Panel') }} - Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
</head>
<body class="bg-gray-50 font-sans">
    <div class="min-h-screen flex">
        <!-- Sidebar -->
        <div class="w-64 bg-slate-900 text-white flex flex-col">
            <div class="p-5 text-xl font-bold border-b border-slate-800 flex items-center gap-2">
                <i class="fa-solid fa-robot text-emerald-400"></i>
                <span>{{ settings.get('business_name', 'Dhaka Exclusive') }}</span>
            </div>
            <nav class="flex-1 p-4 space-y-2">
                <a href="/admin" class="flex items-center gap-3 px-4 py-2.5 rounded-lg bg-emerald-600 text-white font-medium">
                    <i class="fa-solid fa-chart-pie w-5"></i> Dashboard
                </a>
                <a href="#orders" class="flex items-center gap-3 px-4 py-2.5 rounded-lg text-slate-300 hover:bg-slate-800 hover:text-white transition">
                    <i class="fa-solid fa-box w-5"></i> Orders
                </a>
                <a href="#calls" class="flex items-center gap-3 px-4 py-2.5 rounded-lg text-slate-300 hover:bg-slate-800 hover:text-white transition">
                    <i class="fa-solid fa-headset w-5"></i> Call Requests
                </a>
                <a href="#settings" class="flex items-center gap-3 px-4 py-2.5 rounded-lg text-slate-300 hover:bg-slate-800 hover:text-white transition">
                    <i class="fa-solid fa-sliders w-5"></i> System Settings
                </a>
            </nav>
            <div class="p-4 border-t border-slate-800 text-xs text-slate-500 text-center">
                v4.0-Dynamic &copy; 2026
            </div>
        </div>

        <!-- Main Content -->
        <div class="flex-1 flex flex-col max-h-screen overflow-y-auto p-8">
            <div class="flex justify-between items-center mb-8">
                <div>
                    <h1 class="text-2xl font-bold text-slate-800">Overview Dashboard</h1>
                    <p class="text-sm text-slate-500">Live summary of your WhatsApp Bot & Pathao Orders</p>
                </div>
                <div class="flex items-center gap-4 bg-white px-4 py-2 rounded-xl shadow-sm border">
                    <span class="w-2.5 h-2.5 bg-emerald-500 rounded-full animate-pulse"></span>
                    <span class="text-sm font-medium text-slate-600">Bot Service: Active</span>
                </div>
            </div>

            <!-- Stats Grid -->
            <div class="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-100 flex items-center justify-between">
                    <div>
                        <p class="text-sm text-slate-500 font-medium">Total Orders</p>
                        <h3 class="text-2xl font-bold text-slate-800 mt-1">{{ stats.total_orders }}</h3>
                    </div>
                    <div class="w-12 h-12 bg-blue-50 text-blue-600 rounded-xl flex items-center justify-center text-lg"><i class="fa-solid fa-shopping-cart"></i></div>
                </div>
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-100 flex items-center justify-between">
                    <div>
                        <p class="text-sm text-slate-500 font-medium">Total Revenue</p>
                        <h3 class="text-2xl font-bold text-slate-800 mt-1">{{ stats.total_revenue or 0 }}৳</h3>
                    </div>
                    <div class="w-12 h-12 bg-emerald-50 text-emerald-600 rounded-xl flex items-center justify-center text-lg"><i class="fa-solid fa-bangladeshi-taka-sign"></i></div>
                </div>
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-100 flex items-center justify-between">
                    <div>
                        <p class="text-sm text-slate-500 font-medium">Pending Calls</p>
                        <h3 class="text-2xl font-bold text-slate-800 mt-1">{{ stats.pending_calls }}</h3>
                    </div>
                    <div class="w-12 h-12 bg-amber-50 text-amber-600 rounded-xl flex items-center justify-center text-lg"><i class="fa-solid fa-phone-volume"></i></div>
                </div>
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-100 flex items-center justify-between">
                    <div>
                        <p class="text-sm text-slate-500 font-medium">Active Products</p>
                        <h3 class="text-2xl font-bold text-slate-800 mt-1">{{ stats.total_products }}</h3>
                    </div>
                    <div class="w-12 h-12 bg-purple-50 text-purple-600 rounded-xl flex items-center justify-center text-lg"><i class="fa-solid fa-boxes-stacked"></i></div>
                </div>
            </div>

            <!-- Sections Container -->
            <div class="space-y-8">
                <!-- Call Requests Table -->
                <div id="calls" class="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
                    <div class="p-6 border-b border-slate-100 bg-slate-50/50">
                        <h2 class="text-lg font-bold text-slate-800 flex items-center gap-2"><i class="fa-solid fa-headset text-amber-500"></i> Active Call Back Requests</h2>
                    </div>
                    <div class="overflow-x-auto">
                        <table class="w-full text-left border-collapse">
                            <thead>
                                <tr class="bg-slate-50 text-xs font-semibold text-slate-600 uppercase tracking-wider border-b border-slate-100">
                                    <th class="p-4">Customer</th>
                                    <th class="p-4">Phone</th>
                                    <th class="p-4">Reason/Query</th>
                                    <th class="p-4">Time</th>
                                    <th class="p-4">Action</th>
                                </tr>
                            </thead>
                            <tbody class="text-sm text-slate-700 divide-y divide-slate-100">
                                {% for call in calls %}
                                <tr class="hover:bg-slate-50/80 transition">
                                    <td class="p-4 font-medium text-slate-900">{{ call.name }}</td>
                                    <td class="p-4">{{ call.phone }}</td>
                                    <td class="p-4 text-slate-500 italic">"{{ call.reason }}"</td>
                                    <td class="p-4 text-xs text-slate-400">{{ call.created_at }}</td>
                                    <td class="p-4">
                                        <form action="/admin/call/complete/{{ call.id }}" method="POST" class="inline">
                                            <button type="submit" class="bg-emerald-50 text-emerald-600 border border-emerald-200 px-3 py-1 rounded-lg text-xs font-medium hover:bg-emerald-600 hover:text-white transition">
                                                <i class="fa-solid fa-check mr-1"></i> Done
                                            </button>
                                        </form>
                                    </td>
                                </tr>
                                {% else %}
                                <tr>
                                    <td colspan="5" class="p-8 text-center text-slate-400">কোনো পেন্ডিং কল রিকোয়েস্ট নেই।</td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- Recent Orders Table -->
                <div id="orders" class="bg-white rounded-2xl shadow-sm border border-slate-100 overflow-hidden">
                    <div class="p-6 border-b border-slate-100 bg-slate-50/50">
                        <h2 class="text-lg font-bold text-slate-800 flex items-center gap-2"><i class="fa-solid fa-receipt text-blue-500"></i> Recent Orders</h2>
                    </div>
                    <div class="overflow-x-auto">
                        <table class="w-full text-left border-collapse">
                            <thead>
                                <tr class="bg-slate-50 text-xs font-semibold text-slate-600 uppercase tracking-wider border-b border-slate-100">
                                    <th class="p-4">Order ID</th>
                                    <th class="p-4">Customer</th>
                                    <th class="p-4">Details</th>
                                    <th class="p-4">Total Amount</th>
                                    <th class="p-4">Pathao Consignment</th>
                                    <th class="p-4">Status</th>
                                </tr>
                            </thead>
                            <tbody class="text-sm text-slate-700 divide-y divide-slate-100">
                                {% for order in orders %}
                                <tr class="hover:bg-slate-50/80 transition">
                                    <td class="p-4 font-semibold text-blue-600">#{{ order.id }}</td>
                                    <td class="p-4">
                                        <div class="font-medium text-slate-900">{{ order.name }}</div>
                                        <div class="text-xs text-slate-400">{{ order.phone }}</div>
                                    </td>
                                    <td class="p-4">
                                        <div class="text-slate-800">Product ID: {{ order.product_id }} (Qty: {{ order.quantity }})</div>
                                        <div class="text-xs text-slate-400 max-w-xs truncate">{{ order.address }}</div>
                                    </td>
                                    <td class="p-4 font-medium">{{ order.total }}৳</td>
                                    <td class="p-4">
                                        {% if order.pathao_consignment_id %}
                                        <span class="bg-slate-100 text-slate-800 font-mono px-2 py-1 rounded text-xs border">{{ order.pathao_consignment_id }}</span>
                                        {% else %}
                                        <span class="text-xs text-amber-500 font-medium"><i class="fa-solid fa-triangle-exclamation"></i> Manual Entry</span>
                                        {% endif %}
                                    </td>
                                    <td class="p-4">
                                        <span class="px-2.5 py-1 rounded-full text-xs font-medium 
                                            {% if order.status in ['created', 'pending'] %} bg-blue-50 text-blue-600
                                            {% elif order.status == 'manual_pending' %} bg-amber-50 text-amber-600
                                            {% else %} bg-emerald-50 text-emerald-600 {% endif %}">
                                            {{ order.status }}
                                        </span>
                                    </td>
                                </tr>
                                {% else %}
                                <tr>
                                    <td colspan="6" class="p-8 text-center text-slate-400">এখনো কোনো অর্ডার পাওয়া যায়নি।</td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>

                <!-- Configuration Settings Form -->
                <div id="settings" class="bg-white rounded-2xl shadow-sm border border-slate-100">
                    <div class="p-6 border-b border-slate-100 bg-slate-50/50">
                        <h2 class="text-lg font-bold text-slate-800 flex items-center gap-2"><i class="fa-solid fa-sliders text-emerald-500"></i> Live Dynamic Configuration</h2>
                        <p class="text-xs text-slate-400 mt-1">কোর সেটিংস ও আইডি সরাসরি এখান থেকে ব্রাউজারে আপডেট করতে পারবেন।</p>
                    </div>
                    <form action="/admin/settings/save" method="POST" class="p-6 grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div>
                            <label class="block text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2">Business Name</label>
                            <input type="text" name="business_name" value="{{ settings.get('business_name', '') }}" class="w-full px-4 py-2.5 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500">
                        </div>
                        <div>
                            <label class="block text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2">Pathao Client ID</label>
                            <input type="text" name="pathao_client_id" value="{{ settings.get('pathao_client_id', '') }}" class="w-full px-4 py-2.5 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500">
                        </div>
                        <div>
                            <label class="block text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2">Pathao Store ID</label>
                            <input type="text" name="pathao_store_id" value="{{ settings.get('pathao_store_id', '') }}" class="w-full px-4 py-2.5 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500">
                        </div>
                        <div>
                            <label class="block text-xs font-semibold uppercase tracking-wider text-slate-500 mb-2">Pathao Base URL</label>
                            <input type="text" name="pathao_base_url" value="{{ settings.get('pathao_base_url', 'https://api-hermes.pathao.com') }}" class="w-full px-4 py-2.5 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-emerald-500/20 focus:border-emerald-500">
                        </div>
                        <div class="md:col-span-2 flex justify-end border-t pt-4 mt-2">
                            <button type="submit" class="bg-slate-900 text-white font-medium px-6 py-2.5 rounded-xl hover:bg-slate-800 transition shadow-sm">
                                <i class="fa-solid fa-floppy-disk mr-2"></i> Save Configurations
                            </button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    </div>
</body>
</html>
"""

# =====================================================================
# FLASK ROUTES
# =====================================================================
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "running", "service": f"{BUSINESS_NAME} WhatsApp Bot", "version": "4.0-dynamic", "timestamp": datetime.utcnow().isoformat()})

@app.route("/health", methods=["GET"])
def health():
    try: db_query("SELECT 1", fetchone=True); return jsonify({"status": "healthy", "database": True})
    except: return jsonify({"status": "unhealthy", "database": False})

@app.route("/admin", methods=["GET"])
@login_required
def admin_dashboard():
    total_orders = db_query("SELECT COUNT(*) as cnt FROM orders", fetchone=True)["cnt"]
    total_revenue = db_query("SELECT SUM(total) as rev FROM orders WHERE status NOT IN ('cancelled', 'returned')", fetchone=True)["rev"]
    pending_calls = db_query("SELECT COUNT(*) as cnt FROM call_requests WHERE status = 'pending'", fetchone=True)["cnt"]
    total_products = db_query("SELECT COUNT(*) as cnt FROM products WHERE active = 1", fetchone=True)["cnt"]
    
    stats = {
        "total_orders": total_orders,
        "total_revenue": total_revenue,
        "pending_calls": pending_calls,
        "total_products": total_products
    }
    
    calls = db_query("SELECT * FROM call_requests WHERE status = 'pending' ORDER BY created_at DESC", fetchall=True) or []
    orders = db_query("SELECT * FROM orders ORDER BY created_at DESC LIMIT 10", fetchall=True) or []
    settings = get_all_settings()
    
    return render_template_string(ADMIN_HTML, stats=stats, calls=calls, orders=orders, settings=settings)

@app.route("/admin/call/complete/<int:call_id>", methods=["POST"])
@login_required
def complete_call(call_id):
    db_query("UPDATE call_requests SET status = 'completed' WHERE id = ?", (call_id,), commit=True)
    return redirect(url_for('admin_dashboard'))

@app.route("/admin/settings/save", methods=["POST"])
@login_required
def save_settings():
    for key, value in request.form.items():
        if value.strip():
            set_setting(key, value.strip())
    return redirect(url_for('admin_dashboard'))

@app.route("/webhook", methods=["GET"])
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN: return challenge, 200
    return "Verification failed", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    signature = request.headers.get("X-Hub-Signature-256", "")
    payload = request.get_data()
    if not verify_meta_signature(payload, signature): return "Invalid signature", 403
    data = request.get_json(silent=True) or {}
    try:
        entry = data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        if "messages" in value:
            msg = value["messages"][0]
            from_number = msg.get("from")
            Thread(target=process_webhook_async, args=(msg, from_number)).start()
    except Exception as e:
        logger.error("Webhook main structure parse failed: %s", e)
    return "EVENT_RECEIVED", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
