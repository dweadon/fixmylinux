# v4
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

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
KB_FILE = "kb.jsonl"

SYSTEM_PROMPT = """You are FixMyLinux, the world's best Linux troubleshooting assistant.

When given a Linux problem, respond ONLY with a JSON object (no markdown, no explanation outside the JSON) in this exact format:
{
  "fix": "The actual fix with exact commands to run",
  "why": "Why this problem happened (1-2 sentences)",
  "learn": "What the user can learn from this (1-2 sentences)",
  "confidence": 92,
  "sources": ["Arch Wiki", "Ubuntu Documentation", "Stack Overflow"],
  "dangerous_commands": ["rm -rf /", "dd if=..."],
  "beginner_explanation": "A simple plain-English explanation of what the fix does step by step"
}

Rules:
- confidence is a number 0-100 based on how certain you are
- sources is a list of places where this solution is documented (Arch Wiki, Ubuntu Docs, Fedora Docs, GitHub Issues, Stack Overflow, Reddit, man pages, etc.)
- dangerous_commands is a list of any commands in the fix that could be destructive if misused. Empty list [] if none.
- beginner_explanation is always filled in with a friendly explanation
- Answer in the same language the user writes in
- Be direct and concise in the fix
- Always return valid JSON
"""

# Shell patterns considered risky enough to flag
DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/(?!\S)",
    r"rm\s+-rf\s+\*",
    r"mkfs\.\w+",
    r"dd\s+if=\S+\s+of=/dev/\w+",
    r":\(\)\{.*:\|:&\};:",
    r"chmod\s+-R\s+777\s+/",
    r">\s*/dev/sd[a-z]",
    r"wipefs\s",
    r"fdisk\s+/dev/",
    r"parted\s+/dev/",
]


def check_dangerous(text):
    """Return True if any dangerous pattern is found in text."""
    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def save_to_kb(problem, distro, answer, why, sources, flagged):
    entry = {
        "id": str(uuid.uuid4())[:8],
        "problem": problem,
        "distro": distro,
        "answer": answer,
        "why": why,
        "sources": sources,
        "flagged": flagged,
        "created_at": datetime.utcnow().isoformat(),
        "votes_up": 0,
        "votes_down": 0,
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
                    e = json.loads(line)
                    e.setdefault("votes_up", 0)
                    e.setdefault("votes_down", 0)
                    entries.append(e)
                except json.JSONDecodeError:
                    continue
    return entries


def save_kb_all(entries):
    """Rewrite the whole KB file (used after a vote updates an entry)."""
    with open(KB_FILE, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def confidence_tier(confidence, sources, flagged):
    """Turn the raw AI confidence number into an honest qualitative tier,
    since a bare percentage implies more precision than the model actually has."""
    try:
        confidence = int(confidence)
    except (TypeError, ValueError):
        confidence = 0

    n_sources = len(sources or [])
    if flagged:
        label, level = "Needs verification", "low"
    elif confidence >= 85 and n_sources >= 2:
        label, level = "High confidence", "high"
    elif confidence >= 60 and n_sources >= 1:
        label, level = "Medium confidence", "medium"
    else:
        label, level = "Needs verification", "low"

    basis = list(sources or [])
    if not basis:
        basis = ["Model's general knowledge (no specific source cited)"]
    return {"label": label, "level": level, "basis": basis}


# In-memory follow-up conversation store: {conversation_id: [messages...]}
# NOTE: this resets on server restart since it isn't persisted to disk.
CONVERSATIONS = {}
MAX_TURNS = 6  # cap how long a follow-up thread can grow


def call_groq(messages):
    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"model": "llama-3.3-70b-versatile", "messages": messages},
        timeout=30,
    )
    data = response.json()
    if "choices" not in data:
        raise RuntimeError(data.get("error", {}).get("message", "unknown error"))
    raw = data["choices"][0]["message"]["content"]
    clean = raw.strip()
    if clean.startswith("```"):
        clean = clean.split("```")[1]
        if clean.startswith("json"):
            clean = clean[4:]
    try:
        parsed = json.loads(clean)
    except Exception:
        parsed = {
            "fix": raw,
            "why": "",
            "learn": "",
            "confidence": 0,
            "sources": [],
            "dangerous_commands": [],
            "beginner_explanation": "",
        }
    return parsed


