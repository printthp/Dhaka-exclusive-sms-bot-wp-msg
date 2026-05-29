import os
import requests
import logging
import json

# Setup logging
logger = logging.getLogger(__name__)

# Fetch API Key from environment (Render environment settings এ এই নামে সেভ থাকতে হবে: GEMINI_KEY)
GEMINI_API_KEY = os.environ.get("GEMINI_KEY", "")

def get_optimized_gemini_reply(user_message, phone, db_query_func):
    """
    Sales Intelligence AI Engine for Dhaka Exclusive.
    Optimized for Gemini 1.5 Flash (v1beta).
    """
    
    if not GEMINI_API_KEY:
        logger.warning("CRITICAL: GEMINI_KEY missing in Environment Variables!")
        return "সালাম ভাই! Dhaka Exclusive এ আপনাকে স্বাগতম। আমাদের একজন রিপ্রেজেন্টেটিভ খুব শীঘ্রই আপনার সাথে চ্যাটে যোগাযোগ করবেন।"

    # 1. Fetch Customer Context (Checking orders table)
    history_ctx = "New Customer (No history yet)"
    try:
        # SQL ফিক্স: আমরা শুধু আইডি এবং স্ট্যাটাস দেখছি যাতে কোনো ইরর না আসে
        history = db_query_func("SELECT id, total, status FROM orders WHERE phone=? ORDER BY id DESC LIMIT 2", (phone,), fetchall=True) or []
        if history:
            history_ctx = f"Customer History: {json.dumps(history)}"
    except Exception as e:
        logger.error(f"Context error (Safe to ignore): {e}")

    # 2. Fetch Product Context (From DB products table)
    product_list = "বর্তমানে ক্যাটালগ আপডেট হচ্ছে। কাস্টমারকে আমাদের নতুন কালেকশন সম্পর্কে সাধারণ ধারণা দিন।"
    try:
        products = db_query_func("SELECT name, price FROM products LIMIT 15", fetchall=True) or []
        if products:
            product_list = "\n".join([f"- {p['name']}: {p['price']}৳" for p in products])
    except Exception as e:
        logger.error(f"Catalog error (Safe to ignore): {e}")

    # 3. Enhanced Sales System Instruction
    system_instruction = f"""
    তুমি 'Dhaka Exclusive'-এর প্রধান AI সেলস অ্যাসিস্ট্যান্ট। তোমার কাজ গ্রাহকদের সাথে প্রফেশনাল এবং স্মার্টলি কথা বলে সেলস ক্লোজ করা।
    
    মুল গাইডলাইন:
    - ভাষা: মার্জিত বাংলা (কাস্টমারকে 'ভাই/আপু' বলে ডাকবে)।
    - টোন: বন্ধুত্বপূর্ণ এবং সাহায্যকারী।
    - ডেলিভারি তথ্য: ঢাকা সিটি ২৪ ঘণ্টা, বাইরে ৪৮-৭২ ঘণ্টা। ক্যাশ অন ডেলিভারি সহজলভ্য।
    - লক্ষ্য: কাস্টমার বিরক্ত যাতে না হয়, প্রতিটি মেসেজের শেষ একটি সুন্দর প্রশ্ন থাকবে।
    - অর্ডার প্রসেস: অর্ডার করতে চাইলে ঠিকানা ও ফোন নম্বর চাইবে।
    
    ডাটাবেস তথ্য (ব্যবহার করার জন্য):
    উপলব্ধ পণ্য:
    {product_list}
    
    কাস্টমারের পূর্বের অর্ডার তথ্য:
    {history_ctx}
    """

    # 4. API Call - Using exact model path that works for v1beta
    # এই URL টি একদম লেটেস্ট ফরম্যাটে সাজানো হয়েছে
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"
    
    payload = {
        "contents": [{
            "parts": [{"text": f"{system_instruction}\n\nCustomer: {user_message}"}]
        }],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 600,
            "topP": 0.9
        }
    }
    
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=25)
        res_data = response.json()

        # যদি প্রাইমারি মডেল কোনো কারণে ফেল করে, তবে gemini-pro ট্রাই করবে
        if 'error' in res_data:
            logger.info("Retrying with backup gemini-1.5-flash name...")
            alt_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
            response = requests.post(alt_url, json=payload, headers=headers, timeout=25)
            res_data = response.json()

        if 'candidates' in res_data and len(res_data['candidates']) > 0:
            ai_reply = res_data['candidates'][0]['content']['parts'][0]['text']
            return ai_reply.strip()
        
        logger.error(f"Full API Error Detail: {res_data}")
        return "ধন্যবাদ! আপনার মেসেজটি আমরা পেয়েছি। আমাদের সেলস টিম খুব দ্রুত আপনার সাথে যোগাযোগ করবে।"

    except Exception as e:
        logger.error(f"Critical Engine Error: {e}")
        return "সালাম ভাই! আমাদের AI সিস্টেমে কিছুটা কাজ চলছে। আমরা দ্রুত আপনার চ্যাটের উত্তর দিচ্ছি।"
