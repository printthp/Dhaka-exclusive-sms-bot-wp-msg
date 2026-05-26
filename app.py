import os
import sys
import json
import sqlite3
import logging
import ctypes
import time
import requests
from datetime import datetime
from threading import Thread, Lock

from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session

# =====================================================================
# LOGGING
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# =====================================================================
# FLASK APP SETUP
# =====================================================================
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY")
if not app.secret_key:
    raise ValueError("SECRET_KEY environment variable is required. Set a strong random string.")

# For Gunicorn deployment on Render
application = app

# =====================================================================
# C++ ENGINE LOADER
# =====================================================================
lib = None
try:
    so_candidates = ["engine.so", "core_engine.so"]
    for candidate in so_candidates:
        if os.path.exists(candidate):
            lib = ctypes.CDLL(os.path.abspath(candidate))
            lib.process_business_logic.restype = ctypes.c_char_p
            logger.info(f"C++ Engine loaded: {candidate}")
            break
    if not lib:
        logger.warning("No C++ engine .so file found")
except Exception as e:
    logger.error(f"C++ Engine Load Error: {e}")

# =====================================================================
# ASSEMBLY ENGINE LOADER
# =====================================================================
#asm_lib = None
#try:
    #if os.path.exists("asm_engine.so"):
        #asm_lib = ctypes.CDLL(os.path.abspath("asm_engine.so"))
        #asm_lib.asm_process_command.restype = ctypes.c_char_p
        #asm_lib.asm_strlen.restype = ctypes.c_uint64
        #asm_lib.asm_checksum.restype = ctypes.c_uint64
        #logger.info("Assembly Engine loaded: asm_engine.so")
    #else:
        #logger.warning("No Assembly engine .so file found")
#except Exception as e:
    #logger.error(f"Assembly Engine Load Error: {e}")

# =====================================================================
# HYBRID ENGINES
# =====================================================================
class CppEngine:
    def process(self, command):
        if not lib:
            return "C++ Engine Not Found"
        try:
            res = lib.process_business_logic(command.encode("utf-8"))
            return res.decode("utf-8", errors="replace") if res else "No response"
        except Exception as e:
            return f"C++ Engine Error: {str(e)}"

class AsmEngine:
    def process(self, command):
        if not asm_lib:
            return "Assembly Engine Not Found"
        try:
            res = asm_lib.asm_process_command(command.encode("utf-8"))
            return res.decode("utf-8", errors="replace") if res else "No response"
        except Exception as e:
            return f"Assembly Engine Error: {str(e)}"
    def strlen(self, text):
        if not asm_lib:
            return 0
        return asm_lib.asm_strlen(text.encode("utf-8"))
    def checksum(self, text):
        if not asm_lib:
            return 0
        return asm_lib.asm_checksum(text.encode("utf-8"))

cpp_engine = CppEngine()
asm_engine = AsmEngine()

# =====================================================================
# DATABASE
# =====================================================================
DB_FILE = "bot_v7_ultimate.db"
db_lock = Lock()

def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
        c.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, msg_id TEXT UNIQUE, from_number TEXT, content TEXT, msg_type TEXT DEFAULT 'text', direction TEXT DEFAULT 'inbound', agent_id TEXT DEFAULT 'system', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS sessions (phone TEXT PRIMARY KEY, state TEXT DEFAULT 'idle', context TEXT DEFAULT '{}', last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP, recovered INTEGER DEFAULT 0, bot_paused INTEGER DEFAULT 0)")
        c.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, name TEXT, address TEXT, city_id INTEGER DEFAULT 1, zone_id INTEGER DEFAULT 1, area_id INTEGER DEFAULT 1, product_id INTEGER, quantity INTEGER DEFAULT 1, total INTEGER, delivery_fee INTEGER, pathao_consignment_id TEXT, status TEXT DEFAULT 'pending', agent_name TEXT DEFAULT 'System', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, fb_product_id TEXT UNIQUE, name TEXT, price INTEGER, description TEXT, stock INTEGER DEFAULT 10, active INTEGER DEFAULT 1, image_url TEXT DEFAULT '')")
        c.execute("CREATE TABLE IF NOT EXISTS users (phone TEXT PRIMARY KEY, name TEXT DEFAULT 'Customer', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS agents (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT, role TEXT DEFAULT 'representative', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS agent_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, action TEXT, details TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS complaints (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, complaint_text TEXT, status TEXT DEFAULT 'pending', resolved_by TEXT DEFAULT '', resolution_notes TEXT DEFAULT '', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        
        defaults = [
            ("business_name", "Dhaka Exclusive"),
            ("permanent_token", ""),
            ("phone_number_id", ""),
            ("gemini_key", ""),
            ("verify_token", "dhakaex0020"),
            ("fb_catalogue_id", ""),
            ("fb_access_token", ""),
            ("ai_system_instruction", "আপনি একজন প্রফেশনাল কাস্টমার অ্যাসিস্ট্যান্ট। কাস্টমারের সাথে বাংলায় বিনীতভাবে কথা বলুন এবং প্রোডাক্ট কিনতে সাহায্য করুন।"),
            ("pathao_base_url", "https://api-hermes.pathao.com"),
            ("pathao_store_id", ""),
            ("pathao_client_id", ""),
            ("pathao_client_secret", ""),
            ("pathao_merchant_email", ""),
            ("pathao_merchant_password", ""),
            ("delivery_inside_dhaka", "60"),
            ("delivery_outside_dhaka", "120")
        ]
        for k, v in defaults:
            c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
            
        c.execute("INSERT OR IGNORE INTO agents (username, password, role) VALUES ('admin', 'admin123', 'admin')")
        c.execute("INSERT OR IGNORE INTO agents (username, password, role) VALUES ('agent1', 'agent123', 'representative')")
        
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
DEFAULT_PRODUCT_IMAGE = "https://i.postimg.cc/ydG2D187/Adobe-Express-file.png"

def sync_facebook_catalogue():
    s = get_all_settings()
    cat_id = s.get("fb_catalogue_id")
    token = s.get("fb_access_token")
    if not cat_id or not token:
        return False, "ফেসবুক ক্যাটালগ আইডি বা অ্যাক্সেস টোকেন সেটিংস থেকে মিসিং!"
    
    url = f"https://graph.facebook.com/v21.0/{cat_id}/products"
    params = {"fields": "id,name,price,description,image_url,image_cdn_urls,images{url}", "access_token": token, "limit": 100}
    try:
        r = requests.get(url, params=params, timeout=15)
        res = r.json()
        if "data" not in res:
            return False, res.get("error", {}).get("message", "Unknown Meta Error")
        
        sync_count = 0
        for item in res["data"]:
            fb_id = item.get("id")
            name = item.get("name")
            desc = item.get("description", "No description")
            
            img_url = item.get("image_url", "") or item.get("imageUrl", "")
            if not img_url and item.get("image_cdn_urls"):
                icu = item["image_cdn_urls"]
                if isinstance(icu, list) and len(icu) > 0:
                    img_url = icu[0]
                elif isinstance(icu, str):
                    img_url = icu.split(",")[0].strip()
            if not img_url and item.get("images"):
                imgs = item["images"]
                if isinstance(imgs, list) and len(imgs) > 0:
                    first = imgs[0]
                    img_url = first.get("url", "") if isinstance(first, dict) else str(first)
            if not img_url:
                img_url = DEFAULT_PRODUCT_IMAGE
            
            raw_price = item.get("price", "0")
            price = 0
            try:
                if isinstance(raw_price, dict):
                    price = int(raw_price.get("amount", 0))
                elif isinstance(raw_price, str):
                    digits = "".join([c for c in raw_price if c.isdigit() or c == '.'])
                    price = int(float(digits)) if digits else 0
                elif isinstance(raw_price, (int, float)):
                    price = int(raw_price)
            except:
                price = 0
            if price <= 0:
                price = 100
            
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
            "client_id": s.get("pathao_client_id"),
            "client_secret": s.get("pathao_client_secret"),
            "username": s.get("pathao_merchant_email"),
            "password": s.get("pathao_merchant_password"),
            "grant_type": "password"
        }, headers={"content-type": "application/json"}, timeout=10)
        res_data = r.json()
        token = res_data.get("access_token") or res_data.get("token")
        return token, None
    except Exception as e:
        return None, str(e)

