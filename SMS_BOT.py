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
            for stmt in [
                "CREATE TABLE IF NOT EXISTS messages (msg_id TEXT PRIMARY KEY, from_number TEXT, content TEXT, msg_type TEXT DEFAULT 'text', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
                "CREATE TABLE IF NOT EXISTS sessions (phone TEXT PRIMARY KEY, state TEXT DEFAULT 'idle', context TEXT DEFAULT '{}', last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
                "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, name TEXT, address TEXT, city_id INTEGER DEFAULT 1, zone_id INTEGER DEFAULT 1, area_id INTEGER DEFAULT 1, product_id INTEGER, quantity INTEGER DEFAULT 1, price INTEGER, delivery_charge INTEGER DEFAULT 80, discount INTEGER DEFAULT 0, total INTEGER, payment_method TEXT DEFAULT 'cod', payment_status TEXT DEFAULT 'pending', pathao_consignment_id TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
                "CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, price INTEGER, description TEXT, stock INTEGER DEFAULT 0, active INTEGER DEFAULT 1, image_url TEXT DEFAULT '')",
                "CREATE TABLE IF NOT EXISTS users (phone TEXT PRIMARY KEY, name TEXT, language TEXT DEFAULT 'bn', first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP, total_orders INTEGER DEFAULT 0, total_spent INTEGER DEFAULT 0)",
                "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            ]:
                c.execute(stmt)
            defaults = [
                ("business_name", BUSINESS_NAME),
                ("logo_url", ""),
                ("primary_color", "#667eea"),
                ("header_color", "#1f2937"),
                ("sidebar_color", "#374151"),
                ("accent_color", "#10b981"),
            ]
            for k, v in defaults:
                c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
            conn.commit()
            conn.close()
            logger.info("Database initialized: %s", DB_FILE)
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
            if commit:
                conn.commit(); conn.close(); return True
            if fetchone:
                row = c.fetchone(); conn.close(); return dict(row) if row else None
            if fetchall:
                rows = c.fetchall(); conn.close(); return [dict(r) for r in rows]
            conn.close(); return None
        except Exception as e:
            logger.error("DB Error: %s | Query: %s", e, query)
            conn.close()
            raise

def format_phone(num):
    num = str(num).strip().replace(" ", "").replace("-", "").replace("+", "")
    if num.startswith("01") and len(num) == 11:
        num = "88" + num
    return num

def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        admin_user = os.environ.get("ADMIN_PANEL_USER", "admin")
        admin_pass = os.environ.get("ADMIN_PANEL_PASS", "admin123")
        if not auth or auth.username != admin_user or auth.password != admin_pass:
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
# PATHAO API - FIXED VERSION
# =====================================================================

def get_pathao_token():
    if not all([PATHAO_CLIENT_ID, PATHAO_CLIENT_SECRET, PATHAO_MERCHANT_EMAIL, PATHAO_MERCHANT_PASSWORD]):
        return None, "Pathao credentials missing"
    
    url = f"{PATHAO_BASE_URL}/aladdin/api/v1/issue-token"
    headers = {
        "accept": "application/json",
        "content-type": "application/json"
    }
    payload = {
        "client_id": PATHAO_CLIENT_ID,
        "client_secret": PATHAO_CLIENT_SECRET,
        "username": PATHAO_MERCHANT_EMAIL,
        "password": PATHAO_MERCHANT_PASSWORD,
        "grant_type": "password"
    }
    
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=15)
        data = res.json()
        if res.status_code == 200:
            token = data.get("token") or data.get("access_token") or data.get("data", {}).get("token")
            if token:
                return str(token), None
            return None, data.get("message", "Token not found in response")
        return None, data.get("message", f"HTTP {res.status_code}")
    except Exception as e:
        return None, str(e)

def track_pathao_order(tracking_key):
    token, err = get_pathao_token()
    if not token:
        return f"Token Error: {err}"
    tracking_key = str(tracking_key).strip().replace("+", "")
    url = f"{PATHAO_BASE_URL}/aladdin/api/v1/orders/{tracking_key}/tracking"
    headers = {"authorization": f"Bearer {token}", "accept": "application/json"}
    try:
        res = requests.get(url, headers=headers, timeout=15)
        data = res.json()
        if res.status_code == 200 and data.get("status") == 200:
            status = data.get("data", {}).get("order_status", "unknown").lower()
            status_map = {
                "pending": "পেন্ডিং", "picked": "কুরিয়ারে হস্তান্তরিত",
                "in_transit": "ডেলিভারির পথে", "delivered": "ডেলিভারি সম্পন্ন",
                "cancelled": "বাতিল", "returned": "রিটার্ন"
            }
            return status_map.get(status, f"Status: {status.upper()}")
        return "অর্ডার পাওয়া যায়নি।"
    except Exception as e:
        return f"ট্র্যাকিং ত্রুটি: {str(e)}"

