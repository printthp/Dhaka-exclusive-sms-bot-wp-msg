import os
import sys
import json
import sqlite3
import logging
import functools
import hmac
import hashlib
from datetime import datetime
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

# Environment Variables
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

DB_FILE = "bot_v5_fully_dynamic.db"
db_lock = Lock()

def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        tables = [
            "CREATE TABLE IF NOT EXISTS messages (msg_id TEXT PRIMARY KEY, from_number TEXT, content TEXT, msg_type TEXT DEFAULT 'text', direction TEXT DEFAULT 'inbound', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS sessions (phone TEXT PRIMARY KEY, state TEXT DEFAULT 'idle', context TEXT DEFAULT '{}', last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, name TEXT, address TEXT, city_id INTEGER, zone_id INTEGER, area_id INTEGER, product_id INTEGER, quantity INTEGER DEFAULT 1, total INTEGER, delivery_fee INTEGER, pathao_consignment_id TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, price INTEGER, description TEXT, stock INTEGER DEFAULT 0, active INTEGER DEFAULT 1, image_url TEXT DEFAULT '')",
            "CREATE TABLE IF NOT EXISTS users (phone TEXT PRIMARY KEY, name TEXT, language TEXT DEFAULT 'bn', last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS call_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, name TEXT, reason TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ]
        for t in tables:
            c.execute(t)
        defaults = [
            ("business_name", BUSINESS_NAME), 
            ("pathao_base_url", PATHAO_BASE_URL),
            ("delivery_inside_dhaka", "60"),
            ("delivery_outside_dhaka", "120")
        ]
        for k, v in defaults:
            c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
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
            return ('<h3>Unauthorized</h3>', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})
        return f(*args, **kwargs)
    return decorated

def get_all_settings():
    rows = db_query("SELECT key, value FROM settings", fetchall=True) or []
    return {r["key"]: r["value"] for r in rows}

# =====================================================================
# DYNAMIC PATHAO API SEARCH & INTEGRATION
# =====================================================================
def get_pathao_token():
    s = get_all_settings()
    cid = s.get("pathao_client_id") or PATHAO_CLIENT_ID
    csec = s.get("pathao_client_secret") or PATHAO_CLIENT_SECRET
    email = s.get("pathao_merchant_email") or PATHAO_MERCHANT_EMAIL
    pwd = s.get("pathao_merchant_password") or PATHAO_MERCHANT_PASSWORD
    base = s.get("pathao_base_url") or PATHAO_BASE_URL
    if not all([cid, csec, email, pwd]): return None, "Credentials missing"
    try:
        r = requests.post(f"{base}/aladdin/api/v1/issue-token", json={"client_id": cid, "client_secret": csec, "username": email, "password": pwd, "grant_type": "password"}, headers={"content-type": "application/json"}, timeout=10)
        d = r.json()
        token = d.get("token") or d.get("access_token") or d.get("data", {}).get("token")
        return (str(token), None) if token else (None, d.get("message", "Token fail"))
    except Exception as e: return None, str(e)

def get_pathao_data(endpoint):
    token, _ = get_pathao_token()
    if not token: return []
    try:
        base = get_all_settings().get("pathao_base_url") or PATHAO_BASE_URL
        r = requests.get(f"{base}{endpoint}", headers={"authorization": f"Bearer {token}", "accept": "application/json"}, timeout=10)
        return r.json().get("data", {}).get("data", [])
    except: return []

def create_pathao_order(name, phone, address, city_id, zone_id, area_id, item_desc, cod_amount):
    token, err = get_pathao_token()
    if not token: return False, err
    s = get_all_settings()
    base = s.get("pathao_base_url") or PATHAO_BASE_URL
    store = s.get("pathao_store_id") or PATHAO_STORE_ID
    try:
        payload = {"store_id": int(store) if store else 0, "recipient_name": name, "recipient_phone": format_phone(phone), "recipient_address": address, "recipient_city": int(city_id), "recipient_zone": int(zone_id), "recipient_area": int(area_id), "delivery_type": 48, "item_type": 2, "special_instruction": "WhatsApp fully dynamic bot", "item_quantity": 1, "amount_to_collect": int(cod_amount), "item_description": item_desc}
        r = requests.post(f"{base}/aladdin/api/v1/orders", json=payload, headers={"authorization": f"Bearer {token}", "content-type": "application/json"}, timeout=15)
        d = r.json()
        if r.status_code == 200 and d.get("status") == 200: return True, d.get("data", {}).get("consignment_id")
        return False, d.get("message", r.text)
    except Exception as e: return False, str(e)

