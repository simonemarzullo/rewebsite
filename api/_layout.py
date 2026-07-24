"""Shared rendering helpers for the /areas hub + area guide pages.

Not a route itself -- Vercel's Python runtime skips files/directories that
start with an underscore when building serverless functions, so this module
is only ever imported by the real handlers (api/areas.py, api/area-*.py).

NOTE ON PHOTOGRAPHY: no licensed per-neighborhood photography exists yet, so
every hero below reuses the single existing site photo (assets/hero-skyline-day.jpg)
as a placeholder. Swap AREAS[n]["photo"] for real, licensed photography per
area once it's available.
"""

from http.server import BaseHTTPRequestHandler
from html import escape

SITE_URL = "https://www.marzullore.com"
AGENT_SCHEMA = {
    "@type": "RealEstateAgent",
    "name": "Simone Marzullo",
    "url": SITE_URL + "/",
    "image": SITE_URL + "/assets/headshot.jpg",
    "telephone": "+1-310-696-6596",
    "email": "Simone@SimoneMarzullo.com",
    "address": {
        "@type": "PostalAddress",
        "streetAddress": "331 Foothill Rd. #100",
        "addressLocality": "Beverly Hills",
        "addressRegion": "CA",
        "postalCode": "90210",
        "addressCountry": "US",
    },
    "areaServed": {"@type": "City", "name": "Los Angeles"},
    "knowsLanguage": ["en", "it"],
    "memberOf": {"@type": "Organization", "name": "The Agency", "url": "https://www.theagencyre.com/"},
}

# Single source of truth for every area page -- used by the /areas hub grid,
# each page's "related areas" module, and the homepage teaser section.
AREAS = [
    {"slug": "santa-monica", "name": "Santa Monica", "region_label": "Coastal City",
     "tags": "COAST · DOWNTOWN", "photo": "/assets/hero-skyline-day.jpg"},
    {"slug": "venice", "name": "Venice", "region_label": "Canals & Coast",
     "tags": "WALK-STREETS · CREATIVE", "photo": "/assets/hero-skyline-day.jpg"},
    {"slug": "beverly-hills", "name": "Beverly Hills", "region_label": "Global Address",
     "tags": "FLATS · TROUSDALE", "photo": "/assets/hero-skyline-day.jpg"},
    {"slug": "bel-air", "name": "Bel Air", "region_label": "Westside Hills",
     "tags": "ESTATES · VIEWS", "photo": "/assets/hero-skyline-day.jpg"},
    {"slug": "malibu", "name": "Malibu", "region_label": "Coastal Estates",
     "tags": "COAST · COMPOUNDS", "photo": "/assets/hero-skyline-day.jpg"},
    {"slug": "culver-city", "name": "Culver City", "region_label": "Media & Downtown",
     "tags": "MEDIA · DOWNTOWN", "photo": "/assets/hero-skyline-day.jpg"},
    {"slug": "century-city", "name": "Century City", "region_label": "Towers & Amenities",
     "tags": "TOWERS · AMENITIES", "photo": "/assets/hero-skyline-day.jpg"},
    {"slug": "mar-vista", "name": "Mar Vista", "region_label": "Character & Community",
     "tags": "CHARACTER · COMMUNITY", "photo": "/assets/hero-skyline-day.jpg"},
    {"slug": "hollywood", "name": "Hollywood", "region_label": "Hills & Iconic",
     "tags": "HILLS · ICONIC", "photo": "/assets/hero-skyline-day.jpg"},
]
AREAS_BY_SLUG = {a["slug"]: a for a in AREAS}

NAV_ITEMS = [
    ("START", "/home"),
    ("AREAS", "/areas"),
    ("ABOUT", "/about"),
    ("THE AGENCY", "/#the-agency"),
    ("CALCULATOR", "/flipcalculator"),
    ("CONTACT", "/contact"),
]

