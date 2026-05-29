import os
import requests
import logging
import json

# Setup logging
logger = logging.getLogger(__name__)

# Fetch API Key from environment
GEMINI_API_KEY = os.environ.get("GEMINI_KEY", "")

def get_optimized_gemini_reply(user_message, phone, db_query_func):
    """উন্নত সেলস ইন্টেলিজেন্ট ইঞ্জিন যা প্রোডাক্ট না থাকলেও কাজ করবে"""
    
    if not GEMINI_API_KEY:
        return "Dhaka Exclusive এ আপনাকে স্বাগতম! আমরা শীঘ্রই আপনার সাথে যোগাযোগ করবো।"

    # ১. কাস্টমার হিস্ট্রি সংগ্রহ (যদি থাকে)
    history_ctx = "New Customer"
    try:
        history = db_query_func("SELECT * FROM orders WHERE phone=? ORDER BY id DESC LIMIT 2", (phone,), fetchall=True) or []
        if history:
            history_ctx = f"Customer Past Orders: {json.dumps(history)}"
    except Exception as e:
        logger.error(f"Context fetch error: {e}")

    # ২. প্রোডাক্ট ক্যাটালগ কালেকশন
    # যদি ডাটাবেসে প্রোডাক্ট না থাকে, তবে একটি ডিফল্ট মেসেজ সেট হবে
    product_list = "বর্তমানে আমাদের স্টক আপডেট করা হচ্ছে। কাস্টমারকে আমাদের হট আইটেমগুলোর কথা বলো।"
    try:
        products = db_query_func("SELECT name, price FROM products LIMIT 10", fetchall=True) or []
        if products:
            product_list = "\n".join([f"- {p['name']}: {p['price']}৳" for p in products])
    except Exception as e:
        logger.error(f"Product fetch error: {e}")

    # ৩. শক্তিশালী সিস্টেম ইনস্ট্রাকশন
    system_instruction = f"""
    তুমি 'Dhaka Exclusive'-এর প্রিমিয়াম সেলস ম্যানেজার। তোমার কথা বলার স্টাইল হবে অত্যন্ত অমায়িক এবং পেশাদার।
    
    নিয়মাবলী:
    - ভাষা: শুদ্ধ বাংলায় কথা বলো। গ্রাহককে 'ভাই/আপু' বলে সম্মান দাও।
    - যদি প্রোডাক্ট লিস্ট না থাকে: তাহলে বলো "আমাদের কাছে অনেক কালেকশন আছে, আপনি কি ধরণের প্রোডাক্ট খুঁজছেন?"
    - অফার: প্রতিটি মেসেজের শেষে অফার বা কোনো সাহায্য লাগবে কি না জিজ্ঞেস করো।
    - ডেলিভারি: ঢাকা সিটিতে ২৪ ঘণ্টা, বাইরে ৪৮-৭২ ঘণ্টা। পেমেন্ট: ক্যাশ অন ডেলিভারি (COD)।
    - হিউম্যান টাচ: যদি কাস্টমার এমন কিছু জিজ্ঞেস করে যা তুমি জানো না, তবে বিনয়ের সাথে বলো "ভাই/আপু, আমি চেক করে জানাচ্ছি, আমাদের একজন এক্সিকিউটিভ এক্ষুণি আপনাকে কল দিবে।"
    
    ব্যাকগ্রাউন্ড তথ্য:
    প্রোডাক্ট ক্যাটালগ:
    {product_list}
    
    গ্রাহকের তথ্য:
    {history_ctx}
    """

    try:
        # মডেল: gemini-1.5-flash-latest (দ্রুত এবং আধুনিক)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"
        
        payload = {
            "contents": [{
                "parts": [{"text": f"{system_instruction}\n\nCustomer Message: {user_message}"}]
            }],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 400
            }
        }
        
        headers = {"Content-Type": "application/json"}
        r = requests.post(url, json=payload, headers=headers, timeout=20)
        res = r.json()
        
        if 'candidates' in res and len(res['candidates']) > 0:
            reply = res['candidates'][0]['content']['parts'][0]['text']
            return reply.strip()
        
        logger.error(f"Gemini API fail: {res}")
        return "ধন্যবাদ! আপনার মেসেজটি আমরা পেয়েছি। একজন এজেন্ট শীঘ্রই আপনার সাথে কথা বলবে।"

    except Exception as e:
        logger.error(f"Engine critical error: {e}")
        return " আমাদের সার্ভারে কাজ চলছে, কিছুক্ষণ পর আবার মেসেজ দিন অথবা সরাসরি কল করুন।"
