# ১. পাইথন ইমেজ ব্যবহার করে পাইথন লজিক ও Flask সার্ভার রান করবে
FROM python:3.10-slim

# ২. সার্ভারে C++ এবং Assembly টুলসগুলো ইন্সটল করে নেবে
RUN apt-get update && apt-get install -y nasm g++ make gcc

# ৩. আপনার সব ফাইল কপি করবে
WORKDIR /app
COPY . .

# ৪. Makefile চালিয়ে ইঞ্জিনগুলোকে বাইনারি ফাইলে রূপান্তর করবে (এটিই আপনার মেইন পাওয়ার)
RUN make

# ৫. সব ডিপেন্ডেন্সি ইন্সটল করে ওয়েব সার্ভার চালু করবে
RUN pip install -r requirements.txt
CMD gunicorn --bind 0.0.0.0:$PORT app:application
