import os
import io
import requests
import json
from flask import Flask, request
from google import genai
from google.genai import types
from threading import Thread

app = Flask(__name__)

# --- মেটা ও জেমিনি কনফিগারেশন ---
PERMANENT_TOKEN = "EAANtSb24BiwBRREXu8HztnpOLtamcKIvi09Qb24LiYax45S4aoYtFEVKEQZAxigfO2wbGf6RgHh51IURbQzKKrzPhkcprLxHpZBfOwxZAVCscdVOpjbapbS9sOLCIqZBM8tZAtSRRaVVYSTZBjUkkPZAQaLABSnG6cQcgQcwqZBC5I5yrB4cXgoUPDlzzn7HzUwsMAZDZD"
PHONE_NUMBER_ID = "1039959469208417"
GEMINI_KEY = "AIzaSyDICBRwj4wdwmqlut_Xjf0GgvXx_Mjcc0Q"
VERIFY_TOKEN = "dhakaex0020"

# 🔐 এডমিন নম্বর লিস্ট
ADMIN_NUMBERS = ["8801717121068", "8801954080047", "8801884413951", "8801735514320"]

# মেটা ডুপ্লিকেট মেসেজ ফিল্টার
global_processed_messages = {}
MEMORY_FILE = "knowledge.txt"

# --- 🚚 পাঠাও এপিআই লাইভ কনফিগারেশন ---
PATHAO_BASE_URL = "https://api-hermes.pathao.com"  
PATHAO_STORE_ID = "333358"
PATHAO_CLIENT_ID = "openOlRa7A"
PATHAO_CLIENT_SECRET = "7clJGfV1jh5njQEuR5yepVXZ9nYAjGORhNCOjgzG"
PATHAO_MERCHANT_EMAIL = "cocid1000006@gmail.com"
PATHAO_MERCHANT_PASSWORD = "trustedaA@2"

# --- New Gemini Client Setup ---
client = genai.Client(api_key=GEMINI_KEY)
MODEL_NAME = "gemini-2.5-flash"

# --- নলেজ বেস ফাংশন ---
def read_knowledge():
    if not os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "w", encoding="utf-8") as f:
            f.write("Brand Name: Dhaka Exclusive. Location: Bangladesh. Product: Premium kitchenware.\n")
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        return f.read()

def save_knowledge(new_info):
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n- {new_info}")

# =====================================================================
# 🚚 পাঠাও মার্চেন্ট এপিআই ফাংশনসমূহ (অর্ডার ক্রিয়েট ও ট্র্যাকিং)
# =====================================================================
def get_pathao_token():
    url = f"{PATHAO_BASE_URL}/aladdin/api/v1/issue-token"
    
    # পাঠাও লাইভ এপিআই-এর জন্য একদম নিখুঁত হেডার্স
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "X-API-KEY": PATHAO_CLIENT_ID.strip(),
        "X-SECRET-KEY": PATHAO_CLIENT_SECRET.strip()
    }
    
    payload = {
        "client_id": PATHAO_CLIENT_ID.strip(),
        "client_secret": PATHAO_CLIENT_SECRET.strip(),
        "username": PATHAO_MERCHANT_EMAIL.strip(),
        "password": PATHAO_MERCHANT_PASSWORD.strip()
    }
    
    try:
        # ভেরিফিকেশনের জন্য হেডার্স এবং বডি দুটিতেই কী-গুলো পাঠানো হচ্ছে
        res = requests.post(url, json=payload, headers=headers, timeout=10)
        
        if res.status_code == 200:
            return res.json().get("access_token")
        else:
            # যদি এরর আসে, রেন্ডার লগে আমরা দেখতে পাবো ঠিক কী বলছে (যেমন: Client ID mismatch)
            print(f"❌ PATHAO API REJECTION RESP: {res.status_code} - {res.text}")
            return None
            
    except Exception as e:
        print(f"❌ Pathao Token Exception: {e}")
        return None