def esc(s):
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


@app.route("/")
def home():
    return send_file("skeleton.html")


@app.route("/faq", methods=["GET"])
def faq():
    return send_file("faq.html")


@app.route("/feed", methods=["GET", "POST"])
def feed():
    if request.method == "POST":
        user_feed = request.form["feedback"]
        with open("feed.txt", "a") as e:
            e.write(user_feed + "\n")
        return "Thank you! If your feedback is helpful we will include it in our site!"
    return send_file("feed.html")


@app.route("/save", methods=["POST"])
def save():
    text = request.form["problem"]
    with open("problems.txt", "a") as f:
        f.write(text + "\n")
    return "Saved"


MAX_LOG_CHARS = 12000  # keep uploaded logs from blowing up the prompt/cost


def read_uploaded_log():
    """Read an optional uploaded .log/.txt/journalctl/dmesg dump from the request."""
    f = request.files.get("log_file")
    if not f or not f.filename:
        return ""
    try:
        raw = f.read().decode("utf-8", errors="replace")
    except Exception:
        return ""
    if len(raw) > MAX_LOG_CHARS:
        raw = raw[-MAX_LOG_CHARS:]  # keep the tail, usually the most relevant part
        raw = "[...truncated, showing last part of the log...]\n" + raw
    return raw


@app.route("/ask", methods=["POST"])
def ask():
    user_problem = request.form["problem"]
    distro = request.form.get("distro", "Any")
    mode = request.form.get("mode", "normal")
    extra_info = request.form.get("extra_info", "").strip()
    share_publicly = request.form.get("share_publicly", "1") in ("1", "true", "True")
    log_text = read_uploaded_log()

    # Build system context
    system_info = ""
    if distro and distro != "Any":
        system_info += f"Distribution: {distro}. "

    profile_distro = request.form.get("profile_distro", "")
    profile_de = request.form.get("profile_de", "")
    profile_gpu = request.form.get("profile_gpu", "")
    profile_pm = request.form.get("profile_pm", "")

    if profile_distro:
        system_info += f"Distro: {profile_distro}. "
    if profile_de:
        system_info += f"Desktop: {profile_de}. "
    if profile_gpu:
        system_info += f"GPU: {profile_gpu}. "
    if profile_pm:
        system_info += f"Package manager: {profile_pm}. "
    if extra_info:
        system_info += f"Extra system info: {extra_info}. "

    mode_instruction = ""
    if mode == "beginner":
        mode_instruction = " The user is a beginner — make the fix extra simple and explain every command."
    elif mode == "learning":
        mode_instruction = " The user wants to learn — be thorough in the 'why' and 'learn' fields."

    final_prompt = SYSTEM_PROMPT
    if system_info:
        final_prompt += f"\n\nUser's system: {system_info}"
    if mode_instruction:
        final_prompt += mode_instruction
    if log_text:
        final_prompt += (
            "\n\nThe user also attached a log file (journalctl/dmesg/apt output, etc)."
            " Use it as primary evidence — look for the actual error lines, not just the"
            " description, and mention what you found in it."
        )

    user_content = user_problem
    if log_text:
        user_content += f"\n\n--- Attached log ---\n{log_text}"

    messages = [
        {"role": "system", "content": final_prompt},
        {"role": "user", "content": user_content},
    ]

    try:
        parsed = call_groq(messages)
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"AI request failed: {e}"}), 500
    except RuntimeError as e:
        print("Groq error:", e)
        return jsonify({"error": str(e)}), 500

    # Double-check dangerous commands with regex (belt and suspenders)
    flagged = bool(parsed.get("dangerous_commands")) or check_dangerous(parsed.get("fix", ""))
    parsed["flagged"] = flagged
    parsed["confidence_info"] = confidence_tier(parsed.get("confidence", 0), parsed.get("sources"), flagged)

    # Save to knowledge base
    kb_id = None
    if share_publicly:
        kb_id = save_to_kb(
            problem=user_problem,
            distro=distro if distro != "Any" else profile_distro or "Unknown",
            answer=parsed.get("fix", ""),
            why=parsed.get("why", ""),
            sources=parsed.get("sources", []),
            flagged=flagged,
        )
    parsed["kb_id"] = kb_id

    # Start a follow-up conversation thread so the user can say "I tried it, didn't work"
    conversation_id = str(uuid.uuid4())[:12]
    CONVERSATIONS[conversation_id] = messages + [
        {"role": "assistant", "content": json.dumps(parsed)}
    ]
    parsed["conversation_id"] = conversation_id

    return jsonify(parsed)


