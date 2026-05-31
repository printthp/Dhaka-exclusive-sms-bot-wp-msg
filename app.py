import os
import sys
import json
import sqlite3
import logging
import time
import requests
import base64
import re
from io import BytesIO
from datetime import datetime, timedelta
from threading import Lock
from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session, flash

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)

if os.path.exists("/opt/render/project/src/data"):
    DB_PATH = "/opt/render/project/src/data/bot_v7_ultimate.db"
else:
    local_data_dir = os.path.join(os.getcwd(), "data")
    if not os.path.exists(local_data_dir):
        os.makedirs(local_data_dir)
    DB_PATH = os.path.join(local_data_dir, "bot_v7_ultimate.db")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dhaka-exclusive-2026")
application = app
db_lock = Lock()

GEMINI_API_KEY = os.environ.get("GEMINI_KEY", "")

# =====================================================================
# DATABASE
# =====================================================================
def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, from_number TEXT, content TEXT, direction TEXT, agent_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS users (phone TEXT PRIMARY KEY, name TEXT DEFAULT 'Customer', last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, name TEXT, address TEXT, product_name TEXT, quantity INTEGER DEFAULT 1, price INTEGER, total INTEGER, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("""CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, price INTEGER, stock INTEGER DEFAULT 10,
            image_url TEXT, description TEXT, category TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS group_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, customer_name TEXT, address TEXT,
            product_name TEXT, quantity INTEGER DEFAULT 1, price INTEGER, total INTEGER,
            status TEXT DEFAULT 'pending', group_name TEXT, raw_message TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS team_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT UNIQUE, name TEXT,
            role TEXT DEFAULT 'moderator', wa_id TEXT, is_active INTEGER DEFAULT 1
        )""")
        c.execute("CREATE TABLE IF NOT EXISTS agents (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)")
        c.execute("INSERT OR IGNORE INTO agents (username, password) VALUES ('admin', 'admin123')")
        conn.commit()
        conn.close()

init_db()

def db_query(query, params=(), fetchall=False, commit=False):
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        try:
            c.execute(query, params)
            if commit:
                conn.commit()
            if fetchall:
                return c.fetchall()
            return c.fetchone()
        finally:
            conn.close()

def get_all_settings():
    rows = db_query("SELECT key, value FROM settings", fetchall=True) or []
    return {r["key"]: r["value"] for r in rows}

# =====================================================================
# GEMINI AI
# =====================================================================
def get_gemini_reply(prompt_text, image_data=None):
    if not GEMINI_API_KEY:
        return "AI service unavailable"
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt_text}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 800}
    }
    if image_data:
        payload["contents"][0]["parts"].append({"inline_data": {"mime_type": "image/jpeg", "data": image_data}})
    try:
        r = requests.post(url, json=payload, timeout=30)
        res = r.json()
        if res.get("candidates"):
            return res["candidates"][0]["content"]["parts"][0].get("text", "")
        return "Sorry"
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        return "AI error"

