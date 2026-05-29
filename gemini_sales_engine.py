import os
import requests
import logging
import json

# Setup
GEMINI_API_KEY = os.environ.get("GEMINI_KEY", "")
logger = logging.getLogger(__name__)

def get_optimized_gemini_reply(user_message, phone, db_query_func):
    """উন্নত সেলস ইন্টেলিজেন্ট ইঞ্জিন"""
    if not GEMINI_API_KEY:
        return "Dhaka Exclusive এ আপনাকে স্বাগতম! আমরা শীঘ্রই আপনার সাথে যোগাযোগ করবো।"

    # ১. প্রোডাক্ট এবং কাস্টমার হিস্ট্রি সংগ্রহ (Context)
    # আপনার কাস্টমারের অর্ডার হিস্ট্রি
    history = db_query_func("SELECT * FROM orders WHERE phone=? ORDER BY id DESC LIMIT 2", (phone,), fetchall=True) or []
    order_context = f"Customer Past Orders: {json.dumps(history)}" if history else "New Customer"
    
    # প্রোডাক্ট ক্যাটালগ (টেবিল থেকে)
    products = db_query_func("SELECT name, price FROM products LIMIT 15", fetchall=True) or []
    product_list = "\n".join([f"- {p['name']}: {p['price']}৳" for p in products])

    # ২. সিস্টেম প্রম্পট (বাংলায় সেলস করার জন্য)
    system_instruction = f"""
    তুমি 'Dhaka Exclusive'-এর প্রধান AI সেলস অ্যাসিস্ট্যান্ট। তোমার কাজ গ্রাহককে পণ্য কিনতে উৎসাহিত করা। 
    
    নিয়মাবলী:
    - ভাষা: সবসময় প্রফেশনাল বাংলায় কথা বলো। গ্রাহককে 'ভাই/আপু' বলে সম্বোধন করো।
    - লক্ষ্য: প্রতিটি মেসেজের শেষে গ্রাহককে অর্ডার কনফার্ম করতে বা অন্য কিছু দেখতে উৎসাহিত করো।
    - লজিস্টিকস: ঢাকা সিটিতে ২৪ ঘণ্টা, বাইরে ৪৮-৭২ ঘণ্টা ডেলিভারি। ক্যাশ অন ডেলিভারি সহজলভ্য।
    
    বর্তমান ডাটা:
    পণ্য তালিকা:
    {product_list}
    
    গ্রাহকের তথ্য:
    {order_context}
    """

    try:
        # মডেল আপডেট: gemini-1.5-flash-latest (বেশি স্থিতিশীল এবং দ্রুত)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "parts": [{"text": f"{system_instruction}\n\nCustomer: {user_message}"}]
            }]
        }
        r = requests.post(url, json=payload, timeout=30)
        res = r.json()
        
        reply = res['candidates'][0]['content']['parts'][0]['text']
        return reply.strip()
    except Exception as e:
        logger.error(f"Gemini Engine Error: {e}")
        return "ধন্যবাদ! আমাদের টিম আপনার মেসেজটি পেয়েছে এবং আমরা শীঘ্রই রিপ্লাই দিচ্ছি।"
