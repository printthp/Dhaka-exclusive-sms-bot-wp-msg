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
from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session
from threading import Thread, Lock
import requests
import functools

# =====================================================================
# 0. LOGGING
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# =====================================================================
# 1. ENV SECRETS
# =====================================================================
PERMANENT_TOKEN = os.environ.get("PERMANENT_TOKEN", "EAANtSb24BiwBRREXu8HztnpOLtamcKIvi09Qb24LiYax45S4aoYtFEVKEQZAxigfO2wbGf6RgHh51IURbQzKKrzPhkcprLxHpZBfOwxZAVCscdVOpjbapbS9sOLCIqZBM8tZAtSRRaVVYSTZBjUkkPZAQaLABSnG6cQcgQcwqZBC5I5yrB4cXgoUPDlzzn7HzUwsMAZDZD")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "1039959469208417")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "")
GEMINI_KEY = os.environ.get("GEMINI_KEY", "AIzaSyCRZIRWSoenfhA33qr7rkzoa56Byun0IWU")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "dhakaex0020")
APP_SECRET = os.environ.get("APP_SECRET", "")

ADMIN_NUMBERS_STR = os.environ.get("ADMIN_NUMBERS", "8801717121068")
ADMIN_NUMBERS = [n.strip() for n in ADMIN_NUMBERS_STR.split(",") if n.strip()]

PATHAO_BASE_URL = os.environ.get("PATHAO_BASE_URL", "https://api-hermes.pathao.com")
PATHAO_STORE_ID = os.environ.get("PATHAO_STORE_ID", "333358")
PATHAO_CLIENT_ID = os.environ.get("PATHAO_CLIENT_ID", "openOlRa7A")
PATHAO_CLIENT_SECRET = os.environ.get("PATHAO_CLIENT_SECRET", "7clJGfV1jh5njQEuR5yepVXZ9nYAjGORhNCOjgzG")
PATHAO_MERCHANT_EMAIL = os.environ.get("PATHAO_MERCHANT_EMAIL", "cocid1000006@gmail.com")
PATHAO_MERCHANT_PASSWORD = os.environ.get("PATHAO_MERCHANT_PASSWORD", "trustedaA@2")

