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
app.secret_key = os.environ.get("SECRET_KEY", "default-secret-key-123")

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
asm_lib = None
try:
    if os.path.exists("asm_engine.so"):
        asm_lib = ctypes.CDLL(os.path.abspath("asm_engine.so"))
        asm_lib.asm_process_command.restype = ctypes.c_char_p
        asm_lib.asm_strlen.restype = ctypes.c_uint64
        asm_lib.asm_checksum.restype = ctypes.c_uint64
        logger.info("Assembly Engine loaded: asm_engine.so")
    else:
        logger.warning("No Assembly engine .so file found")
except Exception as e:
    logger.error(f"Assembly Engine Load Error: {e}")

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
            ("ai_system_instruction", "আপনি একজন প্রফেশনাল কাস্টমার অ্যাসিস্ট্যান্ট। কাস্টমারের সাথে বাংলায় বিনীতভাবে কথা বলুন এবং প্রোডাক্ট কিনতে সাহায্য করুন।"),
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
            img_url = item.get("image_url", "") or DEFAULT_PRODUCT_IMAGE
            
            db_query('''
                INSERT INTO products (fb_product_id, name, price, description, image_url, stock, active)
                VALUES (?, ?, ?, ?, ?, 10, 1)
                ON CONFLICT(fb_product_id) DO UPDATE SET name=excluded.name, price=excluded.price, description=excluded.description, image_url=excluded.image_url
            ''', (fb_id, name, 100, desc, img_url), commit=True)
            sync_count += 1
        return True, f"সফলভাবে {sync_count}টি প্রোডাক্ট ফেসবুক ক্যাটালগ থেকে সিঙ্ক হয়েছে!"
    except Exception as e:
        return False, str(e)

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
    
    try:
        r = requests.post(url, json=body, headers=headers, timeout=10)
        if r.status_code in [200, 201]:
            db_query("INSERT INTO messages (msg_id, from_number, content, msg_type, direction, agent_id) VALUES (?, ?, ?, ?, 'outbound', ?)",
                     (f"out_{int(time.time())}", to, str(content), payload_type, agent), commit=True)
            return True
        return False
    except: return False

def get_ai_answer(user_query, chat_history_str=""):
    s = get_all_settings()
    key = s.get("gemini_key")
    if not key: return "আমাদের প্রতিনিধি যোগাযোগ করবেন।"
    return "AI response dummy (Gemini integration active)"

# =====================================================================
# ADMIN DASHBOARD HTML
# =====================================================================
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ settings.get('business_name', 'Control Station') }}</title>
<script src="https://cdn.tailwindcss.com"></script>
<link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css" rel="stylesheet">
</head>
<body class="bg-slate-900 text-slate-100 min-h-screen font-sans antialiased flex flex-col md:flex-row">

<aside class="w-full md:w-72 bg-slate-950 border-r border-slate-800 flex flex-col">
    <div class="p-5 border-b border-slate-800 text-center">
        <h1 class="text-xl font-black text-indigo-400"><i class="fa-solid fa-robot"></i> {{ settings.get('business_name') }}</h1>
        <div class="text-xs text-slate-400">User: {{ session.get('username') }}</div>
    </div>
    <nav class="p-3 flex flex-col gap-1">
        <button onclick="switchTab('orders')" class="tab-btn flex items-center justify-between px-3 py-2.5 rounded-xl bg-indigo-600 text-white font-bold">
            <span><i class="fa-solid fa-wallet mr-2"></i>Orders</span>
        </button>
        <button onclick="switchTab('livechat')" class="tab-btn flex items-center justify-between px-3 py-2.5 rounded-xl text-slate-400 hover:bg-slate-800">
            <span><i class="fa-solid fa-comments mr-2"></i>Live Chat</span>
            {% if unread_chat_count > 0 %}<span class="bg-amber-500 text-black text-[10px] px-1.5 rounded-full">{{ unread_chat_count }}</span>{% endif %}
        </button>
        <button onclick="switchTab('complaints')" class="tab-btn flex items-center justify-between px-3 py-2.5 rounded-xl text-slate-400 hover:bg-slate-800">
            <span><i class="fa-solid fa-triangle-exclamation mr-2"></i>Complaints</span>
            {% if pending_complaints_count > 0 %}<span class="bg-rose-500 text-white text-[10px] px-1.5 rounded-full">{{ pending_complaints_count }}</span>{% endif %}
        </button>
        <button onclick="switchTab('config')" class="tab-btn flex items-center px-3 py-2.5 rounded-xl text-slate-400 hover:bg-slate-800">
            <i class="fa-solid fa-sliders mr-2"></i>Settings
        </button>
        <a href="/admin/logout" class="px-3 py-2.5 text-rose-400 hover:bg-rose-950/20 rounded-xl mt-10">Logout</a>
    </nav>
