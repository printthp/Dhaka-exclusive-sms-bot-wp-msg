def call_hybrid_engine(value):
    # পাইথন থেকে সি++ ইঞ্জিনে ডাটা পাঠানো
    return hybrid_engine.process_business_logic(value)

# ড্যাশবোর্ড থেকে এই ফাংশনটি কল করলে সরাসরি অ্যাসেম্বলি লেভেলে কাজ করবে
