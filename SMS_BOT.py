import os
import io
import requests
import xml.etree.ElementTree as ET
from flask import Flask, request
from PIL import Image
from google import genai
from google.genai import types
from threading import Thread

app = Flask(__name__)

# ডুপ্লিকেট মেসেজ এবং কাস্টমারের চ্যাট সেশন ট্র্যাকিং
global_processed_messages = {}
user_chat_sessions = {}  

# --- কনফিগারেশন ---
PERMANENT_TOKEN = "EAANtSb24BiwBRREXu8HztnpOLtamcKIvi09Qb24LiYax45S4aoYtFEVKEQZAxigfO2wbGf6RgHh51IURbQzKKrzPhkcprLxHpZBfOwxZAVCscdVOpjbapbS9sOLCIqZBM8tZAtSRRaVVYSTZBjUkkPZAQaLABSnG6cQcgQcwqZBC5I5yrB4cXgoUPDlzzn7HzUwsMAZDZD"
PHONE_NUMBER_ID = "1039959469208417"
GEMINI_KEY = "AIzaSyDICBRwj4wdwmqlut_Xjf0GgvXx_Mjcc0Q"
VERIFY_TOKEN = "dhakaex0020"

CATALOG_URL = "https://www.dhakaexclusive.org/facebook-catalog.xml"

# --- জেমিনি ক্লায়েন্ট সেটআপ ---
client = genai.Client(api_key=GEMINI_KEY)
MODEL_NAME = "gemini-2.5-flash"

# --- হোয়াটসঅ্যাপ থেকে ছবি ডাউনলোড ---
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
        if not user_query:
            return ""
        ignored_words = {"dam", "koto", "price", "কত", "দাম", "বলেন", "বলো", "blo", "bolen", "tmi"}
        clean_query = " ".join([w for w in user_query.lower().split() if w not in ignored_words]).strip()
        
        if not clean_query or len(clean_query) < 2:
            return ""

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        res = requests.get(CATALOG_URL, headers=headers, timeout=12)
        if res.status_code != 200:
            return ""
            
        root = ET.fromstring(res.content)
        matched_products = ""
        count = 0
        query_words = clean_query.split()
        
        for item in root.findall('.//item'):
            title = item.find('title')
            price = item.find('price')
            if title is not None and price is not None:
                title_text = title.text.strip()
                price_text = price.text.strip()
                
                if any(word in title_text.lower() for word in query_words):
                    matched_products += f"- Product: {title_text}, Price: {price_text}\n"
                    count += 1
                if count >= 3:
                    break
        return matched_products
    except Exception as e:
        print(f"Catalog Filter Error: {e}")
        return ""

