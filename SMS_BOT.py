import os
import requests
from flask import Flask, request, jsonify
import google.generativeai as genai

app = Flask(__name__)

# আপনার সংগৃহীত তথ্যগুলো এখানে বসান
PERMANENT_TOKEN = "খাEAANtSb24BiwBRXK6X68nSEJhQxZAiPCvLdUGYDzuKDYZAZATkEoB3A9MY4HUwUd831wWeuiAeGe1Fkb9k512dQnho5R2oYZCt66DI4hEGfYK8kuUVT4niNsKJHHFP6bWscKBK1HZBcLZCVs7GAVwskp8gbavqxgSWQoQCoK7BQnOhawLLBpcOZCNtUnY4S1CKHJBAZDZD"
PHONE_NUMBER_ID = "1039959469208417"
GEMINI_KEY = "খাAIzaSyDcj0pNDNiCSW4no_8RU_x4bzbvobXwEL0"
VERIFY_ID = "Abid@0020"


def get_ai_answer(user_query):
    context = "You are the official assistant for 'Dhaka Exclusive', an online shop in Bangladesh selling kitchenware and household items. Answer politely in Bengali or English."
    response = model.generate_content(f"{context}\nCustomer: {user_query}")
    return response.text

def send_message(recipient_number, message_body):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {PERMANENT_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient_number,
        "type": "text",
        "text": {"body": message_body}
    }
    requests.post(url, json=payload, headers=headers)

# ১. ফেসবুক ভেরিফিকেশনের জন্য (GET Method)
@app.route("/webhook", methods=["GET"])
def verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Verification failed", 403

# ২. কাস্টমারের মেসেজ রিসিভ করার জন্য (POST Method)
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    try:
        if "messages" in data["entry"][0]["changes"][0]["value"]:
            message = data["entry"][0]["changes"][0]["value"]["messages"][0]
            from_number = message["from"]
            user_text = message["text"]["body"]

            # জেমিনি থেকে উত্তর নেওয়া এবং পাঠানো
            ai_response = get_ai_answer(user_text)
            send_message(from_number, ai_response)
    except:
        pass
    return "ok", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
