"""Off-market listings, served at /off-market via a vercel.json rewrite to
/api/offmarket (this file's real path -- the login form on the page below
POSTs directly to /api/offmarket, same as every other form on this site
posts straight to its api/*.py function). Also serves the public,
no-login "flyer" page for a single listing at /flyer/<id> (rewritten to
/api/offmarket?flyer=<id>), meant to be shared directly with a specific
buyer via text/email rather than found on the site.

Self-contained (own nav/footer/theme boilerplate, copied from api/areas.py)
with no cross-file imports, matching the pattern already proven to work in
production for this project -- an earlier multi-file version with shared
helpers in a separate module returned 404s in production despite working
locally.

Auth model: individual buyer accounts (table `offmarket_buyers`, managed
from /admin -- see api/portal.py), replacing the old single shared
OFFMARKET_PASSWORD gate. Every active buyer sees the same pool of active
off-market listings; there's no per-buyer assignment. Password hashing
(PBKDF2-HMAC-SHA256) and signed session cookies use the exact same
approach as api/portal.py's client login, duplicated here rather than
imported for the same reason as everything else in this file.

Listings themselves (`offmarket_listings`) are also admin-managed --
Simone adds address/price/details and pastes photo URLs (she hosts photos
elsewhere; there's no file upload here) from /admin, no code change or
redeploy needed to publish a new one.
"""

import base64
import hashlib
import hmac
import html
import json
import os
import re
import smtplib
import time
import urllib.error
import urllib.request
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler

import psycopg2
import psycopg2.extras

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
COOKIE_NAME = "offmarket_session"
SESSION_HOURS = 24
FUB_EVENTS_URL = "https://api.followupboss.com/v1/events"
MAX_BODY_BYTES = 8 * 1024
MAX_FIELD_LEN = 500
MIN_PASSWORD_LEN = 8

NAV_ITEMS = [
    ("AREAS", "/areas"),
    ("OFF-MARKET", "/off-market"),
    ("NEWS", "/news"),
    ("ABOUT", "/about"),
    ("THE AGENCY", "/#the-agency"),
    ("CALCULATOR", "/flipcalculator"),
    ("CONTACT", "/contact"),
    ("LOG IN", "/clientaccess"),
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


def render_page(body_html, title="Off-Market Opportunities | Simone Marzullo"):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{THEME_INIT_SCRIPT}
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>{html.escape(title)}</title>
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
# Password hashing (PBKDF2-HMAC-SHA256, stdlib only) -- identical to
# api/portal.py's, duplicated per this file's no-cross-import rule.
# ---------------------------------------------------------------------------
PBKDF2_ITERATIONS = 600_000


def hash_password(password):
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password, stored_hash):
    try:
        algo, iterations, salt_hex, hash_hex = stored_hash.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def clean(value, max_len=MAX_FIELD_LEN):
    return str(value or "").strip()[:max_len]


# ---------------------------------------------------------------------------
# Session tokens -- HMAC-signed with SESSION_SECRET (same env var api/
# portal.py's client/admin sessions use; this file only ever issues one
# kind of token, for an offmarket_buyers row, so there's no role to encode).
# ---------------------------------------------------------------------------
def make_session_token(buyer_id):
    secret = os.environ.get("SESSION_SECRET", "")
    expiry = int(time.time()) + SESSION_HOURS * 3600
    payload = f"{buyer_id}:{expiry}"
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_session_token(token):
    secret = os.environ.get("SESSION_SECRET", "")
    if not token or not secret or token.count(":") != 2:
        return None
    buyer_id_str, expiry_str, sig = token.split(":")
    payload = f"{buyer_id_str}:{expiry_str}"
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        buyer_id, expiry = int(buyer_id_str), int(expiry_str)
    except ValueError:
        return None
    if time.time() >= expiry:
        return None
    return buyer_id


def get_cookie(cookie_header, name):
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


# ---------------------------------------------------------------------------
# Database (Postgres via Vercel/Supabase) -- same _clean_dsn/get_conn as
# api/portal.py, duplicated per this file's no-cross-import rule.
# ---------------------------------------------------------------------------
_LIBPQ_QUERY_PARAMS = {"sslmode", "connect_timeout", "application_name", "options"}


