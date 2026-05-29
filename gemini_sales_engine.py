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
    Handles responses even if the product database is empty.
    """
    
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_KEY missing in Environment Variables")
        return "Dhaka Exclusive এ আপনাকে স্বাগতম! আমরা শীঘ্রই আপনার সাথে যোগাযোগ করছি।"

    # 1. Fetch Customer Context (Order History)
    history_ctx = "New Customer (No previous orders logged)"
    try:
        # Checking for the last 2 orders to give context to AI
        history = db_query_func("SELECT id, total, status, created_at FROM orders WHERE phone=? ORDER BY id DESC LIMIT 2", (phone,), fetchall=True) or []
        if history:
            history_ctx = f"Customer's recent order history: {json.dumps(history)}"
    except Exception as e:
        logger.error(f"Error fetching customer history: {e}")

    # 2. Fetch Product List Context
    product_list = "Currently, our digital catalog is being updated. Ask the customer what type of products they are looking for."
    try:
        # Dynamic product fetch
        products = db_query_func("SELECT name, price FROM products LIMIT 15", fetchall=True) or []
        if products:
            product_list = "\n".join([f"- {p['name']}: {p['price']}৳" for p in products])
    except Exception as e:
        logger.error(f"Error fetching products: {e}")

    # 3. System Persona & Instruction
    system_instruction = f"""
    তুমি 'Dhaka Exclusive'-এর প্রিমিয়াম সেলস বিশেষজ্ঞ। তোমার কাজ হলো অত্যন্ত বিনয়ের সাথে কাস্টমারের প্রশ্নের উত্তর দেওয়া এবং অর্ডার কনফার্ম করা।
    
    নির্দেশনা:
    - সম্বোধন: গ্রাহককে 'ভাই' বা 'আপু' বলে ডাকবে।
    - ভাষা: শুদ্ধ এবং মার্জিত বাংলা ব্যবহার করবে।
    - অর্ডার প্রসেস: কাস্টমার কিছু কিনতে চাইলে তার নাম, ফোন নম্বর এবং পূর্ণ ঠিকানা চাইবে।
    - ডেলিভারি তথ্য: ঢাকা সিটি ২৪ ঘণ্টা, ঢাকার বাইরে ৪৮-৭২ ঘণ্টা। ক্যাশ অন ডেলিভারি (COD) অ্যাভেইলেবল।
    - কাস্টমার এক্সপেরিয়েন্স: প্রতিটি মেসেজের শেষে একটি প্রশ্ন করবে (যেমন: "ভাই, আর কোনো সাহায্য করতে পারি?" বা "অর্ডারটি কি কনফার্ম করবো?")।
    
    বিজনেসের বর্তমান তথ্য:
    উপলব্ধ প্রোডাক্ট তালিকা:
    {product_list}
    
    গ্রাহকের গত অর্ডারের তথ্য:
    {history_ctx}
    """

    try:
        # API Endpoint (Using Stable V1 version for gemini-1.5-flash)
        url = f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        
        payload = {
            "contents": [{
                "parts": [{"text": f"{system_instruction}\n\nCustomer Message: {user_message}"}]
            }],
            "generationConfig": {
                "temperature": 0.8,
                "maxOutputTokens": 500,
                "topP": 0.9
            }
        }
        
        headers = {"Content-Type": "application/json"}
        response = requests.post(url, json=payload, headers=headers, timeout=25)
        res_data = response.json()
        
        if 'candidates' in res_data and len(res_data['candidates']) > 0:
            ai_reply = res_data['candidates'][0]['content']['parts'][0]['text']
            return ai_reply.strip()
        
        # Log failure reason for diagnosis
        logger.error(f"Gemini API Error Response: {res_data}")
        return "ধন্যবাদ! আমরা আপনার মেসেজটি পেয়েছি। একজন এজেন্ট শীঘ্রই আপনার সাথে কথা বলবে।"

    except Exception as e:
        logger.critical(f"Critical Error in Sales Engine: {e}")
        return "দুঃখিত ভাই/আপু, আমাদের সার্ভারে কিছুটা টেকনিক্যাল সমস্যা হচ্ছে। আপনার মেসেজটি সেভ করা হয়েছে, আমরা দ্রুত রিপ্লাই দিচ্ছি।"
