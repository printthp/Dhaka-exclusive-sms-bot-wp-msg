import os
import sys
import json
import sqlite3
import logging
import ctypes
import time
import requests
from threading import Thread, Lock
from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session, flash

# =====================================================================
# SYSTEM SETUP
# =====================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dhaka-exclusive-pro-2026")
application = app

# =====================================================================
# DATABASE UTILITIES
# =====================================================================
DB_FILE = "bot_v7_ultimate.db"
db_lock = Lock()

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
        finally: conn.close()

def get_all_settings():
    rows = db_query("SELECT key, value FROM settings", fetchall=True) or []
    return {r["key"]: r["value"] for r in rows}

# =====================================================================
# ULTIMATE PRO DASHBOARD HTML (ADVANCED FEATURES)
# =====================================================================
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Control Station PRO</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body class="bg-[#0f172a] text-slate-100 min-h-screen font-sans flex flex-col md:flex-row">

<!-- SIDEBAR -->
<aside class="w-full md:w-72 bg-[#020617] border-r border-slate-800 flex flex-col shrink-0">
    <div class="p-6 border-b border-slate-800 text-center">
        <h1 class="text-2xl font-black text-indigo-500 tracking-tighter italic">DHAKA EXCLUSIVE</h1>
        <div class="text-[10px] text-slate-500 mt-1 uppercase tracking-widest font-bold">Advanced Management System</div>
    </div>
    
    <nav class="p-4 flex flex-col gap-2 flex-1">
        <button onclick="switchTab('analytics')" class="tab-btn flex items-center gap-3 px-4 py-3 rounded-xl text-sm bg-indigo-600 text-white font-bold transition-all"><i class="fa-solid fa-chart-line"></i> Analytics</button>
        <button onclick="switchTab('orders')" class="tab-btn flex items-center gap-3 px-4 py-3 rounded-xl text-sm text-slate-400 hover:bg-slate-800 transition-all"><i class="fa-solid fa-cart-shopping"></i> Orders</button>
        <button onclick="switchTab('livechat')" class="tab-btn flex items-center justify-between px-4 py-3 rounded-xl text-sm text-slate-400 hover:bg-slate-800 transition-all">
            <span><i class="fa-solid fa-comment-dots mr-3"></i>Live Inbox</span>
            {% if unread_chat_count > 0 %}<span class="bg-amber-500 text-black text-[10px] px-2 rounded-full font-bold">{{ unread_chat_count }}</span>{% endif %}
        </button>
        <button onclick="switchTab('inventory')" class="tab-btn flex items-center gap-3 px-4 py-3 rounded-xl text-sm text-slate-400 hover:bg-slate-800 transition-all"><i class="fa-solid fa-boxes-stacked"></i> Inventory</button>
        <button onclick="switchTab('complaints')" class="tab-btn flex items-center justify-between px-4 py-3 rounded-xl text-sm text-slate-400 hover:bg-slate-800 transition-all">
            <span><i class="fa-solid fa-shield-virus mr-3"></i>Complaints</span>
            {% if pending_complaints_count > 0 %}<span class="bg-rose-500 text-white text-[10px] px-2 rounded-full font-bold">{{ pending_complaints_count }}</span>{% endif %}
        </button>
        <button onclick="switchTab('config')" class="tab-btn flex items-center gap-3 px-4 py-3 rounded-xl text-sm text-slate-400 hover:bg-slate-800 transition-all"><i class="fa-solid fa-gears"></i> AI & Settings</button>
        
        <div class="mt-auto p-4 bg-slate-900/50 rounded-2xl border border-slate-800">
            <div class="text-xs text-slate-500 mb-1">প্রতিনিধি: {{ session.get('username') }}</div>
            <a href="/admin/logout" class="text-xs text-rose-400 font-bold hover:underline">লগআউট করুন</a>
        </div>
    </nav>
</aside>

