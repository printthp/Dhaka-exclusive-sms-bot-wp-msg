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
    Stabilized Sales AI for Dhaka Exclusive using Gemini Pro.
    """
    if not GEMINI_API_KEY:
        return "সালাম ভাই! Dhaka Exclusive এ আপনাকে স্বাগতম।"

    # 1. context creation (Pro-active mode)
    product_list = "Premium Lifestyle products including Panjabi, Gadgets, and more."
    try:
        products = db_query_func("SELECT name, price FROM products LIMIT 5", fetchall=True) or []
        if products:
            product_list = ", ".join([f"{p['name']} ({p['price']}TK)" for p in products])
    except: pass

    # 2. Strong System Prompt
    system_prompt = f"""
    You are the Sales Head of 'Dhaka Exclusive'. 
    - Always reply in BANGLA.
    - Be polite (Use Bhai/Apu).
    - We offer Cash on Delivery. Delivery: Dhaka 24h, Outside 48-72h.
    - Products: {product_list}
    Current Customer Phone: {phone}
    Goal: Make the customer order something. Give short, punchy sales replies.
    """

    # 3. 🛡️ STABLE API CALL (Using v1beta models/gemini-pro)
    # v1beta তে gemini-pro সবচেয়ে বেশি স্টেবল
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"
    
    payload = {
        "contents": [{
            "parts": [{
                "text": f"{system_prompt}\n\nCustomer says: {user_message}"
            }]
        }],
        "generationConfig": {
            "temperature": 0.5,
            "maxOutputTokens": 400
        }
    }
    
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=20)
        res_data = response.json()

        # candidate logic
        if 'candidates' in res_data and len(res_data['candidates']) > 0:
            return res_data['candidates'][0]['content']['parts'][0]['text'].strip()
        
        # ⚠️ BACKUP: If gemini-pro fails, try flash with basic naming
        logger.info("Gemini Pro failed, attempting flash fallback...")
        url_flash = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        response = requests.post(url_flash, json=payload, headers=headers, timeout=20)
        res_data = response.json()
        
        if 'candidates' in res_data:
            return res_data['candidates'][0]['content']['parts'][0]['text'].strip()

        logger.error(f"Full Model Error: {res_data}")
        return "ধন্যবাদ! আমরা আপনার মেসেজটি পেয়েছি এবং আমাদের সেলস টিম আপনাকে খুব দ্রুত কল দিবে।"

    except Exception as e:
        logger.error(f"Critical Engine Failure: {e}")
        return "সালাম ভাই! আমাদের সার্ভারে আপডেট চলছে। খুব দ্রুত আপনাকে আমাদের ম্যানেজার কল দিবে।"