def create_pathao_order(order_ctx, phone, total_cod):
    token, err = get_pathao_token()
    if not token:
        return False, f"Pathao Token Error: {err}"
    s = get_all_settings()
    try:
        payload = {
            "store_id": int(s.get("pathao_store_id", 0)),
            "recipient_name": order_ctx["cust_name"],
            "recipient_phone": phone,
            "recipient_address": order_ctx["address"],
            "recipient_city": 1,
            "recipient_zone": 1,
            "recipient_area": 1,
            "delivery_type": 48,
            "item_type": 2,
            "special_instruction": "Bot Auto Order",
            "item_quantity": int(order_ctx["quantity"]),
            "amount_to_collect": int(total_cod),
            "item_description": order_ctx["name"]
        }
        r = requests.post(f"{s.get('pathao_base_url')}/aladdin/api/v1/orders", json=payload,
                          headers={"authorization": f"Bearer {token}", "content-type": "application/json"}, timeout=15)
        if r.status_code == 200 and r.json().get("status") == 200:
            return True, r.json().get("data", {}).get("consignment_id")
        return False, r.json().get("message", "Booking failed")
    except Exception as e:
        return False, str(e)


# =====================================================================
# WHATSAPP SENDER & AI ENGINE
# =====================================================================
def send_whatsapp(to, payload_type, content, extra=None, agent="system"):
    s = get_all_settings()
    token = s.get("permanent_token")
    phone_id = s.get("phone_number_id")
    if not token or not phone_id:
        return False
    
    url = f"https://graph.facebook.com/v21.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"messaging_product": "whatsapp", "to": to, "type": payload_type}
    if payload_type == "text":
        body["text"] = {"body": content}
    elif payload_type == "image":
        body["image"] = {"link": content, "caption": extra or ""}
    elif payload_type == "interactive":
        body["interactive"] = content
    
    try:
        r = requests.post(url, json=body, headers=headers, timeout=10)
        if r.status_code in [200, 201]:
            gen_id = r.json().get("messages", [{}])[0].get("id", f"out_{int(time.time())}")
            db_query("INSERT INTO messages (msg_id, from_number, content, msg_type, direction, agent_id) VALUES (?, ?, ?, ?, 'outbound', ?)",
                     (gen_id, to, str(content), payload_type, agent), commit=True)
            return True
        return False
    except:
        return False

def get_ai_answer(user_query, chat_history_str=""):
    s = get_all_settings()
    key = s.get("gemini_key")
    if not key:
        return "আমাদের কাস্টমার রিপ্রেজেন্টেটিভ খুব দ্রুত আপনার সাথে যোগাযোগ করবেন।"
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key)
        p_rows = db_query("SELECT * FROM products WHERE active = 1 AND stock > 0", fetchall=True) or []
        catalog = "\n".join([f"- {p['name']}: {p['price']}৳ ({p['description']})" for p in p_rows])
        si = f"{s.get('ai_system_instruction')}\n\nচলতি প্রোডাক্ট ক্যাটালগ:\n{catalog}"
        cfg = types.GenerateContentConfig(system_instruction=si, temperature=0.3, max_output_tokens=300)
        
        full_prompt = f"চ্যাটের পূর্ববর্তী প্রসঙ্গ:\n{chat_history_str}\n\nকাস্টমারের বর্তমান মেসেজ: {user_query}"
        return client.models.generate_content(model="gemini-2.5-flash", contents=full_prompt, config=cfg).text
    except Exception as e:
        return "আপনার মেসেজটি আমাদের প্যানেলে জমা হয়েছে। লাইভ এজেন্ট কিছুক্ষণের মধ্যে উত্তর দেবে।"

def send_main_menu_buttons(from_number, text_content="Dhaka Exclusive এ আপনাকে স্বাগতম! নিচে থেকে আপনার প্রয়োজনীয় বাটনটি সিলেক্ট করুন:"):
    btns = {
        "type": "button",
        "body": {"text": text_content},
        "action": {
            "buttons": [
                {"type": "reply", "reply": {"id": "menu_products", "title": "🛒 প্রোডাক্ট দেখুন"}},
                {"type": "reply", "reply": {"id": "menu_call", "title": "📞 কল রিকোয়েস্ট"}},
                {"type": "reply", "reply": {"id": "menu_complain", "title": "⚠️ কমপ্লেইন বক্স"}}
            ]
        }
    }
    send_whatsapp(from_number, "interactive", btns)

