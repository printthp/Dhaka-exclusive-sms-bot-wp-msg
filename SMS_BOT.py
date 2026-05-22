import os
import sys
import json
import sqlite3
import logging
import hmac
import hashlib
import time
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string, redirect, url_for
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

DB_FILE = "bot_v6_enterprise.db"
db_lock = Lock()

def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        tables = [
            "CREATE TABLE IF NOT EXISTS messages (msg_id TEXT PRIMARY KEY, from_number TEXT, content TEXT, msg_type TEXT DEFAULT 'text', direction TEXT DEFAULT 'inbound', agent_id TEXT DEFAULT 'system', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS sessions (phone TEXT PRIMARY KEY, state TEXT DEFAULT 'idle', context TEXT DEFAULT '{}', last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP, recovered INTEGER DEFAULT 0)",
            "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, name TEXT, address TEXT, city_id INTEGER, zone_id INTEGER, area_id INTEGER, product_id INTEGER, quantity INTEGER DEFAULT 1, total INTEGER, delivery_fee INTEGER, pathao_consignment_id TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, fb_product_id TEXT UNIQUE, name TEXT, price INTEGER, description TEXT, stock INTEGER DEFAULT 10, active INTEGER DEFAULT 1, image_url TEXT DEFAULT '')",
            "CREATE TABLE IF NOT EXISTS users (phone TEXT PRIMARY KEY, name TEXT, language TEXT DEFAULT 'bn', total_spent INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS call_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, name TEXT, reason TEXT, status TEXT DEFAULT 'pending', assigned_agent TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS staff (username TEXT PRIMARY KEY, password TEXT, role TEXT DEFAULT 'support', active INTEGER DEFAULT 1)",
            "CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, phone TEXT, rating INTEGER, comment TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ]
        for t in tables:
            c.execute(t)
            
        defaults = [
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
            
            # FACEBOOK CATALOGUE CONFIGS
            ("fb_catalogue_id", "4177718442481756"),
            ("fb_access_token", "EAAfHnOKpOIsBRmkGWKMiJhvZAEseZC0r6Ca6aZBckd2XZCdZBLb9uBfZAiipiBdSAseuotBW7v8BKTvVgYUZBX5PzcgbEZAyJoV08kxc1d3CTZA4UmpJbK5dZCC4WZBzz3eUZBWfV8AaIgh6ThfuyfySzj6MiSNK26EnY68RrOtHj9CDSNLyyJyhp9YXO2FQxGCoPyZByngZDZD"),
            
            ("pathao_base_url", "https://api-hermes.pathao.com"),
        
            ("delivery_inside_dhaka", "80"),
            ("delivery_outside_dhaka", "130"),
            ("invoice_footer", "আমাদের সাথে থাকার জন্য ধন্যবাদ!")
        ]
        for k, v in defaults:
            c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
            
        c.execute("INSERT OR IGNORE INTO staff (username, password, role) VALUES ('admin', 'admin123', 'admin')")
        conn.commit()
        conn.close()

init_db()

def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        try:
            c.execute(query, params)
            if commit: conn.commit(); return True
            if fetchone: row = c.fetchone(); return dict(row) if row else None
            if fetchall: rows = c.fetchall(); return [dict(r) for r in rows]
            return None
        except Exception as e:
            logger.error(f"DB Error: {e} | Query: {query}")
            raise
        finally:
            conn.close()

def get_all_settings():
    rows = db_query("SELECT key, value FROM settings", fetchall=True) or []
    return {r["key"]: r["value"] for r in rows}

def check_auth(username, password):
    user = db_query("SELECT * FROM staff WHERE username = ? AND password = ? AND active = 1", (username, password), fetchone=True)
    return user is not None

def requires_auth(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return ('Unauthorized access', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})
        return f(*args, **kwargs)
    return decorated

# =====================================================================
# FACEBOOK CATALOGUE AUTO-SYNC ENGINE (AUTOMATION)
# =====================================================================
def sync_facebook_catalogue():
    s = get_all_settings()
    cat_id = s.get("fb_catalogue_id")
    token = s.get("fb_access_token")
    
    if not cat_id or not token:
        logger.warning("Facebook Catalogue ID or Access Token is missing in System Config. Skipping Sync.")
        return False, "Credentials missing"

    url = f"https://graph.facebook.com/v21.0/{cat_id}/products"
    params = {
        "fields": "id,name,price,description,image_url",
        "access_token": token,
        "limit": 100
    }
    
    try:
        r = requests.get(url, params=params, timeout=15)
        res = r.json()
        
        if "data" not in res:
            logger.error(f"FB API Error: {res}")
            return False, res.get("error", {}).get("message", "Unknown FB Error")
            
        products_fetched = res["data"]
        sync_count = 0
        
        for item in products_fetched:
            fb_id = item.get("id")
            name = item.get("name")
            desc = item.get("description", "No description provided")
            img_url = item.get("image_url", "https://placehold.co/400")
            
            # Extract clean integer price (e.g., "BDT 1,450.00" -> 1450)
            raw_price = item.get("price", "0")
            price = 0
            try:
                digits = "".join([c for c in raw_price if c.isdigit() or c == '.'])
                price = int(float(digits)) if digits else 0
            except:
                price = 0
                
            # Upsert into database based on unique fb_product_id
            db_query('''
                INSERT INTO products (fb_product_id, name, price, description, image_url, stock, active)
                VALUES (?, ?, ?, ?, ?, 10, 1)
                ON CONFLICT(fb_product_id) DO UPDATE SET
                    name = excluded.name,
                    price = excluded.price,
                    description = excluded.description,
                    image_url = excluded.image_url
            ''', (fb_id, name, price, desc, img_url), commit=True)
            sync_count += 1
            
        logger.info(f"Successfully synced {sync_count} products from Facebook Catalogue.")
        return True, f"Successfully synced {sync_count} products!"
    except Exception as e:
        logger.error(f"Exception during FB Sync: {e}")
        return False, str(e)

# Background Sync Scheduler Loop (Runs every 1 hour)
def facebook_sync_cron():
    while True:
        try:
            time.sleep(3600) # 1 hour
            sync_facebook_catalogue()
        except Exception as e:
            logger.error(f"Cron Sync Error: {e}")

Thread(target=facebook_sync_cron, daemon=True).start()

# =====================================================================
# DYNAMIC CART RECOVERY LOOP
# =====================================================================
def cart_recovery_scheduler():
    while True:
        try:
            time.sleep(1800)
            one_hour_ago = (datetime.now() - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
            abandoned_sessions = db_query(
                "SELECT * FROM sessions WHERE state NOT IN ('idle', 'awaiting_confirmation') AND last_active < ? AND recovered = 0",
                (one_hour_ago,), fetchall=True
            ) or []
            
            for sess in abandoned_sessions:
                phone = sess["phone"]
                ctx = json.loads(sess["context"])
                if "name" in ctx:
                    msg = f"👋 হ্যালো! আপনার কার্টে থাকা '{ctx['name']}' অর্ডারটি কিন্তু এখনো অসম্পূর্ণ রয়ে গেছে। স্টক শেষ হওয়ার আগে অর্ডারটি কনফার্ম করতে চাইলে ঝটপট ইনবক্সে 'অর্ডার' লিখে...🛒"
                    send_whatsapp(phone, "text", msg)
                    db_query("UPDATE sessions SET recovered = 1 WHERE phone = ?", (phone,), commit=True)
        except Exception as e:
            logger.error(f"Cart Recovery Error: {e}")

Thread(target=cart_recovery_scheduler, daemon=True).start()

# =====================================================================
# PATHAO GATEWAY
# =====================================================================
def get_pathao_token():
    s = get_all_settings()
    cid = s.get("pathao_client_id")
    csec = s.get("pathao_client_secret")
    email = s.get("pathao_merchant_email")
    pwd = s.get("pathao_merchant_password")
    base = s.get("pathao_base_url", "https://api-hermes.pathao.com")
    if not all([cid, csec, email, pwd]): return None, "Credentials missing"
    try:
        r = requests.post(f"{base}/aladdin/api/v1/issue-token", json={"client_id": cid, "client_secret": csec, "username": email, "password": pwd, "grant_type": "password"}, headers={"content-type": "application/json"}, timeout=10)
        d = r.json()
        token = d.get("token") or d.get("access_token") or d.get("data", {}).get("token")
        return (str(token), None) if token else (None, "Token generation failed")
    except Exception as e: return None, str(e)

def get_pathao_data(endpoint):
    token, _ = get_pathao_token()
    if not token: return []
    try:
        base = get_all_settings().get("pathao_base_url", "https://api-hermes.pathao.com")
        r = requests.get(f"{base}{endpoint}", headers={"authorization": f"Bearer {token}", "accept": "application/json"}, timeout=10)
        return r.json().get("data", {}).get("data", [])
    except: return []

def create_pathao_order(name, phone, address, city_id, zone_id, area_id, item_desc, cod_amount):
    token, err = get_pathao_token()
    if not token: return False, err
    s = get_all_settings()
    base = s.get("pathao_base_url", "https://api-hermes.pathao.com")
    store = s.get("pathao_store_id")
    try:
        payload = {"store_id": int(store) if store else 0, "recipient_name": name, "recipient_phone": phone, "recipient_address": address, "recipient_city": int(city_id), "recipient_zone": int(zone_id), "recipient_area": int(area_id), "delivery_type": 48, "item_type": 2, "special_instruction": "WhatsApp Dynamic Auto", "item_quantity": 1, "amount_to_collect": int(cod_amount), "item_description": item_desc}
        r = requests.post(f"{base}/aladdin/api/v1/orders", json=payload, headers={"authorization": f"Bearer {token}", "content-type": "application/json"}, timeout=15)
        d = r.json()
        if r.status_code == 200 and d.get("status") == 200: return True, d.get("data", {}).get("consignment_id")
        return False, d.get("message", r.text)
    except Exception as e: return False, str(e)

# =====================================================================
# WHATSAPP ENGINE & DYNAMIC GEMINI AI
# =====================================================================
def send_whatsapp(to, payload_type, content, extra=None, agent="system"):
    s = get_all_settings()
    token = s.get("permanent_token")
    phone_id = s.get("phone_number_id")
    if not token or not phone_id: return False
    
    url = f"https://graph.facebook.com/v21.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"messaging_product": "whatsapp", "to": to, "type": payload_type}
    if payload_type == "text": body["text"] = {"body": content}
    elif payload_type == "image": body["image"] = {"link": content, "caption": extra or ""}
    elif payload_type == "interactive": body["interactive"] = content
    try:
        r = requests.post(url, json=body, headers=headers, timeout=10)
        if r.status_code in [200, 201]:
            db_query("INSERT INTO messages (msg_id, from_number, content, msg_type, direction, agent_id) VALUES (?, ?, ?, ?, 'outbound', ?)", 
                     (r.json().get("messages",[{}])[0].get("id", "out"), to, str(content), payload_type, agent), commit=True)
            return True
        return False
    except: return False

def get_ai_answer(user_query):
    s = get_all_settings()
    key = s.get("gemini_key")
    if not key: return "আমাদের প্রতিনিধি খুব দ্রুত আপনার সাথে যোগাযোগ করবেন।"
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key)
        p_rows = db_query("SELECT * FROM products WHERE active = 1 AND stock > 0", fetchall=True) or []
        catalog = "\n".join([f"- {p['name']}: {p['price']}৳ ({p['description']})" for p in p_rows])
        si = f"You are the senior AI coordinator for '{s.get('business_name')}'. Be professional and reply in Bengali. Catalog:\n{catalog}"
        cfg = types.GenerateContentConfig(system_instruction=si, temperature=0.3, max_output_tokens=300)
        return client.models.generate_content(model="gemini-2.5-flash", contents=user_query, config=cfg).text
    except: return "আপনার মেসেজটি সেভ করা হয়েছে। সাপোর্ট এক্সিকিউটিভ দ্রুত উত্তর দিচ্ছেন।"

# =====================================================================
# INBOUND ENGINE (STATE MACHINE)
# =====================================================================
def process_webhook_async(msg, from_number):
    msg_id = msg.get("id")
    if db_query("SELECT 1 FROM messages WHERE msg_id = ?", (msg_id,), fetchone=True): return
    msg_type = msg.get("type", "text")
    body_text = msg.get("text", {}).get("body", "").strip() if msg_type == "text" else ""
    if msg_type == "interactive":
        int_type = msg.get("interactive", {}).get("type")
        if int_type == "list_reply": body_text = msg["interactive"]["list_reply"]["id"]
        elif int_type == "button_reply": body_text = msg["interactive"]["button_reply"]["id"]
    
    db_query("INSERT INTO messages (msg_id, from_number, content, msg_type, direction) VALUES (?, ?, ?, ?, 'inbound')", (msg_id, from_number, body_text if body_text else f"[{msg_type}]", msg_type), commit=True)
    db_query("INSERT OR IGNORE INTO users (phone, name) VALUES (?, 'Customer')", (from_number,), commit=True)
    db_query("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE phone = ?", (from_number,), commit=True)

    if body_text.startswith("feed_"):
        parts = body_text.split("_")
        oid, rating = int(parts[1]), int(parts[2])
        db_query("INSERT INTO feedback (order_id, phone, rating) VALUES (?, ?, ?)", (oid, from_number, rating), commit=True)
        send_whatsapp(from_number, "text", "❤️ ফিডব্যাক দেওয়ার জন্য অনেক ধন্যবাদ!")
        return

    sess = db_query("SELECT * FROM sessions WHERE phone = ?", (from_number,), fetchone=True)
    state = sess["state"] if sess else "idle"
    ctx = json.loads(sess["context"]) if sess and sess.get("context") else {}

    if state == "idle" and any(k in body_text.lower() for k in ["কিনব", "অর্ডার", "buy", "order"]):
        products = db_query("SELECT * FROM products WHERE active = 1 AND stock > 0 LIMIT 10", fetchall=True) or []
        if not products:
            send_whatsapp(from_number, "text", "দুঃখিত, আমাদের স্টক এই মুহূর্তে খালি।")
            return
        rows = [{"id": f"p_{p['id']}", "title": p['name'][:24], "description": f"{p['price']}৳"} for p in products]
        menu = {"type": "list", "body": {"text": "আমাদের ক্যাটালগ থেকে প্রোডাক্ট সিলেক্ট করুন:"}, "action": {"button": "প্রোডাক্টস লিস্ট", "sections": [{"title": "চলতি স্টক", "rows": rows}]}}
        db_query("INSERT INTO sessions (phone, state, context, recovered) VALUES (?, 'selecting_product', '{}', 0) ON CONFLICT(phone) DO UPDATE SET state='selecting_product', context='{}', recovered=0", (from_number,), commit=True)
        send_whatsapp(from_number, "interactive", menu)
        return

    if state == "selecting_product" and body_text.startswith("p_"):
        pid = int(body_text.split("_")[1])
        p = db_query("SELECT * FROM products WHERE id = ?", (pid,), fetchone=True)
        if p:
            ctx = {"product_id": pid, "name": p["name"], "price": p["price"]}
            btns = {"type": "button", "body": {"text": f"🔹 {p['name']}\n💰 মূল্য: {p['price']}৳\n\nকত পিস অর্ডার করতে চান?"}, "action": {"buttons": [{"type": "reply", "reply": {"id": "q_1", "title": "১ পিস"}}, {"type": "reply", "reply": {"id": "q_2", "title": "২ পিস"}}]}}
            db_query("UPDATE sessions SET state='selecting_qty', context=? WHERE phone=?", (json.dumps(ctx), from_number), commit=True)
            if p.get("image_url"): send_whatsapp(from_number, "image", p["image_url"], p["name"])
            send_whatsapp(from_number, "interactive", btns)
            return

    if state == "selecting_qty" and body_text.startswith("q_"):
        ctx["quantity"] = int(body_text.split("_")[1])
        ctx["subtotal"] = ctx["price"] * ctx["quantity"]
        db_query("UPDATE sessions SET state='awaiting_name', context=? WHERE phone=?", (json.dumps(ctx), from_number), commit=True)
        send_whatsapp(from_number, "text", "📝 আপনার নাম টাইপ করুন:")
        return

    if state == "awaiting_name":
        ctx["cust_name"] = body_text
        db_query("UPDATE sessions SET state='awaiting_address', context=? WHERE phone=?", (json.dumps(ctx), from_number), commit=True)
        send_whatsapp(from_number, "text", "📍 ডেলিভারির সম্পূর্ণ ঠিকানাটি লিখুন:")
        return

    if state == "awaiting_address":
        ctx["address"] = body_text
        s = get_all_settings()
        ctx["city_id"], ctx["zone_id"], ctx["area_id"] = 1, 1, 1
        ctx["delivery_fee"] = int(s.get("delivery_inside_dhaka", 60))
        confirm_order_final(from_number, ctx)
        return

    if state == "awaiting_confirmation":
        if body_text == "conf_yes":
            total_cod = ctx["subtotal"] + ctx["delivery_fee"]
            success, res = create_pathao_order(ctx["cust_name"], from_number, ctx["address"], ctx["city_id"], ctx["zone_id"], ctx["area_id"], ctx["name"], total_cod)
            consignment = str(res) if success else "MANUAL_HOLD"
            
            db_query("INSERT INTO orders (phone, name, address, city_id, zone_id, area_id, product_id, quantity, total, delivery_fee, pathao_consignment_id, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved')", 
                     (from_number, ctx["cust_name"], ctx["address"], ctx["city_id"], ctx["zone_id"], ctx["area_id"], ctx["product_id"], ctx["quantity"], total_cod, ctx["delivery_fee"], consignment), commit=True)
            
            order_id = db_query("SELECT last_insert_rowid() as id", fetchone=True)["id"]
            invoice_url = f"{request.host_url}invoice/{order_id}"
            send_whatsapp(from_number, "text", f"🎉 অর্ডার কনফার্ম হয়েছে!\n🧾 মেমো লিঙ্ক: {invoice_url}")
        db_query("UPDATE sessions SET state='idle', context='{}' WHERE phone=?", (from_number,), commit=True)
        return

    ai_msg = get_ai_answer(body_text)
    send_whatsapp(from_number, "text", ai_msg)

def confirm_order_final(from_number, ctx):
    total = ctx["subtotal"] + ctx["delivery_fee"]
    summary = f"🛒 অর্ডারের বিবরণী:\n\n🛍️ আইটেম: {ctx['name']}\n🔢 পরিমাণ: {ctx['quantity']} টি\n💵 সর্বমোট বিল: {total}৳\n\nসব ঠিক থাকলে কনফার্ম করুন:"
    btns = {"type": "button", "body": {"text": summary}, "action": {"buttons": [{"type": "reply", "reply": {"id": "conf_yes", "title": "কনফার্ম করুন 👍"}}, {"type": "reply", "reply": {"id": "conf_no", "title": "বাতিল করুন ❌"}}]}}
    db_query("UPDATE sessions SET state='awaiting_confirmation', context=? WHERE phone=?", (json.dumps(ctx), from_number), commit=True)
    send_whatsapp(from_number, "interactive", btns)

# =====================================================================
# INVOICE HTML
# =====================================================================
@app.route("/invoice/<int:order_id>")
def view_invoice(order_id):
    order = db_query("SELECT * FROM orders WHERE id = ?", (order_id,), fetchone=True)
    if not order: return "Invoice Not Found", 404
    s = get_all_settings()
    return f"<h1>{s.get('business_name')} Invoice #{order['id']}</h1><p>Amount: {order['total']}৳</p>"

# =====================================================================
# MASTER CONTROL PANEL HTML (WITH FB SYNC TRIGGER BUTTON)
# =====================================================================
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8"><title>Master Config Dynamic Desk</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
</head>
<body class="bg-slate-50 flex min-h-screen font-sans">
    
    <!-- Sidebar -->
    <div class="w-64 bg-slate-900 text-white flex flex-col shadow-xl">
        <div class="p-6 border-b border-slate-800 font-black text-xl text-indigo-400">{{ settings.get('business_name') }}</div>
        <nav class="flex-1 p-4 space-y-1">
            <a href="#orders" onclick="switchTab('orders')" class="tab-btn flex items-center gap-3 px-4 py-3 rounded-xl bg-indigo-600 text-white font-bold"><i class="fa-solid fa-basket-shopping"></i> Orders Panel</a>
            <a href="#products" onclick="switchTab('products')" class="tab-btn flex items-center gap-3 px-4 py-3 rounded-xl text-slate-400 hover:bg-slate-800 hover:text-white"><i class="fa-solid fa-boxes-stacked"></i> Inventory (FB Catalogue)</a>
            <a href="#config" onclick="switchTab('config')" class="tab-btn flex items-center gap-3 px-4 py-3 rounded-xl text-slate-400 hover:bg-slate-800 hover:text-white"><i class="fa-solid fa-sliders"></i> System Config Gateway</a>
        </nav>
    </div>

    <!-- Main Board -->
    <div class="flex-1 p-8 overflow-y-auto max-h-screen">
        
        <!-- MESSAGES RETURNING FLASH NOTIFICATIONS -->
        {% if msg %}
        <div class="mb-4 p-4 bg-emerald-100 text-emerald-800 font-bold rounded-xl text-sm"><i class="fa-solid fa-circle-check mr-2"></i>{{ msg }}</div>
        {% endif %}

        <!-- TAB: ORDERS -->
        <div id="tab-orders" class="tab-content bg-white rounded-2xl border shadow-sm overflow-hidden">
            <div class="p-5 border-b font-bold text-lg text-slate-800">অর্ডার ট্র্যাকিং ম্যাট্রিক্স</div>
            <table class="w-full text-left text-sm">
                <tr class="bg-slate-50 border-b text-xs text-slate-500 uppercase"><th class="p-4">Memo ID</th><th class="p-4">Client</th><th class="p-4">COD Total</th><th class="p-4">Consignment Code</th></tr>
                {% for o in orders %}
                <tr class="border-b"><td class="p-4 font-bold">#{{ o.id }}</td><td class="p-4">{{ o.name }} ({{ o.phone }})</td><td class="p-4 font-bold text-indigo-600">{{ o.total }}৳</td><td class="p-4 font-mono text-xs">{{ o.pathao_consignment_id }}</td></tr>
                {% endfor %}
            </table>
        </div>

        <!-- TAB: INVENTORY & FB SYNC BOARD -->
        <div id="tab-products" class="tab-content hidden space-y-6">
            <div class="bg-indigo-900 text-white p-6 rounded-2xl shadow-md flex justify-between items-center">
                <div>
                    <h3 class="text-lg font-black"><i class="fa-brands fa-facebook mr-2 text-xl"></i>ফেসবুক ক্যাটালগ অটো-সিঙ্ক ড্যাশবোর্ড</h3>
                    <p class="text-xs text-indigo-200 mt-1">আপনার কমার্স ম্যানেজারের প্রোডাক্ট ডাটা অটোমেটিক বোটে আপলোড করতে নিচের বাটনে চাপুন।</p>
                </div>
                <a href="/admin/sync-facebook" class="bg-white text-indigo-900 hover:bg-indigo-50 font-black px-6 py-3 rounded-xl text-sm shadow-lg transition"><i class="fa-solid fa-rotate mr-2 animate-spin"></i> Sync Now (ফেসবুক থেকে লোড করুন)</a>
            </div>
            
            <div class="bg-white rounded-2xl border shadow-sm overflow-hidden">
                <div class="p-5 border-b font-bold text-slate-800">চলতি ইনভেন্টরি ক্যাটালগ (Synced Data)</div>
                <table class="w-full text-left text-sm">
                    <tr class="bg-slate-50 border-b text-xs text-slate-500"><th class="p-4">FB ID Reference</th><th class="p-4">Product Image</th><th class="p-4">Product Details</th><th class="p-4">Price</th></tr>
                    {% for p in products %}
                    <tr class="border-b hover:bg-slate-50">
                        <td class="p-4 font-mono text-xs text-slate-400">{{ p.fb_product_id or 'Manual' }}</td>
                        <td class="p-4"><img src="{{ p.image_url }}" class="h-10 w-10 object-cover rounded-lg border"></td>
                        <td class="p-4"><b>{{ p.name }}</b><br><span class="text-xs text-slate-400">{{ p.description[:60] }}...</span></td>
                        <td class="p-4 font-bold text-emerald-600">{{ p.price }}৳</td>
                    </tr>
                    {% endfor %}
                </table>
            </div>
        </div>

        <!-- TAB: SYSTEM SYSTEM CONFIG GATEWAY (WITH FB ADDITIONS) -->
        <div id="tab-config" class="tab-content hidden bg-white rounded-2xl border shadow-sm p-6">
            <div class="font-bold text-lg text-slate-800 mb-6 border-b pb-3"><i class="fa-solid fa-screwdriver-wrench text-indigo-500 mr-2"></i>সিস্টেম কন্ট্রোল গেটওয়ে</div>
            <form action="/admin/settings/save" method="POST" class="space-y-5">
                <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <div><label class="block text-xs font-bold text-slate-500 uppercase mb-2">Business Brand Name</label><input type="text" name="business_name" value="{{ settings.get('business_name', '') }}" class="w-full border p-3 rounded-xl text-sm"></div>
                    <div><label class="block text-xs font-bold text-slate-500 uppercase mb-2">WhatsApp Phone Number ID</label><input type="text" name="phone_number_id" value="{{ settings.get('phone_number_id', '') }}" class="w-full border p-3 rounded-xl text-sm"></div>
                    <div class="md:col-span-2"><label class="block text-xs font-bold text-slate-500 uppercase mb-2">WhatsApp Permanent Token</label><input type="text" name="permanent_token" value="{{ settings.get('permanent_token', '') }}" class="w-full border p-3 rounded-xl text-sm"></div>
                    
                    <!-- FACEBOOK CATALOG AUTOMATION CREDENTIALS HERE -->
                    <div class="p-4 bg-blue-50 border border-blue-200 rounded-xl md:col-span-2 grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div class="md:col-span-2 font-bold text-blue-900 text-xs uppercase tracking-wider"><i class="fa-brands fa-facebook-f mr-2"></i>Facebook Catalog Auto Upload Access Config</div>
                        <div><label class="block text-xs font-semibold text-slate-600 mb-1">Facebook Catalogue ID</label><input type="text" name="fb_catalogue_id" value="{{ settings.get('fb_catalogue_id', '') }}" placeholder="Enter Meta Commerce Catalog ID" class="w-full border p-2.5 rounded-xl text-sm bg-white"></div>
                        <div><label class="block text-xs font-semibold text-slate-600 mb-1">Facebook Page/User Access Token</label><input type="password" name="fb_access_token" value="{{ settings.get('fb_access_token', '') }}" placeholder="EAAZ..." class="w-full border p-2.5 rounded-xl text-sm bg-white"></div>
                    </div>

                    <div><label class="block text-xs font-bold text-slate-500 uppercase mb-2">Google Gemini Secure API Key</label><input type="password" name="gemini_key" value="{{ settings.get('gemini_key', '') }}" class="w-full border p-3 rounded-xl text-sm"></div>
                    <div><label class="block text-xs font-bold text-slate-500 uppercase mb-2">Webhook Verify Token</label><input type="text" name="verify_token" value="{{ settings.get('verify_token', '') }}" class="w-full border p-3 rounded-xl text-sm"></div>
                </div>
                <button type="submit" class="w-full bg-slate-900 text-white font-bold p-3 rounded-xl text-sm hover:bg-slate-800 transition">Apply & Save Dynamic Configuration</button>
            </form>
        </div>
    </div>

    <script>
        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            document.getElementById('tab-' + tabId).classList.remove('hidden');
            document.querySelectorAll('.tab-btn').forEach(btn => {
                btn.classList.remove('bg-indigo-600', 'font-bold', 'text-white');
                btn.classList.add('text-slate-400');
            });
            const activeBtn = Array.from(document.querySelectorAll('.tab-btn')).find(b => b.getAttribute('href') === '#' + tabId);
            if(activeBtn) {
                activeBtn.classList.remove('text-slate-400');
                activeBtn.classList.add('bg-indigo-600', 'font-bold', 'text-white');
            }
        }
        window.addEventListener('DOMContentLoaded', () => {
            const hash = window.location.hash.replace('#', '') || 'orders';
            switchTab(hash);
        });
    </script>