def create_pathao_order(name, phone, address, city_id=1, zone_id=1, area_id=1, item_desc="Premium Kitchenware", cod_amount=0):
    token, err = get_pathao_token()
    if not token:
        return False, err
    url = f"{PATHAO_BASE_URL}/aladdin/api/v1/orders"
    headers = {
        "authorization": f"Bearer {token}",
        "accept": "application/json",
        "content-type": "application/json"
    }
    phone = format_phone(phone)
    payload = {
        "store_id": int(PATHAO_STORE_ID) if PATHAO_STORE_ID else 0,
        "recipient_name": str(name),
        "recipient_phone": phone,
        "recipient_address": str(address),
        "recipient_city": int(city_id),
        "recipient_zone": int(zone_id),
        "recipient_area": int(area_id),
        "delivery_type": 48,
        "item_type": 2,
        "special_instruction": "WhatsApp Bot Order",
        "item_quantity": 1,
        "amount_to_collect": int(cod_amount),
        "item_description": str(item_desc)
    }
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=15)
        data = res.json()
        if res.status_code == 200 and data.get("status") == 200:
            return True, data.get("data", {}).get("consignment_id")
        return False, data.get("message", res.text)
    except Exception as e:
        return False, str(e)

def get_pathao_cities():
    token, _ = get_pathao_token()
    if not token: return []
    try:
        res = requests.get(f"{PATHAO_BASE_URL}/aladdin/api/v1/countries/1/city-list",
            headers={"authorization": f"Bearer {token}", "accept": "application/json"}, timeout=10)
        return res.json().get("data", {}).get("data", [])
    except: return []

def get_pathao_zones(city_id):
    token, _ = get_pathao_token()
    if not token: return []
    try:
        res = requests.get(f"{PATHAO_BASE_URL}/aladdin/api/v1/cities/{city_id}/zone-list",
            headers={"authorization": f"Bearer {token}", "accept": "application/json"}, timeout=10)
        return res.json().get("data", {}).get("data", [])
    except: return []

def get_pathao_areas(zone_id):
    token, _ = get_pathao_token()
    if not token: return []
    try:
        res = requests.get(f"{PATHAO_BASE_URL}/aladdin/api/v1/zones/{zone_id}/area-list",
            headers={"authorization": f"Bearer {token}", "accept": "application/json"}, timeout=10)
        return res.json().get("data", {}).get("data", [])
    except: return []

# =====================================================================
# WHATSAPP SEND
# =====================================================================
def send_text(to, body):
    if not PERMANENT_TOKEN or not PHONE_NUMBER_ID:
        return False
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {PERMANENT_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": format_phone(to),
        "type": "text",
        "text": {"body": body}
    }
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=15)
        return res.status_code in (200, 201)
    except:
        return False

def send_buttons(to, body, buttons):
    if not PERMANENT_TOKEN or not PHONE_NUMBER_ID: return False
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {PERMANENT_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": format_phone(to),
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {"buttons": [{"type": "reply", "reply": {"id": b["id"], "title": b["title"]}} for b in buttons[:3]]}
        }
    }
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=15)
        return res.status_code in (200, 201)
    except:
        return False

def send_list_menu(to, body, button_text, sections):
    if not PERMANENT_TOKEN or not PHONE_NUMBER_ID: return False
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {PERMANENT_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": format_phone(to),
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body},
            "action": {"button": button_text, "sections": sections}
        }
    }
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=15)
        return res.status_code in (200, 201)
    except:
        return False

def send_image(to, image_url, caption=""):
    if not PERMANENT_TOKEN or not PHONE_NUMBER_ID: return False
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {PERMANENT_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": format_phone(to),
        "type": "image",
        "image": {"link": image_url, "caption": caption}
    }
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=15)
        return res.status_code in (200, 201)
    except:
        return False

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
    products = get_products()
    if not products: return "কোনো প্রোডাক্ট আপডেট হয়নি।"
    lines = ["📋 আমাদের প্রোডাক্ট:"]
    for p in products:
        lines.append(f"🔹 {p['name']} — {p['price']}৳")
    return "\n".join(lines)

def get_session(phone):
    try: return db_query("SELECT * FROM sessions WHERE phone = ?", (phone,), fetchone=True)
    except: return None

def set_session(phone, state, context=None):
    try:
        ctx = json.dumps(context or {}, ensure_ascii=False)
        existing = get_session(phone)
        if existing:
            db_query("UPDATE sessions SET state = ?, context = ?, last_active = CURRENT_TIMESTAMP WHERE phone = ?",
                     (state, ctx, phone), commit=True)
        else:
            db_query("INSERT INTO sessions (phone, state, context) VALUES (?, ?, ?)", (phone, state, ctx), commit=True)
    except: pass

