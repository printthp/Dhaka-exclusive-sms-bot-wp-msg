"""
Dynamic Admin Panel for Dhaka Exclusive WhatsApp Bot
Use: from admin_dynamic import init_admin_routes
"""

import os
import sqlite3
import json
from datetime import datetime
from flask import request, render_template_string, jsonify
from functools import wraps

DB_FILE = "bot_v3.db"

ADMIN_PANEL_USER = os.environ.get("ADMIN_PANEL_USER", "admin")
ADMIN_PANEL_PASS = os.environ.get("ADMIN_PANEL_PASS", "admin123")

def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    try:
        c.execute(query, params)
        if commit:
            conn.commit(); conn.close(); return True
        if fetchone:
            row = c.fetchone(); conn.close(); return dict(row) if row else None
        if fetchall:
            rows = c.fetchall(); conn.close(); return [dict(r) for r in rows]
        conn.close(); return None
    except Exception as e:
        print(f"DB Error: {e}")
        conn.close()
        raise

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or auth.username != ADMIN_PANEL_USER or auth.password != ADMIN_PANEL_PASS:
            return ('<<h3>অননুমোদিত</h3>', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})
        return f(*args, **kwargs)
    return decorated

def init_settings_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    defaults = [
        ("business_name", os.environ.get("BUSINESS_NAME", "Dhaka Exclusive")),
        ("logo_url", ""),
        ("primary_color", "#667eea"),
        ("header_color", "#1f2937"),
        ("sidebar_color", "#374151"),
        ("accent_color", "#10b981"),
    ]
    for k, v in defaults:
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()

init_settings_db()

def get_setting(key, default=""):
    row = db_query("SELECT value FROM settings WHERE key = ?", (key,), fetchone=True)
    return row["value"] if row else default

def set_setting(key, value):
    db_query("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
             (key, value), commit=True)

def get_all_settings():
    rows = db_query("SELECT * FROM settings", fetchall=True) or []
    return {r["key"]: r["value"] for r in rows}

