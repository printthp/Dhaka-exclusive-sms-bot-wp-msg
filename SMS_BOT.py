import os
import requests
processed_messages = set()
from flask import Flask, request
import google.generativeai as genai

app = Flask(__name__)

# --- কনফিগারেশন ---
PERMANENT_TOKEN = "EAANtSb24BiwBRREXu8HztnpOLtamcKIvi09Qb24LiYax45S4aoYtFEVKEQZAxigfO2wbGf6RgHh51IURbQzKKrzPhkcprLxHpZBfOwxZAVCscdVOpjbapbS9sOLCIqZBM8tZAtSRRaVVYSTZBjUkkPZAQaLABSnG6cQcgQcwqZBC5I5yrB4cXgoUPDlzzn7HzUwsMAZDZD"
PHONE_NUMBER_ID = "1039959469208417"
GEMINI_KEY = "AIzaSyDICBRwj4wdwmqlut_Xjf0GgvXx_Mjcc0Q"
VERIFY_TOKEN = "dhakaex0020"

# --- Gemini AI Setup ---
genai.configure(api_key=GEMINI_KEY)
#এটি হোয়াটসঅ্যাপ থেকে ছবি ডাউনলোড করার কাজ করবে:
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

# মেমোরি ফাইল তৈরি বা পড়ার ফাংশন
MEMORY_FILE = "knowledge.txt"

def read_knowledge():
    if not os.path.exists(MEMORY_FILE):
        # ফাইল না থাকলে একটা ডিফল্ট টেক্সট তৈরি হবে
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            f.write("Brand Name: Dhaka Exclusive. Location: Bangladesh. Product: Premium kitchenware.\n")
    
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        return f.read()

def save_knowledge(new_info):
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"- {new_info}\n")

def get_ai_answer(user_query, image_bytes=None):
    try:
        # গুগলের লাইভ সার্চ টুল চালু করা হচ্ছে যাতে ওয়েবসাইটের রিয়েল-টাইম ডাটা পড়তে পারে
        model = genai.GenerativeModel(
            model_name='gemini-2.5-flash',
            tools=[{"google_search": {}}]  # এই লাইনের মাধ্যমে লাইভ সার্চ একটিভ হলো
        ) 
        
        context = (
            "You are the professional AI sales assistant for 'Dhaka Exclusive' (https://dhakaexclusive.org/).\n"
            "STRICT RULES:\n"
            "1. ALWAYS address the customer as 'প্রিয় গ্রাহক'. NEVER use 'নমস্কার'.\n"
            "2. KEEP REPLIES EXTREMELY SHORT (Max 2-3 lines). Do not write histories or long essays.\n"
            "3. Look at the image provided. Use your built-in Google Search tool to search our website: https://dhakaexclusive.org/ "
            "Find the exact product matching the photo, then extract its current Name, Size/Measurement, and Price in BDT.\n"
            "4. Only state the price if you successfully find it via Google Search on dhakaexclusive.org.\n"
            "5. CRITICAL: If you cannot find the product or its specific price on dhakaexclusive.org, identify the item and strictly say:\n"
            "'প্রিয় গ্রাহক, এটি আমাদের একটি প্রিমিয়াম প্রোডাক্ট। এটির সঠিক লাইভ দাম ও সাইজটি নিশ্চিত করতে আমাদের একজন প্রতিনিধি খুব দ্রুত আপনাকে ইনবক্সে মেসেজ দিচ্ছেন।'"
        )
        
        if image_bytes:
            image_parts = [{"mime_type": "image/jpeg", "data": image_bytes}]
            prompt_parts = [context, image_parts[0], f"Customer Question: {user_query or 'এটার দাম কত?'}" ]
            response = model.generate_content(prompt_parts)
        else:
            response = model.generate_content(f"{context}\nCustomer: {user_query}")
            
        return response.text
    except Exception as e:
        print(f"Gemini Error: {e}")
        return "প্রিয় গ্রাহক, কারিগরি সমস্যার কারণে আমি এই মুহূর্তে মেসেজটি বুঝতে পারছি না। আমাদের প্রতিনিধি খুব দ্রুত আপনার সাথে যোগাযোগ করছেন।"

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
            from_number = msg["from"]
            
            # কাস্টমার টেক্সট পাঠালে
            if msg.get("type") == "text":
                user_text = msg["text"]["body"].strip()
                ai_response = get_ai_answer(user_text)
                send_message(from_number, ai_response)
                
            # কাস্টমার ছবি পাঠালে
            elif msg.get("type") == "image":
                media_id = msg["image"]["id"]
                caption = msg["image"].get("caption", "এটার দাম কত?")
                
                # ছবি ডাউনলোড করা হচ্ছে
                image_bytes = download_whatsapp_media(media_id)
                
                if image_bytes:
                    # ছবি সহ জেমিনিকে কল করা
                    model = genai.GenerativeModel('gemini-2.5-flash')
                    context = "You are the helpful AI assistant for 'Dhaka Exclusive'. NEVER use 'নমস্কার'. ALWAYS use 'প্রিয় গ্রাহক'. Answer politely in Bengali. Identify the kitchenware product in this image and tell its details or price."
                    image_parts = [{"mime_type": "image/jpeg", "data": image_bytes}]
                    response = model.generate_content([context, image_parts[0], caption])
                    ai_response = response.text
                else:
                    ai_response = "প্রিয় গ্রাহক, আমি আপনার পাঠানো ছবিটি সঠিকভাবে দেখতে পাচ্ছি না। দয়া করে আবার চেষ্টা করুন।"
                    
                send_message(from_number, ai_response)
            else:
                send_message(from_number, "দুঃখিত প্রিয় গ্রাহক, আমি বর্তমানে শুধু টেক্সট এবং ছবি বুঝতে পারি।")
                
    except Exception as e:
        print(f"WEBHOOK ERROR: {e}")
        
    return "ok", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
