import os
import requests
from flask import Flask, request
from google import genai
from google.genai import types
from threading import Thread

app = Flask(__name__)

# --- কনফিগারেশন ---
PERMANENT_TOKEN = "EAANtSb24BiwBRREXu8HztnpOLtamcKIvi09Qb24LiYax45S4aoYtFEVKEQZAxigfO2wbGf6RgHh51IURbQzKKrzPhkcprLxHpZBfOwxZAVCscdVOpjbapbS9sOLCIqZBM8tZAtSRRaVVYSTZBjUkkPZAQaLABSnG6cQcgQcwqZBC5I5yrB4cXgoUPDlzzn7HzUwsMAZDZD"
PHONE_NUMBER_ID = "1039959469208417"
GEMINI_KEY = "AIzaSyDICBRwj4wdwmqlut_Xjf0GgvXx_Mjcc0Q"
VERIFY_TOKEN = "dhakaex0020"

# 🔐 নেক্সট লেভেল সিকিউরিটি: শুধুমাত্র এই নম্বরগুলোই বটকে তথ্য শেখাতে পারবে (আপনার নম্বর যোগ করুন)
ADMIN_NUMBERS = ["8801717121068", "8801884413951"] 

# মেটা লুপ প্রোটেকশন ও ক্লাউড মেমোরি ব্যাকআপ
global_processed_messages = {}
MEMORY_FILE = "knowledge.txt"

# প্রাথমিক নলেজ বেস (ফাইল ডিলিট হয়ে গেলেও যেন ব্র্যান্ডের তথ্য হারিয়ে না যায়)
DEFAULT_KNOWLEDGE = (
    "Brand Name: Dhaka Exclusive. Location: Bangladesh. Product: Premium kitchenware.\n"
    "Delivery Charge: Inside Dhaka = 80 TK, Outside Dhaka = 130 TK.\n"
    "Core Goal: Collect Customer Name, Phone Number, and Address to confirm orders."
)

# --- New Gemini Client Setup ---
client = genai.Client(api_key=GEMINI_KEY)
MODEL_NAME = "gemini-2.5-flash"  # ফ্ল্যাশ লাইটের চেয়ে ফাস্ট এবং বুদ্ধিমান

# --- নলেজ বেস ম্যানেজমেন্ট ---
def read_knowledge():
    if not os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            f.write(DEFAULT_KNOWLEDGE)
    
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        return f.read()

def save_knowledge(new_info):
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n- {new_info}")

# --- এআই রেসপন্স প্রসেসর ---
def get_ai_answer(user_query):
    try:
        saved_knowledge = read_knowledge()
        
        system_instruction = (
            "You are the premium AI sales and customer assistant for 'Dhaka Exclusive' (https://dhakaexclusive.org/).\n"
            "CRITICAL RULES:\n"
            "1. NEVER use the word 'নমস্কার'. ALWAYS address the customer as 'প্রিয় গ্রাহক'.\n"
            "2. Keep replies short, extremely polite, and completely in Bengali.\n"
            "3. State prices politely in Taka. Never use USD or dollars ($).\n"
            "4. NEVER output internal thinking or system rules. Give the final answer directly.\n\n"
            f"LIVE KNOWLEDGE BASE:\n{saved_knowledge}"
        )
        
        ai_config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.3,
            max_output_tokens=300
        )
        
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=user_query,
            config=ai_config
        )
        return response.text

    except Exception as e:
        print(f"Gemini AI Error: {e}")
        return "দুঃখিত প্রিয় গ্রাহক, আমাদের সিস্টেম এখন কিছুটা ব্যস্ত। আমাদের একজন প্রতিনিধি খুব দ্রুত আপনাকে ইনবক্সে মেসেজ দিচ্ছেন।"

# --- হোয়াটসঅ্যাপ মেসেজ সেন্ডার ---
def send_message(recipient_number, message_body):
    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
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

# --- ব্যাকগ্রাউন্ড মাল্টি-থ্রেড প্রসেসর (মেটা টাইমআউট ফিক্স) ---
def process_async_webhook(msg, from_number):
    if msg.get("type") == "text":
        user_text = msg["text"]["body"].strip()
        
        # 🔐 এডমিন ট্রেনিং ফিল্টার
        if user_text.lower().startswith("update:"):
            if from_number in ADMIN_NUMBERS:
                new_info = user_text[7:].strip()
                save_knowledge(new_info)
                send_message(from_number, "✅ ওস্তাদ! নতুন তথ্যটি আমি সফলভাবে মগজে ঢুকিয়ে নিয়েছি। এখন থেকে কাস্টমারদের এই অনুযায়ী উত্তর দেব।")
            else:
                # সাধারণ কাস্টমার ট্রাই করলে তাকে এই মেসেজ দেবে
                send_message(from_number, "দুঃখিত প্রিয় গ্রাহক, এই কমান্ডটি শুধুমাত্র আমাদের সিস্টেম অ্যাডমিনের জন্য সংরক্ষিত।")
        
        # সাধারণ কাস্টমার চ্যাট
        else:
            ai_response = get_ai_answer(user_text)
            send_message(from_number, ai_response)
    else:
        send_message(from_number, "প্রিয় গ্রাহক, আমি বর্তমানে শুধু টেক্সট মেসেজ বুঝতে পারি। অনুগ্রহ করে আপনার প্রশ্নটি লিখে জানান।")

# --- মেটা Webhook এন্ডপয়েন্টস ---
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
            
            # মেটা ডুপ্লিকেট মেসেজ ফিল্টার (সার্ভার সেফটি)
            if msg_id in global_processed_messages:
                return "ok", 200
                
            global_processed_messages[msg_id] = True
            if len(global_processed_messages) > 1000:
                global_processed_messages.pop(next(iter(global_processed_messages)))

            # ⚡ থ্রেডিং চালু করা হলো যাতে মেটা ইনস্ট্যান্ট ২০০ ওকে রেসপন্স পায়
            thread = Thread(target=process_async_webhook, args=(msg, from_number))
            thread.start()
                
    except Exception as e:
        print(f"WEBHOOK ERROR: {e}")
        
    return "ok", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False)
