import bot_extensions  # এটিই মেইন ফাইলের সব ফাংশনকে বাইরে থেকে কন্ট্রোল করবে
import SMS_BOT

app = SMS_BOT.application

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