# =====================================================================
# WHATSAPP API & AI CORES
# =====================================================================
def send_whatsapp(to, payload_type, content, extra=None):
    if not PERMANENT_TOKEN or not PHONE_NUMBER_ID: return False
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {PERMANENT_TOKEN}", "Content-Type": "application/json"}
    body = {"messaging_product": "whatsapp", "to": format_phone(to), "type": payload_type}
    if payload_type == "text": body["text"] = {"body": content}
    elif payload_type == "image": body["image"] = {"link": content, "caption": extra or ""}
    elif payload_type == "interactive": body["interactive"] = content
    try:
        r = requests.post(url, json=body, headers=headers, timeout=10)
        if r.status_code in [200, 201]:
            db_query("INSERT INTO messages (msg_id, from_number, content, msg_type, direction) VALUES (?, ?, ?, ?, 'outbound')", (r.json().get("messages",[{}])[0].get("id", "out"), to, str(content), payload_type), commit=True)
            return True
        return False
    except: return False

def get_ai_answer(user_query):
    if not GEMINI_KEY: return "আমাদের প্রতিনিধি খুব দ্রুত আপনার সাথে যোগাযোগ করবেন।"
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_KEY)
        p_rows = db_query("SELECT * FROM products WHERE active = 1 AND stock > 0", fetchall=True) or []
        catalog = "\n".join([f"- {p['name']}: {p['price']}৳ ({p['description']}) [Stock: {p['stock']}]" for p in p_rows])
        s = get_all_settings()
        si = f"You are the AI Sales Executive for '{s.get('business_name')}' in Bangladesh. Answer shortly and sweetly in Bengali. Catalog:\n{catalog}"
        cfg = types.GenerateContentConfig(system_instruction=si, temperature=0.2, max_output_tokens=300)
        return client.models.generate_content(model="gemini-2.5-flash", contents=user_query, config=cfg).text
    except: return "আপনার প্রশ্নটি রেকর্ড করা হয়েছে। আমাদের কাস্টমার কেয়ার টিম মেসেজ দিচ্ছে।"

