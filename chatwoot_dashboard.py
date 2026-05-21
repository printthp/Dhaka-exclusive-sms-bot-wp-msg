"""
Chatwoot-style Conversation Inbox for WhatsApp Bot
Integrate with: from chatwoot_dashboard import init_chatwoot_routes
"""

import os
import sqlite3
import json
import time
from datetime import datetime
from flask import request, render_template_string, jsonify, redirect, url_for, session
from functools import wraps

DB_FILE = "bot_v3.db"

# =====================================================================
# Database Extensions for Chatwoot Features
# =====================================================================
def init_chatwoot_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    c = conn.cursor()
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE,
            name TEXT,
            status TEXT DEFAULT 'open',
            priority TEXT DEFAULT 'medium',
            agent TEXT,
            last_message TEXT,
            last_message_at TIMESTAMP,
            unread_count INTEGER DEFAULT 0,
            labels TEXT DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS conversation_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            phone TEXT,
            content TEXT,
            msg_type TEXT DEFAULT 'text',
            direction TEXT DEFAULT 'incoming',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id)
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            email TEXT UNIQUE,
            phone TEXT,
            role TEXT DEFAULT 'agent',
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS labels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            color TEXT DEFAULT '#667eea',
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS canned_responses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shortcut TEXT UNIQUE,
            content TEXT,
            active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    c.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER,
            content TEXT,
            agent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()

init_chatwoot_db()

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

# =====================================================================
# Auth
# =====================================================================
ADMIN_PANEL_USER = os.environ.get("ADMIN_PANEL_USER", "admin")
ADMIN_PANEL_PASS = os.environ.get("ADMIN_PANEL_PASS", "admin123")

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or auth.username != ADMIN_PANEL_USER or auth.password != ADMIN_PANEL_PASS:
            return ('<<h3>Unauthorized</h3>', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})
        return f(*args, **kwargs)
    return decorated

