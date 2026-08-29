"""Client dashboard (/clientaccess) and admin panel (/admin) that feeds it,
combined into one Vercel Python function. Routed internally by a `section`
query param (dashboard|admin) set in vercel.json's rewrite destinations --
the same pattern api/areas.py already uses for its own multiple routes.
(The internal section value stays "dashboard" -- only the public URL
changed to /clientaccess.)

Combined into a single file deliberately: Vercel's Hobby plan caps a
deployment at 12 Serverless Functions, and this site already had 11 --
two more self-contained files (one per route, matching every other page
here) would have pushed it to 13 and failed to deploy
(exceeded_serverless_functions_per_deployment). One file for both keeps
the total at 12.

Auth model: each client gets their own row in the `clients` table (email
+ a password Simone assigns via /admin) -- unlike the off-market page's
single shared password. Admin uses a single shared ADMIN_PASSWORD, same
pattern as OFFMARKET_PASSWORD. Session cookies are HMAC-signed and
role-scoped (a client token can't be replayed as an admin token or
someone else's client session). Both sessions last 24h -- sellers re-enter
their password daily, which also naturally caps the FollowUpBoss login
notification below to about once per day per seller instead of needing
its own throttling/schema.

Every successful seller login pushes a FollowUpBoss event (same
push-to-FollowUpBoss pattern as api/offmarket.py and api/submit-lead.py,
using the FUB_API_KEY/FUB_SOURCE/FUB_SYSTEM* env vars already set for
those) so Simone knows when a seller checks their dashboard.

"Cancel account" sets active=false rather than deleting -- a client's
data stays intact and shows up in admin's History filter, never gone for
good.

Requires the `clients`/`listings`/`feedback_notes`/`offers`/`open_houses`
tables from db/schema.sql, POSTGRES_URL (or DATABASE_URL), ADMIN_PASSWORD,
and SESSION_SECRET.

Team member / resource hub support was dropped from this build to fit
the function-count limit -- may come back as its own file later if the
site drops below 12 functions, or once this project is on a paid plan.
"""

import base64
import hashlib
import hmac
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from http.server import BaseHTTPRequestHandler

import psycopg2
import psycopg2.errors
import psycopg2.extras

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CLIENT_COOKIE_NAME = "client_session"
ADMIN_COOKIE_NAME = "admin_session"
SESSION_HOURS = {"client": 24, "admin": 24}  # both: 24h -- sellers re-enter their password daily
MAX_BODY_BYTES = 16 * 1024
MAX_FIELD_LEN = 500
MIN_PASSWORD_LEN = 8
FUB_EVENTS_URL = "https://api.followupboss.com/v1/events"

LISTING_STATUSES = ["Active", "Under Contract", "Sold", "Expired", "Withdrawn"]
FEEDBACK_CATEGORIES = {
    "showing": "Showing Feedback",
    "pricing_agent": "Pricing Feedback (Agent)",
    "pricing_buyer": "Pricing Feedback (Buyer)",
    "buyer_feedback": "Buyer Feedback",
}

# Shared tab-toggle script for the 4-tab layout on each listing (Marketing /
# Number of Offers / Open Houses & Showings / Buyer Feedback) -- used on both
# the client dashboard and the admin panel, each listing scoped independently
# via the nearest [data-tabscope] ancestor.
DB_TABS_SCRIPT = """
document.querySelectorAll('[data-tabscope]').forEach(function (scope) {
  var tabs = scope.querySelectorAll(':scope > .db-tabs > .db-tab');
  var panels = scope.querySelectorAll(':scope > .db-tab-panel');
  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      tabs.forEach(function (t) { t.classList.toggle('active', t === tab); });
      panels.forEach(function (p) { p.style.display = (p.dataset.tabPanel === tab.dataset.tab) ? '' : 'none'; });
    });
  });
});
"""

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


def render_page(body_html, title):
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
# Password hashing (PBKDF2-HMAC-SHA256, stdlib only). Format mirrors
# Django's: "pbkdf2_sha256$<iters>$<salt>$<hash>".
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
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
    return hmac.compare_digest(dk, expected)


def clean(value, max_len=MAX_FIELD_LEN):
    return str(value).strip()[:max_len] if value is not None else ""


# ---------------------------------------------------------------------------
# Signed session cookies. Role-scoped ("client" carries an id, "admin"
# doesn't) and signed with a dedicated server-only secret (SESSION_SECRET)
# -- never a client's own password or ADMIN_PASSWORD itself, so either can
# be rotated independently of active sessions signed the other way.
# ---------------------------------------------------------------------------
def _session_secret():
    return os.environ.get("SESSION_SECRET", "")


def make_session_token(role, entity_id=None):
    secret = _session_secret()
    expiry = int(time.time()) + SESSION_HOURS[role] * 3600
    payload = f"{role}:{entity_id}:{expiry}" if entity_id is not None else f"{role}:{expiry}"
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(token, expected_role):
    """Returns True for a valid admin token, a client id (int) for a valid
    client token, or None if missing/forged/expired/wrong role."""
    secret = _session_secret()
    if not token or not secret or "." not in token:
        return None
    payload, _, sig = token.rpartition(".")
    expected_sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, sig):
        return None
    parts = payload.split(":")
    if not parts or parts[0] != expected_role:
        return None
    try:
        expiry = int(parts[-1])
    except ValueError:
        return None
    if time.time() >= expiry:
        return None
    if expected_role == "admin":
        return len(parts) == 2
    if len(parts) != 3:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


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
_LIBPQ_QUERY_PARAMS = {"sslmode", "connect_timeout", "application_name", "options"}


