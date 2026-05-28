import os
import sys
import json
import sqlite3
import logging
import time
import requests
from threading import Lock
from flask import Flask, request, jsonify, render_template_string, redirect, session

# =====================================================================
# SYSTEM SETUP
# =====================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dhaka-exclusive-pro-2026")
application = app

DB_FILE = "bot_v7_ultimate.db"
db_lock = Lock()

# =====================================================================
# DATABASE INITIALIZER (এটি এরর আসা বন্ধ করবে)
# =====================================================================
def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        # সব প্রয়োজনীয় টেবিল তৈরি করা হচ্ছে
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, from_number TEXT, content TEXT, direction TEXT, agent_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS users (phone TEXT PRIMARY KEY, last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, name TEXT, address TEXT, total INTEGER, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, price INTEGER, stock INTEGER, image_url TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS agent_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, action TEXT, details TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS complaints (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, complaint_text TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS agents (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)")
        
        # ডিফল্ট ডাটা প্রবেশ করানো
        c.execute("INSERT OR IGNORE INTO agents (username, password) VALUES ('admin', 'admin123')")
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('business_name', 'Dhaka Exclusive')")
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('ai_system_instruction', 'আপনি একজন অ্যাসিস্ট্যান্ট।')")
        conn.commit()
        conn.close()

init_db() # অ্যাপ রান হওয়ার সাথে সাথে ডাটাবেস চেক করবে

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
            logger.error(f"DB Error: {e}")
            return None
        finally: conn.close()

# =====================================================================
# PRO DASHBOARD HTML
# =====================================================================
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8"><title>DHAKA EXCLUSIVE PRO</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
</head>
<body class="bg-[#0f172a] text-slate-100 min-h-screen flex flex-col md:flex-row">
<aside class="w-full md:w-72 bg-[#020617] border-r border-slate-800 p-5 flex flex-col">
    <h1 class="text-xl font-black text-indigo-500 mb-10 text-center">CONTROL PANEL</h1>
    <nav class="flex flex-col gap-2">
        <button onclick="switchTab('analytics')" class="tab-btn p-3 rounded-xl bg-indigo-600 font-bold text-left"><i class="fa-solid fa-chart-line mr-2"></i>Analytics</button>
        <button onclick="switchTab('orders')" class="tab-btn p-3 rounded-xl text-slate-400 hover:bg-slate-800 text-left"><i class="fa-solid fa-cart-shopping mr-2"></i>Orders</button>
        <button onclick="switchTab('livechat')" class="tab-btn p-3 rounded-xl text-slate-400 hover:bg-slate-800 text-left flex justify-between">
            <span><i class="fa-solid fa-comment mr-2"></i>Inbox</span>
            {% if unread_chat_count > 0 %}<span class="bg-amber-500 text-black px-2 rounded-full text-[10px]">{{ unread_chat_count }}</span>{% endif %}
        </button>
        <button onclick="switchTab('config')" class="tab-btn p-3 rounded-xl text-slate-400 hover:bg-slate-800 text-left"><i class="fa-solid fa-gears mr-2"></i>Settings</button>
        <a href="/admin/logout" class="p-3 text-rose-500 mt-20"><i class="fa-solid fa-right-from-bracket mr-2"></i>Logout</a>
    </nav>
</aside>
<main class="flex-1 p-6 md:p-10 overflow-y-auto">
    {% if msg %}<div class="bg-emerald-500/10 border border-emerald-500/20 p-4 mb-5 rounded-xl text-emerald-400 font-bold">{{ msg }}</div>{% endif %}
    
    <div id="tab-analytics" class="tab-content">
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-10">
            <div class="bg-slate-900 p-6 rounded-3xl border border-slate-800"><div class="text-xs text-slate-500">TOTAL ORDERS</div><div class="text-3xl font-black">{{ analytics.total_orders }}</div></div>
            <div class="bg-slate-900 p-6 rounded-3xl border border-slate-800"><div class="text-xs text-slate-500">REVENUE</div><div class="text-3xl font-black text-emerald-400">{{ analytics.total_revenue }}৳</div></div>
            <div class="bg-slate-900 p-6 rounded-3xl border border-slate-800"><div class="text-xs text-slate-500">USERS</div><div class="text-3xl font-black">{{ users|length }}</div></div>
        </div>
    </div>

    <div id="tab-orders" class="tab-content hidden h-full">
        <h2 class="text-2xl font-black mb-5">Order Management</h2>
        <div class="bg-slate-900 rounded-2xl border border-slate-800 overflow-hidden">
            <table class="w-full text-left text-sm">
                <thead class="bg-slate-950 text-slate-500"><tr><th class="p-4">ID</th><th class="p-4">Customer</th><th class="p-4">Amount</th><th class="p-4 text-right">Memo</th></tr></thead>
                <tbody>
                    {% for o in orders %}
                    <tr class="border-b border-slate-800"><td class="p-4">#{{ o.id }}</td><td class="p-4 font-bold">{{ o.name }}<br><span class="text-xs">{{ o.phone }}</span></td><td class="p-4 text-emerald-400">{{ o.total }}৳</td><td class="p-4 text-right"><a href="/invoice/{{ o.id }}" target="_blank" class="p-2 bg-slate-800 rounded"><i class="fa-solid fa-print"></i></a></td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>

    <div id="tab-livechat" class="tab-content hidden h-[80vh] flex gap-4">
        <div class="w-64 bg-slate-900 rounded-2xl border border-slate-800 overflow-y-auto">
            {% for u in users %}
            <a href="/admin?chat_with={{ u.phone }}#livechat" class="block p-4 border-b border-slate-800 hover:bg-indigo-600/20 text-xs">{{ u.phone }}</a>
            {% endfor %}
        </div>
        <div class="flex-1 bg-slate-900 rounded-2xl border border-slate-800 flex flex-col">
            <div class="p-4 bg-slate-950 font-bold border-b border-slate-800">{{ active_chat or 'Select Contact' }}</div>
            <div class="flex-1 p-5 overflow-y-auto flex flex-col gap-3">
                {% for m in chat_history %}
                <div class="p-3 rounded-2xl text-xs max-w-xs {% if m.direction == 'inbound' %}bg-slate-800 self-start{% else %}bg-indigo-600 self-end{% endif %}">{{ m.content }}</div>
                {% endfor %}
            </div>
            {% if active_chat %}
            <form action="/admin/chat/send" method="POST" class="p-4 bg-slate-950 flex gap-2">
                <input type="hidden" name="phone" value="{{ active_chat }}">
                <input name="message" class="bg-slate-900 flex-1 p-3 rounded-xl outline-none" placeholder="Type here..." required>
                <button class="bg-indigo-600 px-6 rounded-xl font-bold">SEND</button>
            </form>
            {% endif %}
        </div>
    </div>

    <div id="tab-config" class="tab-content hidden">
        <div class="max-w-xl bg-slate-900 p-8 rounded-3xl border border-slate-800 shadow-2xl">
            <h2 class="text-xl font-black text-indigo-400 mb-6">System Configuration</h2>
            <form action="/admin/settings/save" method="POST" class="space-y-4">
                <div><label class="text-[10px] text-slate-500 uppercase">Store Name</label><input name="business_name" value="{{ settings.get('business_name', '') }}" class="w-full bg-slate-800 border-none p-3 rounded-xl mt-1"></div>
                <div><label class="text-[10px] text-slate-500 uppercase">Gemini AI Key</label><input type="password" name="gemini_key" value="{{ settings.get('gemini_key', '') }}" class="w-full bg-slate-800 border-none p-3 rounded-xl mt-1"></div>
                <button type="submit" class="w-full bg-indigo-600 py-3 rounded-xl font-black mt-10">SAVE CHANGES</button>
            </form>
        </div>
    </div>
</main>
<script>
function switchTab(t) {
    document.querySelectorAll('.tab-content').forEach(e => e.classList.add('hidden'));
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('bg-indigo-600','text-white'));
    document.getElementById('tab-'+t).classList.remove('hidden');
    event.currentTarget.classList.add('bg-indigo-600','text-white');
    window.location.hash = t;
}
const currentHash = window.location.hash.replace('#','') || 'analytics';
switchTab(currentHash);
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
        u, p = request.form.get("username"), request.form.get("password")
        agent = db_query("SELECT * FROM agents WHERE username = ? AND password = ?", (u, p), fetchone=True)
        if agent:
            session.permanent = True
            session["logged_in"], session["username"] = True, agent["username"]
            return redirect("/admin")
        return "Login Failed"
    return """<body style="background:#0f172a; color:white; display:flex; justify-content:center; align-items:center; height:100vh; font-family:sans-serif;">
        <form method="POST" style="background:#1e293b; padding:40px; border-radius:30px; text-align:center;">
            <h2 style="color:#6366f1">ADMIN LOGIN</h2>
            <input name="username" placeholder="Username" style="width:100%; padding:15px; margin:10px 0; border-radius:10px; border:none; background:#0f172a; color:white;"><br>
            <input name="password" type="password" placeholder="Password" style="width:100%; padding:15px; margin:10px 0; border-radius:10px; border:none; background:#0f172a; color:white;"><br>
            <button style="width:100%; padding:15px; background:#6366f1; color:white; border:none; border-radius:10px; font-weight:bold; cursor:pointer; margin-top:20px;">LOGIN</button>
        </form></body>"""

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")

@app.route("/admin")
def admin_portal():
    if not session.get("logged_in"): return redirect("/admin/login")
    
    settings_data = {}
    rows = db_query("SELECT key, value FROM settings", fetchall=True)
    if rows:
        settings_data = {r["key"]: r["value"] for r in rows}

    analytics = {
        "total_orders": db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)["c"] or 0,
        "total_revenue": db_query("SELECT SUM(total) as s FROM orders", fetchone=True)["s"] or 0
    }
    
    unread_chat_count = db_query("SELECT COUNT(*) as c FROM messages WHERE direction='inbound'", fetchone=True)["c"] or 0
    pending_complaints_count = db_query("SELECT COUNT(*) as c FROM complaints WHERE status='pending'", fetchone=True)["c"] or 0
    
    orders = db_query("SELECT * FROM orders ORDER BY id DESC LIMIT 50", fetchall=True) or []
    users = db_query("SELECT * FROM users ORDER BY last_active DESC", fetchall=True) or []
    chat_with = request.args.get("chat_with", "")
    chat_history = db_query("SELECT * FROM messages WHERE from_number = ? ORDER BY id ASC", (chat_with,), fetchall=True) or [] if chat_with else []
    msg = request.args.get("msg", "")

    return render_template_string(ADMIN_HTML, 
                                  settings=settings_data, msg=msg, orders=orders, users=users,
                                  analytics=analytics, unread_chat_count=unread_chat_count,
                                  pending_complaints_count=pending_complaints_count,
                                  active_chat=chat_with, chat_history=chat_history)

@app.route("/admin/settings/save", methods=["POST"])
def save_settings():
    if not session.get("logged_in"): return redirect("/admin/login")
    for k, v in request.form.items():
        db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?", (k, v, v), commit=True)
    return redirect("/admin?msg=Settings Saved!#config")

@app.route("/admin/chat/send", methods=["POST"])
def admin_send_message():
    if not session.get("logged_in"): return redirect("/admin/login")
    phone, msg = request.form.get("phone"), request.form.get("message")
    if phone and msg:
        db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', ?)",
                 (phone, msg, session.get("username")), commit=True)
    return redirect(f"/admin?chat_with={phone}#livechat")

@app.route("/invoice/<int:order_id>")
def print_invoice(order_id):
    order = db_query("SELECT * FROM orders WHERE id = ?", (order_id,), fetchone=True)
    if not order: return "Order Not Found", 404
    return f"<h1>Invoice #{order['id']}</h1><p>Customer: {order['name']}</p><p>Bill: {order['total']}৳</p><button onclick='window.print()'>Print Memo</button>"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
