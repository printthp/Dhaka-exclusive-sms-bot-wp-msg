from SMS_BOT import app
import os

if __name__ == "__main__":
    # Gunicorn বা সরাসরি রান করার জন্য উপযোগী
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
