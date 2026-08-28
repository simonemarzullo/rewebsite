"""Admin panel, served at /admin via a vercel.json rewrite to /api/admin
(this file's real path). Self-contained, no cross-file imports, matching
every other page in this project. Not linked from the public nav or
sitemap.

Auth model: a single shared password (ADMIN_PASSWORD env var, same pattern
as /off-market's OFFMARKET_PASSWORD) -- there's only one admin (Simone).
From here she creates client and team-member accounts (email + a password
she assigns), manages each client's listings/offers/feedback (shown on
/dashboard), manages the shared Team Resource Hub tiles (shown on /team),
and manages the admin-editable contingency-type list used on offers.

"Cancel account" sets active=false rather than deleting -- their data
stays intact and shows up in the History filter, never gone for good.

Requires the same tables as api/dashboard.py and api/team.py
(db/schema.sql) and the same POSTGRES_URL/DATABASE_URL env var, plus
ADMIN_PASSWORD and SESSION_SECRET.
"""

import hashlib
import hmac
import html
import json
import os
import re
import time
from datetime import date
from http.server import BaseHTTPRequestHandler

import psycopg2
import psycopg2.errors
import psycopg2.extras

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
COOKIE_NAME = "admin_session"
SESSION_HOURS = 24
MAX_BODY_BYTES = 16 * 1024
MAX_FIELD_LEN = 500
MIN_PASSWORD_LEN = 8

LISTING_STATUSES = ["Active", "Under Contract", "Sold", "Expired", "Withdrawn"]
FEEDBACK_CATEGORIES = {"showing": "Showing Feedback", "pricing_agent": "Pricing Feedback (Agent)", "pricing_buyer": "Pricing Feedback (Buyer)"}

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
<script>
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


def render_page(body_html):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{THEME_INIT_SCRIPT}
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>Admin | Simone Marzullo</title>
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
{FOOTER_AND_SCRIPTS}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Password hashing -- identical scheme to api/dashboard.py and api/team.py
# (kept duplicated rather than imported, matching this project's
# no-cross-file-imports rule).
# ---------------------------------------------------------------------------
PBKDF2_ITERATIONS = 600_000


def hash_password(password):
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def clean(value, max_len=MAX_FIELD_LEN):
    return str(value).strip()[:max_len] if value is not None else ""


def _session_secret():
    return os.environ.get("SESSION_SECRET", "")


def make_admin_token():
    secret = _session_secret()
    expiry = int(time.time()) + SESSION_HOURS * 3600
    payload = f"admin:{expiry}"
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_admin_token(token):
    secret = _session_secret()
    if not token or not secret or "." not in token:
        return False
    payload, _, sig = token.rpartition(".")
    expected = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return False
    parts = payload.split(":")
    if len(parts) != 2 or parts[0] != "admin":
        return False
    try:
        expiry = int(parts[1])
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


# ---------------------------------------------------------------------------
# Database access -- every query below is parameterized (%s placeholders,
# values passed separately to execute()); user input is never formatted
# directly into SQL text.
# ---------------------------------------------------------------------------
def get_conn():
    dsn = os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    return psycopg2.connect(dsn, connect_timeout=5)


