"""Password-gated off-market listings page, served at /off-market via a
vercel.json rewrite to /api/offmarket (this file's real path -- the gate
form on the page below POSTs directly to /api/offmarket, same as every
other form on this site posts straight to its api/*.py function).

Self-contained (own nav/footer/theme boilerplate, copied from api/areas.py)
with no cross-file imports, matching the pattern already proven to work in
production for this project -- an earlier multi-file version with shared
helpers in a separate module returned 404s in production despite working
locally.

Auth model: a single shared password (OFFMARKET_PASSWORD env var, rotatable
in the Vercel dashboard with no code change -- just a redeploy) that Simone
hands out to specific contacts. A visitor enters their email plus that password;
on success their email is logged to FollowUpBoss (tag "Off-Market Access")
and a signed, expiring cookie is set so they don't have to re-enter it on
later visits. The cookie is HMAC-signed using OFFMARKET_PASSWORD itself as
the key, so rotating the password also invalidates every existing session
-- a deliberate side effect that doubles as a way to cut off access broadly.
"""

import hashlib
import hmac
import json
import os
import re
import smtplib
import time
import base64
import urllib.request
import urllib.error
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
COOKIE_NAME = "offmarket_session"
SESSION_HOURS = 24
FUB_EVENTS_URL = "https://api.followupboss.com/v1/events"

# Real, currently-available off-market opportunities. Empty until Simone
# provides live listings -- add entries here (matching this shape) and
# redeploy to publish them; there is no separate admin UI by design.
OFFMARKET_LISTINGS = [
    # {
    #     "address": "123 Example St, Beverly Hills, CA 90210",
    #     "area": "Beverly Hills",
    #     "status": "Available",
    #     "price": "$4,995,000",
    #     "beds": 5, "baths": 5, "sqft": "4,200",
    #     "description": "One-sentence description of the property.",
    #     "photo": "/assets/offmarket/example.jpg",
    #     "photo_alt": "Description of the photo for screen readers",
    # },
]

NAV_ITEMS = [
    ("START", "/home"),
    ("AREAS", "/areas"),
    ("OFF-MARKET", "/off-market"),
    ("NEWS", "/news"),
    ("ABOUT", "/about"),
    ("THE AGENCY", "/#the-agency"),
    ("CALCULATOR", "/flipcalculator"),
    ("CONTACT", "/contact"),
]

THEME_INIT_SCRIPT = """<script>if ('scrollRestoration' in history) history.scrollRestoration = 'manual'; window.scrollTo(0, 0);</script>
<script>window.addEventListener('pageshow', function () { window.scrollTo(0, 0); });</script>
<script>
(function () {
  var choice = localStorage.getItem('themeChoice') || 'auto';
  var resolved = choice === 'auto' ? (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark') : choice;
  document.documentElement.setAttribute('data-theme', resolved);
  document.documentElement.setAttribute('data-theme-choice', choice);
})();
</script>"""

