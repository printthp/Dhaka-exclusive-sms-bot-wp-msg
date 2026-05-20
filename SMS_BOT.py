import os
import sys
import json
import re
import sqlite3
import time
import hmac
import hashlib
import logging
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from google import genai
from google.genai import types
from threading import Thread, Lock, Timer
import requests

# =====================================================================
# 🔧 ০. লগিং
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# =====================================================================
# ⚙️ ১. ENV সিক্রেটস
# =====================================================================
PERMANENT_TOKEN = os.environ.get("PERMANENT_TOKEN", "EAANtSb24BiwBRREXu8HztnpOLtamcKIvi09Qb24LiYax45S4aoYtFEVKEQZAxigfO2wbGf6RgHh51IURbQzKKrzPhkcprLxHpZBfOwxZAVCscdVOpjbapbS9sOLCIqZBM8tZAtSRRaVVYSTZBjUkkPZAQaLABSnG6cQcgQcwqZBC5I5yrB4cXgoUPDlzzn7HzUwsMAZDZD")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "1039959469208417")
GEMINI_KEY = os.environ.get("GEMINI_KEY", "AIzaSyDICBRwj4wdwmqlut_Xjf0GgvXx_Mjcc0Q")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "dhakaex0020")
APP_SECRET = os.environ.get("APP_SECRET", "")

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

client = genai.Client(api_key=GEMINI_KEY)
MODEL_NAME = "gemini-2.5-flash"

# =====================================================================
# 🗄️ ২. SQLite DB — নতুন নাম, পুরোনো schema conflict এড়াতে
# =====================================================================
DB_FILE = "bot_v3.db"  # নতুন নাম — পুরোনো DB conflict থেকে মুক্তি
db_lock = Lock()