def _clean_dsn(dsn):
    """Some providers (Supabase's pooler included) append extra query
    params to the connection URL for their own routing purposes (e.g.
    "supa=base-pooler.x") that aren't real libpq options -- psycopg2's DSN
    parser rejects the whole URL outright over a single param it doesn't
    recognize. Keep only the handful of params libpq actually understands."""
    parsed = urllib.parse.urlsplit(dsn)
    query = urllib.parse.parse_qs(parsed.query)
    safe_query = {k: v for k, v in query.items() if k in _LIBPQ_QUERY_PARAMS}
    new_query = urllib.parse.urlencode(safe_query, doseq=True)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))


def get_conn():
    dsn = os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    return psycopg2.connect(_clean_dsn(dsn), connect_timeout=5)


def push_dashboard_login_to_followupboss(email, name):
    """Best-effort notification that a seller just logged into their
    client-access dashboard -- same pattern as offmarket.py's FollowUpBoss
    push. Never raises; a FollowUpBoss outage should never break a login."""
    api_key = os.environ.get("FUB_API_KEY")
    if not api_key:
        print("portal: FUB_API_KEY is not configured")
        return
    who = name or email
    event_payload = {
        "source": os.environ.get("FUB_SOURCE", "Simone Marzullo Website"),
        "system": os.environ.get("FUB_SYSTEM", "Simone Marzullo Website"),
        "type": "General Inquiry",
        "message": f"{who} logged into their Client Access dashboard.",
        "person": {"emails": [{"value": email}], "tags": ["Client Dashboard Login"]},
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
        print(f"portal: FollowUpBoss API error {e.code}: {e.read().decode('utf-8', errors='replace')}")
    except Exception as e:
        print(f"portal: unexpected error calling FollowUpBoss: {e}")


def fetch_client_by_email(conn, email):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, email, password_hash, name, active FROM clients WHERE email = %s", (email,))
        return cur.fetchone()


def fetch_dashboard_data(conn, client_id):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, name FROM clients WHERE id = %s AND active = TRUE", (client_id,))
        client = cur.fetchone()
        if not client:
            return None

        cur.execute(
            """SELECT id, address, status, agents_reached_count
               FROM listings WHERE client_id = %s ORDER BY created_at DESC""",
            (client_id,),
        )
        listings = cur.fetchall()

        cur.execute("SELECT id, name FROM metric_types WHERE active = TRUE ORDER BY name")
        metric_types = cur.fetchall()

        for listing in listings:
            cur.execute(
                """SELECT price, financing_type, close_of_escrow, created_at
                   FROM offers WHERE listing_id = %s ORDER BY created_at ASC""",
                (listing["id"],),
            )
            listing["offers"] = cur.fetchall()

            cur.execute(
                "SELECT event_date, groups_count, notes FROM open_houses WHERE listing_id = %s ORDER BY event_date DESC, created_at DESC",
                (listing["id"],),
            )
            listing["open_houses"] = cur.fetchall()

            cur.execute(
                "SELECT category, note, created_at FROM feedback_notes WHERE listing_id = %s ORDER BY created_at DESC",
                (listing["id"],),
            )
            listing["feedback"] = cur.fetchall()

            cur.execute("SELECT metric_type_id, value FROM listing_metrics WHERE listing_id = %s", (listing["id"],))
            values_by_type = {row["metric_type_id"]: row["value"] for row in cur.fetchall()}
            listing["metrics"] = [
                {"name": mt["name"], "value": values_by_type.get(mt["id"], 0)} for mt in metric_types
            ]

        return {"client": client, "listings": listings}


def fetch_all_clients(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, email, name, active, created_at FROM clients ORDER BY created_at DESC")
        clients = cur.fetchall()

        cur.execute("SELECT id, name, active FROM metric_types ORDER BY name")
        metric_types = cur.fetchall()

        for client in clients:
            cur.execute(
                """SELECT id, address, status, agents_reached_count
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
                    "SELECT id, price, financing_type, close_of_escrow, created_at FROM offers WHERE listing_id = %s ORDER BY created_at ASC",
                    (listing["id"],),
                )
                listing["offers"] = cur.fetchall()

                cur.execute(
                    "SELECT id, event_date, groups_count, notes FROM open_houses WHERE listing_id = %s ORDER BY event_date DESC, created_at DESC",
                    (listing["id"],),
                )
                listing["open_houses"] = cur.fetchall()

                cur.execute("SELECT metric_type_id, value FROM listing_metrics WHERE listing_id = %s", (listing["id"],))
                values_by_type = {row["metric_type_id"]: row["value"] for row in cur.fetchall()}
                listing["metric_values"] = {mt["id"]: values_by_type.get(mt["id"], 0) for mt in metric_types}
            client["listings"] = listings
        return clients, metric_types


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


def reset_client_password(conn, client_id, password):
    with conn.cursor() as cur:
        cur.execute("UPDATE clients SET password_hash = %s WHERE id = %s", (hash_password(password), client_id))
    conn.commit()


def update_client_email(conn, client_id, email):
    with conn.cursor() as cur:
        cur.execute("UPDATE clients SET email = %s WHERE id = %s", (email, client_id))
    conn.commit()


def create_listing(conn, client_id, address):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO listings (client_id, address) VALUES (%s, %s)", (client_id, address))
    conn.commit()


def update_listing_status(conn, listing_id, status):
    with conn.cursor() as cur:
        cur.execute("UPDATE listings SET status = %s, updated_at = now() WHERE id = %s", (status, listing_id))
    conn.commit()


def update_listing_address(conn, listing_id, address):
    with conn.cursor() as cur:
        cur.execute("UPDATE listings SET address = %s, updated_at = now() WHERE id = %s", (address, listing_id))
    conn.commit()


def update_marketing(conn, listing_id, agents_reached):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE listings SET agents_reached_count = %s, updated_at = now() WHERE id = %s",
            (agents_reached, listing_id),
        )
    conn.commit()


def create_open_house(conn, listing_id, event_date, groups_count, notes):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO open_houses (listing_id, event_date, groups_count, notes) VALUES (%s, %s, %s, %s)",
            (listing_id, event_date, groups_count, notes),
        )
    conn.commit()


def add_feedback(conn, listing_id, category, note):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO feedback_notes (listing_id, category, note) VALUES (%s, %s, %s)",
            (listing_id, category, note),
        )
    conn.commit()


def update_feedback(conn, feedback_id, category, note):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE feedback_notes SET category = %s, note = %s WHERE id = %s",
            (category, note, feedback_id),
        )
    conn.commit()


def delete_feedback(conn, feedback_id):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM feedback_notes WHERE id = %s", (feedback_id,))
    conn.commit()


def add_offer(conn, listing_id, price, financing_type, close_of_escrow):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO offers (listing_id, price, financing_type, close_of_escrow)
               VALUES (%s, %s, %s, %s)""",
            (listing_id, price, financing_type, close_of_escrow),
        )
    conn.commit()


def create_metric_type(conn, name):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO metric_types (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (name,))
    conn.commit()


def toggle_metric_type_active(conn, metric_type_id):
    with conn.cursor() as cur:
        cur.execute("UPDATE metric_types SET active = NOT active WHERE id = %s", (metric_type_id,))
    conn.commit()


def upsert_listing_metric(conn, listing_id, metric_type_id, value):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO listing_metrics (listing_id, metric_type_id, value)
               VALUES (%s, %s, %s)
               ON CONFLICT (listing_id, metric_type_id)
               DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
            (listing_id, metric_type_id, value),
        )
    conn.commit()


def fetch_all_toolbox_links(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, name, url, sort_order, active FROM toolbox_links ORDER BY sort_order, created_at")
        return cur.fetchall()


def create_toolbox_link(conn, name, url, sort_order):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO toolbox_links (name, url, sort_order) VALUES (%s, %s, %s)",
            (name, url, sort_order),
        )
    conn.commit()


def update_toolbox_link(conn, link_id, name, url, sort_order):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE toolbox_links SET name = %s, url = %s, sort_order = %s WHERE id = %s",
            (name, url, sort_order, link_id),
        )
    conn.commit()


