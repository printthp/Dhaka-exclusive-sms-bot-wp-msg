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

# ডুপ্লিকেট মেসেজ আইডি ট্র্যাকিং (যাতে মেটার ডাবল রিকোয়েস্ট কোড রিজেক্ট করতে পারে)
global_processed_messages = {}
# কাস্টমারের চ্যাট সেশন মেমোরি (এটি কাস্টমারের সব কথা মনে রাখবে)
user_chat_sessions = {}  

# --- কনফিগারেশন ---
PERMANENT_TOKEN = "EAANtSb24BiwBRREXu8HztnpOLtamcKIvi09Qb24LiYax45S4aoYtFEVKEQZAxigfO2wbGf6RgHh51IURbQzKKrzPhkcprLxHpZBfOwxZAVCscdVOpjbapbS9sOLCIqZBM8tZAtSRRaVVYSTZBjUkkPZAQaLABSnG6cQcgQcwqZBC5I5yrB4cXgoUPDlzzn7HzUwsMAZDZD"
PERMANENT_TOKEN = "EAANr3r8XPT8BRSkeIvxAIJYVRqjTKpOdZAeXtXIn97kgaGcpmSj8JEcGGH8ZAR12Yyimp7RQcMZCdzZBILrNXqSCL4QXF8do1J2oGp0JESS15sxq637ZAVZAwR5WKP3RuEUqhm43EfCqRtAWJcgcBZBiXvgI4bLn06uircRob3dNxqfrk0ocuC2GX4Svmox2CtNtQZDZD"
CATALOG_ID = "3224452064423784"
PHONE_NUMBER_ID = "1039959469208417"
GEMINI_KEY = "AIzaSyDICBRwj4wdwmqlut_Xjf0GgvXx_Mjcc0Q"
VERIFY_TOKEN = "dhakaex0020"

CATALOG_URL = "https://www.dhakaexclusive.org/facebook-catalog.xml"

# --- জেমিনি ক্লায়েন্ট সেটআপ ---
client = genai.Client(api_key=GEMINI_KEY)
MODEL_NAME = "gemini-2.5-flash"



# =====================================================================
# ২. ক্যাটালগ ডাটা প্রসেসিং ফাংশনসমূহ (মেটা স্ট্যান্ডার্ড ফিক্সড)
# =====================================================================
def get_full_catalog_context():
    """লাইভ XML লিংক থেকে ফেসবুকের স্ট্যান্ডার্ড g:title ও g:price ডেটা রিড করবে"""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res = requests.get(CATALOG_URL, headers=headers, timeout=15)
        if res.status_code != 200:
            print("❌ ক্যাটালগ সার্ভার রেসপন্স দেয়নি।")
            return "Catalog currently unavailable."
            
        root = ET.fromstring(res.content)
        context_str = ""
        count = 0
        
        for item in root.findall('.//item'):
            title_node = item.find('g:title', NAMESPACES) or item.find('title')
            price_node = item.find('g:price', NAMESPACES) or item.find('price')
            
            if title_node is not None and price_node is not None:
                title_text = title_node.text.strip()
                price_text = price_node.text.strip()
                
                context_str += f"- Product: {title_text}, Price: {price_text}\n"
                count += 1
                # জেমিনির প্রম্পট লিমিট ঠিক রাখতে প্রথম ৪০০টি প্রোডাক্ট পাঠানো হচ্ছে
                if count >= 400:  
                    break
        return context_str
    except Exception as e:
        print(f"Catalog Load Error: {e}")
        return "Catalog error."


def search_product_in_catalog(user_query):
    """টেক্সট মেসেজের জন্য ক্যাটালগ থেকে মিল থাকা প্রোডাক্ট ফিল্টার করবে"""
    try:
        if not user_query:
            return ""
        ignored_words = {"dam", "koto", "price", "কত", "দাম", "বলেন", "বলো", "blo", "bolen", "tmi", "buka", "okay", "thik", "ace", "এটার"}
        clean_query = " ".join([w for w in user_query.lower().split() if w not in ignored_words]).strip()
        
        if not clean_query or len(clean_query) < 2:
            return ""

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        res = requests.get(CATALOG_URL, headers=headers, timeout=12)
        if res.status_code != 200:
            return ""
            
        root = ET.fromstring(res.content)
        matched_products = ""
        count = 0
        query_words = clean_query.split()
        
        for item in root.findall('.//item'):
            title_node = item.find('g:title', NAMESPACES) or item.find('title')
            price_node = item.find('g:price', NAMESPACES) or item.find('price')
            
            if title_node is not None and price_node is not None:
                title_text = title_node.text.strip()
                price_text = price_node.text.strip()
                
                if any(word in title_text.lower() for word in query_words):
                    matched_products += f"- Product: {title_text}, Price: {price_text}\n"
                    count += 1
                if count >= 3:
                    break
        return matched_products
    except Exception as e:
        print(f"Catalog Filter Error: {e}")
        return ""


# =====================================================================
# ৩. হোয়াটসঅ্যাপ থেকে ছবি ডাউনলোড করার ফাংশন
# =====================================================================
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


