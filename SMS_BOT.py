import os
import io
import requests
import xml.etree.ElementTree as ET
from flask import Flask, request
from PIL import Image
from google import genai
from google.genai import types
from threading import Thread
from gtts import gTTS # টেক্সটকে ভয়েস বানানোর জন্য লাইব্রেরি

app = Flask(__name__)

# মেমোরি ও সেশন ট্র্যাকিং
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

# --- সুপার ডায়নামিক ক্যাটালগ সার্চ টুল ---
def find_products_by_keyword(search_keyword: str) -> str:
    """
    Searches the live product catalog using any keyword or product name identified from text or images.
    """
    try:
        if not search_keyword or len(search_keyword) < 2:
            return "No specific product keyword provided for search."
            
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(CATALOG_URL, headers=headers, timeout=12)
        if res.status_code != 200:
            return "Product catalog is currently offline."

        root = ET.fromstring(res.content)
        matched_products = []
        keywords = search_keyword.lower().replace("টাকার", "").replace("মধ্যে", "").split()
        
        for item in root.findall('.//item'):
            title = item.find('title')
            price = item.find('price')
            link = item.find('link')
            
            if title is not None and price is not None:
                title_text = title.text.strip()
                price_text = price.text.strip()
                link_text = link.text.strip() if link is not None else ""
                
                if any(kw in title_text.lower() for kw in keywords):
                    matched_products.append(f"- Product: {title_text} | Price: {price_text} | Link: {link_text}")
                    if len(matched_products) >= 5:
                        break
                        
        if matched_products:
            return "\n".join(matched_products)
        return f"No products found matching '{search_keyword}'."
    except Exception as e:
        return f"Error during search: {str(e)}"

# --- মূল জেমিনি এআই ইঞ্জিন ---
def get_ai_answer(from_number, user_query, image_bytes=None):
    try:
        if len(user_chat_sessions) > 1000:
            user_chat_sessions.pop(next(iter(user_chat_sessions)))

        system_instruction = (
            "You are 'Dhaka Exclusive's live AI Audio/Voice Executive. You talk to customers using voice notes.\n\n"
            "EMOTIONAL & SITUATIONAL RULES:\n"
            "1. ALWAYS address the customer as 'প্রিয় গ্রাহক'.\n"
            "2. Adapt to the customer's mood. If the customer is angry, be extremely apologetic, soft, and comforting. If they are confused, explain step by step.\n"
            "3. Speak completely in natural, polite Bengali. Avoid sounding like a rigid robot.\n\n"
            "PRODUCT SEARCH & ORDERING:\n"
            "- When they ask for a product or send an image, use `find_products_by_keyword` tool instantly to find the price.\n"
            "- If they want to order, dynamically track and collect: 1. Full Name, 2. Phone Number, 3. Delivery Address.\n"
            "- Inside Dhaka delivery is 80 TK, Outside Dhaka is 130 TK.\n"
            "- If any product is completely missing from search, say: 'প্রিয় গ্রাহক, এটি আমাদের একটি প্রিমিয়াম প্রোডাক্ট। এটির সঠিক লাইভ দাম ও সাইজটি নিশ্চিত করতে আমাদের একজন প্রতিনিধি খুব দ্রুত আপনাকে ইনবক্সে মেসেজ দিচ্ছেন।'"
        )

        ai_config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=[find_products_by_keyword, types.Tool(google_search=types.GoogleSearch())],
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            temperature=0.4,       
            max_output_tokens=300  
        )

        if from_number not in user_chat_sessions:
            user_chat_sessions[from_number] = client.chats.create(model=MODEL_NAME, config=ai_config)
            
        chat_session = user_chat_sessions[from_number]

        message_parts = []
        if image_bytes:
            img = Image.open(io.BytesIO(image_bytes))
            img.thumbnail((800, 800))
            message_parts.append(img)
            message_parts.append("Look at this image, identify the product, and find its price using the tool.")
            
        if user_query:
            message_parts.append(user_query)
        elif not image_bytes:
            message_parts.append("দাম কত?")

        response = chat_session.send_message(message_parts)
        return response.text
    except Exception as e:
        print(f"Gemini Error: {e}")
        return "প্রিয় গ্রাহক, কারিগরি সমস্যার কারণে আমি বুঝতে পারছি না। আমাদের প্রতিনিধি যোগাযোগ করছেন।"

# --- হোয়াটসঅ্যাপে ভয়েস মেসেজ পাঠানো (নতুন এপিআই লজিক) ---
def send_voice_message(recipient_number, text_body):
    try:
        # ১. টেক্সটকে বাংলা ভয়েস ফাইলে রূপান্তর করা
        tts = gTTS(text=text_body, lang='bn', slow=False)
        
        # রেন্ডার সার্ভারের লোকাল স্টোরেজে সাময়িকভাবে অডিও সেভ করা
        audio_path = f"/tmp/{recipient_number}.mp3"
        tts.save(audio_path)
        
        # ২. মেটা সার্ভারে অডিও ফাইলটি আপলোড করা
        upload_url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/media"
        headers = {"Authorization": f"Bearer {PERMANENT_TOKEN}"}
        
        with open(audio_path, 'rb') as f:
            files = {
                'file': (f'{recipient_number}.mp3', f, 'audio/mpeg'),
                'messaging_product': (None, 'whatsapp')
            }
            upload_res = requests.post(upload_url, headers=headers, files=files, timeout=20)
            
        if upload_res.status_code == 200:
            media_id = upload_res.json().get("id")
            
            # ৩. কাস্টমারকে অডিও মেসেজটি সেন্ড করা
            send_url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
            payload = {
                "messaging_product": "whatsapp",
                "to": recipient_number,
                "type": "audio",
                "audio": {"id": media_id}
            }
            requests.post(send_url, json=payload, headers={"Authorization": f"Bearer {PERMANENT_TOKEN}"}, timeout=10)
            
        # ক্লিনআপ লোকাল ফাইল
        if os.path.exists(audio_path):
            os.remove(audio_path)
            
    except Exception as e:
        print(f"Voice Send Error: {e}")

# --- ব্যাকগ্রাউন্ড প্রসেস ট্র্যাকিং ---
def process_async_webhook(msg, from_number):
    user_text = None
    image_bytes = None
    
    if msg.get("type") == "text":
        user_text = msg["text"]["body"].strip()
    elif msg.get("type") == "image":
        media_id = msg["image"]["id"]
        user_text = msg["image"].get("caption", "").strip()
        image_bytes = download_whatsapp_media(media_id)
        
    if user_text or image_bytes:
        # জেমিনি থেকে সেরা উত্তর নেওয়া
        ai_response = get_ai_answer(from_number, user_text, image_bytes)
        # উত্তরটিকে ভয়েস নোট আকারে পাঠানো
        send_voice_message(from_number, ai_response)

# --- মেটা Webhook ভেরিফিকেশন ---
@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge"), 200
    return "Failed", 403

# --- হোয়াটসঅ্যাপ রিসিভার ---
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