FOOTER_AND_SCRIPTS = """
<div class="mcf" id="mcf">
  <div class="mcf-menu" id="mcfMenu">
    <a class="mcf-item" href="sms:+14243639227" aria-label="Text Simone Marzullo">
      <svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg>
      <span>Text</span>
    </a>
    <a class="mcf-item" href="tel:+14243639227" aria-label="Call Simone Marzullo">
      <svg viewBox="0 0 24 24"><path d="M6.62 10.79a15.05 15.05 0 006.59 6.59l2.2-2.2a1 1 0 011.01-.24c1.12.37 2.33.57 3.58.57a1 1 0 011 1V20a1 1 0 01-1 1C10.61 21 3 13.39 3 4a1 1 0 011-1h3.5a1 1 0 011 1c0 1.25.2 2.46.57 3.58a1 1 0 01-.25 1.01z"/></svg>
      <span>Call</span>
    </a>
  </div>
  <button type="button" class="mcf-toggle" id="mcfToggle" aria-haspopup="true" aria-expanded="false" aria-label="Call or text Simone">
    <svg class="mcf-icon-phone" viewBox="0 0 24 24"><path d="M6.62 10.79a15.05 15.05 0 006.59 6.59l2.2-2.2a1 1 0 011.01-.24c1.12.37 2.33.57 3.58.57a1 1 0 011 1V20a1 1 0 01-1 1C10.61 21 3 13.39 3 4a1 1 0 011-1h3.5a1 1 0 011 1c0 1.25.2 2.46.57 3.58a1 1 0 01-.25 1.01z"/></svg>
    <svg class="mcf-icon-close" viewBox="0 0 24 24"><path d="M18.3 5.71L12 12.01l-6.3-6.3-1.41 1.42 6.29 6.29-6.29 6.29 1.41 1.42L12 14.84l6.3 6.29 1.41-1.42-6.29-6.29 6.29-6.29z"/></svg>
  </button>
</div>
<script>
(function () {
  var fab = document.getElementById('mcf');
  var toggle = document.getElementById('mcfToggle');
  if (!fab || !toggle) return;
  toggle.addEventListener('click', function (e) {
    e.stopPropagation();
    var open = fab.classList.toggle('on');
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  document.addEventListener('click', function (e) {
    if (fab.classList.contains('on') && !fab.contains(e.target)) {
      fab.classList.remove('on');
      toggle.setAttribute('aria-expanded', 'false');
    }
  });
})();
function updateThemePickerUI(choice) {
  document.querySelectorAll('.theme-opt').forEach(function (btn) {
    btn.classList.toggle('active', btn.dataset.themeChoice === choice);
  });
}
function applyThemeChoice(choice) {
  const resolved = choice === 'auto'
    ? (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark')
    : choice;
  document.documentElement.setAttribute('data-theme', resolved);
  document.documentElement.setAttribute('data-theme-choice', choice);
  updateThemePickerUI(choice);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', resolved === 'light' ? '#FFFFFF' : '#000000');
}
function setThemeChoice(choice) {
  localStorage.setItem('themeChoice', choice);
  applyThemeChoice(choice);
}
if (window.matchMedia) {
  window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', function () {
    if ((localStorage.getItem('themeChoice') || 'auto') === 'auto') applyThemeChoice('auto');
  });
}
updateThemePickerUI(document.documentElement.getAttribute('data-theme-choice') || 'auto');
function toggleMobileNav() {
  const menu = document.getElementById('nav-mobile');
  const btn = document.querySelector('.nav-toggle');
  const willOpen = !menu.classList.contains('on');
  menu.classList.toggle('on', willOpen);
  btn.setAttribute('aria-expanded', String(willOpen));
}
function closeMobileNav() {
  document.getElementById('nav-mobile').classList.remove('on');
  document.querySelector('.nav-toggle').setAttribute('aria-expanded', 'false');
}
</script>
"""


def _theme_picker_html():
    return """<div class="theme-picker" role="group" aria-label="Theme">
      <button type="button" class="theme-opt" data-theme-choice="light" onclick="setThemeChoice('light')" aria-label="Light theme">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M2 12h2M20 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"></path></svg>
      </button>
      <button type="button" class="theme-opt" data-theme-choice="dark" onclick="setThemeChoice('dark')" aria-label="Dark theme">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>
      </button>
      <button type="button" class="theme-opt" data-theme-choice="auto" onclick="setThemeChoice('auto')" aria-label="Match device setting">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"></circle><path d="M12 3a9 9 0 010 18z" fill="currentColor" stroke="none"></path></svg>
      </button>
    </div>"""


