
import os
import sqlite3
import logging
import google.generativeai as genai
from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session
import ctypes
from flask import Flask, request, jsonify
lib_path = os.path.abspath("business_engine.so")
hybrid_engine = ctypes.CDLL(lib_path)

app = Flask(__name__)
application = app

# ইঞ্জিন লোডার (নিরাপদ পদ্ধতি)
# পুরোনো কোড মুছে এটি বসান
import sys
lib_path = os.path.join(os.getcwd(), "business_engine.so")

if os.path.exists(lib_path):
    hybrid_engine = ctypes.CDLL(lib_path)
    # ... বাকি লজিক ...
else:
    print(f"Warning: Engine file not found at {lib_path}")
    hybrid_engine = None

@app.route("/execute", methods=["POST"])
def execute():
    data = request.json.get("value", 0)
    if hybrid_engine:
        result = hybrid_engine.process_logic(data)
        return jsonify({"result": result, "status": "hybrid_mode"})
    return jsonify({"result": data * 2, "status": "python_fallback"})