def toggle_toolbox_link_active(conn, link_id):
    with conn.cursor() as cur:
        cur.execute("UPDATE toolbox_links SET active = NOT active WHERE id = %s", (link_id,))
    conn.commit()


def delete_toolbox_link(conn, link_id):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM toolbox_links WHERE id = %s", (link_id,))
    conn.commit()


def build_error_html(message, title):
    body = f"""
<section class="section" style="text-align:center;padding-top:140px">
  <div class="wrap"><p class="om-empty">{html.escape(message)}</p></div>
</section>
"""
    return render_page(body, title)


# ---------------------------------------------------------------------------
# Client dashboard rendering
# ---------------------------------------------------------------------------
def build_client_login_html(error=None):
    error_html = f'<div class="om-error" style="display:block">{html.escape(error)}</div>' if error else '<div class="om-error" id="dash-error"></div>'
    body = f"""
<section class="area-hero dash-hero">
  <img class="area-hero-img" src="/assets/hero-skyline-day.jpg" alt="Los Angeles skyline at dusk" loading="eager" style="object-position:50% 50%">
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
    <p style="margin-top:28px"><a href="/admin" class="om-logout">Admin</a></p>
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
    const res = await fetch('/api/portal?section=dashboard', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{action: 'login', email, password}}),
    }});
    const data = await res.json();
    if (res.ok && data.ok) {{
      window.location.href = '/clientaccess';
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
    return render_page(body, "Client Dashboard | Simone Marzullo")


def _stat_tile(label, value):
    return f"""<div class="db-stat">
      <div class="db-stat-value">{value}</div>
      <div class="db-stat-label">{html.escape(label)}</div>
    </div>"""


def _offer_html(offer, number):
    price = f"${offer['price']:,.0f}"
    financing_label = "Cash" if offer["financing_type"] == "cash" else "Loan"
    close_label = offer["close_of_escrow"].strftime("%b %-d, %Y") if offer["close_of_escrow"] else "Not specified"
    return f"""<div class="db-offer-row">
      <div class="db-offer-main">
        <span class="db-offer-number">Offer #{number}</span>
        <span class="db-offer-price">{price}</span>
        <span class="om-status">{financing_label}</span>
      </div>
      <div class="db-offer-meta">Close of escrow: {close_label}</div>
    </div>"""


def _open_house_html(oh):
    date_label = oh["event_date"].strftime("%b %-d, %Y") if oh["event_date"] else ""
    groups = oh["groups_count"] or 0
    groups_label = f"{groups} group{'s' if groups != 1 else ''} through"
    note_html = f'<div class="db-note-text">{html.escape(oh["notes"])}</div>' if oh.get("notes") else ""
    return f"""<div class="db-note">
      <div class="db-note-date">{date_label}<span class="db-note-sub">{groups_label}</span></div>
      {note_html}
    </div>"""


def _feedback_html(notes, empty_message):
    if not notes:
        return f'<p class="db-empty-note">{html.escape(empty_message)}</p>'
    items = []
    for n in notes:
        sub = FEEDBACK_CATEGORIES.get(n["category"], n["category"])
        items.append(f"""<div class="db-note">
          <div class="db-note-date">{n["created_at"].strftime("%b %-d, %Y")}<span class="db-note-sub">{html.escape(sub)}</span></div>
          <div class="db-note-text">{html.escape(n["note"])}</div>
        </div>""")
    return "".join(items)


def _listing_html(listing):
    groups_total = sum(oh["groups_count"] or 0 for oh in listing["open_houses"])
    marketing_stats = "".join([
        _stat_tile("Showings", len(listing["open_houses"])),
        _stat_tile("Open House Groups", groups_total),
        _stat_tile("Agents Reached", listing["agents_reached_count"]),
        _stat_tile("Offers Received", len(listing["offers"])),
    ] + [_stat_tile(m["name"], m["value"]) for m in listing["metrics"]])

    offers_html = "".join(_offer_html(o, i + 1) for i, o in enumerate(listing["offers"])) or '<p class="db-empty-note">No offers received yet.</p>'
    open_houses_html = "".join(_open_house_html(oh) for oh in listing["open_houses"]) or '<p class="db-empty-note">No open houses logged yet.</p>'
    feedback_html = _feedback_html(listing["feedback"], "No feedback logged yet.")

    return f"""
    <div class="db-listing" data-tabscope>
      <div class="db-listing-head">
        <div class="db-listing-address">{html.escape(listing["address"])}</div>
        <span class="om-status">{html.escape(listing["status"])}</span>
      </div>
      <div class="db-tabs">
        <button type="button" class="db-tab active" data-tab="marketing">Marketing</button>
        <button type="button" class="db-tab" data-tab="offers">Number of Offers</button>
        <button type="button" class="db-tab" data-tab="openhouses">Open Houses &amp; Showings</button>
        <button type="button" class="db-tab" data-tab="feedback">Buyer Feedback</button>
      </div>
      <div class="db-tab-panel" data-tab-panel="marketing">
        <div class="db-stats">{marketing_stats}</div>
      </div>
      <div class="db-tab-panel" data-tab-panel="offers" style="display:none">
        {offers_html}
      </div>
      <div class="db-tab-panel" data-tab-panel="openhouses" style="display:none">
        {open_houses_html}
      </div>
      <div class="db-tab-panel" data-tab-panel="feedback" style="display:none">
        {feedback_html}
      </div>
    </div>"""


def build_client_dashboard_html(data):
    client = data["client"]
    listings = data["listings"]
    name_html = f", {html.escape(client['name'])}" if client.get("name") else ""

    if listings:
        listings_html = "".join(_listing_html(l) for l in listings)
    else:
        listings_html = '<div class="om-empty">No listings on your account yet -- check back once Simone has one set up.</div>'

    body = f"""