def make_admin_html(settings):
    primary = settings.get("primary_color", "#667eea")
    header = settings.get("header_color", "#1f2937")
    accent = settings.get("accent_color", "#10b981")
    logo = settings.get("logo_url", "")
    name = settings.get("business_name", "Dhaka Exclusive")
    
    return """
<!DOCTYPE html>
<html lang="bn">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Admin Panel | """ + name + """</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: 'Segoe UI', sans-serif; background: #f3f4f6; color: #1f2937; }
.header { background: """ + header + """; color: white; padding: 16px 24px; display: flex; justify-content: space-between; align-items: center; position: fixed; top: 0; left: 0; right: 0; z-index: 100; height: 60px; }
.header .logo-area { display: flex; align-items: center; gap: 12px; }
.header .logo-area img { height: 36px; border-radius: 6px; background: white; padding: 2px; }
.header h1 { font-size: 20px; }
.nav { display: flex; gap: 8px; }
.nav-btn { padding: 8px 16px; background: rgba(255,255,255,0.15); color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 13px; }
.nav-btn:hover { background: rgba(255,255,255,0.25); }
.nav-btn.active { background: """ + primary + """; }
.container { margin-top: 60px; padding: 24px; max-width: 1400px; margin-left: auto; margin-right: auto; }

.stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }
.stat-card { background: white; padding: 20px; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.stat-card h3 { font-size: 13px; color: #6b7280; text-transform: uppercase; margin-bottom: 8px; }
.stat-card .value { font-size: 28px; font-weight: 700; color: #1f2937; }
.stat-card.orders { border-left: 4px solid """ + primary + """; }
.stat-card.revenue { border-left: 4px solid """ + accent + """; }
.stat-card.users { border-left: 4px solid #f59e0b; }
.stat-card.pending { border-left: 4px solid #ef4444; }

.section { display: none; }
.section.active { display: block; }

.card { background: white; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); overflow: hidden; margin-bottom: 20px; }
.card-header { padding: 16px 20px; border-bottom: 1px solid #e5e7eb; display: flex; justify-content: space-between; align-items: center; }
.card-header h2 { font-size: 18px; }
.btn { padding: 10px 20px; border: none; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 600; }
.btn-primary { background: """ + primary + """; color: white; }
.btn-success { background: #10b981; color: white; }
.btn-danger { background: #ef4444; color: white; }
.btn-sm { padding: 6px 12px; font-size: 12px; }

.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th { background: #f9fafb; padding: 12px 16px; text-align: left; font-size: 13px; font-weight: 600; color: #6b7280; text-transform: uppercase; }
td { padding: 12px 16px; border-top: 1px solid #e5e7eb; font-size: 14px; }
tr:hover { background: #f9fafb; }
.status-badge { padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; }
.status-pending { background: #fef3c7; color: #92400e; }
.status-created { background: #dbeafe; color: #1e40af; }
.status-delivered { background: #d1fae5; color: #065f46; }
.status-cancelled { background: #fee2e2; color: #991b1b; }

.form-group { display: flex; flex-direction: column; margin-bottom: 14px; }
.form-group label { font-size: 13px; font-weight: 600; color: #6b7280; margin-bottom: 6px; }
.form-group input, .form-group select, .form-group textarea {
    padding: 10px 14px; border: 1px solid #d1d5db; border-radius: 8px; font-size: 14px; font-family: inherit;
}
.form-group input:focus, .form-group select:focus, .form-group textarea:focus {
    outline: none; border-color: """ + primary + """; box-shadow: 0 0 0 3px rgba(102,126,234,0.1);
}
.color-picker-wrapper { display: flex; align-items: center; gap: 10px; }
.color-preview { width: 40px; height: 40px; border-radius: 8px; border: 2px solid #e5e7eb; }

.modal-overlay { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 200; align-items: center; justify-content: center; }
.modal-overlay.active { display: flex; }
.modal { background: white; border-radius: 16px; width: 90%; max-width: 600px; max-height: 90vh; overflow-y: auto; }
.modal-header { padding: 20px; border-bottom: 1px solid #e5e7eb; display: flex; justify-content: space-between; align-items: center; }
.modal-header h3 { font-size: 18px; }
.modal-close { background: none; border: none; font-size: 24px; cursor: pointer; color: #6b7280; }
.modal-body { padding: 20px; }

.search-box { padding: 10px 14px; border: 1px solid #d1d5db; border-radius: 8px; font-size: 14px; width: 250px; }
.settings-preview { padding: 20px; background: #f9fafb; border-radius: 12px; margin-bottom: 20px; text-align: center; }
.settings-preview .demo-header { padding: 16px; border-radius: 8px; margin-bottom: 12px; color: white; font-weight: 600; }
.settings-preview .demo-btn { padding: 10px 20px; border-radius: 8px; color: white; display: inline-block; font-weight: 600; }

@media (max-width: 768px) {
    .stats-grid { grid-template-columns: repeat(2, 1fr); }
    .nav { flex-wrap: wrap; }
    .container { padding: 12px; }
    .header h1 { font-size: 16px; }
}
</style>
</head>
<body>

<div class="header">
    <div class="logo-area">
        """ + (('<img src="' + logo + '" alt="logo">' if logo else '')) + """
        <h1>🔧 Admin Panel | """ + name + """</h1>
    </div>
    <div class="nav">
        <button class="nav-btn active" onclick="showSection('dashboard')">📊 Dashboard</button>
        <button class="nav-btn" onclick="showSection('products')">📦 Products</button>
        <button class="nav-btn" onclick="showSection('orders')">🛒 Orders</button>
        <button class="nav-btn" onclick="showSection('users')">👤 Users</button>
        <button class="nav-btn" onclick="showSection('tools')">🛠️ Tools</button>
        <button class="nav-btn" onclick="showSection('settings')">⚙️ Settings</button>
    </div>
</div>

<div class="container">

<div class="section active" id="dashboard">
    <div class="stats-grid">
        <div class="stat-card orders"><h3>মোট অর্ডার</h3><div class="value">{{ stats.total_orders }}</div></div>
        <div class="stat-card revenue"><h3>মোট রেভেনিউ</h3><div class="value">৳{{ stats.revenue }}</div></div>
        <div class="stat-card users"><h3>মোট ইউজার</h3><div class="value">{{ stats.users }}</div></div>
        <div class="stat-card pending"><h3>পেন্ডিং</h3><div class="value">{{ stats.pending }}</div></div>
    </div>
    <div class="card">
        <div class="card-header"><h2>📈 সর্বশেষ ৫টি অর্ডার</h2></div>
        <div class="table-wrap">
            <table>
                <tr><th>ID</th><th>কাস্টমার</th><th>ফোন</th><th>টোটাল</th><th>স্ট্যাটাস</th><th>তারিখ</th></tr>
                {% for o in recent_orders %}
                <tr>
                    <td>#{{ o.id }}</td>
                    <td>{{ o.name or 'Unknown' }}</td>
                    <td>{{ o.phone }}</td>
                    <td>৳{{ o.total }}</td>
                    <td><span class="status-badge status-{{ o.status }}">{{ o.status }}</span></td>
                    <td>{{ o.created_at[:16] if o.created_at else '' }}</td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </div>
</div>

<div class="section" id="products">
    <div class="card">
        <div class="card-header">
            <h2>📦 প্রোডাক্ট ম্যানেজমেন্ট</h2>
            <div style="display:flex;gap:8px;">
                <button class="btn btn-success" onclick="openModal('importModal')">📥 Bulk CSV</button>
                <button class="btn btn-primary" onclick="openModal('productModal')">➕ নতুন প্রোডাক্ট</button>
            </div>
        </div>
        <div class="table-wrap">
            <table>
                <tr><th>ID</th><th>নাম</th><th>দাম</th><th>স্টক</th><th>অ্যাকশন</th></tr>
                {% for p in products %}
                <tr>
                    <td>#{{ p.id }}</td>
                    <td>{{ p.name }}</td>
                    <td>৳{{ p.price }}</td>
                    <td>{{ p.stock }}</td>
                    <td>
                        <button class="btn btn-sm btn-success" onclick="editProduct({{ p.id }}, '{{ p.name|replace("'", "\\'") }}', {{ p.price }}, {{ p.stock }}, '{{ p.description|replace("'", "\\'") }}')">✏️</button>
                        <button class="btn btn-sm btn-danger" onclick="deleteProduct({{ p.id }})">🗑️</button>
                    </td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </div>
</div>

<div class="section" id="orders">
    <div class="card">
        <div class="card-header">
            <h2>🛒 অর্ডার ম্যানেজমেন্ট</h2>
            <input type="text" class="search-box" id="orderSearch" placeholder="ফোন/নামে সার্চ..." onkeyup="searchOrders()">
        </div>
        <div class="table-wrap">
            <table>
                <tr><th>ID</th><th>কাস্টমার</th><th>ফোন</th><th>ঠিকানা</th><th>টোটাল</th><th>স্ট্যাটাস</th><th>অ্যাকশন</th></tr>
                {% for o in orders %}
                <tr data-phone="{{ o.phone }}" data-name="{{ o.name or '' }}">
                    <td>#{{ o.id }}</td>
                    <td>{{ o.name or 'N/A' }}</td>
                    <td>{{ o.phone }}</td>
                    <td>{{ o.address or 'N/A' }}</td>
                    <td>৳{{ o.total }}</td>
                    <td>
                        <select onchange="updateOrderStatus({{ o.id }}, this.value)" style="padding:4px 8px;border-radius:6px;border:1px solid #d1d5db;">
                            <option value="pending" {{ 'selected' if o.status=='pending' else '' }}>Pending</option>
                            <option value="created" {{ 'selected' if o.status=='created' else '' }}>Created</option>
                            <option value="confirmed" {{ 'selected' if o.status=='confirmed' else '' }}>Confirmed</option>
                            <option value="shipped" {{ 'selected' if o.status=='shipped' else '' }}>Shipped</option>
                            <option value="delivered" {{ 'selected' if o.status=='delivered' else '' }}>Delivered</option>
                            <option value="cancelled" {{ 'selected' if o.status=='cancelled' else '' }}>Cancelled</option>
                        </select>
                    </td>
                    <td><button class="btn btn-sm btn-danger" onclick="deleteOrder({{ o.id }})">🗑️</button></td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </div>
</div>

<div class="section" id="users">
    <div class="card">
        <div class="card-header"><h2>👤 কাস্টমার লিস্ট</h2></div>
        <div class="table-wrap">
            <table>
                <tr><th>ফোন</th><th>নাম</th><th>মোট অর্ডার</th><th>মোট খরচ</th></tr>
                {% for u in users %}
                <tr>
                    <td>{{ u.phone }}</td>
                    <td>{{ u.name or 'N/A' }}</td>
                    <td>{{ u.total_orders }}</td>
                    <td>৳{{ u.total_spent }}</td>
                </tr>
                {% endfor %}
            </table>
        </div>
    </div>
</div>

<div class="section" id="tools">
    <div class="card">
        <div class="card-header"><h2>📢 ব্রডকাস্ট মেসেজ</h2></div>
        <div style="padding:20px;">
            <div class="form-group" style="margin-bottom:12px;">
                <label>সব কাস্টমারকে মেসেজ পাঠান:</label>
                <textarea id="broadcastText" rows="4" placeholder="মেসেজ লিখুন..."></textarea>
            </div>
            <button class="btn btn-primary" onclick="sendBroadcast()">📤 পাঠান</button>
            <div id="broadcastResult" style="margin-top:12px;font-size:14px;"></div>
        </div>
    </div>
</div>

<div class="section" id="settings">
    <div class="card">
        <div class="card-header"><h2>⚙️ Appearance & Branding</h2></div>
        <div style="padding:20px;max-width:600px;">
            <div class="settings-preview">
                <div class="demo-header" id="previewHeader" style="background:{{ settings.header_color }}">Header Preview</div>
                <div class="demo-btn" id="previewBtn" style="background:{{ settings.primary_color }}">Button Preview</div>
            </div>
            
            <div class="form-group">
                <label>বিজনেস নাম</label>
                <input type="text" id="settingName" value="{{ settings.business_name }}">
            </div>
            <div class="form-group">
                <label>লোগো URL (ছবির লিংক)</label>
                <input type="text" id="settingLogo" value="{{ settings.logo_url }}" placeholder="https://...">
                <small style="color:#6b7280">লোগো ছবির লিংক দিন (ঐচ্ছিক)</small>
            </div>
            <div class="form-group">
                <label>প্রাইমারি কালার (বাটন, লিংক)</label>
                <div class="color-picker-wrapper">
                    <input type="color" id="settingPrimary" value="{{ settings.primary_color }}" onchange="updatePreview()">
                    <input type="text" id="settingPrimaryText" value="{{ settings.primary_color }}" style="width:120px;" onchange="document.getElementById('settingPrimary').value=this.value;updatePreview()">
                </div>
            </div>
            <div class="form-group">
                <label>হেডার কালার (Top bar)</label>
                <div class="color-picker-wrapper">
                    <input type="color" id="settingHeader" value="{{ settings.header_color }}" onchange="updatePreview()">
                    <input type="text" id="settingHeaderText" value="{{ settings.header_color }}" style="width:120px;" onchange="document.getElementById('settingHeader').value=this.value;updatePreview()">
                </div>
            </div>
            <div class="form-group">
                <label>অ্যাকসেন্ট কালার (Success, Revenue)</label>
                <div class="color-picker-wrapper">
                    <input type="color" id="settingAccent" value="{{ settings.accent_color }}" onchange="updatePreview()">
                    <input type="text" id="settingAccentText" value="{{ settings.accent_color }}" style="width:120px;" onchange="document.getElementById('settingAccent').value=this.value;updatePreview()">
                </div>
            </div>
            <button class="btn btn-primary" onclick="saveSettings()">💾 সেভ করুন</button>
            <div id="settingsResult" style="margin-top:12px;"></div>
        </div>
    </div>
</div>

</div>

<!-- CSV Import Modal -->
<div class="modal-overlay" id="importModal">
    <div class="modal">
        <div class="modal-header">
            <h3>📥 Bulk CSV Import</h3>
            <button class="modal-close" onclick="closeModal('importModal')">&times;</button>
        </div>
        <div class="modal-body">
            <div class="form-group">
                <label>CSV Data (নাম, দাম, স্টক, বর্ণনা)</label>
                <textarea id="csvInput" rows="10" placeholder="প্রতি লাইনে একটা প্রোডাক্ট:
পেস্টেল কুর্তি, 1299, 15, সুন্দর পেস্টেল কালার
ব্ল্যাক শার্ট, 999, 20, ক্লাসিক ব্ল্যাক
..."></textarea>
            </div>
            <button class="btn btn-success" onclick="importCSV()">📥 ইমপোর্ট করুন</button>
            <div id="importResult" style="margin-top:12px;font-size:14px;"></div>
        </div>
    </div>
</div>

<!-- Product Modal -->
<div class="modal-overlay" id="productModal">
    <div class="modal">
        <div class="modal-header">
            <h3>📦 প্রোডাক্ট যোগ/এডিট</h3>
            <button class="modal-close" onclick="closeModal('productModal')">&times;</button>
        </div>
        <div class="modal-body">
            <input type="hidden" id="productId">
            <div class="form-group"><label>প্রোডাক্ট নাম</label><input type="text" id="productName" placeholder="যেমন: পেস্টেল কুর্তি"></div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
                <div class="form-group"><label>দাম (৳)</label><input type="number" id="productPrice" placeholder="1299"></div>
                <div class="form-group"><label>স্টক</label><input type="number" id="productStock" placeholder="10"></div>
            </div>
            <div class="form-group"><label>বর্ণনা</label><textarea id="productDesc" rows="3" placeholder="প্রোডাক্টের বর্ণনা..."></textarea></div>
            <button class="btn btn-primary" onclick="saveProduct()">💾 সেভ করুন</button>
        </div>
    </div>
</div>

<script>
function showSection(id) {
    document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    document.getElementById(id).classList.add('active');
    event.target.classList.add('active');
}
function openModal(id) { document.getElementById(id).classList.add('active'); }
function closeModal(id) { document.getElementById(id).classList.remove('active'); }

function editProduct(id, name, price, stock, desc) {
    document.getElementById('productId').value = id;
    document.getElementById('productName').value = name;
    document.getElementById('productPrice').value = price;
    document.getElementById('productStock').value = stock;
    document.getElementById('productDesc').value = desc;
    openModal('productModal');
}

function saveProduct() {
    const data = {
        id: document.getElementById('productId').value,
        name: document.getElementById('productName').value,
        price: parseInt(document.getElementById('productPrice').value),
        stock: parseInt(document.getElementById('productStock').value),
        description: document.getElementById('productDesc').value
    };
    fetch('/admin/api/product', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
    }).then(() => { closeModal('productModal'); location.reload(); });
}

function deleteProduct(id) {
    if (!confirm('ডিলিট করবেন?')) return;
    fetch('/admin/api/product/' + id, {method: 'DELETE'}).then(() => location.reload());
}

function updateOrderStatus(id, status) {
    fetch('/admin/api/order/' + id + '/status', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({status: status})
    }).then(r => r.json()).then(d => alert(d.message || 'Updated'));
}

function deleteOrder(id) {
    if (!confirm('অর্ডার ডিলিট করবেন?')) return;
    fetch('/admin/api/order/' + id, {method: 'DELETE'}).then(() => location.reload());
}

function searchOrders() {
    const q = document.getElementById('orderSearch').value.toLowerCase();
    document.querySelectorAll('#orders tr[data-phone]').forEach(tr => {
        const phone = tr.dataset.phone.toLowerCase();
        const name = tr.dataset.name.toLowerCase();
        tr.style.display = (phone.includes(q) || name.includes(q)) ? '' : 'none';
    });
}

function sendBroadcast() {
    const text = document.getElementById('broadcastText').value.trim();
    if (!text) return;
    fetch('/admin/api/broadcast', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: text})
    }).then(r => r.json()).then(d => {
        document.getElementById('broadcastResult').textContent = d.message;
    });
}

function importCSV() {
    const text = document.getElementById('csvInput').value.trim();
    if (!text) return;
    fetch('/admin/api/products/import', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({csv: text})
    }).then(r => r.json()).then(d => {
        document.getElementById('importResult').textContent = d.message || d.error || 'Done';
        if (d.success) setTimeout(() => { closeModal('importModal'); location.reload(); }, 1500);
    });
}

function updatePreview() {
    document.getElementById('previewHeader').style.background = document.getElementById('settingHeader').value;
    document.getElementById('previewBtn').style.background = document.getElementById('settingPrimary').value;
}

function saveSettings() {
    const data = {
        business_name: document.getElementById('settingName').value,
        logo_url: document.getElementById('settingLogo').value,
        primary_color: document.getElementById('settingPrimary').value,
        header_color: document.getElementById('settingHeader').value,
        accent_color: document.getElementById('settingAccent').value,
    };
    fetch('/admin/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
    }).then(r => r.json()).then(d => {
        document.getElementById('settingsResult').textContent = d.message || 'সেভ হয়েছে! পেজ reload করুন।';
        if (d.success) setTimeout(() => location.reload(), 800);
    });
}
</script>

</body>
</html>
    """

