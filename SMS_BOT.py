import google.generativeai as genai
import os
from flask import Flask, request, jsonify

app = Flask(__name__)
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-1.5-pro-latest')

@app.route("/api/vision", methods=["POST"])
def vision_analysis():
    # ইমেজ এনালাইসিস করে বিজনেস ডিসিশন নিবে
    image_data = request.json.get("image_base64")
    response = model.generate_content(["এই পণ্যটি কি এবং এর দাম কত হওয়া উচিত?", image_data])
    return jsonify({"analysis": response.text})