BUSINESS_NAME = os.environ.get("BUSINESS_NAME", "Dhaka Exclusive")
BUSINESS_HOURS = os.environ.get("BUSINESS_HOURS", "09:00-21:00")

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
            c.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            defaults = [
                ("business_name", BUSINESS_NAME),
                ("logo_url", ""),
                ("primary_color", "#667eea"),
                ("header_color", "#1f2937"),
                ("sidebar_color", "#374151"),
                ("accent_color", "#10b981"),
                ("fb_catalog_id", os.environ.get("FB_CATALOG_ID", "")),
                ("fb_access_token", os.environ.get("FB_ACCESS_TOKEN", "")),
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

# =====================================================================
# 3. HELPERS
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
# 4. WEBHOOK VERIFY
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
# 5. PATHAO API
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
BD_USER_INIT_COST = 0.004   # Meta WhatsApp user-initiated (approx)
BD_BUSINESS_INIT_COST = 0.006  # Meta WhatsApp business-initiated (approx)

def ensure_employee_tables():
    """Add missing analytics tables to existing DB."""
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


# Run employee table migration after DB init
ensure_employee_tables()


def hash_pwd(text):
    return hashlib.sha256(text.encode()).hexdigest()


def log_outgoing_cost(phone, msg_type="text"):
    """Log estimated Meta API cost per unique phone per day."""
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        existing = db_query(
            "SELECT id FROM cost_logs WHERE date = ? AND phone = ? LIMIT 1",
            (today, phone), fetchone=True)
        if existing:
            return  # same conversation today, no extra cost
        cost_usd = BD_USER_INIT_COST
        cost_bdt = cost_usd * USD_TO_BDT
        db_query(
            "INSERT INTO cost_logs (date, conversation_type, phone, cost_usd, cost_bdt, message_type) VALUES (?, ?, ?, ?, ?, ?)",
            (today, "user_initiated", phone, cost_usd, cost_bdt, msg_type), commit=True)
    except Exception as e:
        logger.error("log_outgoing_cost: %s", e)


def assign_employee_to_conversation(phone):
    """Assign first active employee to a conversation."""
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
def read_knowledge():
    try:
        rows = db_query("SELECT content FROM knowledge ORDER BY created_at DESC", fetchall=True)
        if not rows:
            return "Brand: Dhaka Exclusive. Bangladesh. Premium kitchenware."
        return "\n".join([r["content"] for r in rows])
    except:
        return "Brand: Dhaka Exclusive. Bangladesh. Premium kitchenware."

def save_knowledge(category, content):
    try:
        db_query("INSERT INTO knowledge (category, content) VALUES (?, ?)", (category, content), commit=True)
    except:
        pass

def get_ai_answer(user_query, session_context=None):
    if not genai_available or not client:
        return "দুঃখিত প্রিয় গ্রাহক, এখন AI সার্ভিস অফলাইন। প্রতিনিধি শীঘ্রই যোগাযোগ করবেন।"
    try:
        saved_knowledge = read_knowledge()
        products_text = format_catalog()
        
        # 🔥 FIXED: Clearly tell AI it CAN track orders
        system_instruction = (
            "You are the AI sales assistant for 'Dhaka Exclusive' (Bangladesh).\n"
            "CRITICAL RULES:\n"
            "1. NEVER say 'নমস্কার'. ALWAYS 'প্রিয় গ্রাহক'.\n"
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
        return "দুঃখিত প্রিয় গ্রাহক, সিস্টেম ব্যস্ত। প্রতিনিধি শীঘ্রই যোগাযোগ করবেন।"

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

def validate_coupon(code):
    try:
        row = db_query("SELECT * FROM coupons WHERE code = ? AND active = 1", (code.upper(),), fetchone=True)
        if not row:
            return None, "কুপন সঠিক নয়।"
        if row["used_count"] >= row["max_uses"]:
            return None, "কুপন শেষ।"
        if row["valid_until"] and datetime.now().isoformat() > row["valid_until"]:
            return None, "মেয়াদ শেষ।"
        return row, None
    except:
        return None, "কুপন ত্রুটি।"

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
    try:
        db_query("UPDATE coupons SET used_count = used_count + 1 WHERE code = ?", (code.upper(),), commit=True)
    except:
        pass

def get_dashboard_stats():
    try:
        total_users = db_query("SELECT COUNT(*) as c FROM users", fetchone=True)["c"]
        total_orders = db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)["c"]
        today_orders = db_query("SELECT COUNT(*) as c FROM orders WHERE date(created_at) = date('now')", fetchone=True)["c"]
        revenue = db_query("SELECT COALESCE(SUM(total), 0) as s FROM orders WHERE status = 'delivered'", fetchone=True)["s"]
        pending = db_query("SELECT COUNT(*) as c FROM orders WHERE status = 'pending'", fetchone=True)["c"]
        return {"users": total_users, "total_orders": total_orders, "today_orders": today_orders,
                "revenue": revenue, "pending": pending}
    except:
        return {"users": 0, "total_orders": 0, "today_orders": 0, "revenue": 0, "pending": 0}

# =====================================================================
# 9. SESSION
# =====================================================================
def get_session(phone):
    try:
        return db_query("SELECT * FROM sessions WHERE phone = ?", (phone,), fetchone=True)
    except:
        return None

def set_session(phone, state, context=None):
    try:
        ctx = json.dumps(context or {}, ensure_ascii=False)
        existing = get_session(phone)
        if existing:
            db_query("UPDATE sessions SET state = ?, context = ?, last_active = CURRENT_TIMESTAMP WHERE phone = ?",
                     (state, ctx, phone), commit=True)
        else:
            db_query("INSERT INTO sessions (phone, state, context) VALUES (?, ?, ?)",
                     (phone, state, ctx), commit=True)
    except:
        pass

def update_context(phone, key, value):
    try:
        session = get_session(phone)
        ctx = json.loads(session["context"]) if session and session.get("context") else {}
        ctx[key] = value
        set_session(phone, session["state"] if session else "idle", ctx)
    except:
        pass

def get_context(phone):
    try:
        session = get_session(phone)
        return json.loads(session["context"]) if session and session.get("context") else {}
    except:
        return {}

def ensure_user(phone):
    try:
        user = db_query("SELECT * FROM users WHERE phone = ?", (phone,), fetchone=True)
        if not user:
            db_query("INSERT OR IGNORE INTO users (phone) VALUES (?)", (phone,), commit=True)
        else:
            db_query("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE phone = ?", (phone,), commit=True)
    except:
        pass

# =====================================================================
# 10. RATE LIMIT
# =====================================================================
def is_rate_limited(phone):
    try:
        one_min_ago = (datetime.utcnow() - timedelta(minutes=1)).isoformat()
        count = db_query("SELECT COUNT(*) as cnt FROM messages WHERE from_number = ? AND created_at > ?",
                         (phone, one_min_ago), fetchone=True)
        return count and count["cnt"] >= 10
    except:
        return False

def log_message(msg_id, phone, content, msg_type="text"):
    try:
        db_query("INSERT OR IGNORE INTO messages (msg_id, from_number, content, msg_type) VALUES (?, ?, ?, ?)",
                 (msg_id, phone, content, msg_type), commit=True)
    except:
        try:
            db_query("INSERT OR IGNORE INTO messages (msg_id, from_number, content) VALUES (?, ?, ?)",
                     (msg_id, phone, content), commit=True)
        except:
            pass

# =====================================================================
# 11. BROADCAST
# =====================================================================
def broadcast_message(message_text, exclude_admins=False):
    try:
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
    except:
        return 0, 0

# =====================================================================
# 12. MAIN PROCESSOR (BULLETPROOF)
# =====================================================================
def process_webhook_async(msg, from_number):
    msg_type = msg.get("type")
    msg_id = msg.get("id")

    try:
        existing = db_query("SELECT 1 FROM messages WHERE msg_id = ?", (msg_id,), fetchone=True)
        if existing:
            return
    except:
        pass

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
        pass  # Don't break bot if chatwoot fails

    try:
        if is_rate_limited(from_number):
            send_text(from_number, "প্রিয় গ্রাহক, অনেক মেসেজ পাঠিয়েছেন। কিছুক্ষণ অপেক্ষা করুন।")
            return
    except:
        pass

    # 🎙️ AUDIO
    if msg_type in ["audio", "voice"]:
        send_text(from_number, "প্রিয় গ্রাহক, ভয়েস মেসেজ সাপোর্টেড নয়। টেক্সটে লিখুন।")
        return

    # 📸 IMAGE — SMART REPLY (FIXED!)
    if msg_type == "image":
        caption = msg.get("image", {}).get("caption", "").lower()
        
        if any(k in caption for k in ["কত", "দাম", "কিনব", "চাই", "price", "order"]):
            send_text(from_number, 
                "📸 প্রোডাক্ট ছবি পেয়েছি!\n\n"
                "আমাদের ক্যাটালগ দেখতে 'কিনব' লিখুন,\n"
                "অথবা প্রোডাক্টের নামটি লিখুন।")
            return
        
        if any(k in caption for k in ["পেমেন্ট", "টাকা", "bkash", "nagad", "paid", "রিসিপ্ট"]):
            send_text(from_number,
                "💳 পেমেন্ট রিসিপ্ট পেয়েছি!\n\n"
                "আপনার অর্ডার আইডি বা ফোন নম্বরটি দিন,\n"
                "আমরা কনফার্ম করে জানাবো।")
            return
        
        send_text(from_number,
            "📸 ছবি পেয়েছি!\n\n"
            "• প্রোডাক্ট কিনতে চাইলে 'কিনব' লিখুন\n"
            "• পেমেন্ট রিসিপ্ট হলে অর্ডার আইডি দিন\n"
            "• অর্ডার ট্র্যাক করতে ফোন নম্বর দিন")
        return

    if msg_type != "text":
        send_text(from_number, "প্রিয় গ্রাহক, শুধু টেক্সট বুঝি।")
        return

    user_text = msg["text"]["body"].strip()
    session = get_session(from_number)
    state = session["state"] if session else "idle"
    context = get_context(from_number)

    # 🔐 ADMIN
    if user_text.lower().startswith("admin:"):
        if from_number not in ADMIN_NUMBERS:
            send_text(from_number, "দুঃখিত, এই কমান্ড শুধু অ্যাডমিনের জন্য।")
            return
        cmd = user_text[6:].strip()

        if cmd.lower().startswith("addproduct"):
            parts = [p.strip() for p in cmd.split("|")]
            if len(parts) >= 4:
                add_product(parts[1], int(parts[2]), parts[3], stock=int(parts[4]) if len(parts) > 4 else 10)
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
            send_text(from_number,
                f"📊 ড্যাশবোর্ড:\n"
                f"👤 ইউজার: {stats['users']}\n"
                f"📦 মোট অর্ডার: {stats['total_orders']}\n"
                f"📅 আজ: {stats['today_orders']}\n"
                f"💰 রেভেনিউ: {stats['revenue']}৳\n"
                f"⏳ পেন্ডিং: {stats['pending']}")
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
                    (code.upper(), disc_pct, disc_amt, maxuse, valid), commit=True
                )
                send_text(from_number, f"🎫 কুপন '{code}' তৈরি!")
            else:
                send_text(from_number, "ফরম্যাট: admin:coupon | CODE | value | percent/amount | max_uses | [YYYY-MM-DD]")
            return

        if cmd.lower().startswith("help"):
            send_text(from_number,
                "🔧 অ্যাডমিন কমান্ড:\n\n"
                "admin:addproduct | নাম | দাম | বর্ণনা | [স্টক]\n"
                "admin:knowledge তথ্য\n"
                "admin:orders\n"
                "admin:stats\n"
                "admin:broadcast মেসেজ\n"
                "admin:coupon | CODE | 10 | percent | 100 | 2025-12-31\n"
                "admin:help")
            return

        send_text(from_number, "অজানা কমান্ড। admin:help লিখুন।")
        return

    # ─────────────── SMART IDLE HANDLERS (FIXED!) ───────────────

    # 🎯 "তুমি কি কি পারো" → Capability list
    if any(k in user_text.lower() for k in ["তুমি কি কি পারো", "তুমি কি পারো", "কি কি পারো", "what can you do", "তোমার কাজ কি", "সাহায্য", "help"]):
        send_text(from_number,
            "🙋‍♂️ প্রিয় গ্রাহক, আমি আপনাকে সাহায্য করতে পারি:\n\n"
            "1️⃣ 🛒 প্রোডাক্ট অর্ডার করতে\n"
            "2️⃣ 📦 আপনার অর্ডার ট্র্যাক করতে\n"
            "3️⃣ 💰 প্রোডাক্টের দাম ও তথ্য জানতে\n"
            "4️⃣ 🚚 ডেলিভারি সম্পর্কে জানতে\n\n"
            "কীভাবে সাহায্য করতে পারি?")
        return

    # 🎯 "অর্ডার কোথায়" → Auto track from DB
    if any(k in user_text.lower() for k in ["অর্ডার কোথায়", "আমার অর্ডার", "ট্র্যাক", "track", "কোথায় আছে", "পণ্য কোথায়", "ডেলিভারি কোথায়", "কবে আসবে"]):
        orders = db_query(
            "SELECT * FROM orders WHERE phone = ? ORDER BY created_at DESC LIMIT 1",
            (from_number,), fetchone=True)
        if orders and orders.get("pathao_consignment_id"):
            live_status = track_pathao_order(orders["pathao_consignment_id"])
            send_text(from_number,
                f"📦 আপনার সর্বশেষ অর্ডার (#{orders['id']}):\n\n"
                f"📌 স্ট্যাটাস: {live_status}\n"
                f"🆔 Tracking: {orders['pathao_consignment_id']}")
            return
        else:
            send_text(from_number,
                "📦 অর্ডার ট্র্যাক করতে আপনার ফোন নম্বর বা Tracking ID টি দিন:\n"
                "(যেমন: 01712XXXXXX)")
            return

    # 🎯 Direct phone number → Tracking
    clean_text = user_text.replace(" ", "").replace("+", "").strip()
    if clean_text.isdigit() and (len(clean_text) == 11 or len(clean_text) == 13) and clean_text.startswith(("01", "8801")):
        live_status = track_pathao_order(clean_text)
        send_text(from_number, f"প্রিয় গ্রাহক, আপনার অর্ডারের অবস্থা:\n\n📌 {live_status}")
        return

    # ─────────────── STATE MACHINE ───────────────

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
            zones = get_pathao_zones(city_id)
            if zones:
                sections = [{"title": "জোন", "rows": [{"id": f"zone_{z['zone_id']}", "title": z['zone_name'][:24]} for z in zones[:10]]}]
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
                sections = [{"title": "এরিয়া", "rows": [{"id": f"area_{a['area_id']}", "title": a['area_name'][:24]} for a in areas[:10]]}]
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
            send_buttons(from_number,
                f"🎫 কুপন আছে? কোড লিখুন, না থাকলে 'নেই'।\n\n"
                f"💰 সাবটোটাল: {ctx['subtotal']}৳\n🚚 ডেলিভারি: {ctx['delivery_charge']}৳\n💵 মোট: {ctx['total']}৳",
                [{"id": "no_coupon", "title": "কুপন নেই"}])
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
                send_text(from_number, f"🎉 কুপন '{user_text.upper()}' প্রযোজ্য! ডিসকাউন্ট: {ctx['discount']}৳")

        set_session(from_number, "awaiting_confirmation", context=ctx)
        ctx = get_context(from_number)
        summary = (
            f"📦 ফাইনাল অর্ডার\n━━━━━━━━━━━━━━\n"
            f"🔹 {ctx['product_name']} x {ctx['quantity']}\n"
            f"💰 প্রাইস: {ctx['subtotal']}৳\n🚚 ডেলিভারি: {ctx['delivery_charge']}৳\n"
        )
        if ctx.get("discount", 0) > 0:
            summary += f"🎫 ডিসকাউন্ট: -{ctx['discount']}৳\n"
        summary += (
            f"━━━━━━━━━━━━━━\n💵 মোট: {ctx['total']}৳\n"
            f"💳 পেমেন্ট: {ctx['payment_method'].upper()}\n"
            f"👤 {ctx['name']}\n📞 {ctx['phone']}\n📍 {ctx['address']}\n\n"
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

    # ─────────────── IDLE: HISTORY / CANCEL / HANDOFF / FEEDBACK ───────────────

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

    # ─────────────── AI FALLBACK ───────────────
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
        "version": "3.1",
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

"""
INLINE ADMIN PANEL — pasted into SMS_BOT.py
"""

# =====================================================================
# 14. DYNAMIC ADMIN PANEL — INLINED
# =====================================================================
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

def fetch_facebook_catalog(catalog_id, access_token):
    try:
        url = f"https://graph.facebook.com/v18.0/{catalog_id}/products"
        r = requests.get(url, params={"access_token": access_token, "fields": "name,price,description,image_url,availability", "limit": "100"})
        data = r.json()
        if "error" in data: return [], data["error"].get("message", "Facebook API error")
        return data.get("data", []), None
    except Exception as e: return [], str(e)

def parse_fb_price(val):
    if isinstance(val, (int, float)): return int(val)
    if isinstance(val, str):
        val = val.replace("BDT", "").replace("৳", "").replace(",", "").strip()
        try: return int(float(val))
        except: pass
    return 0

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
*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f3f4f6;color:#1f2937}
.header{background:{{ settings.header_color }};color:#fff;padding:12px 20px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100}
.header h1{font-size:18px}.tabs{display:flex;gap:8px}
.tab-btn{padding:8px 16px;border:none;border-radius:8px;background:rgba(255,255,255,.15);color:#fff;cursor:pointer;font-size:13px}
.tab-btn.active{background:#fff;color:{{ settings.header_color }};font-weight:600}
.container{max-width:1200px;margin:0 auto;padding:20px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px;margin-bottom:24px}
.stat-card{background:#fff;padding:20px;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.stat-card h3{font-size:13px;color:#6b7280;text-transform:uppercase;margin-bottom:8px}
.stat-card .num{font-size:28px;font-weight:700;color:{{ settings.primary_color }}}
.card{background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.08);margin-bottom:20px;overflow:hidden}
.card-header{padding:16px 20px;border-bottom:1px solid #e5e7eb;display:flex;align-items:center;justify-content:space-between}
.card-header h2{font-size:16px}.btn{padding:8px 16px;border:none;border-radius:8px;cursor:pointer;font-size:13px;font-weight:500}
.btn-primary{background:{{ settings.primary_color }};color:#fff}.btn-success{background:#10b981;color:#fff}
.btn-danger{background:#ef4444;color:#fff}.btn-sm{padding:6px 12px;font-size:12px}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:12px 16px;text-align:left;border-bottom:1px solid #e5e7eb}
th{background:#f9fafb;font-weight:600;font-size:12px;text-transform:uppercase;color:#6b7280}tr:hover{background:#f9fafb}
.form-group{margin-bottom:12px}.form-group label{display:block;font-size:13px;font-weight:500;margin-bottom:4px;color:#374151}
.form-group input,.form-group textarea{width:100%;padding:8px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:14px}
.search-box{padding:8px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:14px;width:240px}
.modal-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.5);z-index:200;align-items:center;justify-content:center}
.modal-overlay.active{display:flex}.modal{background:#fff;padding:24px;border-radius:12px;width:90%;max-width:500px;max-height:90vh;overflow-y:auto}
.section{display:none}.section.active{display:block}
.color-picker-wrapper{display:flex;align-items:center;gap:8px}
.settings-preview{display:flex;gap:12px;margin-bottom:16px;padding:12px;background:#f3f4f6;border-radius:8px}
.demo-header{padding:8px 16px;border-radius:6px;color:#fff;font-weight:600}
.demo-btn{padding:8px 16px;border-radius:6px;color:#fff;font-weight:600}
</style>
</head>
<body>
<div class="header"><h1>🔧 Admin Panel | {{ settings.business_name }}</h1>
<div class="tabs"><button class="tab-btn active" onclick="showTab('dashboard')">📊 Dashboard</button><button class="tab-btn" onclick="showTab('products')">📦 Products</button><button class="tab-btn" onclick="showTab('orders')">🛒 Orders</button><button class="tab-btn" onclick="showTab('users')">👤 Users</button><button class="tab-btn" onclick="showTab('messages')">💬 Messages</button><button class="tab-btn" onclick="showTab('tools')">📢 Tools</button><button class="tab-btn" onclick="showTab('settings')">⚙️ Settings</button></div></div>
<div class="container">
<div id="dashboard" class="section active">
<div class="stats"><div class="stat-card"><h3>মোট অর্ডার</h3><div class="num">{{ stats.total_orders }}</div></div><div class="stat-card"><h3>মোট রেভেনিউ</h3><div class="num">৳{{ stats.revenue }}</div></div><div class="stat-card"><h3>মোট ইউজার</h3><div class="num">{{ stats.users }}</div></div><div class="stat-card"><h3>পেন্ডিং</h3><div class="num">{{ stats.pending }}</div></div></div>
<div class="card"><div class="card-header"><h2>📝 সর্বশেষ ৫টি অর্ডার</h2></div>
<table><tr><th>ID</th><th>কাস্টমার</th><th>ফোন</th><th>টোটাল</th><th>স্ট্যাটাস</th></tr>
{% for o in recent_orders %}<tr><td>#{{ o.id }}</td><td>{{ o.name or 'N/A' }}</td><td>{{ o.phone }}</td><td>৳{{ o.total }}</td><td><span style="padding:4px 8px;border-radius:6px;background:{% if o.status=='delivered' %}#d1fae5{% elif o.status=='cancelled' %}#fee2e2{% else %}#fef3c7{% endif %};font-size:12px">{{ o.status }}</span></td></tr>{% endfor %}
</table></div></div>
<div id="products" class="section"><div class="card"><div class="card-header"><h2>📦 প্রোডাক্ট ম্যানেজমেন্ট</h2><div><button class="btn btn-primary" onclick="openModal('productModal')">➕ যোগ করুন</button> <button class="btn btn-success" onclick="openModal('importModal')">📥 CSV Import</button></div></div>
<table><tr><th>ID</th><th>নাম</th><th>দাম</th><th>স্টক</th><th>অ্যাকশন</th></tr>
{% for p in products %}<tr><td>#{{ p.id }}</td><td>{{ p.name }}</td><td>৳{{ p.price }}</td><td>{{ p.stock }}</td><td><button class="btn btn-sm btn-success" onclick="editProduct({{ p.id }},'{{ (p.name or '')|replace(\"'\",\\\"'\\\") }}',{{ p.price }},{{ p.stock }},'{{ (p.description or '')|replace(\"'\",\\\"'\\\") }}')">✏️</button> <button class="btn btn-sm btn-danger" onclick="deleteProduct({{ p.id }})">🗑️</button></td></tr>{% endfor %}
</table></div></div>
<div id="orders" class="section"><div class="card"><div class="card-header"><h2>🛒 অর্ডার ম্যানেজমেন্ট</h2><input type="text" class="search-box" id="orderSearch" placeholder="ফোন/নামে সার্চ..." onkeyup="searchOrders()"></div>
<table><tr><th>ID</th><th>কাস্টমার</th><th>ফোন</th><th>ঠিকানা</th><th>টোটাল</th><th>স্ট্যাটাস</th><th>অ্যাকশন</th></tr>
{% for o in orders %}<tr data-phone="{{ o.phone }}" data-name="{{ o.name or '' }}"><td>#{{ o.id }}</td><td>{{ o.name or 'N/A' }}</td><td>{{ o.phone }}</td><td>{{ o.address or 'N/A' }}</td><td>৳{{ o.total }}</td><td><select onchange="updateOrderStatus({{ o.id }},this.value)" style="padding:4px 8px;border-radius:6px;border:1px solid #d1d5db"><option value="pending" {{ 'selected' if o.status=='pending' else '' }}>Pending</option><option value="created" {{ 'selected' if o.status=='created' else '' }}>Created</option><option value="confirmed" {{ 'selected' if o.status=='confirmed' else '' }}>Confirmed</option><option value="shipped" {{ 'selected' if o.status=='shipped' else '' }}>Shipped</option><option value="delivered" {{ 'selected' if o.status=='delivered' else '' }}>Delivered</option><option value="cancelled" {{ 'selected' if o.status=='cancelled' else '' }}>Cancelled</option></select></td><td><button class="btn btn-sm btn-danger" onclick="deleteOrder({{ o.id }})">🗑️</button></td></tr>{% endfor %}
</table></div></div>
<div id="users" class="section"><div class="card"><div class="card-header"><h2>👤 কাস্টমার লিস্ট</h2></div>
<table><tr><th>ফোন</th><th>নাম</th><th>মোট অর্ডার</th><th>মোট খরচ</th></tr>
{% for u in users %}<tr><td>{{ u.phone }}</td><td>{{ u.name or 'N/A' }}</td><td>{{ u.total_orders }}</td><td>৳{{ u.total_spent }}</td></tr>{% endfor %}
</table></div></div>
<div id="tools" class="section"><div class="card"><div class="card-header"><h2>📢 ব্রডকাস্ট মেসেজ</h2></div><div style="padding:20px"><div class="form-group"><label>সব কাস্টমারকে মেসেজ পাঠান:</label><textarea id="broadcastText" rows="4" placeholder="মেসেজ লিখুন..."></textarea></div><button class="btn btn-primary" onclick="sendBroadcast()">📤 পাঠান</button><div id="broadcastResult" style="margin-top:12px;font-size:14px"></div></div></div></div>
<div id="settings" class="section">
<div class="card"><div class="card-header"><h2>⚙️ Appearance & Branding</h2></div><div style="padding:20px;max-width:600px">
<div class="settings-preview"><div class="demo-header" id="previewHeader" style="background:{{ settings.header_color }}">Header Preview</div><div class="demo-btn" id="previewBtn" style="background:{{ settings.primary_color }}">Button Preview</div></div>
<div class="form-group"><label>বিজনেস নাম</label><input type="text" id="settingName" value="{{ settings.business_name }}"></div>
<div class="form-group"><label>লোগো URL</label><input type="text" id="settingLogo" value="{{ settings.logo_url }}" placeholder="https://..."></div>
<div class="form-group"><label>প্রাইমারি কালার</label><div class="color-picker-wrapper"><input type="color" id="settingPrimary" value="{{ settings.primary_color }}" onchange="updatePreview()"><input type="text" id="settingPrimaryText" value="{{ settings.primary_color }}" style="width:120px" onchange="document.getElementById('settingPrimary').value=this.value;updatePreview()"></div></div>
<div class="form-group"><label>হেডার কালার</label><div class="color-picker-wrapper"><input type="color" id="settingHeader" value="{{ settings.header_color }}" onchange="updatePreview()"><input type="text" id="settingHeaderText" value="{{ settings.header_color }}" style="width:120px" onchange="document.getElementById('settingHeader').value=this.value;updatePreview()"></div></div>
<div class="form-group"><label>অ্যাকসেন্ট কালার</label><div class="color-picker-wrapper"><input type="color" id="settingAccent" value="{{ settings.accent_color }}" onchange="updatePreview()"><input type="text" id="settingAccentText" value="{{ settings.accent_color }}" style="width:120px" onchange="document.getElementById('settingAccent').value=this.value;updatePreview()"></div></div>
</div></div>
<div class="card"><div class="card-header"><h2>📘 Facebook Catalog</h2></div><div style="padding:20px;max-width:600px">
<div class="form-group"><label>Facebook Catalog ID</label><input type="text" id="settingFbCatalog" value="{{ settings.fb_catalog_id }}"></div>
<div class="form-group"><label>Facebook Access Token</label><input type="text" id="settingFbToken" value="{{ settings.fb_access_token }}"></div>
<button class="btn btn-primary" onclick="saveSettings()">💾 সব Settings সেভ করুন</button>
<div id="settingsResult" style="margin-top:12px"></div>
</div></div></div>
<div id="messages" class="section"><div class="card"><div class="card-header"><h2>💬 কাস্টমার মেসেজেস</h2><input type="text" id="msgSearch" class="search-box" placeholder="সার্চ..." onkeyup="filterMessages()"></div><div style="display:flex;height:500px"><div style="width:300px;border-right:1px solid #e5e7eb;overflow-y:auto" id="convList">{{CONVERSATION_LIST}}</div><div style="flex:1;display:flex;flex-direction:column"><div style="flex:1;overflow-y:auto;padding:16px" id="chatBox"><div style="text-align:center;color:#9ca3af;padding-top:100px">কোনো কাস্টমার সিলেক্ট করুন</div></div><div style="padding:12px;border-top:1px solid #e5e7eb;display:flex;gap:8px"><input type="text" id="replyText" placeholder="মেসেজ লিখুন..." style="flex:1;padding:8px 12px;border:1px solid #d1d5db;border-radius:8px" onkeypress="if(event.key==='Enter')sendReply()"><button class="btn btn-primary" onclick="sendReply()">পাঠান</button></div></div></div></div></div>
</div>
<div class="modal-overlay" id="productModal"><div class="modal"><h3>➕ প্রোডাক্ট</h3><input type="hidden" id="prodId"><div class="form-group"><label>নাম</label><input type="text" id="prodName"></div><div class="form-group"><label>দাম (৳)</label><input type="number" id="prodPrice"></div><div class="form-group"><label>স্টক</label><input type="number" id="prodStock" value="10"></div><div class="form-group"><label>বিবরণ</label><textarea id="prodDesc" rows="3"></textarea></div><div class="form-group"><label>ছবি URL</label><input type="text" id="prodImage" placeholder="https://..."></div><div style="display:flex;gap:8px"><button class="btn btn-primary" onclick="saveProduct()">💾 সেভ</button><button class="btn" onclick="closeModal('productModal')" style="background:#e5e7eb">বাতিল</button></div></div></div>
<div class="modal-overlay" id="importModal"><div class="modal"><h3>📥 CSV Import</h3><p style="font-size:13px;color:#6b7280;margin-bottom:8px">Format: নাম,দাম,স্টক,বিবরণ,ছবি_URL</p><textarea id="csvInput" rows="8" style="width:100%;font-family:monospace;font-size:13px" placeholder="পেস্টেল কুর্তি,1299,15,সুন্দর কুর্তি,https://..."></textarea><div id="importResult" style="margin:8px 0;font-size:14px"></div><div style="display:flex;gap:8px"><button class="btn btn-primary" onclick="importCSV()">📥 Import</button><button class="btn" onclick="closeModal('importModal')" style="background:#e5e7eb">বাতিল</button></div></div></div>
<script>
function showTab(id){document.querySelectorAll('.section').forEach(s=>s.classList.remove('active'));document.getElementById(id).classList.add('active');document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));event.target.classList.add('active')}
function openModal(id){document.getElementById(id).classList.add('active')}
function closeModal(id){document.getElementById(id).classList.remove('active')}
function editProduct(id,name,price,stock,desc){document.getElementById('prodId').value=id;document.getElementById('prodName').value=name;document.getElementById('prodPrice').value=price;document.getElementById('prodStock').value=stock;document.getElementById('prodDesc').value=desc;openModal('productModal')}
function saveProduct(){const id=document.getElementById('prodId').value;const data={id:id||null,name:document.getElementById('prodName').value,price:parseInt(document.getElementById('prodPrice').value)||0,stock:parseInt(document.getElementById('prodStock').value)||0,description:document.getElementById('prodDesc').value,image_url:document.getElementById('prodImage').value};fetch('/admin/api/product',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(()=>{closeModal('productModal');location.reload()}).catch(err=>alert('❌ Error: '+err))}
function deleteProduct(id){if(!confirm('ডিলিট করবেন?'))return;fetch('/admin/api/product/'+id,{method:'DELETE'}).then(()=>location.reload()).catch(err=>alert('❌ Error: '+err))}
function updateOrderStatus(id,status){fetch('/admin/api/order/'+id+'/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:status})}).then(r=>r.json()).then(d=>alert(d.message||'Updated')).catch(err=>alert('❌ Error: '+err))}
function deleteOrder(id){if(!confirm('অর্ডার ডিলিট করবেন?'))return;fetch('/admin/api/order/'+id,{method:'DELETE'}).then(()=>location.reload()).catch(err=>alert('❌ Error: '+err))}
function searchOrders(){const q=document.getElementById('orderSearch').value.toLowerCase();document.querySelectorAll('#orders tr[data-phone]').forEach(tr=>{const phone=tr.dataset.phone.toLowerCase();const name=tr.dataset.name.toLowerCase();tr.style.display=(phone.includes(q)||name.includes(q))?'':'none'})}
function sendBroadcast(){const text=document.getElementById('broadcastText').value.trim();if(!text)return;fetch('/admin/api/broadcast',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text})}).then(r=>r.json()).then(d=>{document.getElementById('broadcastResult').textContent=d.message||'পাঠানো হয়েছে'}).catch(err=>alert('❌ Error: '+err))}
function importCSV(){const text=document.getElementById('csvInput').value.trim();if(!text)return;fetch('/admin/api/products/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csv:text})}).then(r=>r.json()).then(d=>{document.getElementById('importResult').textContent=d.message||d.error||'Done';if(d.success)setTimeout(()=>{closeModal('importModal');location.reload()},1500)}).catch(err=>alert('❌ Error: '+err))}
function updatePreview(){document.getElementById('previewHeader').style.background=document.getElementById('settingHeader').value;document.getElementById('previewBtn').style.background=document.getElementById('settingPrimary').value}
function saveSettings(){const data={business_name:document.getElementById('settingName').value,logo_url:document.getElementById('settingLogo').value,primary_color:document.getElementById('settingPrimary').value,header_color:document.getElementById('settingHeader').value,accent_color:document.getElementById('settingAccent').value,fb_catalog_id:document.getElementById('settingFbCatalog').value,fb_access_token:document.getElementById('settingFbToken').value};fetch('/admin/api/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)}).then(r=>r.json()).then(d=>{document.getElementById('settingsResult').textContent=d.message||'সেভ হয়েছে!';if(d.success)setTimeout(()=>location.reload(),800)}).catch(err=>alert('❌ Error: '+err))}
let activePhone='';
function loadConversation(phone,name){activePhone=phone;document.querySelectorAll('.conv-row').forEach(el=>el.classList.remove('active'));const row=document.getElementById('conv-'+phone);if(row)row.classList.add('active');fetch('/admin/api/conversations/'+encodeURIComponent(phone)).then(r=>r.json()).then(d=>{let html='';d.messages.forEach(m=>{const cls=m.direction==='out'?'msg-out':'msg-in';html+='<div style="max-width:70%;padding:10px 14px;border-radius:14px;margin-bottom:8px;font-size:14px;line-height:1.5;'+(m.direction==='out'?'background:#667eea;color:#fff;align-self:flex-end;border-bottom-right-radius:4px;':'background:#fff;align-self:flex-start;border-bottom-left-radius:4px;box-shadow:0 1px 2px rgba(0,0,0,.08)')+'">'+escapeHtml(m.content)+'<div style="font-size:11px;opacity:.7;margin-top:4px;text-align:right">'+m.time+'</div></div>';});document.getElementById('chatBox').innerHTML='<div style="display:flex;flex-direction:column;gap:8px;height:100%">'+html+'</div>';setTimeout(()=>{const box=document.getElementById('chatBox');box.scrollTop=box.scrollHeight},50);}).catch(err=>console.error(err))}
function sendReply(){const text=document.getElementById('replyText').value.trim();if(!text||!activePhone)return;fetch('/admin/api/reply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({phone:activePhone,message:text})}).then(r=>r.json()).then(d=>{if(d.success){document.getElementById('replyText').value='';loadConversation(activePhone,'')}else{alert(d.error||'Failed')}}).catch(err=>alert('❌ Error: '+err))}
function filterMessages(){const q=document.getElementById('msgSearch').value.toLowerCase();document.querySelectorAll('.conv-row').forEach(el=>{const t=el.textContent.toLowerCase();el.style.display=t.includes(q)?'block':'none'})}
function escapeHtml(t){const d=document.createElement('div');d.textContent=t;return d.innerHTML}
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
        users_list = db_query("SELECT * FROM users ORDER BY last_active DESC", fetchall=True) or []
        recent_orders = db_query("SELECT * FROM orders ORDER BY created_at DESC LIMIT 5", fetchall=True) or []
        if not products:
            db_query("INSERT INTO products (name, price, description, stock, image_url) VALUES (?, ?, ?, ?, ?)", ("পেস্টেল কুর্তি", 1299, "সুন্দর পেস্টেল কালার কুর্তি, প্রিমিয়াম কোয়ালিটি ফেব্রিক", 15, ""), commit=True)
            products = db_query("SELECT * FROM products ORDER BY id DESC", fetchall=True) or []
        html = ADMIN_HTML
        html = html.replace("{{ settings.business_name }}", settings.get("business_name", "Dhaka Exclusive"))
        html = html.replace("{{ settings.header_color }}", settings.get("header_color", "#1f2937"))
        html = html.replace("{{ settings.primary_color }}", settings.get("primary_color", "#667eea"))
        html = html.replace("{{ settings.accent_color }}", settings.get("accent_color", "#10b981"))
        html = html.replace("{{ settings.logo_url }}", settings.get("logo_url", ""))
        html = html.replace("{{ settings.fb_catalog_id }}", settings.get("fb_catalog_id", ""))
        html = html.replace("{{ settings.fb_access_token }}", settings.get("fb_access_token", ""))
        html = html.replace("{{ stats.total_orders }}", str(stats["total_orders"]))
        html = html.replace("{{ stats.revenue }}", str(stats["revenue"]))
        html = html.replace("{{ stats.users }}", str(stats["users"]))
        html = html.replace("{{ stats.pending }}", str(stats["pending"]))
        
        # Build recent orders rows
        recent_rows = ""
        for o in recent_orders:
            bg = "#d1fae5" if o.get("status") == "delivered" else ("#fee2e2" if o.get("status") == "cancelled" else "#fef3c7")
            recent_rows += f"<tr><td>#{o['id']}</td><td>{o.get('name') or 'N/A'}</td><td>{o['phone']}</td><td>৳{o['total']}</td><td><span style='padding:4px 8px;border-radius:6px;background:{bg};font-size:12px'>{o['status']}</span></td></tr>"
        html = html.replace("{% for o in recent_orders %}", "")
        html = html.replace("{% endfor %}", "")
        # Remove the template loop line and insert rows
        import re
        html = re.sub(r'<tr>.*recent_orders.*?</tr>', recent_rows, html, flags=re.DOTALL)
        
        # Build products rows
        prod_rows = ""
        for p in products:
            name_esc = (p.get('name') or '').replace("'", "\\'")
            desc_esc = (p.get('description') or '').replace("'", "\\'")
            pid = p['id']
            prod_rows += "<tr><td>#" + str(pid) + "</td><td>" + str(p['name']) + "</td><td>৳" + str(p['price']) + "</td><td>" + str(p['stock']) + "</td><td><button class='btn btn-sm btn-success' onclick=\"editProduct(" + str(pid) + ",'" + name_esc + "'," + str(p['price']) + "," + str(p['stock']) + ",'" + desc_esc + "')\">✏️</button> <button class='btn btn-sm btn-danger' onclick='deleteProduct(" + str(pid) + ")'>🗑️</button></td></tr>"
        html = html.replace("{% for p in products %}", "")
        html = html.replace("{% endfor %}", "")
        html = re.sub(r'<tr>.*products.*?</tr>', prod_rows, html, flags=re.DOTALL)
        
        # Build orders rows
        order_rows = ""
        for o in orders:
            oid = o['id']
            status = o.get('status', 'pending')
            sel = lambda v: "selected" if status == v else ""
            order_rows += "<tr data-phone='" + str(o['phone']) + "' data-name='" + str(o.get('name') or '') + "'><td>#" + str(oid) + "</td><td>" + str(o.get('name') or 'N/A') + "</td><td>" + str(o['phone']) + "</td><td>" + str(o.get('address') or 'N/A') + "</td><td>৳" + str(o['total']) + "</td><td><select onchange=\"updateOrderStatus(" + str(oid) + ",this.value)\" style='padding:4px 8px;border-radius:6px;border:1px solid #d1d5db'><option value='pending' " + sel('pending') + ">Pending</option><option value='created' " + sel('created') + ">Created</option><option value='confirmed' " + sel('confirmed') + ">Confirmed</option><option value='shipped' " + sel('shipped') + ">Shipped</option><option value='delivered' " + sel('delivered') + ">Delivered</option><option value='cancelled' " + sel('cancelled') + ">Cancelled</option></select></td><td><button class='btn btn-sm btn-danger' onclick='deleteOrder(" + str(oid) + ")'>🗑️</button></td></tr>"
        html = html.replace("{% for o in orders %}", "")
        html = html.replace("{% endfor %}", "")
        html = re.sub(r'<tr>.*orders.*?</tr>', order_rows, html, flags=re.DOTALL)
        
        # Build users rows
        user_rows = ""
        for u in users_list:
            user_rows += f"<tr><td>{u['phone']}</td><td>{u.get('name') or 'N/A'}</td><td>{u['total_orders']}</td><td>৳{u['total_spent']}</td></tr>"
        html = html.replace("{% for u in users %}", "")
        html = html.replace("{% endfor %}", "")
        html = re.sub(r'<tr>.*users.*?</tr>', user_rows, html, flags=re.DOTALL)
        
        # Build conversation list for Messages tab
        conv_rows = ""
        msg_rows = db_query("""
            SELECT from_number as phone, content, msg_type, created_at,
                   ROW_NUMBER() OVER (PARTITION BY from_number ORDER BY created_at DESC) as rn
            FROM messages ORDER BY created_at DESC
        """, fetchall=True) or []
        seen_conv = set()
        for r in msg_rows:
            phone = r["phone"]
            if phone in seen_conv:
                continue
            seen_conv.add(phone)
            user = db_query("SELECT name FROM users WHERE phone = ?", (phone,), fetchone=True)
            name = user["name"] if user else None
            display = name or phone
            last_msg = (r["content"] or "")[:40]
            last_time = r["created_at"][11:16] if r["created_at"] else ""
            conv_rows += "<div class='conv-row' id='conv-" + phone + "' onclick=\"loadConversation('" + phone + "','" + (name or "") + "')\" style='padding:12px 16px;border-bottom:1px solid #f3f4f6;cursor:pointer;transition:.2s'><div style='font-weight:600;font-size:14px;color:#111827'>" + display + "</div><div style='font-size:12px;color:#6b7280;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>" + last_msg + "</div><div style='font-size:11px;color:#9ca3af;margin-top:2px'>" + last_time + "</div></div>"
        if not conv_rows:
            conv_rows = "<div style='padding:20px;text-align:center;color:#9ca3af'>কোনো মেসেজ নেই</div>"
        html = html.replace("{{CONVERSATION_LIST}}", conv_rows)
        
        return html
    except Exception as e:
        logger.exception("Admin dashboard error")
        return f"<h3>Admin Panel Error:</h3><pre>{str(e)}</pre>", 500

@app.route("/admin/api/product", methods=["POST"])
@login_required
def admin_add_product():
    data = request.get_json() or {}
    pid = data.get("id")
    name = data.get("name", "").strip()
    price = data.get("price", 0)
    stock = data.get("stock", 0)
    desc = data.get("description", "").strip()
    image = data.get("image_url", "").strip()
    if not name or price <= 0: return jsonify({"error": "Invalid data"}), 400
    if pid:
        db_query("UPDATE products SET name=?, price=?, stock=?, description=?, image_url=? WHERE id=?", (name, price, stock, desc, image, pid), commit=True)
        return jsonify({"success": True, "message": "Updated"})
    db_query("INSERT INTO products (name, price, stock, description, image_url) VALUES (?, ?, ?, ?, ?)", (name, price, stock, desc, image), commit=True)
    return jsonify({"success": True, "message": "Added"})

@app.route("/admin/api/product/<int:pid>", methods=["DELETE"])
@login_required
def admin_delete_product(pid):
    db_query("DELETE FROM products WHERE id = ?", (pid,), commit=True)
    return jsonify({"success": True})

@app.route("/admin/api/order/<int:oid>/status", methods=["POST"])
@login_required
def admin_update_order_status(oid):
    data = request.get_json() or {}
    status = data.get("status", "").strip()
    if status: db_query("UPDATE orders SET status = ? WHERE id = ?", (status, oid), commit=True)
    return jsonify({"success": True, "message": "Status updated"})

@app.route("/admin/api/order/<int:oid>", methods=["DELETE"])
@login_required
def admin_delete_order(oid):
    db_query("DELETE FROM orders WHERE id = ?", (oid,), commit=True)
    return jsonify({"success": True})

@app.route("/admin/api/broadcast", methods=["POST"])
@login_required
def admin_broadcast():
    data = request.get_json() or {}
    msg = data.get("message", "").strip()
    if not msg: return jsonify({"error": "Empty message"}), 400
    users = db_query("SELECT phone FROM users", fetchall=True) or []
    sent = 0
    for u in users:
        try: send_text(u["phone"], msg); sent += 1
        except: pass
    return jsonify({"success": True, "message": f"{sent} জনকে পাঠানো হয়েছে"})

@app.route("/admin/api/settings", methods=["POST"])
@login_required
def admin_save_settings():
    data = request.get_json() or {}
    for key in ["business_name", "logo_url", "primary_color", "header_color", "accent_color", "sidebar_color", "fb_catalog_id", "fb_access_token"]:
        if key in data: set_setting(key, data[key])
    return jsonify({"success": True, "message": "Settings saved! Reload to see changes."})

@app.route("/admin/api/settings", methods=["GET"])
@login_required
def admin_get_settings():
    return jsonify(get_all_settings())

@app.route("/admin/api/products/import", methods=["POST"])
@login_required
def admin_bulk_import():
    data = request.get_json() or {}
    csv_text = data.get("csv", "").strip()
    if not csv_text: return jsonify({"error": "Empty CSV"}), 400
    lines = csv_text.splitlines()
    if not lines: return jsonify({"error": "No lines"}), 400
    added = 0; skipped = 0
    for line in lines:
        line = line.strip()
        if not line: continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2: skipped += 1; continue
        name = parts[0]
        try: price = int(parts[1])
        except: skipped += 1; continue
        stock = int(parts[2]) if len(parts) > 2 and parts[2].strip() else 10
        desc = parts[3] if len(parts) > 3 else ""
        image = parts[4] if len(parts) > 4 else ""
        try:
            db_query("INSERT INTO products (name, price, stock, description, image_url) VALUES (?, ?, ?, ?, ?)", (name, price, stock, desc, image), commit=True)
            added += 1
        except: skipped += 1
    return jsonify({"success": True, "added": added, "skipped": skipped, "message": f"{added} প্রোডাক্ট যোগ হয়েছে, {skipped} স্কিপ"})

@app.route("/admin/api/catalog/sync", methods=["POST"])
@login_required
def admin_sync_catalog():
    catalog_id = get_setting("fb_catalog_id", "")
    access_token = get_setting("fb_access_token", "")
    if not catalog_id or not access_token: return jsonify({"error": "Facebook Catalog ID বা Access Token সেটিংসে যোগ করুন"}), 400
    fb_products, error = fetch_facebook_catalog(catalog_id, access_token)
    if error: return jsonify({"error": error}), 400
    added = 0; updated = 0
    for item in fb_products:
        name = item.get("name", "").strip()
        price = parse_fb_price(item.get("price"))
        desc = item.get("description", "")
        image = item.get("image_url", "") or item.get("imageUrl", "")
        avail = item.get("availability", "")
        stock = 50 if avail and "in_stock" in avail.lower() else 0
        if not name or price <= 0: continue
        existing = db_query("SELECT id FROM products WHERE name = ?", (name,), fetchone=True)
        if existing:
            db_query("UPDATE products SET price=?, stock=?, description=?, image_url=? WHERE id=?", (price, stock, desc, image, existing["id"]), commit=True)
            updated += 1
        else:
            db_query("INSERT INTO products (name, price, stock, description, image_url) VALUES (?, ?, ?, ?, ?)", (name, price, stock, desc, image), commit=True)
            added += 1
    return jsonify({"success": True, "added": added, "updated": updated, "message": f"✅ {added} নতুন + {updated} আপডেট = মোট {added+updated} প্রোডাক্ট সিঙ্ক হয়েছে!"})


# =====================================================================
# 15. ADMIN API — CONVERSATIONS & REPLY
# =====================================================================

@app.route("/admin/api/conversations/<phone>", methods=["GET"])
@login_required
def admin_get_conversation(phone):
    try:
        msgs = db_query("SELECT * FROM messages WHERE from_number = ? ORDER BY created_at ASC", (phone,), fetchall=True) or []
        messages = []
        for m in msgs:
            messages.append({
                "content": m["content"] or "",
                "direction": "out" if (m["msg_type"] == "out" or m.get("direction") == "out") else "in",
                "time": m["created_at"][11:16] if m["created_at"] else ""
            })
        return jsonify({"success": True, "messages": messages})
    except Exception as e:
        logger.exception("Conversation API error")
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
        logger.exception("Admin reply error")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