@app.route("/followup", methods=["POST"])
def followup():
    conversation_id = request.form.get("conversation_id", "")
    message = request.form.get("message", "").strip()
    if not conversation_id or conversation_id not in CONVERSATIONS:
        return jsonify({"error": "This conversation has expired. Please ask again from the main form."}), 400
    if not message:
        return jsonify({"error": "Tell us what happened when you tried the fix."}), 400

    history = CONVERSATIONS[conversation_id]
    if len(history) >= (MAX_TURNS * 2):
        return jsonify({"error": "This thread got long — please start a fresh question."}), 400

    history = history + [{"role": "user", "content": message}]

    try:
        parsed = call_groq(history)
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"AI request failed: {e}"}), 500
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

    flagged = bool(parsed.get("dangerous_commands")) or check_dangerous(parsed.get("fix", ""))
    parsed["flagged"] = flagged
    parsed["confidence_info"] = confidence_tier(parsed.get("confidence", 0), parsed.get("sources"), flagged)

    history = history + [{"role": "assistant", "content": json.dumps(parsed)}]
    CONVERSATIONS[conversation_id] = history
    parsed["conversation_id"] = conversation_id

    return jsonify(parsed)


@app.route("/vote/<kb_id>", methods=["POST"])
def vote(kb_id):
    direction = request.form.get("direction", "")
    if direction not in ("up", "down"):
        return jsonify({"error": "invalid vote"}), 400

    entries = load_kb()
    entry = next((e for e in entries if e["id"] == kb_id), None)
    if not entry:
        return jsonify({"error": "not found"}), 404

    if direction == "up":
        entry["votes_up"] = entry.get("votes_up", 0) + 1
    else:
        entry["votes_down"] = entry.get("votes_down", 0) + 1

    save_kb_all(entries)
    return jsonify({"votes_up": entry["votes_up"], "votes_down": entry["votes_down"]})


@app.route("/diagnose.sh", methods=["GET"])
def diagnose_script():
    """A small read-only diagnostic script users can run and paste the output back in.
    It only reads system info — it never modifies anything."""
    script = """#!/bin/bash
# FixMyLinux diagnostic snapshot -- read-only, changes nothing on your system.
echo "=== uname ==="; uname -a
echo; echo "=== os-release ==="; cat /etc/os-release 2>/dev/null
echo; echo "=== kernel modules (nvidia/amd/intel) ==="; lsmod | grep -Ei 'nvidia|amdgpu|i915'
echo; echo "=== GPU (lspci) ==="; lspci -k 2>/dev/null | grep -A2 -Ei 'vga|3d|display'
echo; echo "=== package manager ==="; command -v apt || command -v pacman || command -v dnf || command -v zypper
echo; echo "=== disk usage ==="; df -h /
echo; echo "=== recent journal errors ==="; journalctl -p 3 -xb --no-pager 2>/dev/null | tail -n 100
echo; echo "=== dmesg tail ==="; dmesg 2>/dev/null | tail -n 100
"""
    return app.response_class(
        script,
        mimetype="text/x-shellscript",
        headers={"Content-Disposition": "attachment; filename=fixmylinux-diagnose.sh"},
    )


