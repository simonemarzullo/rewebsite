"""Team member resource hub, served at /team via a vercel.json rewrite to
/api/team (this file's real path). Self-contained, no cross-file imports,
matching every other page in this project. Not linked from the public nav
or sitemap -- Simone sends each team member a direct link.

Auth model: each team member gets their own row in the `team_members` table
(email + a password Simone assigns via /admin). A successful login sets a
signed, expiring cookie. Once in, they see a shared grid of resource tiles
(links -- Google Drive script PDFs, other tools) that Simone manages from
/admin; every team member sees the same tiles.

Requires the tables from db/schema.sql, POSTGRES_URL/DATABASE_URL, and
SESSION_SECRET (same secret used by api/dashboard.py and api/admin.py).
"""

import hashlib
import hmac
import html
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler

import psycopg2
import psycopg2.extras

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
COOKIE_NAME = "team_session"
SESSION_HOURS = 24 * 30
MAX_BODY_BYTES = 8 * 1024

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
<title>Team Resources | Simone Marzullo</title>
<meta name="robots" content="noindex, nofollow">
<meta name="theme-color" content="#000000">
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


PBKDF2_ITERATIONS = 600_000


def verify_password(password, stored_hash):
    try:
        algo, iterations, salt_hex, hash_hex = stored_hash.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
    return hmac.compare_digest(dk, expected)


def _session_secret():
    return os.environ.get("SESSION_SECRET", "")


def make_session_token(team_member_id):
    secret = _session_secret()
    expiry = int(time.time()) + SESSION_HOURS * 3600
    payload = f"team:{team_member_id}:{expiry}"
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(token):
    secret = _session_secret()
    if not token or not secret or "." not in token:
        return None
    payload, _, sig = token.rpartition(".")
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    parts = payload.split(":")
    if len(parts) != 3 or parts[0] != "team":
        return None
    try:
        team_member_id = int(parts[1])
        expiry = int(parts[2])
    except ValueError:
        return None
    if time.time() >= expiry:
        return None
    return team_member_id


def get_cookie(cookie_header, name):
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


def get_conn():
    dsn = os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    return psycopg2.connect(dsn, connect_timeout=5)


def fetch_team_member_by_email(conn, email):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, email, password_hash, name, active FROM team_members WHERE email = %s",
            (email,),
        )
        return cur.fetchone()


def fetch_team_member(conn, team_member_id):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, name FROM team_members WHERE id = %s AND active = TRUE", (team_member_id,))
        return cur.fetchone()


def fetch_resource_tiles(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT title, description, url FROM resource_tiles WHERE active = TRUE ORDER BY sort_order, created_at"
        )
        return cur.fetchall()


def build_login_html(error=None):
    error_html = f'<div class="om-error" style="display:block">{html.escape(error)}</div>' if error else '<div class="om-error" id="team-error"></div>'
    body = f"""
<section class="area-hero om-hero-compact">
  <img class="area-hero-img" src="/assets/hero-skyline-day.jpg" alt="Los Angeles skyline at dusk" loading="eager" style="object-position:50% 55%">
  <div class="area-hero-scrim"></div>
  <div class="area-hero-content">
    <div class="area-eyebrow"><span class="area-eyebrow-line"></span><span class="area-eyebrow-text">Team Access</span></div>
    <h1 class="area-h1">Team Resources</h1>
    <p class="area-tagline">Sign in with the email and password Simone gave you.</p>
  </div>
</section>

<section class="section" style="text-align:center">
  <div class="wrap" style="max-width:440px">
    <form id="team-form" novalidate>
      <div class="om-form">
        <label class="om-field">
          <span class="om-field-label">Email</span>
          <input type="email" id="team-email" class="om-input" required autocomplete="username">
        </label>
        <label class="om-field">
          <span class="om-field-label">Password</span>
          <input type="password" id="team-password" class="om-input" required autocomplete="current-password">
        </label>
      </div>
      {error_html}
      <button type="submit" class="btn-primary" id="team-submit" style="width:100%;justify-content:center;margin-top:22px">Sign In</button>
    </form>
  </div>
</section>

<script>
document.getElementById('team-form').addEventListener('submit', async function (e) {{
  e.preventDefault();
  const email = document.getElementById('team-email').value.trim();
  const password = document.getElementById('team-password').value;
  const errEl = document.getElementById('team-error');
  const btn = document.getElementById('team-submit');
  errEl.style.display = 'none';
  btn.textContent = 'Signing in…';
  try {{
    const res = await fetch('/api/team', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{action: 'login', email, password}}),
    }});
    const data = await res.json();
    if (res.ok && data.ok) {{
      window.location.href = '/team';
    }} else {{
      errEl.textContent = data.error || 'Something went wrong. Please try again.';
      errEl.style.display = 'block';
      btn.textContent = 'Sign In';
    }}
  }} catch (err) {{
    errEl.textContent = 'Network error. Please try again.';
    errEl.style.display = 'block';
    btn.textContent = 'Sign In';
  }}
}});
</script>
"""
    return render_page(body)