# =====================================================================
# 100% DYNAMIC INTERACTIVE WEBHOOK WORKFLOW
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

    if any(k in body_text.lower() for k in ["call", "কল", "ফোন", "কথা", "প্রতিনিধি"]):
        db_query("INSERT INTO call_requests (phone, name, reason) VALUES (?, 'Customer', ?)", (from_number, body_text[:100]), commit=True)
        send_whatsapp(from_number, "text", "📞 ধন্যবাদ। আপনার কল ব্যাক রিকোয়েস্টটি লাইভ অ্যাডমিন প্যানেলে পাঠানো হয়েছে।")
        return

    sess = db_query("SELECT * FROM sessions WHERE phone = ?", (from_number,), fetchone=True)
    state = sess["state"] if sess else "idle"
    ctx = json.loads(sess["context"]) if sess and sess.get("context") else {}

    # State: IDLE -> Trigger Catalogue
    if state == "idle" and any(k in body_text.lower() for k in ["কিনব", "অর্ডার", "buy", "order", "প্রোডাক্ট"]):
        products = db_query("SELECT * FROM products WHERE active = 1 AND stock > 0 LIMIT 10", fetchall=True) or []
        if not products:
            send_whatsapp(from_number, "text", "দুঃখিত, আমাদের সবগুলো প্রোডাক্ট এই মুহূর্তে স্টক-আউট আছে।")
            return
        rows = [{"id": f"p_{p['id']}", "title": p['name'][:24], "description": f"{p['price']}৳"} for p in products]
        menu = {"type": "list", "body": {"text": "আমাদের লাইভ স্টক ক্যাটালগ থেকে প্রোডাক্টটি সিলেক্ট করুন:"}, "action": {"button": "প্রোডাক্ট লিস্ট", "sections": [{"title": "স্টক আইটেম", "rows": rows}]}}
        db_query("INSERT INTO sessions (phone, state, context) VALUES (?, 'selecting_product', '{}') ON CONFLICT(phone) DO UPDATE SET state='selecting_product', context='{}'", (from_number,), commit=True)
        send_whatsapp(from_number, "interactive", menu)
        return

    # State: Selecting Product
    if state == "selecting_product" and body_text.startswith("p_"):
        pid = int(body_text.split("_")[1])
        p = db_query("SELECT * FROM products WHERE id = ?", (pid,), fetchone=True)
        if p:
            ctx = {"product_id": pid, "name": p["name"], "price": p["price"], "max_stock": p["stock"]}
            # Generate Dynamic Quantity Buttons based on Live Stock
            buttons = []
            for i in range(1, min(p["stock"] + 1, 4)):
                buttons.append({"type": "reply", "reply": {"id": f"q_{i}", "title": f"{i} পিস"}})
            
            btns = {"type": "button", "body": {"text": f"🔹 {p['name']}\n💰 মূল্য: {p['price']}৳\n📊 স্টকে আছে: {p['stock']} টি\n\nআপনি কত পিস অর্ডার করতে চান?"}, "action": {"buttons": buttons}}
            db_query("UPDATE sessions SET state='selecting_qty', context=? WHERE phone=?", (json.dumps(ctx), from_number), commit=True)
            if p.get("image_url"): send_whatsapp(from_number, "image", p["image_url"], p["name"])
            send_whatsapp(from_number, "interactive", btns)
            return

    # State: Selecting Quantity
    if state == "selecting_qty" and body_text.startswith("q_"):
        qty = int(body_text.split("_")[1])
        ctx["quantity"] = qty
        ctx["subtotal"] = ctx["price"] * qty
        db_query("UPDATE sessions SET state='awaiting_name', context=? WHERE phone=?", (json.dumps(ctx), from_number), commit=True)
        send_whatsapp(from_number, "text", "📝 ধন্যবাদ! এবার অনুগ্রহ করে আপনার সম্পূর্ণ নাম টাইপ করুন:")
        return

    # State: Awaiting Name
    if state == "awaiting_name" and body_text:
        ctx["cust_name"] = body_text
        db_query("UPDATE sessions SET state='awaiting_address', context=? WHERE phone=?", (json.dumps(ctx), from_number), commit=True)
        send_whatsapp(from_number, "text", "📍 আপনার সম্পূর্ণ ডেলিভারি ঠিকানা (বাসা নম্বর, রোড, এলাকা) দিন:")
        return

    # State: Awaiting Address -> Fetch 100% Dynamic Cities from Pathao API matching user text input
    if state == "awaiting_address" and body_text:
        ctx["address"] = body_text
        # Dynamic Search on Pathao API matching user context
        all_cities = get_pathao_data("/aladdin/api/v1/countries/1/city-list")
        search_query = body_text.split(",")[-1].strip().lower() # Check last word for city guess
        
        filtered_cities = [c for c in all_cities if search_query in c['city_name'].lower()][:10]
        if not filtered_cities: filtered_cities = all_cities[:10] # Fallback to top cities
        
        rows = [{"id": f"c_{c['city_id']}_{c['city_name']}", "title": c['city_name'][:24]} for c in filtered_cities]
        menu = {"type": "list", "body": {"text": "আপনার ঠিকানা অনুযায়ী নিচে থেকে আপনার শহরটি সুনির্দিষ্টভাবে সিলেক্ট করুন:"}, "action": {"button": "শহর সিলেক্ট করুন", "sections": [{"title": "শহরসমূহ", "rows": rows}]}}
        db_query("UPDATE sessions SET state='selecting_city', context=? WHERE phone=?", (json.dumps(ctx), from_number), commit=True)
        send_whatsapp(from_number, "interactive", menu)
        return

    # State: Selecting City -> Fetch Dynamic Zones from Pathao
    if state == "selecting_city" and body_text.startswith("c_"):
        parts = body_text.split("_")
        cid = int(parts[1])
        cname = parts[2]
        ctx["city_id"] = cid
        ctx["city_name"] = cname
        
        # Calculate Fully Dynamic Delivery Fee based on City Name
        s = get_all_settings()
        if "dhaka" in cname.lower():
            ctx["delivery_fee"] = int(s.get("delivery_inside_dhaka", 60))
        else:
            ctx["delivery_fee"] = int(s.get("delivery_outside_dhaka", 120))

        zones = get_pathao_data(f"/aladdin/api/v1/cities/{cid}/zone-list")[:10]
        if zones:
            rows = [{"id": f"z_{z['zone_id']}", "title": z['zone_name'][:24]} for z in zones]
            menu = {"type": "list", "body": {"text": "এবার আপনার নির্দিষ্ট জোন/থানাটি সিলেক্ট করুন:"}, "action": {"button": "জোন সিলেক্ট করুন", "sections": [{"title": "জোন/থানা সমূহ", "rows": rows}]}}
            db_query("UPDATE sessions SET state='selecting_zone', context=? WHERE phone=?", (json.dumps(ctx), from_number), commit=True)
            send_whatsapp(from_number, "interactive", menu)
        else:
            ctx["zone_id"], ctx["area_id"] = 1, 1
            confirm_order_final(from_number, ctx)
        return

    # State: Selecting Zone -> Fetch Dynamic Areas from Pathao
    if state == "selecting_zone" and body_text.startswith("z_"):
        zid = int(body_text.split("_")[1])
        ctx["zone_id"] = zid
        areas = get_pathao_data(f"/aladdin/api/v1/zones/{zid}/area-list")[:10]
        if areas:
            rows = [{"id": f"a_{a['area_id']}", "title": a['area_name'][:24]} for a in areas]
            menu = {"type": "list", "body": {"text": "সর্বশেষ, আপনার ডেলিভারি এরিয়া/পোস্টাল কোড এরিয়া সিলেক্ট করুন:"}, "action": {"button": "এরিয়া সিলেক্ট করুন", "sections": [{"title": "এরিয়া তালিকা", "rows": rows}]}}
            db_query("UPDATE sessions SET state='selecting_area', context=? WHERE phone=?", (json.dumps(ctx), from_number), commit=True)
            send_whatsapp(from_number, "interactive", menu)
        else:
            ctx["area_id"] = 1
            confirm_order_final(from_number, ctx)
        return

    # State: Selecting Area
    if state == "selecting_area" and body_text.startswith("a_"):
        ctx["area_id"] = int(body_text.split("_")[1])
        confirm_order_final(from_number, ctx)
        return

    # State: Final Confirmation Order Processing
    if state == "awaiting_confirmation":
        if body_text == "conf_yes":
            # Dynamic Order Placement on Pathao System
            total_cod = ctx["subtotal"] + ctx["delivery_fee"]
            success, res = create_pathao_order(ctx["cust_name"], from_number, ctx["address"], ctx["city_id"], ctx["zone_id"], ctx["area_id"], f"{ctx['name']} x{ctx['quantity']}", total_cod)
            
            status = "created_in_pathao" if success else "pending_manual_approval"
            consignment = str(res) if success else ""
            
            db_query("INSERT INTO orders (phone, name, address, city_id, zone_id, area_id, product_id, quantity, total, delivery_fee, pathao_consignment_id, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", 
                     (from_number, ctx["cust_name"], ctx["address"], ctx["city_id"], ctx["zone_id"], ctx["area_id"], ctx["product_id"], ctx["quantity"], total_cod, ctx["delivery_fee"], consignment, status), commit=True)
            
            db_query("UPDATE products SET stock = MAX(0, stock - ?) WHERE id = ?", (ctx["quantity"], ctx["product_id"]), commit=True)
            
            msg = f"🎉 অভিনন্দন {ctx['cust_name']}! আপনার অর্ডারটি নিশ্চিত করা হয়েছে।\n📦 পাঠাও ট্র্যাকিং আইডি: {consignment}\n💰 সর্বমোট ক্যাশ-অন-ডেলিভারি কালেকশন: {total_cod}৳" if success else "⚠️ অর্ডারটি সিস্টেমে সেভ করা হয়েছে। ডেলিভারি এপিআই সমস্যার কারণে আমাদের টিম ম্যানুয়ালি চেক করে কনফার্ম করবে।"
            send_whatsapp(from_number, "text", msg)
        else:
            send_whatsapp(from_number, "text", "❌ আপনার অর্ডারটি বাতিল করা হয়েছে। আবার শুরু করতে 'অর্ডার' লিখুন।")
        db_query("UPDATE sessions SET state='idle', context='{}' WHERE phone=?", (from_number,), commit=True)
        return

    # Fallback to Dynamic AI Engine
    ai_msg = get_ai_answer(body_text)
    send_whatsapp(from_number, "text", ai_msg)

