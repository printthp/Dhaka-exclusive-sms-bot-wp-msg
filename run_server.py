import os
# প্রথমে মেইন ফাইল ইম্পোর্ট করুন
import SMS_BOT
# এরপর আপনার এক্সটেনশন ফাইল ইম্পোর্ট করুন
import bot_extensions

# মেইন ফাইলের অ্যাপটিকে ধরুন
application = SMS_BOT.application

if __name__ == "__main__":
    # পোর্ট ঠিক রেখে সার্ভার চালু করুন
    port = int(os.environ.get("PORT", 5000))
    application.run(host="0.0.0.0", port=port)