# =====================================================================
# Chatwoot HTML Template
# =====================================================================
CHATWOOT_HTML = """
<!DOCTYPE html>
<html lang="bn">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Chatwoot Inbox | Dhaka Exclusive</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f4f5f7; color: #2c3e50; height: 100vh; overflow: hidden; }
        
        .header { background: #1f2937; color: white; padding: 0 20px; height: 56px; display: flex; align-items: center; justify-content: space-between; }
        .header .brand { font-size: 18px; font-weight: 700; }
        .header .user { font-size: 14px; opacity: 0.8; }
        
        .app { display: flex; height: calc(100vh - 56px); }
        
        .sidebar { width: 320px; background: white; border-right: 1px solid #e5e7eb; display: flex; flex-direction: column; }
        .sidebar-header { padding: 16px; border-bottom: 1px solid #e5e7eb; }
        .sidebar-header h3 { font-size: 16px; margin-bottom: 12px; }
        .search-box { width: 100%; padding: 8px 12px; border: 1px solid #e5e7eb; border-radius: 8px; font-size: 14px; }
        .filters { display: flex; gap: 8px; margin-top: 10px; }
        .filter-btn { padding: 6px 12px; border: none; border-radius: 6px; font-size: 12px; cursor: pointer; background: #f3f4f6; }
        .filter-btn.active { background: #667eea; color: white; }
        
        .conversation-list { flex: 1; overflow-y: auto; }
        .conversation-item { padding: 14px 16px; border-bottom: 1px solid #f3f4f6; cursor: pointer; transition: 0.2s; display: flex; gap: 12px; }
        .conversation-item:hover { background: #f9fafb; }
        .conversation-item.active { background: #eff6ff; border-left: 3px solid #667eea; }
        .conversation-item.unread { background: #fef3c7; }
        
        .avatar { width: 40px; height: 40px; border-radius: 50%; background: linear-gradient(135deg, #667eea, #764ba2); display: flex; align-items: center; justify-content: center; color: white; font-weight: 600; font-size: 14px; flex-shrink: 0; }
        .conv-info { flex: 1; min-width: 0; }
        .conv-top { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
        .conv-name { font-weight: 600; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .conv-time { font-size: 11px; color: #9ca3af; }
        .conv-msg { font-size: 13px; color: #6b7280; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .conv-badges { display: flex; gap: 4px; margin-top: 6px; }
        .badge { padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 500; }
        .badge-label { background: #dbeafe; color: #1e40af; }
        .badge-status-open { background: #d1fae5; color: #065f46; }
        .badge-status-resolved { background: #e5e7eb; color: #374151; }
        .unread-count { background: #ef4444; color: white; padding: 2px 6px; border-radius: 10px; font-size: 11px; font-weight: 600; }
        
        .chat-area { flex: 1; display: flex; flex-direction: column; background: white; }
        .chat-header { padding: 16px 20px; border-bottom: 1px solid #e5e7eb; display: flex; justify-content: space-between; align-items: center; }
        .chat-contact { display: flex; align-items: center; gap: 12px; }
        .chat-contact-info h4 { font-size: 16px; }
        .chat-contact-info p { font-size: 13px; color: #6b7280; }
        .chat-actions { display: flex; gap: 8px; }
        .action-btn { padding: 8px 16px; border: 1px solid #e5e7eb; border-radius: 6px; background: white; cursor: pointer; font-size: 13px; }
        .action-btn.primary { background: #667eea; color: white; border-color: #667eea; }
        
        .messages { flex: 1; overflow-y: auto; padding: 20px; background: #f9fafb; }
        .message { display: flex; margin-bottom: 16px; }
        .message.incoming { justify-content: flex-start; }
        .message.outgoing { justify-content: flex-end; }
        .message-bubble { max-width: 70%; padding: 12px 16px; border-radius: 12px; font-size: 14px; line-height: 1.5; }
        .message.incoming .message-bubble { background: white; border: 1px solid #e5e7eb; border-bottom-left-radius: 4px; }
        .message.outgoing .message-bubble { background: #667eea; color: white; border-bottom-right-radius: 4px; }
        .message-time { font-size: 11px; color: #9ca3af; margin-top: 4px; text-align: right; }
        .message.outgoing .message-time { color: rgba(255,255,255,0.7); }
        
        .chat-input { padding: 16px 20px; border-top: 1px solid #e5e7eb; display: flex; gap: 12px; align-items: center; }
        .chat-input input { flex: 1; padding: 12px 16px; border: 1px solid #e5e7eb; border-radius: 8px; font-size: 14px; }
        .chat-input button { padding: 12px 24px; background: #667eea; color: white; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; }
        
        .right-panel { width: 280px; background: white; border-left: 1px solid #e5e7eb; padding: 20px; overflow-y: auto; }
        .right-panel h4 { font-size: 14px; text-transform: uppercase; color: #6b7280; margin-bottom: 12px; }
        .contact-card { background: #f9fafb; padding: 16px; border-radius: 8px; margin-bottom: 20px; }
        .contact-card p { font-size: 14px; margin-bottom: 8px; }
        .contact-card .label { color: #6b7280; font-size: 12px; }
        .contact-card .value { font-weight: 600; color: #1f2937; }
        
        .label-list { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 20px; }
        .label-tag { padding: 4px 12px; border-radius: 16px; font-size: 12px; font-weight: 500; cursor: pointer; }
        .label-tag:hover { opacity: 0.8; }
        
        .canned-list { display: flex; flex-direction: column; gap: 8px; }
        .canned-item { padding: 10px; background: #f3f4f6; border-radius: 6px; font-size: 13px; cursor: pointer; }
        .canned-item:hover { background: #e5e7eb; }
        .canned-shortcut { color: #667eea; font-weight: 600; font-size: 11px; }
        
        .notes-area { width: 100%; min-height: 80px; padding: 10px; border: 1px solid #e5e7eb; border-radius: 6px; font-size: 13px; resize: vertical; }
        .note-item { background: #fef3c7; padding: 10px; border-radius: 6px; margin-bottom: 8px; font-size: 13px; }
        .note-agent { font-size: 11px; color: #92400e; font-weight: 600; }
        
        @media (max-width: 1024px) { .right-panel { display: none; } }
        @media (max-width: 768px) { .sidebar { width: 100%; } .chat-area { display: none; } .chat-area.active { display: flex; position: fixed; top: 56px; left: 0; right: 0; bottom: 0; z-index: 100; } }
    </style>
</head>
<body>
    <div class="header">
        <div class="brand">💬 Chatwoot Inbox | {{ business_name }}</div>
        <div class="user">🔐 {{ user }}</div>
    </div>
    
    <div class="app">
        <div class="sidebar">
            <div class="sidebar-header">
                <h3>📥 ইনবক্স ({{ conversations|length }})</h3>
                <input type="text" class="search-box" id="searchInput" placeholder="কনভারসেশন সার্চ করুন..." onkeyup="searchConversations()">
                <div class="filters">
                    <button class="filter-btn active" onclick="filterStatus('all')">সব</button>
                    <button class="filter-btn" onclick="filterStatus('open')">খোলা</button>
                    <button class="filter-btn" onclick="filterStatus('resolved')">সমাধান</button>
                </div>
            </div>
            <div class="conversation-list" id="conversationList">
                {% for conv in conversations %}
                <div class="conversation-item {{ 'unread' if conv.unread_count > 0 else '' }}" data-id="{{ conv.id }}" data-status="{{ conv.status }}" onclick="loadConversation({{ conv.id }})">
                    <div class="avatar">{{ conv.name[:1] if conv.name else conv.phone[-2:] }}</div>
                    <div class="conv-info">
                        <div class="conv-top">
                            <span class="conv-name">{{ conv.name or conv.phone }}</span>
                            <span class="conv-time">{{ conv.last_message_at[:16] if conv.last_message_at else '' }}</span>
                        </div>
                        <div class="conv-msg">{{ conv.last_message or 'কোনো মেসেজ নেই' }}</div>
                        <div class="conv-badges">
                            <span class="badge badge-status-{{ conv.status }}">{{ 'খোলা' if conv.status == 'open' else 'সমাধান' if conv.status == 'resolved' else conv.status }}</span>
                            {% for label in conv.labels_list %}
                            <span class="badge badge-label" style="background: {{ label.color }}20; color: {{ label.color }}">{{ label.name }}</span>
                            {% endfor %}
                            {% if conv.unread_count > 0 %}
                            <span class="unread-count">{{ conv.unread_count }}</span>
                            {% endif %}
                        </div>
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
        
        <div class="chat-area" id="chatArea">
            <div class="chat-header" id="chatHeader">
                <div class="chat-contact">
                    <div class="avatar" id="chatAvatar">?</div>
                    <div class="chat-contact-info">
                        <h4 id="chatName">Select a conversation</h4>
                        <p id="chatPhone"></p>
                    </div>
                </div>
                <div class="chat-actions">
                    <button class="action-btn" onclick="resolveConversation()">✓ সমাধান</button>
                    <button class="action-btn" onclick="toggleStatus()">↻ পুনরায় খুলুন</button>
                    <button class="action-btn primary" onclick="assignAgent()">👤 এজেন্ট দিন</button>
                </div>
            </div>
            <div class="messages" id="messagesArea">
                <div style="text-align: center; color: #9ca3af; margin-top: 100px;">
                    📩 বাম পাশ থেকে একটি কনভারসেশন সিলেক্ট করুন
                </div>
            </div>
            <div class="chat-input">
                <input type="text" id="messageInput" placeholder="মেসেজ লিখুন..." onkeypress="if(event.key==='Enter')sendMessage()">
                <button onclick="sendMessage()">📤 পাঠান</button>
            </div>
        </div>
        
        <div class="right-panel">
            <div id="contactPanel">
                <h4>👤 যোগাযোগ তথ্য</h4>
                <div class="contact-card">
                    <p><span class="label">নাম</span><br><span class="value" id="contactName">-</span></p>
                    <p><span class="label">ফোন</span><br><span class="value" id="contactPhone">-</span></p>
                    <p><span class="label">মোট অর্ডার</span><br><span class="value" id="contactOrders">-</span></p>
                    <p><span class="label">মোট খরচ</span><br><span class="value" id="contactSpent">-</span></p>
                </div>
                
                <h4>🏷️ লেবেল</h4>
                <div class="label-list" id="labelList">
                    {% for label in labels %}
                    <span class="label-tag" style="background: {{ label.color }}20; color: {{ label.color }}; border: 1px solid {{ label.color }}" onclick="addLabel('{{ label.name }}')">{{ label.name }}</span>
                    {% endfor %}
                </div>
                
                <h4>⚡ প্রস্তুত উত্তর</h4>
                <div class="canned-list">
                    {% for canned in canned_responses %}
                    <div class="canned-item" onclick="useCanned('{{ canned.content|replace(chr(39), chr(92)+chr(39)) }}')">
                        <span class="canned-shortcut">/{{ canned.shortcut }}</span><br>
                        {{ canned.content[:50] }}{% if canned.content|length > 50 %}...{% endif %}
                    </div>
                    {% endfor %}
                </div>
                
                <h4>📝 প্রাইভেট নোট</h4>
                <textarea class="notes-area" id="noteInput" placeholder="প্রাইভেট নোট লিখুন..."></textarea>
                <button class="action-btn primary" style="width: 100%; margin-top: 8px;" onclick="addNote()">💾 নোট সেভ করুন</button>
                <div id="notesList" style="margin-top: 12px;"></div>
            </div>
        </div>
    </div>
    
    <script>
        let currentConvId = null;
        let currentPhone = null;
        
        function loadConversation(id) {
            currentConvId = id;
            document.querySelectorAll('.conversation-item').forEach(el => el.classList.remove('active'));
            document.querySelector(`[data-id="${id}"]`).classList.add('active');
            
            fetch(`/wp/api/conversations/${id}/messages`)
                .then(r => r.json())
                .then(data => {
                    currentPhone = data.phone;
                    document.getElementById('chatName').textContent = data.name || data.phone;
                    document.getElementById('chatPhone').textContent = data.phone;
                    document.getElementById('chatAvatar').textContent = (data.name || data.phone).slice(0, 1);
                    
                    document.getElementById('contactName').textContent = data.name || '-';
                    document.getElementById('contactPhone').textContent = data.phone;
                    document.getElementById('contactOrders').textContent = data.total_orders || '0';
                    document.getElementById('contactSpent').textContent = '৳' + (data.total_spent || '0');
                    
                    renderMessages(data.messages);
                    renderNotes(data.notes);
                });
        }
        
        function renderMessages(messages) {
            const area = document.getElementById('messagesArea');
            area.innerHTML = messages.map(m => `
                <div class="message ${m.direction}">
                    <div>
                        <div class="message-bubble">${escapeHtml(m.content)}</div>
                        <div class="message-time">${m.created_at ? m.created_at.slice(11, 16) : ''}</div>
                    </div>
                </div>
            `).join('');
            area.scrollTop = area.scrollHeight;
        }
        
        function renderNotes(notes) {
            document.getElementById('notesList').innerHTML = notes.map(n => `
                <div class="note-item">
                    <div class="note-agent">🕐 ${n.agent || 'System'}</div>
                    ${escapeHtml(n.content)}
                </div>
            `).join('');
        }
        
        function sendMessage() {
            const input = document.getElementById('messageInput');
            const text = input.value.trim();
            if (!text || !currentConvId) return;
            
            fetch('/wp/api/messages/send', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({conversation_id: currentConvId, phone: currentPhone, content: text})
            }).then(() => {
                input.value = '';
                setTimeout(() => loadConversation(currentConvId), 500);
            });
        }
        
        function useCanned(content) {
            document.getElementById('messageInput').value = content;
        }
        
        function addLabel(labelName) {
            if (!currentConvId) return;
            fetch('/wp/api/conversations/' + currentConvId + '/label', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({label: labelName})
            }).then(() => location.reload());
        }
        
        function addNote() {
            const text = document.getElementById('noteInput').value.trim();
            if (!text || !currentConvId) return;
            fetch('/wp/api/conversations/' + currentConvId + '/note', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({content: text})
            }).then(() => {
                document.getElementById('noteInput').value = '';
                loadConversation(currentConvId);
            });
        }
        
        function resolveConversation() {
            if (!currentConvId) return;
            fetch('/wp/api/conversations/' + currentConvId + '/resolve', {method: 'POST'})
                .then(() => location.reload());
        }
        
        function toggleStatus() {
            if (!currentConvId) return;
            fetch('/wp/api/conversations/' + currentConvId + '/reopen', {method: 'POST'})
                .then(() => location.reload());
        }
        
        function assignAgent() {
            const agent = prompt('Enter agent name:');
            if (!agent || !currentConvId) return;
            fetch('/wp/api/conversations/' + currentConvId + '/assign', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({agent: agent})
            }).then(() => location.reload());
        }
        
        function searchConversations() {
            const q = document.getElementById('searchInput').value.toLowerCase();
            document.querySelectorAll('.conversation-item').forEach(el => {
                const text = el.textContent.toLowerCase();
                el.style.display = text.includes(q) ? '' : 'none';
            });
        }
        
        function filterStatus(status) {
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            event.target.classList.add('active');
            document.querySelectorAll('.conversation-item').forEach(el => {
                if (status === 'all') { el.style.display = ''; }
                else { el.style.display = el.dataset.status === status ? '' : 'none'; }
            });
        }
        
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        setInterval(() => {
            if (currentConvId) loadConversation(currentConvId);
        }, 10000);
    </script>
</body>
</html>
"""

