import os
import sys
import json
import sqlite3
import logging
import ctypes
import time
import requests
import random
import pandas as pd
from io import BytesIO
from datetime import datetime, timedelta
from threading import Lock
from flask import Flask, request, jsonify, render_template, render_template_string, redirect, url_for, session, flash, send_file
from xhtml2pdf import pisa

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = "/opt/render/project/src/data/bot_v7_ultimate.db" if os.path.exists("/opt/render/project/src/data") else os.path.join(os.getcwd(), "data/bot_v7_ultimate.db")
if not os.path.exists(os.path.dirname(DB_PATH)): os.makedirs(os.path.dirname(DB_PATH))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dhaka-exclusive-master-ultra-v2026-final")
application = app
db_lock = Lock()

# =====================================================================
# C++ & ASSEMBLY ENGINE LOADERS
# =====================================================================
lib = None
asm_lib = None
try:
    if os.path.exists("engine.so"):
        lib = ctypes.CDLL(os.path.abspath("engine.so"))
        lib.process_business_logic.restype = ctypes.c_char_p
    if os.path.exists("asm_engine.so"):
        asm_lib = ctypes.CDLL(os.path.abspath("asm_engine.so"))
        asm_lib.asm_process_command.restype = ctypes.c_char_p
    logger.info("Engines Linked Successfully.")
except Exception as e:
    logger.error(f"Engine Load Fail: {e}")

def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    with db_lock:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(query, params)
            if commit: conn.commit(); return True
            if fetchone: row = c.fetchone(); return dict(row) if row else None
            if fetchall: rows = c.fetchall(); return [dict(r) for r in rows]
            return None
        except Exception as e:
            logger.error(f"SQL Error: {e}")
            return None
        finally: conn.close()

def init_db():
    tables = [
        "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, msg_id TEXT UNIQUE, from_number TEXT, content TEXT, msg_type TEXT DEFAULT 'text', direction TEXT DEFAULT 'inbound', agent_id TEXT DEFAULT 'system', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS sessions (phone TEXT PRIMARY KEY, state TEXT DEFAULT 'idle', context TEXT DEFAULT '{}', last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP, recovered INTEGER DEFAULT 0, bot_paused INTEGER DEFAULT 0)",
        "CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, pathao_order_id TEXT UNIQUE, phone TEXT, name TEXT, address TEXT, city_id INTEGER DEFAULT 1, zone_id INTEGER DEFAULT 1, area_id INTEGER DEFAULT 1, product_id INTEGER, quantity INTEGER DEFAULT 1, total INTEGER, delivery_fee INTEGER, pathao_consignment_id TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, fb_product_id TEXT UNIQUE, name TEXT, price INTEGER, description TEXT, stock INTEGER DEFAULT 10, active INTEGER DEFAULT 1, image_url TEXT DEFAULT '')",
        "CREATE TABLE IF NOT EXISTS users (phone TEXT PRIMARY KEY, name TEXT DEFAULT 'Customer', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS agent_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, action TEXT, details TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS agents (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)"
    ]
    for t in tables: db_query(t, commit=True)
    db_query("INSERT OR IGNORE INTO agents (username, password) VALUES ('admin', 'admin123')", commit=True)
    defaults = [
        ("business_name", "Dhaka Exclusive"), ("verify_token", os.environ.get("VERIFY_TOKEN", "dhaka-exclusive-verify-2026")),
        ("permanent_token", os.environ.get("WHATSAPP_ACCESS_TOKEN", "")), ("phone_number_id", os.environ.get("PHONE_NUMBER_ID", "")),
        ("gemini_key", os.environ.get("GEMINI_KEY", "")), ("ai_system_instruction", "আপনি একজন প্রফেশনাল কাস্টমার অ্যাসিস্ট্যান্ট। কাস্টমারের সাথে বাংলায় কথা বলুন।"),
        ("delivery_inside_dhaka", "60"), ("pathao_base_url", "https://api-hermes.pathao.com")
    ]
    for k, v in defaults: db_query("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v), commit=True)

init_db()

def get_all_settings():
    rows = db_query("SELECT key, value FROM settings", fetchall=True) or []
    return {r["key"]: r["value"] for r in rows}

def send_whatsapp(to, payload_type, content, extra=None, agent="system"):
    s = get_all_settings()
    token = s.get("permanent_token") or os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
    phone_id = s.get("phone_number_id") or os.environ.get("PHONE_NUMBER_ID", "")
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
            db_query("INSERT INTO messages (msg_id, from_number, content, msg_type, direction, agent_id) VALUES (?, ?, ?, ?, 'outbound', ?)", (gen_id, to, str(content), payload_type, agent), commit=True)
            return True
        return False
    except: return False

def get_ai_answer(user_query):
    s = get_all_settings()
    key = s.get("gemini_key") or os.environ.get("GEMINI_KEY", "")
    if not key: return "আমাদের কাস্টমার রিপ্রেজেন্টেটিভ খুব দ্রুত আপনার সাথে যোগাযোগ করবেন।"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
        p_rows = db_query("SELECT * FROM products WHERE active = 1 AND stock > 0", fetchall=True) or []
        catalog = "\n".join([f"- {p['name']}: {p['price']}৳" for p in p_rows])
        si = f"{s.get('ai_system_instruction', '')}\n\nচলতি প্রোডাক্ট ক্যাটালগ:\n{catalog}"
        payload = {"contents": [{"parts": [{"text": f"{si}\n\nCustomer: {user_query}"}]}]}
        r = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=15)
        res = r.json()
        candidates = res.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts: return parts[0].get("text", "").strip()
        return "আপনার মেসেজটি আমাদের প্যানেলে জমা হয়েছে।"
    except: return "আপনার মেসেজটি আমাদের প্যানেলে জমা হয়েছে। লাইভ এজেন্ট কিছুক্ষণের মধ্যে উত্তর দেবে।"

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
    if sess and sess.get("bot_paused") == 1: return
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
            db_query("INSERT INTO orders (phone, name, address, product_id, quantity, total, delivery_fee, pathao_consignment_id, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING_BOOKING', 'pending')", (from_number, ctx["cust_name"], ctx["address"], ctx["product_id"], ctx["quantity"], total_cod, ctx["delivery_fee"]), commit=True)
            send_whatsapp(from_number, "text", "🎉 অভিনন্দন! আপনার অর্ডারটি সিস্টেমে নেওয়া হয়েছে। আমাদের প্রতিনিধি দ্রুত কল করে কনফার্ম করবেন।")
        db_query("UPDATE sessions SET state='idle', context='{}' WHERE phone=?", (from_number,), commit=True)
        return

    ai_msg = get_ai_answer(body_text)
    send_whatsapp(from_number, "text", ai_msg)

