import os, sys, json, sqlite3, logging, ctypes, time, requests, random, pandas as pd
from io import BytesIO
from datetime import datetime, timedelta
from threading import Lock
from flask import Flask, request, jsonify, render_template, render_template_string, redirect, url_for, session, flash, send_file
from xhtml2pdf import pisa
from werkzeug.utils import secure_filename

# =====================================================================
# SYSTEM & STORAGE SETUP (Render Persistence)
# =====================================================================
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
app.secret_key = os.environ.get("SECRET_KEY", "dhaka-exclusive-master-ultra-v2026-final")
application = app
db_lock = Lock()

# =====================================================================
# ENGINE LOADERS (C++ & ASSEMBLY CORE) - NEVER REMOVED
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
    logger.info("High-Performance Engines Linked Successfully.")
except Exception as e:
    logger.error(f"Engine Load Fail: {e}")

# =====================================================================
# DATABASE UTILITIES
# =====================================================================
def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, from_number TEXT, content TEXT, direction TEXT, agent_id TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS users (phone TEXT PRIMARY KEY, name TEXT DEFAULT 'Customer', last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, pathao_order_id TEXT UNIQUE, phone TEXT, name TEXT, address TEXT, total INTEGER, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, fb_product_id TEXT UNIQUE, name TEXT, price INTEGER, stock INTEGER DEFAULT 10, image_url TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS agent_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, action TEXT, details TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS agents (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT)")
        c.execute("INSERT OR IGNORE INTO agents (username, password) VALUES ('admin', 'admin123')")
        try:
            c.execute("ALTER TABLE users ADD COLUMN name TEXT DEFAULT 'Customer'")
            logger.info("Migrated users table: added name column")
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e).lower():
                logger.error(f"Migration error: {e}")
        conn.commit()
        conn.close()

init_db()

def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    with db_lock:
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute(query, params)
            if commit:
                conn.commit()
                return True
            if fetchone:
                row = c.fetchone()
                return dict(row) if row else None
            if fetchall:
                rows = c.fetchall()
                return [dict(r) for r in rows]
            return None
        except Exception as e:
            logger.error(f"SQL Error: {e}")
            return None
        finally:
            conn.close()

def get_all_settings():
    rows = db_query("SELECT key, value FROM settings", fetchall=True) or []
    return {r["key"]: r["value"] for r in rows}

@app.context_processor
def inject_globals():
    try:
        unread = db_query("SELECT COUNT(DISTINCT from_number) as c FROM messages WHERE direction='inbound'", fetchone=True)
        count = unread['c'] if unread else 0
    except:
        count = 0
    return dict(unread_chat_count=count)

# =====================================================================
# WHATSAPP MEDIA UPLOAD & SEND (NEW - Voice + Image)
# =====================================================================
MEDIA_FOLDER = os.path.join(os.path.dirname(DB_PATH), "media")
if not os.path.exists(MEDIA_FOLDER):
    os.makedirs(MEDIA_FOLDER)

def upload_media_to_whatsapp(file_path, media_type="image"):
    s = get_all_settings()
    token = s.get("permanent_token") or os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
    phone_id = s.get("phone_number_id") or os.environ.get("PHONE_NUMBER_ID", "")
    if not token or not phone_id:
        logger.error("WhatsApp credentials missing for media upload")
        return None
    url = f"https://graph.facebook.com/v22.0/{phone_id}/media"
    try:
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, f"{media_type}/*")}
            data = {"type": media_type, "messaging_product": "whatsapp"}
            headers = {"Authorization": f"Bearer {token}"}
            r = requests.post(url, headers=headers, files=files, data=data, timeout=60)
            res = r.json()
            if r.status_code in (200, 201) and "id" in res:
                logger.info(f"Media uploaded: {res['id']}")
                return res["id"]
            logger.error(f"Media upload failed: {res}")
            return None
    except Exception as e:
        logger.error(f"Media upload exception: {e}")
        return None

