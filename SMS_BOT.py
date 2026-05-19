import os
import io
import requests
import json
from flask import Flask, request
from google import genai
from google.genai import types
from threading import Thread

app = Flask(__name__)

# =====================================================================
# ⚙️ ১. মেটা, জেমিনি ও পাঠাও লাইভ কনফিগারেশন
# =====================================================================
PERMANENT_TOKEN = "EAANtSb24BiwBRREXu8HztnpOLtamcKIvi09Qb24LiYax45S4aoYtFEVKEQZAxigfO2wbGf6RgHh51IURbQzKKrzPhkcprLxHpZBfOwxZAVCscdVOpjbapbS9sOLCIqZBM8tZAtSRRaVVYSTZBjUkkPZAQaLABSnG6cQcgQcwqZBC5I5yrB4cXgoUPDlzzn7HzUwsMAZDZD"
PHONE_NUMBER_ID = "1039959469208417"
GEMINI_KEY = "AIzaSyDICBRwj4wdwmqlut_Xjf0GgvXx_Mjcc0Q"
VERIFY_TOKEN = "dhakaex0020"

# 🔐 এডমিন নম্বর লিস্ট (যারা update: কমান্ড দিতে পারবেন)
ADMIN_NUMBERS = ["8801717121068", "8801954080047", "8801884413951", "8801735514320"]

# মেটা ডুপ্লিকেট মেসেজ ফিল্টার এবং মেমোরি ফাইল
global_processed_messages = {}
MEMORY_FILE = "knowledge.txt"

# 🚚 পাঠাও মার্চেন্ট ক্রেডেনশিয়ালস
PATHAO_BASE_URL = "https://api-hermes.pathao.com"  # লাইভ প্রোডাকশন ইউআরএল
PATHAO_STORE_ID = "333358"
PATHAO_CLIENT_ID = "openOlRa7A"
PATHAO_CLIENT_SECRET = "7clJGfV1jh5njQEuR5yepVXZ9nYAjGORhNCOjgzG"
PATHAO_MERCHANT_EMAIL = "cocid1000006@gmail.com"

# ⚠️ জরুরি: এখানে আপনার মার্চেন্ট অ্যাকাউন্টের আসল পাসওয়ার্ডটি বসিয়ে নিন
PATHAO_MERCHANT_PASSWORD = "trustedaA@2"

# --- New Gemini Client Setup ---
client = genai.Client(api_key=GEMINI_KEY)
MODEL_NAME = "gemini-2.5-flash"


# =====================================================================
# 📂 ২. নলেজ বেস (AI Training Data) ফাংশনসমূহ
# =====================================================================
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
# 🚚 ৩. পাঠাও মার্চেন্ট এপিআই ফাংশনসমূহ (অর্ডার ক্রিয়েট ও ট্র্যাকিং)
# =====================================================================
def get_pathao_token():
    """পাঠাও এপিআই-তে কানেক্ট করার জন্য লাইভ এক্সেস টোকেন জেনারেট করবে"""
    url = f"{PATHAO_BASE_URL}/aladdin/api/v1/issue-token"
    
    # ✅ হেডার একদম পরিষ্কার করা হয়েছে (অতিরিক্ত X-API-KEY বা X-SECRET-KEY বাদ দেওয়া হয়েছে)
    headers = {
        "accept": "application/json",
        "content-type": "application/json"
    }
    
    payload = {
        "client_id": PATHAO_CLIENT_ID.strip(),
        "client_secret": PATHAO_CLIENT_SECRET.strip(),
        "grant_type": "password",
        "username": PATHAO_MERCHANT_EMAIL.strip(),
        "password": PATHAO_MERCHANT_PASSWORD.strip()
    }
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=10)
        if res.status_code == 200:
            return res.json().get("access_token")
        else:
            print(f"❌ PATHAO API REJECTION RESP: {res.status_code} - {res.text}")
            return None
    except Exception as e:
        print(f"❌ Pathao Token Exception: {e}")
        return None