</body>
</html>
"""

# =====================================================================
# ENDPOINTS
# =====================================================================
@app.route("/admin", methods=["GET"])
@requires_auth
def admin_dashboard():
    orders = db_query("SELECT * FROM orders ORDER BY id DESC", fetchall=True) or []
    products = db_query("SELECT * FROM products WHERE active = 1 ORDER BY id DESC", fetchall=True) or []
    settings = get_all_settings()
    msg = request.args.get("msg", "")
    return render_template_string(ADMIN_HTML, orders=orders, products=products, settings=settings, msg=msg)

@app.route("/admin/sync-facebook")
@requires_auth
def run_manual_fb_sync():
    success, detail = sync_facebook_catalogue()
    return redirect(url_for('admin_dashboard', msg=detail) + "#products")

@app.route("/admin/settings/save", methods=["POST"])
@requires_auth
def save_settings():
    for k, v in request.form.items():
        db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v.strip()), commit=True)
    return redirect(url_for('admin_dashboard', msg="Configurations Successfully Applied") + "#config")

@app.route("/webhook", methods=["GET"])
def verify():
    s = get_all_settings()
    if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == s.get("verify_token", "my_secret_token"):
        return request.args.get("hub.challenge"), 200
    return "Invalid verification token", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}
    try:
        value = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
        if "messages" in value:
            msg = value["messages"][0]
            Thread(target=process_webhook_async, args=(msg, msg.get("from"))).start()
    except: pass
    return "EVENT_RECEIVED", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
