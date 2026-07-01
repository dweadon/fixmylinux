from flask import Flask, request, send_file
import requests
import os
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

SYSTEM_PROMPT = "You are a Linux expert. Help users fix their Linux problems. Be clear and concise. You can also answer in many different languages based on input from user language"

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

@app.route("/")
def home():
    return send_file("skeleton.html")

@app.route("/save", methods=["POST"])
def save():
    text = request.form["problem"]
    with open("problems.txt", "a") as f:
        f.write(text + "\n")
    return "Saved"
@app.route("/faq", methods=["GET"])
def faq():
    with open("faq.txt", "r") as g:
        faqs = g.read()
    return faqs
@app.route("/ask", methods=["POST"])
def ask():
    user_problem = request.form["problem"]
    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_problem}
            ]
        }
    )
    print(response.json())
    answer = response.json()["choices"][0]["message"]["content"]
    return answer

app.run(host="0.0.0.0")