def create_pathao_order(customer_name, customer_phone, delivery_address):
    """কাস্টমার সব তথ্য দিলে অটোমেটিক পাঠাও প্যানেলে অর্ডার এন্ট্রি করবে"""
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
        "recipient_name": customer_name,
        "recipient_phone": customer_phone,
        "recipient_address": delivery_address,
        "recipient_city": "1",      # ১ = ঢাকা সিটি
        "recipient_zone": "1",      
        "recipient_area": "1",      
        "delivery_type": "48",      # ৪৮ ঘণ্টা স্ট্যান্ডার্ড ডেলিভারি
        "item_type": "2",           # ২ = পার্সেল (কিচেন আইটেম)
        "special_instruction": "WhatsApp Bot Auto Order",
        "item_quantity": 1,
        "amount_to_collect": 0,     # বিল অ্যামাউন্ট (মার্চেন্ট প্যানেল থেকে এডিট করা যাবে)
        "item_description": "Premium Kitchenware"
    }
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=10)
        if res.status_code == 201:
            return True, res.json().get("data", {}).get("consignment_id")
        return False, res.text
    except Exception as e:
        return False, str(e)

def track_pathao_order(tracking_key):
    """পাঠাও প্যানেল থেকে ফোন নম্বর দিয়ে লাইভ স্ট্যাটাস নিয়ে আসবে"""
    token = get_pathao_token()
    if not token:
        return "সিস্টেম ত্রুটি (Token Error)"
        
    tracking_key = str(tracking_key).strip().replace("+", "")
    url = f"{PATHAO_BASE_URL}/aladdin/api/v1/orders/{tracking_key}/tracking"
    headers = {
        "authorization": f"Bearer {token}",
        "accept": "application/json"
    }
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json().get("data", {})
            status = data.get("order_status", "unknown").lower()
            
            # পাঠাও স্ট্যাটাসগুলোর সহজ বাংলা ম্যাপ
            status_map = {
                "pending": "পেন্ডিং (অর্ডারটি রিভিউ করা হচ্ছে)",
                "picked": "কুরিয়ারের কাছে হস্তান্তরিত হয়েছে (Picked)",
                "in_transit": "ডেলিভারির জন্য রাস্তায় আছে (In Transit)",
                "delivered": "সফলভাবে ডেলিভারি সম্পন্ন হয়েছে 🎉",
                "cancelled": "অর্ডারটি বাতিল করা হয়েছে",
                "returned": "অর্ডারটি রিটার্ন এসেছে"
            }
            return status_map.get(status, f"স্ট্যাটাস: {status.upper()}")
        else:
            return "দুঃখিত, এই নম্বর বা ট্র্যাকিং আইডি দিয়ে কোনো অর্ডার খুঁজে পাওয়া যায়নি।"
    except Exception as e:
        print(f"❌ Pathao Track Exception: {e}")
        return "ট্র্যাকিং তথ্য লোড করতে সমস্যা হচ্ছে।"


# =====================================================================
# 🤖 ৪. জেমিনি এআই ইঞ্জিন (অটোমেটিক ডাটা ডিটেকশন স্ট্রাকচার)
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
            
            "TRACKING DETECTION RULE:\n"
            "If the customer asks about order status, where is the product, or wants to track an old order and provides a phone number or a tracking ID, you MUST extract that ID/phone number and append this block at the very end:\n"
            "||TRACK_DATA||{\"key\": \"EXTRACTED_PHONE_OR_ID\"}||\n"
            "Example: If they say 'আমার প্রোডাক্ট কোথায়', ask them for the phone number.\n\n"
            
            f"LIVE KNOWLEDGE BASE:\n{saved_knowledge}"
        )
        
        ai_config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.1,
            max_output_tokens=400
        )
        
        response = client.models.generate_content(
            model=MODEL_NAME, contents=user_query, config=ai_config
        )
        return response.text
    except Exception as e:
        print(f"Gemini AI Error: {e}")
        return "দুঃখিত প্রিয় গ্রাহক, আমাদের系统 এখন কিছুটা ব্যস্ত। আমাদের প্রতিনিধি দ্রুত যোগাযোগ করছেন।"


