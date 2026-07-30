"""Real estate news roundup, served at /news via a vercel.json rewrite to
/api/news (this file's real path). Self-contained (own nav/footer/theme
boilerplate, copied from api/areas.py / api/offmarket.py) with no
cross-file imports, matching the pattern already proven to work in
production for this project.

Fetches a handful of real, neutral LA real-estate RSS feeds live on every
request (no database, no scheduled job -- this stays a stateless
serverless function like every other page here) and renders the most
recent articles. To keep that from meaning "4 slow external HTTP calls on
every single page view," the response carries a Cache-Control header
telling Vercel's edge network to serve a cached copy for up to an hour
(and up to a day past that while quietly refetching in the background)
-- so almost every real visitor gets an instant cached response, and the
feeds only actually get hit roughly once an hour.
"""

import html
import json
import re
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler
from xml.etree import ElementTree

SITE_URL = "https://www.marzullore.com"
MAX_ARTICLES = 12
FETCH_TIMEOUT = 6

# Neutral LA real-estate news/trade sources only -- deliberately excludes
# other agents' personal blogs (several appear on generic "top RSS feeds"
# lists) since publishing a competitor's blog content here would be an
# odd look. Verified each of these actually serves fetchable RSS/XML
# (The Real Deal's feed 403s server-side requests, likely bot protection,
# so it isn't included despite showing up on most such lists).
NEWS_SOURCES = [
    {"name": "LA Times Real Estate", "url": "https://www.latimes.com/business/real-estate.rss"},
    {"name": "Urbanize LA", "url": "https://la.urbanize.city/rss.xml"},
    {"name": "LA Magazine Real Estate", "url": "https://lamag.com/tag/real-estate/feed/"},
    {"name": "Robb Report Celebrity Homes", "url": "https://robbreport.com/shelter/celebrity-homes/feed/"},
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
    title = "Real Estate News | Simone Marzullo, The Agency"
    description = "Los Angeles real estate news roundup, curated from local and national coverage -- brought to you by Simone Marzullo, REALTOR® with The Agency."
    canonical = SITE_URL + "/news"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{THEME_INIT_SCRIPT}
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description)}">
<link rel="canonical" href="{canonical}">
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


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
CONTENT_ENCODED_TAG = "{http://purl.org/rss/1.0/modules/content/}encoded"


def _plain_text(raw):
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


_BYLINE_DATE_RE = re.compile(
    r"^[.\s]*[A-Z][a-zA-Z'\-]+(?: [A-Z][a-zA-Z'\-]+){0,2}\s+"
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun),\s+\d{1,2}/\d{1,2}/\d{4}\s*-\s*\d{1,2}:\d{2}\s+"
)


def _strip_leading_title(text, title):
    """Some feeds (e.g. Urbanize LA) prefix the body text with the title,
    byline, and a publish timestamp again before the real content --
    strip that duplicate lead-in instead of just discarding the whole
    field, since real article text often follows it."""
    if not text or not title:
        return text
    bare_title = title.rstrip(".")
    if text.lower().startswith(bare_title.lower()):
        text = text[len(bare_title):].strip()
        text = _BYLINE_DATE_RE.sub("", text)
    return text


_TRAILING_BOILERPLATE_RE = re.compile(
    r"\s*Follow us on social media.*$", re.IGNORECASE | re.DOTALL
)


def _strip_trailing_boilerplate(text):
    """Urbanize LA's description field runs the real article straight into
    a "Follow us on social media" share block, then tag/image-caption CMS
    fields, all as one run-on paragraph once HTML is stripped -- cut
    everything from that share block onward."""
    return _TRAILING_BOILERPLATE_RE.sub("", text).strip()


def _truncate(text, max_len):
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "…"