def confirm_order_final(from_number, ctx):
    total = ctx["subtotal"] + ctx["delivery_fee"]
    summary = f"📦 অর্ডারের পুঙ্খানুপুঙ্খ বিবরণী:\n\n📝 প্রোডাক্ট: {ctx['name']} (x{ctx['quantity']})\n💰 সাবটোটাল: {ctx['subtotal']}৳\n🚚 ডেলিভারি চার্জ ({ctx.get('city_name','শহর')}): {ctx['delivery_fee']}৳\n💵 সর্বমোট প্রদেয় বিল: {total}৳\n👤 কাস্টমার নাম: {ctx['cust_name']}\n📍 ঠিকানা: {ctx['address']}\n\nসব তথ্য ঠিক থাকলে নিচের বাটনে ক্লিক করুন:"
    btns = {"type": "button", "body": {"text": summary}, "action": {"buttons": [{"type": "reply", "reply": {"id": "conf_yes", "title": "হ্যাঁ, কনফার্ম"}}, {"type": "reply", "reply": {"id": "conf_no", "title": "না, বাতিল"}}]}}
    db_query("UPDATE sessions SET state='awaiting_confirmation', context=? WHERE phone=?", (json.dumps(ctx), from_number), commit=True)
    send_whatsapp(from_number, "interactive", btns)

# =====================================================================
# 100% REAL-TIME SUPER ADMIN DASHBOARD
# =====================================================================
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <title>Dynamic Super Admin Control Panel</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <script>
        // Feature 2 & 3: Fully Dynamic Live Chat Poller & Counter without page reload
        async function fetchRealtimeUpdates() {
            try {
                const res = await fetch('/admin/api/updates');
                const data = await res.json();
                if(document.getElementById('stat-orders')) document.getElementById('stat-orders').innerText = data.total_orders;
                if(document.getElementById('stat-rev')) document.getElementById('stat-rev').innerText = data.total_revenue + "৳";
                if(document.getElementById('stat-calls')) document.getElementById('stat-calls').innerText = data.pending_calls;
                
                // Live Chat Auto Refresher (Feature 3)
                const activeChat = document.getElementById('active_chat_phone').value;
                if (activeChat) {
                    const chatRes = await fetch('/admin/api/chat/' + activeChat);
                    const chatData = await chatRes.json();
                    const chatBox = document.getElementById('live-chat-messages-container');
                    let html = '';
                    chatData.messages.forEach(m => {
                        const isOut = m.direction === 'outbound';
                        html += `<div class="max-w-[70%] p-3 rounded-2xl text-sm ${isOut ? 'bg-emerald-600 text-white self-end rounded-tr-none' : 'bg-white text-slate-800 self-start rounded-tl-none shadow-sm border'}">${m.content}</div>`;
                    });
                    chatBox.innerHTML = html;
                }
            } catch (e) { console.log(e); }
        }
        setInterval(fetchRealtimeUpdates, 3000); // 3 Seconds dynamic interval reload
    </script>
