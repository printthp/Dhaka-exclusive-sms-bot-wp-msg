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

genai_available = False
client = None
MODEL_NAME = "gemini-2.5-flash"

try:
    from google import genai
    from google.genai import types
    if GEMINI_KEY:
        client = genai.Client(api_key=GEMINI_KEY)
        genai_available = True
        logger.info("Gemini loaded successfully.")
    else:
        logger.warning("GEMINI_KEY missing from environment variables.")
except Exception as e:
    logger.error("Gemini import failed: %s", e)

# =====================================================================
# 2. DATABASE SYSTEM
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
    except Exception as e:
        logger.error("Database init failed: %s", e)
        raise

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
        logger.error("ensure_employee_tables migration failed: %s", e)

# ডাটাবেজ রান করা
try:
    init_db()
    ensure_employee_tables()
except Exception as e:
    logger.critical("Cannot start without database setup: %s", e)
    sys.exit(1)

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
        except Exception as e:
            logger.error("DB Error: %s | Query: %s", e, query)
            conn.close()
            raise

# =====================================================================
# 3. HELPER FUNCTIONS
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

def hash_pwd(text):
    return hashlib.sha256(text.encode()).hexdigest()

# =====================================================================
# 4. WEBHOOK SIGNATURE VERIFICATION
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
# 5. PATHAO COURIER INTEGRATION API
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
        "accept": "application/json", "content-type": "application/json",
        "X-API-KEY": PATHAO_CLIENT_ID, "X-SECRET-KEY": PATHAO_CLIENT_SECRET
    }
    payload = {
        "client_id": PATHAO_CLIENT_ID, "client_secret": PATHAO_CLIENT_SECRET,
        "username": PATHAO_MERCHANT_EMAIL, "password": PATHAO_MERCHANT_PASSWORD
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
    if not token: return []
    try:
        res = requests.get(
            f"{PATHAO_BASE_URL}/aladdin/api/v1/countries/1/city-list",
            headers={"authorization": f"Bearer {token}", "accept": "application/json"},
            timeout=10
        )
        return res.json().get("data", {}).get("data", [])
    except: return []

def get_pathao_zones(city_id):
    token, _ = get_pathao_token()
    if not token: return []
    try:
        res = requests.get(
            f"{PATHAO_BASE_URL}/aladdin/api/v1/cities/{city_id}/zone-list",
            headers={"authorization": f"Bearer {token}", "accept": "application/json"},
            timeout=10
        )
        return res.json().get("data", {}).get("data", [])
    except: return []

def get_pathao_areas(zone_id):
    token, _ = get_pathao_token()
    if not token: return []
    try:
        res = requests.get(
            f"{PATHAO_BASE_URL}/aladdin/api/v1/zones/{zone_id}/area-list",
            headers={"authorization": f"Bearer {token}", "accept": "application/json"},
            timeout=10
        )
        return res.json().get("data", {}).get("data", [])
    except: return []

def create_pathao_order(name, phone, address, city_id=1, zone_id=1, area_id=1, item_desc="Premium Kitchenware", cod_amount=0):
    token, err = get_pathao_token()
    if not token:
        return False, err
    url = f"{PATHAO_BASE_URL}/aladdin/api/v1/orders"
    headers = {
        "authorization": f"Bearer {token}", "accept": "application/json",
        "content-type": "application/json"
    }
    phone = format_phone(phone)
    payload = {
        "store_id": int(PATHAO_STORE_ID) if PATHAO_STORE_ID else 0,
        "recipient_name": str(name), "recipient_phone": phone,
        "recipient_address": str(address), "recipient_city": int(city_id),
        "recipient_zone": int(zone_id), "recipient_area": int(area_id),
        "delivery_type": 48, "item_type": 2,
        "special_instruction": "WhatsApp Bot Order", "item_quantity": 1,
        "amount_to_collect": int(cod_amount), "item_description": str(item_desc)
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
                "pending": "পেন্ডিং", "picked": "কুরিয়ারে হস্তান্তরিত",
                "in_transit": "ডেলিভারির পথে", "delivered": "ডেলিভারি সম্পন্ন 🎉",
                "cancelled": "বাতিল", "returned": "রিটার্ন"
            }
            return status_map.get(status, f"Status: {status.upper()}")
        return "অর্ডার পাওয়া যায়নি।"
    except:
        return "ট্র্যাকিং ত্রুটি।"

# =====================================================================
# 6. WHATSAPP SEND GRAPH API FUNCTIONS
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
    if not PERMANENT_TOKEN or not PHONE_NUMBER_ID: return False
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
    if not PERMANENT_TOKEN or not PHONE_NUMBER_ID: return False
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
    if not PERMANENT_TOKEN or not PHONE_NUMBER_ID: return False
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {PERMANENT_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp", "to": format_phone(to),
        "type": "image", "image": {"link": image_url, "caption": caption}
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
# 6.5 EMPLOYEE & API COST TRACKING
# =====================================================================
USD_TO_BDT = 120.0
BD_USER_INIT_COST = 0.004   
BD_BUSINESS_INIT_COST = 0.006  

def log_outgoing_cost(phone, msg_type="text"):
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        existing = db_query(
            "SELECT id FROM cost_logs WHERE date = ? AND phone = ? LIMIT 1",
            (today, phone), fetchone=True)
        if existing: return  
        cost_usd = BD_USER_INIT_COST
        cost_bdt = cost_usd * USD_TO_BDT
        db_query(
            "INSERT INTO cost_logs (date, conversation_type, phone, cost_usd, cost_bdt, message_type) VALUES (?, ?, ?, ?, ?, ?)",
            (today, "user_initiated", phone, cost_usd, cost_bdt, msg_type), commit=True)
    except Exception as e:
        logger.error("log_outgoing_cost error: %s", e)

def assign_employee_to_conversation(phone):
    try:
        emp = db_query("SELECT id FROM employees WHERE active = 1 ORDER BY id LIMIT 1", fetchone=True)
        if not emp: return None
        today = datetime.utcnow().strftime("%Y-%m-%d")
        existing = db_query(
            "SELECT id FROM conversation_sessions WHERE phone = ? AND date = ? AND status = 'active' LIMIT 1",
            (phone, today), fetchone=True)
        if existing:
            db_query("UPDATE conversation_sessions SET employee_id = ? WHERE id = ?", (emp["id"], existing["id"]), commit=True)
        else:
            db_query(
                "INSERT INTO conversation_sessions (phone, employee_id, date, status) VALUES (?, ?, ?, 'active')",
                (phone, emp["id"], today), commit=True)
        return emp["id"]
    except Exception as e:
        logger.error("assign_employee error: %s", e)
        return None

def increment_session_messages(phone):
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        db_query(
            "UPDATE conversation_sessions SET message_count = message_count + 1 WHERE phone = ? AND date = ?",
            (phone, today), commit=True)
    except Exception as e:
        logger.error("increment_session_messages error: %s", e)

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
        logger.error("log_employee_order error: %s", e)

def get_daily_cost_summary(days=30):
    try:
        return db_query(
            "SELECT date, SUM(cost_usd) as usd, SUM(cost_bdt) as bdt, COUNT(*) as conversations FROM cost_logs WHERE date >= date('now', '-{} days') GROUP BY date ORDER BY date DESC".format(days),
            fetchall=True) or []
    except Exception as e:
        logger.error("get_daily_cost_summary error: %s", e)
        return []

def get_monthly_cost_summary():
    try:
        return db_query(
            "SELECT strftime('%Y-%m', date) as month, SUM(cost_usd) as usd, SUM(cost_bdt) as bdt, COUNT(*) as conversations FROM cost_logs GROUP BY month ORDER BY month DESC",
            fetchall=True) or []
    except Exception as e:
        logger.error("get_monthly_cost_summary error: %s", e)
        return []

def get_employee_stats(date_filter=None):
    if not date_filter:
        date_filter = datetime.utcnow().strftime("%Y-%m-%d")
    try:
        return db_query(
            """SELECT e.id, e.name, e.role,
                COALESCE(SUM(c.message_count), 0) as messages,
                COALESCE(SUM(c.orders_count), 0) as orders,
                COUNT(c.id) as chats
            FROM employees e
            LEFT JOIN conversation_sessions c ON c.employee_id = e.id AND c.date = ?
            WHERE e.active = 1
            GROUP BY e.id ORDER BY e.id""", (date_filter,), fetchall=True) or []
    except Exception as e:
        logger.error("get_employee_stats error: %s", e)
        return []

# =====================================================================
# 7. GEMINI AI INTENT IMPLEMENTATION
# =====================================================================
def read_knowledge():
    try:
        rows = db_query("SELECT content FROM knowledge ORDER BY created_at DESC", fetchall=True)
        if not rows: return "Brand: Dhaka Exclusive. Bangladesh. Premium kitchenware."
        return "\n".join([r["content"] for r in rows])
    except:
        return "Brand: Dhaka Exclusive. Bangladesh. Premium kitchenware."

def save_knowledge(category, content):
    try:
        db_query("INSERT INTO knowledge (category, content) VALUES (?, ?)", (category, content), commit=True)
    except: pass

def get_ai_answer(user_query, session_context=None):
    if not genai_available or not client:
        return "দুঃখিত প্রিয় গ্রাহক, এখন AI সার্ভিস অফলাইন। প্রতিনিধি শীঘ্রই যোগাযোগ করবেন।"
    try:
        saved_knowledge = read_knowledge()
        products_text = format_catalog()
        
        system_instruction = (
            "You are the AI sales assistant for 'Dhaka Exclusive' (Bangladesh).\n"
            "CRITICAL RULES:\n"
            "1. NEVER say 'নমস্কার'. ALWAYS 'প্রিয় গ্রাহক'.\n"
            "2. Short, polite, Bengali replies. Taka only.\n"
            "3. You CAN track orders — ask for phone/ID, append ||TRACK_DATA||{'key':'VALUE'}||\n"
            "4. You CAN take orders — Name+Phone+Address, append ||ORDER_DATA||{'name':'N','phone':'P','address':'A'}||\n\n"
            f"PRODUCTS:\n{products_text}\n\n"
            f"KNOWLEDGE:\n{saved_knowledge}\n\n"
            f"CONTEXT: {json.dumps(session_context or {}, ensure_ascii=False)}"
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
        return "দুঃখিত প্রিয় গ্রাহক, সিস্টেম ব্যস্ত। প্রতিনিধি শীঘ্রই যোগাযোগ করবেন।"

# =====================================================================
# 8. PRODUCTS & COUPON SYSTEM
# =====================================================================
def get_products():
    try: return db_query("SELECT * FROM products WHERE active = 1", fetchall=True)
    except: return []

def get_product_by_id(pid):
    try: return db_query("SELECT * FROM products WHERE id = ?", (pid,), fetchone=True)
    except: return None

def add_product(name, price, description, stock=10):
    try:
        db_query("INSERT INTO products (name, price, description, stock) VALUES (?, ?, ?, ?)",
                 (name, price, description, stock), commit=True)
    except: pass

def update_stock(product_id, qty_sold):
    try:
        db_query("UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
                 (qty_sold, product_id, qty_sold), commit=True)
    except: pass

def format_catalog():
    products = get_products()
    if not products: return "কোনো প্রোডাক্ট আপডেট হয়নি।"
    lines = ["📋 আমাদের প্রোডাক্ট:"]
    for p in products:
        lines.append(f"\n🔹 {p['name']} — {p['price']}৳\n📝 {p['description']}\n📦 স্টক: {p['stock']}টি")
    return "\n".join(lines)

def validate_coupon(code):
    try:
        row = db_query("SELECT * FROM coupons WHERE code = ? AND active = 1", (code.upper(),), fetchone=True)
        if not row: return None, "কুপন সঠিক নয়।"
        if row["used_count"] >= row["max_uses"]: return None, "কুপন শেষ।"
        if row["valid_until"] and datetime.utcnow().isoformat() > row["valid_until"]:
            return None, "মেয়াদ শেষ।"
        return row, None
    except:
        return None, "কুপন ত্রুটি।"

def apply_coupon(code, original_price):
    coupon, err = validate_coupon(code)
    if not coupon: return original_price, err
    if coupon["discount_percent"] > 0:
        discount = int(original_price * coupon["discount_percent"] / 100)
        return original_price - discount, None
    elif coupon["discount_amount"] > 0:
        return max(0, original_price - coupon["discount_amount"]), None
    return original_price, "কুপনে ডিসকাউন্ট নেই।"

def use_coupon(code):
    try: db_query("UPDATE coupons SET used_count = used_count + 1 WHERE code = ?", (code.upper(),), commit=True)
    except: pass

def get_dashboard_stats():
    try:
        total_users = db_query("SELECT COUNT(*) as c FROM users", fetchone=True)["c"]
        total_orders = db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)["c"]
        today_orders = db_query("SELECT COUNT(*) as c FROM orders WHERE date(created_at) = date('now')", fetchone=True)["c"]
        revenue = db_query("SELECT COALESCE(SUM(total), 0) as s FROM orders WHERE status = 'delivered'", fetchone=True)["s"]
        pending = db_query("SELECT COUNT(*) as c FROM orders WHERE status = 'pending'", fetchone=True)["c"]
        return {"users": total_users, "total_orders": total_orders, "today_orders": today_orders, "revenue": revenue, "pending": pending}
    except:
        return {"users": 0, "total_orders": 0, "today_orders": 0, "revenue": 0, "pending": 0}

# =====================================================================
# 9. USER SESSIONS CONTEXT SYSTEM
# =====================================================================
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

def update_context(phone, key, value):
    try:
        session = get_session(phone)
        ctx = json.loads(session["context"]) if session and session.get("context") else {}
        ctx[key] = value
        set_session(phone, session["state"] if session else "idle", ctx)
    except: pass

def get_context(phone):
    try:
        session = get_session(phone)
        return json.loads(session["context"]) if session and session.get("context") else {}
    except: return {}

def ensure_user(phone):
    try:
        user = db_query("SELECT * FROM users WHERE phone = ?", (phone,), fetchone=True)
        if not user:
            db_query("INSERT OR IGNORE INTO users (phone) VALUES (?)", (phone,), commit=True)
        else:
            db_query("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE phone = ?", (phone,), commit=True)
    except: pass

# =====================================================================
# 10. FLOOD CONTROLLER (RATE LIMIT)
# =====================================================================
def is_rate_limited(phone):
    try:
        one_min_ago = (datetime.utcnow() - timedelta(minutes=1)).isoformat()
        count = db_query("SELECT COUNT(*) as cnt FROM messages WHERE from_number = ? AND created_at > ?",
                         (phone, one_min_ago), fetchone=True)
        return count and count["cnt"] >= 10
    except: return False

def log_message(msg_id, phone, content, msg_type="text"):
    try:
        db_query("INSERT OR IGNORE INTO messages (msg_id, from_number, content, msg_type) VALUES (?, ?, ?, ?)",
                 (msg_id, phone, content, msg_type), commit=True)
    except:
        try:
            db_query("INSERT OR IGNORE INTO messages (msg_id, from_number, content) VALUES (?, ?, ?)",
                     (msg_id, phone, content), commit=True)
        except: pass

# =====================================================================
# 11. BROADCAST ENGINE
# =====================================================================
def broadcast_message(message_text, exclude_admins=False):
    try:
        users = db_query("SELECT phone FROM users", fetchall=True)
        sent = 0
        for u in users:
            phone = u["phone"]
            if exclude_admins and phone in ADMIN_NUMBERS: continue
            if send_text(phone, message_text): sent += 1
            time.sleep(0.5)
        return sent, len(users)
    except: return 0, 0

# =====================================================================
# 12. MAIN PROCESSOR (BULLETPROOF & FIXED)
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

    # 🆕 CHATWOOT CONVERSATION SYNC
    try:
        from chatwoot_dashboard import sync_message_to_conversation
        if msg_type == "text":
            preview = msg.get("text", {}).get("body", "")[:200]
        elif msg_type == "image":
            preview = msg.get("image", {}).get("caption", "")[:200] or "[Image received]"
        elif msg_type in ["audio", "voice"]:
            preview = "[Voice message]"
        else:
            preview = f"[{msg_type}] {str(msg)[:200]}"
        sync_message_to_conversation(from_number, preview, msg_type)
    except Exception:
        pass  # Chatwoot ডাউন থাকলেও যেন বট সচল থাকে

    try:
        if is_rate_limited(from_number):
            send_text(from_number, "প্রিয় গ্রাহক, অনেক মেসেজ পাঠিয়েছেন। কিছুক্ষণ অপেক্ষা করুন।")
            return
    except: pass

    # 🎙️ AUDIO MESSAGE PREVENT
    if msg_type in ["audio", "voice"]:
        send_text(from_number, "প্রিয় গ্রাহক, ভয়েস মেসেজ সাপোর্টেড নয়। দয়া করে টেক্সটে লিখুন।")
        return

    # 📸 IMAGE — SMART REPLY (FIXED & FULLY IMPLEMENTED)
    if msg_type == "image":
        caption = msg.get("image", {}).get("caption", "").strip()
        image_id = msg.get("image", {}).get("id", "")
        
        logger.info("Received image ID %s with caption: %s", image_id, caption)
        
        if caption:
            session_context = get_context(from_number)
            ai_reply = get_ai_answer(caption, session_context)
            send_text(from_number, ai_reply)
        else:
            send_text(from_number, "প্রিয় গ্রাহক, আপনার ছবিটি আমরা পেয়েছি। আমাদের প্রতিনিধি খুব শীঘ্রই এটি দেখে আপনাকে রিপ্লাই দিচ্ছেন।")
        return

    # 📝 TEXT MESSAGE PROCESSING WITH SYSTEM TRIGGERS
    if msg_type == "text":
        user_query = msg.get("text", {}).get("body", "").strip()
        if not user_query: return
            
        session_context = get_context(from_number)
        ai_reply = get_ai_answer(user_query, session_context)
        
        # ডাটা এক্সট্রাকশন হ্যান্ডলার (যদি এআই ব্লক রিটার্ন করে)
        json_block = extract_json_block(ai_reply)
        if json_block:
            try:
                data = json.loads(json_block)
                # কাস্টম ট্র্যাকিং হ্যান্ডলার ট্র্রিগার
                if "||TRACK_DATA||" in ai_reply or "key" in data:
                    tracking_key = data.get("key")
                    status_msg = track_pathao_order(tracking_key)
                    clean_reply = ai_reply.split("||")[0].strip()
                    send_text(from_number, f"{clean_reply}\n\n🔍 বর্তমান আপডেট: {status_msg}")
                    return
                
                # কাস্টম অর্ডার হ্যান্ডলার ট্রিগার
                if "||ORDER_DATA||" in ai_reply or "address" in data:
                    name = data.get("name", "গ্রাহক")
                    phone = data.get("phone", from_number)
                    address = data.get("address", "Not Provided")
                    
                    # ডাটাবেজে একটি ডিফল্ট পেন্ডিং অর্ডার লগ তৈরি
                    db_query(
                        "INSERT INTO orders (phone, name, address, total) VALUES (?, ?, ?, ?)",
                        (phone, name, address, 0), commit=True)
                    
                    clean_reply = ai_reply.split("||")[0].strip()
                    send_text(from_number, f"{clean_reply}\n\n✅ আপনার অর্ডারটির ডিটেইলস ব্যাকএন্ডে সাবমিট করা হয়েছে।")
                    return
            except Exception as json_err:
                logger.error("JSON processing error inside text processor: %s", json_err)

        # সাধারণ এআই মেসেজ পাঠানো
        send_text(from_number, ai_reply)
        return
