import google.generativeai as genai
# ... অন্যান্য ইমপোর্ট ...

# Gemini AI Setup
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
ai_model = genai.GenerativeModel('gemini-1.5-pro-latest')

@app.route("/ai/chat", methods=["POST"])
def ai_chat():
    user_input = request.json.get("message")
    response = ai_model.generate_content(user_input)
    return jsonify({"reply": response.text})
    
@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    # এখানে সেশন চেক করার কোড বসবে
    if not session.get("logged_in"):
        return redirect(url_for("login"))
    # ড্যাশবোর্ড কন্টেন্ট...
