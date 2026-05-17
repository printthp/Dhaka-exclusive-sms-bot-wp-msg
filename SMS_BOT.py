import os
import requests
import xml.etree.ElementTree as ET
import json
import time
from threading import Thread  # <--- এই লাইনটি অবশ্যই থাকতে হবে
from flask import Flask, request
import google.generativeai as genai
from PIL import Image
from io import BytesIO

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




# আপনার ফেসবুক ক্যাটালগ লিঙ্ক
CATALOG_URL = "https://www.dhakaexclusive.org/facebook-catalog.xml"
DATABASE_FILE = "catalog_db.json"


# --- ১. ফেসবুক ক্যাটালগ XML ডাউনলোড এবং প্রসেস করার ফাংশন ---
def update_catalog_database():
    while True:
        try:
            print("🔄 Fetching product catalog from XML...")
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(CATALOG_URL, headers=headers, timeout=30)
            
            if response.status_code == 200:
                root = ET.fromstring(response.content)
                namespaces = {'g': 'http://base.google.com/ns/1.0'}
                products = []
                
                for item in root.findall('.//item'):
                    title = item.find('title')
                    price = item.find('.//g:price', namespaces)
                    link = item.find('link')
                    image_link = item.find('.//g:image_link', namespaces)
                    description = item.find('description')
                    
                    product_data = {
                        "title": title.text.strip() if title is not None else "Unknown Product",
                        "price": price.text.strip() if price is not None else "Contact Admin",
                        "link": link.text.strip() if link is not None else "",
                        "image_url": image_link.text.strip() if image_link is not None else "",
                        "description": description.text.strip() if description is not None else ""
                    }
                    products.append(product_data)
                
                with open(DATABASE_FILE, "w", encoding="utf-8") as f:
                    json.dump(products, f, ensure_ascii=False, indent=4)
                
                print(f"✅ Catalog updated successfully! Total products: {len(products)}")
            else:
                print(f"❌ Failed to fetch XML. Status code: {response.status_code}")
                
        except Exception as e:
            print(f"❌ Error updating catalog: {e}")
            
        time.sleep(3600) # প্রতি ১ ঘণ্টা পর পর আপডেট

# ব্যাকগ্রাউন্ডে ক্যাটালগ আপডেটের থ্রেড চালু করা
catalog_thread = Thread(target=update_catalog_database, daemon=True)
catalog_thread.start()

# --- ২. ডাটাবেজ থেকে প্রোডাক্টের তালিকা পড়ার ফাংশন ---
def get_catalog_data():
    if os.path.exists(DATABASE_FILE):
        with open(DATABASE_FILE, "r", encoding="utf-8") as f:
            return f.read()
    return "No product catalog data available yet."

# --- ৩. WhatsApp থেকে ছবি ডাউনলোড করার ফাংশন ---
def download_whatsapp_image(media_id):
    try:
        url = f"https://graph.facebook.com/v18.0/{media_id}"
        headers = {"Authorization": f"Bearer {PERMANENT_TOKEN}"}
        res = requests.get(url, headers=headers)
        if res.status_code != 200:
            return None
        
        # FIXED: res.get_json() পরিবর্তন করে res.json() করা হয়েছে
        media_url = res.json().get("url")
        if not media_url:
            return None
            
        img_res = requests.get(media_url, headers=headers)
        if img_res.status_code == 200:
            return Image.open(BytesIO(img_res.content))
    except Exception as e:
        print(f"Error downloading image: {e}")
    return None

# --- ৪. এআই থেকে উত্তর নেওয়ার মূল ফাংশন ---
def get_ai_answer(user_query, image_obj=None):
    try:
        catalog_info = get_catalog_data()
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        context = (
            f"You are the helpful AI assistant for 'Dhaka Exclusive', a premium kitchenware brand in Bangladesh.\n"
            f"RULES:\n"
            f"1. NEVER use the word 'নমস্কার'.\n"
            f"2. ALWAYS address the customer as 'প্রিয় গ্রাহক'.\n"
            f"3. Answer politely and naturally in Bengali.\n"
            f"4. If the customer sends an image, look at the image and match it with the 'LIVE PRODUCT CATALOG' below. "
            f"Find the correct product title, price, and details to reply.\n"
            f"5. If a product or its price is not found in the catalog, politely say that our live representative will provide the price shortly.\n\n"
            f"HERE IS YOUR LIVE PRODUCT CATALOG (JSON FORMAT):\n"
            f"{catalog_info}"
        )
        
        prompt = f"{context}\nCustomer: {user_query if user_query else 'Please identify this product from the catalog and tell me the price.'}"
        
        if image_obj:
            response = model.generate_content([prompt, image_obj])
        else:
            response = model.generate_content(prompt)
            
        return response.text

    except Exception as e:
        print(f"Primary Model Error: {e}")
        return "দুঃখিত প্রিয় গ্রাহক, আমাদের সিস্টেম এখন একটু ব্যস্ত। আমরা দ্রুত আপনার সাথে যোগাযোগ করছি।"

def send_message(recipient_number, message_body):
    if not PHONE_NUMBER_ID or not PERMANENT_TOKEN:
        return
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

# --- ৫. হোয়াটসঅ্যাপ রিকোয়েস্ট ব্যাকগ্রাউন্ডে প্রসেস করার ফাংশন ---
def handle_async_message(msg):
    try:
        from_number = msg["from"]
        
        # টেক্সট মেসেজ হ্যান্ডেল
        if msg.get("type") == "text":
            user_text = msg["text"]["body"].strip()
            ai_response = get_ai_answer(user_text)
            send_message(from_number, ai_response)
        
        # ইমেজ বা ছবি মেসেজ হ্যান্ডেল
        elif msg.get("type") == "image":
            media_id = msg["image"]["id"]
            caption = msg["image"].get("caption", "")
            
            # গ্রাহককে তাৎক্ষণিক একটি আপডেট পাঠানো (যাতে তারা বুঝতে পারে কাজ হচ্ছে)
            send_message(from_number, "প্রিয় গ্রাহক, আপনার পাঠানো পণ্যটি আমি আমাদের ক্যাটালগে চেক করছি। একটু অপেক্ষা করুন...")
            
            image_obj = download_whatsapp_image(media_id)
            if image_obj:
                ai_response = get_ai_answer(caption, image_obj=image_obj)
                send_message(from_number, ai_response)
            else:
                send_message(from_number, "দুঃখিত প্রিয় গ্রাহক, ছবিটি দেখতে সমস্যা হয়েছে। দয়া করে প্রোডাক্টের নাম লিখে জানাবেন কি?")
    except Exception as e:
        print(f"ASYNC PROCESSING ERROR: {e}")

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
            
            # FIXED: এআই প্রসেসিং আলাদা থ্রেডে পাঠিয়ে দিয়ে হোয়াটসঅ্যাপকে দ্রুত 'ok' পাঠানো হচ্ছে
            processing_thread = Thread(target=handle_async_message, args=(msg,))
            processing_thread.start()
                    
    except Exception as e:
        print(f"WEBHOOK ERROR: {e}")
        
    return "ok", 200 # হোয়াটসঅ্যাপকে তাৎক্ষণিক রেসপন্স দেওয়া হচ্ছে

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
