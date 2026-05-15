import os
import requests
from flask import Flask, request
import google.generativeai as genai

app = Flask(__name__)

# Environment Variables
PERMANENT_TOKEN = os.environ.get('EAANtSb24BiwBRREXu8HztnpOLtamcKIvi09Qb24LiYax45S4aoYtFEVKEQZAxigfO2wbGf6RgHh51IURbQzKKrzPhkcprLxHpZBfOwxZAVCscdVOpjbapbS9sOLCIqZBM8tZAtSRRaVVYSTZBjUkkPZAQaLABSnG6cQcgQcwqZBC5I5yrB4cXgoUPDlzzn7HzUwsMAZDZD')
PHONE_NUMBER_ID = os.environ.get('1039959469208417')
GEMINI_KEY = os.environ.get('AIzaSyDcj0pNDNiCSW4no_8RU_x4bzbvobXwEL0')
VERIFY_TOKEN = os.environ.get('dhakaex0020')

# Gemini AI Setup
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

def get_ai_answer(user_query):
    try:
        context = "You are the helpful AI assistant for 'Dhaka Exclusive', a premium kitchenware brand in Bangladesh. Answer politely in Bengali."
        response = model.generate_content(f"{context}\nCustomer: {user_query}")
        return response.text
    except Exception as e:
        print(f"AI ERROR: {e}")
        return "দুঃখিত, আমাদের এআই সিস্টেম এখন একটু ব্যস্ত। আমরা দ্রুত আপনার সাথে যোগাযোগ করছি।"

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
    
    # এটি আপনার লগে বিস্তারিত এরর দেখাবে
    response = requests.post(url, json=payload, headers=headers)
    print(f"DEBUG: Meta Status: {response.status_code}")
    print(f"DEBUG: Meta Full Response: {response.text}")

@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge"), 200
    return "Failed", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    try:
        # মেসেজটি চেক করা
        if "messages" in data["entry"][0]["changes"][0]["value"]:
            msg = data["entry"][0]["changes"][0]["value"]["messages"][0]
            from_number = msg["from"]
            user_text = msg["text"]["body"]
            
            print(f"New Message from {from_number}: {user_text}")
            
            ai_response = get_ai_answer(user_text)
            send_message(from_number, ai_response)
    except Exception as e:
        print(f"WEBHOOK ERROR: {e}")
        
    return "ok", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