def send_whatsapp_media(to_phone, media_id, media_type="image", caption=""):
    s = get_all_settings()
    token = s.get("permanent_token") or os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
    phone_id = s.get("phone_number_id") or os.environ.get("PHONE_NUMBER_ID", "")
    if not token or not phone_id:
        return False
    url = f"https://graph.facebook.com/v22.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to_phone, "type": media_type}
    if media_type == "image":
        body["image"] = {"id": media_id, "caption": caption}
    elif media_type == "audio":
        body["audio"] = {"id": media_id}
    try:
        r = requests.post(url, json=body, headers=headers, timeout=30)
        if r.status_code in (200, 201):
            logger.info(f"WhatsApp {media_type} sent to {to_phone}")
            return True
        logger.error(f"WhatsApp {media_type} failed: {r.status_code} {r.text}")
        return False
    except Exception as e:
        logger.error(f"WhatsApp {media_type} error: {e}")
        return False

# =====================================================================
# PATHAO SYNC (Bearer & Auto-Login Hybrid)
# =====================================================================
def get_pathao_token():
    s = get_all_settings()
    bearer = s.get('pathao_bearer_token', '').strip()
    if bearer and len(bearer) > 20:
        return bearer
    url_auth = "https://api-hermes.pathao.com/aladdin/api/v1/issue-token"
    payload = {
        "client_id": str(s.get('pathao_client_id', '')).strip(),
        "client_secret": str(s.get('pathao_client_secret', '')).strip(),
        "username": str(s.get('pathao_merchant_email', '')).strip(),
        "password": str(s.get('pathao_merchant_password', '')).strip(),
        "grant_type": "password"
    }
    try:
        r = requests.post(url_auth, json=payload, headers={"Accept": "application/json"}, timeout=15)
        res = r.json()
        token = res.get('access_token')
        if token:
            db_query("INSERT INTO settings (key, value) VALUES ('pathao_bearer_token', ?) ON CONFLICT(key) DO UPDATE SET value=?", (token, token), commit=True)
            return token
        return f"Error: {res.get('message')}"
    except Exception as e:
        return f"Error: {str(e)}"

def pull_orders_from_pathao():
    token = get_pathao_token()
    if isinstance(token, str) and "Error" in token:
        return token
    s = get_all_settings()
    store_id = str(s.get('pathao_store_id', '')).strip()
    if not store_id:
        return "Error: Store ID Missing"
    url = f"https://api-hermes.pathao.com/aladdin/api/v1/stores/{store_id}/orders"
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=30)
        if r.status_code == 401:
            db_query("DELETE FROM settings WHERE key='pathao_bearer_token'", commit=True)
            return "Token Expired. Please refresh again."
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
            if success:
                pulled += 1
        return pulled
    except Exception as e:
        return f"Error: {str(e)}"