def _clean_dsn(dsn):
    if "?" not in dsn:
        return dsn
    base, _, query = dsn.partition("?")
    import urllib.parse
    kept = [(k, v) for k, v in urllib.parse.parse_qsl(query, keep_blank_values=True) if k in _LIBPQ_QUERY_PARAMS]
    return base + ("?" + urllib.parse.urlencode(kept) if kept else "")


def get_conn():
    dsn = os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    return psycopg2.connect(_clean_dsn(dsn), connect_timeout=5)


def fetch_offmarket_buyer_by_email(conn, email):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, email, password_hash, name, active FROM offmarket_buyers WHERE email = %s", (email,))
        return cur.fetchone()


def fetch_active_offmarket_listings(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT id, address, area, status, price, beds, baths, sqft, lot_size, description, photo_urls, photo_alt,
                      hide_address, media_link, hide_media_link
               FROM offmarket_listings WHERE active = TRUE ORDER BY created_at DESC"""
        )
        return cur.fetchall()


def fetch_offmarket_listing_by_id(conn, listing_id):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT id, address, area, status, price, beds, baths, sqft, lot_size, description, photo_urls, photo_alt,
                      hide_address, media_link, hide_media_link
               FROM offmarket_listings WHERE id = %s""",
            (listing_id,),
        )
        return cur.fetchone()


