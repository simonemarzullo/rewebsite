import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _layout import render_page, make_handler, breadcrumb_html, breadcrumb_schema, AREAS, SITE_URL

TITLE = "Los Angeles Westside Area Guides | Simone Marzullo"
DESCRIPTION = "Neighborhood guides to the Los Angeles Westside — Santa Monica, Venice, Beverly Hills, Bel Air, Malibu, Culver City, Century City, Mar Vista, and Hollywood — from Simone Marzullo, The Agency."
CANONICAL_URL = SITE_URL + "/areas"


def build_html():
    cards = "".join(f"""
    <a class="hub-card" href="/areas/{a['slug']}">
      <img class="hub-card-img" src="{a['photo']}" alt="{a['name']}, Los Angeles" loading="lazy">
      <div class="hub-card-scrim"></div>
      <div class="hub-card-content">
        <div class="hub-card-kicker">{a['region_label']}</div>
        <div class="hub-card-name">{a['name']}</div>
        <div class="hub-card-tags">{a['tags']}</div>
      </div>
    </a>""" for a in AREAS)

    body = f"""
<section class="hub-hero">
  <div class="wrap">
    {breadcrumb_html([("Home", "/"), ("Areas", None)], dark=True)}
    <span class="label label-red">The Westside</span>
    <h1 class="action-title">Los Angeles Westside area guides</h1>
    <p>Not every neighborhood asks the same of a home — or of its owner. Start with the areas that define coastal and central Westside living, from Santa Monica to Bel Air.</p>
  </div>
</section>
<section class="section">
  <div class="wrap">
    <div class="hub-grid">
      {cards}
    </div>
  </div>
</section>
<section class="section section-alt">
  <div class="wrap" style="text-align:center;max-width:640px">
    <span class="label label-red">Not Sure Where to Start?</span>
    <h2 class="action-title" style="margin-top:14px">Let's talk through your options</h2>
    <p style="font-size:.88rem;color:var(--g5);font-weight:300;line-height:1.8;margin-top:16px">Every Westside neighborhood trades off differently between privacy, walkability, schools, and commute. Tell me what matters most and I'll help you narrow it down.</p>
    <div style="margin-top:28px"><a href="/contact" class="btn-primary" style="background:var(--red);color:#fff;display:inline-flex;padding:14px 32px;font-size:.68rem;letter-spacing:.2em;text-transform:uppercase;font-weight:500">Ask a Question</a></div>
  </div>
</section>"""

    schema = breadcrumb_schema([("Home", SITE_URL + "/"), ("Areas", CANONICAL_URL)])
    return render_page(TITLE, DESCRIPTION, CANONICAL_URL, body, active_nav="AREAS", extra_schema=schema)


handler = make_handler(build_html)
