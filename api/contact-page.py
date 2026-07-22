import os
import re
from http.server import BaseHTTPRequestHandler

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TITLE = "Contact Simone Marzullo | The Agency Los Angeles"
DESCRIPTION = "Get in touch with Simone Marzullo, REALTOR® with The Agency in Beverly Hills, to talk buying, selling, or investing in Los Angeles real estate."
CANONICAL_URL = "https://www.marzullore.com/contact"


def replace_first(pattern, replacement, html):
    return re.sub(pattern, replacement, html, count=1)


def build_html():
    with open(os.path.join(PROJECT_ROOT, "index.html"), "r", encoding="utf-8") as f:
        html = f.read()
    html = replace_first(r"<title>.*?</title>", f"<title>{TITLE}</title>", html)
    html = replace_first(
        r'<meta name="description" content="[^"]*">',
        f'<meta name="description" content="{DESCRIPTION}">',
        html,
    )
    html = replace_first(
        r'<link rel="canonical" href="[^"]*">',
        f'<link rel="canonical" href="{CANONICAL_URL}">',
        html,
    )
    html = replace_first(
        r'<meta property="og:title" content="[^"]*">',
        f'<meta property="og:title" content="{TITLE}">',
        html,
    )
    html = replace_first(
        r'<meta property="og:description" content="[^"]*">',
        f'<meta property="og:description" content="{DESCRIPTION}">',
        html,
    )
    html = replace_first(
        r'<meta property="og:url" content="[^"]*">',
        f'<meta property="og:url" content="{CANONICAL_URL}">',
        html,
    )
    return html


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            html = build_html()
        except OSError:
            self.send_error(404)
            return
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass
