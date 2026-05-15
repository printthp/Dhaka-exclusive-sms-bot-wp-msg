import os
import requests
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

def get_ai_answer(user_query):
    try:
        # ফাইল থেকে আপনার শিখিয়ে দেওয়া সব তথ্য লোড করা হচ্ছে
        saved_knowledge = read_knowledge()
        
        model = genai.GenerativeModel('gemini-2.5-flash-lite') 
        
        context = (
            f"You are the helpful AI assistant for 'Dhaka Exclusive', a premium kitchenware brand in Bangladesh. "
            f"Answer politely and naturally in Bengali.\n\n"
            f"HERE IS YOUR LIVE KNOWLEDGE BASE (Use this info to answer):\n"
            f"{saved_knowledge}\n"
            f"Rule: Never use placeholders like [insert link]. If the answer is not in the knowledge base, answer politely based on what you know."
        )
        
        response = model.generate_content(f"{context}\nCustomer: {user_query}")
        return response.text
    except Exception as e:
        print(f"Primary Model Error: {e}")
        return "দুঃখিত, আমাদের সিস্টেম এখন একটু ব্যস্ত। আমরা দ্রুত আপনার সাথে যোগাযোগ করছি।"

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
            
            if msg.get("type") == "text":
                user_text = msg["text"]["body"].strip()
                
                # --- আপনি নিজে এআই-কে ট্রেইনিং বা নতুন তথ্য শেখানোর অংশ ---
                if user_text.lower().startswith("update:"):
                    new_info = user_text[7:].strip() # "update:" লেখাটা বাদ দিয়ে বাকি তথ্যটুকু নেবে
                    save_knowledge(new_info)
                    send_message(from_number, "✅ ধন্যবাদ! আপনার দেওয়া নতুন তথ্যটি আমি সফলভাবে মনে রেখেছি।")
                
                # --- সাধারণ কাস্টমার সার্ভিস ---
                else:
                    ai_response = get_ai_answer(user_text)
                    send_message(from_number, ai_response)
            else:
                send_message(from_number, "দুঃখিত, আমি বর্তমানে শুধু টেক্সট মেসেজ বুঝতে পারি।")
                
    except Exception as e:
        print(f"WEBHOOK ERROR: {e}")
        
    return "ok", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
