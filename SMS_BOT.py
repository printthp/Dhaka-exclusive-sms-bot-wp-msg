import os
import sys
import json
import sqlite3
import logging
from datetime import datetime
from threading import Thread, Lock
import time
import requests
from flask import Flask, request, jsonify, render_template_string, redirect, url_for

# Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
application = app  # For Gunicorn deployment on Render

# DB file configured for persistent environment
DB_FILE = "bot_v7_ultimate.db"
db_lock = Lock()

# =====================================================================
# DATABASE INITIALIZATION (With your exact default credentials)
# =====================================================================
def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        tables = [
            "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, msg_id TEXT UNIQUE, from_number TEXT, content TEXT, msg_type TEXT DEFAULT 'text', direction TEXT DEFAULT 'inbound', agent_id TEXT DEFAULT 'system', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS sessions (phone TEXT PRIMARY KEY, state TEXT DEFAULT 'idle', context TEXT DEFAULT '{}', last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP, recovered INTEGER DEFAULT 0, bot_paused INTEGER DEFAULT 0)",
            "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, name TEXT, address TEXT, city_id INTEGER DEFAULT 1, zone_id INTEGER DEFAULT 1, area_id INTEGER DEFAULT 1, product_id INTEGER, quantity INTEGER DEFAULT 1, total INTEGER, delivery_fee INTEGER, pathao_consignment_id TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, fb_product_id TEXT UNIQUE, name TEXT, price INTEGER, description TEXT, stock INTEGER DEFAULT 10, active INTEGER DEFAULT 1, image_url TEXT DEFAULT '')",
            "CREATE TABLE IF NOT EXISTS users (phone TEXT PRIMARY KEY, name TEXT DEFAULT 'Customer', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ]
        for t in tables:
            c.execute(t)
            
        # আপনার দেওয়া রিয়েল টোকেন এবং ক্রেডেনশিয়ালস এখানে ডিফল্ট হিসেবে সেট করা হয়েছে
        defaults = [
            ("business_name", "Dhaka Exclusive"), 
            ("permanent_token", "EAANtSb24BiwBRREXu8HztnpOLtamcKIvi09Qb24LiYax45S4aoYtFEVKEQZAxigfO2wbGf6RgHh51IURbQzKKrzPhkcprLxHpZBfOwxZAVCscdVOpjbapbS9sOLCIqZBM8tZAtSRRaVVYSTZBjUkkPZAQaLABSnG6cQcgQcwqZBC5I5yrB4cXgoUPDlzzn7HzUwsMAZDZD"),
            ("phone_number_id", "1039959469208417"),
            ("gemini_key", "AIzaSyCRZIRWSoenfhA33qr7rkzoa56Byun0IWU"),
            ("verify_token", "dhakaex0020"),
            ("fb_catalogue_id", ""),
            ("fb_access_token", ""),
            ("ai_system_instruction", "আপনি একজন প্রফেশনাল কাস্টমার অ্যাসিস্ট্যান্ট। কাস্টমারের সাথে বাংলায় বিনীতভাবে কথা বলুন এবং প্রোডাক্ট কিনতে সাহায্য করুন।"),
            ("pathao_base_url", "https://api-hermes.pathao.com"),
            ("pathao_store_id", "333358"),
            ("pathao_client_id", "openOlRa7A"),
            ("pathao_client_secret", "7clJGfV1jh5njQEuR5yepVXZ9nYAjGORhNCOjgzG"),
            ("pathao_merchant_email", "cocid1000006@gmail.com"),
            ("pathao_merchant_password", "trustedaA@2"),
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
            return None
        finally:
            conn.close()

def get_all_settings():
    rows = db_query("SELECT key, value FROM settings", fetchall=True) or []
    return {r["key"]: r["value"] for r in rows}

# =====================================================================
# FACEBOOK CATALOGUE SYNC ENGINE
# =====================================================================
def sync_facebook_catalogue():
    s = get_all_settings()
    cat_id = s.get("fb_catalogue_id")
    token = s.get("fb_access_token")
    if not cat_id or not token: return False, "ফেসবুক ক্যাটালগ আইডি বা অ্যাক্সেস টোকেন সেটিংস থেকে মিসিং!"
    
    url = f"https://graph.facebook.com/v21.0/{cat_id}/products"
    params = {"fields": "id,name,price,description,image_url", "access_token": token, "limit": 100}
    try:
        r = requests.get(url, params=params, timeout=15)
        res = r.json()
        if "data" not in res: return False, res.get("error", {}).get("message", "Unknown Meta Error")
        
        sync_count = 0
        for item in res["data"]:
            fb_id = item.get("id")
            name = item.get("name")
            desc = item.get("description", "No description")
            img_url = item.get("image_url", "https://placehold.co/400")
            raw_price = item.get("price", "0")
            try:
                digits = "".join([c for c in raw_price if c.isdigit() or c == '.'])
                price = int(float(digits)) if digits else 0
            except: price = 0
            
            db_query('''
                INSERT INTO products (fb_product_id, name, price, description, image_url, stock, active)
                VALUES (?, ?, ?, ?, ?, 10, 1)
                ON CONFLICT(fb_product_id) DO UPDATE SET name=excluded.name, price=excluded.price, description=excluded.description, image_url=excluded.image_url
            ''', (fb_id, name, price, desc, img_url), commit=True)
            sync_count += 1
        return True, f"সফলভাবে {sync_count}টি প্রোডাক্ট ফেসবুক ক্যাটালগ থেকে সিঙ্ক হয়েছে!"
    except Exception as e:
        return False, str(e)

# =====================================================================
# PATHAO COURIER API GATEWAY
# =====================================================================
def get_pathao_token():
    s = get_all_settings()
    try:
        r = requests.post(f"{s.get('pathao_base_url')}/aladdin/api/v1/issue-token", json={
            "client_id": s.get("pathao_client_id"), "client_secret": s.get("pathao_client_secret"),
            "username": s.get("pathao_merchant_email"), "password": s.get("pathao_merchant_password"), "grant_type": "password"
        }, headers={"content-type": "application/json"}, timeout=10)
        res_data = r.json()
        token = res_data.get("access_token") or res_data.get("token")
        return token, None
    except Exception as e: return None, str(e)

def create_pathao_order(order_ctx, phone, total_cod):
    token, err = get_pathao_token()
    if not token: return False, f"Pathao Token Error: {err}"
    s = get_all_settings()
    try:
        payload = {
            "store_id": int(s.get("pathao_store_id", 0)), "recipient_name": order_ctx["cust_name"],
            "recipient_phone": phone, "recipient_address": order_ctx["address"], "recipient_city": 1,
            "recipient_zone": 1, "recipient_area": 1, "delivery_type": 48, "item_type": 2,
            "special_instruction": "Bot Auto Order", "item_quantity": int(order_ctx["quantity"]),
            "amount_to_collect": int(total_cod), "item_description": order_ctx["name"]
        }
        r = requests.post(f"{s.get('pathao_base_url')}/aladdin/api/v1/orders", json=payload, headers={"authorization": f"Bearer {token}", "content-type": "application/json"}, timeout=15)
        if r.status_code == 200 and r.json().get("status") == 200:
            return True, r.json().get("data", {}).get("consignment_id")
        return False, r.json().get("message", "Booking failed")
    except Exception as e: return False, str(e)

# =====================================================================
# WHATSAPP SENDER & AI ENGINE
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
            gen_id = r.json().get("messages", [{}])[0].get("id", f"out_{int(time.time())}")
            db_query("INSERT INTO messages (msg_id, from_number, content, msg_type, direction, agent_id) VALUES (?, ?, ?, ?, 'outbound', ?)", 
                     (gen_id, to, str(content), payload_type, agent), commit=True)
            return True
        return False
    except: return False

def get_ai_answer(user_query):
    s = get_all_settings()
    key = s.get("gemini_key")
    if not key: return "আমাদের কাস্টমার রিপ্রেজেন্টেটিভ খুব দ্রুত আপনার সাথে যোগাযোগ করবেন।"
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key)
        p_rows = db_query("SELECT * FROM products WHERE active = 1 AND stock > 0", fetchall=True) or []
        catalog = "\n".join([f"- {p['name']}: {p['price']}৳ ({p['description']})" for p in p_rows])
        si = f"{s.get('ai_system_instruction')}\n\nচলতি প্রোডাক্ট ক্যাটালগ:\n{catalog}"
        cfg = types.GenerateContentConfig(system_instruction=si, temperature=0.3, max_output_tokens=300)
        return client.models.generate_content(model="gemini-2.5-flash", contents=user_query, config=cfg).text
    except Exception as e:
        return "আপনার মেসেজটি আমাদের প্যানেলে জমা হয়েছে। লাইভ এজেন্ট কিছুক্ষণের মধ্যে উত্তর দেবে।"

# =====================================================================
# INBOUND STATE MACHINE
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

    sess = db_query("SELECT * FROM sessions WHERE phone = ?", (from_number,), fetchone=True)
    if sess and sess.get("bot_paused") == 1:
        return

    state = sess["state"] if sess else "idle"
    ctx = json.loads(sess["context"]) if sess and sess.get("context") else {}

    if state == "idle" and any(k in body_text.lower() for k in ["কিনব", "অর্ডার", "buy", "order", "প্রোডাক্ট"]):
        products = db_query("SELECT * FROM products WHERE active = 1 AND stock > 0 LIMIT 10", fetchall=True) or []
        if not products:
            send_whatsapp(from_number, "text", "দুঃখিত ভাই, আমাদের স্টক এখন খালি। খুব দ্রুত নতুন স্টক আসবে।")
            return
        rows = [{"id": f"p_{p['id']}", "title": p['name'][:24], "description": f"{p['price']}৳"} for p in products]
        menu = {"type": "list", "body": {"text": "আমাদের হট সেলিং ক্যাটালগ থেকে প্রোডাক্ট সিলেক্ট করুন:"}, "action": {"button": "প্রোডাক্টস লিস্ট", "sections": [{"title": "চলতি স্টক", "rows": rows}]}}
        db_query("INSERT INTO sessions (phone, state, context, bot_paused) VALUES (?, 'selecting_product', '{}', 0) ON CONFLICT(phone) DO UPDATE SET state='selecting_product', context='{}'", (from_number,), commit=True)
        send_whatsapp(from_number, "interactive", menu)
        return

    if state == "selecting_product" and body_text.startswith("p_"):
        pid = int(body_text.split("_")[1])
        p = db_query("SELECT * FROM products WHERE id = ?", (pid,), fetchone=True)
        if p:
            ctx = {"product_id": pid, "name": p["name"], "price": p["price"]}
            btns = {"type": "button", "body": {"text": f"🔹 {p['name']}\n💰 মূল্য: {p['price']}৳\n\nকত পিস নিতে চান?"}, "action": {"buttons": [{"type": "reply", "reply": {"id": "q_1", "title": "১ পিস"}}, {"type": "reply", "reply": {"id": "q_2", "title": "২ পিস"}}]}}
            db_query("UPDATE sessions SET state='selecting_qty', context=? WHERE phone=?", (json.dumps(ctx), from_number), commit=True)
            if p.get("image_url"): send_whatsapp(from_number, "image", p["image_url"], p["name"])
            send_whatsapp(from_number, "interactive", btns)
            return

    if state == "selecting_qty" and body_text.startswith("q_"):
        ctx["quantity"] = int(body_text.split("_")[1])
        ctx["subtotal"] = ctx["price"] * ctx["quantity"]
        db_query("UPDATE sessions SET state='awaiting_name', context=? WHERE phone=?", (json.dumps(ctx), from_number), commit=True)
        send_whatsapp(from_number, "text", "📝 আপনার নাম কি?")
        return

    if state == "awaiting_name":
        ctx["cust_name"] = body_text
        db_query("UPDATE sessions SET state='awaiting_address', context=? WHERE phone=?", (json.dumps(ctx), from_number), commit=True)
        send_whatsapp(from_number, "text", "📍 ডেলিভারির সম্পূর্ণ ঠিকানা ও জেলা লিখুন:")
        return

    if state == "awaiting_address":
        ctx["address"] = body_text
        s = get_all_settings()
        ctx["delivery_fee"] = int(s.get("delivery_inside_dhaka", 60))
        total = ctx["subtotal"] + ctx["delivery_fee"]
        
        summary = f"🛒 আপনার অর্ডারের সামারি:\n\n🛍️ প্রোডাক্ট: {ctx['name']}\n🔢 পরিমাণ: {ctx['quantity']} টি\n💵 সর্বমোট বিল (ডেলিভারি ফি সহ): {total}৳\n\nসব তথ্য ঠিক থাকলে নিচের বাটনে চাপুন:"
        btns = {"type": "button", "body": {"text": summary}, "action": {"buttons": [{"type": "reply", "reply": {"id": "conf_yes", "title": "অর্ডার কনফার্ম করুন 👍"}}, {"type": "reply", "reply": {"id": "conf_no", "title": "বাতিল করুন ❌"}}]}}
        db_query("UPDATE sessions SET state='awaiting_confirmation', context=? WHERE phone=?", (json.dumps(ctx), from_number), commit=True)
        send_whatsapp(from_number, "interactive", btns)
        return

    if state == "awaiting_confirmation":
        if body_text == "conf_yes":
            total_cod = ctx["subtotal"] + ctx["delivery_fee"]
            db_query("INSERT INTO orders (phone, name, address, product_id, quantity, total, delivery_fee, pathao_consignment_id, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING_BOOKING', 'pending')", 
                     (from_number, ctx["cust_name"], ctx["address"], ctx["product_id"], ctx["quantity"], total_cod, ctx["delivery_fee"]), commit=True)
            send_whatsapp(from_number, "text", "🎉 অভিনন্দন! আপনার অর্ডারটি সিস্টেমে নেওয়া হয়েছে। আমাদের প্রতিনিধি দ্রুত কল করে কনফার্ম করবেন।")
        db_query("UPDATE sessions SET state='idle', context='{}' WHERE phone=?", (from_number,), commit=True)
        return

    ai_msg = get_ai_answer(body_text)
    send_whatsapp(from_number, "text", ai_msg)

# =====================================================================
# MASTER DASHBOARD HTML
# =====================================================================
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8"><title>Ultimate Bot Control Station</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
</head>
<body class="bg-slate-900 text-slate-100 flex min-h-screen font-sans antialiased">
    
    <div class="w-72 bg-slate-950 border-r border-slate-800 flex flex-col">
        <div class="p-6 border-b border-slate-800 bg-slate-950 text-center">
            <h1 class="text-xl font-black text-indigo-400 tracking-wider"><i class="fa-solid fa-robot mr-2"></i>{{ settings.get('business_name') }}</h1>
            <span class="text-[10px] bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 rounded-full px-2 py-0.5 mt-1 inline-block">Render Engine Active</span>
        </div>
        <nav class="flex-1 p-4 space-y-1">
            <button onclick="switchTab('orders')" class="tab-btn w-full flex items-center justify-between px-4 py-3 rounded-xl bg-indigo-600 text-white font-bold transition"><span class="flex items-center gap-3"><i class="fa-solid fa-wallet"></i> অর্ডার প্যানেল</span><span class="text-xs bg-black/30 px-2 py-0.5 rounded-md">{{ orders|length }}</span></button>
            <button onclick="switchTab('livechat')" class="tab-btn w-full flex items-center gap-3 px-4 py-3 rounded-xl text-slate-400 hover:bg-slate-800/50 hover:text-white transition"><i class="fa-solid fa-comments"></i> লাইভ চ্যাট ইনবক্স</button>
            <button onclick="switchTab('inventory')" class="tab-btn w-full flex items-center gap-3 px-4 py-3 rounded-xl text-slate-400 hover:bg-slate-800/50 hover:text-white transition"><i class="fa-solid fa-box-open"></i> প্রোডাক্ট ও ফেসবুক সিঙ্ক</button>
            <button onclick="switchTab('config')" class="tab-btn w-full flex items-center gap-3 px-4 py-3 rounded-xl text-slate-400 hover:bg-slate-800/50 hover:text-white transition"><i class="fa-solid fa-sliders"></i> এআই ও গেটওয়ে সেটিংস</button>
        </nav>
    </div>

    <div class="flex-1 flex flex-col min-w-0 bg-slate-900">
        {% if msg %}
        <div class="m-6 p-4 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 font-bold rounded-xl text-sm flex items-center gap-2"><i class="fa-solid fa-circle-check"></i> {{ msg }}</div>
        {% endif %}

        <div class="p-8 flex-1 overflow-y-auto">
            <div id="tab-orders" class="tab-content space-y-6">
                <div class="flex justify-between items-center border-b border-slate-800 pb-4">
                    <h2 class="text-2xl font-black">অর্ডার ট্র্যাকিং ও বুকিং কন্ট্রোল</h2>
                </div>
                <div class="bg-slate-950 rounded-2xl border border-slate-800 overflow-hidden shadow-2xl">
                    <table class="w-full text-left text-sm">
                        <thead>
                            <tr class="bg-slate-900 border-b border-slate-800 text-xs text-slate-400 uppercase"><th class="p-4">Memo / Customer</th><th class="p-4">Address</th><th class="p-4">COD Total</th><th class="p-4">Courier ID</th><th class="p-4 text-right">Actions</th></tr>
                        </thead>
                        <tbody>
                            {% for o in orders %}
                            <tr class="border-b border-slate-800/60 hover:bg-slate-800/20">
                                <td class="p-4"><span class="font-mono text-indigo-400 font-bold">#{{ o.id }}</span><br><b class="text-white">{{ o.name }}</b><br><span class="text-xs text-slate-500">{{ o.phone }}</span></td>
                                <td class="p-4 text-xs max-w-xs truncate">{{ o.address }}</td>
                                <td class="p-4 font-bold text-emerald-400">{{ o.total }}৳</td>
                                <td class="p-4"><span class="px-2 py-0.5 rounded bg-slate-800 text-xs font-mono text-slate-300">{{ o.pathao_consignment_id }}</span></td>
                                <td class="p-4 text-right space-x-1">
                                    <a href="/invoice/{{ o.id }}" target="_blank" class="p-2 bg-slate-800 hover:bg-slate-700 rounded-xl text-slate-300 text-xs"><i class="fa-solid fa-print"></i> মেমো</a>
                                    {% if o.status == 'pending' %}
                                    <a href="/admin/order/book/{{ o.id }}" class="p-2 bg-indigo-600 hover:bg-indigo-500 rounded-xl text-white text-xs font-bold"><i class="fa-solid fa-truck-fast"></i> Pathao Book</a>
                                    {% endif %}
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>

            <div id="tab-livechat" class="tab-content hidden grid grid-cols-1 lg:grid-cols-3 gap-6 h-[70vh]">
                <div class="bg-slate-950 rounded-2xl border border-slate-800 p-4 flex flex-col">
                    <h3 class="font-bold border-b border-slate-800 pb-3 mb-3 text-slate-300">অ্যাক্টিভ কাস্টমারস</h3>
                    <div class="flex-1 overflow-y-auto space-y-2">
                        {% for u in users %}
                        <a href="/admin?chat_with={{ u.phone }}#livechat" class="block p-3 rounded-xl bg-slate-900 border border-slate-800 hover:border-indigo-500/50 transition">
                            <div class="font-bold text-white text-sm">{{ u.phone }}</div>
                        </a>
                        {% endfor %}
                    </div>
                </div>
                <div class="lg:col-span-2 bg-slate-950 rounded-2xl border border-slate-800 flex flex-col overflow-hidden">
                    <div class="p-4 bg-slate-900 border-b border-slate-800 flex justify-between items-center">
                        <div class="font-bold text-indigo-400">💬 চ্যাটবক্স: {{ active_chat or 'সিলেক্ট করুন' }}</div>
                        {% if active_chat %}
                        <a href="/admin/chat/toggle-bot/{{ active_chat }}" class="px-3 py-1 bg-amber-500 text-slate-950 rounded-lg text-xs font-bold">বট পজ/অন করুন</a>
                        {% endif %}
                    </div>
                    <div class="flex-1 p-4 overflow-y-auto space-y-3 flex flex-col">
                        {% for m in chat_history %}
                        <div class="max-w-md p-3 rounded-2xl text-xs {% if m.direction == 'inbound' %}bg-slate-800 text-white self-start{% else %}bg-indigo-600 text-white self-end{% endif %}">
                            <div>{{ m.content }}</div>
                        </div>
                        {% endfor %}
                    </div>
                    {% if active_chat %}
                    <form action="/admin/chat/send" method="POST" class="p-3 bg-slate-900 border-t border-slate-800 flex gap-2">
                        <input type="hidden" name="phone" value="{{ active_chat }}">
                        <input type="text" name="message" placeholder="এখানে রিপ্লাই লিখুন..." class="flex-1 bg-slate-950 border border-slate-800 rounded-xl p-3 text-sm text-white focus:outline-none">
                        <button type="submit" class="bg-indigo-600 text-white px-5 rounded-xl text-sm font-bold hover:bg-indigo-500"><i class="fa-solid fa-paper-plane"></i></button>
                    </form>
                    {% endif %}
                </div>
            </div>

            <div id="tab-inventory" class="tab-content hidden space-y-6">
                <div class="bg-gradient-to-r from-indigo-950 to-blue-950 border border-indigo-500/20 p-6 rounded-2xl flex justify-between items-center shadow-xl">
                    <div>
                        <h3 class="text-lg font-black text-white">মেটা ক্যাটালগ অটো সিঙ্ক</h3>
                    </div>
                    <a href="/admin/sync-facebook-trigger" class="bg-indigo-600 hover:bg-indigo-500 text-white font-bold px-6 py-3 rounded-xl text-sm shadow-lg transition">Sync Meta Catalogue</a>
                </div>
                <div class="bg-slate-950 rounded-2xl border border-slate-800 overflow-hidden">
                    <table class="w-full text-left text-sm">
                        <thead>
                            <tr class="bg-slate-900/30 border-b border-slate-800 text-xs text-slate-400"><th class="p-4">FB Product ID</th><th class="p-4">Image</th><th class="p-4">Details</th><th class="p-4">Price</th></tr>
                        </thead>
                        <tbody>
                            {% for p in products %}
                            <tr class="border-b border-slate-800/40 hover:bg-slate-800/10">
                                <td class="p-4 font-mono text-xs text-slate-500">{{ p.fb_product_id or 'Manual' }}</td>
                                <td class="p-4"><img src="{{ p.image_url }}" class="h-10 w-10 object-cover rounded-lg"></td>
                                <td class="p-4"><b class="text-white">{{ p.name }}</b></td>
                                <td class="p-4 font-bold text-emerald-400">{{ p.price }}৳</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>

            <div id="tab-config" class="tab-content hidden bg-slate-950 rounded-2xl border border-slate-800 p-6">
                <div class="font-bold text-lg text-slate-300 mb-6 border-b border-slate-800 pb-3">সিস্টেম প্যারামিটার কনফিগ</div>
                <form action="/admin/settings/save" method="POST" class="space-y-6">
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div><label class="block text-xs font-bold text-slate-400 uppercase mb-2">Business Brand Name</label><input type="text" name="business_name" value="{{ settings.get('business_name', '') }}" class="w-full bg-slate-900 border border-slate-800 p-3 rounded-xl text-sm text-white focus:outline-none"></div>
                        <div><label class="block text-xs font-bold text-slate-400 uppercase mb-2">WhatsApp Phone ID</label><input type="text" name="phone_number_id" value="{{ settings.get('phone_number_id', '') }}" class="w-full bg-slate-900 border border-slate-800 p-3 rounded-xl text-sm text-white focus:outline-none"></div>
                        <div class="md:col-span-2"><label class="block text-xs font-bold text-slate-400 uppercase mb-2">WhatsApp Permanent Token</label><input type="password" name="permanent_token" value="{{ settings.get('permanent_token', '') }}" class="w-full bg-slate-900 border border-slate-800 p-3 rounded-xl text-sm text-white focus:outline-none"></div>
                        
                        <div class="md:col-span-2 p-5 bg-indigo-950/30 border border-indigo-500/20 rounded-xl space-y-4">
                            <div class="font-bold text-xs text-indigo-400 uppercase">Google Gemini AI Config</div>
                            <div><label class="block text-xs text-slate-400 mb-1">Gemini API Key</label><input type="password" name="gemini_key" value="{{ settings.get('gemini_key', '') }}" class="w-full bg-slate-900 border border-slate-800 p-2.5 rounded-xl text-sm text-white"></div>
                            <div><label class="block text-xs text-slate-400 mb-1">AI Prompt Instruction</label><textarea name="ai_system_instruction" rows="3" class="w-full bg-slate-900 border border-slate-800 p-2.5 rounded-xl text-sm text-white">{{ settings.get('ai_system_instruction', '') }}</textarea></div>
                        </div>

                        <div class="p-5 bg-blue-950/20 border border-blue-500/20 rounded-xl space-y-4">
                            <div class="font-bold text-xs text-blue-400 uppercase">Meta Commerce Catalog ID</div>
                            <div><label class="block text-xs text-slate-400 mb-1">Facebook Catalogue ID</label><input type="text" name="fb_catalogue_id" value="{{ settings.get('fb_catalogue_id', '') }}" class="w-full bg-slate-900 border border-slate-800 p-2.5 rounded-xl text-sm text-white"></div>
                            <div><label class="block text-xs text-slate-400 mb-1">Meta Access Token</label><input type="password" name="fb_access_token" value="{{ settings.get('fb_access_token', '') }}" class="w-full bg-slate-900 border border-slate-800 p-2.5 rounded-xl text-sm text-white"></div>
                        </div>

                        <div class="p-5 bg-emerald-950/20 border border-emerald-500/20 rounded-xl space-y-4">
                            <div class="font-bold text-xs text-emerald-400 uppercase">Pathao Courier Integration</div>
                            <div class="grid grid-cols-2 gap-2">
                                <div><label class="block text-xs text-slate-400 mb-1">Store ID</label><input type="text" name="pathao_store_id" value="{{ settings.get('pathao_store_id', '') }}" class="w-full bg-slate-900 border border-slate-800 p-2 rounded-xl text-xs text-white"></div>
                                <div><label class="block text-xs text-slate-400 mb-1">Client ID</label><input type="text" name="pathao_client_id" value="{{ settings.get('pathao_client_id', '') }}" class="w-full bg-slate-900 border border-slate-800 p-2 rounded-xl text-xs text-white"></div>
                            </div>
                            <div><label class="block text-xs text-slate-400 mb-1">Merchant Email</label><input type="text" name="pathao_merchant_email" value="{{ settings.get('pathao_merchant_email', '') }}" class="w-full bg-slate-900 border border-slate-800 p-2 rounded-xl text-xs text-white"></div>
                            <div><label class="block text-xs text-slate-400 mb-1">Merchant Password</label><input type="password" name="pathao_merchant_password" value="{{ settings.get('pathao_merchant_password', '') }}" class="w-full bg-slate-900 border border-slate-800 p-2 rounded-xl text-xs text-white"></div>
                        </div>
                    </div>
                    <button type="submit" class="w-full bg-indigo-600 text-white font-bold p-4 rounded-xl text-sm hover:bg-indigo-500 transition">Save Configurations</button>
                </form>
            </div>
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
# SYSTEM ENDPOINTS
# =====================================================================
@app.route("/admin", methods=["GET"])
def admin_portal():
    f = request.args.get("filter", "all")
    query = "SELECT * FROM orders ORDER BY id DESC" if f == "all" else f"SELECT * FROM orders WHERE status='{f}' ORDER BY id DESC"
    orders = db_query(query, fetchall=True) or []
    products = db_query("SELECT * FROM products ORDER BY id DESC", fetchall=True) or []
    users = db_query("SELECT * FROM users ORDER BY last_active DESC LIMIT 30", fetchall=True) or []
    settings = get_all_settings()
    
    active_chat = request.args.get("chat_with", "")
    chat_history = db_query("SELECT * FROM messages WHERE from_number=? ORDER BY id ASC LIMIT 50", (active_chat,), fetchall=True) if active_chat else []
    
    return render_template_string(ADMIN_HTML, orders=orders, products=products, users=users, settings=settings, active_chat=active_chat, chat_history=chat_history, msg=request.args.get("msg", ""))

@app.route("/admin/settings/save", methods=["POST"])
def save_settings():
    for k, v in request.form.items():
        db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v.strip()), commit=True)
    return redirect(url_for('admin_portal', msg="কনফিগারেশন আপডেট সফল হয়েছে!") + "#config")

