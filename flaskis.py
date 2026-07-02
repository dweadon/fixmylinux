# v2
from flask import Flask, request, send_file
import requests
import os
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

SYSTEM_PROMPT = "You are a Linux expert. Help users fix their Linux problems. Be clear and concise. You can also answer in many different languages based on input from user language. Let your answers to be straight not lomg and only solutions, if questions accured you may ask them normally"

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
    return send_file("faq.html")
@app.route("/feed", methods=["GET", "POST"])
def feed():
    if request.method == "POST":
        user_feed = request.form["feedback"]
        with open("feed.txt", "a") as e:
            e.write(user_feed + "\n")
        return "Thank you, if your feedback would be helpful we will include it in our site!"
    return send_file("feed.html")
@app.route("/ask", methods=["POST"])
def ask():
    user_problem = request.form["problem"]
    distro = request.form.get("distro", "Any")
    
    distro_context = f" The user is on {distro} Linux." if distro != "Any" else ""
    
    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "llama-3.3-70b-versatile",
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT + distro_context},
                {"role": "user", "content": user_problem}
            ]
        }
    )
    data = response.json()
    if "choices" not in data:
        print("Groq error:", data)
        return f"AI error: {data.get('error', {}).get('message', 'unknown error')}", 500
    answer = data["choices"][0]["message"]["content"]
    return answer

port = int(os.environ.get("PORT", 8080))
app.run(host="0.0.0.0", port=port)