# =====================================================================
# ⚡ ৫. হোয়াটসঅ্যাপ রিকোয়েস্ট ও ব্যাকগ্রাউন্ড ইন্টেলিজেন্স প্রসেসর
# =====================================================================
def process_async_webhook(msg, from_number):
    if msg.get("type") == "text":
        user_text = msg["text"]["body"].strip()
        
        # 🔐 ক) এডমিন ট্রেনিং ফিল্টার
        if user_text.lower().startswith("update:"):
            if from_number in ADMIN_NUMBERS:
                new_info = user_text[7:].strip()
                save_knowledge(new_info)
                send_message(from_number, "✅ তথ্যটি সফলভাবে আপডেট করা হয়েছে। এখন থেকে সম্মানিত গ্রাহকদের এই নতুন তথ্যের ভিত্তিতেই রেসপন্স করা হবে।")
            else:
                send_message(from_number, "দুঃখিত প্রিয় গ্রাহক, এই আদেশটি শুধুমাত্র আমাদের সিস্টেম অ্যাডমিনের জন্য সংরক্ষিত।")
        
        # 🛒 খ) সাধারণ কাস্টমার চ্যাট এবং মোবাইল নম্বর রিয়েল-টাইম ডিটেক্টর
        else:
            # স্পেস এবং কান্ট্রি কোডের প্লাস সাইন ক্লিন করা
            clean_text = user_text.replace(" ", "").replace("+", "").strip()
            
            # 🎯 কাস্টমার যদি সরাসরি শুধু ১১ বা ১৩ ডিজিটের মোবাইল নম্বর ইনপুট দেয় (যেমন: 018... বা 88018...)
            if clean_text.isdigit() and (len(clean_text) == 11 or len(clean_text) == 13) and clean_text.startswith(("01", "8801")):
                # জেমিনিকে বাইপাস করে সরাসরি লাইভ পাঠাও ট্র্যাকিং রান হবে
                live_status = track_pathao_order(clean_text)
                final_msg = f"প্রিয় গ্রাহক, আপনার অর্ডারের বর্তমান অবস্থা নিচে দেওয়া হলো:\n\n📌 **অবস্থা:** {live_status}"
                send_message(from_number, final_msg)
                
            else:
                # কাস্টমার সাধারণ কথা বললে জেমিনি এআই রেসপন্স হ্যান্ডেল করবে
                ai_response = get_ai_answer(user_text)
                
                # ১. জেমিনি ট্র্যাকিং রিকোয়েস্ট ব্লক ডিটেক্ট করলে
                if "||TRACK_DATA||" in ai_response:
                    try:
                        parts = ai_response.split("||TRACK_DATA||")
                        json_str = parts[1].strip().replace("||", "")
                        track_info = json.loads(json_str)
                        
                        live_status = track_pathao_order(track_info.get("key"))
                        final_msg = f"প্রিয় গ্রাহক, আপনার অর্ডারের বর্তমান অবস্থা নিচে দেওয়া হলো:\n\n📌 **অবস্থা:** {live_status}"
                        send_message(from_number, final_msg)
                    except Exception as track_err:
                        send_message(from_number, ai_response.split("||TRACK_DATA||")[0].strip())
                
                # ২. জেমিনি নতুন অর্ডার ক্রিয়েট ব্লক ডিটেক্ট করলে
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
                            final_msg = f"{clean_reply}\n\n📦 আপনার অর্ডারটি সফলভাবে পাঠাও কুরিয়ারে এন্ট্রি করা হয়েছে! ট্র্যাকিং আইডি: {result}"
                            send_message(from_number, final_msg)
                        else:
                            send_message(from_number, f"{clean_reply}\n\n(আপনার অর্ডারটি নোট করা হয়েছে, আমাদের প্রতিনিধি খুব দ্রুত কল করে কনফার্ম করবেন।)")
                    except Exception as json_err:
                        send_message(from_number, ai_response.split("||ORDER_DATA||")[0].strip())
                
                # ৩. নরমাল কাস্টমার মেসেজ
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
    try:
        requests.post(url, json=payload, headers=headers, timeout=10)
    except Exception as e:
        print(f"Send Message Error: {e}")


# =====================================================================
# 🛠️ ৬. মেটা Webhook গেটওয়ে এন্ডপয়েন্টস
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