def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        c = conn.cursor()
        
        # messages টেবিল (msg_type সহ)
        c.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                msg_id TEXT PRIMARY KEY,
                from_number TEXT,
                content TEXT,
                msg_type TEXT DEFAULT 'text',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # sessions
        c.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                phone TEXT PRIMARY KEY,
                state TEXT DEFAULT 'idle',
                context TEXT DEFAULT '{}',
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # orders
        c.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT,
                name TEXT,
                address TEXT,
                city_id INTEGER DEFAULT 1,
                zone_id INTEGER DEFAULT 1,
                area_id INTEGER DEFAULT 1,
                product_id INTEGER,
                quantity INTEGER DEFAULT 1,
                price INTEGER,
                delivery_charge INTEGER DEFAULT 80,
                discount INTEGER DEFAULT 0,
                total INTEGER,
                payment_method TEXT DEFAULT 'cod',
                payment_status TEXT DEFAULT 'pending',
                pathao_consignment_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # products
        c.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                price INTEGER,
                description TEXT,
                stock INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1
            )
        """)
        
        # knowledge
        c.execute("""
            CREATE TABLE IF NOT EXISTS knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT DEFAULT 'general',
                content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # coupons
        c.execute("""
            CREATE TABLE IF NOT EXISTS coupons (
                code TEXT PRIMARY KEY,
                discount_percent INTEGER DEFAULT 0,
                discount_amount INTEGER DEFAULT 0,
                max_uses INTEGER DEFAULT 100,
                used_count INTEGER DEFAULT 0,
                valid_until TEXT,
                active INTEGER DEFAULT 1
            )
        """)
        
        # users
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                phone TEXT PRIMARY KEY,
                name TEXT,
                language TEXT DEFAULT 'bn',
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_orders INTEGER DEFAULT 0,
                total_spent INTEGER DEFAULT 0
            )
        """)
        
        conn.commit()
        conn.close()
        logger.info("✅ Database initialized: %s", DB_FILE)

init_db()

def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        try:
            c.execute(query, params)
            if commit:
                conn.commit()
                conn.close()
                return True
            if fetchone:
                row = c.fetchone()
                conn.close()
                return dict(row) if row else None
            if fetchall:
                rows = c.fetchall()
                conn.close()
                return [dict(r) for r in rows]
            conn.close()
            return None
        except sqlite3.OperationalError as e:
            conn.close()
            logger.error("DB Error: %s | Query: %s", e, query)
            raise

# =====================================================================
# 🔧 ৩. হেলপারস
# =====================================================================
def format_phone(num):
    num = str(num).strip().replace(" ", "").replace("-", "").replace("+", "")
    if num.startswith("01") and len(num) == 11:
        num = "88" + num
    return num

def extract_json_block(text):
    start = text.find("{")
    if start == -1:
        return None
    brace_count = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            brace_count += 1
        elif ch == "}":
            brace_count -= 1
            if brace_count == 0:
                return text[start:i+1]
    return None

def is_within_business_hours():
    try:
        start, end = BUSINESS_HOURS.split("-")
        now = datetime.now().strftime("%H:%M")
        return start <= now <= end
    except:
        return True

# =====================================================================
# 🛡️ ৪. Webhook Verify (APP_SECRET না থাকলে skip)
# =====================================================================
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
# 🚚 ৫. পাঠাও API
# =====================================================================
def api_post_retry(url, payload, headers, max_retries=3):
    for attempt in range(max_retries):
        try:
            res = requests.post(url, json=payload, headers=headers, timeout=15)
            if res.status_code in (200, 201):
                return res
            if res.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            return res
        except Exception as e:
            logger.warning("API attempt %d fail: %s", attempt + 1, e)
            time.sleep(2 ** attempt)
    return None

def get_pathao_token():
    if not all([PATHAO_CLIENT_ID, PATHAO_CLIENT_SECRET, PATHAO_MERCHANT_EMAIL, PATHAO_MERCHANT_PASSWORD]):
        return None, "Pathao credentials missing"
    url = f"{PATHAO_BASE_URL}/aladdin/api/v1/issue-token"
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "X-API-KEY": PATHAO_CLIENT_ID,
        "X-SECRET-KEY": PATHAO_CLIENT_SECRET
    }
    payload = {
        "client_id": PATHAO_CLIENT_ID,
        "client_secret": PATHAO_CLIENT_SECRET,
        "username": PATHAO_MERCHANT_EMAIL,
        "password": PATHAO_MERCHANT_PASSWORD
    }
    res = api_post_retry(url, payload, headers)
    if not res:
        return None, "Network error"
    data = res.json()
    if res.status_code == 200 and data.get("status") == 200:
        return data.get("token"), None
    return None, data.get("message", res.text)

def get_pathao_cities():
    token, _ = get_pathao_token()
    if not token:
        return []
    try:
        res = requests.get(
            f"{PATHAO_BASE_URL}/aladdin/api/v1/countries/1/city-list",
            headers={"authorization": f"Bearer {token}", "accept": "application/json"},
            timeout=10
        )
        return res.json().get("data", {}).get("data", [])
    except:
        return []

def get_pathao_zones(city_id):
    token, _ = get_pathao_token()
    if not token:
        return []
    try:
        res = requests.get(
            f"{PATHAO_BASE_URL}/aladdin/api/v1/cities/{city_id}/zone-list",
            headers={"authorization": f"Bearer {token}", "accept": "application/json"},
            timeout=10
        )
        return res.json().get("data", {}).get("data", [])
    except:
        return []

def get_pathao_areas(zone_id):
    token, _ = get_pathao_token()
    if not token:
        return []
    try:
        res = requests.get(
            f"{PATHAO_BASE_URL}/aladdin/api/v1/zones/{zone_id}/area-list",
            headers={"authorization": f"Bearer {token}", "accept": "application/json"},
            timeout=10
        )
        return res.json().get("data", {}).get("data", [])
    except:
        return []

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
    res = api_post_retry(url, payload, headers)
    if not res:
        return False, "API timeout"
    data = res.json()
    if res.status_code == 200 and data.get("status") == 200:
        return True, data.get("data", {}).get("consignment_id")
    return False, data.get("message", res.text)

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
                "pending": "পেন্ডিং",
                "picked": "কুরিয়ারে হস্তান্তরিত",
                "in_transit": "ডেলিভারির পথে",
                "delivered": "ডেলিভারি সম্পন্ন 🎉",
                "cancelled": "বাতিল",
                "returned": "রিটার্ন"
            }
            return status_map.get(status, f"Status: {status.upper()}")
        return "অর্ডার পাওয়া যায়নি।"
    except:
        return "ট্র্যাকিং ত্রুটি।"

# =====================================================================
# 📲 ৬. WhatsApp Send Methods
# =====================================================================
def send_text(to, body):
    if not PERMANENT_TOKEN or not PHONE_NUMBER_ID:
        logger.error("WhatsApp credentials missing")
        return False
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {PERMANENT_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": format_phone(to),
        "type": "text",
        "text": {"body": body}
    }
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=15)
        return res.status_code in (200, 201)
    except Exception as e:
        logger.error("Send text error: %s", e)
        return False

def send_buttons(to, body, buttons):
    if not PERMANENT_TOKEN or not PHONE_NUMBER_ID:
        return False
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {PERMANENT_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": format_phone(to),
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                    for b in buttons[:3]
                ]
            }
        }
    }
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=15)
        return res.status_code in (200, 201)
    except Exception as e:
        logger.error("Send buttons error: %s", e)
        return False

def send_list_menu(to, body, button_text, sections):
    if not PERMANENT_TOKEN or not PHONE_NUMBER_ID:
        return False
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {PERMANENT_TOKEN}",
        "Content-Type": "application/json"
    }
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
    except Exception as e:
        logger.error("Send list error: %s", e)
        return False

def send_image(to, image_url, caption=""):
    if not PERMANENT_TOKEN or not PHONE_NUMBER_ID:
        return False
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {PERMANENT_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": format_phone(to),
        "type": "image",
        "image": {"link": image_url, "caption": caption}
    }
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=15)
        return res.status_code in (200, 201)
    except Exception as e:
        logger.error("Send image error: %s", e)
        return False

# =====================================================================
# 🤖 ৭. Gemini AI
# =====================================================================
def read_knowledge():
    rows = db_query("SELECT content FROM knowledge ORDER BY created_at DESC", fetchall=True)
    if not rows:
        return "Brand: Dhaka Exclusive. Bangladesh. Premium kitchenware."
    return "\n".join([r["content"] for r in rows])

def save_knowledge(category, content):
    db_query(
        "INSERT INTO knowledge (category, content) VALUES (?, ?)",
        (category, content),
        commit=True
    )

def get_ai_answer(user_query, session_context=None):
    try:
        saved_knowledge = read_knowledge()
        products_text = format_catalog()
        system_instruction = (
            "You are the AI sales assistant for 'Dhaka Exclusive'.\n"
            "CRITICAL:\n"
            "1. NEVER say 'নমস্কার'. ALWAYS use 'প্রিয় গ্রাহক'.\n"
            "2. Short, polite, Bengali replies. Taka only.\n"
            "3. To track: append ||TRACK_DATA||{'key':'VALUE'}||\n"
            "4. If Name+Phone+Address confirmed: append ||ORDER_DATA||{'name':'N','phone':'P','address':'A'}||\n\n"
            f"PRODUCTS:\n{products_text}\n\n"
            f"KNOWLEDGE:\n{saved_knowledge}\n\n"
            f"CONTEXT: {json.dumps(session_context or {}, ensure_ascii=False)}"
        )
        ai_config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.15,
            max_output_tokens=500
        )
        response = client.models.generate_content(
            model=MODEL_NAME, contents=user_query, config=ai_config
        )
        return response.text
    except Exception as e:
        logger.error("Gemini error: %s", e)
        return "দুঃখিত প্রিয় গ্রাহক, সিস্টেম ব্যস্ত। প্রতিনিধি শীঘ্রই যোগাযোগ করবেন।"

# =====================================================================
# 📦 ৮. Product, Coupon, Invoice
# =====================================================================
def get_products():
    return db_query("SELECT * FROM products WHERE active = 1", fetchall=True)

def get_product_by_id(pid):
    return db_query("SELECT * FROM products WHERE id = ?", (pid,), fetchone=True)

def add_product(name, price, description, stock=10):
    db_query(
        "INSERT INTO products (name, price, description, stock) VALUES (?, ?, ?, ?)",
        (name, price, description, stock),
        commit=True
    )

def update_stock(product_id, qty_sold):
    db_query(
        "UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
        (qty_sold, product_id, qty_sold),
        commit=True
    )

def format_catalog():
    products = get_products()
    if not products:
        return "কোনো প্রোডাক্ট আপডেট হয়নি।"
    lines = ["📋 *আমাদের প্রোডাক্ট:*"]
    for p in products:
        lines.append(
            f"\n🔹 *{p['name']}* — {p['price']}৳\n"
            f"📝 {p['description']}\n"
            f"📦 স্টক: {p['stock']}টি"
        )
    return "\n".join(lines)

def validate_coupon(code):
    row = db_query(
        "SELECT * FROM coupons WHERE code = ? AND active = 1",
        (code.upper(),),
        fetchone=True
    )
    if not row:
        return None, "কুপন সঠিক নয়।"
    if row["used_count"] >= row["max_uses"]:
        return None, "কুপন শেষ।"
    if row["valid_until"] and datetime.now().isoformat() > row["valid_until"]:
        return None, "মেয়াদ শেষ।"
    return row, None

def apply_coupon(code, original_price):
    coupon, err = validate_coupon(code)
    if not coupon:
        return original_price, err
    if coupon["discount_percent"] > 0:
        discount = int(original_price * coupon["discount_percent"] / 100)
        return original_price - discount, None
    elif coupon["discount_amount"] > 0:
        return max(0, original_price - coupon["discount_amount"]), None
    return original_price, "কুপনে ডিসকাউন্ট নেই।"

def use_coupon(code):
    db_query(
        "UPDATE coupons SET used_count = used_count + 1 WHERE code = ?",
        (code.upper(),),
        commit=True
    )

def generate_invoice_text(order_row):
    return (
        f"🧾 *অর্ডার ইনভয়েস*\n"
        f"━━━━━━━━━━━━━━\n"
        f"🆔 অর্ডার ID: #{order_row['id']}\n"
        f"👤 নাম: {order_row['name']}\n"
        f"📞 ফোন: {order_row['phone']}\n"
        f"📍 ঠিকানা: {order_row['address']}\n\n"
        f"💰 প্রোডাক্ট: {order_row['price']}৳\n"
        f"🚚 ডেলিভারি: {order_row['delivery_charge']}৳\n"
        f"🎫 ডিসকাউন্ট: -{order_row['discount']}৳\n"
        f"━━━━━━━━━━━━━━\n"
        f"💵 *মোট: {order_row['total']}৳*\n"
        f"💳 পেমেন্ট: {order_row['payment_method'].upper()}\n"
        f"📦 Tracking: {order_row['pathao_consignment_id'] or 'N/A'}\n"
        f"━━━━━━━━━━━━━━"
    )

# =====================================================================
# 🧠 ৯. Session Manager
# =====================================================================
def get_session(phone):
    return db_query("SELECT * FROM sessions WHERE phone = ?", (phone,), fetchone=True)

def set_session(phone, state, context=None):
    ctx = json.dumps(context or {}, ensure_ascii=False)
    existing = get_session(phone)
    if existing:
        db_query(
            "UPDATE sessions SET state = ?, context = ?, last_active = CURRENT_TIMESTAMP WHERE phone = ?",
            (state, ctx, phone),
            commit=True
        )
    else:
        db_query(
            "INSERT INTO sessions (phone, state, context) VALUES (?, ?, ?)",
            (phone, state, ctx),
            commit=True
        )

def update_context(phone, key, value):
    session = get_session(phone)
    ctx = json.loads(session["context"]) if session and session["context"] else {}
    ctx[key] = value
    set_session(phone, session["state"] if session else "idle", ctx)

def get_context(phone):
    session = get_session(phone)
    return json.loads(session["context"]) if session and session["context"] else {}

def ensure_user(phone):
    user = db_query("SELECT * FROM users WHERE phone = ?", (phone,), fetchone=True)
    if not user:
        db_query("INSERT OR IGNORE INTO users (phone) VALUES (?)", (phone,), commit=True)
    else:
        db_query(
            "UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE phone = ?",
            (phone,),
            commit=True
        )
    return user

# =====================================================================
# ⏱️ ১০. Rate Limit
# =====================================================================
def is_rate_limited(phone):
    one_min_ago = (datetime.utcnow() - timedelta(minutes=1)).isoformat()
    count = db_query(
        "SELECT COUNT(*) as cnt FROM messages WHERE from_number = ? AND created_at > ?",
        (phone, one_min_ago),
        fetchone=True
    )
    return count and count["cnt"] >= 10

def log_message(msg_id, phone, content, msg_type="text"):
    try:
        db_query(
            "INSERT OR IGNORE INTO messages (msg_id, from_number, content, msg_type) VALUES (?, ?, ?, ?)",
            (msg_id, phone, content, msg_type),
            commit=True
        )
    except sqlite3.OperationalError:
        # Fallback: msg_type column না থাকলে column ছাড়া insert
        try:
            db_query(
                "INSERT OR IGNORE INTO messages (msg_id, from_number, content) VALUES (?, ?, ?)",
                (msg_id, phone, content),
                commit=True
            )
        except Exception as e2:
            logger.error("log_message fallback error: %s", e2)

# =====================================================================
# 📊 ১১. Dashboard Stats
# =====================================================================
def get_dashboard_stats():
    total_users = db_query("SELECT COUNT(*) as c FROM users", fetchone=True)["c"]
    total_orders = db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)["c"]
    today_orders = db_query(
        "SELECT COUNT(*) as c FROM orders WHERE date(created_at) = date('now')",
        fetchone=True
    )["c"]
    revenue = db_query(
        "SELECT COALESCE(SUM(total), 0) as s FROM orders WHERE status = 'delivered'",
        fetchone=True
    )["s"]
    pending = db_query(
        "SELECT COUNT(*) as c FROM orders WHERE status = 'pending'",
        fetchone=True
    )["c"]
    return {
        "users": total_users,
        "total_orders": total_orders,
        "today_orders": today_orders,
        "revenue": revenue,
        "pending": pending
    }

# =====================================================================
# 📢 ১২. Broadcast
# =====================================================================
def broadcast_message(message_text, exclude_admins=False):
    users = db_query("SELECT phone FROM users", fetchall=True)
    sent = 0
    for u in users:
        phone = u["phone"]
        if exclude_admins and phone in ADMIN_NUMBERS:
            continue
        if send_text(phone, message_text):
            sent += 1
        time.sleep(0.5)
    return sent, len(users)

# =====================================================================
# 🧠 ১৩. Main Processor (State Machine)
# =====================================================================
def process_webhook_async(msg, from_number):
    msg_type = msg.get("type")
    msg_id = msg.get("id")

    existing = db_query("SELECT 1 FROM messages WHERE msg_id = ?", (msg_id,), fetchone=True)
    if existing:
        return
    log_message(msg_id, from_number, str(msg), msg_type)
    ensure_user(from_number)

    if is_rate_limited(from_number):
        send_text(from_number, "প্রিয় গ্রাহক, অনেক মেসেজ পাঠিয়েছেন। কিছুক্ষণ অপেক্ষা করুন।")
        return

    if msg_type in ["audio", "voice"]:
        send_text(from_number, "প্রিয় গ্রাহক, ভয়েস মেসেজ সাপোর্টেড নয়। টেক্সটে লিখুন।")
        return
    if msg_type == "image":
        send_text(from_number, "📸 ছবি পেয়েছি! প্রতিনিধি যাচাই করে রিপ্লাই দেবেন।")
        return
    if msg_type != "text":
        send_text(from_number, "প্রিয় গ্রাহক, শুধু টেক্সট বুঝি।")
        return

    user_text = msg["text"]["body"].strip()
    session = get_session(from_number)
    state = session["state"] if session else "idle"
    context = get_context(from_number)

    # 🔐 Admin
    if user_text.lower().startswith("admin:"):
        if from_number not in ADMIN_NUMBERS:
            send_text(from_number, "দুঃখিত, এই কমান্ড শুধু অ্যাডমিনের জন্য।")
            return
        cmd = user_text[6:].strip()

        if cmd.lower().startswith("addproduct"):
            parts = [p.strip() for p in cmd.split("|")]
            if len(parts) >= 4:
                add_product(
                    parts[1],
                    int(parts[2]),
                    parts[3],
                    stock=int(parts[4]) if len(parts) > 4 else 10
                )
                send_text(from_number, f"✅ '{parts[1]}' যোগ হয়েছে।")
            else:
                send_text(from_number, "ফরম্যাট: admin:addproduct | নাম | দাম | বর্ণনা | [স্টক]")
            return

        if cmd.lower().startswith("knowledge"):
            save_knowledge("general", cmd[9:].strip())
            send_text(from_number, "✅ নলেজ আপডেট।")
            return

        if cmd.lower().startswith("orders"):
            orders = db_query("SELECT * FROM orders ORDER BY created_at DESC LIMIT 5", fetchall=True)
            if orders:
                lines = ["📦 সর্বশেষ অর্ডার:"]
                for o in orders:
                    lines.append(f"\n#{o['id']} | {o['name']} | {o['total']}৳ | {o['status']}")
                send_text(from_number, "\n".join(lines))
            else:
                send_text(from_number, "কোনো অর্ডার নেই।")
            return

        if cmd.lower().startswith("stats"):
            stats = get_dashboard_stats()
            send_text(
                from_number,
                f"📊 ড্যাশবোর্ড:\n"
                f"👤 ইউজার: {stats['users']}\n"
                f"📦 মোট অর্ডার: {stats['total_orders']}\n"
                f"📅 আজ: {stats['today_orders']}\n"
                f"💰 রেভেনিউ: {stats['revenue']}৳\n"
                f"⏳ পেন্ডিং: {stats['pending']}"
            )
            return

        if cmd.lower().startswith("broadcast"):
            message = cmd[9:].strip()
            sent, total = broadcast_message(message, exclude_admins=True)
            send_text(from_number, f"📢 ব্রডকাস্ট! {sent}/{total} জনকে পাঠানো হয়েছে।")
            return

        if cmd.lower().startswith("coupon"):
            parts = [p.strip() for p in cmd.split("|")]
            if len(parts) >= 5:
                code, val, ctype, maxuse = parts[1], int(parts[2]), parts[3], int(parts[4])
                valid = parts[5] if len(parts) > 5 else None
                disc_pct = val if ctype == "percent" else 0
                disc_amt = val if ctype == "amount" else 0
                db_query(
                    "INSERT INTO coupons (code, discount_percent, discount_amount, max_uses, valid_until) VALUES (?, ?, ?, ?, ?)",
                    (code.upper(), disc_pct, disc_amt, maxuse, valid),
                    commit=True
                )
                send_text(from_number, f"🎫 কুপন '{code}' তৈরি!")
            else:
                send_text(from_number, "ফরম্যাট: admin:coupon | CODE | value | percent/amount | max_uses | [YYYY-MM-DD]")
            return

        if cmd.lower().startswith("help"):
            send_text(
                from_number,
                "🔧 অ্যাডমিন কমান্ড:\n\n"
                "admin:addproduct | নাম | দাম | বর্ণনা | [স্টক]\n"
                "admin:knowledge তথ্য\n"
                "admin:orders\n"
                "admin:stats\n"
                "admin:broadcast মেসেজ\n"
                "admin:coupon | CODE | 10 | percent | 100 | 2025-12-31\n"
                "admin:help"
            )
            return

        send_text(from_number, "অজানা কমান্ড। admin:help লিখুন।")
        return

    # ─────────────── STATE MACHINE ───────────────

    if state == "idle" and any(k in user_text.lower() for k in ["কিনব", "অর্ডার", "চাই", "buy", "order"]):
        products = get_products()
        if products:
            sections = [{
                "title": "আমাদের প্রোডাক্ট",
                "rows": [
                    {
                        "id": f"product_{p['id']}",
                        "title": p['name'][:24],
                        "description": f"{p['price']}৳ | স্টক: {p['stock']}"
                    }
                    for p in products[:10]
                ]
            }]
            set_session(from_number, "selecting_product", context={})
            send_list_menu(
                from_number,
                "কোন প্রোডাক্ট কিনতে চান? লিস্ট থেকে বাছাই করুন:",
                "প্রোডাক্ট",
                sections
            )
            return

    if state == "selecting_product":
        if user_text.startswith("product_"):
            pid = int(user_text.replace("product_", ""))
            product = get_product_by_id(pid)
            if product:
                ctx = {
                    "product_id": pid,
                    "product_name": product["name"],
                    "price": product["price"]
                }
                set_session(from_number, "selecting_qty", context=ctx)
                send_buttons(
                    from_number,
                    f"🔹 *{product['name']}*\n"
                    f"💰 {product['price']}৳\n"
                    f"📝 {product['description']}\n\n"
                    f"কতটি চান?",
                    [
                        {"id": "qty_1", "title": "১টি"},
                        {"id": "qty_2", "title": "২টি"},
                        {"id": "qty_3", "title": "৩টি"}
                    ]
                )
                return
        send_text(from_number, "অনুগ্রহ করে লিস্ট থেকে প্রোডাক্ট বাছাই করুন।")
        return

    if state == "selecting_qty":
        qty_map = {
            "qty_1": 1, "qty_2": 2, "qty_3": 3,
            "1": 1, "2": 2, "3": 3,
            "১": 1, "২": 2, "৩": 3
        }
        qty = qty_map.get(user_text, 1)
        ctx = get_context(from_number)
        ctx["quantity"] = qty
        ctx["subtotal"] = ctx["price"] * qty
        set_session(from_number, "awaiting_name", context=ctx)
        send_text(
            from_number,
            f"✅ {qty}টি '{ctx['product_name']}'। আপনার সম্পূর্ণ নাম:"
        )
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
            sections = [{
                "title": "শহর",
                "rows": [
                    {"id": f"city_{c['city_id']}", "title": c['city_name'][:24]}
                    for c in cities[:10]
                ]
            }]
            set_session(from_number, "selecting_city", context=ctx)
            send_list_menu(
                from_number,
                "ডেলিভারির জন্য শহর বাছাই করুন:",
                "শহর",
                sections
            )
            return
        ctx["city_id"] = 1
        ctx["city_name"] = "ঢাকা"
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
                sections = [{
                    "title": "জোন",
                    "rows": [
                        {"id": f"zone_{z['zone_id']}", "title": z['zone_name'][:24]}
                        for z in zones[:10]
                    ]
                }]
                set_session(from_number, "selecting_zone", context=ctx)
                send_list_menu(from_number, "জোন বাছাই করুন:", "জোন", sections)
                return
            else:
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
                sections = [{
                    "title": "এরিয়া",
                    "rows": [
                        {"id": f"area_{a['area_id']}", "title": a['area_name'][:24]}
                        for a in areas[:10]
                    ]
                }]
                set_session(from_number, "selecting_area", context=ctx)
                send_list_menu(from_number, "এরিয়া বাছাই করুন:", "এরিয়া", sections)
                return
            else:
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
        if user_text.lower() in ["cod", "cash", "ক্যাশ", "ক্যাশঅনডেলিভারি"]:
            ctx = get_context(from_number)
            ctx["payment_method"] = "cod"
            ctx["delivery_charge"] = 80
            ctx["total"] = ctx["subtotal"] + 80
            set_session(from_number, "awaiting_coupon", context=ctx)
            send_buttons(
                from_number,
                f"🎫 কুপন আছে? কোড লিখুন, না থাকলে 'নেই'।\n\n"
                f"💰 সাবটোটাল: {ctx['subtotal']}৳\n"
                f"🚚 ডেলিভারি: {ctx['delivery_charge']}৳\n"
                f"💵 মোট: {ctx['total']}৳",
                [{"id": "no_coupon", "title": "কুপন নেই"}]
            )
            return
        if user_text.lower() in ["bkash", "বিকাশ"]:
            ctx = get_context(from_number)
            ctx["payment_method"] = "bkash"
            ctx["delivery_charge"] = 80
            ctx["total"] = ctx["subtotal"] + 80
            set_session(from_number, "awaiting_coupon", context=ctx)
            send_text(from_number, "💳 bKash নির্বাচন। কুপন আছে? কোড লিখুন, না থাকলে 'নেই'।")
            return
        if user_text.lower() in ["nagad", "নগদ"]:
            ctx = get_context(from_number)
            ctx["payment_method"] = "nagad"
            ctx["delivery_charge"] = 80
            ctx["total"] = ctx["subtotal"] + 80
            set_session(from_number, "awaiting_coupon", context=ctx)
            send_text(from_number, "💳 Nagad নির্বাচন। কুপন আছে? কোড লিখুন, না থাকলে 'নেই'।")
            return
        send_text(from_number, "পেমেন্ট বাছাই করুন: *COD*, *bKash*, *Nagad*")
        return

    if state == "awaiting_coupon":
        ctx = get_context(from_number)
        if user_text.lower() in ["নেই", "no", "nope", "কুপন নেই"]:
            ctx["coupon"] = None
            ctx["discount"] = 0
        else:
            new_total, err = apply_coupon(user_text, ctx["total"])
            if err:
                send_text(from_number, f"⚠️ {err}\nকুপন ছাড়াই এগিয়ে যাচ্ছি...")
                ctx["coupon"] = None
                ctx["discount"] = 0
            else:
                ctx["coupon"] = user_text.upper()
                ctx["discount"] = ctx["total"] - new_total
                ctx["total"] = new_total
                send_text(
                    from_number,
                    f"🎉 কুপন '{user_text.upper()}' প্রযোজ্য! ডিসকাউন্ট: {ctx['discount']}৳"
                )

        set_session(from_number, "awaiting_confirmation", context=ctx)
        ctx = get_context(from_number)
        summary = (
            f"📦 *ফাইনাল অর্ডার*\n"
            f"━━━━━━━━━━━━━━\n"
            f"🔹 {ctx['product_name']} x {ctx['quantity']}\n"
            f"💰 প্রাইস: {ctx['subtotal']}৳\n"
            f"🚚 ডেলিভারি: {ctx['delivery_charge']}৳\n"
        )
        if ctx.get("discount", 0) > 0:
            summary += f"🎫 ডিসকাউন্ট: -{ctx['discount']}৳\n"
        summary += (
            f"━━━━━━━━━━━━━━\n"
            f"💵 *মোট: {ctx['total']}৳*\n"
            f"💳 পেমেন্ট: {ctx['payment_method'].upper()}\n"
            f"👤 {ctx['name']}\n"
            f"📞 {ctx['phone']}\n"
            f"📍 {ctx['address']}\n\n"
            f"অর্ডার কনফার্ম করতে 'হ্যাঁ' লিখুন।"
        )
        send_buttons(
            from_number,
            summary,
            [
                {"id": "confirm_yes", "title": "✅ হ্যাঁ"},
                {"id": "confirm_no", "title": "❌ না"}
            ]
        )
        return

    if state == "awaiting_confirmation":
        if user_text in ["হ্যাঁ", "yes", "confirm_yes", "✅ হ্যাঁ"]:
            ctx = get_context(from_number)
            cod_amount = ctx["total"] if ctx["payment_method"] == "cod" else 0
            success, result = create_pathao_order(
                name=ctx.get("name"),
                phone=ctx.get("phone"),
                address=ctx.get("address"),
                city_id=ctx.get("city_id", 1),
                zone_id=ctx.get("zone_id", 1),
                area_id=ctx.get("area_id", 1),
                item_desc=f"{ctx['product_name']} x{ctx['quantity']}",
                cod_amount=cod_amount
            )
            if success:
                db_query(
                    """INSERT INTO orders (
                        phone, name, address, city_id, zone_id, area_id,
                        product_id, quantity, price, delivery_charge, discount, total,
                        payment_method, pathao_consignment_id, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ctx.get("phone"), ctx.get("name"), ctx.get("address"),
                        ctx.get("city_id", 1), ctx.get("zone_id", 1), ctx.get("area_id", 1),
                        ctx.get("product_id"), ctx.get("quantity"), ctx.get("subtotal"),
                        ctx.get("delivery_charge", 80), ctx.get("discount", 0),
                        ctx.get("total"), ctx.get("payment_method", "cod"),
                        str(result), "created"
                    ),
                    commit=True
                )
                if ctx.get("coupon"):
                    use_coupon(ctx["coupon"])
                update_stock(ctx.get("product_id"), ctx.get("quantity", 1))
                db_query(
                    "UPDATE users SET total_orders = total_orders + 1, total_spent = total_spent + ? WHERE phone = ?",
                    (ctx.get("total", 0), from_number),
                    commit=True
                )
                send_text(
                    from_number,
                    f"🎉 অর্ডার সফল!\n"
                    f"📦 Tracking: {result}\n"
                    f"🚚 পাঠাও কুরিয়ার আসবে।\n"
                    f"ধন্যবাদ প্রিয় গ্রাহক! 🙏"
                )
            else:
                db_query(
                    """INSERT INTO orders (
                        phone, name, address, city_id, zone_id, area_id,
                        product_id, quantity, price, delivery_charge, discount, total,
                        payment_method, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ctx.get("phone"), ctx.get("name"), ctx.get("address"),
                        ctx.get("city_id", 1), ctx.get("zone_id", 1), ctx.get("area_id", 1),
                        ctx.get("product_id"), ctx.get("quantity"), ctx.get("subtotal"),
                        ctx.get("delivery_charge", 80), ctx.get("discount", 0),
                        ctx.get("total"), ctx.get("payment_method", "cod"),
                        "manual_pending"
                    ),
                    commit=True
                )
                send_text(
                    from_number,
                    f"⚠️ কুরিয়ার API ত্রুটি: {result}\n"
                    f"অর্ডার ম্যানুয়ালি নোট। প্রতিনিধি কল করে কনফার্ম করবেন।"
                )
            set_session(from_number, "idle", {})
            return
        else:
            send_text(from_number, "অর্ডার বাতিল। আপনাকে কীভাবে সাহায্য করতে পারি?")
            set_session(from_number, "idle", {})
            return

    # ─────────────── IDLE STATE ───────────────

    # সরাসরি ট্র্যাকিং
    clean_text = user_text.replace(" ", "").replace("+", "").strip()
    if clean_text.isdigit() and (len(clean_text) == 11 or len(clean_text) == 13) and clean_text.startswith(("01", "8801")):
        live_status = track_pathao_order(clean_text)
        send_text(
            from_number,
            f"প্রিয় গ্রাহক, আপনার অর্ডারের অবস্থা:\n\n📌 {live_status}"
        )
        return

    # অর্ডার হিস্ট্রি
    if any(k in user_text.lower() for k in ["আগের", "হিস্ট্রি", "history", "আগের অর্ডার", "পুরনো"]):
        orders = db_query(
            "SELECT * FROM orders WHERE phone = ? ORDER BY created_at DESC LIMIT 5",
            (from_number,),
            fetchall=True
        )
        if orders:
            lines = ["📦 *আপনার অর্ডার:*"]
            for o in orders:
                lines.append(
                    f"\n🆔 #{o['id']} | {o['total']}৳ | {o['status']} | "
                    f"📦 {o['pathao_consignment_id'] or 'N/A'}"
                )
            send_text(from_number, "\n".join(lines))
        else:
            send_text(from_number, "আপনার কোনো পূর্ববর্তী অর্ডার নেই।")
        return

    # ক্যানসেল
    if any(k in user_text.lower() for k in ["cancel", "বাতিল", "stop"]):
        pending = db_query(
            "SELECT * FROM orders WHERE phone = ? AND status IN ('pending', 'created') ORDER BY created_at DESC LIMIT 1",
            (from_number,),
            fetchone=True
        )
        if pending:
            db_query(
                "UPDATE orders SET status = 'cancelled' WHERE id = ?",
                (pending["id"],),
                commit=True
            )
            send_text(from_number, f"✅ অর্ডার #{pending['id']} বাতিল করা হয়েছে।")
        else:
            send_text(from_number, "বাতিল করার মতো কোনো সক্রিয় অর্ডার নেই।")
        return

    # মানব হস্তান্তর
    if any(k in user_text.lower() for k in ["এজেন্ট", "মানুষ", "agent", "human", "কল", "ফোন"]):
        set_session(from_number, "handoff_human", {})
        send_text(
            from_number,
            "🔄 আপনার অনুরোধ প্রতিনিধির কাছে পাঠানো হয়েছে। শীঘ্রই কল করা হবে।"
        )
        return

    # ফিডব্যাক
    if any(k in user_text.lower() for k in ["রেটিং", "ফিডব্যাক", "rating", "feedback"]):
        last_order = db_query(
            "SELECT * FROM orders WHERE phone = ? AND status = 'delivered' ORDER BY created_at DESC LIMIT 1",
            (from_number,),
            fetchone=True
        )
        if last_order:
            set_session(from_number, "awaiting_feedback", {"order_id": last_order["id"]})
            send_buttons(
                from_number,
                "আপনার সর্বশেষ অর্ডারের অভিজ্ঞতা?",
                [
                    {"id": "rate_5", "title": "⭐⭐⭐⭐⭐"},
                    {"id": "rate_4", "title": "⭐⭐⭐⭐"},
                    {"id": "rate_3", "title": "⭐⭐⭐"}
                ]
            )
        else:
            send_text(from_number, "ফিডব্যাক দেওয়ার জন্য কোনো ডেলিভারড অর্ডার নেই।")
        return

    if state == "awaiting_feedback":
        rating_map = {"rate_5": 5, "rate_4": 4, "rate_3": 3, "5": 5, "4": 4, "3": 3}
        rating = rating_map.get(user_text, 0)
        if rating > 0:
            ctx = get_context(from_number)
            db_query(
                "INSERT INTO feedback (phone, order_id, rating) VALUES (?, ?, ?)",
                (from_number, ctx.get("order_id"), rating),
                commit=True
            )
            send_text(from_number, "❤️ ধন্যবাদ! আপনার ফিডব্যাক গুরুত্বপূর্ণ।")
            set_session(from_number, "idle", {})
        return

    # AI Fallback
    ai_response = get_ai_answer(user_text, context)

    if "||TRACK_DATA||" in ai_response:
        parts = ai_response.split("||TRACK_DATA||")
        clean_reply = parts[0].strip()
        json_block = extract_json_block(parts[1])
        if json_block:
            try:
                track_info = json.loads(json_block)
                key = track_info.get("key", "").strip()
                if key:
                    live_status = track_pathao_order(key)
                    send_text(
                        from_number,
                        f"{clean_reply}\n\n📌 অবস্থা: {live_status}"
                    )
                    return
            except:
                pass
        send_text(from_number, clean_reply)
        return

    if "||ORDER_DATA||" in ai_response:
        parts = ai_response.split("||ORDER_DATA||")
        json_block = extract_json_block(parts[1])
        if json_block:
            try:
                order_info = json.loads(json_block)
                name = order_info.get("name", "").strip()
                phone = order_info.get("phone", "").strip()
                address = order_info.get("address", "").strip()
                if name and phone and address:
                    ctx = {"name": name, "phone": phone, "address": address}
                    set_session(from_number, "awaiting_confirmation", context=ctx)
                    send_buttons(
                        from_number,
                        f"📦 অর্ডার কনফার্ম?\n{name}\n{phone}\n{address}",
                        [
                            {"id": "confirm_yes", "title": "✅ হ্যাঁ"},
                            {"id": "confirm_no", "title": "❌ না"}
                        ]
                    )
                    return
            except:
                pass
        send_text(from_number, ai_response)
        return

    # Buy intent → start product flow
    if any(k in user_text.lower() for k in ["কিনব", "অর্ডার", "চাই", "buy", "order", "দাম"]):
        products = get_products()
        if products:
            sections = [{
                "title": "প্রোডাক্ট",
                "rows": [
                    {
                        "id": f"product_{p['id']}",
                        "title": p['name'][:24],
                        "description": f"{p['price']}৳"
                    }
                    for p in products[:10]
                ]
            }]
            set_session(from_number, "selecting_product", context={})
            send_list_menu(
                from_number,
                "কোন প্রোডাক্টটি দেখতে চান?",
                "প্রোডাক্ট",
                sections
            )
            return

    send_text(from_number, ai_response)


def send_payment_options(to, ctx):
    subtotal = ctx.get("subtotal", 0)
    send_buttons(
        to,
        f"💰 সাবটোটাল: {subtotal}৳\n\nপেমেন্ট মেথড:",
        [
            {"id": "pay_cod", "title": "💵 COD"},
            {"id": "pay_bkash", "title": "📱 bKash"},
            {"id": "pay_nagad", "title": "💳 Nagad"}
        ]
    )


# =====================================================================
# 🌐 ১৪. Flask Routes
# =====================================================================
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "running",
        "service": f"{BUSINESS_NAME} WhatsApp Bot",
        "version": "3.0",
        "business_hours": BUSINESS_HOURS,
        "timestamp": datetime.utcnow().isoformat()
    })

@app.route("/health", methods=["GET"])
def health():
    try:
        db_query("SELECT 1", fetchone=True)
        db_ok = True
    except:
        db_ok = False
    return jsonify({
        "status": "healthy" if db_ok else "unhealthy",
        "database": db_ok,
        "timestamp": datetime.utcnow().isoformat()
    })

@app.route("/dashboard", methods=["GET"])
def dashboard():
    return jsonify(get_dashboard_stats())

@app.route("/webhook", methods=["GET"])
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("Webhook verified")
        return challenge, 200
    logger.warning("Webhook verification failed")
    return "Verification failed", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    signature = request.headers.get("X-Hub-Signature-256", "")
    payload = request.get_data()
    if not verify_meta_signature(payload, signature):
        logger.warning("Invalid webhook signature")
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
                thread = Thread(target=process_webhook_async, args=(msg, from_number))
                thread.start()
    except Exception as e:
        logger.error("Webhook error: %s", e)

    return "ok", 200

# =====================================================================
# 🚀 START
# =====================================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)

