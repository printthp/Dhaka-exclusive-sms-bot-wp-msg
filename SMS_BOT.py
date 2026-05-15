import os
import requests
from flask import Flask, request
import google.generativeai as genai

app = Flask(__name__)

# --- রেন্ডার সেটিংস থেকে তথ্য নেওয়া (সঠিক নিয়ম) ---
# os.environ.get এর ভেতরে আপনার Render ড্যাশবোর্ডের "KEY" এর নাম দিতে হয়, সরাসরি টোকেন নয়।
PERMANENT_TOKEN = os.environ.get('EAANtSb24BiwBRXK6X68nSEJhQxZAiPCvLdUGYDzuKDYZAZATkEoB3A9MY4HUwUd831wWeuiAeGe1Fkb9k512dQnho5R2oYZCt66DI4hEGfYK8kuUVT4niNsKJHHFP6bWscKBK1HZBcLZCVs7GAVwskp8gbavqxgSWQoQCoK7BQnOhawLLBpcOZCNtUnY4S1CKHJBAZDZD') 
PHONE_NUMBER_ID = os.environ.get('1039959469208417')
GEMINI_KEY = os.environ.get('GEMINI_KEY')
VERIFY_TOKEN = "dhakaex0020"

# জেমিনি এআই সেটআপ
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-pro')

def get_ai_answer(user_query):
    try:
        context = "You are the assistant for 'Dhaka Exclusive', a shop selling kitchenware. Answer politely in Bengali."
        response = model.generate_content(f"{context}\nCustomer: {user_query}")
        return response.text
    except Exception as e:
        print(f"AI Error: {e}")
        return "দুঃখিত, আমি এখন উত্তর দিতে পারছি না।"

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
    # ফেসবুক এই প্যারামিটারগুলো পাঠায়
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    
    if mode == "subscribe" and token == VERIFY_TOKEN:
        # এখানে challenge টি সরাসরি টেক্সট হিসেবে রিটার্ন করতে হয়
        return str(challenge), 200
    
    return "Verification failed", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    try:
        # হোয়াটসঅ্যাপ মেসেজ ফরম্যাট চেক করা
        value = data["entry"][0]["changes"][0]["value"]
        if "messages" in value:
            message = value["messages"][0]
            from_number = message["from"]
            user_text = message["text"]["body"]
            
            ai_response = get_ai_answer(user_text)
            send_message(from_number, ai_response)
    except Exception as e:
        print(f"Error: {e}")
        
    return "ok", 200

if __name__ == "__main__":
    # Render এর জন্য পোর্ট ডায়নামিক রাখা ভালো
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