</head>
<body class="bg-slate-50 flex min-h-screen">
    <input type="hidden" id="active_chat_phone" value="{{ active_chat }}">

    <!-- Sidebar Dashboard Left Drawer -->
    <div class="w-64 bg-slate-900 text-white flex flex-col">
        <div class="p-6 text-lg font-black border-b border-slate-800 text-emerald-400"><i class="fa-solid fa-layer-group mr-2"></i> {{ settings.get('business_name') }}</div>
        <nav class="flex-1 p-4 space-y-1">
            <a href="#dashboard" onclick="switchTab('dashboard')" class="tab-btn flex items-center gap-3 px-4 py-3 rounded-xl bg-emerald-600 text-white font-bold transition"><i class="fa-solid fa-gauge"></i> Dashboard</a>
            <a href="#orders" onclick="switchTab('orders')" class="tab-btn flex items-center gap-3 px-4 py-3 rounded-xl text-slate-400 hover:bg-slate-800 hover:text-white transition"><i class="fa-solid fa-shopping-bag"></i> Orders</a>
            <a href="#calls" onclick="switchTab('calls')" class="tab-btn flex items-center gap-3 px-4 py-3 rounded-xl text-slate-400 hover:bg-slate-800 hover:text-white transition"><i class="fa-solid fa-phone"></i> Call Requests</a>
            <a href="#products" onclick="switchTab('products')" class="tab-btn flex items-center gap-3 px-4 py-3 rounded-xl text-slate-400 hover:bg-slate-800 hover:text-white transition"><i class="fa-solid fa-cubes"></i> Products (CRUD)</a>
            <a href="#chat" onclick="switchTab('chat')" class="tab-btn flex items-center gap-3 px-4 py-3 rounded-xl text-slate-400 hover:bg-slate-800 hover:text-white transition"><i class="fa-solid fa-message"></i> Live Chat Desk</a>
            <a href="#broadcast" onclick="switchTab('broadcast')" class="tab-btn flex items-center gap-3 px-4 py-3 rounded-xl text-slate-400 hover:bg-slate-800 hover:text-white transition"><i class="fa-solid fa-bullhorn"></i> Broadcast</a>
            <a href="#settings" onclick="switchTab('settings')" class="tab-btn flex items-center gap-3 px-4 py-3 rounded-xl text-slate-400 hover:bg-slate-800 hover:text-white transition"><i class="fa-solid fa-sliders"></i> System Config</a>
        </nav>
    </div>

    <!-- Right Container Panel Workspace -->
    <div class="flex-1 p-8 overflow-y-auto max-h-screen">
        
        <!-- DASHBOARD TAB -->
        <div id="tab-dashboard" class="tab-content space-y-6">
            <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
                <div class="bg-white p-6 rounded-2xl border shadow-sm">
                    <p class="text-xs font-bold uppercase text-slate-400">Total Live Orders</p>
                    <h2 id="stat-orders" class="text-3xl font-black text-slate-800 mt-2">{{ stats.total_orders }}</h2>
                </div>
                <div class="bg-white p-6 rounded-2xl border shadow-sm">
                    <p class="text-xs font-bold uppercase text-slate-400">Total Dynamic Revenue</p>
                    <h2 id="stat-rev" class="text-3xl font-black text-emerald-600 mt-2">{{ stats.total_revenue or 0 }}৳</h2>
                </div>
                <div class="bg-white p-6 rounded-2xl border shadow-sm">
                    <p class="text-xs font-bold uppercase text-slate-400">Pending Callbacks</p>
                    <h2 id="stat-calls" class="text-3xl font-black text-amber-500 mt-2">{{ stats.pending_calls }}</h2>
                </div>
            </div>
        </div>

        <!-- ORDERS TAB -->
        <div id="tab-orders" class="tab-content hidden bg-white rounded-2xl border shadow-sm overflow-hidden">
            <table class="w-full text-left text-sm">
                <tr class="bg-slate-100 border-b text-xs font-bold text-slate-600 uppercase"><th class="p-4">ID</th><th class="p-4">Customer</th><th class="p-4">Product Specs</th><th class="p-4">Delivery Fee</th><th class="p-4">Total Collected</th><th class="p-4">Pathao Consignment</th></tr>
                {% for o in orders %}
                <tr class="border-b hover:bg-slate-50">
                    <td class="p-4 font-bold">#{{ o.id }}</td>
                    <td class="p-4"><div><b>{{ o.name }}</b></div><div class="text-xs text-slate-400">{{ o.phone }}</div><div class="text-xs text-slate-500 italic">{{ o.address }}</div></td>
                    <td class="p-4">Product ID: {{ o.product_id }} (x{{ o.quantity }})</td>
                    <td class="p-4 text-amber-600 font-bold">{{ o.delivery_fee }}৳</td>
                    <td class="p-4 text-emerald-600 font-black">{{ o.total }}৳</td>
                    <td class="p-4 font-mono text-xs">{{ o.pathao_consignment_id or 'Manual Pending' }}</td>
                </tr>
                {% endfor %}
            </table>
        </div>

        <!-- CALLS TAB -->
        <div id="tab-calls" class="tab-content hidden bg-white rounded-2xl border shadow-sm overflow-hidden">
            <table class="w-full text-left text-sm">
                <tr class="bg-slate-100 border-b text-xs font-bold text-slate-600"><th class="p-4">Phone</th><th class="p-4">Reason Asked</th><th class="p-4">Timestamp</th><th class="p-4">Action</th></tr>
                {% for c in calls %}
                <tr class="border-b">
                    <td class="p-4 font-bold">{{ c.phone }}</td><td class="p-4 italic text-slate-700">"{{ c.reason }}"</td><td class="p-4 text-xs text-slate-400">{{ c.created_at }}</td>
                    <td class="p-4"><form action="/admin/call/complete/{{ c.id }}" method="POST"><button class="bg-emerald-600 text-white px-3 py-1 rounded text-xs font-bold">Resolve</button></form></td>
                </tr>
                {% endfor %}
            </table>
        </div>

        <!-- CRUD PRODUCTS TAB -->
        <div id="tab-products" class="tab-content hidden space-y-6">
            <div class="bg-white p-6 rounded-2xl border shadow-sm">
                <h3 class="font-bold text-slate-800 mb-4"><i class="fa-solid fa-circle-plus text-emerald-500"></i> Add New Product Item to Inventory</h3>
                <form action="/admin/products/add" method="POST" class="grid grid-cols-1 md:grid-cols-4 gap-4">
                    <input type="text" name="name" placeholder="Item Name" required class="border p-2.5 rounded-xl text-sm">
                    <input type="number" name="price" placeholder="Price Amount" required class="border p-2.5 rounded-xl text-sm">
                    <input type="number" name="stock" placeholder="Stock Available" required class="border p-2.5 rounded-xl text-sm">
                    <input type="text" name="image_url" placeholder="Direct Image URL Address" class="border p-2.5 rounded-xl text-sm">
                    <textarea name="description" placeholder="Write full product specifications..." class="border p-2.5 rounded-xl text-sm md:col-span-4"></textarea>
                    <button type="submit" class="bg-slate-900 text-white font-bold p-3 rounded-xl md:col-span-4">Push Item to Stock</button>
                </form>
            </div>
            <div class="bg-white rounded-2xl border shadow-sm overflow-hidden">
                <table class="w-full text-left text-sm">
                    <tr class="bg-slate-100 border-b text-xs font-bold text-slate-600"><th class="p-4">Visual</th><th class="p-4">Name</th><th class="p-4">Price</th><th class="p-4">Live Stock</th><th class="p-4">Status Actions</th></tr>
                    {% for p in products %}
                    <tr class="border-b">
                        <td class="p-4"><img src="{{ p.image_url or 'https://placehold.co/50' }}" class="w-10 h-10 object-cover rounded border"></td>
                        <td class="p-4 font-bold">{{ p.name }}</td><td class="p-4">{{ p.price }}৳</td><td class="p-4 font-mono font-bold text-blue-600">{{ p.stock }} Units</td>
                        <td class="p-4"><a href="/admin/products/delete/{{ p.id }}" class="text-red-500 hover:underline font-bold"><i class="fa-solid fa-trash-can"></i> Delist</a></td>
                    </tr>
                    {% endfor %}
                </table>
            </div>
        </div>

        <!-- LIVE CHAT DESK TAB -->
        <div id="tab-chat" class="tab-content hidden bg-white rounded-2xl border shadow-sm grid grid-cols-3 min-h-[520px]">
            <div class="border-r p-4 space-y-2 overflow-y-auto max-h-[520px] bg-slate-50/50">
                <h4 class="font-bold text-xs uppercase tracking-wider text-slate-400 mb-4">Inbox Stream Contacts</h4>
                {% for u in users %}
                <a href="?chat_with={{ u.phone }}#chat" onclick="setTimeout(()=>switchTab('chat'), 50)" class="block p-3 rounded-xl border {{ 'bg-emerald-50 border-emerald-300' if active_chat == u.phone else 'bg-white' }} transition shadow-xs">
                    <div class="font-bold text-sm text-slate-800">{{ u.phone }}</div>
                    <div class="text-[10px] text-slate-400 mt-1">Activity Log: {{ u.last_active }}</div>
                </a>
                {% endfor %}
            </div>
            <div class="col-span-2 flex flex-col justify-between max-h-[520px]">
                <div class="p-4 border-b font-bold text-slate-700 bg-slate-50 flex justify-between items-center text-sm">
                    <span>Active Screen: {{ active_chat or 'No Selected Chat' }}</span>
                </div>
                <div id="live-chat-messages-container" class="p-4 space-y-3 flex-1 overflow-y-auto bg-slate-100/50 flex flex-col">
                    {% for m in chat_messages %}
                    <div class="max-w-[70%] p-3 rounded-2xl text-sm {{ 'bg-emerald-600 text-white self-end rounded-tr-none' if m.direction == 'outbound' else 'bg-white text-slate-800 self-start rounded-tl-none shadow-sm border' }}">
                        {{ m.content }}
                    </div>
                    {% endfor %}
                </div>
                <form action="/admin/chat/reply" method="POST" class="p-4 border-t flex gap-2 bg-white">
                    <input type="hidden" name="to_phone" value="{{ active_chat }}">
                    <input type="text" name="reply_text" placeholder="Type WhatsApp manual message reply here..." required {{ 'disabled' if not active_chat }} class="flex-1 border p-3 rounded-xl text-sm">
                    <button type="submit" {{ 'disabled' if not active_chat }} class="bg-emerald-600 text-white font-bold px-6 rounded-xl text-sm"><i class="fa-solid fa-paper-plane"></i></button>
                </form>
            </div>
        </div>

        <!-- BROADCAST TAB -->
        <div id="tab-broadcast" class="tab-content hidden bg-white p-6 rounded-2xl border shadow-sm max-w-xl">
            <h3 class="font-bold text-slate-800 mb-2"><i class="fa-solid fa-bullhorn text-indigo-600"></i> Dynamic Bulk Marketing Broadcast Broadcast</h3>
            <p class="text-xs text-slate-400 mb-6">Loop directly through all registered user database rows and blast text messages via standard API pipes.</p>
            <form action="/admin/broadcast" method="POST" class="space-y-4">
                <textarea name="broadcast_msg" rows="5" required placeholder="Write bulk message script template..." class="w-full border p-3 rounded-xl text-sm"></textarea>
                <button type="submit" class="bg-indigo-600 text-white font-bold py-3 px-6 rounded-xl w-full text-sm"><i class="fa-solid fa-paper-plane-top"></i> Execute Blast Pipeline</button>
            </form>
        </div>

        <!-- SETTINGS CONFIG TAB -->
        <div id="tab-settings" class="tab-content hidden bg-white p-6 rounded-2xl border shadow-sm max-w-xl">
            <h3 class="font-bold text-slate-800 mb-4">Core Variable Threshold configurations</h3>
            <form action="/admin/settings/save" method="POST" class="space-y-4 text-sm">
                <div><label class="block font-bold text-slate-600 mb-1">Business Name</label><input type="text" name="business_name" value="{{ settings.get('business_name','') }}" class="w-full border p-2.5 rounded-xl"></div>
                <div><label class="block font-bold text-slate-600 mb-1">Delivery Charge Inside Dhaka (৳)</label><input type="number" name="delivery_inside_dhaka" value="{{ settings.get('delivery_inside_dhaka','60') }}" class="w-full border p-2.5 rounded-xl"></div>
                <div><label class="block font-bold text-slate-600 mb-1">Delivery Charge Outside Dhaka (৳)</label><input type="number" name="delivery_outside_dhaka" value="{{ settings.get('delivery_outside_dhaka','120') }}" class="w-full border p-2.5 rounded-xl"></div>
                <div><label class="block font-bold text-slate-600 mb-1">Pathao Client ID</label><input type="text" name="pathao_client_id" value="{{ settings.get('pathao_client_id','') }}" class="w-full border p-2.5 rounded-xl"></div>
                <div><label class="block font-bold text-slate-600 mb-1">Pathao Client Secret</label><input type="text" name="pathao_client_secret" value="{{ settings.get('pathao_client_secret','') }}" class="w-full border p-2.5 rounded-xl"></div>
                <div><label class="block font-bold text-slate-600 mb-1">Pathao Store ID</label><input type="text" name="pathao_store_id" value="{{ settings.get('pathao_store_id','') }}" class="w-full border p-2.5 rounded-xl"></div>
                <div><label class="block font-bold text-slate-600 mb-1">Pathao Merchant Email</label><input type="text" name="pathao_merchant_email" value="{{ settings.get('pathao_merchant_email','') }}" class="w-full border p-2.5 rounded-xl"></div>
                <div><label class="block font-bold text-slate-600 mb-1">Pathao Merchant Password</label><input type="password" name="pathao_merchant_password" value="{{ settings.get('pathao_merchant_password','') }}" class="w-full border p-2.5 rounded-xl"></div>
                <button type="submit" class="bg-slate-900 text-white font-bold py-3 px-6 rounded-xl w-full">Commit Configuration Variables</button>
            </form>
        </div>

    </div>

    <script>
        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            document.getElementById('tab-' + tabId).classList.remove('hidden');
            document.querySelectorAll('.tab-btn').forEach(btn => {
                btn.classList.remove('bg-emerald-600', 'font-bold', 'text-white');
                btn.classList.add('text-slate-400');
            });
            const activeBtn = Array.from(document.querySelectorAll('.tab-btn')).find(b => b.getAttribute('href') === '#' + tabId);
            if(activeBtn) {
                activeBtn.classList.remove('text-slate-400');
                activeBtn.classList.add('bg-emerald-600', 'font-bold', 'text-white');
            }
        }
        window.addEventListener('DOMContentLoaded', () => {
            const hash = window.location.hash.replace('#', '') || 'dashboard';
            switchTab(hash);
        });
    </script>
