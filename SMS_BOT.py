import os
import io
import requests
import xml.etree.ElementTree as ET
from flask import Flask, request
from PIL import Image
from google import genai
from google.genai import types

app = Flask(__name__)

# ডুপ্লিকেট মেসেজ আটকানোর জন্য গ্লোবাল মেমোরি ট্র্যাকিং
global_processed_messages = {}

# --- কনফিগারেশন ---
PERMANENT_TOKEN = "EAANtSb24BiwBRREXu8HztnpOLtamcKIvi09Qb24LiYax45S4aoYtFEVKEQZAxigfO2wbGf6RgHh51IURbQzKKrzPhkcprLxHpZBfOwxZAVCscdVOpjbapbS9sOLCIqZBM8tZAtSRRaVVYSTZBjUkkPZAQaLABSnG6cQcgQcwqZBC5I5yrB4cXgoUPDlzzn7HzUwsMAZDZD"
PHONE_NUMBER_ID = "1039959469208417"
GEMINI_KEY = "AIzaSyDICBRwj4wdwmqlut_Xjf0GgvXx_Mjcc0Q"
VERIFY_TOKEN = "dhakaex0020"

# --- Gemini AI Setup ---
# নতুন SDK অনুযায়ী Client ডিফাইন করা হলো
client = genai.Client(api_key=GEMINI_KEY)
MODEL_NAME = "gemini-2.5-flash"  # কমেন্ট আনলক করা হলো

CATALOG_URL = "https://www.dhakaexclusive.org/facebook-catalog.xml"

# গুগল লাইভ সার্চツール চালু
search_tool = types.Tool(google_search=types.GoogleSearch())
ai_config = types.GenerateContentConfig(
    tools=[search_tool],
    temperature=0.1,       # নির্ভুল তথ্যের জন্য
    max_output_tokens=250  # রেসপন্স সংক্ষিপ্ত রাখার জন্য
)

# --- হোয়াটসঅ্যাপ থেকে ছবি ডাউনলোড করার ফাংশন ---
def download_whatsapp_media(media_id):
    try:
        url = f"https://graph.facebook.com/v21.0/{media_id}"
        headers = {"Authorization": f"Bearer {PERMANENT_TOKEN}"}
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            media_url = res.json().get("url")
            img_res = requests.get(media_url, headers=headers)
            if img_res.status_code == 200:
                return img_res.content
    except Exception as e:
        print(f"Media Download Error: {e}")
    return None

# --- ক্যাটালগ সার্চ ফাংশন ---
def search_product_in_catalog(user_query):
    try:
        res = requests.get(CATALOG_URL, timeout=10)
        if res.status_code != 200:
            return ""
            
        root = ET.fromstring(res.content)
        matched_products = ""
        count = 0
        
        query_words = user_query.lower().split() if user_query else []
        
        for item in root.findall('.//item'):
            title = item.find('title')
            price = item.find('price')
            
            if title is not None and price is not None:
                title_text = title.text.strip()
                price_text = price.text.strip()
                
                if not query_words or any(word in title_text.lower() for word in query_words):
                    matched_products += f"- Product: {title_text}, Price: {price_text}\n"
                    count += 1
                
                if count >= 5:
                    break
                    
        return matched_products
    except Exception as e:
        print(f"Catalog Filter Error: {e}")
        return ""

