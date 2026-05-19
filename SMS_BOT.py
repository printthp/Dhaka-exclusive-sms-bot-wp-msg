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

# --- 🚚 পাঠাও এপিআই লাইভ কনফিগারেশন (আপনার স্ক্রিনশট অনুযায়ী) ---
PATHAO_BASE_URL = "https://api-hermes.pathao.com"  # লাইভ প্রোডাকশন ইউআরএল
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
# 🚚 পাঠাও মার্চেন্ট এপিআই ফাংশনসমূহ
# =====================================================================
def get_pathao_token():
    """পাঠাও এপিআই-তে কানেক্ট করার জন্য লাইভ টোকেন জেনারেট করবে"""
    url = f"{PATHAO_BASE_URL}/aladdin/api/v1/issue-token"
    headers = {
        "accept": "application/json",
        "content-type": "application/json"
    }
    payload = {
        "client_id": PATHAO_CLIENT_ID,
        "client_secret": PATHAO_CLIENT_SECRET,
        "username": PATHAO_MERCHANT_EMAIL,
        "password": PATHAO_MERCHANT_PASSWORD
    }
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=10)
        if res.status_code == 200:
            return res.json().get("access_token")
        else:
            print(f"❌ Pathao Token Error: {res.text}")
            return None
    except Exception as e:
        print(f"❌ Pathao Token Exception: {e}")
        return None

def create_pathao_order(customer_name, customer_phone, delivery_address):
    """পাঠাও মার্চেন্ট প্যানেলে অটোমেটিক অর্ডার এন্ট্রি করবে"""
    token = get_pathao_token()
    if not token:
        return False, "Token initialization failed."
        
    url = f"{PATHAO_BASE_URL}/aladdin/api/v1/orders"
    headers = {
        "authorization": f"Bearer {token}",
        "accept": "application/json",
        "content-type": "application/json"
    }
    
    payload = {
        "store_id": int(PATHAO_STORE_ID),
        "merchant_order_id": "", 
        "recipient_name": customer_name,
        "recipient_phone": customer_phone,
        "recipient_address": delivery_address,
        "recipient_city": "1",      # ১ = ঢাকা সিটি স্ট্যান্ডার্ড
        "recipient_zone": "1",      # জোন আইডি (পাঠাও জেনেশুনে ১ দেওয়া)
        "recipient_area": "1",      # এরিয়া আইডি
        "delivery_type": "48",      # নরমাল ডেলিভারি (৪৮ ঘণ্টা)
        "item_type": "2",           # ২ = পার্সেল (কিচেন আইটেম)
        "special_instruction": "WhatsApp Bot Auto Order",
        "item_quantity": 1,
        "amount_to_collect": 0,     # প্রাইস ডাইনামিক করতে চাইলে পরে মার্চেন্ট চেঞ্জ করতে পারবে
        "item_description": "Premium Kitchenware"
    }
    
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=10)
        if res.status_code == 201:
            order_data = res.json().get("data", {})
            consignment_id = order_data.get("consignment_id")  # পাঠাও ট্র্যাকিং আইডি
            return True, consignment_id
        else:
            print(f"❌ Pathao Order Failed: {res.text}")
            return False, res.text
    except Exception as e:
        print(f"❌ Pathao Order Exception: {e}")
        return False, str(e)

# =====================================================================
# 🤖 জেমিনি এআই প্রসেসর (অটোমেটিক ডাটা এক্সট্র্যাকশন লজিক সহ)
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
            "ORDER DETECTION & EXTRACTION RULE:\n"
            "If the customer provides all 3 crucial information (Name, Phone number, Delivery address), you MUST include a special hidden block at the very end of your response text in this EXACT format:\n"
            "||ORDER_DATA||{\"name\": \"EXTRACTED_NAME\", \"phone\": \"EXTRACTED_PHONE\", \"address\": \"EXTRACTED_ADDRESS\"}||\n"
            "Do NOT trigger this block unless all 3 data points are perfectly clear. Keep the visible part of the reply polite and tell them that we are processing the order.\n\n"
            f"LIVE KNOWLEDGE BASE:\n{saved_knowledge}"
        )
        
        ai_config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.2,
            max_output_tokens=400
        )
        
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=user_query,
            config=ai_config
        )
        return response.text

    except Exception as e:
        print(f"Gemini AI Error: {e}")
        return "দুঃখিত প্রিয় গ্রাহক, আমাদের সিস্টেম এখন কিছুটা ব্যস্ত। আমাদের প্রতিনিধি খুব দ্রুত আপনার সাথে যোগাযোগ করছেন।"

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
        
        # সাধারণ কাস্টমার চ্যাট এবং পাঠাও এন্ট্রি চেক
        else:
            ai_response = get_ai_answer(user_text)
            
            # এআই রেসপন্সে অর্ডার ডেটার হিডেন ব্লক আছে কিনা চেক করা
            if "||ORDER_DATA||" in ai_response:
                try:
                    # হিডেন ব্লক থেকে কাস্টমার ডেটা আলাদা করা
                    parts = ai_response.split("||ORDER_DATA||")
                    clean_reply = parts[0].strip()  # কাস্টমারকে দেখানোর অংশ
                    json_str = parts[1].strip().replace("||", "")
                    order_info = json.loads(json_str)
                    
                    # 🚚 পাঠাও-তে এন্ট্রি পাঠানো
                    success, result = create_pathao_order(
                        customer_name=order_info.get("name"),
                        customer_phone=order_info.get("phone"),
                        delivery_address=order_info.get("address")
                    )
                    
                    if success:
                        final_msg = f"{clean_reply}\n\n📦 আপনার অর্ডারটি সফলভাবে পাঠাও কুরিয়ারে এন্ট্রি করা হয়েছে! ট্র্যাকিং আইডি: {result}"
                        send_message(from_number, final_msg)
                    else:
                        # পাঠাও ফেইল করলেও কাস্টমারকে নরমাল অর্ডার নোট মেসেজ দেওয়া
                        send_message(from_number, f"{clean_reply}\n\n(আপনার অর্ডারটি আমাদের সিস্টেমে নোট করা হয়েছে, আমাদের প্রতিনিধি কল করে কনফার্ম করবেন।)")
                except Exception as json_err:
                    print(f"JSON Parsing/Pathao Integration Error: {json_err}")
                    send_message(from_number, ai_response.split("||ORDER_DATA||")[0].strip())
            else:
                # সাধারণ কনভারসেশন মেসেজ পাঠানো
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
    try:
        requests.post(url, json=payload, headers=headers, timeout=10)
    except Exception as e:
        print(f"Send Message Error: {e}")

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
