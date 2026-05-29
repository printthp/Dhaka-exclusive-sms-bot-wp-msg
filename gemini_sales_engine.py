import os
import requests
import logging
import json

# Setup logging
logger = logging.getLogger(__name__)

# Fetch API Key from environment
GEMINI_API_KEY = os.environ.get("GEMINI_KEY", "")

def get_optimized_gemini_reply(user_message, phone, db_query_func):
    """
    Sales Intelligence Engine for Dhaka Exclusive.
    """
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_KEY missing in Environment Variables")
        return "Dhaka Exclusive এ আপনাকে স্বাগতম! আমরা শীঘ্রই আপনার সাথে যোগাযোগ করছি।"

    # 1. Fetch Customer Context
    history_ctx = "New Customer"
    try:
        history = db_query_func("SELECT id, total, status FROM orders WHERE phone=? ORDER BY id DESC LIMIT 2", (phone,), fetchall=True) or []
        if history:
            history_ctx = f"Customer's recent history: {json.dumps(history)}"
    except Exception as e:
        logger.error(f"Error fetching customer history: {e}")

    # 2. Fetch Product Context
    product_list = "Catalog is updating. Ask customer what they need."
    try:
        products = db_query_func("SELECT name, price FROM products LIMIT 10", fetchall=True) or []
        if products:
            product_list = "\n".join([f"- {p['name']}: {p['price']}৳" for p in products])
    except Exception as e:
        logger.error(f"Error fetching products: {e}")

    # 3. System Instruction
    system_instruction = f"""
    তুমি 'Dhaka Exclusive'-এর সেলস এক্সপার্ট। 
    - ভাষা: বাংলা (ভাই/আপু সম্বোধন)।
    - ডেলিভারি: ঢাকা ২৪ ঘণ্টা, বাইরে ৪৮-৭২ ঘণ্টা। 
    - পেমেন্ট: ক্যাশ অন ডেলিভারি।
    - লক্ষ্য: কাস্টমারকে অর্ডার কনফার্ম করতে উৎসাহিত করো।
    
    ডাটা:
    প্রোডাক্টস: {product_list}
    কাস্টমার ডাটা: {history_ctx}
    """

    # 4. API Call with Backup Model Logic
    # এখানে v1/gemini-pro ব্যবহার করছি যা গুগল সিগনেচারের জন্য সবচেয়ে স্টেবল
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"
    
    payload = {
        "contents": [{
            "parts": [{"text": f"{system_instruction}\n\nCustomer: {user_message}"}]
        }]
    }
    
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=20)
        res_data = response.json()

        # যদি gemini-pro কাজ না করে তবে gemini-1.5-flash ট্রাই করবে
        if 'error' in res_data:
            logger.info("Retrying with gemini-1.5-flash...")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
            response = requests.post(url, json=payload, headers=headers, timeout=20)
            res_data = response.json()

        if 'candidates' in res_data and len(res_data['candidates']) > 0:
            return res_data['candidates'][0]['content']['parts'][0]['text'].strip()
        
        logger.error(f"Gemini API Error: {res_data}")
        return "ধন্যবাদ! আমরা আপনার মেসেজটি পেয়েছি এবং শীঘ্রই রিপ্লাই দিচ্ছি।"

    except Exception as e:
        logger.error(f"Critical Engine Error: {e}")
        return "আমাদের সার্ভারে কাজ চলছে, আমরা দ্রুত আপনার সাথে যোগাযোগ করছি।"