</body>
</html>
"""

# =====================================================================
# DYNAMIC BACKEND WEB API ROUTING CONTROLLERS
# =====================================================================
@app.route("/admin", methods=["GET"])
@login_required
def admin_dashboard():
    stats = {
        "total_orders": db_query("SELECT COUNT(*) as cnt FROM orders", fetchone=True)["cnt"],
        "total_revenue": db_query("SELECT SUM(total) as rev FROM orders", fetchone=True)["rev"],
        "pending_calls": db_query("SELECT COUNT(*) as cnt FROM call_requests WHERE status='pending'", fetchone=True)["cnt"]
    }
    orders = db_query("SELECT * FROM orders ORDER BY id DESC LIMIT 30", fetchall=True) or []
    calls = db_query("SELECT * FROM call_requests WHERE status='pending' ORDER BY id DESC", fetchall=True) or []
    products = db_query("SELECT * FROM products WHERE active=1 ORDER BY id DESC", fetchall=True) or []
    users = db_query("SELECT * FROM users ORDER BY last_active DESC LIMIT 30", fetchall=True) or []
    settings = get_all_settings()
    
    active_chat = request.args.get("chat_with", "")
    chat_messages = []
    if active_chat:
        chat_messages = db_query("SELECT * FROM messages WHERE from_number = ? ORDER BY created_at ASC LIMIT 50", (active_chat,), fetchall=True) or []

    return render_template_string(ADMIN_HTML, stats=stats, orders=orders, calls=calls, products=products, users=users, settings=settings, active_chat=active_chat, chat_messages=chat_messages)

@app.route("/admin/api/updates", methods=["GET"])
@login_required
def admin_api_updates():
    tot = db_query("SELECT COUNT(*) as cnt FROM orders", fetchone=True)["cnt"]
    rev = db_query("SELECT SUM(total) as rev FROM orders", fetchone=True)["rev"]
    cl = db_query("SELECT COUNT(*) as cnt FROM call_requests WHERE status='pending'", fetchone=True)["cnt"]
    return jsonify({"total_orders": tot, "total_revenue": rev or 0, "pending_calls": cl})

@app.route("/admin/api/chat/<string:phone>", methods=["GET"])
@login_required
def admin_api_chat_stream(phone):
    rows = db_query("SELECT content, direction FROM messages WHERE from_number = ? ORDER BY created_at ASC LIMIT 50", (phone,), fetchall=True) or []
    return jsonify({"messages": rows})

@app.route("/admin/call/complete/<int:call_id>", methods=["POST"])
@login_required
def complete_call(call_id):
    db_query("UPDATE call_requests SET status = 'completed' WHERE id = ?", (call_id,), commit=True)
    return redirect(url_for('admin_dashboard') + "#calls")

@app.route("/admin/products/add", methods=["POST"])
@login_required
def add_product():
    name = request.form.get("name")
    price = int(request.form.get("price") or 0)
    stock = int(request.form.get("stock") or 0)
    img = request.form.get("image_url", "")
    desc = request.form.get("description", "")
    db_query("INSERT INTO products (name, price, stock, image_url, description) VALUES (?, ?, ?, ?, ?)", (name, price, stock, img, desc), commit=True)
    return redirect(url_for('admin_dashboard') + "#products")

@app.route("/admin/products/delete/<int:pid>", methods=["GET"])
@login_required
def delete_product(pid):
    db_query("UPDATE products SET active=0 WHERE id=?", (pid,), commit=True)
    return redirect(url_for('admin_dashboard') + "#products")

@app.route("/admin/chat/reply", methods=["POST"])
@login_required
def chat_reply():
    to = request.form.get("to_phone")
    text = request.form.get("reply_text")
    if to and text:
        send_whatsapp(to, "text", text)
    return redirect(f"/admin?chat_with={to}#chat")

@app.route("/admin/broadcast", methods=["POST"])
@login_required
def trigger_broadcast():
    msg = request.form.get("broadcast_msg")
    users = db_query("SELECT phone FROM users", fetchall=True) or []
    if msg:
        for u in users:
            send_whatsapp(u["phone"], "text", msg)
    return redirect(url_for('admin_dashboard') + "#broadcast")

@app.route("/admin/settings/save", methods=["POST"])
@login_required
def save_settings():
    for k, v in request.form.items():
        if v.strip():
            db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v.strip()), commit=True)
    return redirect(url_for('admin_dashboard') + "#settings")

# =====================================================================
# SYSTEM GATEWAYS WEBHOOKS
# =====================================================================
@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge"), 200
    return "Verification failed", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    signature = request.headers.get("X-Hub-Signature-256", "")
    payload = request.get_data()
    if APP_SECRET and signature:
        sha_name, sig_val = signature.split('=')
        if sha_name == 'sha256':
            mac = hmac.new(APP_SECRET.encode('utf-8'), payload, hashlib.sha256)
            if not hmac.compare_digest(mac.hexdigest(), sig_val): return "Forbidden", 403
    
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
