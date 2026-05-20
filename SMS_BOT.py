import os
import sys
import json
import re
import sqlite3
import time
import hmac
import hashlib
import logging
import threading
from datetime import datetime, timedelta, timezone
from flask import Flask, request, jsonify
from google import genai
from google.genai import types
from threading import Thread, Lock, Timer
import requests

# =====================================================================
# 🔧 ০. লগিং সেটিংস
# =====================================================================
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# =====================================================================
# ⚙️ ১. ENV কনফিগ
# =====================================================================
PERMANENT_TOKEN = os.environ.get("PERMANENT_TOKEN", "EAANtSb24BiwBRREXu8HztnpOLtamcKIvi09Qb24LiYax45S4aoYtFEVKEQZAxigfO2wbGf6RgHh51IURbQzKKrzPhkcprLxHpZBfOwxZAVCscdVOpjbapbS9sOLCIqZBM8tZAtSRRaVVYSTZBjUkkPZAQaLABSnG6cQcgQcwqZBC5I5yrB4cXgoUPDlzzn7HzUwsMAZDZD")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "1039959469208417")
GEMINI_KEY = os.environ.get("GEMINI_KEY", "AIzaSyDICBRwj4wdwmqlut_Xjf0GgvXx_Mjcc0Q")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "dhakaex0020")

# 🔐 এডমিন নম্বর লিস্ট
ADMIN_NUMBERS = ["8801717121068", "8801954080047", "8801884413951", "8801735514320"]

# মেটা ডুপ্লিকেট মেসেজ ফিল্টার এবং মেমোরি ফাইল
global_processed_messages = {}
MEMORY_FILE = "knowledge.txt"

# 🚚 পাঠাও মার্চেন্ট ক্রেডেনশিয়ালস
PATHAO_BASE_URL = "https://api-hermes.pathao.com"  
PATHAO_STORE_ID = "333358"
PATHAO_CLIENT_ID = "openOlRa7A"
PATHAO_CLIENT_SECRET = "7clJGfV1jh5njQEuR5yepVXZ9nYAjGORhNCOjgzG"
PATHAO_MERCHANT_EMAIL = "cocid1000006@gmail.com"
PATHAO_MERCHANT_PASSWORD = "trustedaA@2" 
# ব্যবসা সেটিংস
BUSINESS_NAME = os.environ.get("BUSINESS_NAME", "Dhaka Exclusive")
BUSINESS_HOURS = os.environ.get("BUSINESS_HOURS", "09:00-21:00")
CURRENCY = "৳"

client = genai.Client(api_key=GEMINI_KEY)
MODEL_NAME = "gemini-2.5-flash"

# =====================================================================
# 🗄️ ২. SQLite ডাটাবেস
# =====================================================================
DB_FILE = "bot_super.db"
db_lock = Lock()