@app.route("/kb", methods=["GET"])
def kb_list():
    entries = load_kb()
    entries.sort(key=lambda e: e["created_at"], reverse=True)
    def worked_badge(e):
        up = e.get("votes_up", 0)
        if up <= 0:
            return ""
        return f'<span class="meta" style="color:#1DB954;">✔ worked for {up}</span>'

    rows = "".join(
        f'<li>'
        f'<a href="/kb/{e["id"]}">{esc(e["problem"][:90])}</a> '
        f'<span class="meta">({esc(e["distro"])})</span>'
        f'{worked_badge(e)}'
        f'{"<span class=flag>⚠️</span>" if e.get("flagged") else ""}'
        f'</li>'
        for e in entries[:300]
    )
    if not rows:
        rows = "<li class='empty'>No solved problems yet — be the first to ask one!</li>"

    return f"""<!DOCTYPE html>
<html lang="en"><head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Solved Problems – FixMyLinux</title>
  <link href="https://fonts.googleapis.com/css2?family=Roboto&display=swap" rel="stylesheet">
  <style>
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{ font-family: Inter, sans-serif; background:#121212; color:#fff; }}
    .header {{ display:flex; justify-content:space-between; align-items:center; padding:20px 40px; border-bottom:1px solid #2a2a2a; }}
    .logo {{ display:flex; align-items:center; gap:12px; font-size:1.4rem; font-weight:700; text-decoration:none; color:white; }}
    nav a {{ text-decoration:none; color:#b3b3b3; margin-left:24px; }}
    nav a:hover {{ color:white; }}
    .hero {{ max-width:900px; margin:40px auto; padding:0 20px; }}
    h2 {{ font-size:2rem; font-weight:700; margin-bottom:10px; }}
    .subtitle {{ color:#b3b3b3; margin-bottom:28px; }}
    ul {{ list-style:none; }}
    li {{ background:#181818; border:1px solid #2d2d2d; border-radius:8px; padding:14px 18px; margin-bottom:10px; display:flex; align-items:center; gap:10px; }}
    li a {{ color:#1DB954; text-decoration:none; font-size:15px; flex:1; }}
    li a:hover {{ text-decoration:underline; }}
    .meta {{ color:#555; font-size:12px; white-space:nowrap; }}
    .flag {{ font-size:14px; }}
    .empty {{ color:#555; background:transparent; border:none; }}
  </style>
</head>
<body>
<header class="header">
  <a class="logo" href="/"><img src="/static/tuix.png" width="36" alt="tux"> FixMyLinux</a>
  <nav>
    <a href="/faq">FAQ</a>
    <a href="/feed">Feedback</a>
  </nav>
</header>
<main class="hero">
  <h2>🏆 Solved Linux Problems</h2>
  <p class="subtitle">Real fixes generated and shared by the community. Click any problem to see the full fix.</p>
  <ul>{rows}</ul>
</main>
</body></html>"""