<!-- MAIN CONTENT -->
<main class="flex-1 p-6 md:p-10 overflow-y-auto">
    
    <!-- ANALYTICS TAB -->
    <div id="tab-analytics" class="tab-content space-y-8">
        <h2 class="text-2xl font-black text-white">সাফল্যের ড্যাশবোর্ড</h2>
        <div class="grid grid-cols-1 md:grid-cols-4 gap-6">
            <div class="bg-slate-900 p-6 rounded-3xl border border-slate-800 shadow-xl">
                <div class="text-indigo-400 text-xs font-bold uppercase mb-2">সর্বমোট অর্ডার</div>
                <div class="text-4xl font-black">{{ analytics.total_orders }}</div>
            </div>
            <div class="bg-slate-900 p-6 rounded-3xl border border-slate-800 shadow-xl">
                <div class="text-emerald-400 text-xs font-bold uppercase mb-2">মোট রেভিনিউ</div>
                <div class="text-4xl font-black">{{ analytics.total_revenue }}৳</div>
            </div>
            <div class="bg-slate-900 p-6 rounded-3xl border border-slate-800 shadow-xl">
                <div class="text-amber-400 text-xs font-bold uppercase mb-2">সক্রিয় ইউজার</div>
                <div class="text-4xl font-black">{{ users|length }}</div>
            </div>
            <div class="bg-slate-900 p-6 rounded-3xl border border-slate-800 shadow-xl">
                <div class="text-rose-400 text-xs font-bold uppercase mb-2">অভিযোগ</div>
                <div class="text-4xl font-black">{{ pending_complaints_count }}</div>
            </div>
        </div>
        
        <div class="grid grid-cols-1 md:grid-cols-2 gap-8">
            <div class="bg-slate-900 p-6 rounded-3xl border border-slate-800">
                <h3 class="text-sm font-bold text-slate-400 mb-4">অর্ডার গ্রাফ (Weekly Trend)</h3>
                <canvas id="orderChart"></canvas>
            </div>
            <div class="bg-slate-900 p-6 rounded-3xl border border-slate-800">
                <h3 class="text-sm font-bold text-slate-400 mb-4">সাম্প্রতিক কার্যক্রম</h3>
                <div class="space-y-4">
                    {% for log in agent_logs[:5] %}
                    <div class="flex items-center gap-3 text-xs border-b border-slate-800 pb-2">
                        <span class="text-indigo-400 font-bold">{{ log.username }}</span>
                        <span class="text-slate-500">{{ log.action }}</span>
                        <span class="text-[10px] ml-auto text-slate-600">{{ log.timestamp }}</span>
                    </div>
                    {% endfor %}
                </div>
            </div>
        </div>
    </div>

    <!-- ORDERS TAB -->
    <div id="tab-orders" class="tab-content hidden space-y-6">
        <div class="flex justify-between items-center">
            <h2 class="text-2xl font-black">অর্ডার ট্র্যাকিং সিস্টেম</h2>
            <button class="bg-indigo-600 px-4 py-2 rounded-xl text-xs font-bold uppercase tracking-widest">সকল অর্ডার প্রিন্ট করুন</button>
        </div>
        <div class="bg-slate-900 rounded-3xl border border-slate-800 overflow-hidden shadow-2xl">
            <table class="w-full text-left text-sm">
                <thead>
                    <tr class="bg-slate-950 border-b border-slate-800 text-slate-500 uppercase text-[10px] font-black">
                        <th class="p-5">ID</th><th class="p-5">কাস্টমার</th><th class="p-5">ঠিকানা</th><th class="p-5">বিল</th><th class="p-5">স্ট্যাটাস</th><th class="p-5">মেমো</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-slate-800">
                    {% for o in orders %}
                    <tr class="hover:bg-slate-800/30 transition-all">
                        <td class="p-5 font-mono text-indigo-400 font-bold">#{{ o.id }}</td>
                        <td class="p-5">
                            <div class="font-black text-white">{{ o.name }}</div>
                            <div class="text-[10px] text-slate-500">{{ o.phone }}</div>
                        </td>
                        <td class="p-5 text-xs text-slate-400 max-w-xs">{{ o.address }}</td>
                        <td class="p-5 font-black text-emerald-400">{{ o.total }}৳</td>
                        <td class="p-5">
                            <span class="px-2 py-1 rounded-lg text-[10px] font-bold bg-indigo-500/20 text-indigo-400 uppercase">{{ o.status }}</span>
                        </td>
                        <td class="p-5">
                            <a href="/invoice/{{ o.id }}" target="_blank" class="p-2 bg-slate-800 rounded-xl hover:bg-slate-700 text-white"><i class="fa-solid fa-file-invoice"></i></a>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>

    <!-- LIVE CHAT TAB -->
    <div id="tab-livechat" class="tab-content hidden h-[80vh] flex gap-6">
        <div class="w-72 bg-slate-900 rounded-3xl border border-slate-800 overflow-y-auto">
            <div class="p-5 border-b border-slate-800 text-xs font-black uppercase text-slate-500">ইনবক্স</div>
            {% for u in users %}
            <a href="/admin?chat_with={{ u.phone }}#livechat" class="block p-4 border-b border-slate-800 hover:bg-slate-800 transition-all">
                <div class="text-xs font-bold text-white">{{ u.phone }}</div>
                <div class="text-[10px] text-slate-500">{{ u.last_active }}</div>
            </a>
            {% endfor %}
        </div>
        <div class="flex-1 bg-slate-900 rounded-3xl border border-slate-800 flex flex-col overflow-hidden">
            <div class="p-5 bg-slate-950 border-b border-slate-800 flex justify-between items-center">
                <div class="font-black text-indigo-400">{{ active_chat or 'মেসেঞ্জার সিলেক্ট করুন' }}</div>
                {% if active_chat %}<span class="text-[10px] bg-emerald-500/10 text-emerald-400 px-2 py-1 rounded-full">বট সক্রিয়</span>{% endif %}
            </div>
            <div class="flex-1 p-6 overflow-y-auto flex flex-col gap-4">
                {% for m in chat_history %}
                <div class="p-4 rounded-2xl max-w-md text-sm {% if m.direction == 'inbound' %}bg-slate-800 self-start text-slate-200{% else %}bg-indigo-600 self-end text-white shadow-lg shadow-indigo-500/20{% endif %}">
                    {{ m.content }}
                </div>
                {% endfor %}
            </div>
            {% if active_chat %}
            <form action="/admin/chat/send" method="POST" class="p-4 bg-slate-950 border-t border-slate-800 flex gap-3">
                <input type="hidden" name="phone" value="{{ active_chat }}">
                <input name="message" class="flex-1 bg-slate-900 border border-slate-800 p-3 rounded-2xl text-sm outline-none focus:border-indigo-500" placeholder="জবাব লিখুন..." required>
                <button class="bg-indigo-600 px-6 rounded-2xl font-bold hover:bg-indigo-500 active:scale-95 transition-all">পাঠান</button>
            </form>
            {% endif %}
        </div>
    </div>

    <!-- INVENTORY TAB -->
    <div id="tab-inventory" class="tab-content hidden space-y-6">
        <div class="flex justify-between items-center">
            <h2 class="text-2xl font-black">প্রোডাক্ট ইনভেন্টরি</h2>
            <a href="/admin/sync-facebook-trigger" class="bg-emerald-600 px-6 py-2 rounded-xl text-xs font-bold transition-all hover:bg-emerald-500">Facebook Sync</a>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            {% for p in products %}
            <div class="bg-slate-900 rounded-3xl border border-slate-800 overflow-hidden group">
                <img src="{{ p.image_url }}" class="w-full h-48 object-cover group-hover:scale-105 transition-all">
                <div class="p-5">
                    <div class="text-lg font-black text-white">{{ p.name }}</div>
                    <div class="text-indigo-400 font-bold">{{ p.price }}৳</div>
                    <div class="text-xs text-slate-500 mt-2">স্টক: {{ p.stock }} পিস</div>
                    <button class="w-full mt-4 bg-slate-800 py-2 rounded-xl text-xs font-bold hover:bg-slate-700">এডিট প্রোডাক্ট</button>
                </div>
            </div>
            {% endfor %}
        </div>
    </div>

    <!-- CONFIG TAB -->
    <div id="tab-config" class="tab-content hidden">
        <div class="max-w-2xl bg-slate-900 p-8 rounded-3xl border border-slate-800">
            <h2 class="text-xl font-black text-indigo-400 mb-6">গ্লোবাল এআই ও সিস্টেম সেটিংস</h2>
            <form action="/admin/settings/save" method="POST" class="space-y-6">
                <div>
                    <label class="text-xs font-bold text-slate-500 uppercase tracking-tighter">Business Name</label>
                    <input name="business_name" value="{{ settings.get('business_name', '') }}" class="w-full bg-slate-950 border border-slate-800 p-3 rounded-xl mt-2 outline-none focus:border-indigo-500">
                </div>
                <div>
                    <label class="text-xs font-bold text-slate-500 uppercase tracking-tighter">Gemini Pro API Key</label>
                    <input type="password" name="gemini_key" value="{{ settings.get('gemini_key', '') }}" class="w-full bg-slate-950 border border-slate-800 p-3 rounded-xl mt-2 outline-none focus:border-indigo-500">
                </div>
                <div>
                    <label class="text-xs font-bold text-slate-500 uppercase tracking-tighter">AI সিস্টেম ইনস্ট্রাকশন</label>
                    <textarea name="ai_system_instruction" rows="4" class="w-full bg-slate-950 border border-slate-800 p-3 rounded-xl mt-2 outline-none focus:border-indigo-500">{{ settings.get('ai_system_instruction', '') }}</textarea>
                </div>
                <button type="submit" class="w-full bg-indigo-600 py-3 rounded-2xl font-black shadow-lg shadow-indigo-500/20">কনফিগারেশন আপডেট করুন</button>
            </form>
        </div>
    </div>