# =====================================================================
# ৪. মূল জেমিনি এআই প্রসেসর (স্মার্ট মেমোরি ও ইমেজ-ম্যাচিং লজিক)
# =====================================================================
def get_ai_answer(from_number, user_query, image_bytes=None):
    try:
        # ইমেজ বা ছবি আসলে জেমিনিকে পুরো ক্যাটালগ লিস্ট দাও যেন সে ছবি দেখে মেলাতে পারে
        if image_bytes:
            catalog_info = get_full_catalog_context()
        else:
            catalog_context = search_product_in_catalog(user_query) if user_query else ""
            catalog_info = f"Matched Products in Catalog:\n{catalog_context}" if catalog_context else "No direct match found in catalog file."

        system_instruction = (
            "You are the professional AI sales assistant for 'Dhaka Exclusive' (https://dhakaexclusive.org/).\n"
            f"XML Catalog Context:\n{catalog_info}\n\n"
            "STRICT CONVERSATION RULES:\n"
            "1. ALWAYS address the customer as 'প্রিয় গ্রাহক'. NEVER use internal system logs or search messages.\n"
            "2. Keep replies short, extremely polite, and completely in Bengali.\n"
            "3. If you find a matching product price from the catalog context, state the price politely in Taka. Never use USD or dollars ($).\n"
            "4. NEVER output your internal thinking, planning, or words like 'আমি অনুসন্ধান করছি'. Give the final answer directly.\n"
            "5. If an image is provided, visually match the item with the provided XML Catalog list. Extract the exact product name and price.\n"
            "6. CORE GOAL: Fulfill customer orders. You must collect: 1. Full Name, 2. Phone Number, 3. Full Delivery Address. Track these requirements across the chat history carefully.\n"
            "7. Delivery Charge: Inside Dhaka = 80 TK, Outside Dhaka = 130 TK.\n"
            "8. Order Confirmation Rule: Once the customer has provided all three details (Name, Phone, Address), state strictly: 'আপনার অর্ডারটি আমরা নোট করে নিয়েছি। আমাদের প্রতিনিধি কল করে কনফার্ম করবেন।'\n"
            "9. Do NOT repeat the order confirmation sentence if the customer says 'okay', 'thik ace' or continues the chat after the order is already placed.\n"
            "10. IF AND ONLY IF the product is completely missing from the XML catalog context and cannot be identified from the image, strictly say this exact sentence and nothing else:\n"
            "'প্রিয় গ্রাহক, এটি আমাদের একটি প্রিমিয়াম প্রোডাক্ট। এটির সঠিক লাইভ দাম ও সাইজটি নিশ্চিত করতে আমাদের একজন প্রতিনিধি খুব দ্রুত আপনাকে ইনবক্সে মেসেজ দিচ্ছেন।'"
        )

        ai_config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=[],
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            temperature=0.2,       
            max_output_tokens=250  
        )

        # ইউজারের সেশন ধরে রেখে ডাইনামিক ক্যাটালগ কনটেক্সট আপডেট করা
        if from_number not in user_chat_sessions:
            user_chat_sessions[from_number] = client.chats.create(model=MODEL_NAME, config=ai_config)
        else:
            user_chat_sessions[from_number].config = ai_config
            
        chat_session = user_chat_sessions[from_number]

        message_contents = []
        if image_bytes:
            img = Image.open(io.BytesIO(image_bytes))
            img.thumbnail((800, 800))
            message_contents.append(img)
            
        if user_query:
            message_contents.append(user_query)
        else:
            # ছবি পাঠানোর সাথে কোনো লেখা না থাকলে এটি ডিফল্ট প্রশ্ন হিসেবে কাজ করবে
            message_contents.append("এই প্রোডাক্টটির দাম কত বা এটার ডিটেইলস বলো?")

        response = chat_session.send_message(message_contents)
        return response.text

    except Exception as e:
        print(f"Gemini Error: {e}")
        return "প্রিয় গ্রাহক, আমি আপনার মেসেজটি বুঝতে পেরেছি। আমাদের একজন প্রতিনিধি খুব দ্রুত আপনাকে ইনবক্সে মেসেজ দিয়ে অর্ডারটি নিশ্চিত করছেন।"


# =====================================================================
# ৫. হোয়াটসঅ্যাপে মেসেজ পাঠানোর ফাংশন
# =====================================================================
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


# =====================================================================
# ৬. ব্যাকগ্রাউন্ড মাল্টি-থ্রেড প্রসেসর
# =====================================================================
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
            send_message(from_number, ai_response)


# =====================================================================
# ৭. মেটা Webhook ভেরিফিকেশন ও রিসিভার এন্ডপয়েন্ট
# =====================================================================
@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge"), 200
    return "Failed", 403


@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    try:
        if "messages" in data["entry"][0]["changes"][0]["value"]:
            value = data["entry"][0]["changes"][0]["value"]
            msg = value["messages"][0]
            msg_id = msg["id"]
            from_number = msg["from"]
            
            # ডুপ্লিকেট মেসেজ ফিল্টার (মেটা লুপ প্রোটেকশন)
            if msg_id in global_processed_messages:
                return "ok", 200
                
            global_processed_messages[msg_id] = True
            if len(global_processed_messages) > 2000:
                global_processed_messages.pop(next(iter(global_processed_messages)))

            # ব্যাকগ্রাউন্ড থ্রেড রান করা যেন মেটা ৩ সেকেন্ডের মধ্যে টাইমাউট এরর না দেয়
            thread = Thread(target=process_async_webhook, args=(msg, from_number))
            thread.start()
                
    except Exception as e:
        print(f"WEBHOOK ERROR: {e}")
        
    return "ok", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
