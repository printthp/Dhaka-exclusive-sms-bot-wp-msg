import os
import sys
import json
import sqlite3
import logging
from datetime import datetime
from threading import Thread, Lock
import time
import requests
from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session

# =====================================================================
# কনফিগারেশন এবং লগার
# =====================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = "dhaka_exclusive_secret_key_2026"
application = app

# মূল ডাটাবেজ ফাইল (সব লজিক এখানে একত্রে রাখা হয়েছে)
DB_FILE = "bot_ultimate.db"
db_lock = Lock()

# =====================================================================
# ডাটাবেজ ইনিশিয়ালাইজেশন (SMS_BOT + bot_extensions এর টেবিলগুলো)
# =====================================================================
def init_db():
    with db_lock:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        
        # SMS_BOT টেবিলসমূহ
        c.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, msg_id TEXT UNIQUE, from_number TEXT, content TEXT, msg_type TEXT DEFAULT 'text', direction TEXT DEFAULT 'inbound', agent_id TEXT DEFAULT 'system', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS sessions (phone TEXT PRIMARY KEY, state TEXT DEFAULT 'idle', context TEXT DEFAULT '{}', last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP, recovered INTEGER DEFAULT 0, bot_paused INTEGER DEFAULT 0)")
        c.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, name TEXT, address TEXT, city_id INTEGER DEFAULT 1, zone_id INTEGER DEFAULT 1, area_id INTEGER DEFAULT 1, product_id INTEGER, quantity INTEGER DEFAULT 1, total INTEGER, delivery_fee INTEGER, pathao_consignment_id TEXT, status TEXT DEFAULT 'pending', agent_name TEXT DEFAULT 'System', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, fb_product_id TEXT UNIQUE, name TEXT, price INTEGER, description TEXT, stock INTEGER DEFAULT 10, active INTEGER DEFAULT 1, image_url TEXT DEFAULT '')")
        c.execute("CREATE TABLE IF NOT EXISTS users (phone TEXT PRIMARY KEY, name TEXT DEFAULT 'Customer', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS complaints (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, complaint_text TEXT, status TEXT DEFAULT 'pending', resolved_by TEXT DEFAULT '', resolution_notes TEXT DEFAULT '', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        
        # bot_extensions টেবিলসমূহ (অ্যাডমিন/এজেন্ট ম্যানেজমেন্ট)
        c.execute("CREATE TABLE IF NOT EXISTS agents (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT, role TEXT DEFAULT 'representative', perm_chat INTEGER DEFAULT 1, perm_orders INTEGER DEFAULT 1, perm_config INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        c.execute("CREATE TABLE IF NOT EXISTS agent_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, action TEXT, details TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        
        conn.commit()
        conn.close()

init_db()

# =====================================================================
# ডাটাবেজ কুয়েরি হেল্পার
# =====================================================================
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
        finally:
            conn.close()

# =====================================================================
# এক্সটেনশন লজিক (bot_extensions থেকে আনা)
# =====================================================================
def get_agent_permissions(username):
    agent = db_query("SELECT * FROM agents WHERE username=?", (username,), fetchone=True)
    return agent if agent else {"role": "representative", "perm_chat": 0, "perm_orders": 0, "perm_config": 0}

# (বাকি সব ফাংশন যেমন sync_facebook_catalogue, send_whatsapp, process_webhook_async এখানে ক্রমানুসারে বসবে)

# =====================================================================
# সার্ভার স্টার্টআপ (run_server.py এর লজিক)
# =====================================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