# =====================================================================
# Routes
# =====================================================================
def init_chatwoot_routes(app):
    
    @app.route("/wp", methods=["GET"])
    @login_required
    def chatwoot_inbox():
        conversations = db_query("""
            SELECT c.*, u.total_orders, u.total_spent 
            FROM conversations c 
            LEFT JOIN users u ON c.phone = u.phone 
            ORDER BY c.last_message_at DESC
        """, fetchall=True)
        
        labels = db_query("SELECT * FROM labels", fetchall=True)
        canned = db_query("SELECT * FROM canned_responses WHERE active = 1", fetchall=True)
        
        for conv in conversations:
            try:
                conv["labels_list"] = json.loads(conv.get("labels", "[]"))
            except:
                conv["labels_list"] = []
        
        return render_template_string(CHATWOOT_HTML, 
            conversations=conversations, 
            labels=labels, 
            canned_responses=canned,
            business_name=os.environ.get("BUSINESS_NAME", "Dhaka Exclusive"),
            user=ADMIN_PANEL_USER)
    
    @app.route("/wp/api/conversations/<int:conv_id>/messages", methods=["GET"])
    @login_required
    def get_conversation_messages(conv_id):
        conv = db_query("SELECT * FROM conversations WHERE id = ?", (conv_id,), fetchone=True)
        if not conv:
            return jsonify({"error": "Not found"}), 404
        
        messages = db_query(
            "SELECT * FROM conversation_messages WHERE conversation_id = ? ORDER BY created_at",
            (conv_id,), fetchall=True)
        
        notes = db_query(
            "SELECT * FROM notes WHERE conversation_id = ? ORDER BY created_at DESC",
            (conv_id,), fetchall=True)
        
        user = db_query("SELECT * FROM users WHERE phone = ?", (conv["phone"],), fetchone=True)
        
        return jsonify({
            **conv,
            "messages": messages,
            "notes": notes,
            "total_orders": user["total_orders"] if user else 0,
            "total_spent": user["total_spent"] if user else 0
        })
    
    @app.route("/wp/api/messages/send", methods=["POST"])
    @login_required
    def send_chatwoot_message():
        data = request.get_json() or {}
        conv_id = data.get("conversation_id")
        phone = data.get("phone")
        content = data.get("content", "").strip()
        
        if not all([conv_id, phone, content]):
            return jsonify({"error": "Missing data"}), 400
        
        db_query(
            "INSERT INTO conversation_messages (conversation_id, phone, content, direction) VALUES (?, ?, ?, ?)",
            (conv_id, phone, content, "outgoing"), commit=True)
        
        db_query(
            "UPDATE conversations SET last_message = ?, last_message_at = CURRENT_TIMESTAMP WHERE id = ?",
            (content, conv_id), commit=True)
        
        try:
            from SMS_BOT import send_text
            send_text(phone, content)
        except Exception as e:
            print(f"WhatsApp send error: {e}")
        
        return jsonify({"success": True})
    
    @app.route("/wp/api/conversations/<int:conv_id>/label", methods=["POST"])
    @login_required
    def add_conversation_label(conv_id):
        data = request.get_json() or {}
        label_name = data.get("label", "").strip()
        
        conv = db_query("SELECT labels FROM conversations WHERE id = ?", (conv_id,), fetchone=True)
        if not conv:
            return jsonify({"error": "Not found"}), 404
        
        try:
            labels = json.loads(conv["labels"] or "[]")
        except:
            labels = []
        
        if label_name not in labels:
            labels.append(label_name)
            db_query("UPDATE conversations SET labels = ? WHERE id = ?",
                     (json.dumps(labels), conv_id), commit=True)
        
        return jsonify({"success": True})
    
    @app.route("/wp/api/conversations/<int:conv_id>/note", methods=["POST"])
    @login_required
    def add_conversation_note(conv_id):
        data = request.get_json() or {}
        content = data.get("content", "").strip()
        
        if content:
            db_query("INSERT INTO notes (conversation_id, content, agent) VALUES (?, ?, ?)",
                     (conv_id, content, ADMIN_PANEL_USER), commit=True)
        
        return jsonify({"success": True})
    
    @app.route("/wp/api/conversations/<int:conv_id>/resolve", methods=["POST"])
    @login_required
    def resolve_conversation(conv_id):
        db_query("UPDATE conversations SET status = 'resolved' WHERE id = ?", (conv_id,), commit=True)
        return jsonify({"success": True})
    
    @app.route("/wp/api/conversations/<int:conv_id>/reopen", methods=["POST"])
    @login_required
    def reopen_conversation(conv_id):
        db_query("UPDATE conversations SET status = 'open' WHERE id = ?", (conv_id,), commit=True)
        return jsonify({"success": True})
    
    @app.route("/wp/api/conversations/<int:conv_id>/assign", methods=["POST"])
    @login_required
    def assign_conversation(conv_id):
        data = request.get_json() or {}
        agent = data.get("agent", "").strip()
        
        if agent:
            db_query("UPDATE conversations SET agent = ? WHERE id = ?", (agent, conv_id), commit=True)
        
        return jsonify({"success": True})
    
    print("✅ Chatwoot dashboard initialized at /wp")


def sync_message_to_conversation(phone, content, msg_type="text"):
    """Call this from your main bot when a message arrives"""
    try:
        conv = db_query("SELECT id FROM conversations WHERE phone = ?", (phone,), fetchone=True)
        
        if conv:
            conv_id = conv["id"]
            db_query(
                "UPDATE conversations SET last_message = ?, last_message_at = CURRENT_TIMESTAMP, unread_count = unread_count + 1 WHERE id = ?",
                (content[:100], conv_id), commit=True)
        else:
            db_query(
                "INSERT INTO conversations (phone, last_message, last_message_at, unread_count) VALUES (?, ?, CURRENT_TIMESTAMP, 1)",
                (phone, content[:100]), commit=True)
            conv = db_query("SELECT id FROM conversations WHERE phone = ?", (phone,), fetchone=True)
            conv_id = conv["id"]
        
        db_query(
            "INSERT INTO conversation_messages (conversation_id, phone, content, msg_type, direction) VALUES (?, ?, ?, ?, ?)",
            (conv_id, phone, content, msg_type, "incoming"), commit=True)
        
    except Exception as e:
        print(f"Sync error: {e}")