# --- মূল জেমিনি এআই প্রসেসর (Pydantic Error Fixed) ---
def get_ai_answer(from_number, user_query, image_bytes=None):
    try:
        if len(user_chat_sessions) > 1000:
            user_chat_sessions.pop(next(iter(user_chat_sessions)))
            
        catalog_context = search_product_in_catalog(user_query) if user_query else ""
        catalog_info = f"Matched Products in Catalog:\n{catalog_context}" if catalog_context else "No direct match in xml catalog file."

        system_instruction = (
            "You are the professional AI sales assistant for 'Dhaka Exclusive' (https://dhakaexclusive.org/).\n"
            f"XML Catalog Context:\n{catalog_info}\n\n"
            "CRITICAL INSTRUCTION FOR SEARCHING PRODUCTS:\n"
            "1. If the customer sends an image or asks for a product, use the Google Search tool to search strictly on site:dhakaexclusive.org to find live price and availability.\n"
            "2. If you find the product price, tell it directly to the customer in a polite manner.\n"
            "3. Do NOT show or output your internal thinking, search queries, or text like 'I am searching' or 'প্রিয় গ্রাহক, ঢাকা এক্সক্লুসিভের পণ্য তালিকা দেখতে...' to the user. Only reply with the final direct answer.\n\n"
            "STRICT BUSINESS RULES:\n"
            "1. ALWAYS address the customer as 'প্রিয় গ্রাহক'. NEVER use 'নমস্কার' or 'হ্যালো'.\n"
            "2. Keep replies short, polite, and completely in Bengali.\n"
            "3. If the customer wants to buy/order, ask for: 1. Full Name, 2. Phone Number, 3. Full Delivery Address.\n"
            "4. Delivery Charge: Inside Dhaka = 80 TK, Outside Dhaka = 130 TK.\n"
            "5. If they provide full delivery details, say: 'আপনার অর্ডারটি আমরা নোট করে নিয়েছি। আমাদের প্রতিনিধি কল করে কনফার্ম করবেন।'\n"
            "6. If you have already confirmed the order or sent a message in this turn, do NOT repeat the same line multiple times.\n"
            "7. IF AND ONLY IF the product or its price cannot be found in the catalog AND Google Search fails, strictly say this exact sentence and nothing else:\n"
            "'প্রিয় গ্রাহক, এটি আমাদের একটি প্রিমিয়াম প্রোডাক্ট। এটির সঠিক লাইভ দাম ও সাইজটি নিশ্চিত করতে আমাদের একজন প্রতিনিধি খুব দ্রুত আপনাকে ইনবক্সে মেসেজ দিচ্ছেন।'"
        )

        ai_config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=[types.Tool(google_search=types.GoogleSearch())],
            thinking_config=types.ThinkingConfig(thinking_budget=0), # ইন্টারনাল প্রসেসিং টেক্সট কাস্টমার চ্যাটে দেখানো বন্ধ করবে
            temperature=0.2,       
            max_output_tokens=350  
        )

        if from_number not in user_chat_sessions:
            user_chat_sessions[from_number] = client.chats.create(model=MODEL_NAME, config=ai_config)
            
        chat_session = user_chat_sessions[from_number]

        # নতুন SDK স্ট্রাকচার অনুযায়ী সঠিক Content Format সাজানো (Validation Error Fix)
        message_contents = []
        
        if image_bytes:
            img = Image.open(io.BytesIO(image_bytes))
            img.thumbnail((800, 800))
            message_contents.append(img)
            
        if user_query:
            message_contents.append(user_query)
        else:
            message_contents.append("এটার দাম কত?")

        # চ্যাট সেশনে সঠিক ফরম্যাটে ডেটা পাঠানো
        response = chat_session.send_message(message_contents)
        return response.text

    except Exception as e:
        print(f"Gemini Error: {e}")
        if from_number in user_chat_sessions:
            del user_chat_sessions[from_number]
        return "প্রিয় গ্রাহক, আন্তরিকভাবে দুঃখিত। কারিগরি আপডেটের কারণে আমার সিস্টেমে সামান্য সমস্যা হচ্ছে। আমাদের প্রতিনিধি খুব দ্রুত আপনাকে মেসেজ দিচ্ছেন।"

# --- হোয়াটসঅ্যাপে মেসেজ পাঠানো ---
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
    try:
        requests.post(url, json=payload, headers=headers, timeout=10)
    except Exception as e:
        print(f"Send Message Error: {e}")

# --- ব্যাকগ্রাউন্ড প্রসেসিং টাস্ক ---
def process_async_webhook(msg, from_number):
    if msg.get("type") == "text":
        user_text = msg["text"]["body"].strip()
        ai_response = get_ai_answer(from_number, user_text)
        send_message(from_number, ai_response)
        
    elif msg.get("type") == "image":
        media_id = msg["image"]["id"]
        caption = msg["image"].get("caption", "").strip()
        
        image_bytes = download_whatsapp_media(media_id)
        if image_bytes:
            ai_response = get_ai_answer(from_number, user_query=caption, image_bytes=image_bytes)
        else:
            ai_response = "প্রিয় গ্রাহক, আমি আপনার পাঠানো ছবিটি সঠিকভাবে দেখতে পাচ্ছি না। দয়া করে আবার চেষ্টা করুন।"
        send_message(from_number, ai_response)
    else:
        send_message(from_number, "দুঃখিত প্রিয় গ্রাহক, আমি বর্তমানে শুধু টেক্সট এবং ছবি বুঝতে পারি।")

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
            
            if msg_id in global_processed_messages:
                return "ok", 200
                
            global_processed_messages[msg_id] = True
            if len(global_processed_messages) > 1000:
                global_processed_messages.pop(next(iter(global_processed_messages)))

            thread = Thread(target=process_async_webhook, args=(msg, from_number))
            thread.start()
                
    except Exception as e:
        print(f"WEBHOOK ERROR: {e}")
        
    return "ok", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
