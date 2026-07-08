from http.server import BaseHTTPRequestHandler
import json
import os
import urllib.request
import urllib.error

GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

MAX_HISTORY_TURNS = 12
MAX_MESSAGE_LEN = 2000

SYSTEM_PROMPT = """You are the on-site assistant for Simone Marzullo, a REALTOR® with The Agency in Beverly Hills, California.

About Simone: 10+ years working the Los Angeles real estate market (Beverly Hills to Malibu), speaks English and Italian, works with buyers, sellers, and investors -- from first-time homebuyers to high-net-worth individuals and developers. DRE# 02174253. Office: The Agency, 331 Foothill Rd. #100, Beverly Hills, CA 90210. Phone/text: 310-696-6596. Email: Simone@SimoneMarzullo.com.

About The Agency: a global luxury real estate brokerage with 130+ offices in 13+ countries. Named the No. 1 Top Luxury Brokerage (Inman Golden I Awards) and No. 1 on the RealTrends Top 50 by average sales price ($2.5M). On the Inc. 5000 list of America's fastest-growing private companies for 7 years running.

What this website lets visitors do: (1) Sell -- list on the market, sell privately (off-market), or get a cash offer; (2) Buy -- a new home, an income property, or a flip/development project; (3) Home Valuation -- a free, no-obligation estimate request (this is lead capture only, not an automated instant estimate).

Your job: answer general questions about Simone, The Agency, the buying/selling process, and the LA real estate market in a warm, concise, professional tone (2-4 sentences, no long essays). When a visitor shows interest in selling, buying, or getting a valuation, encourage them to use the Sell / Buy / Valuation buttons above this chat, or the contact form further down the page -- don't try to collect their full lead info yourself in the chat window.

Important limits: you do not have access to live MLS listings, real-time prices, or property records -- never invent specific listings, prices, or availability. You are not able to give legal, tax, or financial advice -- for those, tell the visitor Simone (or their own advisor) is the right person to ask. If asked something unrelated to real estate or Simone's services, politely decline and steer the conversation back to how you can help with buying, selling, or valuations.

Formatting: reply in plain text only -- no markdown (no asterisks, no bullet points, no headers). The chat window displays raw text exactly as written."""


def clean(value):
    return str(value).strip() if value is not None else ""


def build_contents(history):
    contents = []
    for turn in history[-MAX_HISTORY_TURNS:]:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role")
        text = clean(turn.get("text"))[:MAX_MESSAGE_LEN]
        if role not in ("user", "model") or not text:
            continue
        contents.append({"role": role, "parts": [{"text": text}]})
    return contents


def call_gemini(history):
    """Calls the Gemini API. Returns (reply_text, error_message) -- exactly
    one of the two is set. Never raises."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("chat: GEMINI_API_KEY is not configured")
        return None, "Chat isn't set up yet -- please use the contact form below."

    contents = build_contents(history)
    if not contents or contents[-1]["role"] != "user":
        return None, "No message to respond to."

    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": 500,
            "temperature": 0.6,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    req = urllib.request.Request(
        f"{GEMINI_URL}?key={api_key}",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        print(f"chat: Gemini API error {e.code}: {err_body}")
        return None, "Sorry, I'm having trouble connecting right now. Please try again shortly."
    except Exception as e:
        print(f"chat: unexpected error calling Gemini: {e}")
        return None, "Sorry, I'm having trouble connecting right now. Please try again shortly."

    candidates = body.get("candidates") or []
    if not candidates:
        return None, "I'm not able to help with that -- feel free to ask something else, or use the contact form below."

    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        return None, "I didn't quite catch that -- could you rephrase?"
    return text, None


class handler(BaseHTTPRequestHandler):
    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            length = 0
        raw = self.rfile.read(length) if length else b""

        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "Invalid JSON body"})
            return
        if not isinstance(data, dict):
            self._send_json(400, {"ok": False, "error": "Invalid payload"})
            return

        history = data.get("history")
        if not isinstance(history, list) or not history:
            self._send_json(400, {"ok": False, "error": "No message provided"})
            return

        reply, error = call_gemini(history)
        if reply:
            self._send_json(200, {"ok": True, "reply": reply})
        else:
            self._send_json(200, {"ok": False, "error": error})

    def do_GET(self):
        self._send_json(405, {"ok": False, "error": "Method not allowed"})
