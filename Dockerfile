# পাইথন বেস ইমেজ
FROM python:3.10-slim

# প্রয়োজনীয় বিল্ড টুলস ইন্সটল
RUN apt-get update && apt-get install -y \
    nasm \
    g++ \
    make \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# কাজের ডিরেক্টরি
WORKDIR /app

# সব ফাইল কপি করা
COPY . .

# ইঞ্জিন কম্পাইল করা
RUN make

# পাইথন লাইব্রেরি ইন্সটল
RUN pip install --no-cache-dir -r requirements.txt

# Gunicorn দিয়ে সার্ভার চালু করা
CMD gunicorn --bind 0.0.0.0:$PORT app:application