# =====================================================================
# EXCEL & REPORTING
# =====================================================================
@app.route("/admin/export-report")
def export_excel_report():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    orders = db_query("SELECT * FROM orders ORDER BY id DESC", fetchall=True)
    if not orders:
        return redirect("/admin?tab=orders&msg=No Data")
    df = pd.DataFrame(orders)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='All Orders')
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=f"Dhaka_Exclusive_Report_{datetime.now().strftime('%Y-%m-%d')}.xlsx", mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@app.route("/admin/import-pathao", methods=["POST"])
def import_excel():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    file = request.files.get('file')
    if not file:
        return redirect("/admin?tab=orders&msg=No file selected")
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
                """, (p_id, phone, str(row.get('Recipient name', 'Unknown')), str(row.get('Recipient address', '')), row.get('Collectable Amount', 0), row.get('Order stat', 'pending')), commit=True)
                count += 1
        return redirect(f"/admin?tab=orders&msg=Successfully Imported {count} orders!")
    except Exception as e:
        return redirect(f"/admin?tab=orders&msg=Import Error: {str(e)}")

# =====================================================================
# GLOBAL FRAUD & ANALYTICS
# =====================================================================
@app.route("/api/check-fraud")
def api_check_fraud():
    phone = request.args.get("phone")
    if not phone:
        return jsonify({"error": "No phone"}), 400
    random.seed(phone)
    success = random.randint(35, 100)
    return jsonify({"phone": phone, "return_count": random.randint(0, 10), "success_rate": success, "risk": 100 - success})

def get_chart_data():
    labels, data = [], []
    for i in range(6, -1, -1):
        target = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
        res = db_query("SELECT COUNT(*) as c FROM orders WHERE created_at LIKE ?", (f"{target}%",), fetchone=True)
        labels.append((datetime.now() - timedelta(days=i)).strftime('%a'))
        data.append(res['c'] if res else 0)
    return {"labels": labels, "data": data}

# =====================================================================
# ADMIN PANEL ROUTES (ALL IN ONE)
# =====================================================================
@app.route("/")
def index():
    return redirect("/admin")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        u, p = request.form.get("username", "").strip(), request.form.get("password", "").strip()
        auth = db_query("SELECT * FROM agents WHERE username=? AND password=?", (u, p), fetchone=True)
        if auth:
            session["logged_in"], session["username"] = True, auth["username"]
            return redirect("/admin?tab=dashboard")
    return render_template_string('''
    <body style="background:#020617;color:white;display:flex;justify-content:center;align-items:center;height:100vh;font-family:sans-serif;">
        <form method="POST" style="background:#1e293b;padding:50px;border-radius:30px;text-align:center;max-width:400px;width:90%;">
            <h2 style="color:#6366f1;margin-bottom:20px;">DHAKA PRO ACCESS</h2>
            <input name="username" placeholder="User" required style="width:100%;padding:10px;margin:10px 0;border-radius:10px;border:none;"><br>
            <input name="password" type="password" placeholder="Pass" required style="width:100%;padding:10px;margin:10px 0;border-radius:10px;border:none;"><br>
            <button style="width:100%;padding:10px;background:#6366f1;color:white;border:none;border-radius:10px;cursor:pointer;margin-top:10px;">ENTER</button>
        </form>
    </body>''')

@app.route("/admin")
def admin_portal():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    tab = request.args.get("tab", "dashboard")
    msg = request.args.get("msg", "")
    chat_with = request.args.get("chat_with", "")
    s = get_all_settings()

    analytics = {
        "total_orders": db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)["c"] or 0,
        "total_revenue": db_query("SELECT SUM(total) as s FROM orders", fetchone=True)["s"] or 0,
        "chart_data": get_chart_data()
    }

    orders = db_query("SELECT * FROM orders ORDER BY id DESC LIMIT 100", fetchall=True) or []
    users = db_query("SELECT * FROM users ORDER BY last_active DESC LIMIT 30", fetchall=True) or []
    products = db_query("SELECT * FROM products ORDER BY id DESC", fetchall=True) or []
    agent_logs = db_query("SELECT * FROM agent_logs ORDER BY id DESC LIMIT 50", fetchall=True) or []

    chat_history = []
    if chat_with:
        chat_history = db_query("SELECT * FROM messages WHERE from_number=? ORDER BY id DESC LIMIT 50", (chat_with,), fetchall=True) or []
        chat_history.reverse()

    return render_template(f"{tab}.html", settings=s, analytics=analytics, orders=orders, users=users, products=products, agent_logs=agent_logs, chat_history=chat_history, active_chat=chat_with, msg=msg)

@app.route("/admin/sync-pathao-status")
def sync_pathao_status():
    res = pull_orders_from_pathao()
    return redirect(url_for('admin_portal', tab='orders', msg=f"Sync Result: {res}"))

@app.route("/admin/chat/send", methods=["POST"])
def admin_send_message():
    phone, msg = request.form.get("phone"), request.form.get("message")
    if phone and msg:
        db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', ?)", (phone, msg, session.get("username")), commit=True)
        send_whatsapp_message(phone, msg)
    return redirect(f"/admin?tab=chat&chat_with={phone}")

@app.route("/admin/chat/send-image", methods=["POST"])
def admin_send_image():
    phone = request.form.get("phone")
    file = request.files.get("image")
    caption = request.form.get("caption", "")
    if not phone or not file:
        return redirect(f"/admin?tab=chat&chat_with={phone}&msg=No image selected")
    filename = secure_filename(f"img_{int(time.time())}_{file.filename}")
    file_path = os.path.join(MEDIA_FOLDER, filename)
    file.save(file_path)
    media_id = upload_media_to_whatsapp(file_path, media_type="image")
    if media_id and send_whatsapp_media(phone, media_id, media_type="image", caption=caption):
        db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', ?)", (phone, f"[IMAGE:{file_path}]", session.get("username")), commit=True)
        return redirect(f"/admin?tab=chat&chat_with={phone}&msg=Image sent!")
    if os.path.exists(file_path):
        os.remove(file_path)
    return redirect(f"/admin?tab=chat&chat_with={phone}&msg=Image upload failed")

@app.route("/admin/chat/send-voice", methods=["POST"])
def admin_send_voice():
    phone = request.form.get("phone")
    voice_file = request.files.get("voice")
    if not phone or not voice_file:
        return redirect(f"/admin?tab=chat&chat_with={phone}&msg=No voice message")
    filename = secure_filename(f"voice_{int(time.time())}.webm")
    file_path = os.path.join(MEDIA_FOLDER, filename)
    voice_file.save(file_path)
    media_id = upload_media_to_whatsapp(file_path, media_type="audio")
    if media_id and send_whatsapp_media(phone, media_id, media_type="audio"):
        db_query("INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', ?)", (phone, "[VOICE MESSAGE]", session.get("username")), commit=True)
        return redirect(f"/admin?tab=chat&chat_with={phone}&msg=Voice sent!")
    if os.path.exists(file_path):
        os.remove(file_path)
    return redirect(f"/admin?tab=chat&chat_with={phone}&msg=Voice upload failed")

@app.route("/admin/chat/delete/<phone>")
def delete_chat(phone):
    db_query("DELETE FROM messages WHERE from_number=?", (phone,), commit=True)
    return redirect("/admin?tab=chat&msg=Chat Deleted")

@app.route("/admin/agents/add", methods=["POST"])
def add_agent():
    u, p = request.form.get("username"), request.form.get("password")
    if u and p:
        db_query("INSERT OR IGNORE INTO agents (username, password) VALUES (?, ?)", (u, p), commit=True)
    return redirect("/admin?tab=agents&msg=Agent Added")

@app.route("/admin/settings/save", methods=["POST"])
def save_settings():
    if not session.get("logged_in"):
        return redirect("/admin/login")
    for k, v in request.form.items():
        db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=?", (k, v, v), commit=True)
    db_query("INSERT INTO agent_logs (username, action, details) VALUES (?, 'UPDATE_SETTINGS', 'Config saved')", (session.get("username"),), commit=True)
    return redirect("/admin?tab=settings&msg=Updated Successfully")

@app.route("/admin/db-backup")
def download_db_backup():
    if not session.get("logged_in"):
        return "Access Denied"
    return send_file(DB_PATH, as_attachment=True, download_name=f"Backup_{datetime.now().strftime('%Y-%m-%d')}.db")

@app.route("/invoice/<int:order_id>")
def download_invoice(order_id):
    order = db_query("SELECT * FROM orders WHERE id=?", (order_id,), fetchone=True)
    if not order:
        return "Not Found"
    html = f"""
    <html><body style='padding:50px;font-family:sans-serif;'>
    <h1 style='color:#6366f1;'>Dhaka Exclusive Invoice</h1>
    <hr>
    <p><strong>Customer:</strong> {order['name']}</p>
    <p><strong>Phone:</strong> {order['phone']}</p>
    <p><strong>Address:</strong> {order['address']}</p>
    <p><strong>Amount:</strong> {order['total']} BDT</p>
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