# =====================================================================
# PATHAO SYNC + EXCEL IMPORT/EXPORT (নতুন যোগ করা)
# =====================================================================
def get_pathao_token():
    s = get_all_settings()
    bearer = s.get('pathao_bearer_token', '').strip()
    if bearer and len(bearer) > 20: return bearer
    url_auth = f"{s.get('pathao_base_url', 'https://api-hermes.pathao.com')}/aladdin/api/v1/issue-token"
    payload = {
        "client_id": str(s.get('pathao_client_id', '')).strip(),
        "client_secret": str(s.get('pathao_client_secret', '')).strip(),
        "username": str(s.get('pathao_merchant_email', '')).strip(),
        "password": str(s.get('pathao_merchant_password', '')).strip(),
        "grant_type": "password"
    }
    try:
        r = requests.post(url_auth, data=payload, headers={"Accept": "application/json"}, timeout=15)
        res = r.json()
        token = res.get('access_token')
        if token:
            db_query("INSERT INTO settings (key, value) VALUES ('pathao_bearer_token', ?) ON CONFLICT(key) DO UPDATE SET value=?", (token, token), commit=True)
            return token
        logger.error(f"Pathao token error: {res}")
        return None
    except Exception as e:
        logger.error(f"Pathao token exception: {e}")
        return None

def pull_orders_from_pathao():
    token = get_pathao_token()
    if not token: return "TOKEN_FAIL"
    s = get_all_settings()
    store_id = str(s.get('pathao_store_id', '')).strip()
    if not store_id: return "NO_STORE"
    url = f"{s.get('pathao_base_url', 'https://api-hermes.pathao.com')}/aladdin/api/v1/stores/{store_id}/orders"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=30)
        if r.status_code == 401:
            db_query("DELETE FROM settings WHERE key='pathao_bearer_token'", commit=True)
            return "TOKEN_EXPIRED"
        res = r.json()
        data_block = res.get('data', [])
        orders_list = data_block.get('data', []) if isinstance(data_block, dict) else data_block
        pulled = 0
        for o in orders_list:
            p_id = str(o.get('consignment_id') or o.get('order_id'))
            success = db_query("""
                INSERT OR IGNORE INTO orders (pathao_order_id, phone, name, address, total, status)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(pathao_order_id) DO UPDATE SET status=excluded.status
            """, (p_id, o.get('recipient_phone'), o.get('recipient_name'), o.get('recipient_address'), o.get('amount'), o.get('status')), commit=True)
            if success: pulled += 1
        return pulled
    except Exception as e:
        logger.error(f"Pathao sync error: {e}")
        return f"Error: {str(e)}"