# =====================================================================
# 🤖 জেমিনি এআই প্রসেসর (অটোমেটিক ট্র্যাকিং ও অর্ডার ডিটেকশন)
# =====================================================================
# =====================================================================
# 🤖 জেমিনি এআই প্রসেসর (অটোমেটিক ট্র্যাকিং ও অর্ডার ডিটেকশন আপগ্রেড)
# =====================================================================
def get_ai_answer(user_query):
    try:
        saved_knowledge = read_knowledge()
        
        system_instruction = (
            "You are the professional AI sales assistant for 'Dhaka Exclusive' (https://dhakaexclusive.org/).\n"
            "CRITICAL RULES:\n"
            "1. NEVER use the word 'নমস্কার'. ALWAYS address the customer as 'প্রিয় গ্রাহক'.\n"
            "2. Keep replies short, extremely polite, and completely in Bengali.\n"
            "3. State prices politely in Taka. Never use USD ($).\n"
            "4. Core Goal: Fulfill customer orders. Ask for: Full Name, Phone Number, and Full Delivery Address.\n\n"
            
            "ORDER DETECTION RULE:\n"
            "If the customer provides Name, Phone, and Address, append this block at the end:\n"
            "||ORDER_DATA||{\"name\": \"EXTRACTED_NAME\", \"phone\": \"EXTRACTED_PHONE\", \"address\": \"EXTRACTED_ADDRESS\"}||\n\n"
            
            "TRACKING DETECTION RULE (STRICT):\n"
            "If the user query is JUST a phone number (e.g., starting with 01 or 880) or looks like a tracking ID, "
            "OR if the customer is asking about an old order status, you MUST extract that number/ID and append "
            "this exact block at the very end without fail:\n"
            "||TRACK_DATA||{\"key\": \"EXTRACTED_PHONE_OR_ID\"}||\n"
            "Ensure 'EXTRACTED_PHONE_OR_ID' contains only the numbers/ID provided by the user. "
            "Do not output anything else if the input is just a number.\n\n"
            
            f"LIVE KNOWLEDGE BASE:\n{saved_knowledge}"
        )
        
        ai_config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.1, # ক্রিয়েটিভিটি কমিয়ে ১ করা হলো যেন রুল একদম শক্তভাবে মানে
            max_output_tokens=400
        )
        
        response = client.models.generate_content(
            model=MODEL_NAME, contents=user_query, config=ai_config
        )
        return response.text
    except Exception as e:
        print(f"Gemini AI Error: {e}")
        return "দুঃখিত প্রিয় গ্রাহক, আমাদের সিস্টেম এখন কিছুটা ব্যস্ত। আমাদের প্রতিনিধি দ্রুত যোগাযোগ করছেন।"
# =====================================================================
# ⚡ হোয়াটসঅ্যাপ ও এপিআই কানেক্টর (ব্যাকগ্রাউন্ড প্রসেসর)
# =====================================================================
def process_async_webhook(msg, from_number):
    if msg.get("type") == "text":
        user_text = msg["text"]["body"].strip()
        
        # 🔐 এডমিন ট্রেনিং ফিল্টার
        if user_text.lower().startswith("update:"):
            if from_number in ADMIN_NUMBERS:
                new_info = user_text[7:].strip()
                save_knowledge(new_info)
                send_message(from_number, "✅ তথ্যটি সফলভাবে আপডেট করা হয়েছে। এখন থেকে সম্মানিত গ্রাহকদের এই নতুন তথ্যের ভিত্তিতেই রেসপন্স করা হবে।")
            else:
                send_message(from_number, "দুঃখিত প্রিয় গ্রাহক, এই কমান্ডটি শুধুমাত্র আমাদের সিস্টেম অ্যাডমিনের জন্য সংরক্ষিত।")
        
        # সাধারণ কাস্টমার চ্যাট প্রসেসিং
        else:
            ai_response = get_ai_answer(user_text)
            
            # ১. ট্র্যাকিং রিকোয়েস্ট ডিটেকশন
            if "||TRACK_DATA||" in ai_response:
                try:
                    parts = ai_response.split("||TRACK_DATA||")
                    clean_reply = parts[0].strip()
                    json_str = parts[1].strip().replace("||", "")
                    track_info = json.loads(json_str)
                    
                    # পাঠাও থেকে লাইভ ট্র্যাকিং চেক
                    live_status = track_pathao_order(track_info.get("key"))
                    final_msg = f"প্রিয় গ্রাহক, আপনার অর্ডারের বর্তমান অবস্থা নিচে দেওয়া হলো:\n\n📌 **অবস্থা:** {live_status}"
                    send_message(from_number, final_msg)
                except Exception as track_err:
                    print(f"Tracking Logic Error: {track_err}")
                    send_message(from_number, ai_response.split("||TRACK_DATA||")[0].strip())
            
            # ২. নতুন অর্ডার ক্রিয়েট ডিটেকশন
            elif "||ORDER_DATA||" in ai_response:
                try:
                    parts = ai_response.split("||ORDER_DATA||")
                    clean_reply = parts[0].strip()
                    json_str = parts[1].strip().replace("||", "")
                    order_info = json.loads(json_str)
                    
                    success, result = create_pathao_order(
                        customer_name=order_info.get("name"),
                        customer_phone=order_info.get("phone"),
                        delivery_address=order_info.get("address")
                    )
                    if success:
                        final_msg = f"{clean_reply}\n\n📦 আপনার অর্ডারটি সফলভাবে পাঠাও কুরিয়ারে এন্ট্রি করা হয়েছে! ট্র্যাকিং আইডি: {result}"
                        send_message(from_number, final_msg)
                    else:
                        send_message(from_number, f"{clean_reply}\n\n(অর্ডারটি নোট করা হয়েছে, প্রতিনিধি দ্রুত কল করবেন।)")
                except Exception as json_err:
                    send_message(from_number, ai_response.split("||ORDER_DATA||")[0].strip())
            
            # ৩. সাধারণ আলাপচারিতা
            else:
                send_message(from_number, ai_response)
    else:
        send_message(from_number, "প্রিয় গ্রাহক, আমি বর্তমানে শুধু টেক্সট মেসেজ বুঝতে পারি। অনুগ্রহ করে আপনার প্রশ্নটি লিখে জানান।")

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
    try: requests.post(url, json=payload, headers=headers, timeout=10)
    except Exception as e: print(f"Send Message Error: {e}")

# =====================================================================
# 🛠️ মেটা Webhook রিসিভার এন্ডপয়েন্টস
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