</main>

<script>
function switchTab(t) {
    document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
    document.getElementById('tab-'+t).classList.remove('hidden');
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('bg-indigo-600','text-white','font-bold'));
    event.currentTarget.classList.add('bg-indigo-600','text-white','font-bold');
    window.location.hash = t;
}
const currentHash = window.location.hash.replace('#','') || 'analytics';
switchTab(currentHash);

// Chart Logic
const ctx = document.getElementById('orderChart').getContext('2d');
new Chart(ctx, {
    type: 'line',
    data: {
        labels: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'],
        datasets: [{
            label: 'Orders',
            data: [12, 19, 3, 5, 2, 3, 9],
            borderColor: '#6366f1',
            tension: 0.4,
            fill: true,
            backgroundColor: 'rgba(99, 102, 241, 0.1)'
        }]
    },
    options: {
        plugins: { legend: { display: false } },
        scales: { y: { display: false }, x: { grid: { display: false } } }
    }
});
</script>
</body>
</html>
"""

# =====================================================================
# ROUTES & LOGIC
# =====================================================================

@app.route("/")
def index():
    return redirect("/admin")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        u, p = request.form.get("username", "").strip(), request.form.get("password", "").strip()
        agent = db_query("SELECT * FROM agents WHERE username = ? AND password = ?", (u, p), fetchone=True)
        if agent:
            session.permanent = True
            session["logged_in"], session["username"] = True, agent["username"]
            return redirect("/admin")
        return "Login Failed"
    return render_template_string("""
        <body style="background:#0f172a; color:white; font-family:sans-serif; display:flex; justify-content:center; align-items:center; height:100vh;">
            <form method="POST" style="background:#1e293b; padding:40px; border-radius:20px; width:300px; text-align:center;">
                <h2 style="color:#6366f1">ADMIN LOGIN</h2>
                <input name="username" placeholder="Username" style="width:100%; padding:12px; margin:10px 0; border-radius:10px; border:none; outline:none; background:#0f172a; color:white;">
                <input name="password" type="password" placeholder="Password" style="width:100%; padding:12px; margin:10px 0; border-radius:10px; border:none; outline:none; background:#0f172a; color:white;">
                <button style="width:100%; padding:12px; background:#6366f1; color:white; border:none; border-radius:10px; font-weight:bold; cursor:pointer;">LOGIN</button>
            </form>
        </body>
    """)

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect("/admin/login")

@app.route("/admin")
def admin_portal():
    if not session.get("logged_in"): return redirect("/admin/login")
    
    s = get_all_settings()
    msg, chat_with = request.args.get("msg", ""), request.args.get("chat_with", "")

    # Analytics Data
    analytics = {
        "total_orders": db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)["c"],
        "total_revenue": db_query("SELECT SUM(total) as s FROM orders", fetchone=True)["s"] or 0
    }
    
    unread_chat_count = db_query("SELECT COUNT(*) as c FROM messages WHERE direction='inbound'", fetchone=True)["c"]
    pending_complaints_count = db_query("SELECT COUNT(*) as c FROM complaints WHERE status='pending'", fetchone=True)["c"]
    
    orders = db_query("SELECT * FROM orders ORDER BY id DESC LIMIT 50", fetchall=True) or []
    users = db_query("SELECT * FROM users ORDER BY last_active DESC", fetchall=True) or []
    products = db_query("SELECT * FROM products ORDER BY id DESC LIMIT 20", fetchall=True) or []
    agent_logs = db_query("SELECT * FROM agent_logs ORDER BY id DESC LIMIT 10", fetchall=True) or []
    chat_history = db_query("SELECT * FROM messages WHERE from_number = ? ORDER BY id ASC", (chat_with,), fetchall=True) or [] if chat_with else []
    
    return render_template_string(ADMIN_HTML, 
                                  settings=s, msg=msg, orders=orders, users=users, products=products,
                                  analytics=analytics, agent_logs=agent_logs,
                                  unread_chat_count=unread_chat_count,
                                  pending_complaints_count=pending_complaints_count,
                                  active_chat=chat_with, chat_history=chat_history)

@app.route("/admin/settings/save", methods=["POST"])
def save_settings():
    if not session.get("logged_in"): return redirect("/admin/login")
    for k, v in request.form.items():
        db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?", (k, v, v), commit=True)
    db_query("INSERT INTO agent_logs (username, action, details) VALUES (?, 'UPDATE_SETTINGS', 'Changed Global Settings')", (session.get("username"),), commit=True)
    return redirect("/admin?msg=System configurations updated!#config")

@app.route("/admin/chat/send", methods=["POST"])
def admin_send_message():
    if not session.get("logged_in"): return redirect("/admin/login")
    phone, msg = request.form.get("phone"), request.form.get("message")
    if phone and msg:
        # এখানে send_whatsapp ফাংশনটি কল করার কথা, আপাতত লগ দেখাচ্ছি
        db_query("INSERT INTO messages (msg_id, from_number, content, direction, agent_id) VALUES (?, ?, ?, 'outbound', ?)",
                 (f"out_{int(time.time())}", phone, msg, session.get("username")), commit=True)
    return redirect(f"/admin?chat_with={phone}#livechat")

@app.route("/invoice/<int:order_id>")
def print_invoice(order_id):
    order = db_query("SELECT * FROM orders WHERE id = ?", (order_id,), fetchone=True)
    if not order: return "Not Found", 404
    return f"<h1>Invoice #{order['id']}</h1><p>Customer: {order['name']}</p><p>Total: {order['total']} BDT</p><button onclick='window.print()'>Print</button>"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