# =====================================================================
# INBOUND STATE MACHINE
# =====================================================================
def process_webhook_async(msg, from_number):
    body_text = msg.get("text", {}).get("body", "").strip().lower()
    
    db_query("INSERT INTO users (phone, last_active) VALUES (?, CURRENT_TIMESTAMP) ON CONFLICT(phone) DO UPDATE SET last_active = CURRENT_TIMESTAMP", (from_number,), commit=True)
    
    sess = db_query("SELECT * FROM sessions WHERE phone = ?", (from_number,), fetchone=True)
    if sess and sess.get("bot_paused") == 1:
        return
    
    state = sess["state"] if sess else "idle"
    ctx = json.loads(sess["context"]) if sess and sess.get("context") else {}
    
    # Global menu button handlers
    if body_text == "menu_products" or (state == "idle" and any(k in body_text.lower() for k in ["কিনব", "অর্ডার", "buy", "order", "প্রোডাক্ট"])):
        products = db_query("SELECT * FROM products WHERE active = 1 AND stock > 0 LIMIT 10", fetchall=True) or []
        if not products:
            send_whatsapp(from_number, "text", "দুঃখিত ভাই, আমাদের স্টক এখন খালি। খুব দ্রুত নতুন স্টক আসবে।")
            return
        rows = [{"id": f"p_{p['id']}", "title": p['name'][:24], "description": f"{p['price']}৳"} for p in products]
        menu = {"type": "list", "body": {"text": "আমাদের ক্যাটালগ থেকে প্রোডাক্ট সিলেক্ট করুন:"}, "action": {"button": "প্রোডাক্টস লিস্ট", "sections": [{"title": "চলতি স্টক", "rows": rows}]}}
        db_query("INSERT INTO sessions (phone, state, context, bot_paused) VALUES (?, 'selecting_product', '{}', 0) ON CONFLICT(phone) DO UPDATE SET state='selecting_product', context='{}'", (from_number,), commit=True)
        send_whatsapp(from_number, "interactive", menu)
        return
    
    if body_text == "menu_call":
        db_query("INSERT INTO orders (phone, name, address, product_id, quantity, total, delivery_fee, pathao_consignment_id, status) VALUES (?, 'Call Request', 'Customer requested a callback', 0, 0, 0, 0, 'CALL_REQUEST', 'pending')", (from_number,), commit=True)
        db_query("UPDATE sessions SET state='idle', context='{}' WHERE phone=?", (from_number,), commit=True)
        send_whatsapp(from_number, "text", "📞 আপনার কল রিকোয়েস্টটি এডমিন প্যানেলে পাঠানো হয়েছে। আমাদের প্রতিনিধি খুব দ্রুত আপনাকে কল করবেন। ধন্যবাদ!")
        return
    
    if body_text == "menu_complain":
        db_query("INSERT INTO sessions (phone, state, context, bot_paused) VALUES (?, 'awaiting_complain', '{}', 0) ON CONFLICT(phone) DO UPDATE SET state='awaiting_complain'", (from_number,), commit=True)
        send_whatsapp(from_number, "text", "⚠️ আপনার অভিযোগটি দয়া করে বিস্তারিত লিখে মেসেজ আকারে পাঠান:")
        return
    
    if state == "awaiting_complain":
        db_query("INSERT INTO complaints (phone, complaint_text) VALUES (?, ?)", (from_number, body_text), commit=True)
        db_query("UPDATE sessions SET state='idle' WHERE phone=?", (from_number,), commit=True)
        send_whatsapp(from_number, "text", "✅ আপনার অভিযোগটি নথিভুক্ত করা হয়েছে। আমাদের কমপ্লেইন টিম এটি দ্রুত সমাধান করবে।")
        return
    
    # Product and order processing flow
    if state == "selecting_product" and body_text.startswith("p_"):
        pid = int(body_text.split("_")[1])
        p = db_query("SELECT * FROM products WHERE id = ?", (pid,), fetchone=True)
        if p:
            ctx = {"product_id": pid, "name": p["name"], "price": p["price"]}
            btns = {"type": "button", "body": {"text": f"🔹 {p['name']}\n💰 মূল্য: {p['price']}৳\n\nকত পিস নিতে চান?"}, "action": {"buttons": [{"type": "reply", "reply": {"id": "q_1", "title": "১ পিস"}}, {"type": "reply", "reply": {"id": "q_2", "title": "২ পিস"}}]}}
            db_query("UPDATE sessions SET state='selecting_qty', context=? WHERE phone=?", (json.dumps(ctx), from_number), commit=True)
            if p.get("image_url"):
                send_whatsapp(from_number, "image", p["image_url"], p["name"])
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
        
        summary = f"🛒 আপনার অর্ডারের সামারি:\n\n🛜d️ প্রোডাক্ট: {ctx['name']}\n🔢 পরিমাণ: {ctx['quantity']} টি\n💵 সর্বমোট বিল (ডেলিভারি ফি সহ): {total}৳\n\nসব তথ্য ঠিক থাকলে নিচের বাটনে চাপুন:"
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
        else:
            send_whatsapp(from_number, "text", "❌ আপনার অর্ডারটি বাতিল করা হয়েছে।")
        db_query("UPDATE sessions SET state='idle', context='{}' WHERE phone=?", (from_number,), commit=True)
        return
    
    # AI backup and context reading
    history_rows = db_query("SELECT content, direction FROM messages WHERE from_number=? ORDER BY id DESC LIMIT 5", (from_number,), fetchall=True) or []
    history_str = "\n".join([f"{'কাস্টমার' if r['direction']=='inbound' else 'অ্যাসিস্টান্ট'}: {r['content']}" for r in reversed(history_rows)])
    
    ai_msg = get_ai_answer(body_text, history_str)
    send_main_menu_buttons(from_number, ai_msg)