def _nav_html():
    links = [f'<li><a href="{href}">{label}</a></li>' for label, href in NAV_ITEMS]
    mobile_links = [f'<a href="{href}">{label.title()}</a>' for label, href in NAV_ITEMS]
    return f"""<nav id="nav">
  <a href="/" class="nav-brand" aria-label="Go to homepage">
    <img src="/assets/agency-logo.png" alt="The Agency" class="nav-agency-logo" onerror="this.style.display='none'">
    <div class="nav-brand-text">
      <span class="nav-name">Simone Marzullo</span>
      <span class="nav-sub">REALTOR® · The Agency · Los Angeles</span>
    </div>
  </a>
  <ul class="nav-links">
    {''.join(links)}
  </ul>
  <a href="/home" class="nav-cta">Get Started</a>
  <button type="button" class="nav-toggle" aria-expanded="false" aria-label="Menu" onclick="toggleMobileNav()">
    <span></span><span></span><span></span>
  </button>
</nav>
<div class="nav-mobile" id="nav-mobile">
  {''.join(mobile_links)}
  <a href="/home" class="nav-cta">Get Started</a>
  <div class="nav-mobile-theme-row">
    {_theme_picker_html()}
  </div>
</div>"""


def _footer_html():
    return f"""<footer>
  <div class="footer-inner">
    <div>
      <img src="/assets/agency-logo.png" alt="The Agency" style="height:28px;margin-bottom:8px" onerror="this.style.display='none';document.getElementById('f-agency-text').style.display='block'">
      <div class="f-agency-name" id="f-agency-text" style="display:none">The Agency</div>
      <div class="f-line">DRE# 01904054</div>
      <div class="f-line">331 Foothill Rd. #100</div>
      <div class="f-line">Beverly Hills, CA 90210</div>
    </div>
    <div class="f-legal">
      <div class="f-eq">⌂</div>
      <div class="f-copy">Equal Housing Opportunity<br>© 2026 Simone Marzullo. All rights reserved.<br>Information deemed reliable but not guaranteed.<br>CA DRE# 02174253</div>
      <div class="f-copy" style="margin-top:10px"><a href="/privacy.html" style="color:var(--g5);text-decoration:underline">Privacy Policy</a></div>
    </div>
  </div>
  <div class="f-disclaimer">Simone Marzullo | REALTOR® | DRE#02174253 is a real estate salesperson licensed by the state of California affiliated with The Agency. The Agency is a real estate broker licensed by the state of California and abides by equal housing opportunity laws. All material presented herein is intended for informational purposes only. Information is compiled from sources deemed reliable but is subject to errors, omissions, changes in price, condition, sale, or withdrawal without notice. No statement is made as to accuracy of any description. All measurements and square footages are approximate. This is not intended to solicit property already listed. Nothing herein shall be construed as legal, accounting or other professional advice outside the realm of real estate brokerage.</div>
  <div class="theme-toggle-row">
    {_theme_picker_html()}
  </div>
</footer>"""


def render_page(body_html):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{THEME_INIT_SCRIPT}
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>Off-Market Opportunities | Simone Marzullo</title>
<meta name="robots" content="noindex, nofollow">
<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">
<link rel="icon" type="image/png" sizes="32x32" href="/assets/favicon-32.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=EB+Garamond:ital,wght@0,400;0,500;1,400;1,500&family=Inter:wght@300;400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/assets/areas.css">
</head>
<body>
{_nav_html()}
{body_html}
{_footer_html()}
{FOOTER_AND_SCRIPTS}
</body>
</html>"""


def build_gate_html(error=None):
    error_html = f'<div class="om-error" style="display:block">{error}</div>' if error else '<div class="om-error" id="om-error"></div>'
    body = f"""
<section class="area-hero" style="min-height:52vh">
  <img class="area-hero-img" src="/assets/hero-skyline-day.jpg" alt="Los Angeles skyline at dusk" loading="eager" style="object-position:50% 55%">
  <div class="area-hero-scrim"></div>
  <div class="area-hero-content">
    <div class="area-eyebrow"><span class="area-eyebrow-line"></span><span class="area-eyebrow-text">Private Access</span></div>
    <h1 class="area-h1">Off-Market Opportunities</h1>
    <p class="area-tagline">Exclusive properties shared only with select buyers and investors before they reach the open market. Enter your email and the access code I've given you.</p>
  </div>
</section>