</aside>

<main class="flex-1 p-6 overflow-y-auto">
    {% if msg %}<div class="bg-emerald-500/10 border border-emerald-500/20 p-3 mb-6 rounded-xl text-emerald-400">{{ msg }}</div>{% endif %}

    <div id="tab-orders" class="tab-content">
        <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
            <div class="bg-slate-950 p-4 rounded-xl border border-slate-800">
                <div class="text-2xl font-black text-indigo-400">{{ analytics.total_orders }}</div>
                <div class="text-xs text-slate-500 uppercase">Total Orders</div>
            </div>
            <div class="bg-slate-950 p-4 rounded-xl border border-slate-800">
                <div class="text-2xl font-black text-emerald-400">{{ analytics.total_revenue }}৳</div>
                <div class="text-xs text-slate-500 uppercase">Revenue</div>
            </div>
        </div>
        
        <div class="bg-slate-950 rounded-2xl border border-slate-800 overflow-hidden">
            <table class="w-full text-left text-sm">
                <thead class="bg-slate-900 text-slate-400">
                    <tr><th class="p-4">Order</th><th class="p-4">Customer</th><th class="p-4">COD</th><th class="p-4">Status</th></tr>
                </thead>
                <tbody>
                    {% for o in orders %}
                    <tr class="border-b border-slate-800">
                        <td class="p-4">#{{ o.id }}</td>
                        <td class="p-4"><b>{{ o.name }}</b><br>{{ o.phone }}</td>
                        <td class="p-4 text-emerald-400">{{ o.total }}৳</td>
                        <td class="p-4">{{ o.status|upper }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>

    <div id="tab-livechat" class="tab-content hidden">
        <h2 class="text-xl font-bold mb-4">Live Chat</h2>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div class="bg-slate-950 rounded-xl p-4 border border-slate-800 h-96 overflow-y-auto">
                {% for u in users %}
                <a href="/admin?chat_with={{ u.phone }}#livechat" class="block p-2 border-b border-slate-800 hover:text-indigo-400">{{ u.phone }}</a>
                {% endfor %}
            </div>
            <div class="md:col-span-2 bg-slate-950 rounded-xl border border-slate-800 flex flex-col h-96">
                <div class="p-3 border-b border-slate-800 text-indigo-400 font-bold">{{ active_chat or 'Select Customer' }}</div>
                <div class="flex-1 p-4 overflow-y-auto space-y-2">
                    {% for m in chat_history %}
                    <div class="p-2 rounded-lg text-xs {% if m.direction == 'inbound' %}bg-slate-800 self-start{% else %}bg-indigo-600 self-end{% endif %}">
                        {{ m.content }}
                    </div>
                    {% endfor %}
                </div>
                {% if active_chat %}
                <form action="/admin/chat/send" method="POST" class="p-2 border-t border-slate-800 flex gap-2">
                    <input type="hidden" name="phone" value="{{ active_chat }}">
                    <input name="message" class="bg-slate-900 flex-1 p-2 rounded text-xs" placeholder="Reply...">
                    <button class="bg-indigo-600 px-4 rounded text-xs">Send</button>
                </form>
                {% endif %}
            </div>
        </div>
    </div>

    <div id="tab-config" class="tab-content hidden">
        <form action="/admin/settings/save" method="POST" class="bg-slate-950 p-6 rounded-xl border border-slate-800 space-y-4">
            <div><label class="block text-xs font-bold text-slate-500 mb-1">BUSINESS NAME</label>
            <input type="text" name="business_name" value="{{ settings.get('business_name', '') }}" class="w-full bg-slate-900 p-2 rounded border border-slate-800"></div>
            <div><label class="block text-xs font-bold text-slate-500 mb-1">WA PHONE ID</label>
            <input type="text" name="phone_number_id" value="{{ settings.get('phone_number_id', '') }}" class="w-full bg-slate-900 p-2 rounded border border-slate-800"></div>
            <button type="submit" class="bg-indigo-600 w-full py-2 rounded font-bold">Save Settings</button>
        </form>
    </div>
</main>

<script>
function switchTab(t) {
    document.querySelectorAll('.tab-content').forEach(e => e.classList.add('hidden'));
    document.getElementById('tab-'+t).classList.remove('hidden');
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('bg-indigo-600','text-white','font-bold'));
}
if(window.location.hash) switchTab(window.location.hash.replace('#',''));
</script>
</body>
</html>
"""

# =====================================================================
# ROUTES
# =====================================================================

@app.route("/")
def index(): return redirect("/admin")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        u = request.form.get("username")
        p = request.form.get("password")
        agent = db_query("SELECT * FROM agents WHERE username = ? AND password = ?", (u, p), fetchone=True)
        if agent:
            session["logged_in"] = True
            session["username"] = agent["username"]
            return redirect("/admin")
        return "Login Failed"
    return """<form method='POST' style='margin-top:100px; text-align:center;'>
        <h2>Login</h2><input name='username' placeholder='User'><br>
        <input name='password' type='password' placeholder='Pass'><br>
        <button>Login</button></form>"""

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")

@app.route("/admin")
def admin_portal():
    if not session.get("logged_in"): return redirect("/admin/login")
    
    s = get_all_settings()
    msg = request.args.get("msg", "")
    chat_with = request.args.get("chat_with", "")

    # Stats for Analytics
    analytics_data = {
        "total_orders": db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)["c"],
        "total_revenue": db_query("SELECT SUM(total) as s FROM orders", fetchone=True)["s"] or 0
    }
    
    # Missing variable fixes
    unread_chat_count = db_query("SELECT COUNT(*) as c FROM messages WHERE direction='inbound'", fetchone=True)["c"]
    pending_complaints_count = db_query("SELECT COUNT(*) as c FROM complaints WHERE status='pending'", fetchone=True)["c"]
    
    orders = db_query("SELECT * FROM orders ORDER BY id DESC LIMIT 50", fetchall=True) or []
    users = db_query("SELECT * FROM users ORDER BY last_active DESC", fetchall=True) or []
    chat_history = db_query("SELECT * FROM messages WHERE from_number = ? ORDER BY id ASC", (chat_with,), fetchall=True) or [] if chat_with else []
    
    return render_template_string(ADMIN_HTML, 
                                  settings=s, msg=msg, orders=orders, users=users,
                                  analytics=analytics_data, 
                                  unread_chat_count=unread_chat_count,
                                  pending_complaints_count=pending_complaints_count,
                                  active_chat=chat_with, chat_history=chat_history)

@app.route("/admin/settings/save", methods=["POST"])
def save_settings():
    if not session.get("logged_in"): return redirect("/admin/login")
    for k, v in request.form.items():
        db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?", (k, v, v), commit=True)
    return redirect("/admin?msg=Updated#config")

@app.route("/admin/chat/send", methods=["POST"])
def admin_send_message():
    if not session.get("logged_in"): return redirect("/admin/login")
    phone = request.form.get("phone")
    msg = request.form.get("message")
    if phone and msg: send_whatsapp(phone, "text", msg, agent=session.get("username"))
    return redirect(f"/admin?chat_with={phone}#livechat")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
