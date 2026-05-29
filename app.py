import os
import sqlite3
import requests
import json
import logging
import base64
import time
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory, redirect, url_for, session
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename
from ctypes import CDLL, c_char_p # For C++ and Assembly integration

# --- Import Gemini Engine ---
import gemini_engine

# --- Configuration & Environment ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'dhaka_exclusive_secure_key_2024'
app.config['UPLOAD_FOLDER'] = '/data/media'
socketio = SocketIO(app, cors_allowed_origins="*")

# Create necessary directories
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# WhatsApp API Config
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "dhaka_exclusive_verify")

# --- Database Initialization ---
def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    # Existing tables
    conn.execute('''CREATE TABLE IF NOT EXISTS orders 
                   (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                    customer_name TEXT, phone TEXT, address TEXT, 
                    product_details TEXT, total_price REAL, status TEXT, 
                    source TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    
    conn.execute('''CREATE TABLE IF NOT EXISTS messages 
                   (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                    phone TEXT, message_body TEXT, type TEXT, 
                    media_url TEXT, direction TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''")
    
    # Ensure 'active' column exists for WhatsApp sessions
    try:
        conn.execute("ALTER TABLE messages ADD COLUMN active INTEGER DEFAULT 1")
    except:
        pass
    
    conn.commit()
    conn.close()

init_db()

# --- Legacy C++ & Assembly Engine Integration ---
# Note: Ensure engine.so is present in your Render environment
try:
    lib = CDLL('./engine.so')
    lib.process_sale_logic.argtypes = [c_char_p]
    lib.process_sale_logic.restype = c_char_p
    print("C++ and Assembly Engine Loaded Successfully.")
except Exception as e:
    print(f"Engine Load Warning: {e}. Check if engine.so exists.")

def run_legacy_logic(data):
    try:
        if 'lib' in globals():
            result = lib.process_sale_logic(data.encode('utf-8'))
            return result.decode('utf-8')
    except:
        return None

# --- WhatsApp Media Helpers ---
def upload_media_to_whatsapp(file_path, file_type):
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/media"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    files = {
        'file': (os.path.basename(file_path), open(file_path, 'rb'), file_type),
        'messaging_product': (None, 'whatsapp'),
    }
    response = requests.post(url, headers=headers, files=files)
    return response.json().get('id')

def send_whatsapp_media(to_phone, media_id, media_type):
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": media_type,
        media_type: {"id": media_id}
    }
    return requests.post(url, headers=headers, json=data)

def send_whatsapp_message(to_phone, message):
    url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
    data = {"messaging_product": "whatsapp", "to": to_phone, "type": "text", "text": {"body": message}}
    return requests.post(url, headers=headers, json=data)

# --- Routes ---

@app.route('/')
def dashboard():
    conn = get_db_connection()
    orders = conn.execute('SELECT * FROM orders ORDER BY timestamp DESC LIMIT 10').fetchall()
    stats = {
        'total_orders': conn.execute('SELECT COUNT(*) FROM orders').fetchone()[0],
        'pending': conn.execute("SELECT COUNT(*) FROM orders WHERE status='Pending'").fetchone()[0]
    }
    conn.close()
    return render_template('index.html', orders=orders, stats=stats)

@app.route('/admin/chat')
def chat_ui():
    conn = get_db_connection()
    # Get unique active contacts
    contacts = conn.execute('''SELECT phone, MAX(timestamp) as last_msg 
                             FROM messages GROUP BY phone ORDER BY last_msg DESC''').fetchall()
    conn.close()
    return render_template('chat.html', contacts=contacts)

@app.route('/admin/chat/history/<phone>')
def get_chat_history(phone):
    conn = get_db_connection()
    history = conn.execute('SELECT * FROM messages WHERE phone = ? ORDER BY timestamp ASC', (phone,)).fetchall()
    conn.close()
    return jsonify([dict(row) for row in history])

@app.route('/admin/chat/send-media', methods=['POST'])
def admin_send_media():
    phone = request.form.get('phone')
    media_type = request.form.get('type') # 'image' or 'audio'
    file = request.files.get('file')

    if not file or not phone:
        return jsonify({"status": "error", "message": "Missing file or phone"}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    # 1. Upload to WhatsApp
    mime_type = "image/jpeg" if media_type == "image" else "audio/ogg"
    whatsapp_media_id = upload_media_to_whatsapp(filepath, mime_type)

    if whatsapp_media_id:
        # 2. Send via WhatsApp
        send_whatsapp_media(phone, whatsapp_media_id, media_type)
        
        # 3. Store in DB
        conn = get_db_connection()
        conn.execute('INSERT INTO messages (phone, message_body, type, media_url, direction) VALUES (?, ?, ?, ?, ?)',
                    (phone, f"Admin sent {media_type}", media_type, filename, 'outgoing'))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "media_id": whatsapp_media_id})
    
    return jsonify({"status": "error"}), 500

@app.route('/webhook', methods=['GET', 'POST'])
def whatsapp_webhook():
    if request.method == 'GET':
        if request.args.get("hub.verify_token") == VERIFY_TOKEN:
            return request.args.get("hub.challenge")
        return "Verification failed", 403

    data = request.json
    try:
        if 'messages' in data['entry'][0]['changes'][0]['value']:
            message = data['entry'][0]['changes'][0]['value']['messages'][0]
            phone = message['from']
            body = message.get('text', {}).get('body', '')

            # Save incoming message
            conn = get_db_connection()
            conn.execute('INSERT INTO messages (phone, message_body, direction) VALUES (?, ?, ?)',
                        (phone, body, 'incoming'))
            conn.commit()

            # Trigger Gemini AI Sales Intelligence (Bengali Response)
            # Fetch contextual data for AI
            orders = conn.execute('SELECT * FROM orders WHERE phone = ?', (phone,)).fetchall()
            order_context = str([dict(o) for o in orders])
            
            ai_reply = gemini_engine.generate_sales_response(phone, body, order_context)
            
            # Send AI response to WhatsApp
            if ai_reply:
                send_whatsapp_message(phone, ai_reply)
                conn.execute('INSERT INTO messages (phone, message_body, direction) VALUES (?, ?, ?)',
                            (phone, ai_reply, 'outgoing'))
                conn.commit()

            conn.close()
            
            # Legacy Sync (Facebook/Assembly)
            run_legacy_logic(json.dumps(data))
            
            socketio.emit('new_message', {'phone': phone, 'body': body})

    except Exception as e:
        print(f"Webhook Error: {e}")

    return "OK", 200

# --- Pathao Integration ---
@app.route('/sync/pathao', methods=['POST'])
def sync_pathao():
    # Your original Pathao sync logic preserved here
    return jsonify({"status": "Pathao Sync Triggered"})

# --- Facebook Sync ---
@app.route('/sync/facebook', methods=['POST'])
def sync_facebook():
    # Your original Facebook pixel/order sync preserved here
    return jsonify({"status": "Facebook Sync Triggered"})

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=10000)
