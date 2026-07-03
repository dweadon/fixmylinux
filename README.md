<h1 align="center">FixMyLinux</h1>
<p align="center">
  <img src="https://img.shields.io/badge/FixMyLinux-v4.1-green">
</p>
<p align="center">
# FixMyLinux

AI-powered Linux troubleshooting tool. Paste an error, log, or terminal output and get back a structured fix with sources, a confidence rating, and a danger check for destructive commands.

Live at: https://fixmylinux.up.railway.app

## How it works

1. User submits a problem description (and optionally a log file, system profile, and mode) through the form on the site.
2. The backend builds a prompt combining the problem, system context, and any uploaded log, then sends it to Groq (`llama-3.3-70b-versatile`).
3. The model returns a fixed JSON structure: fix, why, learn, confidence, sources, dangerous commands, beginner explanation.
4. The backend runs a regex check over the fix for destructive commands (`rm -rf /`, raw `dd` to a device, fork bombs, etc.) as a backstop independent of what the model reports.
5. The raw confidence number is converted into a tier (High / Medium / Needs verification) based on confidence score, number of cited sources, and whether anything was flagged as dangerous.
6. If the user opted in, the fix is saved to the knowledge base and becomes browsable at `/kb`.
7. A conversation ID is returned so the user can send a follow-up ("I tried it, didn't work") without restating context — the backend keeps the message history in memory and re-queries the model.
8. Votes (worked / didn't work) are written back to the KB entry on disk.

## Using the site

- Go to the homepage, paste your problem (or attach a log file), optionally set your system profile and mode, and click "Fix it!"
- Solved problems are browsable at `/kb`
- `/diagnose.sh` downloads a read-only diagnostic script you can run and paste the output back in
- `/faq` and `/feed` for questions and feedback



## Running your own instance

Requirements: Python 3.9+, a Groq API key.

```bash
pip install flask flask-cors requests
export GROQ_API_KEY="your-key-here"
python flaskiks.py
```

Runs on `http://localhost:8080` (or `$PORT` if set). Deployed version runs on Railway.

## Routes

| Route | Method | Description |
|---|---|---|
| `/` | GET | Main UI |
| `/ask` | POST | Submit a problem, get a fix |
| `/followup` | POST | Continue an existing conversation |
| `/vote/<kb_id>` | POST | Upvote/downvote a KB entry |
| `/diagnose.sh` | GET | Download the read-only diagnostic script |
| `/kb` | GET | Browse solved problems |
| `/kb/<kb_id>` | GET | View one solved problem |
| `/faq` | GET | FAQ page |
| `/feed` | GET, POST | View/submit feedback |
| `/save` | POST | Log a raw problem submission |

### `/ask` request fields

| Field | Required | Notes |
|---|---|---|
| `problem` | yes | The error, log, or description |
| `distro` | no | Default `Any` |
| `mode` | no | `normal`, `beginner`, or `learning` |
| `profile_distro`, `profile_de`, `profile_gpu`, `profile_pm` | no | Persisted system profile |
| `extra_info` | no | Freeform extra context |
| `log_file` | no | `.log`/`.txt` upload, truncated to ~12,000 chars |
| `share_publicly` | no | Save to public KB, default on |

### `/ask` response

```json
{
  "fix": "...",
  "why": "...",
  "learn": "...",
  "confidence": 92,
  "confidence_info": {
    "label": "High confidence",
    "level": "high",
    "basis": ["Arch Wiki", "Ubuntu Documentation"]
  },
  "sources": ["Arch Wiki", "Ubuntu Documentation"],
  "dangerous_commands": [],
  "flagged": false,
  "beginner_explanation": "...",
  "kb_id": "a1b2c3d4",
  "conversation_id": "f9e8d7c6b5a4"
}
```

## Limitations

- Follow-up conversations are stored in memory and are lost on server restart / redeploy.
- The knowledge base is a flat JSON Lines file; every vote rewrites the whole file. Fine for current scale, not for heavy traffic.
- No package-level cross-checking (e.g. verifying `mesa`/`vulkan`/`nvidia` state) — would require local shell access or a hosted ingestion service.
- Dangerous-command detection is regex-based. It's a backstop, not a guarantee — read a fix before running it.