def get_context(phone):
    try:
        session = get_session(phone)
        return json.loads(session["context"]) if session and session.get("context") else {}
    except: return {}

def update_context(phone, key, value):
    try:
        session = get_session(phone)
        ctx = json.loads(session["context"]) if session and session.get("context") else {}
        ctx[key] = value
        set_session(phone, session["state"] if session else "idle", ctx)
    except: pass

def ensure_user(phone):
    try:
        user = db_query("SELECT * FROM users WHERE phone = ?", (phone,), fetchone=True)
        if not user:
            db_query("INSERT OR IGNORE INTO users (phone) VALUES (?)", (phone,), commit=True)
        else:
            db_query("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE phone = ?", (phone,), commit=True)
    except: pass

def log_message(msg_id, phone, content, msg_type="text"):
    try:
        db_query("INSERT OR IGNORE INTO messages (msg_id, from_number, content, msg_type) VALUES (?, ?, ?, ?)",
                 (msg_id, phone, content, msg_type), commit=True)
    except: pass

def is_rate_limited(phone):
    try:
        one_min_ago = (datetime.utcnow() - timedelta(minutes=1)).isoformat()
        count = db_query("SELECT COUNT(*) as cnt FROM messages WHERE from_number = ? AND created_at > ?",
                         (phone, one_min_ago), fetchone=True)
        return count and count["cnt"] >= 10
    except: return False

# =====================================================================
# AI (Gemini)
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
    else:
        logger.warning("GEMINI_KEY missing")
except Exception as e:
    logger.error("Gemini import failed: %s", e)

def get_ai_answer(user_query, session_context=None):
    if not genai_available or not client:
        return "দুঃখিত প্রিয় গ্রাহক, এখন AI সার্ভিস অফলাইন। প্রতিনিধি শীঘ্রই যোগাযোগ করবেন।"
    try:
        system_instruction = (
            "You are the AI sales assistant for 'Dhaka Exclusive' (Bangladesh).\n"
            "CRITICAL RULES:\n"
            "1. NEVER say 'নমস্কার'. ALWAYS 'প্রিয় গ্রাহক'.\n"
            "2. Short, polite, Bengali replies. Taka only.\n"
            "3. You CAN track orders and take orders.\n\n"
            f"PRODUCTS:\n{format_catalog()}"
        )
        ai_config = types.GenerateContentConfig(
            system_instruction=system_instruction, temperature=0.15, max_output_tokens=500
        )
        response = client.models.generate_content(
            model=MODEL_NAME, contents=user_query, config=ai_config
        )
        return response.text
    except Exception as e:
        logger.error("Gemini error: %s", e)
        return "দুঃখিত প্রিয় গ্রাহক, সিস্টেম ব্যস্ত। প্রতিনিধি শীঘ্রই যোগাযোগ করবেন।"

