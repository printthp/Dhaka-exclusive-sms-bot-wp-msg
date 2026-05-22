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
from flask import Flask, request, jsonify, render_template_string
from threading import Thread, Lock
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", handlers=[logging.StreamHandler(sys.stdout)])
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
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        c = conn.cursor()
        tables = [
            "CREATE TABLE IF NOT EXISTS messages (msg_id TEXT PRIMARY KEY, from_number TEXT, content TEXT, msg_type TEXT DEFAULT 'text', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS sessions (phone TEXT PRIMARY KEY, state TEXT DEFAULT 'idle', context TEXT DEFAULT '{}', last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, name TEXT, address TEXT, city_id INTEGER DEFAULT 1, zone_id INTEGER DEFAULT 1, area_id INTEGER DEFAULT 1, product_id INTEGER, quantity INTEGER DEFAULT 1, price INTEGER, delivery_charge INTEGER DEFAULT 80, discount INTEGER DEFAULT 0, total INTEGER, payment_method TEXT DEFAULT 'cod', pathao_consignment_id TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
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

init_db()

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
            logger.error("DB Error: %s", e)
            conn.close()
            raise

def get_setting(key, default=""):
    row = db_query("SELECT value FROM settings WHERE key = ?", (key,), fetchone=True)
    return row["value"] if row else default

def set_setting(key, value):
    db_query("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (key, value), commit=True)

def get_all_settings():
    rows = db_query("SELECT key, value FROM settings", fetchall=True) or []
    return {r["key"]: r["value"] for r in rows}

def format_phone(num):
    num = str(num).strip().replace(" ", "").replace("-", "").replace("+", "")
    if num.startswith("01") and len(num) == 11:
        num = "88" + num
    return num

def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        au = os.environ.get("ADMIN_PANEL_USER", "admin")
        ap = os.environ.get("ADMIN_PANEL_PASS", "admin123")
        if not auth or auth.username != au or auth.password != ap:
            return ("<h3>Unauthorized</h3>", 401, {"WWW-Authenticate": 'Basic realm="Login Required"'})
        return f(*args, **kwargs)
    return decorated

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
    if not all([cid, csec, email, pwd]):
        return None, "Pathao credentials missing"
    url = f"{base}/aladdin/api/v1/issue-token"
    try:
        r = requests.post(url, json={"client_id": cid, "client_secret": csec, "username": email, "password": pwd, "grant_type": "password"}, headers={"content-type": "application/json"}, timeout=15)
        d = r.json()
        if r.status_code == 200:
            token = d.get("token") or d.get("access_token") or d.get("data", {}).get("token")
            if token: return str(token), None
        return None, d.get("message", "Token failed")
    except Exception as e:
        return None, str(e)

def track_pathao_order(key):
    token, err = get_pathao_token()
    if not token: return f"Token Error: {err}"
    key = str(key).strip().replace("+", "")
    s = get_all_settings()
    base = s.get("pathao_base_url") or PATHAO_BASE_URL
    url = f"{base}/aladdin/api/v1/orders/{key}/tracking"
    try:
        r = requests.get(url, headers={"authorization": f"Bearer {token}", "accept": "application/json"}, timeout=15)
        d = r.json()
        if r.status_code == 200 and d.get("status") == 200:
            st = d.get("data", {}).get("order_status", "unknown").lower()
            mp = {"pending": "পেন্ডিং", "picked": "কুরিয়ারে হস্তান্তরিত", "in_transit": "ডেলিভারির পথে", "delivered": "ডেলিভারি সম্পন্ন", "cancelled": "বাতিল", "returned": "রিটার্ন"}
            return mp.get(st, st.upper())
        return "অর্ডার পাওয়া যায়নি।"
    except:
        return "ট্র্যাকিং ত্রুটি।"

def create_pathao_order(name, phone, address, city_id=1, zone_id=1, area_id=1, item_desc="", cod_amount=0):
    token, err = get_pathao_token()
    if not token: return False, err
    s = get_all_settings()
    base = s.get("pathao_base_url") or PATHAO_BASE_URL
    store = s.get("pathao_store_id") or PATHAO_STORE_ID
    url = f"{base}/aladdin/api/v1/orders"
    phone = format_phone(phone)
    try:
        r = requests.post(url, json={
            "store_id": int(store) if store else 0, "recipient_name": name, "recipient_phone": phone,
            "recipient_address": address, "recipient_city": int(city_id), "recipient_zone": int(zone_id),
            "recipient_area": int(area_id), "delivery_type": 48, "item_type": 2,
            "special_instruction": "WhatsApp Bot Order", "item_quantity": 1,
            "amount_to_collect": int(cod_amount), "item_description": item_desc
        }, headers={"authorization": f"Bearer {token}", "content-type": "application/json"}, timeout=15)
        d = r.json()
        if r.status_code == 200 and d.get("status") == 200:
            return True, d.get("data", {}).get("consignment_id")
        return False, d.get("message", r.text)
    except Exception as e:
        return False, str(e)

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
# WHATSAPP SEND
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
# PRODUCTS & HELPERS
# =====================================================================
def get_products():
    try: return db_query("SELECT * FROM products WHERE active = 1", fetchall=True)
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
# AI (Gemini)
# =====================================================================
def get_ai_answer(user_query, session_context=None):
    if not genai_available or not client: return "দুঃখিত প্রিয় গ্রাহক, এখন AI সার্ভিস অফলাইন।"
    try:
        si = "You are the AI sales assistant for 'Dhaka Exclusive' (Bangladesh).\n1. NEVER say 'নমস্কার'. ALWAYS 'প্রিয় গ্রাহক'.\n2. Short, polite, Bengali replies. Taka only.\n3. You CAN track orders and take orders.\n\nPRODUCTS:\n" + format_catalog()
        cfg = types.GenerateContentConfig(system_instruction=si, temperature=0.15, max_output_tokens=500)
        return client.models.generate_content(model=MODEL_NAME, contents=user_query, config=cfg).text
    except Exception as e: return "দুঃখিত প্রিয় গ্রাহক, সিস্টেম ব্যস্ত।"

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
        if any(k in cap for k in ["কত", "দাম", "কিনব", "চাই", "price"]):
            send_text(from_number, "📸 প্রোডাক্ট ছবি পেয়েছি! আমাদের ক্যাটালগ দেখতে 'কিনব' লিখুন।")
        elif any(k in cap for k in ["পেমেন্ট", "টাকা", "bkash", "nagad", "paid"]):
            send_text(from_number, "💳 পেমেন্ট রিসিপ্ট পেয়েছি! আপনার অর্ডার আইডি দিন।")
        else:
            send_text(from_number, "📸 ছবি পেয়েছি! প্রোডাক্ট কিনতে চাইলে 'কিনব' লিখুন।")
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
        ctx["quantity"] = qty
        ctx["subtotal"] = ctx["price"] * qty
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
        ctx["payment_method"] = "cod"
        ctx["delivery_charge"] = 80
        ctx["total"] = ctx["subtotal"] + 80
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
    return jsonify({"status": "running", "service": f"{BUSINESS_NAME} WhatsApp Bot", "version": "4.0", "timestamp": datetime.utcnow().isoformat()})

@app.route("/health", methods=["GET"])
def health():
    try:
        db_query("SELECT 1", fetchone=True)
        return jsonify({"status": "healthy", "database": True})
    except:
        return jsonify({"status": "unhealthy", "database": False})

@app.route("/webhook", methods=["GET"])
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Verification failed", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    signature = request.headers.get("X-Hub-Signature-256", "")
    payload = request.get_data()
    if not verify_meta_signature(payload, signature):
        return "Invalid signature", 403
    data = request.get_json(silent=True) or {}
    try:
        entry = data.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        if "messages" in value:
            msg = value["messages"][0]
            msg_id = msg.get("id")
            from_number = msg.get("from")
            if msg_id and from_number:
                Thread(target=process_webhook_async, args=(msg, from_number)).start()
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
        if PATHAO_WEBHOOK_SECRET and signature != PATHAO_WEBHOOK_SECRET:
            logger.warning("Pathao webhook invalid signature")
            return jsonify({"error": "Invalid signature"}), 401
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
    except Exception as e:
        logger.exception("Pathao webhook error: %s", e)
        return jsonify({"error": str(e)}), 500

# =====================================================================
# ADMIN PANEL
# =====================================================================
ADMIN_HTML = """<!DOCTYPE html>
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
<meta name="theme-color" content="{{ settings.header_color }}">
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
body{background:#f3f4f6;color:#1f2937}
.header{background:{{ settings.header_color }};color:#fff;padding:12px 20px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
.header h1{font-size:18px}.tabs{display:flex;gap:8px;overflow-x:auto}
.tab-btn{padding:8px 16px;border:none;border-radius:8px;background:rgba(255,255,255,.15);color:#fff;cursor:pointer;font-size:13px;white-space:nowrap}
.tab-btn.active{background:#fff;color:{{ settings.header_color }};font-weight:600}
.container{max-width:1200px;margin:0 auto;padding:15px}
.section{display:none}.section.active{display:block}
.card{background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.08);margin-bottom:20px;overflow:hidden}
.card-header{padding:16px 20px;border-bottom:1px solid #e5e7eb;display:flex;align-items:center;justify-content:space-between}
.card-header h2{font-size:16px}.btn{padding:8px 16px;border:none;border-radius:8px;cursor:pointer;font-size:13px;font-weight:500;background:{{ settings.primary_color }};color:#fff}
.btn-sm{padding:6px 12px;font-size:12px}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:12px;text-align:left;border-bottom:1px solid #e5e7eb}
.form-group{margin-bottom:12px}.form-group label{display:block;font-size:13px;font-weight:500;margin-bottom:4px;color:#374151}
.form-group input,.form-group textarea,.form-group select{width:100%;padding:8px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:14px}
.conv-row:hover{background:#f9fafb}
.status-badge{padding:4px 8px;border-radius:6px;font-size:12px}
.status-pending{background:#fef3c7;color:#92400e}
.status-delivered{background:#d1fae5;color:#065f46}
.status-cancelled{background:#fee2e2;color:#991b1b}
.modal-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.5);z-index:200;align-items:center;justify-content:center}
</style>
</head>
<body>
<div class="header">
<h1>{{ settings.business_name }}</h1>
<div class="tabs">
<button class="tab-btn active" onclick="showTab('dashboard')">📊 Main</button>
<button class="tab-btn" onclick="showTab('products')">📦 Products</button>
<button class="tab-btn" onclick="showTab('orders')">🛒 Orders</button>
<button class="tab-btn" onclick="showTab('messages')">💬 Messages</button>
<button class="tab-btn" onclick="showTab('pathao')">🚚 Pathao</button>
<button class="tab-btn" onclick="showTab('settings')">⚙️ Settings</button>
</div>
</div>
<div class="container">
<div id="dashboard" class="section active">
<div class="card"><div class="card-header"><h2>📊 ওভারভিউ</h2></div>
<div style="padding:20px">
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px">
<div style="background:#fff;padding:16px;border-radius:8px;border:1px solid #e5e7eb"><div style="font-size:12px;color:#6b7280">মোট অর্ডার</div><div style="font-size:24px;font-weight:700;color:{{ settings.primary_color }}">{{ stats.total_orders }}</div></div>
<div style="background:#fff;padding:16px;border-radius:8px;border:1px solid #e5e7eb"><div style="font-size:12px;color:#6b7280">মোট কাস্টমার</div><div style="font-size:24px;font-weight:700;color:{{ settings.primary_color }}">{{ stats.users }}</div></div>
<div style="background:#fff;padding:16px;border-radius:8px;border:1px solid #e5e7eb"><div style="font-size:12px;color:#6b7280">পেন্ডিং</div><div style="font-size:24px;font-weight:700;color:{{ settings.primary_color }}">{{ stats.pending }}</div></div>
<div style="background:#fff;padding:16px;border-radius:8px;border:1px solid #e5e7eb"><div style="font-size:12px;color:#6b7280">রেভেনিউ</div><div style="font-size:24px;font-weight:700;color:{{ settings.primary_color }}">৳{{ stats.revenue }}</div></div>
</div>
</div></div>
</div>
<div id="products" class="section">
<div class="card"><div class="card-header"><h2>📦 প্রোডাক্ট</h2><button class="btn btn-sm" onclick="openModal('productModal')">➕ যোগ</button></div>
<table><tr><th>ID</th><th>নাম</th><th>দাম</th><th>স্টক</th><th>অ্যাকশন</th></tr>
{{PRODUCT_ROWS}}
</table></div></div>
<div id="orders" class="section">
<div class="card"><div class="card-header"><h2>🛒 অর্ডার</h2></div>
<table><tr><th>ID</th><th>কাস্টমার</th><th>ফোন</th><th>টোটাল</th><th>স্ট্যাটাস</th><th>Tracking</th></tr>
{{ORDER_ROWS}}
</table></div></div>
<div id="messages" class="section">
<div class="card"><div class="card-header"><h2>💬 কাস্টমার মেসেজেস</h2></div>
<div style="display:flex;height:500px">
<div style="width:300px;border-right:1px solid #e5e7eb;overflow-y:auto">{{CONVERSATION_LIST}}</div>
<div style="flex:1;display:flex;flex-direction:column">
<div id="chatBox" style="flex:1;padding:16px;overflow-y:auto;background:#f9fafb"><div style="text-align:center;color:#9ca3af;padding-top:100px">কোনো কাস্টমার সিলেক্ট করুন</div></div>
<div style="padding:12px;border-top:1px solid #e5e7eb;display:flex;gap:8px">
<input type="text" id="replyText" placeholder="মেসেজ লিখুন..." style="flex:1;padding:8px 12px;border:1px solid #d1d5db;border-radius:8px" onkeypress="if(event.key==='Enter')sendReply()">
<button class="btn" onclick="sendReply()">পাঠান</button>
</div></div></div></div></div>
<div id="pathao" class="section">
<div class="card"><div class="card-header"><h2>🚚 Pathao Webhook Logs</h2></div>
<div style="padding:20px">
<table><tr><th>Time</th><th>Event</th><th>Consignment ID</th><th>Order ID</th><th>Status</th></tr>
{{PATHAO_LOG_ROWS}}
</table>
</div></div></div>
<div id="settings" class="section">
<div class="card"><div class="card-header"><h2>⚙️ Appearance</h2></div>
<div style="padding:20px;max-width:600px">
<div class="form-group"><label>বিজনেস নাম</label><input type="text" id="settingName" value="{{ settings.business_name }}"></div>
<div class="form-group"><label>লোগো URL</label><input type="text" id="settingLogo" value="{{ settings.logo_url }}"></div>
<div class="form-group"><label>প্রাইমারি কালার</label><input type="color" id="settingPrimary" value="{{ settings.primary_color }}"></div>
<div class="form-group"><label>হেডার কালার</label><input type="color" id="settingHeader" value="{{ settings.header_color }}"></div>
<div class="form-group"><label>অ্যাকসেন্ট কালার</label><input type="color" id="settingAccent" value="{{ settings.accent_color }}"></div>
</div></div>
<div class="card"><div class="card-header"><h2>📘 Facebook Catalog</h2></div>
<div style="padding:20px;max-width:600px">
<div class="form-group"><label>Facebook Catalog ID</label><input type="text" id="settingFbCatalog" value="{{ settings.fb_catalog_id }}"></div>
<div class="form-group"><label>Facebook Access Token</label><input type="text" id="settingFbToken" value="{{ settings.fb_access_token }}"></div>
</div></div>
<div class="card"><div class="card-header"><h2>🚚 Pathao Courier API</h2></div>
<div style="padding:20px;max-width:600px">
<div class="form-group"><label>Client ID</label><input type="text" id="settingPathaoClientId" value="{{ settings.pathao_client_id }}"></div>
<div class="form-group"><label>Client Secret</label><input type="text" id="settingPathaoClientSecret" value="{{ settings.pathao_client_secret }}"></div>
<div class="form-group"><label>Merchant Email</label><input type="text" id="settingPathaoEmail" value="{{ settings.pathao_merchant_email }}"></div>
<div class="form-group"><label>Merchant Password</label><input type="password" id="settingPathaoPassword" value="{{ settings.pathao_merchant_password }}"></div>
<div class="form-group"><label>Store ID</label><input type="text" id="settingPathaoStoreId" value="{{ settings.pathao_store_id }}"></div>
<div class="form-group"><label>Base URL (optional)</label><input type="text" id="settingPathaoBaseUrl" value="{{ settings.pathao_base_url }}" placeholder="https://api-hermes.pathao.com"></div>
<button class="btn" onclick="saveSettings()">💾 সব Settings সেভ করুন</button>
<div id="settingsResult" style="margin-top:12px"></div>
</div></div></div>
</div>
<div class="modal-overlay" id="productModal">
<div style="background:#fff;padding:24px;border-radius:12px;width:90%;max-width:500px;max-height:90vh;overflow-y:auto;margin:auto">
<h3>➕ প্রোডাক্ট</h3>
<input type="hidden" id="prodId">
<div class="form-group"><label>নাম</label><input type="text" id="prodName"></div>
<div class="form-group"><label>দাম (৳)</label><input type="number" id="prodPrice"></div>
<div class="form-group"><label>স্টক</label><input type="number" id="prodStock" value="10"></div>
<div class="form-group"><label>বিবরণ</label><textarea id="prodDesc" rows="3"></textarea></div>
<div class="form-group"><label>ছবি URL</label><input type="text" id="prodImage" placeholder="https://..."></div>
<div style="display:flex;gap:8px">
<button class="btn" onclick="saveProduct()">💾 সেভ</button>
<button class="btn" onclick="closeModal('productModal')" style="background:#e5e7eb;color:#374151">বাতিল</button>
</div></div></div>
<script>
function showTab(id){document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));document.getElementById(id).classList.add('active');document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));event.target.classList.add('active')}
function openModal(id){document.getElementById(id).style.display='flex'}
function closeModal(id){document.getElementById(id).style.display='none'}
function saveProduct(){const data={name:document.getElementById('prodName').value,price:parseInt(document.getElementById('prodPrice').value)||0,stock:parseInt(document.getElementById('prodStock').value)||0,description:document.getElementById('prodDesc').value,image_url:document.getElementById('prodImage').value};fetch('/admin/api/product',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(()=>{closeModal('productModal');location.reload()}).catch(err=>alert('❌ Error: '+err))}
let activePhone='';
function loadConversation(phone,name){activePhone=phone;fetch('/admin/api/conversations/'+encodeURIComponent(phone)).then(r=>r.json()).then(d=>{let html='';d.messages.forEach(m=>{const cls=m.direction==='out'?'msg-out':'msg-in';html+='<div style="max-width:70%;padding:10px 14px;border-radius:14px;margin-bottom:8px;font-size:14px;line-height:1.5;'+(m.direction==='out'?'background:#667eea;color:#fff;align-self:flex-end;border-bottom-right-radius:4px;':'background:#fff;align-self:flex-start;border-bottom-left-radius:4px;box-shadow:0 1px 2px rgba(0,0,0,.08)')+'">'+escapeHtml(m.content)+'<div style="font-size:11px;opacity:.7;margin-top:4px;text-align:right">'+m.time+'</div></div>';});document.getElementById('chatBox').innerHTML='<div style="display:flex;flex-direction:column;gap:8px;height:100%">'+html+'</div>';setTimeout(()=>{const box=document.getElementById('chatBox');box.scrollTop=box.scrollHeight},50);}).catch(err=>console.error(err))}
function sendReply(){const text=document.getElementById('replyText').value.trim();if(!text||!activePhone)return;fetch('/admin/api/reply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:activePhone,message:text})}).then(r=>r.json()).then(d=>{if(d.success){document.getElementById('replyText').value='';loadConversation(activePhone,'')}else{alert(d.error||'Failed')}}).catch(err=>alert('❌ Error: '+err))}
function escapeHtml(t){const d=document.createElement('div');d.textContent=t;return d.innerHTML}
function saveSettings(){const data={business_name:document.getElementById('settingName').value,logo_url:document.getElementById('settingLogo').value,primary_color:document.getElementById('settingPrimary').value,header_color:document.getElementById('settingHeader').value,accent_color:document.getElementById('settingAccent').value,fb_catalog_id:document.getElementById('settingFbCatalog').value,fb_access_token:document.getElementById('settingFbToken').value,pathao_client_id:document.getElementById('settingPathaoClientId').value,pathao_client_secret:document.getElementById('settingPathaoClientSecret').value,pathao_merchant_email:document.getElementById('settingPathaoEmail').value,pathao_merchant_password:document.getElementById('settingPathaoPassword').value,pathao_store_id:document.getElementById('settingPathaoStoreId').value,pathao_base_url:document.getElementById('settingPathaoBaseUrl').value};fetch('/admin/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(r=>r.json()).then(d=>{document.getElementById('settingsResult').textContent=d.message||'সেভ হয়েছে!';if(d.success)setTimeout(()=>location.reload(),800)}).catch(err=>alert('❌ Error: '+err))}
</script>
</body></html>"""

@app.route("/admin", methods=["GET"])
@login_required
def admin_dashboard():
    try:
        settings = get_all_settings()
        stats = {"total_orders": 0, "revenue": 0, "users": 0, "pending": 0}
        total_orders = db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)
        revenue = db_query("SELECT COALESCE(SUM(total), 0) as s FROM orders WHERE status != 'cancelled'", fetchone=True)
        users = db_query("SELECT COUNT(*) as c FROM users", fetchone=True)
        pending = db_query("SELECT COUNT(*) as c FROM orders WHERE status IN ('pending', 'created')", fetchone=True)
        if total_orders: stats["total_orders"] = total_orders["c"]
        if revenue: stats["revenue"] = revenue["s"]
        if users: stats["users"] = users["c"]
        if pending: stats["pending"] = pending["c"]
        products = db_query("SELECT * FROM products ORDER BY id DESC", fetchall=True) or []
        orders = db_query("SELECT * FROM orders ORDER BY id DESC", fetchall=True) or []
        if not products:
            db_query("INSERT INTO products (name, price, description, stock, image_url) VALUES (?, ?, ?, ?, ?)", ("পেস্টেল কুর্তি", 1299, "সুন্দর পেস্টেল কালার কুর্তি", 15, ""), commit=True)
            products = db_query("SELECT * FROM products ORDER BY id DESC", fetchall=True) or []
        html = ADMIN_HTML
        html = html.replace("{{ settings.business_name }}", settings.get("business_name", "Dhaka Exclusive"))
        html = html.replace("{{ settings.header_color }}", settings.get("header_color", "#1f2937"))
        html = html.replace("{{ settings.primary_color }}", settings.get("primary_color", "#667eea"))
        html = html.replace("{{ settings.accent_color }}", settings.get("accent_color", "#10b981"))
        html = html.replace("{{ settings.logo_url }}", settings.get("logo_url", ""))
        html = html.replace("{{ settings.fb_catalog_id }}", settings.get("fb_catalog_id", ""))
        html = html.replace("{{ settings.fb_access_token }}", settings.get("fb_access_token", ""))
        html = html.replace("{{ settings.pathao_client_id }}", settings.get("pathao_client_id", ""))
        html = html.replace("{{ settings.pathao_client_secret }}", settings.get("pathao_client_secret", ""))
        html = html.replace("{{ settings.pathao_merchant_email }}", settings.get("pathao_merchant_email", ""))
        html = html.replace("{{ settings.pathao_merchant_password }}", settings.get("pathao_merchant_password", ""))
        html = html.replace("{{ settings.pathao_store_id }}", settings.get("pathao_store_id", ""))
        html = html.replace("{{ settings.pathao_base_url }}", settings.get("pathao_base_url", ""))
        html = html.replace("{{ stats.total_orders }}", str(stats["total_orders"]))
        html = html.replace("{{ stats.revenue }}", str(stats["revenue"]))
        html = html.replace("{{ stats.users }}", str(stats["users"]))
        html = html.replace("{{ stats.pending }}", str(stats["pending"]))
        prod_rows = ""
        for p in products:
            prod_rows += "<tr><td>#" + str(p["id"]) + "</td><td>" + str(p["name"]) + "</td><td>৳" + str(p["price"]) + "</td><td>" + str(p["stock"]) + "</td><td><button class='btn btn-sm' onclick='editProduct(" + str(p["id"]) + ")'>✏️</button></td></tr>"
        if not prod_rows: prod_rows = "<tr><td colspan='5' style='text-align:center;color:#9ca3af'>কোনো প্রোডাক্ট নেই</td></tr>"
        html = html.replace("{{PRODUCT_ROWS}}", prod_rows)
        order_rows = ""
        for o in orders:
            status_class = f"status-{o['status']}"
            order_rows += f"<tr><td>#{o['id']}</td><td>{o.get('name') or 'N/A'}</td><td>{o['phone']}</td><td>৳{o['total']}</td><td><span class='status-badge {status_class}'>{o['status']}</span></td><td>{o.get('pathao_consignment_id') or 'N/A'}</td></tr>"
        if not order_rows: order_rows = "<tr><td colspan='6' style='text-align:center;color:#9ca3af'>কোনো অর্ডার নেই</td></tr>"
        html = html.replace("{{ORDER_ROWS}}", order_rows)
        conv_rows = ""
        msg_rows = db_query("SELECT from_number as phone, content, created_at, ROW_NUMBER() OVER (PARTITION BY from_number ORDER BY created_at DESC) as rn FROM messages ORDER BY created_at DESC", fetchall=True) or []
        seen = set()
        for r in msg_rows:
            phone = r["phone"]
            if phone in seen: continue
            seen.add(phone)
            user = db_query("SELECT name FROM users WHERE phone = ?", (phone,), fetchone=True)
            name = user["name"] if user else None
            display = name or phone
            last_msg = (r["content"] or "")[:40]
            last_time = r["created_at"][11:16] if r["created_at"] else ""
            conv_rows += f"<div class='conv-row' id='conv-{phone}' onclick=\"loadConversation('{phone}','{name or ''}')\" style='padding:12px 16px;border-bottom:1px solid #f3f4f6;cursor:pointer'><div style='font-weight:600;font-size:14px;color:#111827'>{display}</div><div style='font-size:12px;color:#6b7280;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>{last_msg}</div><div style='font-size:11px;color:#9ca3af;margin-top:2px'>{last_time}</div></div>"
        if not conv_rows: conv_rows = "<div style='padding:20px;text-align:center;color:#9ca3af'>কোনো মেসেজ নেই</div>"
        html = html.replace("{{CONVERSATION_LIST}}", conv_rows)
        pathao_logs = db_query("SELECT * FROM pathao_webhook_logs ORDER BY created_at DESC LIMIT 50", fetchall=True) or []
        pathao_rows = ""
        for w in pathao_logs:
            pathao_rows += f"<tr><td>{w['created_at']}</td><td>{w['event_type']}</td><td>{w['consignment_id']}</td><td>{w['order_id']}</td><td><span class='status-badge status-{w['status'] or 'unknown'}'>{w['status'] or 'N/A'}</span></td></tr>"
        if not pathao_rows: pathao_rows = "<tr><td colspan='5' style='text-align:center;color:#9ca3af'>কোনো webhook লগ নেই</td></tr>"
        html = html.replace("{{PATHAO_LOG_ROWS}}", pathao_rows)
        return html
    except Exception as e:
        logger.exception("Admin dashboard error")
        return f"<h3>Admin Panel Error:</h3><pre>{str(e)}</pre>", 500

@app.route("/admin/api/product", methods=["POST"])
@login_required
def admin_add_product():
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    price = data.get("price", 0)
    if not name or price <= 0: return jsonify({"error": "Invalid data"}), 400
    db_query("INSERT INTO products (name, price, stock, description, image_url) VALUES (?, ?, ?, ?, ?)", (name, price, data.get("stock", 0), data.get("description", ""), data.get("image_url", "")), commit=True)
    return jsonify({"success": True, "message": "Added"})

@app.route("/admin/api/settings", methods=["POST"])
@login_required
def admin_save_settings():
    data = request.get_json() or {}
    for key in ["business_name", "logo_url", "primary_color", "header_color", "accent_color", "fb_catalog_id", "fb_access_token", "pathao_client_id", "pathao_client_secret", "pathao_merchant_email", "pathao_merchant_password", "pathao_store_id", "pathao_base_url"]:
        if key in data: set_setting(key, data[key])
    return jsonify({"success": True, "message": "Settings saved! Reload to see changes."})

@app.route("/admin/api/conversations/<phone>", methods=["GET"])
@login_required
def admin_get_conversation(phone):
    try:
        msgs = db_query("SELECT * FROM messages WHERE from_number = ? ORDER BY created_at ASC", (phone,), fetchall=True) or []
        messages = []
        for m in msgs:
            content = m["content"] or ""
            try:
                msg_data = eval(content)
                if isinstance(msg_data, dict):
                    content = msg_data.get("text", {}).get("body", content)
            except: pass
            messages.append({"content": content, "direction": "out" if m["msg_type"] == "out" else "in", "time": m["created_at"][11:16] if m["created_at"] else ""})
        return jsonify({"success": True, "messages": messages})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/api/reply", methods=["POST"])
@login_required
def admin_reply():
    data = request.get_json() or {}
    phone = data.get("phone", "").strip()
    msg = data.get("message", "").strip()
    if not phone or not msg: return jsonify({"error": "Phone or message missing"}), 400
    try:
        send_text(phone, msg)
        db_query("INSERT INTO messages (msg_id, from_number, content, msg_type) VALUES (?, ?, ?, ?)", (f"admin_{int(time.time())}", phone, msg, "out"), commit=True)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