THEME_INIT_SCRIPT = """<script>if ('scrollRestoration' in history) history.scrollRestoration = 'manual';</script>
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


def _nav_html(active):
    links = []
    for label, href in NAV_ITEMS:
        cls = ' class="active"' if label == active else ""
        links.append(f'<li><a href="{href}"{cls}>{label}</a></li>')
    mobile_links = []
    for label, href in NAV_ITEMS:
        mobile_links.append(f'<a href="{href}">{label.title()}</a>')
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


def breadcrumb_html(trail, dark=False):
    """trail: list of (label, href-or-None-for-current)"""
    cls = "breadcrumb breadcrumb-dark" if dark else "breadcrumb"
    parts = []
    for i, (label, href) in enumerate(trail):
        if i:
            parts.append('<span class="sep">/</span>')
        if href:
            parts.append(f'<a href="{href}">{escape(label)}</a>')
        else:
            parts.append(f"<span>{escape(label)}</span>")
    return f'<div class="{cls}">{"".join(parts)}</div>'


def breadcrumb_schema(trail_with_urls):
    """trail_with_urls: list of (name, url)"""
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": name, "item": url}
            for i, (name, url) in enumerate(trail_with_urls)
        ],
    }


def render_page(title, description, canonical, body_html, active_nav=None,
                 extra_schema=None, og_image=None):
    schema_graph = [AGENT_SCHEMA]
    if extra_schema:
        schema_graph.extend(extra_schema if isinstance(extra_schema, list) else [extra_schema])
    import json
    schema_json = json.dumps({"@context": "https://schema.org", "@graph": schema_graph}, indent=None)
    og_image = og_image or (SITE_URL + "/assets/agency-logo.png")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{THEME_INIT_SCRIPT}
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>{escape(title)}</title>
<meta name="description" content="{escape(description)}">
<link rel="canonical" href="{canonical}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Simone Marzullo | The Agency">
<meta property="og:title" content="{escape(title)}">
<meta property="og:description" content="{escape(description)}">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{og_image}">
<meta name="twitter:card" content="summary">
<meta name="twitter:image" content="{og_image}">
<meta name="theme-color" content="#000000">
<link rel="manifest" href="/manifest.json">
<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">
<link rel="icon" type="image/png" sizes="32x32" href="/assets/favicon-32.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=EB+Garamond:ital,wght@0,400;0,500;1,400;1,500&family=Inter:wght@300;400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/assets/areas.css">
<script type="application/ld+json">{schema_json}</script>
</head>
<body>
{_nav_html(active_nav)}
{body_html}
{_footer_html()}
{FOOTER_AND_SCRIPTS}
</body>
</html>"""


def related_areas_html(current_slug, count=3):
    others = [a for a in AREAS if a["slug"] != current_slug][:count]
    cards = "".join(f"""
      <a class="area-related-card" href="/areas/{a['slug']}">
        <div class="area-related-kicker">{a['region_label']}</div>
        <div class="area-related-name">{a['name']}</div>
        <div class="area-related-tags">{a['tags']}</div>
      </a>""" for a in others)
    return f"""<section class="section section-alt">
  <div class="wrap">
    <span class="label label-red">Nearby</span>
    <h2 class="action-title" style="margin-bottom:28px;margin-top:14px">Related Westside areas</h2>
    <div class="area-related-grid">{cards}</div>
  </div>
</section>"""


