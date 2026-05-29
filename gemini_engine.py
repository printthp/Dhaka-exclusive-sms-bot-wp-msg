import os
import requests
import logging

logger = logging.getLogger(__name__)

def get_gemini_reply(user_msg, phone, db_query_func, settings):
    api_key = settings.get("gemini_key") or os.environ.get("GEMINI_KEY", "")
    if not api_key:
        return "Dhaka Exclusive এ স্বাগতম! আমাদের টিম শীঘ্রই যোগাযোগ করবে।"
    
    # প্রোডাক্ট ডাটা আনা
    products = db_query_func("SELECT name, price FROM products LIMIT 15", fetchall=True) or []
    p_text = "\n".join([f"- {p['name']}: {p['price']}৳" for p in products])
    
    # কাস্টমার ইতিহাস আনা
    orders = db_query_func("SELECT status FROM orders WHERE phone=? ORDER BY id DESC LIMIT 1", (phone,), fetchone=True)
    history = f"কাস্টমারের শেষ অর্ডারের স্ট্যাটাস: {orders['status']}" if orders else "নতুন কাস্টমার।"

    instruction = f"""তুমি Dhaka Exclusive-এর স্মার্ট সেলস AI। তোমার লক্ষ্য কাস্টমারকে সন্তুষ্ট করে অর্ডার নেওয়া।
বিজনেস তথ্য: ডেলিভারি ঢাকা ২৪ ঘণ্টা, বাইরে ৩ দিন। Cash on Delivery। 
প্রোডাক্ট লিস্ট:
{p_text}
ইতিহাস: {history}

নিয়ম:
১. বাংলায় উত্তর দাও। ২. কাস্টমারকে 'Sir/Maam' সম্বোধন করো। ৩. প্রতি উত্তরের শেষে অর্ডার করতে উৎসাহিত করো। ৪. বিনয়ী কিন্তু স্মার্ট হও।"""

    try:
        # লেটেস্ট মোডেল gemini-1.5-flash-latest ব্যবহার
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": f"{instruction}\n\nCustomer: {user_msg}"}]}],
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 450}
        }
        r = requests.post(url, json=payload, timeout=30).json()
        return r['candidates'][0]['content']['parts'][0]['text'].strip()
    except Exception as e:
        logger.error(f"AI Engine Error: {e}")
        return "ধন্যবাদ! আমরা আপনার মেসেজটি পেয়েছি এবং শীঘ্রই আমাদের প্রতিনিধি যোগাযোগ করবে।"
