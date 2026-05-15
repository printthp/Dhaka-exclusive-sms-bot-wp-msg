import os
import requests
from flask import Flask, request
import google.generativeai as genai

app = Flask(__name__)

# --- কনফিগারেশন ---
# টোকেন ও কী সুরক্ষিত রাখতে পরিবেশ ভেরিয়েবল ব্যবহার করা ভালো, তবে আপনার দেওয়া ভ্যালুগুলোই এখানে রাখা হলো
PERMANENT_TOKEN = "EAANtSb24BiwBRREXu8HztnpOLtamcKIvi09Qb24LiYax45S4aoYtFEVKEQZAxigfO2wbGf6RgHh51IURbQzKKrzPhkcprLxHpZBfOwxZAVCscdVOpjbapbS9sOLCIqZBM8tZAtSRRaVVYSTZBjUkkPZAQaLABSnG6cQcgQcwqZBC5I5yrB4cXgoUPDlzzn7HzUwsMAZDZD"
PHONE_NUMBER_ID = "1039959469208417"
GEMINI_KEY = "AIzaSyDICBRwj4wdwmqlut_Xjf0GgvXx_Mjcc0Q"
VERIFY_TOKEN = "dhakaex0020"

# --- Gemini AI Setup ---
genai.configure(api_key=GEMINI_KEY)

def get_ai_answer(user_query):
    try:
        # জেমিনির নতুন আপডেট করা লাইট মডেল (1.5-flash এর পরিবর্তে)
        model = genai.GenerativeModel('gemini-2.5-flash-lite') 
        context = "You are the helpful AI assistant for 'Dhaka Exclusive', a premium kitchenware brand in Bangladesh. Answer politely in Bengali."
        response = model.generate_content(f"{context}\nCustomer: {user_query}")
        return response.text
    except Exception as e:
        print(f"Primary Model Error: {e}. Trying backup model...")
        # যদি flash-lite কাজ না করে, তবে নতুন ২.৫ প্রো মডেল ট্রাই করবে (gemini-pro এর পরিবর্তে)
        try:
            model = genai.GenerativeModel('gemini-2.5-pro')
            context = "You are the helpful AI assistant for 'Dhaka Exclusive', a premium kitchenware brand in Bangladesh. Answer politely in Bengali."
            response = model.generate_content(f"{context}\nCustomer: {user_query}")
            return response.text
        except Exception as e2:
            print(f"AI ERROR: Both models failed. Error: {e2}")
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
    
    response = requests.post(url, json=payload, headers=headers)
    print(f"DEBUG: Meta Status: {response.status_code}")
    print(f"DEBUG: Meta Full Response: {response.text}")

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    try:
        # ভ্যালু চেক করা
        if "messages" in data["entry"][0]["changes"][0]["value"]:
            value = data["entry"][0]["changes"][0]["value"]
            msg = value["messages"][0]
            from_number = msg["from"]
            
            # চেক করা হচ্ছে মেসেজটি কি টেক্সট নাকি অন্য কিছু (ছবি/ভিডিও)
            if msg.get("type") == "text":
                user_text = msg["text"]["body"]
                print(f"New Message from {from_number}: {user_text}")
                
                ai_response = get_ai_answer(user_text)
                send_message(from_number, ai_response)
            else:
                # যদি টেক্সট না হয়ে ছবি বা অন্য কিছু হয়
                print(f"Received a non-text message ({msg.get('type')}) from {from_number}")
                send_message(from_number, "দুঃখিত, আমি বর্তমানে শুধু টেক্সট মেসেজ বুঝতে পারি। দয়া করে আপনার প্রশ্নটি লিখে জানান।")
                
    except Exception as e:
        print(f"WEBHOOK ERROR: {e}")
        
    return "ok", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
