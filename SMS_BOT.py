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
                "CREATE TABLE IF NOT EXISTS pathao_webhook_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT, consignment_id TEXT, order_id TEXT, status TEXT, raw_payload TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            ]
            for t in tables:
                c.execute(t)
            defaults = [("business_name", BUSINESS_NAME), ("logo_url", ""), ("primary_color", "#667eea"), ("header_color", "#1f2937"), ("accent_color", "#10b981")]
            for k, v in defaults:
                c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
            conn.commit()
            conn.close()
            logger.info("Database initialized")
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
        r = requests.post(f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages", json={"messaging_product": "whatsapp", "to": format_phone(to), "type": "interactive", "interactive": {"type": "button", "body": {"text": body}, "action": {"buttons": [{"type": "reply", "reply": {"id": b["id"], "title": b["title"]}} for b in buttons[:3]]}}}, headers={"Authorization": f"Bearer {PERMANENT_TOKEN}", "Content-Type": "application/json"}, timeout=15)
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

    if user_text.lower().startswith("admin:"):
        if from_number not in ADMIN_NUMBERS:
            send_text(from_number, "দুঃখিত, এই কমান্ড শুধু অ্যাডমিনের জন্য।")
            return
        cmd = user_text[6:].strip()
        if cmd.lower().startswith("help"):
            send_text(from_number, "🔧 অ্যাডমিন কমান্ড:\nadmin:stats\nadmin:broadcast মেসেজ")
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
            sections = [{"title": "প্রোডাক্ট", "rows": [{"id": f"product_{p['id']}", "title": p['name'][:24], "description": f"{p['price']}৳"} for p in products[:10]]}]
            set_session(from_number, "selecting_product", {})
            send_list_menu(from_number, "কোন প্রোডাক্টটি দেখতে চান?", "প্রোডাক্ট", sections)
            return
    send_text(from_number, ai_response)

def send_payment_options(to, ctx):
    sub = ctx.get("subtotal", 0)
    send_buttons(to, f"💰 সাবটোটাল: {sub}৳\n\nপেমেন্ট মেথড:", [{"id": "pay_cod", "title": "💵 COD"}, {"id": "pay_bkash", "title": "📱 bKash"}, {"id": "pay_nagad", "title": "💳 Nagad"}])

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
            msg_id = msg.get("id")
            from_number = msg.get("from")
            if msg_id and from_number: Thread(target=process_webhook_async, args=(msg, from_number)).start()
    except Exception as e: logger.error("Webhook error: %s", e)
    return "ok", 200

def verify_meta_signature(payload, signature):
    if not APP_SECRET: return True
    if not signature: return False
    try:
        expected = hmac.new(APP_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        received = signature.replace("sha256=", "")
        return hmac.compare_digest(expected, received)
    except: return False

# =====================================================================
# PATHAO WEBHOOK
# =====================================================================
@app.route("/pathao/webhook", methods=["POST"])
def pathao_webhook():
    try:
        raw = request.get_data(as_text=True)
        payload = request.get_json(silent=True) or {}
        signature = request.headers.get("X-PATHAO-Signature", "")
        if PATHAO_WEBHOOK_SECRET and signature != PATHAO_WEBHOOK_SECRET: return jsonify({"error": "Invalid signature"}), 401
        event_type = payload.get("event_type", "")
        consignment_id = str(payload.get("consignment_id", "")).strip()
        order_id = str(payload.get("order_id", "")).strip()
        status = payload.get("order_status", "") or payload.get("status", "")
        db_query("INSERT INTO pathao_webhook_logs (event_type, consignment_id, order_id, status, raw_payload) VALUES (?, ?, ?, ?, ?)", (event_type, consignment_id, order_id, status, raw), commit=True)
        if consignment_id:
            existing = db_query("SELECT id, phone FROM orders WHERE pathao_consignment_id = ?", (consignment_id,), fetchone=True)
            if existing:
                db_query("UPDATE orders SET status = ? WHERE id = ?", (status.lower() if status else "unknown", existing["id"]), commit=True)
                if status and status.lower() in ["delivered", "picked", "in_transit"]:
                    msg_map = {"delivered": "🎉 প্রিয় গ্রাহক, আপনার অর্ডার সফলভাবে ডেলিভারি হয়েছে!", "picked": "📦 আপনার অর্ডার কুরিয়ারে হস্তান্তরিত হয়েছে।", "in_transit": "🚚 আপনার অর্ডার ডেলিভারির পথে।"}
                    send_text(existing["phone"], msg_map.get(status.lower(), f"📦 অর্ডার স্ট্যাটাস: {status}"))
        return jsonify({"success": True}), 200
    except Exception as e: return jsonify({"error": str(e)}), 500

# =====================================================================
# DYNAMIC ADMIN PANEL — API ENDPOINTS
# =====================================================================
@app.route("/admin/api/stats", methods=["GET"])
@login_required
def api_stats():
    total_orders = db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)
    revenue = db_query("SELECT COALESCE(SUM(total), 0) as s FROM orders WHERE status != 'cancelled'", fetchone=True)
    users = db_query("SELECT COUNT(*) as c FROM users", fetchone=True)
    pending = db_query("SELECT COUNT(*) as c FROM orders WHERE status IN ('pending', 'created')", fetchone=True)
    today_orders = db_query("SELECT COUNT(*) as c FROM orders WHERE date(created_at) = date('now')", fetchone=True)
    return jsonify({
        "total_orders": total_orders["c"] if total_orders else 0,
        "revenue": revenue["s"] if revenue else 0,
        "users": users["c"] if users else 0,
        "pending": pending["c"] if pending else 0,
        "today_orders": today_orders["c"] if today_orders else 0
    })

@app.route("/admin/api/products", methods=["GET"])
@login_required
def api_products():
    products = db_query("SELECT * FROM products WHERE active = 1 ORDER BY id DESC", fetchall=True) or []
    return jsonify({"products": products})

@app.route("/admin/api/product", methods=["POST"])
@login_required
def api_add_product():
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    price = data.get("price", 0)
    if not name or price <= 0: return jsonify({"error": "Invalid data"}), 400
    db_query("INSERT INTO products (name, price, stock, description, image_url) VALUES (?, ?, ?, ?, ?)", (name, price, data.get("stock", 0), data.get("description", ""), data.get("image_url", "")), commit=True)
    return jsonify({"success": True, "message": "Product added"})

@app.route("/admin/api/product/<int:pid>", methods=["DELETE"])
@login_required
def api_delete_product(pid):
    db_query("UPDATE products SET active = 0 WHERE id = ?", (pid,), commit=True)
    return jsonify({"success": True})

@app.route("/admin/api/orders", methods=["GET"])
@login_required
def api_orders():
    orders = db_query("SELECT * FROM orders ORDER BY id DESC", fetchall=True) or []
    return jsonify({"orders": orders})

@app.route("/admin/api/order/<int:oid>/status", methods=["POST"])
@login_required
def api_order_status(oid):
    data = request.get_json() or {}
    status = data.get("status", "").strip()
    if status: db_query("UPDATE orders SET status = ? WHERE id = ?", (status, oid), commit=True)
    return jsonify({"success": True})

@app.route("/admin/api/messages/phones", methods=["GET"])
@login_required
def api_message_phones():
    rows = db_query("""
        SELECT from_number as phone, MAX(created_at) as last_time,
               (SELECT content FROM messages m2 WHERE m2.from_number = m1.from_number ORDER BY created_at DESC LIMIT 1) as last_msg
        FROM messages m1
        GROUP BY from_number
        ORDER BY last_time DESC
    """, fetchall=True) or []
    result = []
    for r in rows:
        user = db_query("SELECT name FROM users WHERE phone = ?", (r["phone"],), fetchone=True)
        result.append({"phone": r["phone"], "name": user["name"] if user else None, "last_msg": (r["last_msg"] or "")[:50], "last_time": r["last_time"]})
    return jsonify({"conversations": result})

@app.route("/admin/api/conversations/<phone>", methods=["GET"])
@login_required
def api_conversation(phone):
    msgs = db_query("SELECT * FROM messages WHERE from_number = ? ORDER BY created_at ASC", (phone,), fetchall=True) or []
    messages = []
    for m in msgs:
        content = m["content"] or ""
        try:
            msg_data = eval(content)
            if isinstance(msg_data, dict): content = msg_data.get("text", {}).get("body", content)
        except: pass
        messages.append({"content": content, "direction": "out" if m["msg_type"] == "out" else "in", "time": m["created_at"][11:16] if m["created_at"] else ""})
    return jsonify({"messages": messages})

@app.route("/admin/api/reply", methods=["POST"])
@login_required
def api_reply():
    data = request.get_json() or {}
    phone = data.get("phone", "").strip()
    msg = data.get("message", "").strip()
    if not phone or not msg: return jsonify({"error": "Missing data"}), 400
    try:
        send_text(phone, msg)
        db_query("INSERT INTO messages (msg_id, from_number, content, msg_type) VALUES (?, ?, ?, ?)", (f"admin_{int(time.time())}", phone, msg, "out"), commit=True)
        return jsonify({"success": True})
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route("/admin/api/pathao/logs", methods=["GET"])
@login_required
def api_pathao_logs():
    logs = db_query("SELECT * FROM pathao_webhook_logs ORDER BY created_at DESC LIMIT 100", fetchall=True) or []
    return jsonify({"logs": logs})

@app.route("/admin/api/settings", methods=["GET"])
@login_required
def api_get_settings():
    return jsonify(get_all_settings())

@app.route("/admin/api/settings", methods=["POST"])
@login_required
def api_save_settings():
    data = request.get_json() or {}
    for key in ["business_name", "logo_url", "primary_color", "header_color", "accent_color", "fb_catalog_id", "fb_access_token", "pathao_client_id", "pathao_client_secret", "pathao_merchant_email", "pathao_merchant_password", "pathao_store_id", "pathao_base_url"]:
        if key in data: set_setting(key, data[key])
    return jsonify({"success": True, "message": "Settings saved"})

# =====================================================================
# DYNAMIC ADMIN PANEL — SINGLE PAGE SHELL
# =====================================================================
ADMIN_SHELL = """<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
<title>Dhaka Admin</title>
<link rel="apple-touch-icon" href="https://i.postimg.cc/ydG2D187/Adobe-Express-file.png">
<link rel="icon" type="image/png" href="https://i.postimg.cc/ydG2D187/Adobe-Express-file.png">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Dhaka Admin">
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
body{background:#f3f4f6;color:#1f2937;overflow-x:hidden}
.header{background:#1f2937;color:#fff;padding:12px 16px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
.header h1{font-size:16px;font-weight:600}
.tabs{display:flex;gap:6px;overflow-x:auto;padding:0 4px}
.tab-btn{padding:8px 14px;border:none;border-radius:8px;background:rgba(255,255,255,.12);color:#fff;cursor:pointer;font-size:12px;white-space:nowrap;transition:.2s}
.tab-btn.active{background:#fff;color:#1f2937;font-weight:600}
.container{max-width:1200px;margin:0 auto;padding:12px}
.section{display:none;animation:fadeIn .3s ease}
.section.active{display:block}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
.card{background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.08);margin-bottom:16px;overflow:hidden}
.card-header{padding:14px 16px;border-bottom:1px solid #e5e7eb;display:flex;align-items:center;justify-content:space-between}
.card-header h2{font-size:15px;font-weight:600}
.btn{padding:8px 16px;border:none;border-radius:8px;cursor:pointer;font-size:13px;font-weight:500;background:#667eea;color:#fff;transition:.2s}
.btn:hover{opacity:.9}
.btn-sm{padding:6px 12px;font-size:12px}
.btn-danger{background:#ef4444}
.btn-success{background:#10b981}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:10px 12px;text-align:left;border-bottom:1px solid #e5e7eb}
th{background:#f9fafb;font-weight:600;font-size:11px;text-transform:uppercase;color:#6b7280}
tr:hover{background:#f9fafb}
.status-badge{padding:4px 10px;border-radius:20px;font-size:11px;font-weight:500}
.status-pending{background:#fef3c7;color:#92400e}
.status-created{background:#dbeafe;color:#1e40af}
.status-confirmed{background:#e0e7ff;color:#3730a3}
.status-shipped{background:#ddd6fe;color:#5b21b6}
.status-delivered{background:#d1fae5;color:#065f46}
.status-cancelled{background:#fee2e2;color:#991b1b}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:16px}
.stat-card{background:#fff;padding:16px;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.stat-label{font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:.5px}
.stat-value{font-size:24px;font-weight:700;color:#667eea;margin-top:4px}
.form-group{margin-bottom:12px}
.form-group label{display:block;font-size:12px;font-weight:500;margin-bottom:4px;color:#374151}
.form-group input,.form-group textarea,.form-group select{width:100%;padding:8px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:14px;transition:.2s}
.form-group input:focus,.form-group textarea:focus{border-color:#667eea;outline:none}
.search-box{padding:8px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:13px;width:100%;max-width:300px}
.conv-sidebar{width:100%;max-width:320px;border-right:1px solid #e5e7eb;overflow-y:auto;max-height:calc(100vh - 200px)}
.conv-row{padding:12px 14px;border-bottom:1px solid #f3f4f6;cursor:pointer;transition:.2s}
.conv-row:hover{background:#f9fafb}
.conv-row.active{background:#eef2ff;border-left:3px solid #667eea}
.conv-name{font-weight:600;font-size:13px;color:#111827}
.conv-preview{font-size:12px;color:#6b7280;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.conv-time{font-size:10px;color:#9ca3af;margin-top:2px}
.chat-area{flex:1;display:flex;flex-direction:column;background:#f9fafb;min-height:500px}
.chat-messages{flex:1;padding:16px;overflow-y:auto}
.chat-bubble{max-width:70%;padding:10px 14px;border-radius:14px;margin-bottom:8px;font-size:14px;line-height:1.5}
.chat-in{background:#fff;align-self:flex-start;border-bottom-left-radius:4px;box-shadow:0 1px 2px rgba(0,0,0,.08)}
.chat-out{background:#667eea;color:#fff;align-self:flex-end;border-bottom-right-radius:4px}
.chat-time{font-size:10px;opacity:.7;margin-top:4px;text-align:right}
.chat-input{padding:12px;border-top:1px solid #e5e7eb;display:flex;gap:8px;background:#fff}
.chat-input input{flex:1;padding:10px 14px;border:1px solid #d1d5db;border-radius:8px;font-size:14px}
.empty-state{text-align:center;color:#9ca3af;padding:60px 20px}
.modal-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.5);z-index:200;align-items:center;justify-content:center}
.modal-overlay.active{display:flex}
.modal{background:#fff;padding:24px;border-radius:12px;width:90%;max-width:500px;max-height:90vh;overflow-y:auto;margin:20px}
.spinner{display:inline-block;width:16px;height:16px;border:2px solid #f3f4f6;border-top-color:#667eea;border-radius:50%;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.toast{position:fixed;bottom:20px;right:20px;background:#1f2937;color:#fff;padding:12px 20px;border-radius:8px;font-size:13px;z-index:300;animation:slideUp .3s ease}
@keyframes slideUp{from{transform:translateY(20px);opacity:0}to{transform:translateY(0);opacity:1}}
</style>
</head>
<body>
<div class="header">
<h1 id="businessName">Dhaka Exclusive</h1>
<div class="tabs">
<button class="tab-btn active" onclick="switchTab('dashboard')">📊 Dashboard</button>
<button class="tab-btn" onclick="switchTab('products')">📦 Products</button>
<button class="tab-btn" onclick="switchTab('orders')">🛒 Orders</button>
<button class="tab-btn" onclick="switchTab('messages')">💬 Messages</button>
<button class="tab-btn" onclick="switchTab('pathao')">🚚 Pathao</button>
<button class="tab-btn" onclick="switchTab('settings')">⚙️ Settings</button>
</div>
</div>
<div class="container">
<!-- DASHBOARD -->
<div id="dashboard" class="section active">
<div class="stat-grid">
<div class="stat-card"><div class="stat-label">মোট অর্ডার</div><div class="stat-value" id="statOrders">0</div></div>
<div class="stat-card"><div class="stat-label">মোট কাস্টমার</div><div class="stat-value" id="statUsers">0</div></div>
<div class="stat-card"><div class="stat-label">পেন্ডিং</div><div class="stat-value" id="statPending">0</div></div>
<div class="stat-card"><div class="stat-label">আজকের অর্ডার</div><div class="stat-value" id="statToday">0</div></div>
<div class="stat-card"><div class="stat-label">রেভেনিউ</div><div class="stat-value" id="statRevenue">৳0</div></div>
</div>
</div>
<!-- PRODUCTS -->
<div id="products" class="section">
<div class="card"><div class="card-header"><h2>📦 প্রোডাক্ট</h2><button class="btn btn-sm" onclick="openProductModal()">➕ যোগ করুন</button></div>
<div style="padding:16px"><input type="text" class="search-box" id="productSearch" placeholder="সার্চ..." onkeyup="filterProducts()"></div>
<table><thead><tr><th>ID</th><th>নাম</th><th>দাম</th><th>স্টক</th><th>অ্যাকশন</th></tr></thead>
<tbody id="productTable"></tbody></table></div>
</div>
<!-- ORDERS -->
<div id="orders" class="section">
<div class="card"><div class="card-header"><h2>🛒 অর্ডার</h2></div>
<div style="padding:16px"><input type="text" class="search-box" id="orderSearch" placeholder="ফোন/নামে সার্চ..." onkeyup="filterOrders()"></div>
<table><thead><tr><th>ID</th><th>কাস্টমার</th><th>ফোন</th><th>টোটাল</th><th>স্ট্যাটাস</th><th>Tracking</th><th>অ্যাকশন</th></tr></thead>
<tbody id="orderTable"></tbody></table></div>
</div>
<!-- MESSAGES -->
<div id="messages" class="section">
<div style="display:flex;gap:0;height:calc(100vh - 160px)">
<div class="conv-sidebar" id="convList"></div>
<div class="chat-area">
<div class="chat-messages" id="chatMessages"><div class="empty-state">কোনো কাস্টমার সিলেক্ট করুন</div></div>
<div class="chat-input"><input type="text" id="replyInput" placeholder="মেসেজ লিখুন..." onkeypress="if(event.key==='Enter')sendReply()"><button class="btn" onclick="sendReply()">পাঠান</button></div>
</div>
</div>
</div>
<!-- PATHAO -->
<div id="pathao" class="section">
<div class="card"><div class="card-header"><h2>🚚 Pathao Webhook Logs</h2></div>
<table><thead><tr><th>Time</th><th>Event</th><th>Consignment</th><th>Order ID</th><th>Status</th></tr></thead>
<tbody id="pathaoTable"></tbody></table></div>
</div>
<!-- SETTINGS -->
<div id="settings" class="section">
<div class="card"><div class="card-header"><h2>⚙️ Appearance</h2></div>
<div style="padding:20px;max-width:600px">
<div class="form-group"><label>বিজনেস নাম</label><input type="text" id="sName"></div>
<div class="form-group"><label>লোগো URL</label><input type="text" id="sLogo"></div>
<div class="form-group"><label>প্রাইমারি কালার</label><input type="color" id="sPrimary"></div>
<div class="form-group"><label>হেডার কালার</label><input type="color" id="sHeader"></div>
<div class="form-group"><label>অ্যাকসেন্ট কালার</label><input type="color" id="sAccent"></div>
</div></div>
<div class="card"><div class="card-header"><h2>📘 Facebook Catalog</h2></div>
<div style="padding:20px;max-width:600px">
<div class="form-group"><label>Facebook Catalog ID</label><input type="text" id="sFbCatalog"></div>
<div class="form-group"><label>Facebook Access Token</label><input type="text" id="sFbToken"></div>
</div></div>
<div class="card"><div class="card-header"><h2>🚚 Pathao Courier API</h2></div>
<div style="padding:20px;max-width:600px">
<div class="form-group"><label>Client ID</label><input type="text" id="sPathaoId"></div>
<div class="form-group"><label>Client Secret</label><input type="text" id="sPathaoSecret"></div>
<div class="form-group"><label>Merchant Email</label><input type="text" id="sPathaoEmail"></div>
<div class="form-group"><label>Merchant Password</label><input type="password" id="sPathaoPass"></div>
<div class="form-group"><label>Store ID</label><input type="text" id="sPathaoStore"></div>
<div class="form-group"><label>Base URL</label><input type="text" id="sPathaoBase" placeholder="https://api-hermes.pathao.com"></div>
<button class="btn" onclick="saveSettings()">💾 সব Settings সেভ করুন</button>
<div id="saveResult" style="margin-top:12px;font-size:14px"></div>
</div></div>
</div>
</div>
<!-- MODALS -->
<div class="modal-overlay" id="productModal">
<div class="modal">
<h3>➕ প্রোডাক্ট যোগ করুন</h3>
<div class="form-group"><label>নাম</label><input type="text" id="pName"></div>
<div class="form-group"><label>দাম (৳)</label><input type="number" id="pPrice"></div>
<div class="form-group"><label>স্টক</label><input type="number" id="pStock" value="10"></div>
<div class="form-group"><label>বিবরণ</label><textarea id="pDesc" rows="3"></textarea></div>
<div class="form-group"><label>ছবি URL</label><input type="text" id="pImage" placeholder="https://..."></div>
<div style="display:flex;gap:8px;margin-top:16px">
<button class="btn" onclick="saveProduct()">💾 সেভ</button>
<button class="btn" style="background:#e5e7eb;color:#374151" onclick="closeModal('productModal')">বাতিল</button>
</div></div></div>
<script>
let activePhone='';
let allProducts=[];
let allOrders=[];
let currentSettings={};

function switchTab(id){
  document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  event.target.classList.add('active');
  if(id==='dashboard') loadDashboard();
  if(id==='products') loadProducts();
  if(id==='orders') loadOrders();
  if(id==='messages') loadConversations();
  if(id==='pathao') loadPathaoLogs();
  if(id==='settings') loadSettings();
}

async function api(url,opts={}){
  try{
    const r=await fetch(url,{...opts,headers:{'Content-Type':'application/json',...opts.headers}});
    return await r.json();
  }catch(e){console.error(e);return{error:e.message};}
}

function showToast(msg){
  const t=document.createElement('div');t.className='toast';t.textContent=msg;
  document.body.appendChild(t);setTimeout(()=>t.remove(),3000);
}

// DASHBOARD
async function loadDashboard(){
  const d=await api('/admin/api/stats');
  if(d.error)return;
  document.getElementById('statOrders').textContent=d.total_orders;
  document.getElementById('statUsers').textContent=d.users;
  document.getElementById('statPending').textContent=d.pending;
  document.getElementById('statToday').textContent=d.today_orders;
  document.getElementById('statRevenue').textContent='৳'+d.revenue;
}

// PRODUCTS
async function loadProducts(){
  const d=await api('/admin/api/products');
  allProducts=d.products||[];
  renderProducts();
}
function renderProducts(){
  const q=document.getElementById('productSearch').value.toLowerCase();
  const tbody=document.getElementById('productTable');
  tbody.innerHTML=allProducts.filter(p=>!q||p.name.toLowerCase().includes(q)).map(p=>`
    <tr><td>#${p.id}</td><td>${p.name}</td><td>৳${p.price}</td><td>${p.stock}</td>
    <td><button class="btn btn-sm btn-danger" onclick="deleteProduct(${p.id})">🗑️</button></td></tr>
  `).join('')||'<tr><td colspan="5" style="text-align:center;color:#9ca3af">কোনো প্রোডাক্ট নেই</td></tr>';
}
function filterProducts(){renderProducts();}
function openProductModal(){document.getElementById('productModal').classList.add('active');}
function closeModal(id){document.getElementById(id).classList.remove('active');}
async function saveProduct(){
  const data={name:document.getElementById('pName').value,price:parseInt(document.getElementById('pPrice').value)||0,stock:parseInt(document.getElementById('pStock').value)||0,description:document.getElementById('pDesc').value,image_url:document.getElementById('pImage').value};
  if(!data.name||data.price<=0){alert('নাম ও দাম দিন');return;}
  const r=await api('/admin/api/product',{method:'POST',body:JSON.stringify(data)});
  if(r.success){closeModal('productModal');showToast('প্রোডাক্ট যোগ হয়েছে!');loadProducts();}
  else alert(r.error||'ত্রুটি');
}
async function deleteProduct(id){if(!confirm('ডিলিট করবেন?'))return;await api('/admin/api/product/'+id,{method:'DELETE'});showToast('ডিলিট হয়েছে');loadProducts();}

// ORDERS
async function loadOrders(){
  const d=await api('/admin/api/orders');
  allOrders=d.orders||[];
  renderOrders();
}
function renderOrders(){
  const q=document.getElementById('orderSearch').value.toLowerCase();
  const tbody=document.getElementById('orderTable');
  const filtered=allOrders.filter(o=>!q||(o.phone||'').toLowerCase().includes(q)||(o.name||'').toLowerCase().includes(q));
  tbody.innerHTML=filtered.map(o=>`
    <tr><td>#${o.id}</td><td>${o.name||'N/A'}</td><td>${o.phone}</td><td>৳${o.total}</td>
    <td><span class="status-badge status-${o.status}">${o.status}</span></td>
    <td>${o.pathao_consignment_id||'N/A'}</td>
    <td><select onchange="updateOrderStatus(${o.id},this.value)" style="padding:4px 8px;border-radius:6px;border:1px solid #d1d5db">
      <option value="pending" ${o.status==='pending'?'selected':''}>Pending</option>
      <option value="created" ${o.status==='created'?'selected':''}>Created</option>
      <option value="confirmed" ${o.status==='confirmed'?'selected':''}>Confirmed</option>
      <option value="shipped" ${o.status==='shipped'?'selected':''}>Shipped</option>
      <option value="delivered" ${o.status==='delivered'?'selected':''}>Delivered</option>
      <option value="cancelled" ${o.status==='cancelled'?'selected':''}>Cancelled</option>
    </select></td></tr>
  `).join('')||'<tr><td colspan="7" style="text-align:center;color:#9ca3af">কোনো অর্ডার নেই</td></tr>';
}
function filterOrders(){renderOrders();}
async function updateOrderStatus(id,status){
  await api('/admin/api/order/'+id+'/status',{method:'POST',body:JSON.stringify({status})});
  showToast('স্ট্যাটাস আপডেট');loadOrders();
}

// MESSAGES
async function loadConversations(){
  const d=await api('/admin/api/messages/phones');
  const list=document.getElementById('convList');
  list.innerHTML=(d.conversations||[]).map(c=>`
    <div class="conv-row" id="conv-${c.phone}" onclick="openConversation('${c.phone}','${(c.name||'').replace(/'/g,"\\'")}')">
      <div class="conv-name">${c.name||c.phone}</div>
      <div class="conv-preview">${escapeHtml(c.last_msg)}</div>
      <div class="conv-time">${c.last_time?c.last_time.substring(11,16):''}</div>
    </div>
  `).join('')||'<div style="padding:20px;text-align:center;color:#9ca3af">কোনো মেসেজ নেই</div>';
}
async function openConversation(phone,name){
  activePhone=phone;
  document.querySelectorAll('.conv-row').forEach(r=>r.classList.remove('active'));
  document.getElementById('conv-'+phone)?.classList.add('active');
  const d=await api('/admin/api/conversations/'+encodeURIComponent(phone));
  const box=document.getElementById('chatMessages');
  if(!d.messages||!d.messages.length){box.innerHTML='<div class="empty-state">কোনো মেসেজ নেই</div>';return;}
  box.innerHTML='<div style="display:flex;flex-direction:column;gap:8px">'+d.messages.map(m=>`
    <div class="chat-bubble ${m.direction==='out'?'chat-out':'chat-in'}">${escapeHtml(m.content)}<div class="chat-time">${m.time}</div></div>
  `).join('')+'</div>';
  box.scrollTop=box.scrollHeight;
}
async function sendReply(){
  const text=document.getElementById('replyInput').value.trim();
  if(!text||!activePhone)return;
  const r=await api('/admin/api/reply',{method:'POST',body:JSON.stringify({phone:activePhone,message:text})});
  if(r.success){document.getElementById('replyInput').value='';openConversation(activePhone,'');showToast('পাঠানো হয়েছে');}
  else alert(r.error||'ত্রুটি');
}
function escapeHtml(t){const d=document.createElement('div');d.textContent=t;return d.innerHTML;}

// PATHAO LOGS
async function loadPathaoLogs(){
  const d=await api('/admin/api/pathao/logs');
  const tbody=document.getElementById('pathaoTable');
  tbody.innerHTML=(d.logs||[]).map(w=>`
    <tr><td>${w.created_at}</td><td>${w.event_type}</td><td>${w.consignment_id}</td><td>${w.order_id||'N/A'}</td>
    <td><span class="status-badge status-${w.status||'unknown'}">${w.status||'N/A'}</span></td></tr>
  `).join('')||'<tr><td colspan="5" style="text-align:center;color:#9ca3af">কোনো webhook লগ নেই</td></tr>';
}

// SETTINGS
async function loadSettings(){
  const s=await api('/admin/api/settings');
  currentSettings=s;
  document.getElementById('businessName').textContent=s.business_name||'Dhaka Exclusive';
  document.getElementById('sName').value=s.business_name||'';
  document.getElementById('sLogo').value=s.logo_url||'';
  document.getElementById('sPrimary').value=s.primary_color||'#667eea';
  document.getElementById('sHeader').value=s.header_color||'#1f2937';
  document.getElementById('sAccent').value=s.accent_color||'#10b981';
  document.getElementById('sFbCatalog').value=s.fb_catalog_id||'';
  document.getElementById('sFbToken').value=s.fb_access_token||'';
  document.getElementById('sPathaoId').value=s.pathao_client_id||'';
  document.getElementById('sPathaoSecret').value=s.pathao_client_secret||'';
  document.getElementById('sPathaoEmail').value=s.pathao_merchant_email||'';
  document.getElementById('sPathaoPass').value=s.pathao_merchant_password||'';
  document.getElementById('sPathaoStore').value=s.pathao_store_id||'';
  document.getElementById('sPathaoBase').value=s.pathao_base_url||'';
}
async function saveSettings(){
  const data={
    business_name:document.getElementById('sName').value,
    logo_url:document.getElementById('sLogo').value,
    primary_color:document.getElementById('sPrimary').value,
    header_color:document.getElementById('sHeader').value,
    accent_color:document.getElementById('sAccent').value,
    fb_catalog_id:document.getElementById('sFbCatalog').value,
    fb_access_token:document.getElementById('sFbToken').value,
    pathao_client_id:document.getElementById('sPathaoId').value,
    pathao_client_secret:document.getElementById('sPathaoSecret').value,
    pathao_merchant_email:document.getElementById('sPathaoEmail').value,
    pathao_merchant_password:document.getElementById('sPathaoPass').value,
    pathao_store_id:document.getElementById('sPathaoStore').value,
    pathao_base_url:document.getElementById('sPathaoBase').value
  };
  const r=await api('/admin/api/settings',{method:'POST',body:JSON.stringify(data)});
  document.getElementById('saveResult').textContent=r.message||'সেভ হয়েছে!';
  if(r.success){showToast('Settings saved!');loadSettings();}
}

// AUTO LOAD DASHBOARD
loadDashboard();
</script>
</body></html>"""

@app.route("/admin", methods=["GET"])
@login_required
def admin_dashboard():
    return ADMIN_SHELL

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
