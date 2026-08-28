"""Client dashboard, served at /dashboard via a vercel.json rewrite to
/api/dashboard (this file's real path -- same pattern as /off-market ->
api/offmarket.py). Self-contained (own nav/footer/theme boilerplate, copied
from api/offmarket.py) with no cross-file imports, matching the pattern
already proven to work in production for this project.

Auth model: each client gets their own row in the `clients` table (email +
a password Simone assigns via /admin), unlike the off-market page's single
shared password. A successful login sets a signed, expiring cookie whose
payload includes which client it belongs to, so it can't be replayed as
someone else's session. Not linked from the public nav or sitemap --
Simone sends each client a direct link to /dashboard.

Requires the tables from db/schema.sql and a POSTGRES_URL (or DATABASE_URL)
env var pointing at the database, plus SESSION_SECRET.
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
COOKIE_NAME = "client_session"
SESSION_HOURS = 24 * 30  # 30 days -- a listing relationship runs for months.
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
<title>Client Dashboard | Simone Marzullo</title>
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


# ---------------------------------------------------------------------------
# Password hashing (PBKDF2-HMAC-SHA256, stdlib only). Format mirrors
# Django's: "pbkdf2_sha256$<iters>$<salt>$<hash>".
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Signed session cookie. Payload embeds the role and which client it
# belongs to, signed with a dedicated server-only secret (SESSION_SECRET,
# shared with api/team.py and api/admin.py) -- never the client's own
# password.
# ---------------------------------------------------------------------------
def _session_secret():
    return os.environ.get("SESSION_SECRET", "")


def make_session_token(client_id):
    secret = _session_secret()
    expiry = int(time.time()) + SESSION_HOURS * 3600
    payload = f"client:{client_id}:{expiry}"
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
    if len(parts) != 3 or parts[0] != "client":
        return None
    try:
        client_id = int(parts[1])
        expiry = int(parts[2])
    except ValueError:
        return None
    if time.time() >= expiry:
        return None
    return client_id


def get_cookie(cookie_header, name):
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


# ---------------------------------------------------------------------------
# Database access -- every query is parameterized (%s placeholders); user
# input is never formatted directly into SQL text.
# ---------------------------------------------------------------------------
def get_conn():
    dsn = os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    return psycopg2.connect(dsn, connect_timeout=5)


def fetch_client_by_email(conn, email):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, email, password_hash, name, active FROM clients WHERE email = %s",
            (email,),
        )
        return cur.fetchone()


def fetch_dashboard_data(conn, client_id):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, name FROM clients WHERE id = %s AND active = TRUE", (client_id,))
        client = cur.fetchone()
        if not client:
            return None

        cur.execute(
            """SELECT id, address, status, showings_count, emails_sent_count,
                      calls_made_count, texts_sent_count
               FROM listings WHERE client_id = %s ORDER BY created_at DESC""",
            (client_id,),
        )
        listings = cur.fetchall()

        for listing in listings:
            cur.execute(
                """SELECT price, financing_type, close_of_escrow, contingencies, created_at
                   FROM offers WHERE listing_id = %s ORDER BY created_at DESC""",
                (listing["id"],),
            )
            listing["offers"] = cur.fetchall()

            cur.execute(
                "SELECT category, note, created_at FROM feedback_notes WHERE listing_id = %s ORDER BY created_at DESC",
                (listing["id"],),
            )
            notes = cur.fetchall()
            listing["showing_feedback"] = [n for n in notes if n["category"] == "showing"]
            listing["pricing_feedback"] = [n for n in notes if n["category"] in ("pricing_agent", "pricing_buyer")]

        return {"client": client, "listings": listings}


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------
def build_login_html(error=None):
    error_html = f'<div class="om-error" style="display:block">{html.escape(error)}</div>' if error else '<div class="om-error" id="dash-error"></div>'
    body = f"""