# =====================================================================
# FULLY RESPONSIVE ADMIN DASHBOARD
# =====================================================================
  ADMIN_HTML = """<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Ultimate Control Station</title>
<script src="https://cdn.tailwindcss.com"></script>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen font-sans antialiased flex flex-col md:flex-row">

<div class="w-full md:w-72 bg-slate-950 border-b md:border-b-0 md:border-r border-slate-800 flex flex-col">
<div class="p-5 border-b border-slate-800 bg-slate-950 flex justify-between items-center md:block text-center">
<h1 class="text-xl font-black text-indigo-400 tracking-wider flex items-center justify-center gap-2"><i class="fa-solid fa-robot"></i>{{ settings.get('business_name') }}</h1>
<div class="text-xs text-slate-400 mt-1">ইউজার: <span class="text-emerald-400 font-bold">{{ session.get('username', 'Guest') }}</span></div>
</div>
<nav class="p-3 grid grid-cols-2 md:flex md:flex-col gap-1 overflow-x-auto">
<button onclick="switchTab('orders')" class="tab-btn flex items-center gap-2 px-3 py-2.5 rounded-xl text-xs md:text-sm bg-indigo-600 text-white font-bold transition"><i class="fa-solid fa-wallet"></i> অর্ডার প্যানেল</button>
<button onclick="switchTab('livechat')" class="tab-btn flex items-center gap-2 px-3 py-2.5 rounded-xl text-xs md:text-sm text-slate-400 hover:bg-slate-800/50 transition"><i class="fa-solid fa-comments"></i> লাইভ ইনবক্স</button>
<button onclick="switchTab('complaints')" class="tab-btn flex items-center gap-2 px-3 py-2.5 rounded-xl text-xs md:text-sm text-slate-400 hover:bg-slate-800/50 transition"><i class="fa-solid fa-triangle-exclamation"></i> কমপ্লেইন বক্স</button>
<button onclick="switchTab('inventory')" class="tab-btn flex items-center gap-2 px-3 py-2.5 rounded-xl text-xs md:text-sm text-slate-400 hover:bg-slate-800/50 transition"><i class="fa-solid fa-box-open"></i> প্রোডাক্ট সিঙ্ক</button>
<button onclick="switchTab('agents')" class="tab-btn flex items-center gap-2 px-3 py-2.5 rounded-xl text-xs md:text-sm text-slate-400 hover:bg-slate-800/50 transition"><i class="fa-solid fa-users"></i> প্রতিনিধি ট্র্যাকার</button>
<button onclick="switchTab('config')" class="tab-btn flex items-center gap-2 px-3 py-2.5 rounded-xl text-xs md:text-sm text-slate-400 hover:bg-slate-800/50 transition"><i class="fa-solid fa-sliders"></i> সেটিংস</button>
<a href="/admin/logout" class="flex items-center gap-2 px-3 py-2.5 rounded-xl text-xs md:text-sm text-rose-400 hover:bg-rose-950/20 transition mt-auto"><i class="fa-solid fa-right-from-bracket"></i> লগআউট</a>
</nav>
</div>

<div class="flex-1 flex flex-col min-w-0 bg-slate-900 overflow-x-hidden">
{% if msg %}
<div class="m-4 md:m-6 p-4 bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 font-bold rounded-xl text-xs md:text-sm flex items-center gap-2"><i class="fa-solid fa-circle-check"></i> {{ msg }}</div>
{% endif %}

<div class="p-4 md:p-8 flex-1 overflow-y-auto">

<div id="tab-orders" class="tab-content space-y-6">
<h2 class="text-xl md:text-2xl font-black">অর্ডার ট্র্যাকিং ও বুকিং</h2>
<div class="bg-slate-950 rounded-2xl border border-slate-800 overflow-x-auto shadow-2xl">
<table class="w-full text-left text-xs md:text-sm min-w-[600px]">
<thead><tr class="bg-slate-900 border-b border-slate-800 text-slate-400 uppercase">
<th class="p-4">Customer</th><th class="p-4">Address</th><th class="p-4">COD Total</th><th class="p-4">Agent Assigned</th><th class="p-4 text-right">Actions</th>
</tr></thead>
<tbody>
{% for o in orders %}
<tr class="border-b border-slate-800/60 hover:bg-slate-800/20">
<td class="p-4">
<span class="font-mono text-indigo-400 font-bold">#{{ o.id }}</span>
{% if o.pathao_consignment_id == 'CALL_REQUEST' %}<span class="ml-2 px-1.5 py-0.5 bg-amber-500/20 text-amber-400 rounded text-[10px]">Call Request</span>{% endif %}<br>
<b class="text-white">{{ o.name }}</b><br><span class="text-xs text-slate-500">{{ o.phone }}</span>
</td>
<td class="p-4 text-xs max-w-xs truncate">{{ o.address }}</td>
<td class="p-4 font-bold text-emerald-400">{{ o.total }}৳</td>
<td class="p-4 text-slate-300 font-medium">{{ o.agent_name }}</td>
<td class="p-4 text-right space-y-1 md:space-y-0 md:space-x-1">
<a href="/invoice/{{ o.id }}" target="_blank" class="inline-block p-2 bg-slate-800 hover:bg-slate-700 rounded-xl text-slate-300 text-xs"><i class="fa-solid fa-print"></i></a>
{% if o.status == 'pending' and o.pathao_consignment_id != 'CALL_REQUEST' %}
<a href="/admin/order/book/{{ o.id }}" class="inline-block p-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-xl text-xs font-bold">Pathao Book</a>
{% endif %}
{% if o.status == 'pending' and o.pathao_consignment_id == 'CALL_REQUEST' %}
<a href="/admin/order/resolve-call/{{ o.id }}" class="inline-block p-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-xl text-xs font-bold">Call Done</a>
{% endif %}
</td>
</tr>
{% endfor %}
</tbody>
</table>
</div>
</div>

<div id="tab-livechat" class="tab-content hidden grid grid-cols-1 md:grid-cols-3 gap-4 h-full">
<div class="bg-slate-950 rounded-2xl border border-slate-800 p-4 h-[50vh] md:h-full overflow-y-auto">
<h3 class="text-xs font-bold text-slate-400 uppercase mb-4">কাস্টমার লিস্ট</h3>
<div class="space-y-2">
{% for u in users %}
<a href="/admin?chat_with={{ u.phone }}#livechat" class="block p-3 rounded-xl bg-slate-900 border border-slate-800 hover:border-indigo-500/50 transition">
<div class="font-bold text-white text-xs md:text-sm">{{ u.phone }}</div>
</a>
{% endfor %}
</div>
</div>
<div class="md:col-span-2 bg-slate-950 rounded-2xl border border-slate-800 flex flex-col h-[50vh] md:h-full overflow-hidden">
<div class="p-4 bg-slate-900 border-b border-slate-800 flex justify-between items-center text-xs md:text-sm">
<div class="font-bold text-indigo-400">💬 কাস্টমার: {{ active_chat or 'সিলেক্ট করুন' }}</div>
{% if active_chat %}<a href="/admin/chat/toggle-bot/{{ active_chat }}" class="px-2 py-1 bg-amber-500 text-slate-950 rounded-lg font-bold text-xs">বট পজ/অন</a>{% endif %}
</div>
<div class="flex-1 p-4 overflow-y-auto space-y-3 flex flex-col">
{% for m in chat_history %}
<div class="max-w-xs md:max-w-md p-3 rounded-2xl text-xs {% if m.direction == 'inbound' %}bg-slate-800 text-white self-start{% else %}bg-indigo-600 text-white self-end{% endif %}">
<div class="font-semibold text-[10px] text-slate-400 mb-0.5">{{ m.agent_id }}</div>
<div>{{ m.content }}</div>
</div>
{% endfor %}
</div>
{% if active_chat %}
<form action="/admin/chat/send" method="POST" class="p-3 bg-slate-900 border-t border-slate-800 flex gap-2">
<input type="hidden" name="phone" value="{{ active_chat }}">
<input type="text" name="message" placeholder="এখানে উত্তর লিখুন..." class="flex-1 bg-slate-950 border border-slate-800 rounded-xl p-3 text-xs md:text-sm text-white focus:outline-none">
<button type="submit" class="bg-indigo-600 text-white px-4 md:px-5 rounded-xl text-xs font-bold hover:bg-indigo-500"><i class="fa-solid fa-paper-plane"></i></button>
</form>
{% endif %}
</div>
</div>

<div id="tab-complaints" class="tab-content hidden space-y-6">
<h2 class="text-xl md:text-2xl font-black text-rose-400">⚠️ কাস্টমার কমপ্লেইন বক্স</h2>
<div class="bg-slate-950 rounded-2xl border border-slate-800 overflow-x-auto shadow-2xl">
<table class="w-full text-left text-xs md:text-sm min-w-[600px]">
<thead><tr class="bg-slate-900 border-b border-slate-800 text-slate-400"><th class="p-4">Customer</th><th class="p-4">Complaint Note</th><th class="p-4">Status</th><th class="p-4">Resolved By</th><th class="p-4 text-right">Action</th></tr></thead>
<tbody>
{% for c in complaints %}
<tr class="border-b border-slate-800/60 hover:bg-slate-800/20">
<td class="p-4 font-bold">{{ c.phone }}<br><span class="text-[10px] text-slate-500">{{ c.created_at }}</span></td>
<td class="p-4 text-xs max-w-xs whitespace-normal">{{ c.complaint_text }}</td>
<td class="p-4"><span class="px-2 py-0.5 rounded text-[11px] font-bold {% if c.status=='pending' %}bg-rose-500/20 text-rose-400{% else %}bg-emerald-500/20 text-emerald-400{% endif %}">{{ c.status.upper() }}</span></td>
<td class="p-4 text-xs"><b>{{ c.resolved_by or '-' }}</b><br><span class="text-slate-400 text-[11px]">{{ c.resolution_notes }}</span></td>
<td class="p-4 text-right">
{% if c.status == 'pending' %}
<form action="/admin/complaint/resolve/{{ c.id }}" method="POST" class="flex flex-col md:flex-row gap-1 justify-end">
<input type="text" name="notes" placeholder="সমাধান নোট লিখুন..." required class="bg-slate-900 border border-slate-800 rounded p-1 text-xs text-white">
<button type="submit" class="p-1.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded text-xs font-bold">Resolve</button>
</form>
{% else %}<span class="text-slate-500 text-xs"><i class="fa-solid fa-circle-check text-emerald-500"></i> Solved</span>{% endif %}
</td>
</tr>
{% endfor %}
</tbody>
</table>
</div>
</div>

<div id="tab-inventory" class="tab-content hidden space-y-6">
<div class="bg-gradient-to-r from-indigo-950 to-blue-950 border border-indigo-500/20 p-5 rounded-2xl flex flex-col md:flex-row justify-between items-center gap-4">
<h3 class="text-sm md:text-base font-black text-white">মেটা ক্যাটালগ অটো সিঙ্ক ইঞ্জিন</h3>
<a href="/admin/sync-facebook-trigger" class="w-full md:w-auto text-center bg-indigo-600 hover:bg-indigo-500 text-white font-bold px-6 py-3 rounded-xl text-xs transition shadow-lg">Sync Meta Catalogue</a>
</div>
<div class="bg-slate-950 rounded-2xl border border-slate-800 overflow-x-auto">
<table class="w-full text-left text-xs md:text-sm min-w-[500px]">
<thead><tr class="bg-slate-900 text-slate-400"><th class="p-4">Product ID</th><th class="p-4">Image</th><th class="p-4">Details</th><th class="p-4">Price</th><th class="p-4">Edit</th></tr></thead>
<tbody>
{% for p in products %}
<tr class="border-b border-slate-800/40" id="prod-row-{{ p.id }}">
<td class="p-4 font-mono text-xs text-slate-500">{{ p.fb_product_id or 'Manual' }}</td>
<td class="p-4"><img src="{{ p.image_url or DEFAULT_PRODUCT_IMAGE }}" class="h-10 w-10 object-cover rounded-lg" onerror="this.src='{{ DEFAULT_PRODUCT_IMAGE }}'"></td>
<td class="p-4">
<div class="view-mode" id="view-{{ p.id }}"><b class="text-white">{{ p.name }}</b><br><span class="text-xs text-slate-400">Stock: {{ p.stock }}</span></div>
<form class="edit-mode hidden" id="edit-{{ p.id }}" action="/admin/product/edit/{{ p.id }}" method="POST" style="display:none">
<input type="text" name="name" value="{{ p.name }}" class="w-full bg-slate-900 border border-slate-700 rounded p-1 text-xs text-white mb-1" required><br>
<input type="number" name="price" value="{{ p.price }}" class="w-20 bg-slate-900 border border-slate-700 rounded p-1 text-xs text-white mr-1" required>
<input type="number" name="stock" value="{{ p.stock }}" class="w-16 bg-slate-900 border border-slate-700 rounded p-1 text-xs text-white"><br>
<input type="text" name="image_url" value="{{ p.image_url or '' }}" placeholder="Image URL" class="w-full bg-slate-900 border border-slate-700 rounded p-1 text-xs text-white mt-1">
<button type="submit" class="mt-1 bg-emerald-600 hover:bg-emerald-500 text-white text-[10px] font-bold px-2 py-1 rounded">Save</button>
</form>
</td>
<td class="p-4 font-bold text-emerald-400">{{ p.price }}৳</td>
<td class="p-4"><button onclick="toggleEdit({{ p.id }})" class="text-xs bg-indigo-600 hover:bg-indigo-500 text-white px-2 py-1 rounded font-bold" id="btn-{{ p.id }}">Edit</button></td>
</tr>
{% endfor %}
</tbody>
</table>
</div>
</div>

<div id="tab-agents" class="tab-content hidden space-y-6">
<div class="grid grid-cols-1 md:grid-cols-3 gap-4">
<div class="bg-slate-950 p-5 rounded-2xl border border-slate-800">
<h3 class="text-slate-400 text-xs font-bold uppercase mb-4">নতুন প্রতিনিধি যোগ করুন</h3>
<form action="/admin/agents/add" method="POST" class="space-y-3">
<input type="text" name="username" placeholder="ইউজারনেম" required class="w-full bg-slate-900 border border-slate-800 p-2 rounded-xl text-xs text-white">
<input type="password" name="password" placeholder="পাসওয়ার্ড" required class="w-full bg-slate-900 border border-slate-800 p-2 rounded-xl text-xs text-white">
<button type="submit" class="w-full bg-indigo-600 p-2 text-xs font-bold rounded-xl text-white">Save Agent</button>
</form>
</div>
<div class="md:col-span-2 bg-slate-950 p-5 rounded-2xl border border-slate-800 overflow-x-auto">
<h3 class="text-slate-400 text-xs font-bold uppercase mb-4">প্রতিনিধিদের কর্মক্ষমতা লগ</h3>
<table class="w-full text-left text-xs">
<thead><tr class="bg-slate-900 text-slate-400"><th class="p-2">Agent Name</th><th class="p-2">Action</th><th class="p-2">Details</th><th class="p-2">Time</th></tr></thead>
<tbody>
{% for l in agent_logs %}
<tr class="border-b border-slate-800/50">
<td class="p-2 font-bold text-indigo-400">{{ l.username }}</td>
<td class="p-2"><span class="px-1.5 py-0.5 rounded bg-slate-800 font-mono text-[10px]">{{ l.action }}</span></td>
<td class="p-2 text-slate-300 max-w-xs truncate">{{ l.details }}</td>
<td class="p-2 text-slate-500">{{ l.timestamp }}</td>
</tr>
{% endfor %}
</tbody>
</table>
</div>
</div>
</div>

<div id="tab-config" class="tab-content hidden bg-slate-950 rounded-2xl border border-slate-800 p-4 md:p-6">
<div class="font-bold text-sm md:text-base text-slate-300 mb-6 border-b border-slate-800 pb-3">সিস্টেম প্যারামিটার কনফিগ</div>
<form action="/admin/settings/save" method="POST" class="space-y-6">
<div class="grid grid-cols-1 md:grid-cols-2 gap-4 md:grid-cols-2 gap-6">
<div><label class="block text-xs font-bold text-slate-400 uppercase mb-2">Business Brand Name</label><input type="text" name="business_name" value="{{ settings.get('business_name', '') }}" class="w-full bg-slate-900 border border-slate-800 p-3 rounded-xl text-xs md:text-sm text-white focus:outline-none"></div>
<div><label class="block text-xs font-bold text-slate-400 uppercase mb-2">WhatsApp Phone ID</label><input type="text" name="phone_number_id" value="{{ settings.get('phone_number_id', '') }}" class="w-full bg-slate-900 border border-slate-800 p-3 rounded-xl text-xs md:text-sm text-white focus:outline-none"></div>
<div class="md:col-span-2"><label class="block text-xs font-bold text-slate-400 uppercase mb-2">WhatsApp Permanent Token</label><input type="password" name="permanent_token" value="{{ settings.get('permanent_token', '') }}" class="w-full bg-slate-900 border border-slate-800 p-3 rounded-xl text-xs md:text-sm text-white focus:outline-none"></div>
<div class="md:col-span-2 p-4 bg-indigo-950/30 border border-indigo-500/20 rounded-xl space-y-3">
<div class="font-bold text-xs text-indigo-400 uppercase">Google Gemini AI Config</div>
<div><input type="password" name="gemini_key" value="{{ settings.get('gemini_key', '') }}" class="w-full bg-slate-900 border border-slate-800 p-2.5 rounded-xl text-xs text-white"></div>
<div><textarea name="ai_system_instruction" rows="3" class="w-full bg-slate-900 border border-slate-800 p-2.5 rounded-xl text-xs text-white">{{ settings.get('ai_system_instruction', '') }}</textarea></div>
</div>
</div>
<button type="submit" class="w-full bg-indigo-600 text-white font-bold p-3 rounded-xl text-xs md:text-sm hover:bg-indigo-500 transition">Save Configurations</button>
</form>
</div>

<div class="p-4 md:p-8 flex-1 overflow-y-auto">

<div id="tab-config" class="tab-content hidden bg-slate-950 rounded-2xl border border-slate-800 p-4 md:p-6">
<div class="font-bold text-sm md:text-base text-slate-300 mb-6 border-b border-slate-800 pb-3">সিস্টেম প্যারামিটার কনফিগ</div>
<form action="/admin/settings/save" method="POST" class="space-y-6">
<div class="grid grid-cols-1 md:grid-cols-2 gap-6">
    <div><label class="block text-xs font-bold text-slate-400 uppercase mb-2">Business Brand Name</label><input type="text" name="business_name" value="{{ settings.get('business_name', '') }}" class="w-full bg-slate-900 border border-slate-800 p-3 rounded-xl text-xs md:text-sm text-white focus:outline-none"></div>
    <div><label class="block text-xs font-bold text-slate-400 uppercase mb-2">WhatsApp Phone ID</label><input type="text" name="phone_number_id" value="{{ settings.get('phone_number_id', '') }}" class="w-full bg-slate-900 border border-slate-800 p-3 rounded-xl text-xs md:text-sm text-white focus:outline-none"></div>

    <div class="md:col-span-2 p-4 bg-indigo-950/30 border border-indigo-500/20 rounded-xl space-y-3">
        <div class="font-bold text-xs text-indigo-400 uppercase">Meta (Facebook) Config</div>
        <div class="grid md:grid-cols-2 gap-4">
            <input type="text" name="fb_catalogue_id" placeholder="Facebook Catalogue ID" value="{{ settings.get('fb_catalogue_id', '') }}" class="w-full bg-slate-900 border border-slate-800 p-2.5 rounded-xl text-xs text-white">
            <input type="password" name="fb_access_token" placeholder="FB Access Token" value="{{ settings.get('fb_access_token', '') }}" class="w-full bg-slate-900 border border-slate-800 p-2.5 rounded-xl text-xs text-white">
            <input type="password" name="permanent_token" placeholder="WhatsApp Permanent Token" value="{{ settings.get('permanent_token', '') }}" class="w-full bg-slate-900 border border-slate-800 p-2.5 rounded-xl text-xs text-white">
        </div>
    </div>

    <div class="md:col-span-2 p-4 bg-rose-950/20 border border-rose-500/20 rounded-xl space-y-3">
        <div class="font-bold text-xs text-rose-400 uppercase">Pathao Courier Config</div>
        <div class="grid md:grid-cols-3 gap-4">
            <input type="text" name="pathao_store_id" placeholder="Store ID" value="{{ settings.get('pathao_store_id', '') }}" class="w-full bg-slate-900 border border-slate-800 p-2.5 rounded-xl text-xs text-white">
            <input type="text" name="pathao_client_id" placeholder="Client ID" value="{{ settings.get('pathao_client_id', '') }}" class="w-full bg-slate-900 border border-slate-800 p-2.5 rounded-xl text-xs text-white">
            <input type="password" name="pathao_client_secret" placeholder="Client Secret" value="{{ settings.get('pathao_client_secret', '') }}" class="w-full bg-slate-900 border border-slate-800 p-2.5 rounded-xl text-xs text-white">
            <input type="text" name="pathao_merchant_email" placeholder="Merchant Email" value="{{ settings.get('pathao_merchant_email', '') }}" class="w-full bg-slate-900 border border-slate-800 p-2.5 rounded-xl text-xs text-white">
            <input type="password" name="pathao_merchant_password" placeholder="Merchant Password" value="{{ settings.get('pathao_merchant_password', '') }}" class="w-full bg-slate-900 border border-slate-800 p-2.5 rounded-xl text-xs text-white">
        </div>
    </div>

    <div class="md:col-span-2 p-4 bg-emerald-950/20 border border-emerald-500/20 rounded-xl space-y-3">
        <div class="font-bold text-xs text-emerald-400 uppercase">Google Gemini AI Config</div>
        <input type="password" name="gemini_key" placeholder="Gemini API Key" value="{{ settings.get('gemini_key', '') }}" class="w-full bg-slate-900 border border-slate-800 p-2.5 rounded-xl text-xs text-white">
        <textarea name="ai_system_instruction" rows="2" placeholder="AI System Instruction" class="w-full bg-slate-900 border border-slate-800 p-2.5 rounded-xl text-xs text-white">{{ settings.get('ai_system_instruction', '') }}</textarea>
    </div>
</div>
<button type="submit" class="w-full bg-indigo-600 text-white font-bold p-3 rounded-xl text-xs md:text-sm hover:bg-indigo-500 transition">Save Configurations</button>
</form>
</div>

</div>
</div>

<script>
function toggleEdit(id) {
const viewEl = document.getElementById('view-' + id);
const editEl = document.getElementById('edit-' + id);
const btnEl = document.getElementById('btn-' + id);
if (editEl.style.display === 'none') {
viewEl.style.display = 'none'; editEl.style.display = 'block'; btnEl.innerText = 'Cancel';
} else {
viewEl.style.display = 'block'; editEl.style.display = 'none'; btnEl.innerText = 'Edit';
}
}
function switchTab(tabId) {
document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
document.getElementById('tab-' + tabId).classList.remove('hidden');
document.querySelectorAll('.tab-btn').forEach(btn => {
btn.classList.remove('bg-indigo-600','text-white','font-bold');
btn.classList.add('text-slate-400');
});
const activeBtn = Array.from(document.querySelectorAll('.tab-btn')).find(b => b.getAttribute('onclick') === "switchTab('" + tabId + "')");
if (activeBtn) { activeBtn.classList.add('bg-indigo-600','text-white','font-bold'); activeBtn.classList.remove('text-slate-400'); }
}
const hash = window.location.hash.replace('#','');
if (hash) switchTab(hash);
</script>
</body>
</html>"""