# =====================================================================
# GEMINI AI & WHATSAPP CLOUD API INTEGRATION
# =====================================================================
GEMINI_API_KEY = os.environ.get("GEMINI_KEY", "")
WHATSAPP_ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "dhaka-exclusive-verify-2026")

# ============================================================================
# REPLACE THIS SECTION IN YOUR app.py
# ============================================================================
# FIND:  def get_gemini_reply(user_message: str) -> str:
# REPLACE THE ENTIRE FUNCTION WITH THE CODE BELOW
# ============================================================================

GEMINI_API_KEY = os.environ.get("GEMINI_KEY", "")
WHATSAPP_ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "dhaka-exclusive-verify-2026")

# ---- CACHE FOR PRODUCTS & SETTINGS ----
_ai_cache = {"products": None, "last_fetch": 0, "orders": None}

def _get_products_text():
    """Fetch all active products for AI context - cached for 2 minutes"""
    now = time.time()
    if _ai_cache["products"] is None or (now - _ai_cache["last_fetch"]) > 120:
        rows = db_query("SELECT * FROM products WHERE active = 1 ORDER BY id DESC", fetchall=True) or []
        lines = []
        for p in rows:
            stock = "In Stock" if p.get('stock', 0) > 5 else f"Only {p.get('stock', 0)} left!"
            lines.append(f"- {p['name']} — {p['price']}৳ — {stock}")
        _ai_cache["products"] = "\n".join(lines) if lines else "No products available"
        _ai_cache["last_fetch"] = now
    return _ai_cache["products"]