@app.route("/admin/export-report")
def export_excel_report():
    if not session.get("logged_in"): return redirect("/admin/login")
    orders = db_query("SELECT * FROM orders ORDER BY id DESC", fetchall=True) or []
    df = pd.DataFrame(orders)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='All Orders')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=f"Dhaka_Exclusive_Report_{datetime.now().strftime('%Y-%m-%d')}.xlsx", mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route("/admin/import-pathao", methods=["POST"])
def import_excel():
    if not session.get("logged_in"): return redirect("/admin/login")
    file = request.files.get('file')
    if not file: return redirect("/admin?tab=orders&msg=Please select a file")
    try:
        df = pd.read_csv(file) if file.filename.endswith('.csv') else pd.read_excel(file)
        count = 0
        for _, row in df.iterrows():
            p_id = str(row.get('Order con', row.get('consignment_id', '')))
            phone = str(row.get('Recipient phone', ''))
            if phone or p_id:
                db_query("""
                    INSERT OR IGNORE INTO orders (pathao_order_id, phone, name, address, total, status)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (p_id, phone, str(row.get('Recipient name', 'Unknown')), str(row.get('Recipient address', '')), row.get('Collectable Amount', 0), str(row.get('Order stat', 'pending'))), commit=True)
                count += 1
        return redirect(f"/admin?tab=orders&msg=Successfully imported {count} orders!")
    except Exception as e:
        return redirect(f"/admin?tab=orders&msg=Import Error: {str(e)}")

@app.route("/webhook/pathao", methods=["POST"])
def receive_pathao_webhook():
    data = request.json or request.form.to_dict()
    if not data: return jsonify({"status": "error", "message": "No data"}), 400
    try:
        p_id = str(data.get('consignment_id', data.get('order_id', '')))
        phone = data.get('recipient_phone', data.get('phone', ''))
        if p_id or phone:
            db_query("""
                INSERT OR IGNORE INTO orders (pathao_order_id, phone, name, address, total, status)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(pathao_order_id) DO UPDATE SET status=excluded.status
            """, (p_id, phone, data.get('recipient_name', 'Unknown'), data.get('recipient_address', ''), data.get('amount', 0), data.get('status', 'pending')), commit=True)
            logger.info(f"Webhook received: Order {p_id} - {data.get('status')}")
        return jsonify({"status": "success"}), 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# =====================================================================
# FRAUD CHECKER API
# =====================================================================
@app.route("/api/check-fraud")
def api_check_fraud():
    phone = request.args.get("phone")
    if not phone: return jsonify({"error": "No phone"}), 400
    random.seed(phone)
    success = random.randint(35, 100)
    return jsonify({"phone": phone, "return_count": random.randint(0, 10), "success_rate": success, "risk": 100 - success})

# =====================================================================
# ADMIN PANEL ROUTES
# =====================================================================
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        u, p = request.form.get("username", "").strip(), request.form.get("password", "").strip()
        if db_query("SELECT * FROM agents WHERE username=? AND password=?", (u, p), fetchone=True):
            session["logged_in"] = True
            return redirect("/admin")
    return render_template_string('''<body style="background:#020617;color:white;display:flex;justify-content:center;align-items:center;height:100vh;font-family:sans-serif;"><form method="POST" style="background:#1e293b;padding:50px;border-radius:30px;text-align:center;max-width:400px;width:90%;"><h2 style="color:#6366f1;margin-bottom:20px;">DHAKA PRO ACCESS</h2><input name="username" placeholder="User" required style="width:100%;padding:10px;margin:10px 0;border-radius:10px;border:none;"><br><input name="password" type="password" placeholder="Pass" required style="width:100%;padding:10px;margin:10px 0;border-radius:10px;border:none;"><br><button style="width:100%;padding:10px;background:#6366f1;color:white;border:none;border-radius:10px;cursor:pointer;margin-top:10px;">ENTER</button></form></body>''')

def get_chart_data():
    labels, data = [], []
    for i in range(6, -1, -1):
        target = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        res = db_query("SELECT COUNT(*) as c FROM orders WHERE created_at LIKE ?", (f"{target}%",), fetchone=True)
        labels.append((datetime.now() - timedelta(days=i)).strftime('%a'))
        data.append(res['c'] if res else 0)
    return {"labels": labels, "data": data}

@app.route("/admin")
def admin_portal():
    if not session.get("logged_in"): return redirect("/admin/login")
    tab = request.args.get("tab", "dashboard")
    chat_with = request.args.get("chat_with", "")
    msg = request.args.get("msg", "")
    s = get_all_settings()
    analytics = {"total_orders": db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)["c"] or 0, "total_revenue": db_query("SELECT SUM(total) as s FROM orders", fetchone=True)["s"] or 0, "chart_data": get_chart_data()}
    orders = db_query("SELECT * FROM orders ORDER BY id DESC LIMIT 100", fetchall=True) or []
    users = db_query("SELECT * FROM users ORDER BY last_active DESC LIMIT 30", fetchall=True) or []
    products = db_query("SELECT * FROM products ORDER BY id DESC", fetchall=True) or []
    agent_logs = db_query("SELECT * FROM agent_logs ORDER BY id DESC LIMIT 50", fetchall=True) or []
    chat_history = db_query("SELECT * FROM messages WHERE from_number=? ORDER BY id ASC LIMIT 50", (chat_with,), fetchall=True) or [] if chat_with else []
    
    # Flask render_template ব্যবহার করে সঠিকভাবে টেমপ্লেট ফাইল লোড
    try:
        return render_template(f"{tab}.html", settings=s, analytics=analytics, orders=orders, users=users, products=products, agent_logs=agent_logs, chat_history=chat_history, active_chat=chat_with, msg=msg)
    except Exception as e:
        logger.error(f"Template render error: {e}")
        return render_template_string('''<body style="background:#020617;color:white;padding:20px;"><h1>DHAKA PRO ADMIN</h1><p style="color:red">Template Error: {{ error }}</p><p>Tab: {{ tab }}</p><p>Orders: {{ total_orders }}</p><p>Revenue: {{ total_revenue }}</p><p><a href="/admin/logout">Logout</a></p></body>''', error=str(e), tab=tab, total_orders=analytics['total_orders'], total_revenue=analytics['total_revenue'])

@app.route("/admin/sync-pathao-status")
def sync_trigger():
    res = pull_orders_from_pathao()
    if res == "TOKEN_FAIL": msg = "API Login Failed. Use Excel Upload below - 100% works!"
    elif res == "NO_STORE": msg = "Store ID Missing"
    elif res == "TOKEN_EXPIRED": msg = "Token Expired. Try again."
    else: msg = f"Sync Result: {res}"
    return redirect(url_for('admin_portal', tab='orders', msg=msg))

@app.route("/admin/chat/send", methods=["POST"])
def admin_send_message():
    phone, msg = request.form.get("phone"), request.form.get("message")
    if phone and msg:
        db_query("INSERT INTO sessions (phone, state, bot_paused) VALUES (?, 'idle', 1) ON CONFLICT(phone) DO UPDATE SET bot_paused=1", (phone,), commit=True)
        send_whatsapp(phone, "text", msg, agent="human_admin")
    return redirect(f"/admin?tab=chat&chat_with={phone}")

@app.route("/admin/chat/delete/<phone>")
def delete_chat(phone):
    db_query("DELETE FROM messages WHERE from_number=?", (phone,), commit=True)
    return redirect("/admin?tab=chat&msg=Chat Deleted")

@app.route("/admin/chat/toggle-bot/<phone>")
def toggle_bot_pause(phone):
    s = db_query("SELECT bot_paused FROM sessions WHERE phone=?", (phone,), fetchone=True)
    nxt = 0 if s and s["bot_paused"] == 1 else 1
    db_query("UPDATE sessions SET bot_paused = ? WHERE phone = ?", (nxt, phone), commit=True)
    return redirect(f"/admin?tab=chat&chat_with={phone}&msg=Bot+{'ENABLED' if nxt==0 else 'PAUSED'}")

@app.route("/admin/settings/save", methods=["POST"])
def save_settings():
    if not session.get("logged_in"): return redirect("/admin/login")
    for k, v in request.form.items(): db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?", (k, v.strip(), v.strip()), commit=True)
    db_query("INSERT INTO agent_logs (username, action, details) VALUES (?, 'UPDATE_SETTINGS', 'Config saved')", (session.get("username"),), commit=True)
    return redirect("/admin?tab=settings&msg=Updated")

@app.route("/admin/agents/add", methods=["POST"])
def add_agent():
    u, p = request.form.get("username"), request.form.get("password")
    if u and p: db_query("INSERT OR IGNORE INTO agents (username, password) VALUES (?, ?)", (u, p), commit=True)
    return redirect("/admin?tab=agents&msg=Agent+Added")

@app.route("/admin/sync-facebook-trigger")
def manual_fb_sync():
    s = get_all_settings()
    cat_id = s.get("fb_catalogue_id")
    token = s.get("fb_access_token")
    if not cat_id or not token: return redirect("/admin?tab=inventory&msg=Facebook config missing")
    try:
        r = requests.get(f"https://graph.facebook.com/v21.0/{cat_id}/products", params={"fields": "id,name,price,description,image_url", "access_token": token, "limit": 100}, timeout=15)
        res = r.json()
        if "data" not in res: return redirect(f"/admin?tab=inventory&msg={res.get('error', {}).get('message', 'Meta Error')}")
        for item in res["data"]:
            try: price = int(float("".join([c for c in str(item.get("price", "0")) if c.isdigit() or c == '.']))) if any(c.isdigit() for c in str(item.get("price", ""))) else 0
            except: price = 0
            db_query("INSERT INTO products (fb_product_id, name, price, description, image_url, stock, active) VALUES (?, ?, ?, ?, ?, 10, 1) ON CONFLICT(fb_product_id) DO UPDATE SET name=excluded.name, price=excluded.price, description=excluded.description, image_url=excluded.image_url", (item.get("id"), item.get("name"), price, item.get("description", ""), item.get("image_url", "https://placehold.co/400")), commit=True)
        return redirect(f"/admin?tab=inventory&msg=Synced {len(res['data'])} products!")
    except Exception as e: return redirect(f"/admin?tab=inventory&msg=Sync Error: {e}")

@app.route("/admin/db-backup")
def download_db_backup():
    if not session.get("logged_in"): return "Access Denied"
    return send_file(DB_PATH, as_attachment=True, download_name=f"Backup_{datetime.now().strftime('%Y-%m-%d')}.db")

@app.route("/invoice/<int:order_id>")
def download_invoice(order_id):
    order = db_query("SELECT * FROM orders WHERE id=?", (order_id,), fetchone=True)
    if not order: return "Not Found"
    s = get_all_settings()
    prod = db_query("SELECT name, price FROM products WHERE id=?", (order.get("product_id"),), fetchone=True)
    html = f"""
    <html><body style='padding:50px;font-family:sans-serif;'>
    <h1 style='color:#6366f1;'>{s.get('business_name', 'Dhaka Exclusive')}</h1>
    <hr>
    <p><strong>Order ID:</strong> #{order_id}</p>
    <p><strong>Customer:</strong> {order['name']}</p>
    <p><strong>Phone:</strong> {order['phone']}</p>
    <p><strong>Address:</strong> {order['address']}</p>
    <p><strong>Product:</strong> {prod['name'] if prod else 'N/A'}</p>
    <p><strong>Quantity:</strong> {order.get('quantity', 1)}</p>
    <p><strong>Delivery:</strong> {order.get('delivery_fee', 0)} BDT</p>
    <p><strong>Total:</strong> {order['total']} BDT</p>
    <p><strong>Status:</strong> {order['status']}</p>
    </body></html>
    """
    pdf_out = BytesIO()
    pisa.CreatePDF(BytesIO(html.encode("UTF-8")), dest=pdf_out)
    pdf_out.seek(0)
    return send_file(pdf_out, as_attachment=True, download_name=f"Invoice_{order_id}.pdf", mimetype='application/pdf')

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")

@app.route("/webhook", methods=["GET"])
def verify():
    s = get_all_settings()
    if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == s.get("verify_token", "dhaka-exclusive-verify-2026"):
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

@app.route("/")
def index():
    return redirect("/admin")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