def _parse_feed(source):
    req = urllib.request.Request(source["url"], headers={"User-Agent": "Mozilla/5.0 (compatible; MarzulloRE/1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            raw = resp.read()
        root = ElementTree.fromstring(raw)
    except Exception as e:
        print(f"news: failed to fetch/parse {source['name']}: {e}")
        return []

    items = []
    for item in root.findall(".//item")[:8]:
        title_el = item.find("title")
        link_el = item.find("link")
        date_el = item.find("pubDate")
        desc_el = item.find("description")
        content_el = item.find(CONTENT_ENCODED_TAG)
        title = (title_el.text or "").strip() if title_el is not None else ""
        link = (link_el.text or "").strip() if link_el is not None else ""
        if not title or not link:
            continue
        try:
            published = parsedate_to_datetime(date_el.text) if date_el is not None and date_el.text else None
        except (TypeError, ValueError):
            published = None

        # Prefer whichever field actually has more real text -- some feeds
        # (LA Magazine, and Urbanize LA via <description> itself) publish
        # the full article body for syndication; others (LA Times, Robb
        # Report) only ever include a one-line teaser. Either way this is
        # text the publisher chose to put in their own syndication feed,
        # not scraped from their website. When it's substantial (over
        # 1000 characters), show the whole thing in the popup -- no need
        # to send the visitor elsewhere to finish reading. When a feed
        # only ever gives a short teaser, there genuinely isn't more to
        # show, so the popup keeps a link to the source for that case.
        desc_text = _strip_trailing_boilerplate(_strip_leading_title(_plain_text(desc_el.text if desc_el is not None else ""), title))
        content_text = _strip_trailing_boilerplate(_strip_leading_title(_plain_text(content_el.text if content_el is not None else ""), title))
        best_text = content_text if len(content_text) > len(desc_text) else desc_text

        items.append({
            "title": title,
            "link": link,
            "source": source["name"],
            "published": published,
            "date_label": published.strftime("%b %-d, %Y") if published else "",
            "excerpt": _truncate(best_text, 170),
            "modal_excerpt": _truncate(best_text, 20000),
        })
    return items


def fetch_all_articles():
    articles = []
    with ThreadPoolExecutor(max_workers=len(NEWS_SOURCES)) as pool:
        futures = [pool.submit(_parse_feed, source) for source in NEWS_SOURCES]
        for future in as_completed(futures):
            articles.extend(future.result())

    # Sort newest first; undated items (a feed missing pubDate) sort last
    # rather than crashing the comparison against tz-aware datetimes.
    articles.sort(key=lambda a: a["published"] or _min_datetime(), reverse=True)
    return articles[:MAX_ARTICLES]


def _min_datetime():
    import datetime
    return datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)


def build_news_html():
    articles = fetch_all_articles()
    if articles:
        cards = "".join(f"""
      <button type="button" class="news-card" onclick="openArticleModal({i})">
        <div class="news-card-meta"><span class="news-card-source">{html.escape(a['source'])}</span>{f'<span class="news-card-date">{a["date_label"]}</span>' if a['date_label'] else ''}</div>
        <div class="news-card-title">{html.escape(a['title'])}</div>
        {f'<p class="news-card-excerpt">{html.escape(a["excerpt"])}</p>' if a['excerpt'] else ''}
        <span class="news-card-link">Read Article ↗</span>
      </button>""" for i, a in enumerate(articles))
        news_html = f'<div class="news-grid">{cards}</div>'
    else:
        news_html = '<div class="om-empty">Unable to load news right now &mdash; please check back shortly.</div>'

    # Embedded as JSON for the popup's JS to read from -- NOT the full
    # article body (RSS feeds only ever carry a short summary, precisely
    # so syndicating sites route readers back to the original source; the
    # popup shows that same summary plus a genuine link out, it doesn't
    # try to rehost anyone else's article).
    articles_json = json.dumps([
        {
            "title": a["title"],
            "source": a["source"],
            "date": a["date_label"],
            "excerpt": a["modal_excerpt"],
            "link": a["link"],
        }
        for a in articles
    ]).replace("</", "<\\/")

    body = f"""
<section class="area-hero om-hero-compact">
  <img class="area-hero-img" src="/assets/hero-skyline-day.jpg" alt="Los Angeles skyline at dusk" loading="eager" style="object-position:50% 55%">
  <div class="area-hero-scrim"></div>
  <div class="area-hero-content">
    <div class="area-eyebrow"><span class="area-eyebrow-line"></span><span class="area-eyebrow-text">Market Coverage</span></div>
    <h1 class="area-h1">Real Estate News</h1>
    <p class="area-tagline">The latest Los Angeles real estate coverage, curated from local and national sources.</p>
  </div>
</section>

<section class="section">
  <div class="wrap">
    {news_html}
  </div>
</section>

<div class="news-modal-overlay" id="news-modal-overlay" onclick="if(event.target===this)closeArticleModal()">
  <div class="news-modal">
    <button type="button" class="news-modal-close" aria-label="Close" onclick="closeArticleModal()">✕</button>
    <div class="news-modal-meta"><span class="news-card-source" id="news-modal-source"></span><span class="news-card-date" id="news-modal-date"></span></div>
    <h2 class="news-modal-title" id="news-modal-title"></h2>
    <p class="news-modal-excerpt" id="news-modal-excerpt"></p>
    <a class="btn-primary" id="news-modal-readmore" href="#" target="_blank" rel="noopener noreferrer" style="width:100%;justify-content:center;margin-top:6px">Read Full Article ↗</a>

    <div class="news-modal-divider"></div>

    <form id="news-email-form" novalidate>
      <div class="om-field">
        <span class="om-field-label">Email me this article</span>
        <input type="email" id="news-email-input" class="om-input" placeholder="you@example.com" required autocomplete="email" style="margin-top:6px">
        <input type="text" id="news-email-hp" style="position:absolute;left:-9999px" tabindex="-1" autocomplete="off">
      </div>
      <button type="submit" class="btn-primary" id="news-email-submit" style="width:100%;justify-content:center;margin-top:14px">Send</button>
      <div class="om-error" id="news-email-error"></div>
      <div class="news-modal-success" id="news-email-success" style="display:none">Sent — check your inbox shortly.</div>
    </form>
  </div>
</div>

<script>
const NEWS_ARTICLES = {articles_json};
let newsCurrentArticle = null;

function openArticleModal(i) {{
  const a = NEWS_ARTICLES[i];
  if (!a) return;
  newsCurrentArticle = a;
  document.getElementById('news-modal-source').textContent = a.source;
  document.getElementById('news-modal-date').textContent = a.date;
  document.getElementById('news-modal-title').textContent = a.title;
  const excerptEl = document.getElementById('news-modal-excerpt');
  excerptEl.textContent = a.excerpt;
  excerptEl.style.display = a.excerpt ? 'block' : 'none';
  // Only offer the external link when the popup doesn't already have the
  // full piece -- a short feed-provided teaser (under 1000 characters)
  // genuinely has more to read only on the source's own site; anything
  // longer than that is shown here in full already, so there's no reason
  // to send the visitor away.
  const readmoreEl = document.getElementById('news-modal-readmore');
  if (a.excerpt.length > 1000) {{
    readmoreEl.style.display = 'none';
  }} else {{
    readmoreEl.style.display = 'inline-flex';
    readmoreEl.href = a.link;
  }}
  document.getElementById('news-email-form').reset();
  document.getElementById('news-email-error').style.display = 'none';
  document.getElementById('news-email-success').style.display = 'none';
  document.getElementById('news-email-form').style.display = 'block';
  document.getElementById('news-modal-overlay').classList.add('on');
  document.body.style.overflow = 'hidden';
}}
function closeArticleModal() {{
  document.getElementById('news-modal-overlay').classList.remove('on');
  document.body.style.overflow = '';
}}
document.addEventListener('keydown', function (e) {{
  if (e.key === 'Escape') closeArticleModal();
}});
document.getElementById('news-email-form').addEventListener('submit', async function (e) {{
  e.preventDefault();
  if (!newsCurrentArticle) return;
  const hp = document.getElementById('news-email-hp').value;
  const errEl = document.getElementById('news-email-error');
  errEl.style.display = 'none';
  if (hp) {{
    document.getElementById('news-email-success').style.display = 'block';
    document.getElementById('news-email-form').style.display = 'none';
    return;
  }}
  const email = document.getElementById('news-email-input').value.trim();
  const btn = document.getElementById('news-email-submit');
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Sending…';
  try {{
    const res = await fetch('/api/submit-lead', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{
        formType: 'article',
        email: email,
        articleTitle: newsCurrentArticle.title,
        articleSource: newsCurrentArticle.source,
        articleLink: newsCurrentArticle.link,
        hp: hp,
      }}),
    }});
    if (!res.ok) throw new Error('failed');
    document.getElementById('news-email-success').style.display = 'block';
    document.getElementById('news-email-form').style.display = 'none';
  }} catch (err) {{
    errEl.textContent = 'Something went wrong. Please try again or contact Simone directly.';
    errEl.style.display = 'block';
    btn.disabled = false;
    btn.textContent = originalText;
  }}
}});
</script>
"""
    return render_page(body)


def build_html():
    return build_news_html()


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        html_body = build_news_html().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html_body)))
        # Cache at Vercel's edge for up to an hour, serving a stale copy
        # for up to a day while quietly refetching in the background --
        # real visitors get an instant cached response almost every time,
        # and the RSS feeds only actually get hit roughly hourly.
        self.send_header("Cache-Control", "public, s-maxage=3600, stale-while-revalidate=86400")
        self.end_headers()
        self.wfile.write(html_body)

    def log_message(self, fmt, *args):
        pass