def fetch_all_clients(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, email, name, active, created_at FROM clients ORDER BY created_at DESC")
        clients = cur.fetchall()
        for client in clients:
            cur.execute(
                """SELECT id, address, status, showings_count, emails_sent_count,
                          calls_made_count, texts_sent_count
                   FROM listings WHERE client_id = %s ORDER BY created_at DESC""",
                (client["id"],),
            )
            listings = cur.fetchall()
            for listing in listings:
                cur.execute(
                    "SELECT id, category, note, created_at FROM feedback_notes WHERE listing_id = %s ORDER BY created_at DESC",
                    (listing["id"],),
                )
                listing["feedback"] = cur.fetchall()
                cur.execute(
                    "SELECT id, price, financing_type, close_of_escrow, contingencies, created_at FROM offers WHERE listing_id = %s ORDER BY created_at DESC",
                    (listing["id"],),
                )
                listing["offers"] = cur.fetchall()
            client["listings"] = listings
        return clients


def fetch_all_team_members(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, email, name, active, created_at FROM team_members ORDER BY created_at DESC")
        return cur.fetchall()


def fetch_all_resource_tiles(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, title, description, url, sort_order, active FROM resource_tiles ORDER BY sort_order, created_at")
        return cur.fetchall()


def fetch_all_contingency_types(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, name, active FROM contingency_types ORDER BY name")
        return cur.fetchall()


def create_client(conn, email, name, password):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO clients (email, name, password_hash) VALUES (%s, %s, %s) RETURNING id",
            (email, name, hash_password(password)),
        )
        client_id = cur.fetchone()[0]
    conn.commit()
    return client_id


def toggle_client_active(conn, client_id):
    with conn.cursor() as cur:
        cur.execute("UPDATE clients SET active = NOT active WHERE id = %s", (client_id,))
    conn.commit()


def create_listing(conn, client_id, address):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO listings (client_id, address) VALUES (%s, %s)", (client_id, address))
    conn.commit()


def update_listing(conn, listing_id, status, showings, emails, calls, texts):
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE listings SET status = %s, showings_count = %s, emails_sent_count = %s,
               calls_made_count = %s, texts_sent_count = %s, updated_at = now() WHERE id = %s""",
            (status, showings, emails, calls, texts, listing_id),
        )
    conn.commit()


def add_feedback(conn, listing_id, category, note):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO feedback_notes (listing_id, category, note) VALUES (%s, %s, %s)",
            (listing_id, category, note),
        )
    conn.commit()


def add_offer(conn, listing_id, price, financing_type, close_of_escrow, contingencies):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO offers (listing_id, price, financing_type, close_of_escrow, contingencies)
               VALUES (%s, %s, %s, %s, %s)""",
            (listing_id, price, financing_type, close_of_escrow, contingencies),
        )
    conn.commit()


def create_team_member(conn, email, name, password):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO team_members (email, name, password_hash) VALUES (%s, %s, %s) RETURNING id",
            (email, name, hash_password(password)),
        )
        team_member_id = cur.fetchone()[0]
    conn.commit()
    return team_member_id


def toggle_team_member_active(conn, team_member_id):
    with conn.cursor() as cur:
        cur.execute("UPDATE team_members SET active = NOT active WHERE id = %s", (team_member_id,))
    conn.commit()


def create_resource_tile(conn, title, description, url, sort_order):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO resource_tiles (title, description, url, sort_order) VALUES (%s, %s, %s, %s)",
            (title, description, url, sort_order),
        )
    conn.commit()


def update_resource_tile(conn, tile_id, title, description, url, sort_order):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE resource_tiles SET title = %s, description = %s, url = %s, sort_order = %s WHERE id = %s",
            (title, description, url, sort_order, tile_id),
        )
    conn.commit()


def toggle_resource_tile_active(conn, tile_id):
    with conn.cursor() as cur:
        cur.execute("UPDATE resource_tiles SET active = NOT active WHERE id = %s", (tile_id,))
    conn.commit()


def create_contingency_type(conn, name):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO contingency_types (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (name,))
    conn.commit()