@app.route("/kb/<kb_id>", methods=["GET"])
def kb_detail(kb_id):
    entries = load_kb()
    entry = next((e for e in entries if e["id"] == kb_id), None)
    if not entry:
        return "Not found", 404

    warn = (
        '<div style="background:#2a1010;border:1px solid #6b1f1f;border-radius:8px;padding:12px 16px;margin-bottom:16px;color:#ff6b6b;">'
        '🚨 <strong>Dangerous command detected</strong> — Read carefully before running anything.</div>'
        if entry.get("flagged") else ""
    )

    sources_html = ""
    if entry.get("sources"):
        tags = "".join(f'<span style="background:#1e1e1e;border:1px solid #2d2d2d;border-radius:6px;padding:3px 10px;font-size:12px;color:#b3b3b3;margin-right:6px;">✔ {esc(s)}</span>' for s in entry["sources"])
        sources_html = f'<div style="margin-bottom:16px;">{tags}</div>'

    why_html = f'<div style="background:#111;border:1px solid #2d2d2d;border-radius:8px;padding:14px 16px;margin-top:12px;color:#ccc;font-size:14px;line-height:1.7;"><strong style="color:#b3b3b3;font-size:12px;text-transform:uppercase;">🔬 Why this happened</strong><br><br>{esc(entry.get("why",""))}</div>' if entry.get("why") else ""

    up = entry.get("votes_up", 0)
    down = entry.get("votes_down", 0)
    community_html = ""
    if up or down:
        community_html = (
            f'<div style="margin-bottom:16px;font-size:13px;color:#1DB954;">'
            f'🟢 Community: worked for {up} user{"s" if up != 1 else ""}'
            + (f", didn't work for {down}" if down else "")
            + "</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="en"><head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(entry['problem'][:60])} – FixMyLinux</title>
  <link href="https://fonts.googleapis.com/css2?family=Roboto&display=swap" rel="stylesheet">
  <style>
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{ font-family: Inter, sans-serif; background:#121212; color:#fff; }}
    .header {{ display:flex; justify-content:space-between; align-items:center; padding:20px 40px; border-bottom:1px solid #2a2a2a; }}
    .logo {{ display:flex; align-items:center; gap:12px; font-size:1.4rem; font-weight:700; text-decoration:none; color:white; }}
    nav a {{ text-decoration:none; color:#b3b3b3; margin-left:24px; }}
    nav a:hover {{ color:white; }}
    .hero {{ max-width:900px; margin:40px auto; padding:0 20px; }}
    a.back {{ color:#b3b3b3; text-decoration:none; font-size:13px; display:inline-block; margin-bottom:20px; }}
    a.back:hover {{ color:white; }}
    h2 {{ font-size:1.6rem; font-weight:700; margin-bottom:8px; line-height:1.4; }}
    .meta {{ color:#555; font-size:12px; margin-bottom:20px; }}
    pre {{ background:#111; border:1px solid #2d2d2d; border-radius:10px; padding:18px; white-space:pre-wrap; font-family:"JetBrains Mono",monospace; font-size:14px; line-height:1.7; }}
    .cta {{ margin-top:24px; background:#181818; border:1px solid #2d2d2d; border-radius:10px; padding:16px 20px; font-size:14px; color:#b3b3b3; }}
    .cta a {{ color:#1DB954; text-decoration:none; }}
    .cta a:hover {{ text-decoration:underline; }}
  </style>
</head>
<body>
<header class="header">
  <a class="logo" href="/"><img src="/static/tuix.png" width="36" alt="tux"> FixMyLinux</a>
  <nav>
    <a href="/faq">FAQ</a>
    <a href="/feed">Feedback</a>
  </nav>
</header>
<main class="hero">
  <a class="back" href="/kb">← All solved problems</a>
  <h2>{esc(entry['problem'])}</h2>
  <p class="meta">Distro: {esc(entry['distro'])} &middot; {esc(entry['created_at'][:10])}</p>
  {warn}
  {community_html}
  {sources_html}
  <pre>{esc(entry['answer'])}</pre>
  {why_html}
  <div class="cta" style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px;">
    <span>Did this fix work for you?
      <button onclick="voteKb('{entry['id']}','up',this)" style="margin-left:8px; background:#1e1e1e; border:1px solid #2d2d2d; color:#b3b3b3; border-radius:6px; padding:4px 10px; cursor:pointer;">👍</button>
      <button onclick="voteKb('{entry['id']}','down',this)" style="background:#1e1e1e; border:1px solid #2d2d2d; color:#b3b3b3; border-radius:6px; padding:4px 10px; cursor:pointer;">👎</button>
    </span>
    <span>Have a similar problem? <a href="/">Ask FixMyLinux →</a></span>
  </div>
  <script>
    async function voteKb(id, dir, btn) {{
      const fd = new FormData(); fd.append('direction', dir);
      await fetch('/vote/' + id, {{ method: 'POST', body: fd }});
      btn.parentElement.innerHTML = 'Thanks for the feedback!';
    }}
  </script>
</main>
</body></html>"""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)