<section class="area-hero dash-hero">
  <img class="area-hero-img" src="/assets/hero-skyline-day.jpg" alt="Los Angeles skyline at dusk" loading="eager" style="object-position:50% 50%">
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
    <div style="text-align:center;margin-top:36px"><a href="/clientaccess?logout=1" class="om-logout">Log out</a></div>
  </div>
</section>
<script>{DB_TABS_SCRIPT}</script>
"""
    return render_page(body, "Client Dashboard | Simone Marzullo")


# ---------------------------------------------------------------------------
# Admin rendering
# ---------------------------------------------------------------------------
def build_admin_login_html(error=None):
    error_html = f'<div class="om-error" style="display:block">{html.escape(error)}</div>' if error else '<div class="om-error" id="adm-error"></div>'
    body = f"""
<section class="section" style="text-align:center;padding-top:140px">
  <div class="wrap" style="max-width:380px">
    <h1 class="adm-h1" style="margin-bottom:24px">Admin</h1>
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
    const res = await fetch('/api/portal?section=admin', {{
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
    return render_page(body, "Admin | Simone Marzullo")


def _fmt_date(d):
    return d.strftime("%Y-%m-%d") if d else ""


def _status_options(current):
    return "".join(f'<option value="{s}"{" selected" if s == current else ""}>{s}</option>' for s in LISTING_STATUSES)




def _offer_admin_html(offer, number):
    price = f"${offer['price']:,.0f}"
    financing_label = "Cash" if offer["financing_type"] == "cash" else "Loan"
    close_label = offer["close_of_escrow"].strftime("%b %-d, %Y") if offer["close_of_escrow"] else "Not specified"
    return f"""<details class="adm-offer">
      <summary>Offer #{number} &mdash; {price}</summary>
      <div class="db-offer-meta">Financing: {financing_label} &middot; Close: {close_label}</div>
    </details>"""


def _category_options(selected=None):
    return "".join(f'<option value="{k}"{" selected" if k == selected else ""}>{v}</option>' for k, v in FEEDBACK_CATEGORIES.items())


def _metric_inputs_html(metric_types, metric_values):
    inputs = []
    for mt in metric_types:
        if not mt["active"]:
            continue
        value = metric_values.get(mt["id"], 0)
        inputs.append(f"""<label class="om-field"><span class="om-field-label">{html.escape(mt["name"])}</span>
          <input type="number" min="0" name="metric_{mt["id"]}" class="om-input" value="{value}">
        </label>""")
    return "".join(inputs)


def _feedback_admin_html(f):
    return f"""
    <div class="adm-feedback-entry">
      <div class="db-note-date">{f["created_at"].strftime("%b %-d, %Y")}</div>
      <form class="adm-inline-form" data-action="update_feedback" data-id="{f["id"]}">
        <label class="om-field"><span class="om-field-label">Type</span>
          <select name="category" class="om-input">{_category_options(selected=f["category"])}</select>
        </label>
        <input type="text" name="note" class="om-input" value="{html.escape(f["note"])}" maxlength="2000" required>
        <button type="submit" class="btn-primary adm-btn-sm">Save</button>
        <button type="button" class="om-logout adm-delete-btn" data-action="delete_feedback" data-id="{f["id"]}">Delete</button>
      </form>
    </div>"""


def _listing_admin_html(listing, metric_types):
    offers_html = "".join(_offer_admin_html(o, i + 1) for i, o in enumerate(listing["offers"])) or '<p class="db-empty-note">No offers yet.</p>'
    open_houses_html = "".join(_open_house_html(oh) for oh in listing["open_houses"]) or '<p class="db-empty-note">No open houses logged yet.</p>'
    groups_total = sum(oh["groups_count"] or 0 for oh in listing["open_houses"])
    summary_stats_html = "".join([
        _stat_tile("Showings", len(listing["open_houses"])),
        _stat_tile("Open House Groups", groups_total),
        _stat_tile("Agents Reached", listing["agents_reached_count"]),
        _stat_tile("Offers Received", len(listing["offers"])),
    ])
    feedback_html = "".join(_feedback_admin_html(f) for f in listing["feedback"]) or '<p class="db-empty-note">No feedback yet.</p>'

    return f"""
    <div class="adm-listing">
      <form class="adm-inline-form" data-action="update_listing_address" data-listing-id="{listing["id"]}" style="margin-bottom:14px">
        <input type="text" name="address" class="om-input" value="{html.escape(listing["address"])}" maxlength="{MAX_FIELD_LEN}" required style="flex:1 1 300px">
        <button type="submit" class="btn-primary adm-btn-sm">Save Address</button>
      </form>
      <form class="adm-inline-form" data-action="update_listing_status" data-listing-id="{listing["id"]}">
        <label class="om-field"><span class="om-field-label">Status</span>
          <select name="status" class="om-input">{_status_options(listing["status"])}</select>
        </label>
        <button type="submit" class="btn-primary adm-btn-sm">Save</button>
      </form>

      <div class="adm-listing-body" data-tabscope>
        <div class="db-tabs">
          <button type="button" class="db-tab active" data-tab="marketing">Marketing</button>
          <button type="button" class="db-tab" data-tab="offers">Number of Offers</button>
          <button type="button" class="db-tab" data-tab="openhouses">Open Houses &amp; Showings</button>
          <button type="button" class="db-tab" data-tab="feedback">Buyer Feedback</button>
        </div>

        <div class="db-tab-panel" data-tab-panel="marketing">
          <div class="db-stats" style="margin-bottom:18px">{summary_stats_html}</div>
          <p class="db-empty-note" style="margin-bottom:14px">Showings, groups, and offers come from the Open Houses &amp; Showings and Number of Offers tabs -- log them there and these update automatically.</p>
          <form class="adm-inline-form" data-action="update_marketing" data-listing-id="{listing["id"]}">
            <label class="om-field"><span class="om-field-label">Agents Reached</span>
              <input type="number" min="0" name="agents_reached_count" class="om-input" value="{listing["agents_reached_count"]}">
            </label>
            {_metric_inputs_html(metric_types, listing["metric_values"])}
            <button type="submit" class="btn-primary adm-btn-sm">Save</button>
          </form>
        </div>

        <div class="db-tab-panel" data-tab-panel="offers" style="display:none">
          {offers_html}
          <form class="adm-inline-form" data-action="add_offer" data-listing-id="{listing["id"]}">
            <input type="number" name="price" class="om-input" placeholder="Price" min="0" step="1" required>
            <label class="om-field"><span class="om-field-label">Financing</span>
              <select name="financing_type" class="om-input"><option value="cash">Cash</option><option value="loan">Loan</option></select>
            </label>
            <label class="om-field"><span class="om-field-label">Close of Escrow</span>
              <input type="date" name="close_of_escrow" class="om-input">
            </label>
            <button type="submit" class="btn-primary adm-btn-sm">+ Add Offer</button>
          </form>
        </div>

        <div class="db-tab-panel" data-tab-panel="openhouses" style="display:none">
          {open_houses_html}
          <form class="adm-inline-form" data-action="add_open_house" data-listing-id="{listing["id"]}">
            <label class="om-field"><span class="om-field-label">Date</span>
              <input type="date" name="event_date" class="om-input" required>
            </label>
            <label class="om-field"><span class="om-field-label">Groups Through</span>
              <input type="number" min="0" name="groups_count" class="om-input" value="0">
            </label>
            <input type="text" name="notes" class="om-input" placeholder="Notes (optional)" maxlength="2000">
            <button type="submit" class="btn-primary adm-btn-sm">+ Log Open House</button>
          </form>
        </div>

        <div class="db-tab-panel" data-tab-panel="feedback" style="display:none">
          {feedback_html}
          <form class="adm-inline-form" data-action="add_feedback" data-listing-id="{listing["id"]}">
            <label class="om-field"><span class="om-field-label">Type</span>
              <select name="category" class="om-input">{_category_options()}</select>
            </label>
            <input type="text" name="note" class="om-input" placeholder="Feedback note…" maxlength="2000" required>
            <button type="submit" class="btn-primary adm-btn-sm">Add</button>
          </form>
        </div>
      </div>
    </div>"""


def _client_admin_html(client, metric_types):
    listings_html = "".join(_listing_admin_html(l, metric_types) for l in client["listings"]) or '<p class="db-empty-note">No listings yet.</p>'
    status_label = "Active" if client["active"] else "Deactivated"
    return f"""
  <details class="adm-client">
    <summary>
      <span class="adm-client-email">{html.escape(client["email"])}</span>
      {f'<span class="adm-client-name">{html.escape(client["name"])}</span>' if client["name"] else ''}
      <span class="om-status">{status_label}</span>
    </summary>
    <div class="adm-client-body">
      <form class="adm-inline-form" data-action="update_client_email" data-id="{client["id"]}" style="margin-bottom:20px">
        <label class="om-field"><span class="om-field-label">Seller Email</span>
          <input type="email" name="email" class="om-input" value="{html.escape(client["email"])}" required>
        </label>
        <button type="submit" class="btn-primary adm-btn-sm">Save Email</button>
      </form>
      {listings_html}
      <form class="adm-inline-form" data-action="create_listing" data-client-id="{client["id"]}">
        <input type="text" name="address" class="om-input" placeholder="New listing address" maxlength="{MAX_FIELD_LEN}" required>
        <button type="submit" class="btn-primary adm-btn-sm">Add Listing</button>
      </form>
      <form class="adm-inline-form" data-action="reset_client_password" data-id="{client["id"]}" style="margin-top:14px">
        <input type="text" name="password" class="om-input" placeholder="New password for this seller" required minlength="{MIN_PASSWORD_LEN}">
        <button type="submit" class="btn-primary adm-btn-sm">Reset Password</button>
      </form>
      <button type="button" class="om-logout adm-toggle-active" data-action="toggle_client_active" data-id="{client["id"]}" style="margin-top:14px">{"Deactivate" if client["active"] else "Reactivate"} this seller</button>
    </div>
  </details>"""


def _metric_type_admin_html(m):
    status_label = "Active" if m["active"] else "Hidden"
    return f"""
  <div class="adm-list-row">
    <span class="adm-client-email">{html.escape(m["name"])}</span>
    <div class="adm-list-row-actions">
      <span class="om-status">{status_label}</span>
      <button type="button" class="om-logout adm-toggle-active" data-action="toggle_metric_type_active" data-id="{m["id"]}">{"Hide" if m["active"] else "Show"}</button>
    </div>
  </div>"""


def _toolbox_link_admin_html(link):
    status_label = "Active" if link["active"] else "Hidden"
    return f"""
  <form class="adm-inline-form" data-action="update_toolbox_link" data-id="{link["id"]}">
    <input type="text" name="name" class="om-input" value="{html.escape(link['name'])}" placeholder="Button name" required>
    <input type="url" name="url" class="om-input" value="{html.escape(link['url'])}" placeholder="https://..." required>
    <input type="number" name="sort_order" class="om-input" value="{link['sort_order']}" style="max-width:80px" title="Sort order">
    <button type="submit" class="btn-primary adm-btn-sm">Save</button>
    <span class="om-status">{status_label}</span>
    <button type="button" class="om-logout adm-toggle-active" data-action="toggle_toolbox_link_active" data-id="{link["id"]}">{"Hide" if link["active"] else "Show"}</button>
    <button type="button" class="om-logout adm-delete-btn" data-action="delete_toolbox_link" data-id="{link["id"]}">Delete</button>
  </form>"""


def build_admin_html(clients, metric_types, toolbox_links):
    active_clients = [c for c in clients if c["active"]]
    history_clients = [c for c in clients if not c["active"]]

    clients_html = "".join(_client_admin_html(c, metric_types) for c in active_clients) or '<div class="om-empty">No active sellers -- add one above.</div>'
    history_clients_html = "".join(_client_admin_html(c, metric_types) for c in history_clients) or '<div class="om-empty">No deactivated sellers.</div>'
    metric_html = "".join(_metric_type_admin_html(m) for m in metric_types) or '<p class="db-empty-note">No marketing metrics yet -- add one below (e.g. "Online Reactions", "Zillow Saves").</p>'

    active_toolbox_links = [t for t in toolbox_links if t["active"]]
    toolbox_buttons_html = "".join(
        f'<a href="{html.escape(t["url"])}" target="_blank" rel="noopener noreferrer" class="btn-primary adm-toolbox-btn">{html.escape(t["name"])}</a>'
        for t in active_toolbox_links
    )
    toolbox_manage_html = "".join(_toolbox_link_admin_html(t) for t in toolbox_links) or '<p class="db-empty-note">No tools yet.</p>'

    body = f"""
<section class="section" style="padding-top:120px">
  <div class="wrap">
    <h1 class="adm-h1" style="margin-bottom:24px">Admin</h1>
    <div id="adm-notice" class="adm-notice" style="display:none"></div>

    <h2 class="db-section-title" style="font-size:.9rem;margin-bottom:14px">Toolbox</h2>
    <div class="adm-toolbox-buttons">
      <button type="button" class="adm-toolbox-add-btn" id="adm-toolbox-add-btn" aria-label="Add a tool" title="Add a tool">+</button>
      {toolbox_buttons_html}
    </div>
    <details class="adm-history" style="margin-top:12px">
      <summary>Manage tools</summary>
      <div class="adm-tiles" style="margin-top:12px">{toolbox_manage_html}</div>
    </details>

    <div class="adm-modal-overlay" id="adm-toolbox-modal-overlay" onclick="if(event.target===this)closeToolboxModal()">
      <div class="adm-modal">
        <button type="button" class="adm-modal-close" aria-label="Close" onclick="closeToolboxModal()">✕</button>
        <h2 class="adm-modal-title">Add a Tool</h2>
        <form id="adm-create-toolbox-form" class="adm-inline-form">
          <input type="text" name="name" class="om-input" placeholder="Button name" required style="flex-basis:100%">
          <input type="url" name="url" class="om-input" placeholder="https://..." required style="flex-basis:100%">
          <input type="number" name="sort_order" class="om-input" placeholder="Sort order" value="0" style="flex-basis:100%">
          <button type="submit" class="btn-primary adm-btn-sm" style="width:100%;justify-content:center">Add Tool</button>
        </form>
      </div>
    </div>

    <h2 class="db-section-title" style="font-size:.9rem;margin:40px 0 16px">Sellers</h2>
    <div class="adm-panel">
      <div class="db-section-title">Create New Seller</div>
      <form id="adm-create-client-form" class="adm-inline-form">
        <input type="email" name="email" class="om-input" placeholder="Seller email" required>
        <input type="text" name="name" class="om-input" placeholder="Seller name">
        <input type="text" name="password" class="om-input" placeholder="Password to assign" required minlength="{MIN_PASSWORD_LEN}">
        <button type="submit" class="btn-primary adm-btn-sm">Create Seller</button>
      </form>
      <div class="om-error" id="adm-create-client-error"></div>
    </div>
    <div class="adm-clients">{clients_html}</div>

    <details class="adm-history">
      <summary>History (deactivated sellers)</summary>
      <div class="adm-clients">{history_clients_html}</div>
    </details>

    <h2 class="db-section-title" style="font-size:.9rem;margin:40px 0 16px">Marketing Metrics</h2>
    <p class="adm-tagline" style="margin-bottom:16px">Define any metric you want tracked per listing (e.g. "Online Reactions", "Zillow Saves", "Ad Impressions") -- each shows up as a field under a listing's Marketing tab once added here.</p>
    <div class="adm-panel">
      <form id="adm-create-metric-form" class="adm-inline-form">
        <input type="text" name="name" class="om-input" placeholder="New metric name" required>
        <button type="submit" class="btn-primary adm-btn-sm">Add</button>
      </form>
    </div>
    <div class="adm-list">{metric_html}</div>

    <div style="text-align:center;margin-top:36px"><a href="/admin?logout=1" class="om-logout">Log out</a></div>
  </div>
</section>

<script>
async function adminPost(payload) {{
  const res = await fetch('/api/portal?section=admin', {{
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
    showNotice(`Seller created — send them: ${{email}} / ${{password}}`);
    window.location.reload();
  }} catch (err) {{
    errEl.textContent = err.message;
    errEl.style.display = 'block';
  }}
}});

document.getElementById('adm-create-metric-form').addEventListener('submit', async function (e) {{
  e.preventDefault();
  const form = e.target;
  try {{
    await adminPost({{action: 'create_metric_type', name: form.name.value.trim()}});
    window.location.reload();
  }} catch (err) {{
    alert(err.message);
  }}
}});

function openToolboxModal() {{ document.getElementById('adm-toolbox-modal-overlay').classList.add('on'); }}
function closeToolboxModal() {{ document.getElementById('adm-toolbox-modal-overlay').classList.remove('on'); }}
document.getElementById('adm-toolbox-add-btn').addEventListener('click', openToolboxModal);

document.getElementById('adm-create-toolbox-form').addEventListener('submit', async function (e) {{
  e.preventDefault();
  const form = e.target;
  const payload = {{action: 'create_toolbox_link'}};
  new FormData(form).forEach(function (v, k) {{ payload[k] = v; }});
  try {{
    await adminPost(payload);
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
    fd.forEach(function (value, key) {{ payload[key] = value; }});
    try {{
      await adminPost(payload);
      if (action === 'reset_client_password') {{
        showNotice(`Password reset — send them: ${{payload.password}}`);
      }}
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

document.querySelectorAll('.adm-delete-btn[data-action]').forEach(function (btn) {{
  btn.addEventListener('click', async function () {{
    if (!confirm('Delete this? This can\\'t be undone.')) return;
    try {{
      await adminPost({{action: btn.dataset.action, id: btn.dataset.id}});
      window.location.reload();
    }} catch (err) {{
      alert(err.message);
    }}
  }});
}});
</script>
<script>{DB_TABS_SCRIPT}</script>
"""
    return render_page(body, "Admin | Simone Marzullo")


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

    def _section(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        return params.get("section", [None])[0], query

    def do_GET(self):
        section, query = self._section()
        is_logout = "logout=1" in query

        if section == "admin":
            if is_logout:
                expired = f"{ADMIN_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"
                self._send_html(200, build_admin_login_html(), set_cookie=expired)
                return
            token = get_cookie(self.headers.get("Cookie", ""), ADMIN_COOKIE_NAME)
            if not verify_session_token(token, "admin"):
                self._send_html(200, build_admin_login_html())
                return
            conn = None
            try:
                conn = get_conn()
                if conn is None:
                    self._send_html(503, build_error_html("The admin panel isn't set up yet -- POSTGRES_URL is missing.", "Admin | Simone Marzullo"))
                    return
                clients, metric_types = fetch_all_clients(conn)
                toolbox_links = fetch_all_toolbox_links(conn)
            except Exception as e:
                print(f"portal(admin): failed to load data: {e}")
                self._send_html(503, build_error_html("Something went wrong loading the admin panel.", "Admin | Simone Marzullo"))
                return
            finally:
                if conn:
                    conn.close()
            self._send_html(200, build_admin_html(clients, metric_types, toolbox_links))
            return

        # Default: client dashboard (section == "dashboard" or unset)
        if is_logout:
            expired = f"{CLIENT_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"
            self._send_html(200, build_client_login_html(), set_cookie=expired)
            return
        token = get_cookie(self.headers.get("Cookie", ""), CLIENT_COOKIE_NAME)
        client_id = verify_session_token(token, "client")
        if not client_id:
            self._send_html(200, build_client_login_html())
            return
        conn = None
        try:
            conn = get_conn()
            if conn is None:
                self._send_html(503, build_error_html("The dashboard isn't set up yet -- please contact Simone directly.", "Client Dashboard | Simone Marzullo"))
                return
            data = fetch_dashboard_data(conn, client_id)
        except Exception as e:
            print(f"portal(dashboard): failed to load data: {e}")
            self._send_html(503, build_error_html("Something went wrong loading your dashboard. Please try again shortly.", "Client Dashboard | Simone Marzullo"))
            return
        finally:
            if conn:
                conn.close()
        if not data:
            expired = f"{CLIENT_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"
            self._send_html(200, build_client_login_html(), set_cookie=expired)
            return
        self._send_html(200, build_client_dashboard_html(data))

    def do_POST(self):
        section, _ = self._section()

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

        if section == "admin":
            self._handle_admin_post(data)
            return

        # Default: client dashboard login
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
            print(f"portal(dashboard): login lookup failed: {e}")
            self._send_json(503, {"ok": False, "error": "Something went wrong. Please try again shortly."})
            return
        finally:
            if conn:
                conn.close()

        if not client or not client["active"] or not verify_password(password, client["password_hash"]):
            self._send_json(401, {"ok": False, "error": "Incorrect email or password."})
            return

        push_dashboard_login_to_followupboss(client["email"], client["name"])
        cookie = f"{CLIENT_COOKIE_NAME}={make_session_token('client', client['id'])}; Path=/; Max-Age={SESSION_HOURS['client'] * 3600}; HttpOnly; Secure; SameSite=Lax"
        self._send_json(200, {"ok": True}, set_cookie=cookie)

    def _handle_admin_post(self, data):
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
            cookie = f"{ADMIN_COOKIE_NAME}={make_session_token('admin')}; Path=/; Max-Age={SESSION_HOURS['admin'] * 3600}; HttpOnly; Secure; SameSite=Lax"
            self._send_json(200, {"ok": True}, set_cookie=cookie)
            return

        token = get_cookie(self.headers.get("Cookie", ""), ADMIN_COOKIE_NAME)
        if not verify_session_token(token, "admin"):
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
                    self._send_json(400, {"ok": False, "error": "A seller with that email already exists."})
                    return
                self._send_json(200, {"ok": True})
                return

            if action == "toggle_client_active":
                toggle_client_active(conn, int(data.get("id")))
                self._send_json(200, {"ok": True})
                return

            if action == "reset_client_password":
                client_id = int(data.get("id"))
                password = str(data.get("password", ""))
                if len(password) < MIN_PASSWORD_LEN:
                    self._send_json(400, {"ok": False, "error": f"Password must be at least {MIN_PASSWORD_LEN} characters."})
                    return
                reset_client_password(conn, client_id, password)
                self._send_json(200, {"ok": True})
                return

            if action == "update_client_email":
                client_id = int(data.get("id"))
                email = clean(data.get("email")).lower()
                if not email or not EMAIL_RE.match(email):
                    self._send_json(400, {"ok": False, "error": "Enter a valid email address."})
                    return
                try:
                    update_client_email(conn, client_id, email)
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
                    self._send_json(400, {"ok": False, "error": "A seller with that email already exists."})
                    return
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

            if action == "update_listing_status":
                listing_id = int(data.get("listingId"))
                status = clean(data.get("status"), 40) or "Active"
                if status not in LISTING_STATUSES:
                    status = "Active"
                update_listing_status(conn, listing_id, status)
                self._send_json(200, {"ok": True})
                return

            if action == "update_listing_address":
                listing_id = int(data.get("listingId"))
                address = clean(data.get("address"))
                if not address:
                    self._send_json(400, {"ok": False, "error": "Address is required."})
                    return
                update_listing_address(conn, listing_id, address)
                self._send_json(200, {"ok": True})
                return

            if action == "update_marketing":
                listing_id = int(data.get("listingId"))
                agents_reached = max(0, int(data.get("agents_reached_count") or 0))
                update_marketing(conn, listing_id, agents_reached)
                for key, value in data.items():
                    if not key.startswith("metric_"):
                        continue
                    try:
                        metric_type_id = int(key[len("metric_"):])
                        metric_value = max(0, int(value or 0))
                    except (ValueError, TypeError):
                        continue
                    upsert_listing_metric(conn, listing_id, metric_type_id, metric_value)
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

            if action == "update_feedback":
                feedback_id = int(data.get("id"))
                category = clean(data.get("category"), 40)
                if category not in FEEDBACK_CATEGORIES:
                    self._send_json(400, {"ok": False, "error": "Invalid feedback category."})
                    return
                note = clean(data.get("note"), 2000)
                if not note:
                    self._send_json(400, {"ok": False, "error": "Note can't be empty."})
                    return
                update_feedback(conn, feedback_id, category, note)
                self._send_json(200, {"ok": True})
                return

            if action == "delete_feedback":
                delete_feedback(conn, int(data.get("id")))
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
                add_offer(conn, listing_id, price, financing_type, close_of_escrow)
                self._send_json(200, {"ok": True})
                return

            if action == "add_open_house":
                listing_id = int(data.get("listingId"))
                event_raw = clean(data.get("event_date"), 20)
                if not event_raw:
                    self._send_json(400, {"ok": False, "error": "Date is required."})
                    return
                try:
                    event_date = date.fromisoformat(event_raw)
                except ValueError:
                    self._send_json(400, {"ok": False, "error": "Invalid date."})
                    return
                groups_count = max(0, int(data.get("groups_count") or 0))
                notes = clean(data.get("notes"), 2000)
                create_open_house(conn, listing_id, event_date, groups_count, notes)
                self._send_json(200, {"ok": True})
                return

            if action == "create_metric_type":
                name = clean(data.get("name"), 200)
                if not name:
                    self._send_json(400, {"ok": False, "error": "Name can't be empty."})
                    return
                create_metric_type(conn, name)
                self._send_json(200, {"ok": True})
                return

            if action == "toggle_metric_type_active":
                toggle_metric_type_active(conn, int(data.get("id")))
                self._send_json(200, {"ok": True})
                return

            if action == "create_toolbox_link":
                name = clean(data.get("name"))
                url = clean(data.get("url"), 2000)
                sort_order = int(data.get("sort_order") or 0)
                if not name or not url:
                    self._send_json(400, {"ok": False, "error": "Name and URL are required."})
                    return
                create_toolbox_link(conn, name, url, sort_order)
                self._send_json(200, {"ok": True})
                return

            if action == "update_toolbox_link":
                link_id = int(data.get("id"))
                name = clean(data.get("name"))
                url = clean(data.get("url"), 2000)
                sort_order = int(data.get("sort_order") or 0)
                if not name or not url:
                    self._send_json(400, {"ok": False, "error": "Name and URL are required."})
                    return
                update_toolbox_link(conn, link_id, name, url, sort_order)
                self._send_json(200, {"ok": True})
                return

            if action == "toggle_toolbox_link_active":
                toggle_toolbox_link_active(conn, int(data.get("id")))
                self._send_json(200, {"ok": True})
                return

            if action == "delete_toolbox_link":
                delete_toolbox_link(conn, int(data.get("id")))
                self._send_json(200, {"ok": True})
                return

            self._send_json(400, {"ok": False, "error": "Unknown action."})
        except (TypeError, ValueError):
            self._send_json(400, {"ok": False, "error": "Invalid request data."})
        except Exception as e:
            print(f"portal(admin): action '{action}' failed: {e}")
            self._send_json(500, {"ok": False, "error": "Something went wrong."})
        finally:
            if conn:
                conn.close()

    def log_message(self, fmt, *args):
        pass