# =====================================================================
# MAIN PROCESSOR
# =====================================================================
def process_webhook_async(msg, from_number):
    msg_type = msg.get("type")
    msg_id = msg.get("id")

    try:
        existing = db_query("SELECT 1 FROM messages WHERE msg_id = ?", (msg_id,), fetchone=True)
        if existing: return
    except: pass

    log_message(msg_id, from_number, str(msg), msg_type)
    ensure_user(from_number)

    try:
        if is_rate_limited(from_number):
            send_text(from_number, "প্রিয় গ্রাহক, অনেক মেসেজ পাঠিয়েছেন। কিছুক্ষণ অপেক্ষা করুন।")
            return
    except: pass

    if msg_type in ["audio", "voice"]:
        send_text(from_number, "প্রিয় গ্রাহক, ভয়েস মেসেজ সাপোর্টেড নয়। টেক্সটে লিখুন।")
        return

    if msg_type == "image":
        caption = msg.get("image", {}).get("caption", "").lower()
        if any(k in caption for k in ["কত", "দাম", "কিনব", "চাই", "price", "order"]):
            send_text(from_number, "📸 প্রোডাক্ট ছবি পেয়েছি! আমাদের ক্যাটালগ দেখতে 'কিনব' লিখুন।")
            return
        if any(k in caption for k in ["পেমেন্ট", "টাকা", "bkash", "nagad", "paid", "রিসিপ্ট"]):
            send_text(from_number, "💳 পেমেন্ট রিসিপ্ট পেয়েছি! আপনার অর্ডার আইডি দিন।")
            return
        send_text(from_number, "📸 ছবি পেয়েছি! প্রোডাক্ট কিনতে চাইলে 'কিনব' লিখুন।")
        return

    if msg_type != "text":
        send_text(from_number, "প্রিয় গ্রাহক, শুধু টেক্সট বুঝি।")
        return

    user_text = msg["text"]["body"].strip()
    session = get_session(from_number)
    state = session["state"] if session else "idle"
    context = get_context(from_number)

    # ADMIN COMMANDS
    if user_text.lower().startswith("admin:"):
        if from_number not in ADMIN_NUMBERS:
            send_text(from_number, "দুঃখিত, এই কমান্ড শুধু অ্যাডমিনের জন্য।")
            return
        cmd = user_text[6:].strip()
        if cmd.lower().startswith("help"):
            send_text(from_number, "🔧 অ্যাডমিন কমান্ড:\nadmin:addproduct | নাম | দাম | বর্ণনা | [স্টক]\nadmin:stats\nadmin:broadcast মেসেজ")
            return
        send_text(from_number, "অজানা কমান্ড। admin:help লিখুন।")
        return

    # SMART IDLE HANDLERS
    if any(k in user_text.lower() for k in ["তুমি কি কি পারো", "what can you do", "কি কি পারো", "তোমার কাজ কি"]):
        send_text(from_number, "🙋‍♂️ প্রিয় গ্রাহক, আমি আপনাকে সাহায্য করতে পারি:\n\n1️⃣ 🛒 প্রোডাক্ট অর্ডার করতে\n2️⃣ 📦 আপনার অর্ডার ট্র্যাক করতে\n3️⃣ 💰 প্রোডাক্টের দাম ও তথ্য জানতে\n\nকীভাবে সাহায্য করতে পারি?")
        return

    if any(k in user_text.lower() for k in ["অর্ডার কোথায়", "আমার অর্ডার", "ট্র্যাক", "track", "কোথায় আছে", "ডেলিভারি কোথায়"]):
        orders = db_query(
            "SELECT * FROM orders WHERE phone = ? ORDER BY created_at DESC LIMIT 1",
            (from_number,), fetchone=True)
        if orders and orders.get("pathao_consignment_id"):
            live_status = track_pathao_order(orders["pathao_consignment_id"])
            send_text(from_number,
                f"📦 আপনার সর্বশেষ অর্ডার (#{orders['id']}):\n\n📌 স্ট্যাটাস: {live_status}\n🆔 Tracking: {orders['pathao_consignment_id']}")
            return
        else:
            send_text(from_number,
                "📦 অর্ডার ট্র্যাক করতে আপনার ফোন নম্বর বা Tracking ID টি দিন:\n(যেমন: 01712XXXXXX)")
            return

    # Direct phone number tracking
    clean_text = user_text.replace(" ", "").replace("+", "").strip()
    if clean_text.isdigit() and (len(clean_text) == 11 or len(clean_text) == 13) and clean_text.startswith(("01", "8801")):
        live_status = track_pathao_order(clean_text)
        send_text(from_number, f"প্রিয় গ্রাহক, আপনার অর্ডারের অবস্থা:\n\n📌 {live_status}")
        return

    # ORDER FLOW
    if state == "idle" and any(k in user_text.lower() for k in ["কিনব", "অর্ডার", "চাই", "buy", "order"]):
        products = get_products()
        if products:
            sections = [{
                "title": "আমাদের প্রোডাক্ট",
                "rows": [{"id": f"product_{p['id']}", "title": p['name'][:24], "description": f"{p['price']}৳ | স্টক: {p['stock']}"}
                         for p in products[:10]]
            }]
            set_session(from_number, "selecting_product", context={})
            send_list_menu(from_number, "কোন প্রোডাক্ট কিনতে চান?", "প্রোডাক্ট", sections)
            return

    if state == "selecting_product":
        if user_text.startswith("product_"):
            pid = int(user_text.replace("product_", ""))
            product = get_product_by_id(pid)
            if product:
                ctx = {"product_id": pid, "product_name": product["name"], "price": product["price"]}
                set_session(from_number, "selecting_qty", context=ctx)
                if product.get("image_url"):
                    send_image(from_number, product["image_url"], f"🔹 {product['name']}\n💰 {product['price']}৳")
                send_buttons(from_number,
                    f"🔹 {product['name']}\n💰 {product['price']}৳\n📝 {product.get('description', '')}\n\nকতটি চান?",
                    [{"id": "qty_1", "title": "১টি"}, {"id": "qty_2", "title": "২টি"}, {"id": "qty_3", "title": "৩টি"}])
                return
        send_text(from_number, "অনুগ্রহ করে লিস্ট থেকে প্রোডাক্ট বাছাই করুন।")
        return

    if state == "selecting_qty":
        qty_map = {"qty_1": 1, "qty_2": 2, "qty_3": 3, "1": 1, "2": 2, "3": 3, "১": 1, "২": 2, "৩": 3}
        qty = qty_map.get(user_text, 1)
        ctx = get_context(from_number)
        ctx["quantity"] = qty
        ctx["subtotal"] = ctx["price"] * qty
        set_session(from_number, "awaiting_name", context=ctx)
        send_text(from_number, f"✅ {qty}টি '{ctx['product_name']}'। আপনার সম্পূর্ণ নাম:")
        return

    if state == "awaiting_name":
        update_context(from_number, "name", user_text)
        set_session(from_number, "awaiting_phone", context=get_context(from_number))
        send_text(from_number, "ধন্যবাদ! এখন ১১ সংখ্যার মোবাইল নম্বর (যেমন: 01712XXXXXX):")
        return

    if state == "awaiting_phone":
        clean = user_text.replace(" ", "").replace("+", "").replace("-", "")
        if not (clean.startswith("01") and len(clean) == 11):
            send_text(from_number, "❌ সঠিক বাংলাদেশি নম্বর দিন (যেমন: 01712XXXXXX):")
            return
        update_context(from_number, "phone", clean)
        set_session(from_number, "awaiting_address", context=get_context(from_number))
        send_text(from_number, "অসাধারণ! সম্পূর্ণ ডেলিভারি ঠিকানা:")
        return

    if state == "awaiting_address":
        update_context(from_number, "address", user_text)
        ctx = get_context(from_number)
        cities = get_pathao_cities()
        if cities:
            sections = [{"title": "শহর", "rows": [{"id": f"city_{c['city_id']}", "title": c['city_name'][:24]} for c in cities[:10]]}]
            set_session(from_number, "selecting_city", context=ctx)
            send_list_menu(from_number, "ডেলিভারির জন্য শহর বাছাই করুন:", "শহর", sections)
            return
        ctx["city_id"] = 1
        set_session(from_number, "selecting_payment", context=ctx)
        send_payment_options(from_number, ctx)
        return

    if state == "selecting_city":
        if user_text.startswith("city_"):
            city_id = int(user_text.replace("city_", ""))
            ctx = get_context(from_number)
            ctx["city_id"] = city_id
            zones = get_pathao_zones(city_id)
            if zones:
                sections = [{"title": "জোন", "rows": [{"id": f"zone_{z['zone_id']}", "title": z['zone_name'][:24]} for z in zones[:10]]}]
                set_session(from_number, "selecting_zone", context=ctx)
                send_list_menu(from_number, "জোন বাছাই করুন:", "জোন", sections)
                return
            ctx["zone_id"] = 1
            ctx["area_id"] = 1
            set_session(from_number, "selecting_payment", context=ctx)
            send_payment_options(from_number, ctx)
            return
        send_text(from_number, "অনুগ্রহ করে লিস্ট থেকে শহর বাছাই করুন।")
        return

    if state == "selecting_zone":
        if user_text.startswith("zone_"):
            zone_id = int(user_text.replace("zone_", ""))
            ctx = get_context(from_number)
            ctx["zone_id"] = zone_id
            areas = get_pathao_areas(zone_id)
            if areas:
                sections = [{"title": "এরিয়া", "rows": [{"id": f"area_{a['area_id']}", "title": a['area_name'][:24]} for a in areas[:10]]}]
                set_session(from_number, "selecting_area", context=ctx)
                send_list_menu(from_number, "এরিয়া বাছাই করুন:", "এরিয়া", sections)
                return
            ctx["area_id"] = 1
            set_session(from_number, "selecting_payment", context=ctx)
            send_payment_options(from_number, ctx)
            return
        send_text(from_number, "অনুগ্রহ করে লিস্ট থেকে জোন বাছাই করুন।")
        return

    if state == "selecting_area":
        if user_text.startswith("area_"):
            area_id = int(user_text.replace("area_", ""))
            ctx = get_context(from_number)
            ctx["area_id"] = area_id
            set_session(from_number, "selecting_payment", context=ctx)
            send_payment_options(from_number, ctx)
            return
        send_text(from_number, "অনুগ্রহ করে লিস্ট থেকে এরিয়া বাছাই করুন।")
        return

    if state == "selecting_payment":
        ctx = get_context(from_number)
        ctx["payment_method"] = "cod"
        ctx["delivery_charge"] = 80
        ctx["total"] = ctx["subtotal"] + 80
        set_session(from_number, "awaiting_confirmation", context=ctx)
        summary = (
            f"📦 ফাইনাল অর্ডার\n━━━━━━━━━━━━━━\n"
            f"🔹 {ctx['product_name']} x {ctx['quantity']}\n"
            f"💰 প্রাইস: {ctx['subtotal']}৳\n🚚 ডেলিভারি: {ctx['delivery_charge']}৳\n"
            f"━━━━━━━━━━━━━━\n💵 মোট: {ctx['total']}৳\n"
            f"👤 {ctx['name']}\n📞 {ctx['phone']}\n📍 {ctx['address']}\n\n"
            f"অর্ডার কনফার্ম করতে 'হ্যাঁ' লিখুন।"
        )
        send_buttons(from_number, summary,
            [{"id": "confirm_yes", "title": "✅ হ্যাঁ"}, {"id": "confirm_no", "title": "❌ না"}])
        return

    if state == "awaiting_confirmation":
        if user_text in ["হ্যাঁ", "yes", "confirm_yes", "✅ হ্যাঁ"]:
            ctx = get_context(from_number)
            cod_amount = ctx["total"] if ctx.get("payment_method") == "cod" else 0
            success, result = create_pathao_order(
                name=ctx.get("name"), phone=ctx.get("phone"), address=ctx.get("address"),
                city_id=ctx.get("city_id", 1), zone_id=ctx.get("zone_id", 1), area_id=ctx.get("area_id", 1),
                item_desc=f"{ctx['product_name']} x{ctx['quantity']}", cod_amount=cod_amount
            )
            if success:
                db_query(
                    "INSERT INTO orders (phone, name, address, city_id, zone_id, area_id, product_id, quantity, price, delivery_charge, discount, total, payment_method, pathao_consignment_id, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ctx.get("phone"), ctx.get("name"), ctx.get("address"), ctx.get("city_id", 1),
                     ctx.get("zone_id", 1), ctx.get("area_id", 1), ctx.get("product_id"), ctx.get("quantity"),
                     ctx.get("subtotal"), ctx.get("delivery_charge", 80), ctx.get("discount", 0),
                     ctx.get("total"), ctx.get("payment_method", "cod"), str(result), "created"),
                    commit=True)
                db_query("UPDATE users SET total_orders = total_orders + 1, total_spent = total_spent + ? WHERE phone = ?",
                         (ctx.get("total", 0), from_number), commit=True)
                send_text(from_number, f"🎉 অর্ডার সফল!\n📦 Tracking: {result}\n🚚 পাঠাও কুরিয়ার আসবে।\nধন্যবাদ প্রিয় গ্রাহক! 🙏")
            else:
                db_query(
                    "INSERT INTO orders (phone, name, address, city_id, zone_id, area_id, product_id, quantity, price, delivery_charge, discount, total, payment_method, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ctx.get("phone"), ctx.get("name"), ctx.get("address"), ctx.get("city_id", 1),
                     ctx.get("zone_id", 1), ctx.get("area_id", 1), ctx.get("product_id"), ctx.get("quantity"),
                     ctx.get("subtotal"), ctx.get("delivery_charge", 80), ctx.get("discount", 0),
                     ctx.get("total"), ctx.get("payment_method", "cod"), "manual_pending"),
                    commit=True)
                send_text(from_number, f"⚠️ কুরিয়ার API ত্রুটি: {result}\nঅর্ডার ম্যানুয়ালি নোট। প্রতিনিধি কল করে কনফার্ম করবেন।")
            set_session(from_number, "idle", {})
            return
        else:
            send_text(from_number, "অর্ডার বাতিল। আপনাকে কীভাবে সাহায্য করতে পারি?")
            set_session(from_number, "idle", {})
            return

    # AI FALLBACK
    ai_response = get_ai_answer(user_text, context)

    # Buy intent fallback
    if any(k in user_text.lower() for k in ["কিনব", "অর্ডার", "চাই", "buy", "order", "দাম"]):
        products = get_products()
        if products:
            sections = [{"title": "প্রোডাক্ট", "rows": [{"id": f"product_{p['id']}", "title": p['name'][:24], "description": f"{p['price']}৳"} for p in products[:10]]}]
            set_session(from_number, "selecting_product", context={})
            send_list_menu(from_number, "কোন প্রোডাক্টটি দেখতে চান?", "প্রোডাক্ট", sections)
            return

    send_text(from_number, ai_response)