# --- মূল জেমিনি এআই প্রসেসর ---
def get_ai_answer(user_query, image_bytes=None):
    try:
        catalog_context = ""
        if user_query:
            catalog_context = search_product_in_catalog(user_query)
            
        if catalog_context:
            catalog_info = f"Matched Products from Our System:\n{catalog_context}"
        else:
            catalog_info = "No direct text match in catalog. Use Google Search if needed."

        context = (
            "You are the professional AI sales assistant for 'Dhaka Exclusive' (https://dhakaexclusive.org/).\n"
            f"{catalog_info}\n"
            "STRICT RULES:\n"
            "1. ALWAYS address the customer as 'প্রিয় গ্রাহক'. NEVER use 'নমস্কার' or 'হ্যালো'.\n"
            "2. KEEP REPLIES EXTREMELY SHORT (Max 2-3 lines). Do not write long paragraphs.\n"
            "3. Answer politely in Bengali.\n"
            "4. If 'Matched Products from Our System' has the product details, use that price directly. "
            "If it's an image or not found in system, use Google Search strictly on site:dhakaexclusive.org to check.\n"
            "5. CRITICAL: If you cannot find the product or its price anywhere, strictly say this exact sentence and nothing else:\n"
            "'প্রিয় গ্রাহক, এটি আমাদের একটি প্রিমিয়াম প্রোডাক্ট। এটির সঠিক লাইভ দাম ও সাইজটি নিশ্চিত করতে আমাদের একজন প্রতিনিধি খুব দ্রুত আপনাকে ইনবক্সে মেসেজ দিচ্ছেন।'"
        )
        
        if image_bytes:
            # নতুন SDK-এর জন্য ইমেজ পার্ট তৈরি
            image_part = types.Part.from_bytes(
                data=image_bytes,
                mime_type="image/jpeg",
            )
            prompt_parts = [context, image_part, f"Customer Question: {user_query or 'এটার দাম কত?'}\n"]
            response = client.models.generate_content(model=MODEL_NAME, contents=prompt_parts, config=ai_config)
        else:
            prompt_text = f"{context}\nCustomer Question: {user_query}"
            response = client.models.generate_content(model=MODEL_NAME, contents=prompt_text, config=ai_config)
            
        return response.text

    except Exception as e:
        print(f"Gemini Error: {e}")
        return "প্রিয় গ্রাহক, কারিগরি সমস্যার কারণে আমি এই মুহূর্তে মেসেজটি বুঝতে পারছি না। আমাদের প্রতিনিধি খুব দ্রুত আপনার সাথে যোগাযোগ করছেন।"

# --- হোয়াটসঅ্যাপে মেসেজ পাঠানোর ফাংশন ---
def send_message(recipient_number, message_body):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {PERMANENT_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient_number,
        "type": "text",
        "text": {"body": message_body}
    }
    requests.post(url, json=payload, headers=headers)

# --- মেটা ভেরিফিকেশন (Webhook GET) ---
@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge"), 200
    return "Failed", 403

# --- মূল হোয়াটসঅ্যাপ রিসিভার (Webhook POST) ---
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    try:
        if "messages" in data["entry"][0]["changes"][0]["value"]:
            value = data["entry"][0]["changes"][0]["value"]
            msg = value["messages"][0]
            msg_id = msg["id"]
            from_number = msg["from"]
            
            # মেটা-র ডুপ্লিকেট রিকার্সিভ রিকোয়েস্ট ড্রপ করা
            if msg_id in global_processed_messages:
                print(f"Duplicate Message Ignored: {msg_id}")
                return "ok", 200
                
            global_processed_messages[msg_id] = True
            
            if len(global_processed_messages) > 1000:
                first_key = next(iter(global_processed_messages))
                global_processed_messages.pop(first_key)

            # কাস্টমার টেক্সট পাঠালে
            if msg.get("type") == "text":
                user_text = msg["text"]["body"].strip()
                ai_response = get_ai_answer(user_text)
                send_message(from_number, ai_response)
                
            # কাস্টমার ছবি পাঠালে
            elif msg.get("type") == "image":
                media_id = msg["image"]["id"]
                caption = msg["image"].get("caption", "").strip()
                
                image_bytes = download_whatsapp_media(media_id)
                if image_bytes:
                    ai_response = get_ai_answer(user_query=caption, image_bytes=image_bytes)
                else:
                    ai_response = "প্রিয় গ্রাহক, আমি আপনার পাঠানো ছবিটি সঠিকভাবে দেখতে পাচ্ছি না। দয়া করে আবার চেষ্টা করুন।"
                    
                send_message(from_number, ai_response)
            else:
                send_message(from_number, "দুঃখিত প্রিয় গ্রাহক, আমি বর্তমানে শুধু টেক্সট এবং ছবি বুঝতে পারি।")
                
    except Exception as e:
        print(f"WEBHOOK ERROR: {e}")
        
    return "ok", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
