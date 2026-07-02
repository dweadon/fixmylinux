# v3
from flask import Flask, request, send_file, jsonify
import requests
import os
import re
import json
import uuid
from datetime import datetime
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

SYSTEM_PROMPT = "You are a Linux expert. Help users fix their Linux problems. Be clear and concise. You can also answer in many different languages based on input from user language. Let your answers to be straight not long and only solutions, if questions accured you may ask them normally"

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

KB_FILE = "kb.jsonl"

# Shell patterns considered risky enough to flag before showing the user
DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/(?!\S)",     # rm -rf /
    r"rm\s+-rf\s+\*",          # rm -rf *
    r"mkfs\.\w+",              # formatting a filesystem
    r"dd\s+if=\S+\s+of=/dev/\w+",  # dd straight to a raw device
    r":\(\)\{.*:\|:&\};:",     # fork bomb
    r"chmod\s+-R\s+777\s+/",   # recursive chmod 777 from root
    r">\s*/dev/sd[a-z]",       # writing directly to a disk device
    r"wipefs\s",
    r"fdisk\s+/dev/",
    r"parted\s+/dev/",
]


def flag_dangerous_commands(text):
    """Wrap risky shell commands with a visible warning the frontend can style."""
    flagged = False
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            flagged = True
            text = re.sub(
                pattern,
                lambda m: f"\n⚠️ WARNING: potentially destructive command ⚠️\n{m.group(0)}\n⚠️ Double-check this before running it. It can cause data loss.\n",
                text,
                flags=re.IGNORECASE,
            )
    return text, flagged


def save_to_kb(problem, distro, extra_info, answer, flagged):
    """Append this solved problem to the public knowledge base file."""
    entry = {
        "id": str(uuid.uuid4())[:8],
        "problem": problem,
        "distro": distro,
        "extra_info": extra_info,
        "answer": answer,
        "flagged": flagged,
        "created_at": datetime.utcnow().isoformat(),
    }
    with open(KB_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry["id"]


def load_kb():
    if not os.path.exists(KB_FILE):
        return []
    entries = []
    with open(KB_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def esc(s):
    """Minimal HTML escaping for safe display."""
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


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
    extra_info = request.form.get("extra_info", "").strip()
    # "1"/"true" means the user is OK with this fix appearing publicly on /kb
    share_publicly = request.form.get("share_publicly", "1") in ("1", "true", "True")

    distro_context = f" The user is on {distro} Linux." if distro != "Any" else ""
    system_context = (
        f" Here is extra system info the user provided (uname/log output/etc): {extra_info}"
        if extra_info
        else ""
    )

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT + distro_context + system_context},
                    {"role": "user", "content": user_problem},
                ],
            },
            timeout=30,
        )
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"AI request failed: {e}"}), 500

    data = response.json()
    if "choices" not in data:
        print("Groq error:", data)
        return jsonify({"error": data.get("error", {}).get("message", "unknown error")}), 500

    answer = data["choices"][0]["message"]["content"]
    answer, flagged = flag_dangerous_commands(answer)

    kb_id = None
    if share_publicly:
        kb_id = save_to_kb(user_problem, distro, extra_info, answer, flagged)

    return jsonify({"answer": answer, "flagged": flagged, "kb_id": kb_id})


@app.route("/kb", methods=["GET"])
def kb_list():
    entries = load_kb()
    entries.sort(key=lambda e: e["created_at"], reverse=True)
    rows = "".join(
        f'<li><a href="/kb/{e["id"]}">{esc(e["problem"][:90])}</a> '
        f'<span style="color:#888;font-size:12px;">({esc(e["distro"])})</span></li>'
        for e in entries[:300]
    )
    if not rows:
        rows = "<li style='color:#888;'>No solved problems yet — be the first to ask one!</li>"
    return f"""
    <html><head><title>FixMyLinux - Knowledge Base</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
      body {{ font-family: sans-serif; background:#121212; color:#fff; padding:40px; max-width:800px; margin:auto; }}
      a {{ color:#1DB954; text-decoration:none; }}
      a:hover {{ text-decoration:underline; }}
      li {{ margin-bottom:10px; }}
    </style></head>
    <body>
      <h1>Solved Linux Problems</h1>
      <p style="color:#888;">Real fixes, generated and shared by the community.</p>
      <ul>{rows}</ul>
      <p><a href="/">&larr; Back to FixMyLinux</a></p>
    </body></html>
    """


@app.route("/kb/<kb_id>", methods=["GET"])
def kb_detail(kb_id):
    entries = load_kb()
    entry = next((e for e in entries if e["id"] == kb_id), None)
    if not entry:
        return "Not found", 404

    warn = (
        "<p style='color:#ff5555; font-weight:600;'>⚠️ This fix includes a command flagged "
        "as potentially destructive. Read carefully before running anything.</p>"
        if entry["flagged"]
        else ""
    )
    return f"""
    <html><head><title>{esc(entry['problem'][:60])} - FixMyLinux</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
      body {{ font-family: sans-serif; background:#121212; color:#fff; padding:40px; max-width:800px; margin:auto; }}
      pre {{ background:#111; padding:16px; border-radius:8px; white-space:pre-wrap; line-height:1.6; }}
      a {{ color:#1DB954; text-decoration:none; }}
      a:hover {{ text-decoration:underline; }}
    </style></head>
    <body>
      <p><a href="/kb">&larr; All solved problems</a></p>
      <h2>{esc(entry['problem'])}</h2>
      <p style="color:#888;">Distro: {esc(entry['distro'])} &middot; {esc(entry['created_at'])}</p>
      {warn}
      <pre>{esc(entry['answer'])}</pre>
      <p><a href="/">Have a similar problem? Ask FixMyLinux &rarr;</a></p>
    </body></html>
    """


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)