def send_payment_options(to, ctx):
    subtotal = ctx.get("subtotal", 0)
    send_buttons(to, f"💰 সাবটোটাল: {subtotal}৳\n\nপেমেন্ট মেথড:", [
        {"id": "pay_cod", "title": "💵 COD"},
        {"id": "pay_bkash", "title": "📱 bKash"},
        {"id": "pay_nagad", "title": "💳 Nagad"}
    ])

# =====================================================================
# FLASK ROUTES
# =====================================================================
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "running",
        "service": f"{BUSINESS_NAME} WhatsApp Bot",
        "version": "3.2",
        "timestamp": datetime.utcnow().isoformat()
    })

@app.route("/health", methods=["GET"])
def health():
    try:
        db_query("SELECT 1", fetchone=True)
        db_ok = True
    except:
        db_ok = False
    return jsonify({"status": "healthy" if db_ok else "unhealthy", "database": db_ok})

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
    except Exception as e:
        logger.error("Webhook error: %s", e)
    return "ok", 200

def verify_meta_signature(payload, signature):
    if not APP_SECRET:
        return True
    if not signature:
        return False
    try:
        expected = hmac.new(APP_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        received = signature.replace("sha256=", "")
        return hmac.compare_digest(expected, received)
    except:
        return False

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
table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:12px;text-align:left;border-bottom:1px solid #e5e7eb}
.conv-row:hover{background:#f9fafb}
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
<button class="tab-btn" onclick="showTab('settings')">⚙️ Settings</button>
</div>
</div>
<div class="container">
<div id="dashboard" class="section active">
<div class="card"><div class="card-header"><h2>📊 ওভারভিউ</h2></div>
<div style="padding:20px">মোট অর্ডার: {{ stats.total_orders }}<br>মোট কাস্টমার: {{ stats.users }}</div></div>
</div>
<div id="products" class="section">
<div class="card"><div class="card-header"><h2>📦 প্রোডাক্ট</h2></div>
<table><tr><th>ID</th><th>নাম</th><th>দাম</th><th>স্টক</th></tr>
{% for p in products %}<tr><td>#{{ p.id }}</td><td>{{ p.name }}</td><td>৳{{ p.price }}</td><td>{{ p.stock }}</td></tr>{% endfor %}
</table></div></div>
<div id="orders" class="section">
<div class="card"><div class="card-header"><h2>🛒 অর্ডার</h2></div>
<table><tr><th>ID</th><th>কাস্টমার</th><th>ফোন</th><th>স্ট্যাটাস</th></tr>
{% for o in orders %}<tr><td>#{{ o.id }}</td><td>{{ o.name or 'N/A' }}</td><td>{{ o.phone }}</td><td>{{ o.status }}</td></tr>{% endfor %}
</table></div></div>
<div id="messages" class="section">
<div class="card"><div class="card-header"><h2>💬 কাস্টমার মেসেজেস</h2></div>
<div style="display:flex;height:500px">
<div style="width:35%;border-right:1px solid #eee;overflow-y:auto">{{CONVERSATION_LIST}}</div>
<div style="flex:1;display:flex;flex-direction:column">
<div id="chatBox" style="flex:1;padding:15px;overflow-y:auto;background:#f9f9f9">সেলেক্ট করুন</div>
<div style="padding:10px;border-top:1px solid #eee;display:flex;gap:5px">
<input type="text" id="replyText" style="flex:1;padding:8px;border-radius:5px;border:1px solid #ddd" placeholder="মেসেজ লিখুন...">
<button class="btn" onclick="sendReply()">পাঠান</button>
</div></div></div></div></div>
<div id="settings" class="section">
<div class="card"><div class="card-header"><h2>⚙️ Settings</h2></div>
<div style="padding:20px;max-width:600px">
<div class="form-group"><label>বিজনেস নাম</label><input type="text" id="settingName" value="{{ settings.business_name }}"></div>
<div class="form-group"><label>লোগো URL</label><input type="text" id="settingLogo" value="{{ settings.logo_url }}"></div>
<button class="btn" onclick="saveSettings()">সেভ করুন</button>
</div></div></div>
</div>
<script>
function showTab(id){
  document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  event.target.classList.add('active');
}
let activePhone='';
function loadConversation(phone,name){
  activePhone=phone;
  fetch('/admin/api/conversations/'+encodeURIComponent(phone)).then(r=>r.json()).then(d=>{
    let h='';
    d.messages.forEach(m=>{
      let cls=m.direction==='out'?'text-align:right':'text-align:left';
      let col=m.direction==='out'?'#667eea':'#fff';
      let txt=m.direction==='out'?'#fff':'#333';
      h+=`<div style="${cls};margin-bottom:10px"><span style="background:${col};color:${txt};padding:8px 12px;border-radius:10px;display:inline-block">${m.content}</span></div>`;
    });
    document.getElementById('chatBox').innerHTML=h;
  });
}
function sendReply(){
  let msg=document.getElementById('replyText').value;
  if(!msg||!activePhone) return;
  fetch('/admin/api/reply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:activePhone,message:msg})})
  .then(()=>{document.getElementById('replyText').value='';loadConversation(activePhone,'');});
}
function saveSettings(){
  let data={business_name:document.getElementById('settingName').value,logo_url:document.getElementById('settingLogo').value};
  fetch('/admin/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})
  .then(()=>alert('সেভ হয়েছে!'));
}
</script>
</body></html>"""

@app.route("/admin", methods=["GET"])
@login_required
def admin_dashboard():
    try:
        settings = get_all_settings()
        stats = {"total_orders": 0, "revenue": 0, "users": 0, "pending": 0}
        total_orders = db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)
        users = db_query("SELECT COUNT(*) as c FROM users", fetchone=True)
        pending = db_query("SELECT COUNT(*) as c FROM orders WHERE status IN ('pending', 'created')", fetchone=True)
        if total_orders: stats["total_orders"] = total_orders["c"]
        if users: stats["users"] = users["c"]
        if pending: stats["pending"] = pending["c"]
        products = db_query("SELECT * FROM products ORDER BY id DESC", fetchall=True) or []
        orders = db_query("SELECT * FROM orders ORDER BY id DESC", fetchall=True) or []
        users_list = db_query("SELECT * FROM users ORDER BY last_active DESC", fetchall=True) or []
        if not products:
            db_query("INSERT INTO products (name, price, description, stock, image_url) VALUES (?, ?, ?, ?, ?)", ("পেস্টেল কুর্তি", 1299, "সুন্দর পেস্টেল কালার কুর্তি", 15, ""), commit=True)
            products = db_query("SELECT * FROM products ORDER BY id DESC", fetchall=True) or []
        html = ADMIN_HTML
        html = html.replace("{{ settings.business_name }}", settings.get("business_name", "Dhaka Exclusive"))
        html = html.replace("{{ settings.header_color }}", settings.get("header_color", "#1f2937"))
        html = html.replace("{{ settings.primary_color }}", settings.get("primary_color", "#667eea"))
        html = html.replace("{{ stats.total_orders }}", str(stats["total_orders"]))
        html = html.replace("{{ stats.users }}", str(stats["users"]))
        html = html.replace("{{ stats.pending }}", str(stats["pending"]))
        
        # Build products rows
        prod_rows = ""
        for p in products:
            prod_rows += f"<tr><td>#{p['id']}</td><td>{p['name']}</td><td>৳{p['price']}</td><td>{p['stock']}</td></tr>"
        html = html.replace("{% for p in products %}", "")
        html = html.replace("{% endfor %}", "")
        import re
        html = re.sub(r'<tr>.*products.*?</tr>', prod_rows, html, flags=re.DOTALL)
        
        # Build orders rows
        order_rows = ""
        for o in orders:
            order_rows += f"<tr><td>#{o['id']}</td><td>{o.get('name') or 'N/A'}</td><td>{o['phone']}</td><td>{o['status']}</td></tr>"
        html = html.replace("{% for o in orders %}", "")
        html = html.replace("{% endfor %}", "")
        html = re.sub(r'<tr>.*orders.*?</tr>', order_rows, html, flags=re.DOTALL)
        
        # Build conversation list
        conv_rows = ""
        msg_rows = db_query("""
            SELECT from_number as phone, content, msg_type, created_at,
                   ROW_NUMBER() OVER (PARTITION BY from_number ORDER BY created_at DESC) as rn
            FROM messages ORDER BY created_at DESC
        """, fetchall=True) or []
        seen_conv = set()
        for r in msg_rows:
            phone = r["phone"]
            if phone in seen_conv: continue
            seen_conv.add(phone)
            user = db_query("SELECT name FROM users WHERE phone = ?", (phone,), fetchone=True)
            name = user["name"] if user else None
            display = name or phone
            last_msg = (r["content"] or "")[:40]
            last_time = r["created_at"][11:16] if r["created_at"] else ""
            conv_rows += f"<div class='conv-row' onclick=\"loadConversation('{phone}','{name or ''}')\" style='padding:12px 16px;border-bottom:1px solid #f3f4f6;cursor:pointer;transition:.2s'><div style='font-weight:600;font-size:14px;color:#111827'>{display}</div><div style='font-size:12px;color:#6b7280;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>{last_msg}</div><div style='font-size:11px;color:#9ca3af;margin-top:2px'>{last_time}</div></div>"
        if not conv_rows:
            conv_rows = "<div style='padding:20px;text-align:center;color:#9ca3af'>কোনো মেসেজ নেই</div>"
        html = html.replace("{{CONVERSATION_LIST}}", conv_rows)
        
        return html
    except Exception as e:
        logger.exception("Admin dashboard error")
        return f"<h3>Admin Panel Error:</h3><pre>{str(e)}</pre>", 500

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
            messages.append({
                "content": content,
                "direction": "out" if (m["msg_type"] == "out" or m.get("direction") == "out") else "in",
                "time": m["created_at"][11:16] if m["created_at"] else ""
            })
        return jsonify({"success": True, "messages": messages})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/api/reply", methods=["POST"])
@login_required
def admin_reply():
    data = request.get_json() or {}
    phone = data.get("phone", "").strip()
    msg = data.get("message", "").strip()
    if not phone or not msg:
        return jsonify({"error": "Phone or message missing"}), 400
    try:
        send_text(phone, msg)
        db_query("INSERT INTO messages (msg_id, from_number, content, msg_type) VALUES (?, ?, ?, ?)",
                 (f"admin_{int(time.time())}", phone, msg, "out"), commit=True)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/admin/api/settings", methods=["POST"])
@login_required
def admin_save_settings():
    data = request.get_json() or {}
    for key in ["business_name", "logo_url", "primary_color", "header_color", "accent_color"]:
        if key in data: set_setting(key, data[key])
    return jsonify({"success": True, "message": "Settings saved!"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
