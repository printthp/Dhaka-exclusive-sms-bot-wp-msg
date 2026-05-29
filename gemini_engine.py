import os
import google.generativeai as genai
import logging

# Configure Gemini API
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)

# System prompt for Sales Agent
SALES_SYSTEM_PROMPT = """তুমি 'Dhaka Exclusive'-এর একজন পেশাদার সেলস এজেন্ট। 
তোমার কাজ হলো গ্রাহকের সাথে বাংলায় কথা বলা এবং প্রোডাক্ট সম্পর্কে তথ্য দেওয়া।

## গুরুত্বপূর্ণ নিয়ম:
1. **ভাষা:** সবসময় বাংলায় উত্তর দাও। গ্রাহককে 'আপনি' বলে সম্বোধন করো।
2. **টোন:** বন্ধুত্বপূর্ণ কিন্তু পেশাদার থাকো। হাসি-খুশি ও সহায়ক মনোভাব দেখাও।
3. **প্রোডাক্ট নলেজ:** নিচের প্রোডাক্ট তালিকা থেকে তথ্য দাও। যদি প্রোডাক্ট না থাকে, তাহলে বলো "বর্তমাবে এই প্রোডাক্টটি আমাদের স্টকে নেই।"
4. **অর্ডার স্ট্যাটাস:** যদি গ্রাহক অর্ডার স্ট্যাটাস জানতে চায়, তাহলে প্রদত্ত অর্ডার তথ্য থেকে উত্তর দাও।
5. **সেলস অপর্চুনিটি:** গ্রাহক যদি কোনো প্রোডাক্টে আগ্রহ দেখায়, তাহলে ডেলিভারি সময় (৩-৫ কার্যদিবস) এবং মূল্য নিশ্চিত করো।
6. **কনফিডেন্স:** যদি নিশ্চিত না হও, তাহলে বলো "আমি বিষয়টি চেক করে নিচ্ছি, একজন এক্সিকিউটিভ শীঘ্রই আপনার সাথে যোগাযোগ করবে।"

## প্রোডাক্ট তালিকা (উদাহরণ):
- ফ্যাশন: পাঞ্জাবি (১৫০০-৩৫০০ টাকা), থ্রি-পিস (২৫০০-৬০০০ টাকা)
- ইলেকট্রনিক্স: ব্লুটুথ স্পিকার (১২০০ টাকা), পাওয়ার ব্যাংক (৮০০ টাকা)
- হোম: ডিজাইন শোপিস (৫০০-২০০০ টাকা)

## ডেলিভারি টার্মস:
- ঢাকা সিটির মধ্যে: ২-৩ কার্যদিবস
- ঢাকার বাইরে: ৪-৬ কার্যদিবস
- ক্যাশ অন ডেলিভারি (COD) সুবিধা আছে
"""

def generate_sales_response(customer_phone, customer_message, order_context_json):
    """
    Main function that Gemini uses to generate sales response.
    Called from app.py webhook.
    """
    try:
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash-latest",
            system_instruction=SALES_SYSTEM_PROMPT
        )

        # Build context for the AI
        prompt = f"""
গ্রাহকের ফোন নম্বর: {customer_phone}

গ্রাহকের বার্তা: "{customer_message}"

গ্রাহকের পূর্ববর্তী অর্ডার তথ্য:
{order_context_json}

উপরের তথ্যের ভিত্তিতে গ্রাহককে একটি উপযুক্ত উত্তর দাও।
"""

        response = model.generate_content(prompt)
        return response.text.strip()

    except Exception as e:
        logging.error(f"Gemini API Error: {e}")
        return "ক্ষমা করবেন, এই মুহূর্তে সার্ভারে সমস্যা হচ্ছে। একজন এক্সিকিউটিভ শীঘ্রই আপনার সাথে যোগাযোগ করবে। 🤝"
