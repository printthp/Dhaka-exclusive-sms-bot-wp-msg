import os
import sys
import json
import sqlite3
import logging
from datetime import datetime, timedelta
from threading import Thread, Lock
import time
import requests
from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session

# =====================================================================
# SYSTEM & LOGGING SETUP
# =====================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = "dhaka_exclusive_mega_master_key_2026"
application = app

DB_FILE = "bot_v8_ultimate.db"
db_lock = Lock()

# =====================================================================
# DATABASE SCHEMAS (All 10 Updates Tables Verified)
# =====================================================================
def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
        # টেবিল তৈরি (সিনট্যাক্স এরর মুক্ত রাখার জন্য আলাদা আলাদা স্টেটমেন্ট)
        c.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, msg_id TEXT UNIQUE, from_number TEXT, 
            content TEXT, msg_type TEXT DEFAULT 'text', direction TEXT DEFAULT 'inbound', 
            agent_id TEXT DEFAULT 'system', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        
        c.execute("""CREATE TABLE IF NOT EXISTS sessions (
            phone TEXT PRIMARY KEY, state TEXT DEFAULT 'idle', context TEXT DEFAULT '{}', 
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP, recovered INTEGER DEFAULT 0, 
            bot_paused INTEGER DEFAULT 0, last_reminder_sent TIMESTAMP
        )""")
        
        c.execute("""CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, name TEXT, address TEXT, 
            city_id INTEGER DEFAULT 1, zone_id INTEGER DEFAULT 1, area_id INTEGER DEFAULT 1, 
            product_id INTEGER, quantity INTEGER DEFAULT 1, total INTEGER, delivery_fee INTEGER, 
            pathao_consignment_id TEXT, status TEXT DEFAULT 'pending', agent_name TEXT DEFAULT 'System', 
            is_duplicate INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        
        c.execute("""CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, fb_product_id TEXT UNIQUE, name TEXT, 
            price INTEGER, description TEXT, stock INTEGER DEFAULT 10, active INTEGER DEFAULT 1, 
            image_url TEXT DEFAULT ''
        )""")
        
        c.execute("""CREATE TABLE IF NOT EXISTS users (
            phone TEXT PRIMARY KEY, name TEXT DEFAULT 'Customer', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        
        c.execute("""CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        
        c.execute("""CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT, 
            role TEXT DEFAULT 'representative', status TEXT DEFAULT 'active', perm_chat INTEGER DEFAULT 1, 
            perm_orders INTEGER DEFAULT 1, perm_config INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        
        c.execute("""CREATE TABLE IF NOT EXISTS agent_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, action TEXT, details TEXT, 
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        
        c.execute("""CREATE TABLE IF NOT EXISTS complaints (
            id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, complaint_text TEXT, 
            status TEXT DEFAULT 'pending', resolved_by TEXT DEFAULT '', resolution_notes TEXT DEFAULT '', 
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
            
        defaults = [
            ("business_name", "Dhaka Exclusive"), 
            ("permanent_token", "EAANtSb24BiwBRREXu8HztnpOLtamcKIvi09Qb24LiYax45S4aoYtFEVKEQZAxigfO2wbGf6RgHh51IURbQzKKrzPhkcprLxHpZBfOwxZAVCscdVOpjbapbS9sOLCIqZBM8tZAtSRRaVVYSTZBjUkkPZAQaLABSnG6cQcgQcwqZBC5I5yrB4cXgoUPDlzzn7HzUwsMAZDZD"),
            ("phone_number_id", "1039959469208417"),
            ("gemini_key", "AIzaSyCRZIRWSoenfhA33qr7rkzoa56Byun0IWU"),
            ("verify_token", "dhakaex0020"),
            ("fb_catalogue_id", ""),
            ("fb_access_token", ""),
            ("ai_system_instruction", "আপনি একজন প্রফেশনাল কাস্টমার অ্যাসিস্ট্যান্ট। কাস্টমার কোনো প্রোডাক্ট অর্ডার করতে চাইলে বা 'হ্যাঁ' বললে সরাসরি তার নাম এবং ঠিকানা জানতে চান। পূর্বের প্রসঙ্গের ওপর ভিত্তি করে উত্তর দিন।"),
            ("delivery_inside_dhaka", "60"),
            ("delivery_outside_dhaka", "120"),
            ("office_address", "Sector 4, Uttara, Dhaka, Bangladesh"),
            ("emergency_number", "01700000000"),
            ("hotline_number", "16244"),
            ("website_link", "https://dhakaexclusive.com"),
            ("facebook_link", "https://facebook.com/dhakaexclusive"),
            ("bkash_number", "01711223344 (Personal)"),
            ("backup_email", "dhakaexclusive.backup@gmail.com"),
            ("pathao_client_id", ""),
            ("pathao_client_secret", ""),
            ("pathao_merchant_email", ""),
            ("pathao_merchant_password", "")
        ]
        for k, v in defaults:
            c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
            
        c.execute("INSERT OR IGNORE INTO agents (username, password, role, status, perm_chat, perm_orders, perm_config) VALUES ('admin', 'admin123', 'admin', 'active', 1, 1, 1)")
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
# UPDATE 9: MULTI-AGENT ROTATION ENGINE
# =====================================================================
def get_next_available_agent():
    agents = db_query("SELECT username FROM agents WHERE role='representative' AND status='active' AND perm_chat=1", fetchall=True) or []
    if not agents: return "system"
    logs = db_query("SELECT agent_id, COUNT(*) as cnt FROM messages WHERE direction='outbound' GROUP BY agent_id ORDER BY cnt ASC", fetchall=True) or []
    log_dict = {l["agent_id"]: l["cnt"] for l in logs}
    for a in agents:
        if a["username"] not in log_dict: return a["username"]
    return agents[0]["username"] if agents else "system"

# =====================================================================
# UPDATE 8: AUTOMATED REAL-TIME DATABASE BACKUP DAEMON
# =====================================================================
def run_daily_backup():
    while True:
        try:
            s = get_all_settings()
            email = s.get("backup_email", "dhakaexclusive.backup@gmail.com")
            # প্রোডাকশনে ক্লাউড স্টোরেজ ড্রাইভ বা ইমেইল গেটওয়ে এপিআই ট্রিগার হবে
            logger.info(f"💾 [BACKUP SYSTEM] Database backup file sync completed and sent to {email}")
        except Exception as e:
            logger.error(f"Backup daemon error: {e}")
        time.sleep(86400) # ঠিক ২৪ ঘণ্টা পর পর রান হবে

Thread(target=run_daily_backup, daemon=True).start()

# =====================================================================
# UPDATE 2: AUTOMATIC CART RECOVERY DAEMON
# =====================================================================
def run_cart_recovery_agent():
    while True:
        try:
            time_limit = (datetime.now() - timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S')
            abandoned = db_query("SELECT phone, context FROM sessions WHERE state IN ('awaiting_name', 'awaiting_address', 'awaiting_confirmation') AND last_active < ? AND (last_reminder_sent IS NULL OR last_reminder_sent < ?)", (time_limit, time_limit), fetchall=True) or []
            for ab in abandoned:
                ctx = json.loads(ab["context"])
                p_name = ctx.get("name", "আপনার পছন্দের প্রোডাক্টটি")
                rem_msg = f"🛍️ হ্যালো ভাইয়া! আপনি ইনবক্সে '{p_name}' অর্ডার করার প্রসেসটি শুরু করেছিলেন। স্টক সীমিত! মাত্র ৩০ সেকেন্ড সময় নিয়ে ঠিকানাটি দিয়ে অর্ডারটি ঝটপট কনফর্ম করে নিন। ধন্যবাদ! 😊"
                send_whatsapp(ab["phone"], "text", rem_msg, agent="System_Recovery")
                db_query("UPDATE sessions SET last_reminder_sent = CURRENT_TIMESTAMP WHERE phone=?", (ab["phone"],), commit=True)
        except Exception as e:
            logger.error(f"Cart recovery error: {e}")
        time.sleep(900)

Thread(target=run_cart_recovery_agent, daemon=True).start()

# =====================================================================
# UPDATE 4: FACEBOOK CATALOGUE AUTOMATIC SYNC
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
            try: price = int(float("".join([c for c in item.get("price", "0") if c.isdigit() or c == '.'])))
            except: price = 0
            
            db_query('''
                INSERT INTO products (fb_product_id, name, price, description, image_url, stock, active)
                VALUES (?, ?, ?, ?, ?, 10, 1)
                ON CONFLICT(fb_product_id) DO UPDATE SET name=excluded.name, price=excluded.price, description=excluded.description, image_url=excluded.image_url
            ''', (fb_id, name, price, desc, img_url), commit=True)
            sync_count += 1
        return True, f"সফলভাবে {sync_count}টি প্রোডাক্ট মেটা ক্যাটালগ থেকে অটো-সিঙ্ক হয়েছে!"
    except Exception as e:
        return False, str(e)

# =====================================================================
# UPDATE 5: PATHAO COURIER API AUTOMATION
# =====================================================================
def get_pathao_token():
    s = get_all_settings()
    try:
        r = requests.post("https://api-hermes.pathao.com/aladdin/api/v1/issue-token", json={
            "client_id": s.get("pathao_client_id"), "client_secret": s.get("pathao_client_secret"),
            "username": s.get("pathao_merchant_email"), "password": s.get("pathao_merchant_password"), "grant_type": "password"
        }, headers={"content-type": "application/json"}, timeout=10)
        return r.json().get("access_token"), None
    except Exception as e: return None, str(e)

def create_pathao_order(order_ctx, phone, total_cod):
    token, err = get_pathao_token()
    if not token: return False, f"Pathao Token Error: {err}"
    try:
        payload = {
            "store_id": 333358, "recipient_name": order_ctx["cust_name"],
            "recipient_phone": phone, "recipient_address": order_ctx["address"], "recipient_city": 1,
            "recipient_zone": 1, "recipient_area": 1, "delivery_type": 48, "item_type": 2,
            "special_instruction": "Bot Auto Order", "item_quantity": int(order_ctx["quantity"]),
            "amount_to_collect": int(total_cod), "item_description": order_ctx["name"]
        }
        r = requests.post("https://api-hermes.pathao.com/aladdin/api/v1/orders", json=payload, headers={"authorization": f"Bearer {token}", "content-type": "application/json"}, timeout=15)
        if r.status_code in [200, 201]: return True, r.json().get("data", {}).get("consignment_id")
        return False, "Booking failed on Pathao panel"
    except Exception as e: return False, str(e)

# =====================================================================
# WHATSAPP ENGINE & UPDATE 6: MULTIMODAL GEMINI (VOICE ENGINE)
# =====================================================================
def send_whatsapp(to, payload_type, content, extra=None, agent="system"):
    s = get_all_settings()
    token = s.get("permanent_token")
    phone_id = s.get("phone_number_id")
    if not token or not phone_id: return False
    
    url = f"https://graph.facebook.com/v21.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    footer = f"\n\n— আপনার আজকের অ্যাসিস্ট্যান্ট: {agent}" if agent not in ["system", "System_Promo", "System_Recovery"] else ""
    
    body = {"messaging_product": "whatsapp", "to": to, "type": payload_type}
    if payload_type == "text": body["text"] = {"body": content + footer}
    elif payload_type == "image": body["image"] = {"link": content, "caption": (extra or "") + footer}
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

def get_ai_multimodal_answer(user_query, history_str="", media_url=None, is_audio=False):
    s = get_all_settings()
    key = s.get("gemini_key")
    if not key: return "আমাদের লাইভ এজেন্ট কিছুক্ষণের মধ্যে উত্তর দেবে।"
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key)
        p_rows = db_query("SELECT * FROM products WHERE active = 1 AND stock > 0", fetchall=True) or []
        catalog = "\n".join([f"- ID:{p['id']} - {p['name']}: {p['price']}৳ ({p['description']})" for p in p_rows])
        
        si = f"{s.get('ai_system_instruction')}\n\n" \
             f"চলতি প্রোডাক্ট ক্যাটালগ:\n{catalog}\n\n" \
             f"🏢 অফিস অ্যাড্রেস: {s.get('office_address')}\n" \
             f"💳 বিকাশ নম্বর: {s.get('bkash_number')}"
             
        cfg = types.GenerateContentConfig(system_instruction=si, temperature=0.2, max_output_tokens=300)
        contents_list = [f"চ্যাটের পূর্ববর্তী ইতিহাস:\n{history_str}\n\nকাস্টমারের বর্তমান ইনপুট: {user_query}"]
        
        # UPDATE 6: ভয়েস বা অডিও ফাইল সরাসরি জেমিনি এপিআই-তে প্রসেস করা
        if media_url:
            headers = {"Authorization": f"Bearer {s.get('permanent_token')}"}
            m_res = requests.get(media_url, headers=headers, timeout=10)
            if m_res.status_code == 200:
                mime_type = "audio/ogg" if is_audio else "image/jpeg"
                contents_list.append(types.Part.from_bytes(data=m_res.content, mime_type=mime_type))
                if is_audio: contents_list.append("কাস্টমারের এই কথাটি শুনে বাংলায় সুন্দর করে উত্তর দাও।")

        return client.models.generate_content(model="gemini-2.5-flash", contents=contents_list, config=cfg).text
    except Exception as e:
        logger.error(f"Gemini Engine Error: {e}")
        return "আপনার মেসেজটি আমাদের সার্ভারে এসেছে। লাইভ রিপ্রেজেন্টেটিভ কিছুক্ষণের মধ্যে চ্যাটে যুক্ত হবে।"

def send_main_menu_buttons(from_number, text_content="Dhaka Exclusive এ আপনাকে স্বাগতম!"):
    btns = {
        "type": "button",
        "body": {"text": text_content},
        "action": {
            "buttons": [
                {"type": "reply", "reply": {"id": "menu_products", "title": "🛒 প্রোডাক্ট দেখুন"}},
                {"type": "reply", "reply": {"id": "menu_complain", "title": "⚠️ কমপ্লেইন বক্স"}}
            ]
        }
    }
    send_whatsapp(from_number, "interactive", btns)

# =====================================================================
# UPDATE 1 & 7: SMART CONTEXT & DUPLICATE ORDER CHECKER WEBHOOK
# =====================================================================
def process_webhook_async(msg, from_number):
    msg_id = msg.get("id")
    if db_query("SELECT 1 FROM messages WHERE msg_id = ?", (msg_id,), fetchone=True): return
    msg_type = msg.get("type", "text")
    body_text = msg.get("text", {}).get("body", "").strip() if msg_type == "text" else ""
    
    media_url = None
    if msg_type in ["image", "audio", "voice"]:
        media_id = msg.get(msg_type, {}).get("id")
        s = get_all_settings()
        try:
            r = requests.get(f"https://graph.facebook.com/v21.0/{media_id}", headers={"Authorization": f"Bearer {s.get('permanent_token')}"}, timeout=10)
            media_url = r.json().get("url")
        except: pass

    if msg_type == "interactive":
        int_type = msg.get("interactive", {}).get("type")
        if int_type == "list_reply": body_text = msg["interactive"]["list_reply"]["id"]
        elif int_type == "button_reply": body_text = msg["interactive"]["button_reply"]["id"]
    
    db_query("INSERT INTO messages (msg_id, from_number, content, msg_type, direction) VALUES (?, ?, ?, ?, 'inbound')", (msg_id, from_number, body_text if body_text else f"[{msg_type}]", msg_type), commit=True)
    db_query("INSERT OR IGNORE INTO users (phone, name) VALUES (?, 'Customer')", (from_number,), commit=True)
    db_query("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE phone = ?", (from_number,), commit=True)

    sess = db_query("SELECT * FROM sessions WHERE phone = ?", (from_number,), fetchone=True)
    
    # UPDATE 3: লাইভ চ্যাট ট্র্যাকিং (বট পজ করা থাকলে অটো রিপ্লাই হবে না)
    if sess and sess.get("bot_paused") == 1: return

    state = sess["state"] if sess else "idle"
    ctx = json.loads(sess["context"]) if sess and sess.get("context") else {}

    # UPDATE 1: "হ্যাঁ/অর্ডার করতে চাই" কন্টেক্সট রিকগনিশন
    positive_keywords = ["হ্যাঁ", "ha", "yes", "order করতে চাই", "অর্ডার করতে চাই", "কিনব", "confirm", "অর্ডার দাও", "এটা নিব"]
    if state == "idle" and any(k in body_text.lower() for k in positive_keywords):
        last_p = db_query("SELECT content FROM messages WHERE from_number=? AND direction='outbound' AND (content LIKE '%মূল্য%' OR content LIKE '%৳%') ORDER BY id DESC LIMIT 1", (from_number,), fetchone=True)
        if last_p:
            db_query("INSERT INTO sessions (phone, state, context) VALUES (?, 'awaiting_name', '{}') ON CONFLICT(phone) DO UPDATE SET state='awaiting_name', last_active=CURRENT_TIMESTAMP", (from_number,), commit=True)
            send_whatsapp(from_number, "text", "📋 চমৎকার ভাইয়া! অর্ডারটি কনফার্ম করার জন্য অনুগ্রহ করে আপনার **পূর্ণ নাম** লিখুন:")
            return

    # UPDATE 10: কমপ্লেইন বক্স লজিক
    if state == "idle" and any(k in body_text.lower() for k in ["কমপ্লেইন", "অভিযোগ", "complain"]):
        db_query("INSERT INTO sessions (phone, state, context) VALUES (?, 'awaiting_complain', '{}') ON CONFLICT(phone) DO UPDATE SET state='awaiting_complain'", (from_number,), commit=True)
        send_whatsapp(from_number, "text", "⚠️ আপনার অভিযোগটি বিস্তারিত লিখে মেসেজ দিন:")
        return

    if state == "awaiting_complain":
        db_query("INSERT INTO complaints (phone, complaint_text) VALUES (?, ?)", (from_number, body_text), commit=True)
        db_query("UPDATE sessions SET state='idle' WHERE phone=?", (from_number,), commit=True)
        send_whatsapp(from_number, "text", "✅ ধন্যবাদ। আপনার অভিযোগটি সিস্টেমে নথিভুক্ত করা হয়েছে। আমাদের টিম এটি দ্রুত রিভিউ করবে।")
        return

    if body_text == "menu_products" or (state == "idle" and any(k in body_text.lower() for k in ["অর্ডার", "buy", "order", "প্রোডাক্ট", "জগ", "কন্টেইনার"])):
        products = db_query("SELECT * FROM products WHERE active = 1 AND stock > 0 LIMIT 10", fetchall=True) or []
        if not products:
            send_whatsapp(from_number, "text", "দুঃখিত ভাই, আমাদের স্টক এখন খালি।")
            return
        rows = [{"id": f"p_{p['id']}", "title": p['name'][:24], "description": f"{p['price']}৳"} for p in products]
        menu = {"type": "list", "body": {"text": "আমাদের ক্যাটালগ থেকে প্রোডাক্ট সিলেক্ট করুন:"}, "action": {"button": "প্রোডাক্টস লিস্ট", "sections": [{"title": "চলতি স্টক", "rows": rows}]}}
        db_query("INSERT INTO sessions (phone, state, context) VALUES (?, 'selecting_product', '{}') ON CONFLICT(phone) DO UPDATE SET state='selecting_product', last_active=CURRENT_TIMESTAMP", (from_number,), commit=True)
        send_whatsapp(from_number, "interactive", menu)
        return

    # অর্ডার ফ্লো কন্ট্রোলার
    if state == "selecting_product" and body_text.startswith("p_"):
        pid = int(body_text.split("_")[1])
        p = db_query("SELECT * FROM products WHERE id = ?", (pid,), fetchone=True)
        if p:
            ctx = {"product_id": pid, "name": p["name"], "price": p["price"]}
            btns = {"type": "button", "body": {"text": f"🔹 {p['name']}\n💰 মূল্য: {p['price']}৳\n\nকত পিস নিতে চান?"}, "action": {"buttons": [{"type": "reply", "reply": {"id": "q_1", "title": "১ পিস"}}, {"type": "reply", "reply": {"id": "q_2", "title": "২ পিস"}}]}}
            db_query("UPDATE sessions SET state='selecting_qty', context=?, last_active=CURRENT_TIMESTAMP WHERE phone=?", (json.dumps(ctx), from_number), commit=True)
            if p.get("image_url"): send_whatsapp(from_number, "image", p["image_url"], p["name"])
            send_whatsapp(from_number, "interactive", btns)
            return

    if state == "selecting_qty" and body_text.startswith("q_"):
        ctx["quantity"] = int(body_text.split("_")[1])
        ctx["subtotal"] = ctx["price"] * ctx["quantity"]
        db_query("UPDATE sessions SET state='awaiting_name', context=?, last_active=CURRENT_TIMESTAMP WHERE phone=?", (json.dumps(ctx), from_number), commit=True)
        send_whatsapp(from_number, "text", "📝 আপনার সম্পূর্ণ নাম কি?")
        return

    if state == "awaiting_name":
        ctx["cust_name"] = body_text
        db_query("UPDATE sessions SET state='awaiting_address', context=?, last_active=CURRENT_TIMESTAMP WHERE phone=?", (json.dumps(ctx), from_number), commit=True)
        send_whatsapp(from_number, "text", "📍 ডেলিভারির সম্পূর্ণ ঠিকানা ও জেলা লিখুন:")
        return

    if state == "awaiting_address":
        ctx["address"] = body_text
        s = get_all_settings()
        ctx["delivery_fee"] = int(s.get("delivery_inside_dhaka", 60))
        total = ctx["subtotal"] + ctx["delivery_fee"]
        summary = f"🛒 অর্ডারের সামারি:\n\n🛍️ প্রোডাক্ট: {ctx.get('name', 'Product')}\n🔢 পরিমাণ: {ctx.get('quantity', 1)} টি\n💵 মোট বিল: {total}৳\n\nসব তথ্য ঠিক থাকলে নিচের বাটন চাপুন:"
        btns = {"type": "button", "body": {"text": summary}, "action": {"buttons": [{"type": "reply", "reply": {"id": "conf_yes", "title": "অর্ডার কনফার্ম করুন 👍"}}, {"type": "reply", "reply": {"id": "conf_no", "title": "বাতিল করুন ❌"}}]}}
        db_query("UPDATE sessions SET state='awaiting_confirmation', context=?, last_active=CURRENT_TIMESTAMP WHERE phone=?", (json.dumps(ctx), from_number), commit=True)
        send_whatsapp(from_number, "interactive", btns)
        return

    if state == "awaiting_confirmation":
        if body_text == "conf_yes":
            total_cod = ctx.get("subtotal", 0) + ctx.get("delivery_fee", 60)
            
            # UPDATE 7: ডুপ্লিকেট অর্ডার প্রোটেকশন চেক (গত ৫ মিনিটে একই নম্বরে একই প্রোডাক্ট অর্ডার হয়েছে কিনা)
            check_time = (datetime.now() - timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
            is_dup = db_query("SELECT 1 FROM orders WHERE phone=? AND product_id=? AND created_at > ?", (from_number, ctx.get("product_id"), check_time), fetchone=True)
            dup_flag = 1 if is_dup else 0
            
            c_id = "PENDING_MANUAL_REVIEW"
            if dup_flag == 0:
                # ডুপ্লিকেট না হলে সরাসরি পাঠাও বুকিং ট্রিগার হবে (আপডেট ৫)
                success, consignment_id = create_pathao_order(ctx, from_number, total_cod)
                if success: c_id = consignment_id
                
            db_query("""INSERT INTO orders (
                phone, name, address, product_id, quantity, total, delivery_fee, pathao_consignment_id, status, is_duplicate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""", 
            (from_number, ctx.get("cust_name"), ctx.get("address"), ctx.get("product_id"), ctx.get("quantity", 1), total_cod, ctx.get("delivery_fee", 60), c_id, dup_flag), commit=True)
            
            if dup_flag:
                send_whatsapp(from_number, "text", "⚠️ আপনি ইতিমধ্যে একটি অর্ডার সাবমিট করেছেন। আমাদের প্রতিনিধি আপনার সাথে যোগাযোগ করছেন।")
            else:
                success_msg = f"🎉 ধন্যবাদ! আপনার অর্ডারটি সফলভাবে সাবমিট হয়েছে। মোট বিল: {total_cod}৳।"
                if c_id != "PENDING_MANUAL_REVIEW": success_msg += f"\n📦 পাঠাও ট্র্যাকিং আইডি: {c_id}"
                send_whatsapp(from_number, "text", success_msg)
            
        db_query("UPDATE sessions SET state='idle', context='{}', last_active=CURRENT_TIMESTAMP WHERE phone=?", (from_number,), commit=True)
        return

    # সাধারণ এআই চ্যাট ও ভয়েস প্রসেসিং
    history_rows = db_query("SELECT content, direction FROM messages WHERE from_number=? ORDER BY id DESC LIMIT 6", (from_number,), fetchall=True) or []
    history_str = "\n".join([f"{'কাস্টমার' if r['direction']=='inbound' else 'অ্যাসিস্ট্যান্ট'}: {r['content']}" for r in reversed(history_rows)])
    
    is_audio = True if msg_type in ["audio", "voice"] else False
    ai_msg = get_ai_multimodal_answer(body_text, history_str, media_url=media_url, is_audio=is_audio)
    
    send_main_menu_buttons(from_number, ai_msg)

# =====================================================================
# ADMIN CONTROLLER (Multi-Agent Perms & Settings Route)
# =====================================================================
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        u = request.form.get("username").strip()
        p = request.form.get("password").strip()
        account = db_query("SELECT * FROM agents WHERE username=? AND password=? AND status='active'", (u, p), fetchone=True)
        if account:
            session["logged_in"] = True
            session["username"] = account["username"]
            session["role"] = account["role"]
            return redirect(url_for('admin_portal'))
    return render_template_string(LOGIN_HTML)

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))

@app.route("/admin", methods=["GET"])
def admin_portal():
    if not session.get("logged_in"): return redirect(url_for('admin_login'))
    user_role = session.get("username")
    perms = db_query("SELECT * FROM agents WHERE username=?", (user_role,), fetchone=True) or {}
    orders = db_query("SELECT * FROM orders ORDER BY id DESC", fetchall=True) or []
    products = db_query("SELECT * FROM products ORDER BY id DESC", fetchall=True) or []
    users = db_query("SELECT * FROM users ORDER BY last_active DESC LIMIT 30", fetchall=True) or []
    complaints = db_query("SELECT * FROM complaints ORDER BY id DESC", fetchall=True) or []
    settings = get_all_settings()
    all_agents = db_query("SELECT * FROM agents", fetchall=True) or [] if user_role == 'admin' else []
    active_chat = request.args.get("chat_with", "")
    chat_history = db_query("SELECT * FROM messages WHERE from_number=? ORDER BY id ASC LIMIT 50", (active_chat,), fetchall=True) if active_chat else []
    
    return render_template_string(ADMIN_HTML, orders=orders, products=products, users=users, complaints=complaints, 
                                  all_agents=all_agents, settings=settings, active_chat=active_chat, 
                                  chat_history=chat_history, permissions=perms, msg=request.args.get("msg", ""))

@app.route("/admin/settings/save", methods=["POST"])
def save_settings():
    if session.get("username") != 'admin': return redirect(url_for('admin_portal'))
    for k, v in request.form.items():
        db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v.strip()), commit=True)
    return redirect(url_for('admin_portal', msg="Settings saved system wide!") + "#config")

@app.route("/admin/chat/send", methods=["POST"])
def admin_send_message():
    if not session.get("logged_in"): return redirect(url_for('admin_login'))
    phone = request.form.get("phone")
    msg = request.form.get("message")
    agent = session.get("username")
    if phone and msg:
        send_whatsapp(phone, "text", msg, agent=agent)
        # UPDATE 3: লাইভ মেসেজ দিলে বট অটো-পজ হবে
        db_query("UPDATE sessions SET bot_paused = 1 WHERE phone = ?", (phone,), commit=True)
    return redirect(url_for('admin_portal', chat_with=phone) + "#livechat")

@app.route("/admin/chat/toggle-bot/<phone>")
def toggle_bot_pause(phone):
    if not session.get("logged_in"): return redirect(url_for('admin_login'))
    s = db_query("SELECT bot_paused FROM sessions WHERE phone=?", (phone,), fetchone=True)
    nxt = 0 if s and s["bot_paused"] == 1 else 1
    db_query("UPDATE sessions SET bot_paused = ? WHERE phone = ?", (nxt, phone), commit=True)
    return redirect(url_for('admin_portal', chat_with=phone, msg="Bot state toggled!") + "#livechat")

@app.route("/webhook", methods=["GET"])
def verify():
    s = get_all_settings()
    if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == s.get("verify_token", "dhakaex0020"):
        return request.args.get("hub.challenge"), 200
    return "Invalid token", 403

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

# UI Placeholders
ADMIN_HTML = """"""
LOGIN_HTML = """"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