def build_hub_html(team_member, tiles):
    name_html = f", {html.escape(team_member['name'])}" if team_member.get("name") else ""

    if tiles:
        cards = "".join(f"""
      <a class="om-card" href="{html.escape(t['url'])}" target="_blank" rel="noopener noreferrer">
        <div class="om-card-body">
          <div class="om-card-title">{html.escape(t['title'])}</div>
          {f'<p class="om-card-desc">{html.escape(t["description"])}</p>' if t['description'] else ''}
          <span class="om-card-link">Open ↗</span>
        </div>
      </a>""" for t in tiles)
        tiles_html = f'<div class="om-grid om-grid-links">{cards}</div>'
    else:
        tiles_html = '<div class="om-empty">No resources added yet -- check back soon.</div>'

    body = f"""
<section class="area-hero om-hero-compact">
  <img class="area-hero-img" src="/assets/hero-skyline-day.jpg" alt="Los Angeles skyline at dusk" loading="eager" style="object-position:50% 55%">
  <div class="area-hero-scrim"></div>
  <div class="area-hero-content">
    <div class="area-eyebrow"><span class="area-eyebrow-line"></span><span class="area-eyebrow-text">Team Access</span></div>
    <h1 class="area-h1">Welcome{name_html}</h1>
    <p class="area-tagline">Scripts, tools, and resources for the MarzulloRE team.</p>
  </div>
</section>

<section class="section">
  <div class="wrap">
    {tiles_html}
    <div style="text-align:center;margin-top:36px"><a href="/team?logout=1" class="om-logout">Log out</a></div>
  </div>
</section>
"""
    return render_page(body)


def build_error_html(message):
    body = f"""
<section class="section" style="text-align:center;padding-top:140px">
  <div class="wrap"><p class="om-empty">{html.escape(message)}</p></div>
</section>
"""
    return render_page(body)


class handler(BaseHTTPRequestHandler):
    def _send_html(self, status, page_html, set_cookie=None):
        body = page_html.encode("utf-8")
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
            self._send_html(200, build_login_html(), set_cookie=expired)
            return

        token = get_cookie(self.headers.get("Cookie", ""), COOKIE_NAME)
        team_member_id = verify_session_token(token)
        if not team_member_id:
            self._send_html(200, build_login_html())
            return

        conn = None
        try:
            conn = get_conn()
            if conn is None:
                self._send_html(503, build_error_html("The team hub isn't set up yet -- please contact Simone directly."))
                return
            team_member = fetch_team_member(conn, team_member_id)
            tiles = fetch_resource_tiles(conn) if team_member else []
        except Exception as e:
            print(f"team: failed to load data: {e}")
            self._send_html(503, build_error_html("Something went wrong loading this page. Please try again shortly."))
            return
        finally:
            if conn:
                conn.close()

        if not team_member:
            expired = f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"
            self._send_html(200, build_login_html(), set_cookie=expired)
            return

        self._send_html(200, build_hub_html(team_member, tiles))

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            length = 0
        if length > MAX_BODY_BYTES:
            self._send_json(413, {"ok": False, "error": "Payload too large"})
            return
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, RecursionError):
            self._send_json(400, {"ok": False, "error": "Invalid request."})
            return
        if not isinstance(data, dict):
            self._send_json(400, {"ok": False, "error": "Invalid request."})
            return

        email = str(data.get("email", "")).strip().lower()
        password = str(data.get("password", ""))

        if not email or not EMAIL_RE.match(email):
            self._send_json(400, {"ok": False, "error": "Enter a valid email address."})
            return
        if not password:
            self._send_json(400, {"ok": False, "error": "Enter your password."})
            return

        conn = None
        try:
            conn = get_conn()
            if conn is None:
                self._send_json(503, {"ok": False, "error": "The team hub isn't set up yet -- please contact Simone directly."})
                return
            team_member = fetch_team_member_by_email(conn, email)
        except Exception as e:
            print(f"team: login lookup failed: {e}")
            self._send_json(503, {"ok": False, "error": "Something went wrong. Please try again shortly."})
            return
        finally:
            if conn:
                conn.close()

        if not team_member or not team_member["active"] or not verify_password(password, team_member["password_hash"]):
            self._send_json(401, {"ok": False, "error": "Incorrect email or password."})
            return

        cookie = f"{COOKIE_NAME}={make_session_token(team_member['id'])}; Path=/; Max-Age={SESSION_HOURS * 3600}; HttpOnly; Secure; SameSite=Lax"
        self._send_json(200, {"ok": True}, set_cookie=cookie)

    def log_message(self, fmt, *args):
        pass
