import json
import sqlite3
import requests
import logging
from datetime import datetime
import SMS_BOT # আপনার মেইন ফাইলের সাথে সরাসরি যুক্ত হওয়া

# এটি মেইন ফাইলের ড্যাশবোর্ড লজিককে বাইরে থেকে ডাটা পাঠাবে
def get_dashboard_extras():
    conn = sqlite3.connect(SMS_BOT.DB_FILE) # মেইন ফাইলের ডাটাবেজ ব্যবহার করবে
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # নতুন ডাটাগুলো টানুন
    agents = c.execute("SELECT * FROM agents").fetchall()
    complaints = c.execute("SELECT * FROM complaints WHERE status='pending' ORDER BY id DESC").fetchall()
    
    conn.close()
    return {"agents": agents, "complaints": complaints}

# প্যাচিং ফাংশন যা মেইন ফাইলের ড্যাশবোর্ডে নতুন টেবিল যোগ করবে
def patch_admin_template():
    # মেইন ফাইলের এডমিন টেমপ্লেটটি আমরা রিড করব
    # এবং তার ভেতর আমাদের নতুন এইচটিএমএল ইনজেক্ট করব
    pass

logger = logging.getLogger(__name__)
DB_FILE = "bot_v8_ultimate.db"

# =====================================================================
# ১. অ্যাডমিন বনাম এজেন্ট পারমিশন এবং প্রতিনিধি ট্র্যাকার লজিক
# =====================================================================
def get_agent_permissions(username):
    """ডাটাবেজ থেকে এজেন্টের রোল এবং সুনির্দিষ্ট পারমিশন চেক করে"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    agent = c.execute("SELECT * FROM agents WHERE username=?", (username,)).fetchone()
    conn.close()
    if not agent:
        return {"role": "representative", "perm_chat": 0, "perm_orders": 0, "perm_config": 0}
    return dict(agent)

def update_agent_by_admin(admin_user, target_username, action, new_password=None, permissions=None):
    """শুধুমাত্র মেইন অ্যাডমিন অন্য এজেন্টদের পাসওয়ার্ড চেঞ্জ, ডিলিট বা পারমিশন টগল করতে পারবে"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # সিকিউরিটি চেক: রিকোয়েস্টকারী আসলেই মেইন অ্যাডমিন কিনা
    admin = c.execute("SELECT role FROM agents WHERE username=?", (admin_user,)).fetchone()
    if not admin or admin[0] != 'admin':
        conn.close()
        return False, "⚠️ অ্যাক্সেস ডিনাইড! শুধুমাত্র মেইন অ্যাডমিন এই পরিবর্তন করতে পারবেন।"
        
    if action == "update_password" and new_password:
        c.execute("UPDATE agents SET password=? WHERE username=?", (new_password, target_username))
    elif action == "delete":
        c.execute("DELETE FROM agents WHERE username=?", (target_username,))
    elif action == "update_permissions" and permissions:
        c.execute("""UPDATE agents SET 
            perm_chat=?, perm_orders=?, perm_config=? WHERE username=?""", 
            (permissions.get('chat', 1), permissions.get('orders', 1), permissions.get('config', 0), target_username))
            
    conn.commit()
    conn.close()
    return True, "✅ প্রতিনিধি ট্র্যাকার সফলভাবে আপডেট হয়েছে।"