def get_dashboard_stats():
    total_orders = db_query("SELECT COUNT(*) as c FROM orders", fetchone=True)
    revenue = db_query("SELECT COALESCE(SUM(total), 0) as s FROM orders WHERE status != 'cancelled'", fetchone=True)
    users = db_query("SELECT COUNT(*) as c FROM users", fetchone=True)
    pending = db_query("SELECT COUNT(*) as c FROM orders WHERE status IN ('pending', 'created')", fetchone=True)
    return {
        "total_orders": total_orders["c"] if total_orders else 0,
        "revenue": revenue["s"] if revenue else 0,
        "users": users["c"] if users else 0,
        "pending": pending["c"] if pending else 0,
    }

def init_admin_routes(app):
    
    @app.route("/admin", methods=["GET"])
    @login_required
    def admin_dashboard():
        stats = get_dashboard_stats()
        products = db_query("SELECT * FROM products ORDER BY id DESC", fetchall=True) or []
        orders = db_query("SELECT * FROM orders ORDER BY id DESC", fetchall=True) or []
        users = db_query("SELECT * FROM users ORDER BY last_active DESC", fetchall=True) or []
        recent_orders = db_query("SELECT * FROM orders ORDER BY created_at DESC LIMIT 5", fetchall=True) or []
        settings = get_all_settings()
        
        return render_template_string(make_admin_html(settings),
            stats=stats, products=products, orders=orders, users=users,
            recent_orders=recent_orders, settings=settings)
    
    @app.route("/admin/api/product", methods=["POST"])
    @login_required
    def admin_add_product():
        data = request.get_json() or {}
        pid = data.get("id")
        name = data.get("name", "").strip()
        price = data.get("price", 0)
        stock = data.get("stock", 0)
        desc = data.get("description", "").strip()
        
        if not name or price <= 0:
            return jsonify({"error": "Invalid data"}), 400
        
        if pid:
            db_query("UPDATE products SET name=?, price=?, stock=?, description=? WHERE id=?",
                     (name, price, stock, desc, pid), commit=True)
            return jsonify({"success": True, "message": "Updated"})
        else:
            db_query("INSERT INTO products (name, price, stock, description) VALUES (?, ?, ?, ?)",
                     (name, price, stock, desc), commit=True)
            return jsonify({"success": True, "message": "Added"})
    
    @app.route("/admin/api/product/<int:pid>", methods=["DELETE"])
    @login_required
    def admin_delete_product(pid):
        db_query("DELETE FROM products WHERE id = ?", (pid,), commit=True)
        return jsonify({"success": True})
    
    @app.route("/admin/api/order/<int:oid>/status", methods=["POST"])
    @login_required
    def admin_update_order_status(oid):
        data = request.get_json() or {}
        status = data.get("status", "").strip()
        if status:
            db_query("UPDATE orders SET status = ? WHERE id = ?", (status, oid), commit=True)
        return jsonify({"success": True, "message": "Status updated"})
    
    @app.route("/admin/api/order/<int:oid>", methods=["DELETE"])
    @login_required
    def admin_delete_order(oid):
        db_query("DELETE FROM orders WHERE id = ?", (oid,), commit=True)
        return jsonify({"success": True})
    
    @app.route("/admin/api/broadcast", methods=["POST"])
    @login_required
    def admin_broadcast():
        data = request.get_json() or {}
        msg = data.get("message", "").strip()
        if not msg:
            return jsonify({"error": "Empty message"}), 400
        
        try:
            from SMS_BOT import send_text
            users = db_query("SELECT phone FROM users", fetchall=True) or []
            sent = 0
            for u in users:
                try:
                    send_text(u["phone"], msg)
                    sent += 1
                except:
                    pass
            return jsonify({"success": True, "message": f"{sent} জনকে পাঠানো হয়েছে"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    
    @app.route("/admin/api/settings", methods=["POST"])
    @login_required
    def admin_save_settings():
        data = request.get_json() or {}
        for key in ["business_name", "logo_url", "primary_color", "header_color", "accent_color", "sidebar_color"]:
            if key in data:
                set_setting(key, data[key])
        return jsonify({"success": True, "message": "Settings saved! Reload to see changes."})
    
    @app.route("/admin/api/settings", methods=["GET"])
    @login_required
    def admin_get_settings():
        return jsonify(get_all_settings())
    
    @app.route("/admin/api/products/import", methods=["POST"])
    @login_required
    def admin_bulk_import():
        data = request.get_json() or {}
        csv_text = data.get("csv", "").strip()
        if not csv_text:
            return jsonify({"error": "Empty CSV"}), 400
        
        lines = csv_text.splitlines()
        if not lines:
            return jsonify({"error": "No lines"}), 400
        
        added = 0
        skipped = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                skipped += 1
                continue
            name = parts[0]
            try:
                price = int(parts[1])
            except:
                skipped += 1
                continue
            stock = int(parts[2]) if len(parts) > 2 and parts[2].strip() else 10
            desc = parts[3] if len(parts) > 3 else ""
            try:
                db_query("INSERT INTO products (name, price, stock, description) VALUES (?, ?, ?, ?)",
                         (name, price, stock, desc), commit=True)
                added += 1
            except Exception as e:
                skipped += 1
        return jsonify({"success": True, "added": added, "skipped": skipped, "message": f"{added} প্রোডাক্ট যোগ হয়েছে, {skipped} স্কিপ"})
    
    print("✅ Dynamic Admin Panel initialized at /admin")
