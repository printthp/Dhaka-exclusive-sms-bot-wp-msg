import os
import io
import requests
import xml.etree.ElementTree as ET
from flask import Flask, request
from PIL import Image
from google import genai
from google.genai import types

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
        # শুধু 'দাম', 'koto', 'dam' এই জাতীয় সাধারণ শব্দ থাকলে ক্যাটালগ সার্চ স্কিপ করা হবে
        ignored_words = {"dam", "koto", "price", "কত", "দাম", "বলেন", "বলো", "blo", "bolen", "tmi"}
        clean_query = " ".join([w for w in user_query.lower().split() if w not in ignored_words]).strip()
        
        if not clean_query or len(clean_query) < 2:
            return "GENERAL_INQUIRY" # সাধারণ প্রশ্ন, কোনো নির্দিষ্ট প্রোডাক্টের নাম নেই

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/xml, text/xml, */*"
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
                if count >= 5:
                    break
        return matched_products
    except Exception as e:
        print(f"Catalog Filter Error: {e}")
        return ""

# --- মূল জেমিনি এআই প্রসেসর ---
def get_ai_answer(from_number, user_query, image_bytes=None):
    try:
        if len(user_chat_sessions) > 1000:
            user_chat_sessions.pop(next(iter(user_chat_sessions)))
            
        catalog_context = ""
        if user_query:
            catalog_context = search_product_in_catalog(user_query)
            
        # ক্যাটালগের কনটেক্সট রুলস হ্যান্ডলিং
        if catalog_context == "GENERAL_INQUIRY":
            catalog_info = "Customer is asking a general question or just saying hello/asking price without specifying a clear product name. Ask them politely which product they are looking for."
        elif catalog_context:
            catalog_info = f"Matched Products in Catalog:\n{catalog_context}"
        else:
            catalog_info = "The specific product requested is not found in the catalog."

        system_instruction = (
            "You are the professional AI sales assistant for 'Dhaka Exclusive' (https://dhakaexclusive.org/).\n"
            f"Context: {catalog_info}\n\n"
            "STRICT RULES:\n"
            "1. ALWAYS address the customer as 'প্রিয় গ্রাহক'. NEVER use 'নমস্কার' or 'হ্যালো'.\n"
            "2. Keep replies short, polite, and completely in Bengali.\n"
            "3. If the customer wants to buy/order (অর্ডার করতে চাই), politely ask for their: 1. Full Name, 2. Phone Number, 3. Full Delivery Address.\n"
            "4. Delivery Charge Rules: Inside Dhaka = 80 TK, Outside Dhaka = 130 TK. When they provide the address, calculate the total bill (Product Price + Delivery Charge) and show them the summary to confirm.\n"
            "5. If they provide Name, Phone, and Address, summarize the order and say 'আপনার অর্ডারটি আমরা নোট করে নিয়েছি। আমাদের প্রতিনিধি কল করে কনফার্ম করবেন।'\n"
            "6. If you have already confirmed the order in the chat history, do NOT repeat the confirmation. Respond naturally and politely to their acknowledgment.\n"
            "7. CRITICAL: If the customer clearly specified a product name or sent a product image, but you cannot find it or its price anywhere in the catalog, strictly say this exact sentence and nothing else:\n"
            "'প্রিয় গ্রাহক, এটি আমাদের একটি প্রিমিয়াম প্রোডাক্ট। এটির সঠিক লাইভ দাম ও সাইজটি নিশ্চিত করতে আমাদের একজন প্রতিনিধি খুব দ্রুত আপনাকে ইনবক্সে মেসেজ দিচ্ছেন।'\n"
            "8. If the customer's message is generic (e.g., 'tmi blo dam', 'hi', 'dam koto' without a product name), do NOT use the premium product sentence. Instead, politely ask them to provide the product name or photo."
        )

        ai_config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            temperature=0.3,       
            max_output_tokens=350  
        )

        if from_number not in user_chat_sessions:
            user_chat_sessions[from_number] = client.chats.create(model=MODEL_NAME, config=ai_config)
            
        chat_session = user_chat_sessions[from_number]

        message_parts = []
        if image_bytes:
            img = Image.open(io.BytesIO(image_bytes))
            img.thumbnail((800, 800))
            message_parts.append(img)
            
        message_parts.append(user_query or "এটার দাম কত?")

        response = chat_session.send_message(message_parts)
        return response.text

    except Exception as e:
        print(f"Gemini Error: {e}")
        if from_number in user_chat_sessions:
            del user_chat_sessions[from_number]
        return "প্রিয় গ্রাহক, কারিগরি সমস্যার কারণে আমি এই মুহূর্তে মেসেজটি বুঝতে পারছি না। আমাদের প্রতিনিধি খুব দ্রুত আপনার সাথে যোগাযোগ করছেন।"

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
                
    except Exception as e:
        print(f"WEBHOOK ERROR: {e}")
        
    return "ok", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