<section class="section" style="text-align:center">
  <div class="wrap" style="max-width:440px">
    <form id="om-form" novalidate>
      <div class="om-form">
        <label class="om-field">
          <span class="om-field-label">Email</span>
          <input type="email" id="om-email" class="om-input" required autocomplete="email">
        </label>
        <label class="om-field">
          <span class="om-field-label">Access Code</span>
          <input type="password" id="om-password" class="om-input" required autocomplete="off">
        </label>
      </div>
      {error_html}
      <button type="submit" class="btn-primary" id="om-submit" style="width:100%;justify-content:center;margin-top:22px">Enter</button>
    </form>
    <p style="font-size:.75rem;color:var(--g5);margin-top:20px;line-height:1.7">Don't have an access code? <a href="/contact" style="color:var(--white);text-decoration:underline">Contact me</a> to request one.</p>
  </div>
</section>

<script>
document.getElementById('om-form').addEventListener('submit', async function (e) {{
  e.preventDefault();
  const email = document.getElementById('om-email').value.trim();
  const password = document.getElementById('om-password').value;
  const errEl = document.getElementById('om-error');
  const btn = document.getElementById('om-submit');
  errEl.style.display = 'none';
  btn.textContent = 'Checking…';
  try {{
    const res = await fetch('/api/offmarket', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{email, password}}),
    }});
    const data = await res.json();
    if (res.ok && data.ok) {{
      window.location.href = '/off-market';
    }} else {{
      errEl.textContent = data.error || 'Something went wrong. Please try again.';
      errEl.style.display = 'block';
      btn.textContent = 'Enter';
    }}
  }} catch (err) {{
    errEl.textContent = 'Network error. Please try again.';
    errEl.style.display = 'block';
    btn.textContent = 'Enter';
  }}
}});
</script>
"""
    return render_page(body)


def build_listings_html():
    if OFFMARKET_LISTINGS:
        cards = "".join(f"""
      <div class="om-card">
        {f'<img class="om-card-img" src="{l["photo"]}" alt="{l.get("photo_alt", "")}" loading="lazy">' if l.get('photo') else '<div class="om-card-img om-card-img-placeholder">Photos coming soon</div>'}
        <div class="om-card-body">
          <div class="om-card-kicker">{l.get('area', '')}<span class="om-status">{l.get('status', 'Available')}</span></div>
          <div class="om-card-title">{l['address']}</div>
          <div class="om-card-price">{l['price']}</div>
          <div class="om-card-specs">{l.get('beds', '')} bd &middot; {l.get('baths', '')} ba &middot; {l.get('sqft', '')} sqft</div>
          {f'<p class="om-card-desc">{l["description"]}</p>' if l.get('description') else ''}
        </div>
      </div>""" for l in OFFMARKET_LISTINGS)
        listings_html = f'<div class="om-grid">{cards}</div>'
    else:
        listings_html = """<div class="om-empty">No active off-market opportunities right now. Check back soon &mdash; new opportunities are shared here before they reach the open market.</div>"""

    body = f"""
<section class="area-hero om-hero-compact">
  <img class="area-hero-img" src="/assets/hero-skyline-day.jpg" alt="Los Angeles skyline at dusk" loading="eager" style="object-position:50% 55%">
  <div class="area-hero-scrim"></div>
  <div class="area-hero-content">
    <div class="area-eyebrow"><span class="area-eyebrow-line"></span><span class="area-eyebrow-text">Private Access</span></div>
    <h1 class="area-h1">Off-Market Opportunities</h1>
    <p class="area-tagline">Exclusive properties shared only with the buyers and investors on this list.</p>
  </div>
</section>

<section class="section">
  <div class="wrap">
    {listings_html}
    <div style="text-align:center;margin-top:36px"><a href="/off-market?logout=1" class="om-logout">Log out</a></div>
  </div>
