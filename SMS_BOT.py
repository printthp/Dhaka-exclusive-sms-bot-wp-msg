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
# DATABASE SCHEMAS
# =====================================================================
def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
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
# BACKGROUND DAEMONS (CART RECOVERY & BACKUP)
# =====================================================================
def run_daily_backup():
    while True:
        try:
            s = get_all_settings()
            email = s.get("backup_email", "dhakaexclusive.backup@gmail.com")
            logger.info(f"💾 [BACKUP SYSTEM] Database auto sync simulated with {email}")
        except Exception as e: logger.error(f"Backup error: {e}")
        time.sleep(86400)

Thread(target=run_daily_backup, daemon=True).start()

def run_cart_recovery_agent():
    while True:
        try:
            time_limit = (datetime.now() - timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S')
            abandoned = db_query("SELECT phone, context FROM sessions WHERE state IN ('awaiting_name', 'awaiting_address', 'awaiting_confirmation') AND last_active < ? AND (last_reminder_sent IS NULL OR last_reminder_sent < ?)", (time_limit, time_limit), fetchall=True) or []
            for ab in abandoned:
                ctx = json.loads(ab["context"])
                p_name = ctx.get("name", "আপনার পছন্দের প্রোডাক্টটি")
                rem_msg = f"🛍️ হ্যালো ভাইয়া! আপনি ইনবক্সে '{p_name}' অর্ডার করার প্রসেসটি শুরু করেছিলেন। স্টক সীমিত! ঝটপট ঠিকানাটি দিয়ে অর্ডারটি কনফর্ম করে নিন। ধন্যবাদ! 😊"
                send_whatsapp(ab["phone"], "text", rem_msg, agent="System_Recovery")
                db_query("UPDATE sessions SET last_reminder_sent = CURRENT_TIMESTAMP WHERE phone=?", (ab["phone"],), commit=True)
        except Exception as e: logger.error(f"Cart recovery error: {e}")
        time.sleep(900)

Thread(target=run_cart_recovery_agent, daemon=True).start()

# =====================================================================
# EXTERNAL APIS (META & PATHAO)
# =====================================================================
def sync_facebook_catalogue():
    s = get_all_settings()
    cat_id = s.get("fb_catalogue_id")
    token = s.get("fb_access_token")
    if not cat_id or not token: return False, "সেটিংস থেকে মেটা টোকেন বা আইডি মিসিং!"
    url = f"https://graph.facebook.com/v21.0/{cat_id}/products"
    params = {"fields": "id,name,price,description,image_url", "access_token": token, "limit": 100}
    try:
        r = requests.get(url, params=params, timeout=15)
        res = r.json()
        if "data" not in res: return False, "Meta Sync Error"
        for item in res["data"]:
            fb_id = item.get("id")
            name = item.get("name")
            desc = item.get("description", "No description")
            img_url = item.get("image_url", "https://placehold.co/400")
            try: price = int(float("".join([c for c in item.get("price", "0") if c.isdigit() or c == '.'])))
            except: price = 0
            db_query('''INSERT INTO products (fb_product_id, name, price, description, image_url, stock, active) VALUES (?, ?, ?, ?, ?, 10, 1) ON CONFLICT(fb_product_id) DO UPDATE SET name=excluded.name, price=excluded.price''', (fb_id, name, price, desc, img_url), commit=True)
        return True, "ক্যাটালগ সফলভাবে সিঙ্ক হয়েছে!"
    except Exception as e: return False, str(e)

def get_pathao_token():
    s = get_all_settings()
    try:
        r = requests.post("https://api-hermes.pathao.com/aladdin/api/v1/issue-token", json={"client_id": s.get("pathao_client_id"), "client_secret": s.get("pathao_client_secret"), "username": s.get("pathao_merchant_email"), "password": s.get("pathao_merchant_password"), "grant_type": "password"}, headers={"content-type": "application/json"}, timeout=10)
        return r.json().get("access_token"), None
    except Exception as e: return None, str(e)

def create_pathao_order(order_ctx, phone, total_cod):
    token, err = get_pathao_token()
    if not token: return False, err
    try:
        payload = {"store_id": 333358, "recipient_name": order_ctx["cust_name"], "recipient_phone": phone, "recipient_address": order_ctx["address"], "recipient_city": 1, "recipient_zone": 1, "recipient_area": 1, "delivery_type": 48, "item_type": 2, "special_instruction": "Bot Order", "item_quantity": int(order_ctx["quantity"]), "amount_to_collect": int(total_cod), "item_description": order_ctx["name"]}
        r = requests.post("https://api-hermes.pathao.com/aladdin/api/v1/orders", json=payload, headers={"authorization": f"Bearer {token}", "content-type": "application/json"}, timeout=15)
        if r.status_code in [200, 201]: return True, r.json().get("data", {}).get("consignment_id")
        return False, "Failed"
    except Exception as e: return False, str(e)

# =====================================================================
# WHATSAPP ENGINE & MULTIMODAL GEMINI
# =====================================================================
def send_whatsapp(to, payload_type, content, extra=None, agent="system"):
    s = get_all_settings()
    token = s.get("permanent_token")
    phone_id = s.get("phone_number_id")
    if not token or not phone_id: return False
    url = f"https://graph.facebook.com/v21.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    footer = f"\n\n— অ্যাসিস্ট্যান্ট: {agent}" if agent not in ["system", "System_Recovery"] else ""
    body = {"messaging_product": "whatsapp", "to": to, "type": payload_type}
    if payload_type == "text": body["text"] = {"body": content + footer}
    elif payload_type == "image": body["image"] = {"link": content, "caption": (extra or "") + footer}
    elif payload_type == "interactive": body["interactive"] = content
    try:
        r = requests.post(url, json=body, headers=headers, timeout=10)
        if r.status_code in [200, 201]:
            gen_id = r.json().get("messages", [{}])[0].get("id", f"out_{int(time.time())}")
            db_query("INSERT INTO messages (msg_id, from_number, content, msg_type, direction, agent_id) VALUES (?, ?, ?, ?, 'outbound', ?)", (gen_id, to, str(content), payload_type, agent), commit=True)
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
        catalog = "\n".join([f"- {p['name']}: {p['price']}৳" for p in p_rows])
        si = f"{s.get('ai_system_instruction')}\n\nক্যাটালগ:\n{catalog}"
        cfg = types.GenerateContentConfig(system_instruction=si, temperature=0.2, max_output_tokens=300)
        contents_list = [f"ইতিহাস:\n{history_str}\n\nইনপুট: {user_query}"]
        if media_url:
            headers = {"Authorization": f"Bearer {s.get('permanent_token')}"}
            m_res = requests.get(media_url, headers=headers, timeout=10)
            if m_res.status_code == 200:
                contents_list.append(types.Part.from_bytes(data=m_res.content, mime_type="audio/ogg" if is_audio else "image/jpeg"))
        return client.models.generate_content(model="gemini-2.5-flash", contents=contents_list, config=cfg).text
    except Exception as e: return "লাইভ এজেন্ট কিছুক্ষণের মধ্যে উত্তর দেবে।"

def send_main_menu_buttons(from_number, text_content="Dhaka Exclusive এ স্বাগতম!"):
    btns = {"type": "button", "body": {"text": text_content}, "action": {"buttons": [{"type": "reply", "reply": {"id": "menu_products", "title": "🛒 প্রোডাক্ট দেখুন"}}, {"type": "reply", "reply": {"id": "menu_complain", "title": "⚠️ কমপ্লেইন বক্স"}}]}}
    send_whatsapp(from_number, "interactive", btns)

# =====================================================================
# SMART WEBHOOK CONTROLLER
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
    if sess and sess.get("bot_paused") == 1: return

    state = sess["state"] if sess else "idle"
    ctx = json.loads(sess["context"]) if sess and sess.get("context") else {}

    # আপডেট ১: স্মার্ট কন্টেক্সট রিকগনিশন লজিক
    positive_keywords = ["হ্যাঁ", "yes", "order করতে চাই", "অর্ডার করতে চাই", "কিনব", "confirm", "এটা নিব"]
    if state == "idle" and any(k in body_text.lower() for k in positive_keywords):
        last_p = db_query("SELECT content FROM messages WHERE from_number=? AND direction='outbound' AND (content LIKE '%মূল্য%' OR content LIKE '%৳%') ORDER BY id DESC LIMIT 1", (from_number,), fetchone=True)
        if last_p:
            db_query("INSERT INTO sessions (phone, state, context) VALUES (?, 'awaiting_name', '{}') ON CONFLICT(phone) DO UPDATE SET state='awaiting_name'", (from_number,), commit=True)
            send_whatsapp(from_number, "text", "📋 চমৎকার ভাইয়া! অর্ডারটি কনফার্ম করার জন্য অনুগ্রহ করে আপনার **पूर्ण नाम** লিখুন:")
            return

    if state == "idle" and any(k in body_text.lower() for k in ["কমপ্লেইন", "অভিযোগ"]):
        db_query("INSERT INTO sessions (phone, state, context) VALUES (?, 'awaiting_complain', '{}') ON CONFLICT(phone) DO UPDATE SET state='awaiting_complain'", (from_number,), commit=True)
        send_whatsapp(from_number, "text", "⚠️ আপনার অভিযোগটি বিস্তারিত লিখে মেসেজ দিন:")
        return

    if state == "awaiting_complain":
        db_query("INSERT INTO complaints (phone, complaint_text) VALUES (?, ?)", (from_number, body_text), commit=True)
        db_query("UPDATE sessions SET state='idle' WHERE phone=?", (from_number,), commit=True)
        send_whatsapp(from_number, "text", "✅ ধন্যবাদ। আপনার অভিযোগটি সিস্টেমে নথিভুক্ত করা হয়েছে।")
        return

    if body_text == "menu_products" or (state == "idle" and any(k in body_text.lower() for k in ["অর্ডার", "buy", "order", "প্রোডাক্ট"])):
        products = db_query("SELECT * FROM products WHERE active = 1 AND stock > 0 LIMIT 10", fetchall=True) or []
        if not products:
            send_whatsapp(from_number, "text", "দুঃখিত ভাই, আমাদের স্টক এখন খালি।")
            return
        rows = [{"id": f"p_{p['id']}", "title": p['name'][:24], "description": f"{p['price']}৳"} for p in products]
        menu = {"type": "list", "body": {"text": "আমাদের ক্যাটালগ থেকে প্রোডাক্ট সিলেক্ট করুন:"}, "action": {"button": "প্রোডাক্টস লিস্ট", "sections": [{"title": "চলতি স্টক", "rows": rows}]}}
        db_query("INSERT INTO sessions (phone, state, context) VALUES (?, 'selecting_product', '{}') ON CONFLICT(phone) DO UPDATE SET state='selecting_product'", (from_number,), commit=True)
        send_whatsapp(from_number, "interactive", menu)
        return

    # অর্ডার ফ্লো প্রসেসর
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
        send_whatsapp(from_number, "text", "📝 আপনার সম্পূর্ণ নাম কি?")
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
        summary = f"🛒 অর্ডারের সামারি:\n\n🛍️ প্রোডাক্ট: {ctx.get('name', 'Product')}\n🔢 পরিমাণ: {ctx.get('quantity', 1)} টি\n💵 মোট বিল: {total}৳\n\nসব তথ্য ঠিক থাকলে নিচের বাটন চাপুন:"
        btns = {"type": "button", "body": {"text": summary}, "action": {"buttons": [{"type": "reply", "reply": {"id": "conf_yes", "title": "অর্ডার কনফার্ম করুন 👍"}}, {"type": "reply", "reply": {"id": "conf_no", "title": "বাতিল করুন ❌"}}]}}
        db_query("UPDATE sessions SET state='awaiting_confirmation', context=? WHERE phone=?", (json.dumps(ctx), from_number), commit=True)
        send_whatsapp(from_number, "interactive", btns)
        return

    if state == "awaiting_confirmation":
        if body_text == "conf_yes":
            total_cod = ctx.get("subtotal", 0) + ctx.get("delivery_fee", 60)
            
            # আপডেট ৭: ডুপ্লিকেট চেক
            check_time = (datetime.now() - timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
            is_dup = db_query("SELECT 1 FROM orders WHERE phone=? AND product_id=? AND created_at > ?", (from_number, ctx.get("product_id"), check_time), fetchone=True)
            dup_flag = 1 if is_dup else 0
            
            c_id = "PENDING_REVIEW"
            if not dup_flag:
                success, consignment_id = create_pathao_order(ctx, from_number, total_cod)
                if success: c_id = consignment_id
                
            db_query("INSERT INTO orders (phone, name, address, product_id, quantity, total, delivery_fee, pathao_consignment_id, status, is_duplicate) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)", (from_number, ctx.get("cust_name"), ctx.get("address"), ctx.get("product_id"), ctx.get("quantity", 1), total_cod, ctx.get("delivery_fee", 60), c_id, dup_flag), commit=True)
            
            if dup_flag: send_whatsapp(from_number, "text", "⚠️ ডুপ্লিকেট অর্ডারের তাগিদ পাওয়া গেছে। প্রতিনিধি চেক করছেন।")
            else: send_whatsapp(from_number, "text", f"🎉 অর্ডার সফল হয়েছে! মোট বিল: {total_cod}৳। ট্র্যাকিং: {c_id}")
            
        db_query("UPDATE sessions SET state='idle', context='{}' WHERE phone=?", (from_number,), commit=True)
        return

    history_rows = db_query("SELECT content, direction FROM messages WHERE from_number=? ORDER BY id DESC LIMIT 6", (from_number,), fetchall=True) or []
    history_str = "\n".join([f"{'কাস্টমার' if r['direction']=='inbound' else 'অ্যাসিস্ট্যান্ট'}: {r['content']}" for r in reversed(history_rows)])
    is_audio = True if msg_type in ["audio", "voice"] else False
    ai_msg = get_ai_multimodal_answer(body_text, history_str, media_url=media_url, is_audio=is_audio)
    send_main_menu_buttons(from_number, ai_msg)

# =====================================================================
# ROUTING CONTROLLER
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
    orders = db_query("SELECT * FROM orders ORDER BY id DESC", fetchall=True) or []
    products = db_query("SELECT * FROM products ORDER BY id DESC", fetchall=True) or []
    users = db_query("SELECT * FROM users ORDER BY last_active DESC LIMIT 30", fetchall=True) or []
    complaints = db_query("SELECT * FROM complaints ORDER BY id DESC", fetchall=True) or []
    settings = get_all_settings()
    active_chat = request.args.get("chat_with", "")
    chat_history = db_query("SELECT * FROM messages WHERE from_number=? ORDER BY id ASC LIMIT 50", (active_chat,), fetchall=True) if active_chat else []
    
    return render_template_string(ADMIN_HTML, orders=orders, products=products, users=users, complaints=complaints, settings=settings, active_chat=active_chat, chat_history=chat_history, msg=request.args.get("msg", ""))

@app.route("/admin/settings/save", methods=["POST"])
def save_settings():
    if session.get("username") != 'admin': return redirect(url_for('admin_portal'))
    for k, v in request.form.items():
        db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v.strip()), commit=True)
    return redirect(url_for('admin_portal', msg="Settings saved!"))

@app.route("/admin/chat/send", methods=["POST"])
def admin_send_message():
    if not session.get("logged_in"): return redirect(url_for('admin_login'))
    phone = request.form.get("phone")
    msg = request.form.get("message")
    if phone and msg:
        send_whatsapp(phone, "text", msg, agent=session.get("username"))
        db_query("UPDATE sessions SET bot_paused = 1 WHERE phone = ?", (phone,), commit=True)
    return redirect(url_for('admin_portal', chat_with=phone))

@app.route("/admin/chat/toggle-bot/<phone>")
def toggle_bot_pause(phone):
    if not session.get("logged_in"): return redirect(url_for('admin_login'))
    s = db_query("SELECT bot_paused FROM sessions WHERE phone=?", (phone,), fetchone=True)
    nxt = 0 if s and s["bot_paused"] == 1 else 1
    db_query("UPDATE sessions SET bot_paused = ? WHERE phone = ?", (nxt, phone), commit=True)
    return redirect(url_for('admin_portal', chat_with=phone, msg="Bot state updated!"))

@app.route("/webhook", methods=["GET"])
def verify():
    s = get_all_settings()
    if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == s.get("verify_token", "dhakaex0020"):
        return request.args.get("hub.challenge"), 200
    return "Invalid", 403

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