@app.route("/admin/sync-facebook-trigger")
def manual_fb_sync():
    suc, detail = sync_facebook_catalogue()
    return redirect(url_for('admin_portal', msg=detail) + "#inventory")

@app.route("/admin/chat/send", methods=["POST"])
def admin_send_message():
    phone = request.form.get("phone")
    msg = request.form.get("message")
    if phone and msg:
        send_whatsapp(phone, "text", msg, agent="human_admin")
        db_query("UPDATE sessions SET bot_paused = 1 WHERE phone = ?", (phone,), commit=True)
    return redirect(url_for('admin_portal', chat_with=phone) + "#livechat")

@app.route("/admin/chat/toggle-bot/<phone>")
def toggle_bot_pause(phone):
    s = db_query("SELECT bot_paused FROM sessions WHERE phone=?", (phone,), fetchone=True)
    nxt = 0 if s and s["bot_paused"] == 1 else 1
    db_query("UPDATE sessions SET bot_paused = ? WHERE phone = ?", (nxt, phone), commit=True)
    return redirect(url_for('admin_portal', chat_with=phone, msg=f"বট স্ট্যাটাস পরিবর্তন সফল!") + "#livechat")

@app.route("/admin/order/book/<int:order_id>")
def book_pathao(order_id):
    order = db_query("SELECT * FROM orders WHERE id = ?", (order_id,), fetchone=True)
    prod = db_query("SELECT name FROM products WHERE id=?", (order["product_id"],), fetchone=True)
    o_ctx = {"cust_name": order["name"], "address": order["address"], "quantity": order["quantity"], "name": prod["name"] if prod else "Ecom Item"}
    
    success, res = create_pathao_order(o_ctx, order["phone"], order["total"])
    if success:
        db_query("UPDATE orders SET pathao_consignment_id=?, status='approved' WHERE id=?", (res, order_id), commit=True)
        return redirect(url_for('admin_portal', msg=f"পাঠাও বুকিং সফল! কনসাইনমেন্ট আইডি: {res}"))
    return redirect(url_for('admin_portal', msg=f"পাঠাও এরর: {res}"))