def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        c = conn.cursor()

        # মেসেজ ও রেট লিমিট
        c.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                msg_id TEXT PRIMARY KEY,
                from_number TEXT,
                content TEXT,
                msg_type TEXT DEFAULT 'text',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ইউজার প্রোফাইল
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

        # সেশন/স্টেট
        c.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                phone TEXT PRIMARY KEY,
                state TEXT DEFAULT 'idle',
                context TEXT DEFAULT '{}',
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # অর্ডার
        c.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT,
                name TEXT,
                address TEXT,
                city_id INTEGER,
                zone_id INTEGER,
                area_id INTEGER,
                product_id INTEGER,
                quantity INTEGER DEFAULT 1,
                price INTEGER,
                delivery_charge INTEGER DEFAULT 0,
                discount INTEGER DEFAULT 0,
                total INTEGER,
                payment_method TEXT DEFAULT 'cod',
                payment_status TEXT DEFAULT 'pending',
                pathao_consignment_id TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # প্রোডাক্ট
        c.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                price INTEGER,
                description TEXT,
                image_url TEXT,
                stock INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                category TEXT DEFAULT 'general'
            )
        """)

        # কুপন
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

        # নলেজ বেস
        c.execute("""
            CREATE TABLE IF NOT EXISTS knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT DEFAULT 'general',
                content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # কার্ট
        c.execute("""
            CREATE TABLE IF NOT EXISTS carts (
                phone TEXT PRIMARY KEY,
                items TEXT DEFAULT '[]',
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        conn.close()
        logger.info("✅ Super Database initialized")

init_db()

def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    with db_lock:
        conn = sqlite3.connect(DB_FILE, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
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

# =====================================================================
# 🌐 ৩. হেলপারস
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
        # বাংলাদেশের টাইমজোন (UTC+6) ফিক্সড করা হয়েছে যেন ইন্টারন্যাশনাল সার্ভারেও কাজ করে
        tz_bd = timezone(timedelta(hours=6))
        now_bd = datetime.now(tz_bd)
        
        start_str, end_str = BUSINESS_HOURS.split("-")
        start_time = datetime.strptime(start_str, "%H:%M").time()
        end_time = datetime.strptime(end_str, "%H:%M").time()
        
        return start_time <= now_bd.time() <= end_time
    except Exception as e:
        logger.error(f"Business hours check error: {e}")
        return True

def t(msg_bn, msg_en=""):
    return msg_bn

# =====================================================================
# 🛡️ ৪. Meta Webhook Verify
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
    except Exception as e:
        logger.error(f"Sig verify error: {e}")
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
            logger.warning(f"API attempt {attempt+1} fail: {e}")
            time.sleep(2 ** attempt)
    return None

def get_pathao_token():
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
                "pending": "পেন্ডিং (অর্ডারটি রিভিউ করা হচ্ছে)",
                "picked": "কুরিয়ারের কাছে হস্তান্তরিত (Picked)",
                "in_transit": "ডেলিভারির পথে (In Transit)",
                "delivered": "সফলভাবে ডেলিভারি সম্পন্ন 🎉",
                "cancelled": "অর্ডার বাতিল",
                "returned": "অর্ডার রিটার্ন"
            }
            return status_map.get(status, f"Status: {status.upper()}")
        return "দুঃখিত, এই নম্বর/ID দিয়ে অর্ডার পাওয়া যায়নি।"
    except Exception as e:
        logger.error(f"Track error: {e}")
        return "ট্র্যাকিং তথ্য লোডে সমস্যা।"

# =====================================================================
# 📲 ৬. WhatsApp API 发送 函数
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
    except Exception as e:
        logger.error(f"Send text error: {e}")
        return False

# =====================================================================
# 🧠 ৭. জেমিনি AI ও প্রোডাক্টস
# =====================================================================
def read_knowledge():
    rows = db_query("SELECT content FROM knowledge ORDER BY created_at DESC", fetchall=True)
    if not rows:
        return "Brand: Dhaka Exclusive. Bangladesh. Premium kitchenware."
    return "\n".join([r["content"] for r in rows])

def save_knowledge(category, content):
    db_query("INSERT INTO knowledge (category, content) VALUES (?, ?)", (category, content), commit=True)

def get_products():
    return db_query("SELECT * FROM products WHERE active = 1", fetchall=True)

def add_product(name, price, description, stock=10, category="general", image_url=""):
    db_query(
        "INSERT INTO products (name, price, description, stock, category, image_url) VALUES (?, ?, ?, ?, ?, ?)",
        (name, price, description, stock, category, image_url), commit=True
    )

def format_catalog():
    products = get_products()
    if not products:
        return "কোনো প্রোডাক্ট আপডেট হয়নি।"
    lines = ["📋 *আমাদের প্রোডাক্ট:*"]
    for p in products:
        lines.append(f"\n🔹 *{p['name']}* — {p['price']}৳\n📝 {p['description']}\n📦 স্টক: {p['stock']}টি")
    return "\n".join(lines)

def get_ai_answer(user_query, session_context=None):
    try:
        saved_knowledge = read_knowledge()
        products_text = format_catalog()
        system_instruction = (
            "You are the AI sales assistant for 'Dhaka Exclusive'.\n"
            "CRITICAL:\n"
            "1. NEVER say 'নমস্কার'. ALWAYS use 'প্রিয় গ্রাহক'.\n"
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
        logger.error(f"Gemini error: {e}")
        return "দুঃখিত প্রিয় গ্রাহক, সিস্টেম ব্যস্ত। প্রতিনিধি শীঘ্রই যোগাযোগ করবেন।"

# =====================================================================
# 🧠 ৮. সেশন ও রেট লিমিট ম্যানেজমেন্ট
# =====================================================================
def get_session(phone):
    return db_query("SELECT * FROM sessions WHERE phone = ?", (phone,), fetchone=True)

def set_session(phone, state, context=None):
    ctx = json.dumps(context or {}, ensure_ascii=False)
    existing = get_session(phone)
    if existing:
        db_query(
            "UPDATE sessions SET state = ?, context = ?, last_active = CURRENT_TIMESTAMP WHERE phone = ?",
            (state, ctx, phone), commit=True
        )
    else:
        db_query(
            "INSERT INTO sessions (phone, state, context) VALUES (?, ?, ?)",
            (phone, state, ctx), commit=True
        )

def get_context(phone):
    session = get_session(phone)
    return json.loads(session["context"]) if session and session["context"] else {}

def ensure_user(phone):
    user = db_query("SELECT * FROM users WHERE phone = ?", (phone,), fetchone=True)
    if not user:
        db_query("INSERT OR IGNORE INTO users (phone) VALUES (?)", (phone,), commit=True)
    else:
        db_query("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE phone = ?", (phone,), commit=True)
    return user

def is_rate_limited(phone):
    one_min_ago = (datetime.utcnow() - timedelta(minutes=1)).isoformat()
    count = db_query(
        "SELECT COUNT(*) as cnt FROM messages WHERE from_number = ? AND created_at > ?",
        (phone, one_min_ago), fetchone=True
    )
    return count and count["cnt"] >= 10

def log_message(msg_id, phone, content, msg_type="text"):
    db_query(
        "INSERT OR IGNORE INTO messages (msg_id, from_number, content, msg_type) VALUES (?, ?, ?, ?)",
        (msg_id, phone, content, msg_type), commit=True
    )

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

def get_dashboard_stats():
    total_users = db_query("SELECT COUNT(*) as c FROM users", fetchone=True)["c"]
    total_orders = db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)["c"]
    today_orders = db_query("SELECT COUNT(*) as c FROM orders WHERE date(created_at) = date('now')", fetchone=True)["c"]
    revenue = db_query("SELECT COALESCE(SUM(total), 0) as s FROM orders WHERE status = 'delivered'", fetchone=True)["s"]
    pending = db_query("SELECT COUNT(*) as c FROM orders WHERE status = 'pending'", fetchone=True)["c"]
    return {"users": total_users, "total_orders": total_orders, "today_orders": today_orders, "revenue": revenue, "pending": pending}

# =====================================================================
# 🧠 ৯. মেইন প্রসেসর (State Machine + ফিক্সড হেল্প ব্লক)
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
        send_text(from_number, "প্রিয় গ্রাহক, অনেক মেসেজ পাঠিয়েছেন। কিছুক্ষণ অপেক্ষা করুন।")
        return

    if not is_within_business_hours():
        send_text(from_number, f"প্রিয় গ্রাহক, আমাদের কার্যক্রম সময় {BUSINESS_HOURS}। আপনার মেসেজটির উত্তর কাজের সময়ে দেওয়া হবে। 🙏")
        return

    if msg_type in ("audio", "voice"):
        send_text(from_number, "প্রিয় গ্রাহক, ভয়েস মেসেজ এখনো সাপোর্টেড নয়। অনুগ্রহ করে টেক্সটে লিখুন।")
        return

    if msg_type == "image":
        send_text(from_number, "📸 ছবি পেয়েছি! আমাদের প্রতিনিধি শীঘ্রই যাচাই করে রিপ্লাই দেবেন।")
        return

    if msg_type != "text":
        send_text(from_number, "প্রিয় গ্রাহক, আমি বর্তমানে শুধু টেক্সট বুঝতে পারি।")
        return

    user_text = msg["text"]["body"].strip()
    session = get_session(from_number)
    state = session["state"] if session else "idle"
    context = get_context(from_number)

    # ─────────────────────────────────────────
    # 🔐 অ্যাডমিন কমান্ড প্রসেসর (সম্পূর্ণ করা হয়েছে)
    # ─────────────────────────────────────────
    if user_text.lower().startswith("admin:"):
        if from_number not in ADMIN_NUMBERS:
            send_text(from_number, "দুঃখিত, এই কমান্ড শুধু অ্যাডমিনের জন্য।")
            return
        cmd = user_text[6:].strip()

        if cmd.lower().startswith("addproduct"):
            parts = [p.strip() for p in cmd.split("|")]
            if len(parts) >= 4:
                add_product(parts[1], int(parts[2]), parts[3], stock=int(parts[4]) if len(parts)>4 else 10)
                send_text(from_number, f"✅ '{parts[1]}' যোগ হয়েছে।")
            else:
                send_text(from_number, "ফরম্যাট: admin:addproduct | নাম | দাম | বর্ণনা | [স্টক]")
            return

        if cmd.lower().startswith("knowledge"):
            save_knowledge("general", cmd[9:].strip())
            send_text(from_number, "✅ নলেজ আপডেট সম্পন্ন।")
            return

        if cmd.lower().startswith("orders"):
            orders = db_query("SELECT * FROM orders ORDER BY created_at DESC LIMIT 5", fetchall=True)
            if orders:
                lines = ["📦 সর্বশেষ অর্ডারসমূহ:"]
                for o in orders:
                    lines.append(f"\n#{o['id']} | {o['name']} | {o['total']}৳ | {o['status']}")
                send_text(from_number, "\n".join(lines))
            else:
                send_text(from_number, "কোনো অর্ডার পাওয়া যায়নি।")
            return

        if cmd.lower().startswith("stats"):
            stats = get_dashboard_stats()
            send_text(from_number,
                f"📊 ড্যাশবোর্ড আপডেট:\n"
                f"👤 মোট ইউজার: {stats['users']}\n"
                f"📦 মোট অর্ডার: {stats['total_orders']}\n"
                f"📅 আজকের অর্ডার: {stats['today_orders']}\n"
                f"💰 মোট রেভেনিউ: {stats['revenue']}৳\n"
                f"⏳ পেন্ডিং অর্ডার: {stats['pending']}"
            )
            return

        if cmd.lower().startswith("broadcast"):
            message = cmd[9:].strip()
            sent, total = broadcast_message(message, exclude_admins=True)
            send_text(from_number, f"📢 ব্রডকাস্ট সম্পন্ন! {sent}/{total} জনকে পাঠানো হয়েছে।")
            return

        if cmd.lower().startswith("coupon"):
            parts = [p.strip() for p in cmd.split("|")]
            if len(parts) >= 4:
                code, val, ctype, maxuse = parts[1], int(parts[2]), parts[3], int(parts[4])
                valid = parts[5] if len(parts) > 5 else None
                disc_pct = val if ctype == "percent" else 0
                disc_amt = val if ctype == "amount" else 0
                db_query(
                    "INSERT INTO coupons (code, discount_percent, discount_amount, max_uses, valid_until) VALUES (?, ?, ?, ?, ?)",
                    (code.upper(), disc_pct, disc_amt, maxuse, valid), commit=True
                )
                send_text(from_number, f"🎫 কুপন '{code}' সফলভাবে তৈরি হয়েছে!")
            else:
                send_text(from_number, "ফরম্যাট: admin:coupon | CODE | value | percent/amount | max_uses | [YYYY-MM-DD]")
            return

        if cmd.lower().startswith("help"):
            help_text = (
                "🔧 *অ্যাডমিন কমান্ডসমূহ:*\n\n"
                "🔹 admin:addproduct | নাম | দাম | বর্ণনা | [স্টক]\n"
                "🔹 admin:knowledge [তথ্য]\n"
                "🔹 admin:orders\n"
                "🔹 admin:stats\n"
                "🔹 admin:broadcast [মেসেজ]\n"
                "🔹 admin:coupon | CODE | value | percent/amount | max_uses | [YYYY-MM-DD]\n"
                "🔹 admin:help"
            )
            send_text(from_number, help_text)
            return

        send_text(from_number, "❌ অজানা অ্যাডমিন কমান্ড। সাহায্য পেতে লিখুন 'admin:help'")
        return

    # ─────────────────────────────────────────
    # 🤖 সাধারণ কাস্টমার ও জেমিনি এআই লজিক
    # ─────────────────────────────────────────
    ai_reply = get_ai_answer(user_text, context)
    
    # পাঠাও ট্র্যাকিং ডাটা চেক
    if "||TRACK_DATA||" in ai_reply:
        match = re.search(r"\|\|TRACK_DATA\|\|({.*?})\|\|", ai_reply)
        if match:
            try:
                track_json = json.loads(match.group(1).replace("'", '"'))
                tracking_key = track_json.get("key")
                status_msg = track_pathao_order(tracking_key)
                ai_reply = re.sub(r"\|\|TRACK_DATA\|\|.*?\|\|", f"\n\n📦 *ট্র্যাকিং স্ট্যাটাস:* {status_msg}", ai_reply)
            except Exception as e:
                logger.error(f"Tracking parse error: {e}")

    # অর্ডার ডাটা ডিটেকশন ও সেভ লজিক
    if "||ORDER_DATA||" in ai_reply:
        match = re.search(r"\|\|ORDER_DATA\|\|({.*?})\|\|", ai_reply)
        if match:
            try:
                order_json = json.loads(match.group(1).replace("'", '"'))
                # ডাটাবেসে অর্ডার পেন্ডিং হিসেবে সেভ করা
                db_query(
                    "INSERT INTO orders (phone, name, address, total, status) VALUES (?, ?, ?, ?, ?)",
                    (from_number, order_json.get("name"), order_json.get("address"), 0, "pending"),
                    commit=True
                )
                ai_reply = re.sub(r"\|\|ORDER_DATA\|\|.*?\|\|", "\n\n✅ *আপনার অর্ডারটি সিস্টেমে রেকর্ড করা হয়েছে!*", ai_reply)
            except Exception as e:
                logger.error(f"Order parse error: {e}")

    send_text(from_number, ai_reply)

# =====================================================================
# 🕸️ ১০. মেটা ওয়েবহুক রুটস (Flask Routes)
# =====================================================================
@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        # মেটা ভেরিফিকেশন (Setup Step)
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode and token:
            if mode == "subscribe" and token == VERIFY_TOKEN:
                logger.info("🎯 Webhook Verified successfully by Meta!")
                return challenge, 200
            return "Forbidden", 403
        return "Not Found", 404

    if request.method == "POST":
        # মেসেজ রিসিভ করা ও সিকিউরিটি ভেরিফিকেশন
        payload = request.data
        signature = request.headers.get("X-Hub-Signature-256", "")
        
        if not verify_meta_signature(payload, signature):
            logger.warning("🔒 Invalid signature request blocked.")
            return "Unauthorized", 401

        data = request.json
        if not data:
            return "Bad Request", 400

        # মেসেজ অবজেক্ট এক্সট্রাকশন
        if "object" in data and data["object"] == "whatsapp_business_account":
            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    if "messages" in value:
                        for msg in value.get("messages", []):
                            from_number = msg.get("from")
                            # ব্যাকগ্রাউন্ড থ্রেডে মেসেজটি হ্যান্ডেল করা যাতে মেটা ৩ সেকেন্ডে ২০০ ওকে পায়
                            Thread(target=process_webhook_async, args=(msg, from_number)).start()
            return "OK", 200
        return "Not Found", 404

@app.route("/", methods=["GET"])
def index():
    return f"🚀 {BUSINESS_NAME} WhatsApp Super Bot Engine is Running Stable.", 200

# =====================================================================
# 🚀 ১১. এপ্লিকেশন স্টার্টার
# =====================================================================
if __name__ == "__main__":
    # ডেভেলপমেন্ট বা লোকাল রান করার জন্য পোর্ট ৫০০৫ সেট করা হলো
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5005)), debug=False)