<section class="area-hero om-hero-compact">
  <img class="area-hero-img" src="/assets/hero-skyline-day.jpg" alt="Los Angeles skyline at dusk" loading="eager" style="object-position:50% 55%">
  <div class="area-hero-scrim"></div>
  <div class="area-hero-content">
    <div class="area-eyebrow"><span class="area-eyebrow-line"></span><span class="area-eyebrow-text">Client Access</span></div>
    <h1 class="area-h1">Your Listing Dashboard</h1>
    <p class="area-tagline">Sign in with the email and password Simone gave you to check on your listing's progress.</p>
  </div>
</section>

<section class="section" style="text-align:center">
  <div class="wrap" style="max-width:440px">
    <form id="dash-form" novalidate>
      <div class="om-form">
        <label class="om-field">
          <span class="om-field-label">Email</span>
          <input type="email" id="dash-email" class="om-input" required autocomplete="username">
        </label>
        <label class="om-field">
          <span class="om-field-label">Password</span>
          <input type="password" id="dash-password" class="om-input" required autocomplete="current-password">
        </label>
      </div>
      {error_html}
      <button type="submit" class="btn-primary" id="dash-submit" style="width:100%;justify-content:center;margin-top:22px">Sign In</button>
    </form>
    <p style="font-size:.75rem;color:var(--g5);margin-top:20px;line-height:1.7">Don't have login details? <a href="/contact" style="color:var(--white);text-decoration:underline">Contact Simone</a>.</p>
  </div>
</section>