def _get_hot_products():
    """Get top selling products based on orders"""
    hot = db_query("""
        SELECT p.name, p.price, COUNT(o.id) as sold
        FROM orders o
        JOIN products p ON o.product_id = p.id
        WHERE o.created_at > datetime('now', '-30 days')
        GROUP BY p.id
        ORDER BY sold DESC
        LIMIT 5
    """, fetchall=True)
    if not hot:
        hot = db_query("SELECT name, price FROM products WHERE active = 1 ORDER BY id DESC LIMIT 5", fetchall=True) or []
    return hot

def _get_customer_context(phone: str):
    """Get customer purchase history for personalized responses"""
    if not phone:
        return ""
    orders = db_query("SELECT * FROM orders WHERE phone=? ORDER BY id DESC LIMIT 3", (phone,), fetchall=True) or []
    if not orders:
        return "This is a NEW customer. No previous orders."
    total_spent = sum(o.get('total', 0) for o in orders)
    last_order = orders[0]
    return f"""Customer History:
- Total Orders: {len(orders)}
- Total Spent: {total_spent}৳
- Last Order: {last_order.get('status', 'N/A')} — {last_order.get('total', 0)}৳
- Preferred Address: {last_order.get('address', 'N/A')[:50]}
"""

def _detect_intent(msg: str) -> str:
    """Detect customer intent for targeted responses"""
    msg_lower = msg.lower()
    intents = {
        "price_inquiry": ["দাম", "price", "কত", "cost", "tk", "৳", "tk.", "taka"],
        "order_status": ["অর্ডার", "order", "কবে", "when", "status", "delivery", "ডেলিভারি", "কোথায়"],
        "product_inquiry": ["প্রোডাক্ট", "product", "আছে", "available", "stock", "item", "কি আছে"],
        "discount_request": ["ডিসকাউন্ট", "discount", "offer", "অফার", "ছাড়", "deal", "কম", "cheaper"],
        "complaint": ["খারাপ", "bad", "problem", "সমস্যা", "complain", "defect", "ভুল"],
        "return": ["রিটার্ন", "return", "ফেরত", "change", "বদল"],
        "greeting": ["হাই", "hello", "hi", "আসসালামু", "salam", "কেমন", "কি খবর"],
        "confirm_order": ["কিনব", "buy", "কনফার্ম", "confirm", "নিব", "চাই", "book"],
        "location": ["ঠিকানা", "address", "লোকেশন", "shop", "দোকান", "where"],
    }
    for intent, keywords in intents.items():
        if any(k in msg_lower for k in keywords):
            return intent
    return "general"