# =====================================================================
# UI CODE BASE (RECONSTRUCTED COMPLETE UI)
# =====================================================================
LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Dhaka Exclusive - Admin Login</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: linear-gradient(135deg, #74b9ff, #0984e3); height: 100vh; display: flex; align-items: center; justify-content: center; }
        .card { border-radius: 15px; box-shadow: 0 10px 30px rgba(0,0,0,0.2); }
    </style>
</head>
<body>
    <div class="card p-4 text-center" style="width: 380px; background: white;">
        <h3 class="mb-3 text-primary">Dhaka Exclusive</h3>
        <p class="text-muted mb-4">Ultimate WhatsApp Bot Control Panel</p>
        <form method="POST">
            <input type="text" name="username" class="form-control mb-3" placeholder="ইউজারনেম" required>
            <input type="password" name="password" class="form-control mb-4" placeholder="পাসওয়ার্ড" required>
            <button type="submit" class="btn btn-primary w-100 py-2">লগইন করুন</button>
        </form>
    </div>
</body>
</html>
"""

ADMIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Dhaka Exclusive - Bot Dashboard</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #f5f6fa; font-family: system-ui, -apple-system, sans-serif; }
        .sidebar { background: #2f3640; min-height: 100vh; color: white; padding-top: 20px; }
        .sidebar a { color: #f5f6fa; text-decoration: none; padding: 12px 20px; display: block; border-bottom: 1px solid #40739e; }
        .sidebar a:hover { background: #4b6584; }
        .card-stat { border-radius: 10px; border: none; box-shadow: 0 4px 15px rgba(0,0,0,0.05); transition: 0.3s; }
        .chat-box { height: 400px; overflow-y: auto; background: #fff; border: 1px solid #ddd; padding: 15px; border-radius: 8px; }
        .msg-in { background: #e1ffc7; padding: 8px 12px; border-radius: 8px; margin-bottom: 10px; width: fit-content; max-width: 80%; }
        .msg-out { background: #f1f0f0; padding: 8px 12px; border-radius: 8px; margin-bottom: 10px; width: fit-content; max-width: 80%; margin-left: auto; }
    </style>
</head>
<body>
    <div class="container-fluid">
        <div class="row">
            <div class="col-md-2 sidebar d-none d-md-block">
                <h4 class="text-center text-warning mb-4">📊 Control Engine</h4>
                <a href="#livechat">💬 লাইভ চ্যাট রুম</a>
                <a href="#orders">📦 অর্ডার ট্র্যাকার</a>
                <a href="#complaints">⚠️ কাস্টমার অভিযোগ</a>
                <a href="#config">⚙️ গ্লোবাল কনফিগারেশন</a>
                <a href="/admin/logout" class="text-danger mt-5">🚪 লগআউট</a>
            </div>
            
            <div class="col-md-10 p-4">
                <div class="d-flex justify-content-between align-items-center mb-4">
                    <h2>Dhaka Exclusive - মেগা কন্ট্রোল ড্যাশবোর্ড v8</h2>
                    <a href="/admin/logout" class="btn btn-danger d-md-none">Logout</a>
                </div>
                
                {% if msg %}<div class="alert alert-success alert-dismissible fade show">{{ msg }}</div>{% endif %}

                <div id="livechat" class="card card-stat p-4 mb-4" style="background: white;">
                    <h4 class="text-primary mb-3">💬 লাইভ চ্যাট এবং কাস্টমার ইনবক্স</h4>
                    <div class="row">
                        <div class="col-md-4" style="border-right: 1px solid #eee;">
                            <h5>সক্রিয় কাস্টমার লিস্ট</h5>
                            <div class="list-group">
                                {% for u in users %}
                                <a href="?chat_with={{ u.phone }}" class="list-group-item list-group-item-action {% if active_chat == u.phone %}active{% endif %}">
                                    📞 {{ u.phone }}
                                </a>
                                {% endfor %}
                            </div>
                        </div>
                        <div class="col-md-8">
                            {% if active_chat %}
                            <h5>চ্যাট হিস্ট্রি: <span class="text-muted">{{ active_chat }}</span></h5>
                            <div class="mb-2">
                                <a href="/admin/chat/toggle-bot/{{ active_chat }}" class="btn btn-sm btn-warning">🤖 বটের অটো-রিপ্লাই টগল করুন</a>
                            </div>
                            <div class="chat-box mb-3 d-flex flex-column">
                                {% for h in chat_history %}
                                <div class="{% if h.direction == 'inbound' %}msg-in{% else %}msg-out{% endif %}">
                                    <strong>{{ h.agent_id }}:</strong> {{ h.content }}
                                    <small class="d-block text-muted" style="font-size: 10px;">{{ h.created_at }}</small>
                                </div>
                                {% endfor %}
                            </div>
                            <form action="/admin/chat/send" method="POST">
                                <input type="hidden" name="phone" value="{{ active_chat }}">
                                <div class="input-group">
                                    <input type="text" name="message" class="form-control" placeholder="এখানে বাংলায় উত্তর লিখুন..." required>
                                    <button class="btn btn-primary" type="submit">মেসেজ পাঠান</button>
                                </div>
                            </form>
                            {% else %}
                            <p class="text-center text-muted my-5">বামের কাস্টমার লিস্ট থেকে যেকোনো একটি চ্যাট সিলেক্ট করুন।</p>
                            {% endif %}
                        </div>
                    </div>
                </div>

                <div id="orders" class="card card-stat p-4 mb-4" style="background: white;">
                    <h4 class="text-success mb-3">📦 কাস্টমার অর্ডার লডার গেটওয়ে</h4>
                    <div class="table-responsive">
                        <table class="table table-striped align-middle">
                            <thead class="table-dark">
                                <tr>
                                    <th>ID</th>
                                    <th>ফোন নম্বর</th>
                                    <th>নাম</th>
                                    <th>ঠিকানা</th>
                                    <th>মোট বিল</th>
                                    <th>পাঠাও কনসাইনমেন্ট</th>
                                    <th>ডুপ্লিকেট?</th>
                                    <th>তারিখ</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for o in orders %}
                                <tr>
                                    <td>#{{ o.id }}</td>
                                    <td>{{ o.phone }}</td>
                                    <td>{{ o.name }}</td>
                                    <td>{{ o.address }}</td>
                                    <td>{{ o.total }}৳</td>
                                    <td><span class="badge bg-primary">{{ o.pathao_consignment_id }}</span></td>
                                    <td>
                                        {% if o.is_duplicate == 1 %}
                                        <span class="badge bg-danger">⚠️ ডুপ্লিকেট ফ্ল্যাগ</span>
                                        {% else %}
                                        <span class="badge bg-success">ইউনিক</span>
                                        {% endif %}
                                    </td>
                                    <td>{{ o.created_at }}</td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>

                <div id="complaints" class="card card-stat p-4 mb-4" style="background: white;">
                    <h4 class="text-danger mb-3">⚠️ কাস্টমার অভিযোগ ও কমপ্লেইন ট্র্যাকিং</h4>
                    <div class="table-responsive">
                        <table class="table table-bordered table-hover">
                            <thead class="table-danger">
                                <tr>
                                    <th>ID</th>
                                    <th>কাস্টমার ফোন</th>
                                    <th>অভিযোগের বিবরণ</th>
                                    <th>স্ট্যাটাস</th>
                                    <th>তারিখ</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for c in complaints %}
                                <tr>
                                    <td>#{{ c.id }}</td>
                                    <td>{{ c.phone }}</td>
                                    <td>{{ c.complaint_text }}</td>
                                    <td><span class="badge bg-warning text-dark">{{ c.status }}</span></td>
                                    <td>{{ c.created_at }}</td>
                                </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                </div>

                <div id="config" class="card card-stat p-4 mb-4" style="background: white;">
                    <h4 class="text-secondary mb-3">⚙️ গ্লোবাল এপিআই ও বট সেটিংস কনফিগারেশন</h4>
                    <form action="/admin/settings/save" method="POST">
                        <div class="row">
                            <div class="col-md-6 mb-3">
                                <label class="form-label">ব্যবসার নাম</label>
                                <input type="text" name="business_name" class="form-control" value="{{ settings.business_name }}">
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label">মেটা ভেরিফাই টোকেন (Webhook)</label>
                                <input type="text" name="verify_token" class="form-control" value="{{ settings.verify_token }}">
                            </div>
                            <div class="col-md-12 mb-3">
                                <label class="form-label">হোয়াটসঅ্যাপ পার্মানেন্ট টোকেন (Meta Cloud API)</label>
                                <textarea name="permanent_token" class="form-control" rows="2">{{ settings.permanent_token }}</textarea>
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label">ফোন নম্বর আইডি (Phone Number ID)</label>
                                <input type="text" name="phone_number_id" class="form-control" value="{{ settings.phone_number_id }}">
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label">জেমিনি এআই এপিআই কি (Gemini API Key)</label>
                                <input type="password" name="gemini_key" class="form-control" value="{{ settings.gemini_key }}">
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label">ফেসবুক ক্যাটালগ আইডি (Meta Commerce Catalogue ID)</label>
                                <input type="text" name="fb_catalogue_id" class="form-control" value="{{ settings.fb_catalogue_id or '' }}">
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label">ফেসবুক ক্যাটালগ অ্যাক্সেস টোকেন</label>
                                <input type="password" name="fb_access_token" class="form-control" value="{{ settings.fb_access_token or '' }}">
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label">ব্যাকআপ ইমেইল অ্যাড্রেস</label>
                                <input type="email" name="backup_email" class="form-control" value="{{ settings.backup_email }}">
                            </div>
                            <div class="col-md-6 mb-3">
                                <label class="form-label">বিকাশ পার্সোনাল নম্বর</label>
                                <input type="text" name="bkash_number" class="form-control" value="{{ settings.bkash_number }}">
                            </div>
                        </div>
                        <button type="submit" class="btn btn-success px-5 py-2 mt-2">সংরক্ষণ করুন</button>
                    </form>
                </div>

            </div>
        </div>
    </div>
</body>
</html>
"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