def render_area_page(data):
    slug = data["slug"]
    area = AREAS_BY_SLUG[slug]
    tags_html = "".join(f'<span class="area-tag">{t}</span>' for t in data["tags"])
    intro_html = "".join(f"<p>{p}</p>" for p in data["intro"])
    pulse_rows = "".join(f"""
      <div class="market-pulse-row">
        <div class="market-pulse-lbl">{lbl}</div>
        <div class="market-pulse-val">{val}</div>
      </div>""" for lbl, val in data["market_pulse"])
    why_cards = "".join(f"""
      <div class="area-card">
        <div class="area-card-kicker">Why buyers choose {data['name']}</div>
        <div class="area-card-title">{title}</div>
        <div class="area-card-body">{body}</div>
      </div>""" for title, body in data["why_choose"])
    stand_out_cards = "".join(f"""
      <div class="area-card">
        <div class="area-card-title">{title}</div>
        <div class="area-card-body">{body}</div>
      </div>""" for title, body in data["what_stands_out"])
    day_items = "".join(f'<div class="area-list-item"><span class="area-list-dot"></span>{i}</div>' for i in data["day_to_day"])
    find_items = "".join(f'<div class="area-list-item"><span class="area-list-dot"></span>{i}</div>' for i in data["what_youll_find"])
    faq_items = "".join(f"""
      <div class="area-faq-item">
        <div class="area-faq-q">{q}</div>
        <div class="area-faq-a">{a}</div>
      </div>""" for q, a in data["faq"])

    body = f"""
<section class="area-hero">
  <img class="area-hero-img" src="{area['photo']}" alt="{data['name']}, Los Angeles" loading="eager">
  <div class="area-hero-scrim"></div>
  <div class="area-hero-content">
    {breadcrumb_html([("Home", "/"), ("Areas", "/areas"), (data['name'], None)])}
    <div class="area-eyebrow"><span class="area-eyebrow-line"></span><span class="area-eyebrow-text">{area['region_label']}</span></div>
    <h1 class="area-h1">{data['name']}</h1>
    <p class="area-tagline">{data['tagline']}</p>
    <div class="area-tags">{tags_html}</div>
    <div class="area-hero-actions">
      <a href="/buy" class="btn-primary">Homes in {data['name']}</a>
      <a href="/sell" class="btn-hero-outline">Sell Here</a>
    </div>
  </div>
</section>

<section class="section">
  <div class="wrap">
    <div class="area-intro-grid">
      <div>
        <span class="label label-red">Area Guide</span>
        <h2 class="action-title" style="margin:14px 0 22px">Living in {data['name']}</h2>
        <div class="area-intro-copy">{intro_html}</div>
        <div class="area-callout">
          <div class="area-callout-label">Who Thrives Here</div>
          <p>{data['who_thrives']}</p>
        </div>
      </div>
      <div class="market-pulse">
        <div class="market-pulse-head">
          <span class="label">Market Pulse</span>
          <div class="market-pulse-title">How this market feels</div>
        </div>
        {pulse_rows}
        <div class="market-pulse-note">{data['market_pulse_note']}</div>
        <a href="/contact" class="market-pulse-cta">Ask About Current Inventory</a>
      </div>
    </div>
  </div>
</section>

<section class="section section-alt">
  <div class="wrap">
    <span class="label label-red">For Home Buyers</span>
    <h2 class="action-title" style="margin:14px 0 12px">Why choose {data['name']}</h2>
    <p style="font-size:.85rem;color:var(--g5);font-weight:300;max-width:640px;margin-bottom:32px">Practical reasons buyers put this area on their shortlist — lifestyle, commute, privacy, and long-term fit.</p>
    <div class="area-cards-grid">{why_cards}</div>
  </div>
</section>

<section class="section">
  <div class="wrap">
    <span class="label label-red">Inside the Area</span>
    <h2 class="action-title" style="margin:14px 0 28px">What stands out</h2>
    <div class="area-cards-grid area-cards-grid-3">{stand_out_cards}</div>
  </div>
</section>

<section class="section section-alt">
  <div class="wrap">
    <div class="area-list-grid">
      <div>
        <span class="label">Lifestyle</span>
        <h3 class="action-title" style="font-size:1.4rem;margin:10px 0 20px">Day-to-day</h3>
        <div class="area-list">{day_items}</div>
      </div>
      <div>
        <span class="label">Property Types</span>
        <h3 class="action-title" style="font-size:1.4rem;margin:10px 0 20px">What you'll find</h3>
        <div class="area-list">{find_items}</div>
      </div>
    </div>
  </div>
</section>

<section class="section">
  <div class="wrap">
    <span class="label label-red">Common Questions</span>
    <h2 class="action-title" style="margin:14px 0 28px">{data['name']} FAQ</h2>
    <div class="area-faq">{faq_items}</div>
  </div>
</section>

{related_areas_html(slug)}
"""
    return body


def make_handler(build_fn):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                html = build_fn()
            except Exception:
                self.send_error(500)
                return
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass

    return Handler