def toggle_contingency_type_active(conn, contingency_type_id):
    with conn.cursor() as cur:
        cur.execute("UPDATE contingency_types SET active = NOT active WHERE id = %s", (contingency_type_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------
def build_login_html(error=None):
    error_html = f'<div class="om-error" style="display:block">{html.escape(error)}</div>' if error else '<div class="om-error" id="adm-error"></div>'
    body = f"""
<section class="section" style="text-align:center;padding-top:140px">
  <div class="wrap" style="max-width:380px">
    <h1 class="area-h1" style="margin-bottom:24px">Admin</h1>
    <form id="adm-login-form" novalidate>
      <div class="om-form">
        <label class="om-field">
          <span class="om-field-label">Password</span>
          <input type="password" id="adm-password" class="om-input" required autocomplete="current-password">
        </label>
      </div>
      {error_html}
      <button type="submit" class="btn-primary" id="adm-login-submit" style="width:100%;justify-content:center;margin-top:22px">Sign In</button>
    </form>
  </div>
</section>
<script>
document.getElementById('adm-login-form').addEventListener('submit', async function (e) {{
  e.preventDefault();
  const password = document.getElementById('adm-password').value;
  const errEl = document.getElementById('adm-error');
  errEl.style.display = 'none';
  try {{
    const res = await fetch('/api/admin', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{action: 'login', password}}),
    }});
    const data = await res.json();
    if (res.ok && data.ok) {{
      window.location.href = '/admin';
    }} else {{
      errEl.textContent = data.error || 'Something went wrong.';
      errEl.style.display = 'block';
    }}
  }} catch (err) {{
    errEl.textContent = 'Network error. Please try again.';
    errEl.style.display = 'block';
  }}
}});
</script>
"""
    return render_page(body)


def _fmt_date(d):
    return d.strftime("%Y-%m-%d") if d else ""


def _status_options(current):
    return "".join(f'<option value="{s}"{" selected" if s == current else ""}>{s}</option>' for s in LISTING_STATUSES)


def _category_options():
    return "".join(f'<option value="{k}">{v}</option>' for k, v in FEEDBACK_CATEGORIES.items())


def _contingency_checkboxes(contingency_types, listing_id):
    boxes = []
    for c in contingency_types:
        if not c["active"]:
            continue
        cid = f"cont-{listing_id}-{c['id']}"
        boxes.append(f'<label class="db-checkbox"><input type="checkbox" name="contingencies" value="{html.escape(c["name"])}" id="{cid}"> {html.escape(c["name"])}</label>')
    return "".join(boxes) or '<span class="db-empty-note">No contingency types yet -- add one below.</span>'


def _offer_admin_html(offer):
    price = f"${offer['price']:,.0f}"
    financing_label = "Cash" if offer["financing_type"] == "cash" else "Loan"
    close_label = offer["close_of_escrow"].strftime("%b %-d, %Y") if offer["close_of_escrow"] else "Not specified"
    tags = ", ".join(offer["contingencies"]) or "None"
    return f"""<div class="db-offer-row">
      <div class="db-offer-main"><span class="db-offer-price">{price}</span><span class="om-status">{financing_label}</span></div>
      <div class="db-offer-meta">Close: {close_label} &middot; Contingencies: {html.escape(tags)}</div>
    </div>"""


def _listing_admin_html(listing, contingency_types):
    offers_html = "".join(_offer_admin_html(o) for o in listing["offers"]) or '<p class="db-empty-note">No offers yet.</p>'
    feedback_html = "".join(
        f"""<div class="db-note"><div class="db-note-date">{f["created_at"].strftime("%b %-d, %Y")} &middot; {FEEDBACK_CATEGORIES.get(f["category"], f["category"])}</div><div class="db-note-text">{html.escape(f["note"])}</div></div>"""
        for f in listing["feedback"]
    ) or '<p class="db-empty-note">No feedback yet.</p>'

    return f"""
    <div class="adm-listing">
      <div class="adm-listing-head">{html.escape(listing["address"])}</div>
      <form class="adm-inline-form" data-action="update_listing" data-listing-id="{listing["id"]}">
        <label class="om-field"><span class="om-field-label">Status</span>
          <select name="status" class="om-input">{_status_options(listing["status"])}</select>
        </label>
        <label class="om-field"><span class="om-field-label">Showings</span>
          <input type="number" min="0" name="showings_count" class="om-input" value="{listing["showings_count"]}">
        </label>
        <label class="om-field"><span class="om-field-label">Emails Sent</span>
          <input type="number" min="0" name="emails_sent_count" class="om-input" value="{listing["emails_sent_count"]}">
        </label>
        <label class="om-field"><span class="om-field-label">Calls Made</span>
          <input type="number" min="0" name="calls_made_count" class="om-input" value="{listing["calls_made_count"]}">
        </label>
        <label class="om-field"><span class="om-field-label">Texts Sent</span>
          <input type="number" min="0" name="texts_sent_count" class="om-input" value="{listing["texts_sent_count"]}">
        </label>
        <button type="submit" class="btn-primary adm-btn-sm">Save</button>
      </form>

      <div class="adm-subsection">
        <div class="db-section-title">Offers Received</div>
        {offers_html}
        <form class="adm-inline-form" data-action="add_offer" data-listing-id="{listing["id"]}">
          <input type="number" name="price" class="om-input" placeholder="Price" min="0" step="1" required>
          <label class="om-field"><span class="om-field-label">Financing</span>
            <select name="financing_type" class="om-input"><option value="cash">Cash</option><option value="loan">Loan</option></select>
          </label>
          <label class="om-field"><span class="om-field-label">Close of Escrow</span>
            <input type="date" name="close_of_escrow" class="om-input">
          </label>
          <div class="db-checkbox-group">{_contingency_checkboxes(contingency_types, listing["id"])}</div>
          <button type="submit" class="btn-primary adm-btn-sm">Add Offer</button>
        </form>
      </div>

      <div class="adm-subsection">
        <div class="db-section-title">Feedback</div>
        {feedback_html}
        <form class="adm-inline-form" data-action="add_feedback" data-listing-id="{listing["id"]}">
          <label class="om-field"><span class="om-field-label">Type</span>
            <select name="category" class="om-input">{_category_options()}</select>
          </label>
          <input type="text" name="note" class="om-input" placeholder="Feedback note…" maxlength="2000" required>
          <button type="submit" class="btn-primary adm-btn-sm">Add</button>
        </form>
      </div>
    </div>"""


def _client_admin_html(client, contingency_types):
    listings_html = "".join(_listing_admin_html(l, contingency_types) for l in client["listings"]) or '<p class="db-empty-note">No listings yet.</p>'
    status_label = "Active" if client["active"] else "Deactivated"
    return f"""
  <details class="adm-client">
    <summary>
      <span class="adm-client-email">{html.escape(client["email"])}</span>
      {f'<span class="adm-client-name">{html.escape(client["name"])}</span>' if client["name"] else ''}
      <span class="om-status">{status_label}</span>
    </summary>
    <div class="adm-client-body">
      {listings_html}
      <form class="adm-inline-form" data-action="create_listing" data-client-id="{client["id"]}">
        <input type="text" name="address" class="om-input" placeholder="New listing address" maxlength="{MAX_FIELD_LEN}" required>
        <button type="submit" class="btn-primary adm-btn-sm">Add Listing</button>
      </form>
      <button type="button" class="om-logout adm-toggle-active" data-action="toggle_client_active" data-id="{client["id"]}" style="margin-top:14px">{"Deactivate" if client["active"] else "Reactivate"} this client</button>
    </div>
  </details>"""


def _team_member_admin_html(tm):
    status_label = "Active" if tm["active"] else "Deactivated"
    return f"""
  <div class="adm-list-row">
    <div>
      <span class="adm-client-email">{html.escape(tm["email"])}</span>
      {f'<span class="adm-client-name">{html.escape(tm["name"])}</span>' if tm["name"] else ''}
    </div>
    <span class="om-status">{status_label}</span>
    <button type="button" class="om-logout adm-toggle-active" data-action="toggle_team_member_active" data-id="{tm["id"]}">{"Deactivate" if tm["active"] else "Reactivate"}</button>
  </div>"""


def _resource_tile_admin_html(t):
    status_label = "Active" if t["active"] else "Hidden"
    return f"""
  <form class="adm-inline-form" data-action="update_resource_tile" data-id="{t["id"]}">
    <input type="text" name="title" class="om-input" value="{html.escape(t['title'])}" placeholder="Title" required>
    <input type="text" name="description" class="om-input" value="{html.escape(t['description'])}" placeholder="Description">
    <input type="url" name="url" class="om-input" value="{html.escape(t['url'])}" placeholder="https://..." required>
    <input type="number" name="sort_order" class="om-input" value="{t['sort_order']}" style="max-width:80px" title="Sort order">
    <button type="submit" class="btn-primary adm-btn-sm">Save</button>
    <span class="om-status">{status_label}</span>
    <button type="button" class="om-logout adm-toggle-active" data-action="toggle_resource_tile_active" data-id="{t["id"]}">{"Hide" if t["active"] else "Show"}</button>
  </form>"""


def _contingency_type_admin_html(c):
    status_label = "Active" if c["active"] else "Hidden"
    return f"""
  <div class="adm-list-row">
    <span class="adm-client-email">{html.escape(c["name"])}</span>
    <span class="om-status">{status_label}</span>
    <button type="button" class="om-logout adm-toggle-active" data-action="toggle_contingency_type_active" data-id="{c["id"]}">{"Hide" if c["active"] else "Show"}</button>
  </div>"""


def build_admin_html(clients, team_members, resource_tiles, contingency_types):
    active_clients = [c for c in clients if c["active"]]
    history_clients = [c for c in clients if not c["active"]]
    active_team = [t for t in team_members if t["active"]]
    history_team = [t for t in team_members if not t["active"]]

    clients_html = "".join(_client_admin_html(c, contingency_types) for c in active_clients) or '<div class="om-empty">No active clients -- add one above.</div>'
    history_clients_html = "".join(_client_admin_html(c, contingency_types) for c in history_clients) or '<div class="om-empty">No deactivated clients.</div>'
    team_html = "".join(_team_member_admin_html(t) for t in active_team) or '<div class="om-empty">No active team members -- add one above.</div>'
    history_team_html = "".join(_team_member_admin_html(t) for t in history_team) or '<div class="om-empty">No deactivated team members.</div>'
    tiles_html = "".join(_resource_tile_admin_html(t) for t in resource_tiles) or '<p class="db-empty-note">No resource tiles yet.</p>'
    contingency_html = "".join(_contingency_type_admin_html(c) for c in contingency_types) or '<p class="db-empty-note">No contingency types yet.</p>'

    body = f"""
<section class="section" style="padding-top:120px">
  <div class="wrap">
    <h1 class="area-h1" style="margin-bottom:8px">Admin</h1>
    <p class="area-tagline" style="margin-bottom:32px">Manage clients, team members, and resources.</p>
    <div id="adm-notice" class="adm-notice" style="display:none"></div>

    <h2 class="db-section-title" style="font-size:.9rem;margin-bottom:16px">Clients</h2>
    <div class="adm-panel">
      <div class="db-section-title">Create New Client</div>
      <form id="adm-create-client-form" class="adm-inline-form">
        <input type="email" name="email" class="om-input" placeholder="Client email" required>
        <input type="text" name="name" class="om-input" placeholder="Client name">
        <input type="text" name="password" class="om-input" placeholder="Password to assign" required minlength="{MIN_PASSWORD_LEN}">
        <button type="submit" class="btn-primary adm-btn-sm">Create Client</button>
      </form>
      <div class="om-error" id="adm-create-client-error"></div>
    </div>
    <div class="adm-clients">{clients_html}</div>

    <details class="adm-history">
      <summary>History (deactivated clients)</summary>
      <div class="adm-clients">{history_clients_html}</div>
    </details>

    <h2 class="db-section-title" style="font-size:.9rem;margin:40px 0 16px">Team Members</h2>
    <div class="adm-panel">
      <div class="db-section-title">Create New Team Member</div>
      <form id="adm-create-team-form" class="adm-inline-form">
        <input type="email" name="email" class="om-input" placeholder="Email" required>
        <input type="text" name="name" class="om-input" placeholder="Name">
        <input type="text" name="password" class="om-input" placeholder="Password to assign" required minlength="{MIN_PASSWORD_LEN}">
        <button type="submit" class="btn-primary adm-btn-sm">Create Team Member</button>
      </form>
      <div class="om-error" id="adm-create-team-error"></div>
    </div>
    <div class="adm-list">{team_html}</div>

    <details class="adm-history">
      <summary>History (deactivated team members)</summary>
      <div class="adm-list">{history_team_html}</div>
    </details>

    <h2 class="db-section-title" style="font-size:.9rem;margin:40px 0 16px">Team Resource Hub Tiles</h2>
    <div class="adm-panel">
      <form id="adm-create-tile-form" class="adm-inline-form">
        <input type="text" name="title" class="om-input" placeholder="Title" required>
        <input type="text" name="description" class="om-input" placeholder="Description">
        <input type="url" name="url" class="om-input" placeholder="https://..." required>
        <input type="number" name="sort_order" class="om-input" placeholder="Sort order" value="0" style="max-width:100px">
        <button type="submit" class="btn-primary adm-btn-sm">Add Tile</button>
      </form>
    </div>
    <div class="adm-tiles">{tiles_html}</div>

    <h2 class="db-section-title" style="font-size:.9rem;margin:40px 0 16px">Contingency Types</h2>
    <div class="adm-panel">
      <form id="adm-create-contingency-form" class="adm-inline-form">
        <input type="text" name="name" class="om-input" placeholder="New contingency type" required>
        <button type="submit" class="btn-primary adm-btn-sm">Add</button>
      </form>
    </div>
    <div class="adm-list">{contingency_html}</div>

    <div style="text-align:center;margin-top:36px"><a href="/admin?logout=1" class="om-logout">Log out</a></div>
  </div>
</section>

<script>
async function adminPost(payload) {{
  const res = await fetch('/api/admin', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify(payload),
  }});
  const data = await res.json();
  if (!res.ok || !data.ok) throw new Error(data.error || 'Something went wrong.');
  return data;
}}

function showNotice(msg) {{ sessionStorage.setItem('adminNotice', msg); }}

(function () {{
  const notice = sessionStorage.getItem('adminNotice');
  if (notice) {{
    const el = document.getElementById('adm-notice');
    el.textContent = notice;
    el.style.display = 'block';
    sessionStorage.removeItem('adminNotice');
  }}
}})();

document.getElementById('adm-create-client-form').addEventListener('submit', async function (e) {{
  e.preventDefault();
  const form = e.target;
  const email = form.email.value.trim();
  const password = form.password.value;
  const errEl = document.getElementById('adm-create-client-error');
  errEl.style.display = 'none';
  try {{
    await adminPost({{action: 'create_client', email, name: form.name.value.trim(), password}});
    showNotice(`Client created — send them: ${{email}} / ${{password}}`);
    window.location.reload();
  }} catch (err) {{
    errEl.textContent = err.message;
    errEl.style.display = 'block';
  }}
}});

document.getElementById('adm-create-team-form').addEventListener('submit', async function (e) {{
  e.preventDefault();
  const form = e.target;
  const email = form.email.value.trim();
  const password = form.password.value;
  const errEl = document.getElementById('adm-create-team-error');
  errEl.style.display = 'none';
  try {{
    await adminPost({{action: 'create_team_member', email, name: form.name.value.trim(), password}});
    showNotice(`Team member created — send them: ${{email}} / ${{password}}`);
    window.location.reload();
  }} catch (err) {{
    errEl.textContent = err.message;
    errEl.style.display = 'block';
  }}
}});

document.getElementById('adm-create-tile-form').addEventListener('submit', async function (e) {{
  e.preventDefault();
  const form = e.target;
  const payload = {{action: 'create_resource_tile'}};
  new FormData(form).forEach(function (v, k) {{ payload[k] = v; }});
  try {{
    await adminPost(payload);
    window.location.reload();
  }} catch (err) {{
    alert(err.message);
  }}
}});

document.getElementById('adm-create-contingency-form').addEventListener('submit', async function (e) {{
  e.preventDefault();
  const form = e.target;
  try {{
    await adminPost({{action: 'create_contingency_type', name: form.name.value.trim()}});
    window.location.reload();
  }} catch (err) {{
    alert(err.message);
  }}
}});

document.querySelectorAll('form[data-action]').forEach(function (form) {{
  form.addEventListener('submit', async function (e) {{
    e.preventDefault();
    const action = form.dataset.action;
    const payload = {{action}};
    if (form.dataset.listingId) payload.listingId = form.dataset.listingId;
    if (form.dataset.clientId) payload.clientId = form.dataset.clientId;
    if (form.dataset.id) payload.id = form.dataset.id;
    const fd = new FormData(form);
    if (action === 'add_offer') {{
      payload.contingencies = fd.getAll('contingencies');
    }}
    fd.forEach(function (value, key) {{ if (key !== 'contingencies') payload[key] = value; }});
    try {{
      await adminPost(payload);
      window.location.reload();
    }} catch (err) {{
      alert(err.message);
    }}
  }});
}});

document.querySelectorAll('.adm-toggle-active[data-action]').forEach(function (btn) {{
  btn.addEventListener('click', async function () {{
    try {{
      await adminPost({{action: btn.dataset.action, id: btn.dataset.id}});
      window.location.reload();
    }} catch (err) {{
      alert(err.message);
    }}
  }});
}});
</script>
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

    def _require_admin(self):
        token = get_cookie(self.headers.get("Cookie", ""), COOKIE_NAME)
        return verify_admin_token(token)

    def do_GET(self):
        query = self.path.partition("?")[2]
        if "logout=1" in query:
            expired = f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"
            self._send_html(200, build_login_html(), set_cookie=expired)
            return

        if not self._require_admin():
            self._send_html(200, build_login_html())
            return

        conn = None
        try:
            conn = get_conn()
            if conn is None:
                self._send_html(503, build_error_html("The admin panel isn't set up yet -- POSTGRES_URL is missing."))
                return
            clients = fetch_all_clients(conn)
            team_members = fetch_all_team_members(conn)
            resource_tiles = fetch_all_resource_tiles(conn)
            contingency_types = fetch_all_contingency_types(conn)
        except Exception as e:
            print(f"admin: failed to load data: {e}")
            self._send_html(503, build_error_html("Something went wrong loading the admin panel."))
            return
        finally:
            if conn:
                conn.close()

        self._send_html(200, build_admin_html(clients, team_members, resource_tiles, contingency_types))

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

        action = clean(data.get("action"), 40)

        if action == "login":
            password = str(data.get("password", ""))
            expected = os.environ.get("ADMIN_PASSWORD")
            if not expected:
                self._send_json(503, {"ok": False, "error": "Admin access isn't set up yet."})
                return
            if not hmac.compare_digest(password.encode("utf-8"), expected.encode("utf-8")):
                self._send_json(401, {"ok": False, "error": "Incorrect password."})
                return
            cookie = f"{COOKIE_NAME}={make_admin_token()}; Path=/; Max-Age={SESSION_HOURS * 3600}; HttpOnly; Secure; SameSite=Lax"
            self._send_json(200, {"ok": True}, set_cookie=cookie)
            return

        if not self._require_admin():
            self._send_json(401, {"ok": False, "error": "Not signed in."})
            return

        conn = None
        try:
            conn = get_conn()
            if conn is None:
                self._send_json(503, {"ok": False, "error": "Database isn't set up yet."})
                return

            if action == "create_client":
                email = clean(data.get("email")).lower()
                name = clean(data.get("name"))
                password = str(data.get("password", ""))
                if not email or not EMAIL_RE.match(email):
                    self._send_json(400, {"ok": False, "error": "Enter a valid email address."})
                    return
                if len(password) < MIN_PASSWORD_LEN:
                    self._send_json(400, {"ok": False, "error": f"Password must be at least {MIN_PASSWORD_LEN} characters."})
                    return
                try:
                    create_client(conn, email, name, password)
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
                    self._send_json(400, {"ok": False, "error": "A client with that email already exists."})
                    return
                self._send_json(200, {"ok": True})
                return

            if action == "toggle_client_active":
                toggle_client_active(conn, int(data.get("id")))
                self._send_json(200, {"ok": True})
                return

            if action == "create_listing":
                client_id = int(data.get("clientId"))
                address = clean(data.get("address"))
                if not address:
                    self._send_json(400, {"ok": False, "error": "Address is required."})
                    return
                create_listing(conn, client_id, address)
                self._send_json(200, {"ok": True})
                return

            if action == "update_listing":
                listing_id = int(data.get("listingId"))
                status = clean(data.get("status"), 40) or "Active"
                if status not in LISTING_STATUSES:
                    status = "Active"
                showings = max(0, int(data.get("showings_count") or 0))
                emails = max(0, int(data.get("emails_sent_count") or 0))
                calls = max(0, int(data.get("calls_made_count") or 0))
                texts = max(0, int(data.get("texts_sent_count") or 0))
                update_listing(conn, listing_id, status, showings, emails, calls, texts)
                self._send_json(200, {"ok": True})
                return

            if action == "add_feedback":
                listing_id = int(data.get("listingId"))
                category = clean(data.get("category"), 40)
                if category not in FEEDBACK_CATEGORIES:
                    self._send_json(400, {"ok": False, "error": "Invalid feedback category."})
                    return
                note = clean(data.get("note"), 2000)
                if not note:
                    self._send_json(400, {"ok": False, "error": "Note can't be empty."})
                    return
                add_feedback(conn, listing_id, category, note)
                self._send_json(200, {"ok": True})
                return

            if action == "add_offer":
                listing_id = int(data.get("listingId"))
                price = float(data.get("price") or 0)
                financing_type = clean(data.get("financing_type"), 10)
                if financing_type not in ("cash", "loan"):
                    self._send_json(400, {"ok": False, "error": "Invalid financing type."})
                    return
                if price <= 0:
                    self._send_json(400, {"ok": False, "error": "Price must be greater than zero."})
                    return
                close_raw = clean(data.get("close_of_escrow"), 20)
                close_of_escrow = date.fromisoformat(close_raw) if close_raw else None
                raw_contingencies = data.get("contingencies") or []
                if not isinstance(raw_contingencies, list):
                    raw_contingencies = []
                contingencies = [clean(c, 200) for c in raw_contingencies[:20] if clean(c, 200)]
                add_offer(conn, listing_id, price, financing_type, close_of_escrow, contingencies)
                self._send_json(200, {"ok": True})
                return

            if action == "create_team_member":
                email = clean(data.get("email")).lower()
                name = clean(data.get("name"))
                password = str(data.get("password", ""))
                if not email or not EMAIL_RE.match(email):
                    self._send_json(400, {"ok": False, "error": "Enter a valid email address."})
                    return
                if len(password) < MIN_PASSWORD_LEN:
                    self._send_json(400, {"ok": False, "error": f"Password must be at least {MIN_PASSWORD_LEN} characters."})
                    return
                try:
                    create_team_member(conn, email, name, password)
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
                    self._send_json(400, {"ok": False, "error": "A team member with that email already exists."})
                    return
                self._send_json(200, {"ok": True})
                return

            if action == "toggle_team_member_active":
                toggle_team_member_active(conn, int(data.get("id")))
                self._send_json(200, {"ok": True})
                return

            if action == "create_resource_tile":
                title = clean(data.get("title"))
                description = clean(data.get("description"), 2000)
                url = clean(data.get("url"), 2000)
                sort_order = int(data.get("sort_order") or 0)
                if not title or not url:
                    self._send_json(400, {"ok": False, "error": "Title and URL are required."})
                    return
                create_resource_tile(conn, title, description, url, sort_order)
                self._send_json(200, {"ok": True})
                return

            if action == "update_resource_tile":
                tile_id = int(data.get("id"))
                title = clean(data.get("title"))
                description = clean(data.get("description"), 2000)
                url = clean(data.get("url"), 2000)
                sort_order = int(data.get("sort_order") or 0)
                if not title or not url:
                    self._send_json(400, {"ok": False, "error": "Title and URL are required."})
                    return
                update_resource_tile(conn, tile_id, title, description, url, sort_order)
                self._send_json(200, {"ok": True})
                return

            if action == "toggle_resource_tile_active":
                toggle_resource_tile_active(conn, int(data.get("id")))
                self._send_json(200, {"ok": True})
                return

            if action == "create_contingency_type":
                name = clean(data.get("name"), 200)
                if not name:
                    self._send_json(400, {"ok": False, "error": "Name can't be empty."})
                    return
                create_contingency_type(conn, name)
                self._send_json(200, {"ok": True})
                return

            if action == "toggle_contingency_type_active":
                toggle_contingency_type_active(conn, int(data.get("id")))
                self._send_json(200, {"ok": True})
                return

            self._send_json(400, {"ok": False, "error": "Unknown action."})
        except (TypeError, ValueError):
            self._send_json(400, {"ok": False, "error": "Invalid request data."})
        except Exception as e:
            print(f"admin: action '{action}' failed: {e}")
            self._send_json(500, {"ok": False, "error": "Something went wrong."})
        finally:
            if conn:
                conn.close()

    def log_message(self, fmt, *args):
        pass
