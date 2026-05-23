import os
import sqlite3
import logging
import google.generativeai as genai
from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session
import ctypes
import os
import ctypes
import os
from flask import Flask, request, jsonify

app = Flask(__name__)
application = app

# ইঞ্জিন লোডার (নিরাপদ পদ্ধতি)
try:
    lib_path = os.path.abspath("business_engine.so")
    if os.path.exists(lib_path):
        hybrid_engine = ctypes.CDLL(lib_path)
        hybrid_engine.process_logic.restype = ctypes.c_int
    else:
        hybrid_engine = None
except Exception as e:
    print(f"Engine Warning: {e}")
    hybrid_engine = None

@app.route("/execute", methods=["POST"])
def execute():
    data = request.json.get("value", 0)
    if hybrid_engine:
        result = hybrid_engine.process_logic(data)
        return jsonify({"result": result, "status": "hybrid_mode"})
    return jsonify({"result": data * 2, "status": "python_fallback"})