# =====================================================================
# WEBHOOK FROM BRIDGE (PC sends messages here)
# =====================================================================
@app.route("/group-webhook", methods=["POST"])
def group_webhook():
    """Messages from WhatsApp groups (Team + Orders)"""
    data = request.get_json(force=True, silent=True) or {}
    s = get_all_settings()
    team_group = s.get("team_group", "")
    orders_group = s.get("orders_group", "")

    group_name = data.get("group_name", "")
    group_type = data.get("group_type", "other")
    sender_name = data.get("sender_name", "Member")
    sender_id = data.get("sender_id", "")
    body = data.get("message", "")
    media_data = data.get("media_data", "")
    media_type = data.get("media_type", "")

    if not body or not group_name:
        return jsonify({"reply": ""})

    logger.info(f"[GROUP:{group_type}] {sender_name}: {body[:50]}")

    products = db_query("SELECT name, price, stock FROM products ORDER BY id DESC LIMIT 50", fetchall=True) or []
    product_list = "\n".join([f"- {p['name']}: {p['price']}৳" for p in products[:20]])

    if group_type == "team":
        # AI answers team member questions
        prompt = f"""তুমি Dhaka Exclusive-এর AI সহকারী। টিম মেম্বার "{sender_name}"-এর প্রশ্নের উত্তর দাও।

প্রশ্ন: "{body}"

আমাদের প্রোডাক্ট:
{product_list}

সংক্ষিপ্ত, সহায়ক উত্তর দাও (বাংলায়)।"""

        reply = get_gemini_reply(prompt, media_data if media_data else None)
        return jsonify({"reply": reply})

    elif group_type == "orders":
        # AI extracts order info
        prompt = f"""তুমি Dhaka Exclusive-এর অর্ডার এক্সট্রাক্টর। মেসেজ থেকে অর্ডার তথ্য বের করো।

মেসেজ: "{body}"

Output format:
NAME: <নাম বা Unknown>
PHONE: <ফোন বা খালি>
ADDRESS: <ঠিকানা বা খালি>
PRODUCT: <প্রোডাক্ট নাম>
QUANTITY: <সংখ্যা বা 1>
PRICE: <দাম সংখ্যায় বা 0>

যদি অর্ডার না হয়: NOT_AN_ORDER"""

        ai_result = get_gemini_reply(prompt)

        if "NOT_AN_ORDER" in ai_result:
            return jsonify({"reply": ""})

        name, phone, address, product, qty, price = "", "", "", "", 1, 0
        for line in ai_result.split("\n"):
            if line.startswith("NAME:"): name = line.replace("NAME:", "").strip()
            if line.startswith("PHONE:"): phone = line.replace("PHONE:", "").strip()
            if line.startswith("ADDRESS:"): address = line.replace("ADDRESS:", "").strip()
            if line.startswith("PRODUCT:"): product = line.replace("PRODUCT:", "").strip()
            if line.startswith("QUANTITY:"):
                try: qty = int(line.replace("QUANTITY:", "").strip())
                except: pass
            if line.startswith("PRICE:"):
                try: price = int(line.replace("PRICE:", "").strip())
                except: pass

        if not product:
            return jsonify({"reply": ""})

        total = price * qty
        db_query("""INSERT INTO group_orders (phone, customer_name, address, product_name, quantity, price, total, status, group_name, raw_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (phone, name, address, product, qty, price, total, group_name, body), commit=True)

        logger.info(f"[ORDER] Saved: {product} x{qty} = {total}৳")
        return jsonify({"reply": f"✅ অর্ডার গ্রহণ হয়েছে!\n📦 {product} x{qty}\n💰 {total}৳"})

    return jsonify({"reply": ""})


@app.route("/business-webhook", methods=["POST"])
def business_webhook():
    """Individual customer messages (AI auto-reply)"""
    data = request.get_json(force=True, silent=True) or {}
    body = data.get("message", "")
    customer_phone = data.get("customer_phone", "")
    customer_name = data.get("customer_name", "Customer")
    media_data = data.get("media_data", "")

    if not body:
        return jsonify({"reply": ""})

    logger.info(f"[CUSTOMER] {customer_name}: {body[:50]}")

    # Save to DB
    db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'inbound', 'customer')", (customer_phone, body), commit=True)
    db_query("INSERT OR IGNORE INTO users (phone, name) VALUES (?, ?)", (customer_phone, customer_name), commit=True)

    # Get products
    products = db_query("SELECT name, price, stock, image_url FROM products ORDER BY id DESC LIMIT 50", fetchall=True) or []
    product_list = "\n".join([f"- {p['name']}: {p['price']}৳" for p in products[:20]])

    prompt = f"""তুমি Dhaka Exclusive-এর AI সেলস সহকারী। একজন কাস্টমারের মেসেজের উত্তর দাও।

কাস্টমার: {customer_name}
মেসেজ: "{body}"

আমাদের প্রোডাক্ট:
{product_list}

নিয়ম:
১. বাংলায়, বন্ধুসুলভ ভাষায় উত্তর দাও
২. প্রোডাক্টের দাম, সাইজ, কালার বলো
৩. অর্ডার করতে বললে ঠিকানা ও ফোন নম্বর চাও
৪. সংক্ষিপ্ত রাখো (৩-৪ লাইন)

উত্তর:"""

    reply = get_gemini_reply(prompt, media_data if media_data else None)
    return jsonify({"reply": reply})

# =====================================================================
# API: Bridge Config
# =====================================================================
@app.route("/api/bridge-config", methods=["GET"])
def api_bridge_config():
    s = get_all_settings()
    return jsonify({
        "configs": [{
            "id": 1,
            "label": "Business WhatsApp",
            "team_group": s.get("team_group", ""),
            "orders_group": s.get("orders_group", ""),
            "moderator_group": "",  # Not used
            "enabled": True
        }]
    })

# =====================================================================
# ADMIN PANEL
# =====================================================================
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        u = request.form.get("username", "")
        p = request.form.get("password", "")
        agent = db_query("SELECT * FROM agents WHERE username=? AND password=?", (u, p))
        if agent:
            session["admin"] = True
            return redirect("/admin-panel")
        return "Wrong password", 401
    return render_template_string("""<form method="POST"><input name="username" placeholder="User"><input name="password" type="password" placeholder="Pass"><button>Login</button></form>""")

@app.route("/admin-panel")
def admin_panel():
    if not session.get("admin"):
        return redirect("/admin/login")
    total_orders = db_query("SELECT COUNT(*) as c FROM group_orders", fetchall=True)
    total_orders = total_orders[0]["c"] if total_orders else 0
    total_products = db_query("SELECT COUNT(*) as c FROM products", fetchall=True)
    total_products = total_products[0]["c"] if total_products else 0
    return render_template_string("""
<!DOCTYPE html><html><head><meta charset="utf-8"><title>Admin</title>
<style>body{font-family:Arial;margin:40px;background:#f5f5f5}.container{max-width:1000px;margin:0 auto;background:white;padding:30px;border-radius:10px}
.quick-links{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:15px;margin-top:20px}
.quick-link{display:flex;align-items:center;padding:20px;background:linear-gradient(135deg,#667eea,#764ba2);color:white;text-decoration:none;border-radius:10px}
.quick-link-icon{font-size:28px;margin-right:15px}
.stat{font-size:24px;font-weight:bold}
</style></head><body><div class="container">
<h1>Admin Panel</h1>
<div class="quick-links">
    <a href="/admin/whatsapp-settings" class="quick-link"><div class="quick-link-icon">📱</div><div><div><b>WhatsApp Settings</b></div><small>Group names</small></div></a>
    <a href="/admin/group-orders" class="quick-link" style="background:linear-gradient(135deg,#f093fb,#f5576c)"><div class="quick-link-icon">📦</div><div><div class="stat">{{ total_orders }}</div><div><b>Group Orders</b></div></div></a>
    <a href="/admin/team" class="quick-link" style="background:linear-gradient(135deg,#4facfe,#00f2fe)"><div class="quick-link-icon">👥</div><div><div><b>Team</b></div><small>Members</small></div></a>
    <a href="/admin/products" class="quick-link" style="background:linear-gradient(135deg,#11998e,#38ef7d)"><div class="quick-link-icon">📋</div><div><div class="stat">{{ total_products }}</div><div><b>Products</b></div></div></a>
</div>
</div></body></html>
""", total_orders=total_orders, total_products=total_products)

@app.route("/admin/whatsapp-settings", methods=["GET"])
def admin_whatsapp_settings():
    if not session.get("admin"):
        return redirect("/admin/login")
    s = get_all_settings()
    return render_template_string("""
<!DOCTYPE html><html><head><meta charset="utf-8"><title>WhatsApp Settings</title>
<style>body{font-family:Arial;margin:40px;background:#f5f5f5}.container{max-width:700px;margin:0 auto;background:white;padding:30px;border-radius:10px}
input{width:100%;padding:12px;margin:8px 0;border:1px solid #ddd;border-radius:5px;box-sizing:border-box}
.btn{background:#25D366;color:white;padding:12px 24px;border:none;border-radius:5px;cursor:pointer;font-size:16px}
label{font-weight:bold;margin-top:15px;display:block}
.info{background:#e3f2fd;padding:15px;border-radius:5px;margin:15px 0;color:#1565c0}
</style></head><body><div class="container">
<a href="/admin-panel">← Admin</a>
<h1>📱 WhatsApp Group Settings</h1>
<div class="info">
<b>টিম গ্রুপ:</b> AI টিম মেম্বারদের প্রশ্নের উত্তর দেবে<br>
<b>অর্ডার গ্রুপ:</b> মেসেজ থেকে অর্ডার অটো শনাক্ত হয়ে এখানে আসবে
</div>
<form method="POST" action="/admin/whatsapp-settings/save">
    <label>Team Group Name (e.g. Team Of Dhaka Exclusive)</label>
    <input type="text" name="team_group" value="{{ s.get('team_group','') }}" placeholder="Exact group name">
    <label>Orders Group Name (e.g. Orders)</label>
    <input type="text" name="orders_group" value="{{ s.get('orders_group','') }}" placeholder="Exact group name">
    <button type="submit" class="btn">💾 Save</button>
</form>
<h3 style="margin-top:30px">🖥️ PC Bridge Setup</h3>
<ol style="line-height:2;color:#666">
    <li>PC তে <code>bridge-manager.js</code> রাখুন</li>
    <li><code>npm install</code></li>
    <li><code>set FLASK_URL=https://your-app.onrender.com</code></li>
    <li><code>node bridge-manager.js</code> → QR Scan করুন</li>
</ol>
</div></body></html>
""", s=s)

@app.route("/admin/whatsapp-settings/save", methods=["POST"])
def save_whatsapp_settings():
    if not session.get("admin"):
        return redirect("/admin/login")
    for k in ["team_group", "orders_group"]:
        v = request.form.get(k, "").strip()
        db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?", (k, v, v), commit=True)
    flash("Saved!")
    return redirect("/admin/whatsapp-settings")

@app.route("/admin/group-orders")
def admin_group_orders():
    if not session.get("admin"):
        return redirect("/admin/login")
    orders = db_query("SELECT * FROM group_orders ORDER BY id DESC LIMIT 200", fetchall=True) or []
    return render_template_string("""
<!DOCTYPE html><html><head><meta charset="utf-8"><title>Orders</title>
<style>body{font-family:Arial;margin:40px;background:#f5f5f5}.container{max-width:1100px;margin:0 auto;background:white;padding:30px;border-radius:10px}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{padding:10px;border-bottom:1px solid #ddd;text-align:left}th{background:#FF9800;color:white}
.btn{padding:6px 12px;border:none;border-radius:4px;cursor:pointer;font-size:12px;color:white;text-decoration:none}
.btn-green{background:#4CAF50}.btn-red{background:#f44336}
</style></head><body><div class="container">
<a href="/admin-panel">← Admin</a>
<h1>📦 Group Orders</h1>
<table><tr><th>ID</th><th>Name</th><th>Phone</th><th>Product</th><th>Qty</th><th>Total</th><th>Status</th><th>Action</th></tr>
{% for o in orders %}
<tr><td>{{ o.id }}</td><td>{{ o.customer_name or '-' }}</td><td>{{ o.phone or '-' }}</td><td>{{ o.product_name }}</td>
<td>{{ o.quantity }}</td><td>{{ o.total }}৳</td><td>{{ o.status }}</td>
<td><a href="/admin/group-orders/status/{{ o.id }}?status=confirmed" class="btn btn-green">Confirm</a>
<a href="/admin/group-orders/status/{{ o.id }}?status=cancelled" class="btn btn-red">Cancel</a></td></tr>
{% endfor %}
</table></div></body></html>
""", orders=orders)

@app.route("/admin/group-orders/status/<int:order_id>")
def update_order_status(order_id):
    if not session.get("admin"):
        return redirect("/admin/login")
    status = request.args.get("status", "pending")
    db_query("UPDATE group_orders SET status = ? WHERE id = ?", (status, order_id), commit=True)
    return redirect("/admin/group-orders")

@app.route("/admin/team")
def admin_team():
    if not session.get("admin"):
        return redirect("/admin/login")
    members = db_query("SELECT * FROM team_members ORDER BY id DESC", fetchall=True) or []
    return render_template_string("""
<!DOCTYPE html><html><head><meta charset="utf-8"><title>Team</title>
<style>body{font-family:Arial;margin:40px;background:#f5f5f5}.container{max-width:900px;margin:0 auto;background:white;padding:30px;border-radius:10px}
table{width:100%;border-collapse:collapse;margin-top:20px}th,td{padding:12px;border-bottom:1px solid #ddd;text-align:left}th{background:#4CAF50;color:white}
.btn{padding:8px 16px;border:none;border-radius:5px;cursor:pointer;color:white;text-decoration:none}
.btn-green{background:#4CAF50}.btn-red{background:#f44336}
input{padding:10px;margin:5px;border:1px solid #ddd;border-radius:5px}
</style></head><body><div class="container">
<a href="/admin-panel">← Admin</a>
<h1>👥 Team Members</h1>
<form method="POST" action="/admin/team/add">
    <input type="text" name="name" placeholder="Name" required>
    <input type="text" name="phone" placeholder="Phone" required>
    <input type="text" name="wa_id" placeholder="WhatsApp ID">
    <button type="submit" class="btn btn-green">Add</button>
</form>
<table><tr><th>ID</th><th>Name</th><th>Phone</th><th>Role</th><th>Action</th></tr>
{% for m in members %}
<tr><td>{{ m.id }}</td><td>{{ m.name }}</td><td>{{ m.phone }}</td><td>{{ m.role }}</td>
<td><a href="/admin/team/delete/{{ m.id }}" class="btn btn-red" onclick="return confirm('Delete?')">Delete</a></td></tr>
{% endfor %}
</table></div></body></html>
""", members=members)

@app.route("/admin/team/add", methods=["POST"])
def admin_team_add():
    if not session.get("admin"):
        return redirect("/admin/login")
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    wa_id = request.form.get("wa_id", "").strip()
    db_query("INSERT OR REPLACE INTO team_members (name, phone, wa_id, role, is_active) VALUES (?, ?, ?, 'moderator', 1)", (name, phone, wa_id), commit=True)
    return redirect("/admin/team")

@app.route("/admin/team/delete/<int:member_id>")
def admin_team_delete(member_id):
    if not session.get("admin"):
        return redirect("/admin/login")
    db_query("DELETE FROM team_members WHERE id = ?", (member_id,), commit=True)
    return redirect("/admin/team")

@app.route("/admin/products")
def admin_products():
    if not session.get("admin"):
        return redirect("/admin/login")
    products = db_query("SELECT * FROM products ORDER BY id DESC LIMIT 200", fetchall=True) or []
    return render_template_string("""
<!DOCTYPE html><html><head><meta charset="utf-8"><title>Products</title>
<style>body{font-family:Arial;margin:40px;background:#f5f5f5}.container{max-width:900px;margin:0 auto;background:white;padding:30px;border-radius:10px}
table{width:100%;border-collapse:collapse;margin-top:20px}th,td{padding:12px;border-bottom:1px solid #ddd;text-align:left}th{background:#11998e;color:white}
.btn{padding:8px 16px;border:none;border-radius:5px;cursor:pointer;color:white;text-decoration:none}
.btn-green{background:#4CAF50}.btn-red{background:#f44336}
input{padding:10px;margin:5px;border:1px solid #ddd;border-radius:5px;width:150px}
</style></head><body><div class="container">
<a href="/admin-panel">← Admin</a>
<h1>📋 Products</h1>
<form method="POST" action="/admin/products/add">
    <input type="text" name="name" placeholder="Product Name" required>
    <input type="number" name="price" placeholder="Price" required>
    <input type="number" name="stock" placeholder="Stock" value="10">
    <button type="submit" class="btn btn-green">Add</button>
</form>
<table><tr><th>ID</th><th>Name</th><th>Price</th><th>Stock</th><th>Action</th></tr>
{% for p in products %}
<tr><td>{{ p.id }}</td><td>{{ p.name }}</td><td>{{ p.price }}৳</td><td>{{ p.stock }}</td>
<td><a href="/admin/products/delete/{{ p.id }}" class="btn btn-red" onclick="return confirm('Delete?')">Delete</a></td></tr>
{% endfor %}
</table></div></body></html>
""", products=products)

@app.route("/admin/products/add", methods=["POST"])
def admin_products_add():
    if not session.get("admin"):
        return redirect("/admin/login")
    name = request.form.get("name", "").strip()
    price = int(request.form.get("price", 0))
    stock = int(request.form.get("stock", 10))
    db_query("INSERT INTO products (name, price, stock) VALUES (?, ?, ?)", (name, price, stock), commit=True)
    return redirect("/admin/products")

@app.route("/admin/products/delete/<int:product_id>")
def admin_products_delete(product_id):
    if not session.get("admin"):
        return redirect("/admin/login")
    db_query("DELETE FROM products WHERE id = ?", (product_id,), commit=True)
    return redirect("/admin/products")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