<script>
document.getElementById('dash-form').addEventListener('submit', async function (e) {{
  e.preventDefault();
  const email = document.getElementById('dash-email').value.trim();
  const password = document.getElementById('dash-password').value;
  const errEl = document.getElementById('dash-error');
  const btn = document.getElementById('dash-submit');
  errEl.style.display = 'none';
  btn.textContent = 'Signing in…';
  try {{
    const res = await fetch('/api/dashboard', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{action: 'login', email, password}}),
    }});
    const data = await res.json();
    if (res.ok && data.ok) {{
      window.location.href = '/dashboard';
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


def _stat_tile(label, value):
    return f"""<div class="db-stat">
      <div class="db-stat-value">{value}</div>
      <div class="db-stat-label">{html.escape(label)}</div>
    </div>"""


FEEDBACK_CATEGORY_LABEL = {
    "pricing_agent": "From an agent",
    "pricing_buyer": "From a buyer",
}


def _offer_html(offer):
    price = f"${offer['price']:,.0f}"
    financing_label = "Cash" if offer["financing_type"] == "cash" else "Loan"
    close_label = offer["close_of_escrow"].strftime("%b %-d, %Y") if offer["close_of_escrow"] else "Not specified"
    tags = "".join(f'<span class="db-tag">{html.escape(c)}</span>' for c in (offer["contingencies"] or []))
    if not tags:
        tags = '<span class="db-tag db-tag-muted">No contingencies</span>'
    return f"""<div class="db-offer-row">
      <div class="db-offer-main">
        <span class="db-offer-price">{price}</span>
        <span class="om-status">{financing_label}</span>
      </div>
      <div class="db-offer-meta">Close of escrow: {close_label}</div>
      <div class="db-offer-tags">{tags}</div>
    </div>"""


def _feedback_html(notes, empty_message):
    if not notes:
        return f'<p class="db-empty-note">{html.escape(empty_message)}</p>'
    items = []
    for n in notes:
        sub = FEEDBACK_CATEGORY_LABEL.get(n["category"])
        sub_html = f'<span class="db-note-sub">{sub}</span>' if sub else ""
        items.append(f"""<div class="db-note">
          <div class="db-note-date">{n["created_at"].strftime("%b %-d, %Y")}{sub_html}</div>
          <div class="db-note-text">{html.escape(n["note"])}</div>
        </div>""")
    return "".join(items)


def _listing_html(listing):
    stats = "".join([
        _stat_tile("Showings", listing["showings_count"]),
        _stat_tile("Emails Sent", listing["emails_sent_count"]),
        _stat_tile("Calls Made", listing["calls_made_count"]),
        _stat_tile("Texts Sent", listing["texts_sent_count"]),
        _stat_tile("Offers Received", len(listing["offers"])),
    ])

    offers_html = "".join(_offer_html(o) for o in listing["offers"]) or '<p class="db-empty-note">No offers received yet.</p>'
    showing_feedback_html = _feedback_html(listing["showing_feedback"], "No showing feedback logged yet.")
    pricing_feedback_html = _feedback_html(listing["pricing_feedback"], "No pricing feedback logged yet.")

    return f"""
    <div class="db-listing">
      <div class="db-listing-head">
        <div class="db-listing-address">{html.escape(listing["address"])}</div>
        <span class="om-status">{html.escape(listing["status"])}</span>
      </div>
      <div class="db-stats">{stats}</div>
      <div class="db-section">
        <div class="db-section-title">Offers Received</div>
        {offers_html}
      </div>
      <div class="db-section">
        <div class="db-section-title">Showing Feedback</div>
        {showing_feedback_html}
      </div>
      <div class="db-section">
        <div class="db-section-title">Pricing Feedback</div>
        {pricing_feedback_html}
      </div>
    </div>"""


def build_dashboard_html(data):
    client = data["client"]
    listings = data["listings"]
    name_html = f", {html.escape(client['name'])}" if client.get("name") else ""

    if listings:
        listings_html = "".join(_listing_html(l) for l in listings)
    else:
        listings_html = '<div class="om-empty">No listings on your account yet -- check back once Simone has one set up.</div>'

    body = f"""
<section class="area-hero om-hero-compact">
  <img class="area-hero-img" src="/assets/hero-skyline-day.jpg" alt="Los Angeles skyline at dusk" loading="eager" style="object-position:50% 55%">
  <div class="area-hero-scrim"></div>
  <div class="area-hero-content">
    <div class="area-eyebrow"><span class="area-eyebrow-line"></span><span class="area-eyebrow-text">Client Access</span></div>
    <h1 class="area-h1">Welcome{name_html}</h1>
    <p class="area-tagline">Here's the latest on your listing.</p>
  </div>
</section>

<section class="section">
  <div class="wrap">
    {listings_html}
    <div style="text-align:center;margin-top:36px"><a href="/dashboard?logout=1" class="om-logout">Log out</a></div>
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


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
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
        client_id = verify_session_token(token)
        if not client_id:
            self._send_html(200, build_login_html())
            return

        conn = None
        try:
            conn = get_conn()
            if conn is None:
                self._send_html(503, build_error_html("The dashboard isn't set up yet -- please contact Simone directly."))
                return
            data = fetch_dashboard_data(conn, client_id)
        except Exception as e:
            print(f"dashboard: failed to load data: {e}")
            self._send_html(503, build_error_html("Something went wrong loading your dashboard. Please try again shortly."))
            return
        finally:
            if conn:
                conn.close()

        if not data:
            expired = f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"
            self._send_html(200, build_login_html(), set_cookie=expired)
            return

        self._send_html(200, build_dashboard_html(data))

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
                self._send_json(503, {"ok": False, "error": "The dashboard isn't set up yet -- please contact Simone directly."})
                return
            client = fetch_client_by_email(conn, email)
        except Exception as e:
            print(f"dashboard: login lookup failed: {e}")
            self._send_json(503, {"ok": False, "error": "Something went wrong. Please try again shortly."})
            return
        finally:
            if conn:
                conn.close()

        if not client or not client["active"] or not verify_password(password, client["password_hash"]):
            self._send_json(401, {"ok": False, "error": "Incorrect email or password."})
            return

        cookie = f"{COOKIE_NAME}={make_session_token(client['id'])}; Path=/; Max-Age={SESSION_HOURS * 3600}; HttpOnly; Secure; SameSite=Lax"
        self._send_json(200, {"ok": True}, set_cookie=cookie)

    def log_message(self, fmt, *args):
        pass