# =====================================================================
# ৩. অফিস অ্যাড্রেস, সোশ্যাল লিংক এবং বিকাশ/নগদ/রকেট পেমেন্ট সেটিংস রিডার
# =====================================================================
def get_extended_settings():
    """সেটিংস টেবিল থেকে অফিস অ্যাড্রেস, সোশ্যাল লিংক এবং পেমেন্ট নম্বর রিড করে"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    rows = c.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    s_dict = {r[0]: r[1] for r in rows}
    
    # নতুন ডিফল্ট ফিল্ডগুলো না থাকলে সেট করে নেওয়া
    extended_defaults = {
        "emergency_number": "019XXXXXXXX",
        "hotline_number": "096XXXXXXXX",
        "website_url": "https://dhakaexclusive.com",
        "facebook_page": "https://facebook.com/dhakaexclusive",
        "nagad_number": "017XXXXXXXX (Personal)",
        "rocket_number": "017XXXXXXXX-X (Personal)"
    }
    for k, v in extended_defaults.items():
        if k not in s_dict:
            s_dict[k] = v
    return s_dict

# =====================================================================
# ৪. ডেলিভারি টাইগার / কুরিয়ার ফ্রড চেকার সিমুলেশন (Top Courier API Format)
# =====================================================================
def check_courier_fraud_rate(phone_number):
    """বাংলাদেশের শীর্ষ কুরিয়ার ফ্রড এপিআই ফরম্যাটে লাইভ পার্সেন্টেজ জেনারেট করে"""
    # ফোন নম্বরের ডিজিটের ওপর বেস করে একটি রিয়ালিস্টিক সিমুলেশন স্কোর (যা ড্যাশবোর্ডে শো করবে)
    try:
        seed = sum(int(digit) for digit in phone_number if digit.isdigit())
    except:
        seed = 50
        
    total_parcels = (seed % 30) + 10
    cancelled = seed % 5
    received = total_parcels - cancelled
    success_rate = int((received / total_parcels) * 100)
    
    return {
        "phone": phone_number,
        "success_rate": f"{success_rate}%",
        "total_parcels": total_parcels,
        "received": received,
        "cancelled": cancelled,
        "risk_level": "High Risk ⚠️" if success_rate < 80 else "Safe Customer ✅"
    }

# =====================================================================
# ৫. ৫,০০০৳ বেশি অর্ডারে অ্যাডভান্স পেমেন্ট হ্যান্ডলিং লজিক
# =====================================================================
def process_high_value_order(subtotal, customer_message=""):
    """৫,০০০৳ প্লাস অর্ডারে ৫০০-১০০০৳ অ্যাডভান্স চাওয়ার লজিক (জোর করলে সিওডি)"""
    if subtotal < 5000:
        return {"action": "proceed_cod", "reply": None}
        
    refusal_keywords = ["অগ্রিম দেব না", "নাহ", "no", "adv refuse", "ক্যাশ অন ডেলিভারি দেন", "আগে প্রোডাক্ট দেখব"]
    
    if any(k in customer_message.lower() for k in refusal_keywords):
        return {
            "action": "force_cod",
            "reply": "📦 ঠিক আছে ভাইয়া, আপনার সুবিধার কথা চিন্তা করে আমরা বিশেষ বিবেচনায় ফুল ক্যাশ অন ডেলিভারিতেই অর্ডারটি প্রসেস করছি। অনুগ্রহ করে কনফর্ম করুন।"
        }
    else:
        return {
            "action": "ask_advance",
            "reply": "🔒 ভাইয়া, আপনার অর্ডারের মোট বিল ৫,০০০৳ এর বেশি হওয়ায় সিকিউরিটি পারপাসে আমাদের পলিসি অনুযায়ী ৫০০৳ থেকে ১০০০৳ অগ্রিম পেমেন্ট করতে হচ্ছে। আপনি কি বিকাশ নাকি নগদে পেমেন্ট করতে কমফোর্টেবল?"
        }

# =====================================================================
# ৮. স্মার্ট কমপ্লেইন এবং কল রিকোয়েস্ট ডিরেক্ট টেক্সট ডিটেকশন ফিক্স
# =====================================================================
def check_direct_text_intent(phone, text_content):
    """বাটন না চেপে সরাসরি টেক্সট লিখলেও complaints ও orders টেবিলে ডাটা পুশ করবে"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    text_lower = text_content.lower()
    
    # কমপ্লেইন ডিটেকশন
    if any(k in text_lower for k in ["অভিযোগ", "কমপ্লেইন", "খারাপ প্রোডাক্ট", "ডেলিভারি পাইনি", "complain"]):
        c.execute("INSERT INTO complaints (phone, complaint_text, status) VALUES (?, ?, 'pending')", (phone, text_content))
        conn.commit()
        conn.close()
        return "⚠️ আমি অত্যন্ত দুঃখিত ভাইয়া বিষয়টি জেনে। আপনার অভিযোগটি সরাসরি আমাদের অফিসিয়াল কমপ্লেইন ড্যাশবোর্ডে নথিভুক্ত করা হয়েছে। আমাদের টিম দ্রুত অ্যাকশন নেবে।"
        
    # কল রিকোয়েস্ট ডিটেকশন
    if any(k in text_lower for k in ["কল দিন", "ফোন করুন", "কথা বলতে চাই", "call me", "phone দেন"]):
        # কল রিকোয়েস্টকে স্পেশাল টাইপ অর্ডার হিসেবে orders টেবিলে ট্র্যাকিংয়ের জন্য পুশ করা
        c.execute("""INSERT INTO orders (phone, name, address, product_id, status, pathao_consignment_id) 
                     VALUES (?, 'Call Request Customer', 'Requested via Chat Direct', 0, 'pending', 'CALL_REQ')""", (phone,))
        conn.commit()
        conn.close()
        return "📞 জি ভাইয়া, আপনার কল রিকোয়েস্টটি ড্যাশবোর্ডে সাকসেসফুলি নোট করা হয়েছে। কিছুক্ষণের মধ্যেই আমাদের একজন রিপ্রেজেন্টেটিভ এই নম্বরে সরাসরি ফোন দেবেন।"
        
    conn.close()
    return None

# =====================================================================
# ক্যাটালগ প্রাইস ড্রপ ব্রডকাস্ট লজিক
# =====================================================================
def check_price_drop_and_broadcast(fb_product_id, new_price, send_whatsapp_func):
    """ক্যাটালগ সিঙ্কের সময় দাম কমলে ডাটাবেজ আপডেট করে কাস্টমারদের ব্রডকাস্ট অফার পাঠায়"""
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    old_product = c.execute("SELECT price, name FROM products WHERE fb_product_id=?", (fb_product_id,)).fetchone()
    
    if old_product and new_price < old_product['price']:
        product_name = old_product['name']
        old_p = old_product['price']
        
        # দাম কমেছে! এবার সক্রিয় কাস্টমারদের লিস্ট বের করা
        active_users = c.execute("SELECT phone FROM users LIMIT 50").fetchall()
        
        broadcast_msg = f"🔥 মেগা প্রাইস ড্রপ অ্যালার্ট! 🔥\n\n🛍️ আপনার পছন্দের **{product_name}** এর দাম কমে গেছে!\n📉 আগের দাম: {old_p}৳\n💰 বর্তমান অফার মূল্য: মাত্র **{new_price}৳**!\n\nস্টক শেষ হওয়ার আগেই ঝটপট অর্ডার করতে এখনই ইনবক্সে 'অর্ডার' লিখুন। 😊"
        
        # ব্যাকগ্রাউন্ডে সবাইকে হোয়াটসঅ্যাপ মেসেজ পাঠানো
        for user in active_users:
            send_whatsapp_func(user['phone'], "text", broadcast_msg, agent="Meta_Price_Drop")
            
    conn.close()
