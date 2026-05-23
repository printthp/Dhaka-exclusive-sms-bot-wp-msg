def process_voice_command(audio_text):
    # ভয়েস কমান্ডকে বিজনেসের অ্যাকশনে রূপান্তর
    if "অর্ডার চেক করো" in audio_text:
        return "Fetching latest orders..."
    elif "সেলস রিপোর্ট দাও" in audio_text:
        return "Generating analytics..."
    return "Command not recognized."