def push_to_followupboss(email, name):
    api_key = os.environ.get("FUB_API_KEY")
    if not api_key:
        print("offmarket: FUB_API_KEY is not configured")
        return
    who = name or email
    event_payload = {
        "source": os.environ.get("FUB_SOURCE", "Simone Marzullo Website"),
        "system": os.environ.get("FUB_SYSTEM", "Simone Marzullo Website"),
        "type": "General Inquiry",
        "message": f"{who} logged into the Off-Market Opportunities page.",
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
    try:
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = recipient
        msg.set_content(body)
    except ValueError as e:
        print(f"offmarket: failed to build notification email: {e}")
        return

    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls()
            if username and password:
                server.login(username, password)
            server.send_message(msg)
    except Exception as e:
        print(f"offmarket: failed to send notification email: {e}")


def build_error_html(message):
    body = f"""
<section class="section" style="text-align:center;padding-top:140px">
  <div class="wrap" style="max-width:480px">
    <h1 class="area-h1" style="color:var(--white)">Off-Market Opportunities</h1>
    <p class="area-tagline" style="color:var(--g5);margin-top:16px">{html.escape(message)}</p>
  </div>
</section>
"""
    return render_page(body)


def build_gate_html(error=None):
    error_html = f'<div class="om-error" style="display:block">{html.escape(error)}</div>' if error else '<div class="om-error" id="om-error"></div>'
    body = f"""
<section class="area-hero" style="min-height:52vh">
  <img class="area-hero-img" src="/assets/hero-skyline-day.jpg" alt="Los Angeles skyline at dusk" loading="eager" style="object-position:50% 55%">
  <div class="area-hero-scrim"></div>
  <div class="area-hero-content">
    <div class="area-eyebrow"><span class="area-eyebrow-line"></span><span class="area-eyebrow-text">Private Access</span></div>
    <h1 class="area-h1">Off-Market Opportunities</h1>
    <p class="area-tagline">Exclusive properties shared only with select buyers and investors before they reach the open market. Sign in with the email and password I've given you.</p>
  </div>
</section>

<section class="section" style="text-align:center">
  <div class="wrap" style="max-width:440px">
    <form id="om-form" novalidate>
      <div class="om-form">
        <label class="om-field">
          <span class="om-field-label">Email</span>
          <input type="email" id="om-email" class="om-input" required autocomplete="username">
        </label>
        <label class="om-field">
          <span class="om-field-label">Password</span>
          <input type="password" id="om-password" class="om-input" required autocomplete="current-password">
        </label>
      </div>
      {error_html}
      <button type="submit" class="btn-primary" id="om-submit" style="width:100%;justify-content:center;margin-top:22px">Sign In</button>
    </form>
    <p style="font-size:.75rem;color:var(--g5);margin-top:20px;line-height:1.7">Don't have login details? <a href="/contact" style="color:var(--white);text-decoration:underline">Contact me</a> to request access.</p>
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
  btn.textContent = 'Signing in…';
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


def _listing_card_html(l):
    photos = l.get("photo_urls") or []
    photo = photos[0] if photos else None
    img_html = f'<img class="om-card-img" src="{html.escape(photo)}" alt="{html.escape(l.get("photo_alt") or "")}" loading="lazy">' if photo else '<div class="om-card-img om-card-img-placeholder">Photos coming soon</div>'
    specs = " &middot; ".join(s for s in [
        f"{l['beds']} bd" if l.get("beds") else "",
        f"{l['baths']} ba" if l.get("baths") else "",
        f"{l['sqft']} sqft" if l.get("sqft") else "",
        f"{l['lot_size']} lot" if l.get("lot_size") else "",
    ] if s)
    title = "Address Available Upon Request" if l.get("hide_address") else l["address"]
    return f"""
      <a class="om-card" href="/flyer/{l['id']}">
        {img_html}
        <div class="om-card-body">
          <div class="om-card-kicker">{html.escape(l.get('area') or '')}<span class="om-status">{html.escape(l.get('status') or 'Available')}</span></div>
          <div class="om-card-title">{html.escape(title)}</div>
          <div class="om-card-price">{html.escape(l.get('price') or '')}</div>
          <div class="om-card-specs">{specs}</div>
          {f'<p class="om-card-desc">{html.escape(l["description"])}</p>' if l.get('description') else ''}
        </div>
      </a>"""


def build_listings_html(buyer, listings):
    if listings:
        cards = "".join(_listing_card_html(l) for l in listings)
        listings_html = f'<div class="om-grid">{cards}</div>'
    else:
        listings_html = """<div class="om-empty">No active off-market opportunities right now. Check back soon &mdash; new opportunities are shared here before they reach the open market.</div>"""

    name_html = f", {html.escape(buyer['name'])}" if buyer.get("name") else ""
    body = f"""
<section class="area-hero om-hero-compact">
  <img class="area-hero-img" src="/assets/hero-skyline-day.jpg" alt="Los Angeles skyline at dusk" loading="eager" style="object-position:50% 55%">
  <div class="area-hero-scrim"></div>
  <div class="area-hero-content">
    <div class="area-eyebrow"><span class="area-eyebrow-line"></span><span class="area-eyebrow-text">Private Access</span></div>
    <h1 class="area-h1">Welcome{name_html}</h1>
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


def build_flyer_html(listing):
    hide_address = bool(listing.get("hide_address"))
    display_address = "Address Available Upon Request" if hide_address else listing["address"]
    photo_alt_fallback = listing.get("area") or "Off-market listing photo"
    photo_alt = listing.get("photo_alt") or (photo_alt_fallback if hide_address else listing["address"])

    photos = listing.get("photo_urls") or []
    if photos:
        hero_photo = photos[0]
        gallery = "".join(f'<img class="flyer-gallery-img" src="{html.escape(p)}" alt="{html.escape(photo_alt)}" loading="lazy">' for p in photos[1:])
        gallery_html = f'<div class="flyer-gallery">{gallery}</div>' if gallery else ""
    else:
        hero_photo = None
        gallery_html = ""

    specs = " &middot; ".join(s for s in [
        f"{listing['beds']} bd" if listing.get("beds") else "",
        f"{listing['baths']} ba" if listing.get("baths") else "",
        f"{listing['sqft']} sqft" if listing.get("sqft") else "",
        f"{listing['lot_size']} lot" if listing.get("lot_size") else "",
    ] if s)

    hero_img_html = f'<img class="area-hero-img" src="{html.escape(hero_photo)}" alt="{html.escape(photo_alt)}" loading="eager">' if hero_photo else '<img class="area-hero-img" src="/assets/hero-skyline-day.jpg" alt="Los Angeles skyline at dusk" loading="eager">'

    media_link = listing.get("media_link") or ""
    media_link_html = f'<a href="{html.escape(media_link)}" target="_blank" rel="noopener noreferrer" class="btn-hero-outline" style="border-color:var(--g3);color:var(--white)">View More Photos &amp; Video</a>' if media_link and not listing.get("hide_media_link") else ""

    body = f"""
<section class="area-hero" style="min-height:56vh">
  {hero_img_html}
  <div class="area-hero-scrim"></div>
  <div class="area-hero-content">
    <div class="area-eyebrow"><span class="area-eyebrow-line"></span><span class="area-eyebrow-text">Off-Market Opportunity</span><span class="om-status" style="margin-left:10px">{html.escape(listing.get('status') or 'Available')}</span></div>
    <h1 class="area-h1">{html.escape(display_address)}</h1>
    <p class="area-tagline">{html.escape(listing.get('area') or '')}</p>
  </div>
</section>

<section class="section" style="text-align:center">
  <div class="wrap" style="max-width:640px">
    <div class="flyer-price">{html.escape(listing.get('price') or 'Price upon request')}</div>
    <div class="flyer-specs">{specs}</div>
    {f'<p class="flyer-desc">{html.escape(listing["description"])}</p>' if listing.get('description') else ''}
    {gallery_html}
    <div style="margin-top:32px;display:flex;gap:14px;justify-content:center;flex-wrap:wrap">
      {media_link_html}
      <a href="/contact" class="btn-primary">Ask a Question</a>
    </div>
  </div>
</section>
"""
    title = f"{display_address} | Off-Market | Simone Marzullo"
    return render_page(body, title=title)


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
        params = {}
        for part in query.split("&"):
            if "=" in part:
                k, _, v = part.partition("=")
                params[k] = v

        # The flyer is public by design (meant to be shared directly with
        # someone who doesn't have -- and doesn't need -- a buyer login).
        if "flyer" in params:
            try:
                listing_id = int(params["flyer"])
            except ValueError:
                self._send_html(404, build_error_html("That listing link isn't valid."))
                return
            conn = None
            try:
                conn = get_conn()
                if conn is None:
                    self._send_html(503, build_error_html("This listing isn't available right now -- please contact Simone directly."))
                    return
                listing = fetch_offmarket_listing_by_id(conn, listing_id)
            except Exception as e:
                print(f"offmarket(flyer): failed to load listing: {e}")
                self._send_html(503, build_error_html("Something went wrong loading this listing. Please try again shortly."))
                return
            finally:
                if conn:
                    conn.close()
            if not listing:
                self._send_html(404, build_error_html("That listing isn't available anymore."))
                return
            self._send_html(200, build_flyer_html(listing))
            return

        if "logout=1" in query:
            expired = f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"
            self._send_html(200, build_gate_html(), set_cookie=expired)
            return

        token = get_cookie(self.headers.get("Cookie", ""), COOKIE_NAME)
        buyer_id = verify_session_token(token)
        if not buyer_id:
            self._send_html(200, build_gate_html())
            return

        conn = None
        try:
            conn = get_conn()
            if conn is None:
                self._send_html(503, build_error_html("Off-market access isn't set up yet -- please contact Simone directly."))
                return
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT id, name, active FROM offmarket_buyers WHERE id = %s AND active = TRUE", (buyer_id,))
                buyer = cur.fetchone()
            if not buyer:
                expired = f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"
                self._send_html(200, build_gate_html(), set_cookie=expired)
                return
            listings = fetch_active_offmarket_listings(conn)
        except Exception as e:
            print(f"offmarket: failed to load data: {e}")
            self._send_html(503, build_error_html("Something went wrong loading the listings. Please try again shortly."))
            return
        finally:
            if conn:
                conn.close()
        self._send_html(200, build_listings_html(buyer, listings))

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
        # A deeply nested array/object parses fine under MAX_BODY_BYTES but
        # blows Python's recursion limit -- RecursionError isn't a
        # JSONDecodeError, so it needs its own catch.
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
                self._send_json(503, {"ok": False, "error": "Off-market access isn't set up yet -- please contact Simone directly."})
                return
            buyer = fetch_offmarket_buyer_by_email(conn, email)
        except Exception as e:
            print(f"offmarket: login lookup failed: {e}")
            self._send_json(503, {"ok": False, "error": "Something went wrong. Please try again shortly."})
            return
        finally:
            if conn:
                conn.close()

        if not buyer or not buyer["active"] or not verify_password(password, buyer["password_hash"]):
            self._send_json(401, {"ok": False, "error": "Incorrect email or password."})
            return

        push_to_followupboss(buyer["email"], buyer["name"])
        send_notification_email(
            "Off-Market Access: Buyer Logged In",
            f"{buyer['name'] or buyer['email']} ({buyer['email']}) logged into /off-market.",
        )

        cookie = f"{COOKIE_NAME}={make_session_token(buyer['id'])}; Path=/; Max-Age={SESSION_HOURS * 3600}; HttpOnly; Secure; SameSite=Lax"
        self._send_json(200, {"ok": True}, set_cookie=cookie)

    def log_message(self, fmt, *args):
        pass