@app.route("/invoice/<int:order_id>")
def print_invoice(order_id):
    order = db_query("SELECT * FROM orders WHERE id = ?", (order_id,), fetchone=True)
    if not order: return "মেমো পাওয়া যায়নি", 404
    s = get_all_settings()
    prod = db_query("SELECT name, price FROM products WHERE id=?", (order["product_id"],), fetchone=True)
    
    html = f"""
    <html><head><title>Invoice #{order['id']}</title><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-white p-10 text-slate-800" onload="window.print()">
        <div class="max-w-xl mx-auto border p-8 rounded-lg shadow-sm">
            <div class="flex justify-between items-center border-b pb-6">
                <div><h1 class="text-2xl font-black text-indigo-600">{s.get('business_name')}</h1><p class="text-xs text-slate-500">অফিসিয়াল ক্যাশ মেমো</p></div>
                <div class="text-right"><h2 class="text-lg font-bold">মেমো নং: #{order['id']}</h2><p class="text-xs text-slate-500">তারিখ: {order['created_at']}</p></div>
            </div>
            <div class="my-6 text-sm"><b class="text-slate-900">ডেলিভারি ঠিকানা:</b><p>{order['name']}</p><p>{order['phone']}</p><p>{order['address']}</p></div>
            <table class="w-full text-left text-xs mb-6 border-collapse">
                <tr class="bg-slate-100 font-bold border-b"><th class="p-2">আইটেম বিবরণ</th><th class="p-2">পরিমাণ</th><th class="p-2 text-right">মূল্য</th></tr>
                <tr class="border-b"><td class="p-2">{prod['name'] if prod else 'Product Item'}</td><td class="p-2">{order['quantity']} টি</td><td class="p-2 text-right">{prod['price'] if prod else 0}৳</td></tr>
            </table>
            <div class="text-right text-xs space-y-1 font-semibold border-t pt-4">
                <p>ডেলিভারি ফি: {order['delivery_fee']}৳</p>
                <p class="text-lg font-black text-indigo-600">সর্বমোট প্রদেয় বিল (COD): {order['total']}৳</p>
            </div>
        </div>
    </body></html>
    """
    return html

@app.route("/webhook", methods=["GET"])
def verify():
    s = get_all_settings()
    if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == s.get("verify_token", "dhakaex0020"):
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