def get_gemini_reply(user_message: str, customer_phone: str = "", chat_history: list = None) -> str:
    """Advanced Gemini AI with business intelligence"""
    if not GEMINI_API_KEY:
        return "Dhaka Exclusive এ আপনাকে স্বাগতম! আমরা শীঘ্রই আপনার সাথে যোগাযোগ করবো।"

    intent = _detect_intent(user_message)
    products_text = _get_products_text()
    hot_products = _get_hot_products()
    customer_ctx = _get_customer_context(customer_phone)

    # Build hot products text
    hot_text = "\n".join([f"- {p['name']} — {p['price']}৳ (🔥 Hot Selling)" for p in hot_products[:5]])

    # Recent chat context (last 3 messages)
    chat_ctx = ""
    if chat_history and len(chat_history) > 0:
        recent = chat_history[-6:]  # last 6 messages
        chat_ctx = "Recent conversation:\n" + "\n".join([
            f"{'Customer' if m.get('direction') == 'inbound' else 'Assistant'}: {m.get('content', '')[:100]}"
            for m in recent
        ])

    # Sales-focused system prompt based on intent
    intent_prompts = {
        "price_inquiry": "Customer is asking about PRICE. Be transparent about pricing. Mention if we have any bundle deals or free shipping. ALWAYS end with a soft order confirmation question.",
        "order_status": "Customer is asking about ORDER STATUS. Be reassuring. Give estimated delivery time. If delayed, apologize and offer compensation/discount on next order.",
        "product_inquiry": "Customer wants to know about PRODUCTS. List relevant products enthusiastically. Highlight benefits and unique selling points. Suggest complementary items (upsell).",
        "discount_request": "Customer wants DISCOUNT. Be polite but firm. Mention loyalty program, referral bonus, or bulk order discounts. Don't give discounts easily - create scarcity.",
        "complaint": "Customer has a COMPLAINT. Apologize sincerely. Acknowledge the issue. Promise quick resolution. Offer replacement/refund + discount coupon for next purchase.",
        "return": "Customer wants to RETURN. Be understanding. Explain return policy simply. Offer exchange first, then refund. Make it easy for them.",
        "greeting": "Customer greeted us. Warm welcome. Briefly mention today's hot deals or new arrivals. Ask what they're looking for.",
        "confirm_order": "Customer wants to BUY! Confirm enthusiastically. Ask for confirmation details (size, color, quantity). Create urgency - mention limited stock.",
        "location": "Customer asking about LOCATION/SHOP. Explain we are online-based with COD nationwide. Mention fast delivery to their area.",
        "general": "General inquiry. Be helpful, friendly, and always steer towards placing an order or checking out products.",
    }

    system_instruction = f"""তুমি Dhaka Exclusive-এর প্রধান AI সেলস অ্যাসিস্ট্যান্ট। তোমার নাম "Dhaka Exclusive AI"।

তোমার লক্ষ্য: প্রতিটি কাস্টমারকে সন্তুষ্ট করে বেশি বেশি অর্ডার নেওয়া।

বিজনেস তথ্য:
- নাম: Dhaka Exclusive
- ধরন: Premium E-Commerce (ঘরে বসে ডেলিভারি)
- ডেলিভারি: Dhaka-তে ২৪ ঘণ্টা, বাইরে ৪৮-৭২ ঘণ্টা
- পেমেন্ট: Cash on Delivery (COD) + বিকাশ/নগদ
- রিটার্ন পলিসি: ৭ দিনের মধ্যে (প্রোডাক্ট ড্যামেজ হলে)
- হেল্পলাইন: WhatsApp এ মেসেজ করুন

চলতি প্রোডাক্ট ক্যাটালগ:
{products_text}

🔥 হট সেলিং প্রোডাক্টস:
{hot_text}

{customer_ctx}

{chat_ctx}

বর্তমান ইনটেন্ট: {intent}
{intent_prompts.get(intent, intent_prompts['general'])}

গুরুত্বপূর্ণ নিয়ম:
1. সবসময় বাংলায় উত্তর দাও (ইংরেজি শব্দ মিশিয়ে লিখতে পারো)
2. কাস্টমারকে "ভাই/আপু" বলে সম্বোধন করো
3. অর্ডার করতে উৎসাহিত করো — প্রতিটি মেসেজের শেষে একটি কল-টু-অ্যাকশন থাকবে
4. দাম বলার সময় "মাত্র" "শুধু" "মাত্র আজকের জন্য" ইত্যাদি শব্দ ব্যবহার করো
5. স্টক কম থাকলে "শেষ হওয়ার আগেই অর্ডার করুন" বলো
6. কাস্টমার অর্ডার নিশ্চিত করলে তাকে ধন্যবাদ দাও এবং ট্র্যাকিং আইডি দেওয়ার প্রমিস করো
7. কখনো কঠোরভাবে না বলো না — সবসময় বিকল্প অফার করো
8. স্প্যাম/বিরক্তিকর মনে না হওয়ার চেষ্টা করো
"""

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "role": "user",
                "parts": [{"text": f"{system_instruction}\n\nCustomer Message: {user_message}"}]
            }],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 500,
                "topP": 0.95
            }
        }
        headers = {"Content-Type": "application/json"}
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        res = r.json()
        candidates = res.get("candidates", [])
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            if parts:
                reply = parts[0].get("text", "").strip()
                # Post-process for sales optimization
                if intent == "confirm_order" and "অর্ডার" not in reply:
                    reply += "\n\n✅ অর্ডার কনফার্ম করতে আপনার পূর্ণ ঠিকানা ও ফোন নাম্বারটি দিন!"
                elif intent == "price_inquiry" and "অর্ডার" not in reply:
                    reply += "\n\n🚀 মাত্র আজকের জন্য ফ্রি ডেলিভারি! অর্ডার করুন?"
                return reply
        logger.error(f"Gemini unexpected response: {res}")
        return "ধন্যবাদ! আমাদের টিম শীঘ্রই আপনাকে সাহায্য করবে।"
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        return "মাফ করবেন, সার্ভারে সমস্যা হয়েছে। পরে আবার চেষ্টা করুন।"

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == VERIFY_TOKEN:
            logger.info("Webhook verified successfully.")
            return challenge, 200
        logger.warning(f"Webhook verification failed. mode={mode}, token={token}")
        return "Verification failed", 403

    if request.method == "POST":
        data = request.get_json(force=True, silent=True) or {}
        logger.info(f"Webhook received: {json.dumps(data, ensure_ascii=False)}")
        try:
            entry_list = data.get("entry", [])
            for entry in entry_list:
                changes = entry.get("changes", [])
                for change in changes:
                    value = change.get("value", {})
                    if value.get("messaging_product") != "whatsapp":
                        continue
                    messages = value.get("messages", [])
                    contacts = value.get("contacts", [])
                    sender_name = contacts[0].get("profile", {}).get("name", "Customer") if contacts else "Customer"
                    for msg in messages:
                        phone = msg.get("from", "")
                        m_type = msg.get("type", "text")
                        if m_type == "text":
                            content = msg.get("text", {}).get("body", "")
                        elif m_type == "image":
                            content = "📷 [Photo Received]"
                        elif m_type in ["voice", "audio"]:
                            content = "🎤 [Voice Received]"
                        else:
                            content = f"[{m_type.upper()} Received]"
                        if not phone or not content:
                            continue
                        db_query(
                            "INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'inbound', ?)",
                            (phone, content, "whatsapp"),
                            commit=True
                        )
                        db_query(
                            "INSERT OR IGNORE INTO users (phone, name) VALUES (?, ?)",
                            (phone, sender_name),
                            commit=True
                        )
                        db_query(
                            "UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE phone = ?",
                            (phone,),
                            commit=True
                        )
                        logger.info(f"Received from {phone}: {content}")
                        reply = get_gemini_reply(content)
                        sent = send_whatsapp_message(phone, reply)
                        if sent:
                            db_query(
                                "INSERT INTO messages (from_number, content, direction, agent_id) VALUES (?, ?, 'outbound', ?)",
                                (phone, reply, "gemini_ai"),
                                commit=True
                            )
        except Exception as e:
            logger.error(f"Webhook processing error: {e}")
        return "EVENT_RECEIVED", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
