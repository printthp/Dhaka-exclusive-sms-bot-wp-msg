import requests
from flask import Flask, request, jsonify
import google.generativeai as genai

app = Flask(__name__)

# আপনার সংগৃহীত তথ্যগুলো এখানে বসান
PERMANENT_TOKEN = "খাEAANtSb24BiwBRXK6X68nSEJhQxZAiPCvLdUGYDzuKDYZAZATkEoB3A9MY4HUwUd831wWeuiAeGe1Fkb9k512dQnho5R2oYZCt66DI4hEGfYK8kuUVT4niNsKJHHFP6bWscKBK1HZBcLZCVs7GAVwskp8gbavqxgSWQoQCoK7BQnOhawLLBpcOZCNtUnY4S1CKHJBAZDZD"
PHONE_NUMBER_ID = "1039959469208417"
GEMINI_KEY = "খাAIzaSyDcj0pNDNiCSW4no_8RU_x4bzbvobXwEL0"

genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')

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
    requests.post(url, headers=headers, json=payload)

@app.route('/webhook', methods=['GET', 'POST'])
def handle_webhook():
    if request.method == 'GET':
        return request.args.get('hub.challenge', '')
    
    data = request.json
    try:
        msg_obj = data['entry'][0]['changes'][0]['value']['messages'][0]
        sender = msg_obj['from']
        user_text = msg_obj['text']['body']
        
        reply = get_ai_answer(user_text)
        send_message(sender, reply)
    except:
        pass
    return "OK", 200

if __name__ == "__main__":
    app.run(port=5000)