# =====================================================================
# FLASK ROUTES
# =====================================================================

@app.route("/")
def index():
    return redirect("/admin")

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "cpp_engine_loaded": lib is not None,
        "asm_engine_loaded": asm_lib is not None,
        "timestamp": datetime.now().isoformat()
    })

@app.route("/api/execute", methods=["POST"])
def execute():
    data = request.get_json(silent=True) or {}
    cmd = data.get("cmd", "").strip()
    if not cmd:
        return jsonify({"error": "No command provided"}), 400
    result = cpp_engine.process(cmd)
    return jsonify({"engine": "cpp", "status": result})

@app.route("/api/asm/execute", methods=["POST"])
def asm_execute():
    data = request.get_json(silent=True) or {}
    cmd = data.get("cmd", "").strip()
    if not cmd:
        return jsonify({"error": "No command provided"}), 400
    result = asm_engine.process(cmd)
    return jsonify({"engine": "asm", "status": result})

@app.route("/api/asm/strlen", methods=["GET"])
def asm_strlen_route():
    text = request.args.get("text", "")
    length = asm_engine.strlen(text)
    return jsonify({"engine": "asm", "operation": "strlen", "input": text, "result": length})

@app.route("/api/asm/checksum", methods=["GET"])
def asm_checksum_route():
    text = request.args.get("text", "")
    cs = asm_engine.checksum(text)
    return jsonify({"engine": "asm", "operation": "checksum", "input": text, "result": cs})

