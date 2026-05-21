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
GEMINI_KEY = os.environ.get("GEMINI_KEY", "AIzaSyCRZIRWSoenfhA33qr7rkzoa56Byun0IWU")
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




=====
# =====================================================================
# 1.5 GEMINI
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

# =====================================================================
# 2. DATABASE
# =====================================================================
DB_FILE = "bot_v3.db"
db_lock = Lock()

def init_db():
    try:
        with db_lock:
            conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    msg_id TEXT PRIMARY KEY, from_number TEXT, content TEXT,
                    msg_type TEXT DEFAULT 'text', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    phone TEXT PRIMARY KEY, state TEXT DEFAULT 'idle',
                    context TEXT DEFAULT '{}', last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, name TEXT,
                    address TEXT, city_id INTEGER DEFAULT 1, zone_id INTEGER DEFAULT 1,
                    area_id INTEGER DEFAULT 1, product_id INTEGER, quantity INTEGER DEFAULT 1,
                    price INTEGER, delivery_charge INTEGER DEFAULT 80, discount INTEGER DEFAULT 0,
                    total INTEGER, payment_method TEXT DEFAULT 'cod',
                    payment_status TEXT DEFAULT 'pending', pathao_consignment_id TEXT,
                    status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, price INTEGER,
                    description TEXT, stock INTEGER DEFAULT 0, active INTEGER DEFAULT 1,
                    image_url TEXT DEFAULT ''
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS knowledge (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT DEFAULT 'general',
                    content TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS coupons (
                    code TEXT PRIMARY KEY, discount_percent INTEGER DEFAULT 0,
                    discount_amount INTEGER DEFAULT 0, max_uses INTEGER DEFAULT 100,
                    used_count INTEGER DEFAULT 0, valid_until TEXT, active INTEGER DEFAULT 1
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    phone TEXT PRIMARY KEY, name TEXT, language TEXT DEFAULT 'bn',
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    total_orders INTEGER DEFAULT 0, total_spent INTEGER DEFAULT 0
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS employees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE, password TEXT, name TEXT,
                    phone TEXT, role TEXT DEFAULT 'agent', active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS conversation_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone TEXT, employee_id INTEGER, date TEXT,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ended_at TIMESTAMP, message_count INTEGER DEFAULT 0,
                    orders_count INTEGER DEFAULT 0, status TEXT DEFAULT 'active'
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS cost_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT, conversation_type TEXT, phone TEXT,
                    cost_usd REAL, cost_bdt REAL, message_type TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            conn.close()
            logger.info("Database initialized: %s", DB_FILE)
            ensure_employee_tables()
    except Exception as e:
        logger.error("Database init failed: %s", e)
        raise

def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    try:
        with db_lock:
            conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(query, params)
            if commit:
                conn.commit(); conn.close(); return True
            if fetchone:
                row = c.fetchone(); conn.close(); return dict(row) if row else None
            if fetchall:
                rows = c.fetchall(); conn.close(); return [dict(r) for r in rows]
            conn.close(); return None
    except Exception as e:
        logger.error("DB query failed: %s | Query: %s | Params: %s", e, query, params)
        raise

init_db()

# =====================================================================
# 3. HELPERS
# =====================================================================
def format_phone(phone):
    phone = str(phone).replace(" ", "").replace("+", "").replace("-", "")
    if phone.startswith("01") and len(phone) == 11:
        return "880" + phone[1:]
    if phone.startswith("8801") and len(phone) == 13:
        return phone
    return phone

def log_message(msg_id, from_number, content, msg_type='text'):
    try:
        db_query(
            "INSERT INTO messages (msg_id, from_number, content, msg_type) VALUES (?, ?, ?, ?)",
            (msg_id, from_number, content, msg_type), commit=True)
    except Exception as e:
        logger.error("log_message error: %s", e)
        try:
            db_query(
                "INSERT INTO messages (msg_id, from_number, content) VALUES (?, ?, ?)",
                (msg_id, from_number, content), commit=True)
        except Exception as e2:
            logger.error("log_message fallback error: %s", e2)

def get_session(phone):
    row = db_query("SELECT * FROM sessions WHERE phone = ?", (phone,), fetchone=True)
    if row:
        try:
            return row["state"], json.loads(row.get("context") or "{}")
        except:
            return row["state"], {}
    return "idle", {}

def set_session(phone, state, context=None):
    ctx_json = json.dumps(context) if context else '{}'
    db_query("INSERT OR REPLACE INTO sessions (phone, state, context, last_active) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
             (phone, state, ctx_json), commit=True)

def get_context(phone):
    _, ctx = get_session(phone)
    return ctx

def update_context(phone, key, value):
    state, ctx = get_session(phone)
    ctx[key] = value
    set_session(phone, state, ctx)

def verify_meta_signature(payload, signature):
    if not APP_SECRET:
        return True
    if not signature:
        return False
    try:
        expected = "sha256=" + hmac.new(APP_SECRET.encode(), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception as e:
        logger.error("Signature verify error: %s", e)
        return False

# =====================================================================
# 4. PATHAO API
# =====================================================================
def get_pathao_token():
    if not PATHAO_CLIENT_ID or not PATHAO_CLIENT_SECRET:
        return None
    url = f"{PATHAO_BASE_URL}/aladdin/api/v1/issue-token"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    payload = {
        "client_id": PATHAO_CLIENT_ID,
        "client_secret": PATHAO_CLIENT_SECRET,
        "username": PATHAO_MERCHANT_EMAIL,
        "password": PATHAO_MERCHANT_PASSWORD,
        "grant_type": "password"
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        data = r.json()
        if r.status_code in (200, 201) and data.get("status") == 200:
            return data.get("data", {}).get("token")
        logger.error("Pathao auth failed: %s", data)
        return None
    except Exception as e:
        logger.error("Pathao auth error: %s", e)
        return None

def get_pathao_cities():
    token = get_pathao_token()
    if not token:
        return []
    url = f"{PATHAO_BASE_URL}/aladdin/api/v1/cities"
    headers = {"authorization": f"Bearer {token}", "accept": "application/json"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        data = r.json()
        if data.get("status") == 200:
            return data.get("data", {}).get("data", [])
        return []
    except Exception as e:
        logger.error("Pathao cities error: %s", e)
        return []

def create_pathao_order(name, phone, address, city_id=1, zone_id=1, area_id=1, item_desc="", cod_amount=0):
    token = get_pathao_token()
    if not token:
        return False, "Pathao auth failed"
    url = f"{PATHAO_BASE_URL}/aladdin/api/v1/orders"
    headers = {"authorization": f"Bearer {token}", "content-type": "application/json", "accept": "application/json"}
    payload = {
        "store_id": PATHAO_STORE_ID,
        "recipient_name": name,
        "recipient_phone": format_phone(phone),
        "recipient_address": address,
        "recipient_city": city_id,
        "recipient_zone": zone_id,
        "recipient_area": area_id,
        "delivery_type": 48,
        "item_type": 2,
        "item_description": item_desc,
        "amount_to_collect": cod_amount
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        data = r.json()
        if r.status_code in (200, 201) and data.get("status") == 200:
            consignment = data.get("data", {}).get("consignment_id") or data.get("data", {}).get("order_id")
            return True, consignment
        logger.error("Pathao order error: %s", data)
        return False, str(data.get("message", "Pathao API error"))
    except Exception as e:
        logger.error("Pathao order exception: %s", e)
        return False, str(e)

def track_pathao_order(tracking_key):
    token = get_pathao_token()
    if not token:
        return "Pathao auth failed"
    url = f"{PATHAO_BASE_URL}/aladdin/api/v1/orders/{tracking_key}/tracking"
    headers = {"authorization": f"Bearer {token}", "accept": "application/json"}
    try:
        res = requests.get(url, headers=headers, timeout=15)
        data = res.json()
        if res.status_code == 200 and data.get("status") == 200:
            status = data.get("data", {}).get("order_status", "unknown").lower()
            status_map = {
                "pending": "পেন্ডিং", "picked": "কুরিয়ারে হস্তান্তরিত",
                "in_transit": "ডেলিভারির পথে", "delivered": "ডেলিভারি সম্পন্ন 🎉",
                "cancelled": "বাতিল", "returned": "রিটার্ন"
            }
            return status_map.get(status, f"Status: {status.upper()}")
        return "অর্ডার পাওয়া যায়নি।"
    except:
        return "ট্র্যাকিং ত্রুটি।"

# =====================================================================
# 6. WHATSAPP SEND
# =====================================================================
def send_text(to, body):
    if not PERMANENT_TOKEN or not PHONE_NUMBER_ID:
        logger.error("WhatsApp credentials missing")
        return False
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {PERMANENT_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp", "to": format_phone(to),
        "type": "text", "text": {"body": body}
    }
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=15)
        ok = res.status_code in (200, 201)
        if ok:
            log_outgoing_cost(to, "text")
            increment_session_messages(to)
        return ok
    except Exception as e:
        logger.error("Send text error: %s", e)
        return False

def send_buttons(to, body, buttons):
    if not PERMANENT_TOKEN or not PHONE_NUMBER_ID:
        return False
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {PERMANENT_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp", "to": format_phone(to), "type": "interactive",
        "interactive": {
            "type": "button", "body": {"text": body},
            "action": {"buttons": [{"type": "reply", "reply": {"id": b["id"], "title": b["title"]}} for b in buttons[:3]]}
        }
    }
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=15)
        ok = res.status_code in (200, 201)
        if ok:
            log_outgoing_cost(to, "interactive")
            increment_session_messages(to)
        return ok
    except Exception as e:
        logger.error("Send buttons error: %s", e)
        return False

def send_list_menu(to, body, button_text, sections):
    if not PERMANENT_TOKEN or not PHONE_NUMBER_ID:
        return False
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {PERMANENT_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp", "to": format_phone(to), "type": "interactive",
        "interactive": {"type": "list", "body": {"text": body},
            "action": {"button": button_text, "sections": sections}}
    }
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=15)
        ok = res.status_code in (200, 201)
        if ok:
            log_outgoing_cost(to, "list")
            increment_session_messages(to)
        return ok
    except Exception as e:
        logger.error("Send list error: %s", e)
        return False

def send_image(to, image_url, caption=""):
    if not PERMANENT_TOKEN or not PHONE_NUMBER_ID:
        return False
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
        ok = res.status_code in (200, 201)
        if ok:
            log_outgoing_cost(to, "image")
            increment_session_messages(to)
        return ok
    except Exception as e:
        logger.error("Send image error: %s", e)
        return False

# =====================================================================
# 6.5 EMPLOYEE & COST TRACKING
# =====================================================================
USD_TO_BDT = 120.0
BD_USER_INIT_COST = 0.004
BD_BUSINESS_INIT_COST = 0.006

def ensure_employee_tables():
    try:
        with db_lock:
            conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            c = conn.cursor()
            for stmt in [
                "ALTER TABLE orders ADD COLUMN employee_id INTEGER DEFAULT 0",
                "ALTER TABLE orders ADD COLUMN handled_by TEXT DEFAULT 'bot'",
            ]:
                try: c.execute(stmt)
                except: pass
            conn.commit()
            conn.close()
    except Exception as e:
        logger.error("ensure_employee_tables: %s", e)

def hash_pwd(text):
    return hashlib.sha256(text.encode()).hexdigest()

def log_outgoing_cost(phone, msg_type="text"):
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        existing = db_query(
            "SELECT id FROM cost_logs WHERE date = ? AND phone = ? LIMIT 1",
            (today, phone), fetchone=True)
        if existing:
            return
        cost_usd = BD_USER_INIT_COST
        cost_bdt = cost_usd * USD_TO_BDT
        db_query(
            "INSERT INTO cost_logs (date, conversation_type, phone, cost_usd, cost_bdt, message_type) VALUES (?, ?, ?, ?, ?, ?)",
            (today, "user_initiated", phone, cost_usd, cost_bdt, msg_type), commit=True)
    except Exception as e:
        logger.error("log_outgoing_cost: %s", e)

def assign_employee_to_conversation(phone):
    try:
        emp = db_query(
            "SELECT id FROM employees WHERE active = 1 ORDER BY id LIMIT 1", fetchone=True)
        if not emp:
            return None
        today = datetime.utcnow().strftime("%Y-%m-%d")
        existing = db_query(
            "SELECT id FROM conversation_sessions WHERE phone = ? AND date = ? AND status = 'active' LIMIT 1",
            (phone, today), fetchone=True)
        if existing:
            db_query("UPDATE conversation_sessions SET employee_id = ? WHERE id = ?",
                     (emp["id"], existing["id"]), commit=True)
        else:
            db_query(
                "INSERT INTO conversation_sessions (phone, employee_id, date, status) VALUES (?, ?, ?, 'active')",
                (phone, emp["id"], today), commit=True)
        return emp["id"]
    except Exception as e:
        logger.error("assign_employee: %s", e)
        return None

def increment_session_messages(phone):
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        db_query(
            "UPDATE conversation_sessions SET message_count = message_count + 1 WHERE phone = ? AND date = ?",
            (phone, today), commit=True)
    except Exception as e:
        logger.error("increment_session_messages: %s", e)

def log_employee_order(phone, order_id):
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        sess = db_query(
            "SELECT employee_id FROM conversation_sessions WHERE phone = ? AND date = ? ORDER BY id DESC LIMIT 1",
            (phone, today), fetchone=True)
        emp_id = sess["employee_id"] if sess else 0
        db_query(
            "UPDATE orders SET employee_id = ?, handled_by = ? WHERE id = ?",
            (emp_id, "employee" if emp_id else "bot", order_id), commit=True)
        if emp_id:
            db_query(
                "UPDATE conversation_sessions SET orders_count = orders_count + 1 WHERE phone = ? AND date = ?",
                (phone, today), commit=True)
    except Exception as e:
        logger.error("log_employee_order: %s", e)

def get_daily_cost_summary(days=30):
    try:
        rows = db_query(
            "SELECT date, SUM(cost_usd) as usd, SUM(cost_bdt) as bdt, COUNT(*) as conversations FROM cost_logs WHERE date >= date('now', '-{} days') GROUP BY date ORDER BY date DESC".format(days),
            fetchall=True)
        return rows or []
    except Exception as e:
        logger.error("get_daily_cost_summary: %s", e)
        return []

def get_monthly_cost_summary():
    try:
        rows = db_query(
            "SELECT strftime('%Y-%m', date) as month, SUM(cost_usd) as usd, SUM(cost_bdt) as bdt, COUNT(*) as conversations FROM cost_logs GROUP BY month ORDER BY month DESC",
            fetchall=True)
        return rows or []
    except Exception as e:
        logger.error("get_monthly_cost_summary: %s", e)
        return []

def get_employee_stats(date_filter=None):
    if not date_filter:
        date_filter = datetime.utcnow().strftime("%Y-%m-%d")
    try:
        rows = db_query(
            """SELECT e.id, e.name, e.role,
                COALESCE(SUM(c.message_count), 0) as messages,
                COALESCE(SUM(c.orders_count), 0) as orders,
                COUNT(c.id) as chats
            FROM employees e
            LEFT JOIN conversation_sessions c ON c.employee_id = e.id AND c.date = ?
            WHERE e.active = 1
            GROUP BY e.id ORDER BY e.id""", (date_filter,), fetchall=True)
        return rows or []
    except Exception as e:
        logger.error("get_employee_stats: %s", e)
        return []

# =====================================================================
# 7. GEMINI AI (FIXED PROMPT)
# =====================================================================
SYSTEM_PROMPT = """তুমি Dhaka Exclusive-এর AI কাস্টমার সার্ভিস এজেন্ট। বাংলায় কথা বলো।
তুমি কাস্টমারদের সাহায্য করো:
- প্রোডাক্ট দেখানো ও দাম বলা
- অর্ডার নেওয়া
- পাঠাও কুরিয়ার ট্র্যাকিং
- শিপিং চার্জ (ঢাকায় ৮০৳, বাইরে ১২০৳)

তুমি অর্ডার নিতে পারো। যদি কাস্টমার অর্ডার দিতে চায়, নাম, ফোন, ঠিকানা, সিটি, প্রোডাক্ট, পরিমাণ নাও।
তারপর "||ORDER_DATA||" দিয়ে JSON ফরম্যাটে অর্ডার ডেটা রিটার্ন করো।

যদি কাস্টমার ট্র্যাকিং জানতে চায়, "||TRACK_DATA||" দিয়ে {"key": "consignment_id"} রিটার্ন করো।

তুমি ফ্রেন্ডলি, হেল্পফুল এবং পেশাদারি। সব সময় বাংলায় রিপ্লাই দাও।"""

def extract_json_block(text):
    text = str(text) if text else ""
    try:
        match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
        return match.group(0) if match else None
    except:
        return None

def get_ai_answer(user_text, context=None):
    if not genai_available or not client:
        return "দুঃখিত, AI সার্ভিস বর্তমানে অনুপলব্ধ। আমাদের প্রতিনিধি শীঘ্রই আপনার সাথে যোগাযোগ করবেন।"
    try:
        content = SYSTEM_PROMPT + "\n\nUser: " + user_text
        response = client.models.generate_content(model=MODEL_NAME, contents=content)
        return response.text.strip() if response and response.text else "দুঃখিত, বুঝতে পারিনি। অন্য ভাবে জিজ্ঞেস করুন।"
    except Exception as e:
        logger.error("Gemini error: %s", e)
        return "দুঃখিত, AI ত্রুটি। অন্য ভাবে জিজ্ঞেস করুন।"

# =====================================================================
# 8. PRODUCTS
# =====================================================================
def get_products():
    try:
        return db_query("SELECT * FROM products WHERE active = 1", fetchall=True)
    except:
        return []

def get_product_by_id(pid):
    try:
        return db_query("SELECT * FROM products WHERE id = ?", (pid,), fetchone=True)
    except:
        return None

def add_product(name, price, description, stock=10):
    try:
        db_query("INSERT INTO products (name, price, description, stock) VALUES (?, ?, ?, ?)",
                 (name, price, description, stock), commit=True)
    except:
        pass

def update_stock(product_id, qty_sold):
    try:
        db_query("UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
                 (qty_sold, product_id, qty_sold), commit=True)
    except:
        pass

def format_catalog():
    products = get_products()
    if not products:
        return "কোনো প্রোডাক্ট আপডেট হয়নি।"
    lines = ["📋 আমাদের প্রোডাক্ট:"]
    for p in products:
        lines.append(f"\n🔹 {p['name']} — {p['price']}৳\n📝 {p['description']}\n📦 স্টক: {p['stock']}টি")
    return "\n".join(lines)

# =====================================================================
# 9. COUPONS
# =====================================================================
def validate_coupon(code):
    try:
        row = db_query("SELECT * FROM coupons WHERE code = ? AND active = 1", (code.upper(),), fetchone=True)
        if not row:
            return None, None
        if row["used_count"] >= row["max_uses"]:
            return None, None
        return row["discount_percent"], row["discount_amount"]
    except:
        return None, None

def use_coupon(code):
    try:
        db_query("UPDATE coupons SET used_count = used_count + 1 WHERE code = ?", (code.upper(),), commit=True)
    except:
        pass

# =====================================================================
# 10. USERS
# =====================================================================
def ensure_user(phone, name=None):
    try:
        exists = db_query("SELECT 1 FROM users WHERE phone = ?", (phone,), fetchone=True)
        if not exists:
            db_query("INSERT INTO users (phone, name) VALUES (?, ?)", (phone, name), commit=True)
        else:
            db_query("UPDATE users SET last_active = CURRENT_TIMESTAMP, name = COALESCE(?, name) WHERE phone = ?",
                     (name, phone), commit=True)
    except Exception as e:
        logger.error("ensure_user: %s", e)

# =====================================================================
# 11. DASHBOARD STATS
# =====================================================================
def get_dashboard_stats():
    try:
        total_orders = db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)
        revenue = db_query("SELECT COALESCE(SUM(total), 0) as s FROM orders WHERE status != 'cancelled'", fetchone=True)
        users = db_query("SELECT COUNT(*) as c FROM users", fetchone=True)
        pending = db_query("SELECT COUNT(*) as c FROM orders WHERE status IN ('pending', 'created')", fetchone=True)
        today_orders = db_query("SELECT COUNT(*) as c FROM orders WHERE date(created_at) = date('now')", fetchone=True)
        today_revenue = db_query("SELECT COALESCE(SUM(total), 0) as s FROM orders WHERE date(created_at) = date('now')", fetchone=True)
        return {
            "total_orders": total_orders["c"] if total_orders else 0,
            "revenue": revenue["s"] if revenue else 0,
            "users": users["c"] if users else 0,
            "pending": pending["c"] if pending else 0,
            "today_orders": today_orders["c"] if today_orders else 0,
            "today_revenue": today_revenue["s"] if today_revenue else 0,
        }
    except Exception as e:
        logger.error("Dashboard stats error: %s", e)
        return {"total_orders": 0, "revenue": 0, "users": 0, "pending": 0, "today_orders": 0, "today_revenue": 0}

# =====================================================================
# 12. WEBHOOK PROCESSOR
# =====================================================================
def process_webhook_async(msg, from_number):
    try:
        msg_id = msg.get("id")
        msg_type = msg.get("type", "text")
        user_text = ""
        image_url = None

        if msg_type == "text":
            user_text = msg.get("text", {}).get("body", "").strip()
        elif msg_type == "interactive":
            user_text = msg.get("interactive", {}).get("button_reply", {}).get("id", "") or msg.get("interactive", {}).get("list_reply", {}).get("id", "")
        elif msg_type == "image":
            image_url = msg.get("image", {}).get("link", "")
            user_text = "[IMAGE_RECEIVED]"

        ensure_user(from_number)

        if msg_id and user_text:
            log_message(msg_id, from_number, user_text, msg_type)

        state, context = get_session(from_number)

        # Admin commands
        if from_number in ADMIN_NUMBERS and user_text.startswith("admin:"):
            cmd = user_text.replace("admin:", "").strip().lower()
            if cmd == "stats":
                stats = get_dashboard_stats()
                send_text(from_number, f"📊 Stats:\nঅর্ডার: {stats['total_orders']}\nরেভেনিউ: ৳{stats['revenue']}\nইউজার: {stats['users']}\nপেন্ডিং: {stats['pending']}")
                return
            elif cmd == "products":
                send_text(from_number, format_catalog())
                return
            elif cmd.startswith("addproduct"):
                parts = cmd.replace("addproduct", "").strip().split(",")
                if len(parts) >= 2:
                    name = parts[0].strip()
                    price = int(parts[1].strip())
                    desc = parts[2].strip() if len(parts) > 2 else ""
                    stock = int(parts[3].strip()) if len(parts) > 3 else 10
                    add_product(name, price, desc, stock)
                    send_text(from_number, f"✅ প্রোডাক্ট যোগ: {name} — {price}৳")
                else:
                    send_text(from_number, "Format: admin:addproduct নাম, দাম, বর্ণনা, স্টক")
                return
            elif cmd.startswith("broadcast"):
                text = cmd.replace("broadcast", "").strip()
                if text:
                    users = db_query("SELECT phone FROM users", fetchall=True) or []
                    for u in users:
                        try:
                            send_text(u["phone"], text)
                        except:
                            pass
                    send_text(from_number, f"📢 {len(users)} জনকে পাঠানো হয়েছে")
                return

        # Product selection flow
        if state == "idle" and any(k in user_text.lower() for k in ["কিনব", "অর্ডার", "চাই", "buy", "order"]):
            products = get_products()
            if products:
                sections = [{
                    "title": "আমাদের প্রোডাক্ট",
                    "rows": [{"id": f"product_{p['id']}", "title": p['name'][:24], "description": f"{p['price']}৳ | স্টক: {p['stock']}"}
                             for p in products[:10]]
                }]
                set_session(from_number, "selecting_product", context={})
                send_list_menu(from_number, "কোন প্রোডাক্ট কিনতে চান? লিস্ট থেকে বাছাই করুন:", "প্রোডাক্ট", sections)
            else:
                send_text(from_number, "কোনো প্রোডাক্ট আপডেট হয়নি।")
            return

        if state == "selecting_product":
            if user_text.startswith("product_"):
                pid = int(user_text.replace("product_", ""))
                product = get_product_by_id(pid)
                if product:
                    ctx = {"product_id": pid, "product_name": product["name"], "price": product["price"]}
                    set_session(from_number, "selecting_qty", context=ctx)
                    # Send product image if available
                    if product.get("image_url"):
                        send_image(from_number, product["image_url"], f"🔹 {product['name']}\n💰 {product['price']}৳")
                    send_buttons(from_number,
                        f"🔹 {product['name']}\n💰 {product['price']}৳\n📝 {product['description']}\n\nকতটি চান?",
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
            ctx["city_name"] = "ঢাকা"
            set_session(from_number, "selecting_payment", context=ctx)
            send_payment_options(from_number, ctx)
            return

        if state == "selecting_city":
            if user_text.startswith("city_"):
                city_id = int(user_text.replace("city_", ""))
                ctx = get_context(from_number)
                ctx["city_id"] = city_id
                set_session(from_number, "selecting_payment", context=ctx)
                send_payment_options(from_number, ctx)
                return
            send_text(from_number, "অনুগ্রহ করে শহর বাছাই করুন।")
            return

        if state == "selecting_payment":
            payment_map = {"pay_cod": "cod", "pay_bkash": "bkash", "pay_nagad": "nagad"}
            ctx = get_context(from_number)
            if user_text in payment_map:
                ctx["payment_method"] = payment_map[user_text]
            else:
                ctx["payment_method"] = "cod"
            delivery = 80 if str(ctx.get("city_id", 1)) == "1" else 120
            ctx["delivery_charge"] = delivery
            subtotal = ctx.get("subtotal", 0)
            discount = 0
            if ctx.get("coupon"):
                pct, amt = validate_coupon(ctx["coupon"])
                if pct:
                    discount = int(subtotal * pct / 100)
                elif amt:
                    discount = amt
            total = subtotal + delivery - discount
            ctx["discount"] = discount
            ctx["total"] = total
            set_session(from_number, "awaiting_confirmation", context=ctx)
            summary = (
                f"📦 অর্ডার সামারি:\n"
                f"🔹 {ctx['product_name']} x {ctx['quantity']}\n"
                f"💰 সাবটোটাল: {subtotal}৳\n"
                f"🚚 ডেলিভারি: {delivery}৳\n"
                f"🎁 ডিসকাউন্ট: -{discount}৳\n"
                f"💵 মোট: {total}৳\n"
                f"💳 পেমেন্ট: {ctx['payment_method'].upper()}\n\n"
                f"অর্ডার কনফার্ম করতে 'হ্যাঁ' লিখুন।"
            )
            send_buttons(from_number, summary,
                [{"id": "confirm_yes", "title": "✅ হ্যাঁ"}, {"id": "confirm_no", "title": "❌ না"}])
            return

        if state == "awaiting_confirmation":
            if user_text in ["হ্যাঁ", "yes", "confirm_yes", "✅ হ্যাঁ"]:
                ctx = get_context(from_number)
                cod_amount = ctx["total"] if ctx["payment_method"] == "cod" else 0
                success, result = create_pathao_order(
                    name=ctx.get("name"), phone=ctx.get("phone"), address=ctx.get("address"),
                    city_id=ctx.get("city_id", 1), zone_id=ctx.get("zone_id", 1), area_id=ctx.get("area_id", 1),
                    item_desc=f"{ctx['product_name']} x{ctx['quantity']}", cod_amount=cod_amount
                )
                if success:
                    db_query(
                        """INSERT INTO orders (phone, name, address, city_id, zone_id, area_id, product_id, quantity,
                            price, delivery_charge, discount, total, payment_method, pathao_consignment_id, status)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (ctx.get("phone"), ctx.get("name"), ctx.get("address"), ctx.get("city_id", 1),
                         ctx.get("zone_id", 1), ctx.get("area_id", 1), ctx.get("product_id"), ctx.get("quantity"),
                         ctx.get("subtotal"), ctx.get("delivery_charge", 80), ctx.get("discount", 0),
                         ctx.get("total"), ctx.get("payment_method", "cod"), str(result), "created"),
                        commit=True)
                    if ctx.get("coupon"):
                        use_coupon(ctx["coupon"])
                    update_stock(ctx.get("product_id"), ctx.get("quantity", 1))
                    db_query(
                        "UPDATE users SET total_orders = total_orders + 1, total_spent = total_spent + ? WHERE phone = ?",
                        (ctx.get("total", 0), from_number), commit=True)
                    last_order = db_query("SELECT id FROM orders WHERE phone = ? ORDER BY id DESC LIMIT 1", (ctx.get("phone"),), fetchone=True)
                    if last_order:
                        log_employee_order(from_number, last_order["id"])
                    send_text(from_number,
                        f"🎉 অর্ডার সফল!\n📦 Tracking: {result}\n🚚 পাঠাও কুরিয়ার আসবে।\nধন্যবাদ প্রিয় গ্রাহক! 🙏")
                else:
                    db_query(
                        """INSERT INTO orders (phone, name, address, city_id, zone_id, area_id, product_id, quantity,
                            price, delivery_charge, discount, total, payment_method, status)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (ctx.get("phone"), ctx.get("name"), ctx.get("address"), ctx.get("city_id", 1),
                         ctx.get("zone_id", 1), ctx.get("area_id", 1), ctx.get("product_id"), ctx.get("quantity"),
                         ctx.get("subtotal"), ctx.get("delivery_charge", 80), ctx.get("discount", 0),
                         ctx.get("total"), ctx.get("payment_method", "cod"), "manual_pending"),
                        commit=True)
                    last_order = db_query("SELECT id FROM orders WHERE phone = ? ORDER BY id DESC LIMIT 1", (ctx.get("phone"),), fetchone=True)
                    if last_order:
                        log_employee_order(from_number, last_order["id"])
                    send_text(from_number,
                        f"⚠️ কুরিয়ার API ত্রুটি: {result}\nঅর্ডার ম্যানুয়ালি নোট। প্রতিনিধি কল করে কনফার্ম করবেন।")
                set_session(from_number, "idle", {})
                return
            else:
                send_text(from_number, "অর্ডার বাতিল। আপনাকে কীভাবে সাহায্য করতে পারি?")
                set_session(from_number, "idle", {})
                return

        # Idle: History / Cancel / Handoff / Feedback
        if any(k in user_text.lower() for k in ["আগের", "হিস্ট্রি", "history", "আগের অর্ডার", "পুরনো"]):
            orders = db_query("SELECT * FROM orders WHERE phone = ? ORDER BY created_at DESC LIMIT 5", (from_number,), fetchall=True)
            if orders:
                lines = ["📦 আপনার অর্ডার:"]
                for o in orders:
                    lines.append(f"\n🆔 #{o['id']} | {o['total']}৳ | {o['status']} | 📦 {o['pathao_consignment_id'] or 'N/A'}")
                send_text(from_number, "\n".join(lines))
            else:
                send_text(from_number, "আপনার কোনো পূর্ববর্তী অর্ডার নেই।")
            return

        if any(k in user_text.lower() for k in ["cancel", "বাতিল", "stop"]):
            pending = db_query(
                "SELECT * FROM orders WHERE phone = ? AND status IN ('pending', 'created') ORDER BY created_at DESC LIMIT 1",
                (from_number,), fetchone=True)
            if pending:
                db_query("UPDATE orders SET status = 'cancelled' WHERE id = ?", (pending["id"],), commit=True)
                send_text(from_number, f"✅ অর্ডার #{pending['id']} বাতিল করা হয়েছে।")
            else:
                send_text(from_number, "বাতিল করার মতো কোনো সক্রিয় অর্ডার নেই।")
            return

        if any(k in user_text.lower() for k in ["এজেন্ট", "মানুষ", "agent", "human", "কল", "ফোন"]):
            emp_id = assign_employee_to_conversation(from_number)
            set_session(from_number, "handoff_human", {})
            send_text(from_number, "🔄 আপনার অনুরোধ প্রতিনিধির কাছে পাঠানো হয়েছে। শীঘ্রই কল করা হবে।")
            return

        if any(k in user_text.lower() for k in ["রেটিং", "ফিডব্যাক", "rating", "feedback"]):
            last_order = db_query(
                "SELECT * FROM orders WHERE phone = ? AND status = 'delivered' ORDER BY created_at DESC LIMIT 1",
                (from_number,), fetchone=True)
            if last_order:
                set_session(from_number, "awaiting_feedback", {"order_id": last_order["id"]})
                send_buttons(from_number, "আপনার সর্বশেষ অর্ডারের অভিজ্ঞতা?",
                    [{"id": "rate_5", "title": "⭐⭐⭐⭐⭐"}, {"id": "rate_4", "title": "⭐⭐⭐⭐"}, {"id": "rate_3", "title": "⭐⭐⭐"}])
            else:
                send_text(from_number, "ফিডব্যাক দেওয়ার জন্য কোনো ডেলিভারড অর্ডার নেই।")
            return

        if state == "awaiting_feedback":
            rating_map = {"rate_5": 5, "rate_4": 4, "rate_3": 3, "5": 5, "4": 4, "3": 3}
            rating = rating_map.get(user_text, 0)
            if rating > 0:
                ctx = get_context(from_number)
                db_query("INSERT INTO feedback (phone, order_id, rating) VALUES (?, ?, ?)",
                         (from_number, ctx.get("order_id"), rating), commit=True)
                send_text(from_number, "❤️ ধন্যবাদ! আপনার ফিডব্যাক গুরুত্বপূর্ণ।")
                set_session(from_number, "idle", {})
            return

        # AI FALLBACK
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
                        send_text(from_number, f"{clean_reply}\n\n📌 অবস্থা: {live_status}")
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
                        send_buttons(from_number, f"📦 অর্ডার কনফার্ম?\n{name}\n{phone}\n{address}",
                            [{"id": "confirm_yes", "title": "✅ হ্যাঁ"}, {"id": "confirm_no", "title": "❌ না"}])
                        return
                except:
                    pass
            send_text(from_number, ai_response)
            return

        # Buy intent fallback
        if any(k in user_text.lower() for k in ["কিনব", "অর্ডার", "চাই", "buy", "order", "দাম"]):
            products = get_products()
            if products:
                sections = [{"title": "প্রোডাক্ট", "rows": [{"id": f"product_{p['id']}", "title": p['name'][:24], "description": f"{p['price']}৳"}
                             for p in products[:10]]}]
                set_session(from_number, "selecting_product", context={})
                send_list_menu(from_number, "কোন প্রোডাক্টটি দেখতে চান?", "প্রোডাক্ট", sections)
                return

        send_text(from_number, ai_response)

    except Exception as e:
        logger.error("Webhook async error: %s", e)
        try:
            send_text(from_number, "দুঃখিত, একটি ত্রুটি হয়েছে। অনুগ্রহ করে আবার চেষ্টা করুন।")
        except:
            pass

def send_payment_options(to, ctx):
    subtotal = ctx.get("subtotal", 0)
    send_buttons(to, f"💰 সাবটোটাল: {subtotal}৳\n\nপেমেন্ট মেথড:", [
        {"id": "pay_cod", "title": "💵 COD"},
        {"id": "pay_bkash", "title": "📱 bKash"},
        {"id": "pay_nagad", "title": "💳 Nagad"}
    ])

# =====================================================================
# 13. FLASK ROUTES
# =====================================================================
@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "running",
        "service": f"{BUSINESS_NAME} WhatsApp Bot",
        "version": "3.2",
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
# GUNICORN COMPATIBILITY
# =====================================================================
application = app

# =====================================================================
# 14. CHATWOOT DASHBOARD
# =====================================================================
try:
    from chatwoot_dashboard import init_chatwoot_routes
    init_chatwoot_routes(app)
except Exception as e:
    logger.warning("Chatwoot dashboard init failed: %s", e)

# =====================================================================
# 15. DYNAMIC ADMIN PANEL
# =====================================================================
try:
    from admin_dynamic import init_admin_routes
    init_admin_routes(app)
except Exception as e:
    logger.warning("Admin panel init failed: %s", e)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)