</section>
"""
    return render_page(body)


def make_session_token():
    secret = os.environ.get("OFFMARKET_PASSWORD", "")
    expiry = int(time.time()) + SESSION_HOURS * 3600
    payload = str(expiry)
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(token):
    secret = os.environ.get("OFFMARKET_PASSWORD", "")
    if not token or not secret or "." not in token:
        return False
    payload, _, sig = token.partition(".")
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return False
    try:
        expiry = int(payload)
    except ValueError:
        return False
    return time.time() < expiry


def get_cookie(cookie_header, name):
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


def push_to_followupboss(email):
    api_key = os.environ.get("FUB_API_KEY")
    if not api_key:
        print("offmarket: FUB_API_KEY is not configured")
        return
    event_payload = {
        "source": os.environ.get("FUB_SOURCE", "Simone Marzullo Website"),
        "system": os.environ.get("FUB_SYSTEM", "Simone Marzullo Website"),
        "type": "General Inquiry",
        "message": "Entered the access code for the Off-Market Opportunities page.",
        "person": {"emails": [{"value": email}], "tags": ["Off-Market Access", "Website Lead"]},
    }
    req = urllib.request.Request(
        FUB_EVENTS_URL,
        data=json.dumps(event_payload).encode("utf-8"),
        method="POST",
    )
    auth = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("ascii")
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Content-Type", "application/json")
    system_name = os.environ.get("FUB_SYSTEM")
    system_key = os.environ.get("FUB_SYSTEM_KEY")
    if system_name and system_key:
        req.add_header("X-System", system_name)
        req.add_header("X-System-Key", system_key)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        print(f"offmarket: FollowUpBoss API error {e.code}: {e.read().decode('utf-8', errors='replace')}")
    except Exception as e:
        print(f"offmarket: unexpected error calling FollowUpBoss: {e}")


def send_notification_email(subject, body):
    host = os.environ.get("SMTP_HOST")
    if not host:
        return
    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ.get("SMTP_USERNAME")
    password = os.environ.get("SMTP_PASSWORD")
    sender = os.environ.get("SMTP_FROM") or username or "noreply@simonemarzullo.com"
    recipient = os.environ.get("NOTIFY_TO", "Simone@SimoneMarzullo.com")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls()
            if username and password:
                server.login(username, password)
            server.send_message(msg)
    except Exception as e:
        print(f"offmarket: failed to send notification email: {e}")


class handler(BaseHTTPRequestHandler):
    def _send_html(self, status, html, set_cookie=None):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status, payload, set_cookie=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        query = self.path.partition("?")[2]
        if "logout=1" in query:
            expired = f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"
            self._send_html(200, build_gate_html(), set_cookie=expired)
            return

        token = get_cookie(self.headers.get("Cookie", ""), COOKIE_NAME)
        if verify_session_token(token):
            self._send_html(200, build_listings_html())
        else:
            self._send_html(200, build_gate_html())

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            length = 0
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "Invalid request."})
            return
        if not isinstance(data, dict):
            self._send_json(400, {"ok": False, "error": "Invalid request."})
            return

        email = str(data.get("email", "")).strip()
        password = str(data.get("password", ""))

        if not email or not EMAIL_RE.match(email):
            self._send_json(400, {"ok": False, "error": "Enter a valid email address."})
            return

        expected_password = os.environ.get("OFFMARKET_PASSWORD")
        if not expected_password:
            print("offmarket: OFFMARKET_PASSWORD is not configured")
            self._send_json(503, {"ok": False, "error": "Off-market access isn't set up yet -- please contact Simone directly."})
            return
        if not hmac.compare_digest(password.encode("utf-8"), expected_password.encode("utf-8")):
            self._send_json(401, {"ok": False, "error": "Incorrect access code."})
            return

        push_to_followupboss(email)
        send_notification_email(
            "Off-Market Access Granted",
            f"{email} entered the off-market access code and was granted access to /off-market.",
        )

        cookie = f"{COOKIE_NAME}={make_session_token()}; Path=/; Max-Age={SESSION_HOURS * 3600}; HttpOnly; Secure; SameSite=Lax"
        self._send_json(200, {"ok": True}, set_cookie=cookie)

    def log_message(self, fmt, *args):
        pass