@app.route("/api/settings", methods=["GET"])
def api_settings():
    return jsonify(get_all_settings())

# Admin Auth
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "").strip()
        agent = db_query("SELECT * FROM agents WHERE username = ? AND password = ?", (u, p), fetchone=True)
        if agent:
            session["logged_in"] = True
            session["username"] = agent["username"]
            session["role"] = agent["role"]
            return redirect("/admin")
        return render_template_string("""<div style='text-align:center;padding:50px;color:red'>লগইন ব্যর্থ!</div><a href='/admin/login'>Retry</a>""")
    return render_template_string("""<form method='POST' style='max-width:300px;margin:100px auto;text-align:center'>
<h2>Admin Login</h2><input name='username' placeholder='Username' style='width:100%;padding:10px;margin:5px 0'><br>
<input name='password' type='password' placeholder='Password' style='width:100%;padding:10px;margin:5px 0'><br>
<button style='padding:10px 20px'>Login</button></form>""")

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")

@app.route("/admin")
def admin_portal():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    s = get_all_settings()
    msg = request.args.get("msg", "")
    chat_with = request.args.get("chat_with", "")
    orders = db_query("SELECT * FROM orders ORDER BY id DESC", fetchall=True) or []
    users = db_query("SELECT * FROM users ORDER BY last_active DESC", fetchall=True) or []
    products = db_query("SELECT * FROM products ORDER BY id DESC", fetchall=True) or []
    complaints = db_query("SELECT * FROM complaints ORDER BY id DESC", fetchall=True) or []
    agent_logs = db_query("SELECT * FROM agent_logs ORDER BY id DESC LIMIT 50", fetchall=True) or []
    chat_history = db_query("SELECT * FROM messages WHERE from_number = ? ORDER BY id ASC", (chat_with,), fetchall=True) or [] if chat_with else []
    return render_template_string(ADMIN_HTML, settings=s, msg=msg, orders=orders, users=users,
                                  products=products, complaints=complaints, agent_logs=agent_logs,
                                  active_chat=chat_with, chat_history=chat_history, DEFAULT_PRODUCT_IMAGE=DEFAULT_PRODUCT_IMAGE)

@app.route("/admin/agents/add", methods=["POST"])
def add_agent():
    if not session.get("logged_in") or session.get("role") != 'admin':
        return redirect("/admin?msg=শুধুমাত্র মেইন অ্যাডমিন এক্সেস আছে!")
    u = request.form.get("username", "").strip()
    p = request.form.get("password", "").strip()
    if u and p:
        db_query("INSERT OR IGNORE INTO agents (username, password, role) VALUES (?, ?, 'representative')", (u, p), commit=True)
        db_query("INSERT INTO agent_logs (username, action, details) VALUES (?, 'CREATE_AGENT', ?)",
                 (session.get("username"), f"Agent Username: {u}"), commit=True)
    return redirect("/admin?msg=নতুন প্রতিনিধি অ্যাকাউন্ট সফলভাবে খোলা হয়েছে!#agents")

@app.route("/admin/complaint/resolve/<int:cid>", methods=["POST"])
def resolve_complaint(cid):
    if not session.get("logged_in"):
        return redirect("/admin/login")
    notes = request.form.get("notes", "")
    agent = session.get("username")
    db_query("UPDATE complaints SET status='resolved', resolved_by=?, resolution_notes=? WHERE id=?", (agent, notes, cid), commit=True)
    db_query("INSERT INTO agent_logs (username, action, details) VALUES (?, 'RESOLVE_COMPLAINT', ?)", (agent, f"Resolved complaint ID: {cid}"), commit=True)
    return redirect("/admin?msg=কমপ্লেইন রেজোলিউশন সফলভাবে সেভ হয়েছে!#complaints")

@app.route("/admin/order/resolve-call/<int:order_id>")
def resolve_call_request(order_id):
    if not session.get("logged_in"):
        return redirect("/admin/login")
    agent = session.get("username")
    db_query("UPDATE orders SET status='approved', agent_name=? WHERE id=?", (agent, order_id), commit=True)
    db_query("INSERT INTO agent_logs (username, action, details) VALUES (?, 'RESOLVE_CALL', ?)", (agent, f"Call Request ID: {order_id} handled"), commit=True)
    return redirect("/admin?msg=কল রিকোয়েস্ট সমাধান সম্পন্ন!#orders")

@app.route("/admin/settings/save", methods=["POST"])
def save_settings():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    for k, v in request.form.items():
        db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v.strip()), commit=True)
    db_query("INSERT INTO agent_logs (username, action, details) VALUES (?, 'UPDATE_SETTINGS', 'সিস্টেম গ্লোবাল সেটিংস মডিফাই করেছেন')",
             (session.get("username"),), commit=True)
    return redirect("/admin?msg=কনফিগারেশন আপডেট সফল হয়েছে!#config")

@app.route("/admin/sync-facebook-trigger")
def manual_fb_sync():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    suc, detail = sync_facebook_catalogue()
    db_query("INSERT INTO agent_logs (username, action, details) VALUES (?, 'SYNC_CATALOGUE', 'ম্যানুয়ালি মেটা শপ ক্যাটালগ সিঙ্ক করেছেন')",
             (session.get("username"),), commit=True)
    return redirect(f"/admin?msg={detail}#inventory")

@app.route("/admin/product/edit/<int:pid>", methods=["POST"])
def edit_product(pid):
    if not session.get("logged_in"):
        return redirect("/admin/login")
    name = request.form.get("name", "").strip()
    price_str = request.form.get("price", "0").strip()
    stock_str = request.form.get("stock", "10").strip()
    desc = request.form.get("description", "").strip()
    img = request.form.get("image_url", "").strip()
    try:
        price = int(price_str)
        stock = int(stock_str)
    except:
        price = 100
        stock = 10
    db_query("UPDATE products SET name=?, price=?, stock=?, description=?, image_url=? WHERE id=?",
             (name, price, stock, desc, img, pid), commit=True)
    db_query("INSERT INTO agent_logs (username, action, details) VALUES (?, 'EDIT_PRODUCT', ?)",
             (session.get("username"), f"Edited product #{pid}: {name} @ {price}৳"), commit=True)
    return redirect(f"/admin?msg=প্রোডাক্ট #{pid} আপডেট সফল!#inventory")

@app.route("/admin/chat/send", methods=["POST"])
def admin_send_message():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    phone = request.form.get("phone", "")
    msg = request.form.get("message", "")
    agent = session.get("username")
    if phone and msg:
        send_whatsapp(phone, "text", msg, agent=agent)
        db_query("UPDATE sessions SET bot_paused = 1 WHERE phone = ?", (phone,), commit=True)
        db_query("INSERT INTO agent_logs (username, action, details) VALUES (?, 'SEND_CHAT', ?)",
                 (agent, f"Sent direct reply to {phone}"), commit=True)
    return redirect(f"/admin?chat_with={phone}#livechat")

@app.route("/admin/chat/toggle-bot/<phone>")
def toggle_bot_pause(phone):
    if not session.get("logged_in"):
        return redirect("/admin/login")
    s = db_query("SELECT bot_paused FROM sessions WHERE phone=?", (phone,), fetchone=True)
    nxt = 0 if s and s["bot_paused"] == 1 else 1
    db_query("UPDATE sessions SET bot_paused = ? WHERE phone = ?", (nxt, phone), commit=True)
    db_query("INSERT INTO agent_logs (username, action, details) VALUES (?, 'TOGGLE_BOT', ?)",
             (session.get("username"), f"Toggled bot to {nxt} for {phone}"), commit=True)
    return redirect(f"/admin?chat_with={phone}&msg=বট স্ট্যাটাস পরিবর্তন সফল!#livechat")

@app.route("/admin/order/book/<int:order_id>")
def book_pathao(order_id):
    if not session.get("logged_in"):
        return redirect("/admin/login")
    agent = session.get("username")
    order = db_query("SELECT * FROM orders WHERE id = ?", (order_id,), fetchone=True)
    if not order:
        return redirect("/admin?msg=অর্ডার পাওয়া যায়নি#orders")
    prod = db_query("SELECT name FROM products WHERE id=?", (order["product_id"],), fetchone=True)
    o_ctx = {"cust_name": order["name"], "address": order["address"], "quantity": order["quantity"],
             "name": prod["name"] if prod else "Ecom Item"}
    success, res = create_pathao_order(o_ctx, order["phone"], order["total"])
    if success:
        db_query("UPDATE orders SET pathao_consignment_id=?, status='approved', agent_name=? WHERE id=?",
                 (res, agent, order_id), commit=True)
        db_query("INSERT INTO agent_logs (username, action, details) VALUES (?, 'PATHAO_BOOKING', ?)",
                 (agent, f"Booked order #{order_id} via Pathao ID: {res}"), commit=True)
        return redirect(f"/admin?msg=পাঠাও বুকিং সফল! কনসাইনমেন্ট আইডি: {res}#orders")
    return redirect(f"/admin?msg=পাঠাও এরর: {res}#orders")

@app.route("/invoice/<int:order_id>")
def print_invoice(order_id):
    order = db_query("SELECT * FROM orders WHERE id = ?", (order_id,), fetchone=True)
    if not order:
        return "মেমো পাওয়া যায়নি", 404
    s = get_all_settings()
    prod = db_query("SELECT name, price FROM products WHERE id=?", (order["product_id"],), fetchone=True)
    html = f"""<html><head><title>Invoice #{order['id']}</title><script src="https://cdn.tailwindcss.com"></script></head>
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
</body></html>"""
    return html

# =====================================================================
# WHATSAPP WEBHOOK
# =====================================================================
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
    except:
        pass
    return "EVENT_RECEIVED", 200

# =====================================================================
# MAIN
# =====================================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
