import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _layout import render_page, render_area_page, make_handler, breadcrumb_schema, SITE_URL

SLUG = "mar-vista"
TITLE = "Mar Vista Real Estate | Simone Marzullo, The Agency"
DESCRIPTION = "Mar Vista homes for sale and area guide — mid-century Eichler homes and a family-friendly community feel, from Simone Marzullo, The Agency."
CANONICAL_URL = SITE_URL + "/areas/" + SLUG

DATA = {
    "slug": SLUG,
    "name": "Mar Vista",
    "tagline": "Mid-century character and a tight-knit, family-friendly feel inland from the coast.",
    "tags": ["CHARACTER", "COMMUNITY", "MID-CENTURY"],
    "intro": [
        "Mar Vista sits just inland from Venice and Santa Monica, and is known in particular for a cluster of Eichler-built mid-century modern homes — post-and-beam construction with atriums and clean, indoor-outdoor lines that have drawn a dedicated following of design-focused buyers.",
        "The neighborhood has a genuinely family-friendly, community feel, anchored by a well-attended weekly farmers market and a growing restaurant and retail corridor along Venice and Centinela Boulevards.",
        "Relative to Venice and Santa Monica, Mar Vista has historically offered more house for the price while still sitting within easy reach of the beach, the 405, and the 10 — though that gap has narrowed as the neighborhood's mid-century homes have drawn wider attention.",
    ],
    "who_thrives": "Families and design-focused buyers looking for character architecture and a community feel, inland enough for relative value but still close to the coast.",
    "market_pulse": [
        ("DEMAND", "Rising, especially for Eichler mid-century homes"),
        ("INVENTORY STYLE", "Mostly single-family, mid-century and postwar stock"),
        ("BUYER MIX", "Families, design-focused buyers, move-up buyers"),
    ],
    "market_pulse_note": "Eichler and other mid-century homes in Mar Vista often command a distinct premium over comparable postwar stock nearby — worth evaluating separately in any comp set.",
    "why_choose": [
        ("Mid-century architecture", "A well-known cluster of Eichler-built post-and-beam homes gives Mar Vista a distinct architectural identity within the Westside."),
        ("Community feel", "A well-attended weekly farmers market and a tight-knit residential character set it apart from denser neighboring areas."),
        ("Relative value", "Historically more accessible pricing than Venice or Santa Monica for comparable square footage, though the gap has narrowed."),
        ("Freeway access", "Proximity to both the 405 and 10 supports commutes across the Westside and toward Downtown LA."),
    ],
    "what_stands_out": [
        ("Eichler homes", "A recognized cluster of mid-century post-and-beam homes with atriums and indoor-outdoor design."),
        ("Farmers market anchor", "A long-running weekly market that functions as a genuine community gathering point."),
        ("Growing dining corridor", "Venice and Centinela Boulevards have seen a steady rise in restaurants and retail."),
    ],
    "day_to_day": [
        "Weekly farmers market",
        "Growing restaurant corridor on Venice/Centinela",
        "Easy access to the 405 and 10 freeways",
        "Short drive to Venice and Santa Monica beaches",
    ],
    "what_youll_find": [
        "Eichler-built mid-century modern homes",
        "Postwar single-family homes",
        "Duplexes and small multi-family properties",
        "Select newer infill construction",
    ],
    "faq": [
        ("What is an Eichler home?", "A mid-century modern house built to a post-and-beam design popularized by developer Joseph Eichler, typically featuring an atrium and strong indoor-outdoor sightlines; Mar Vista has one of the more recognized Eichler tracts in Los Angeles."),
        ("Is Mar Vista walkable?", "Parts of it, particularly near the farmers market and the Venice/Centinela corridor — but it's generally more car-dependent than Venice or downtown Santa Monica."),
    ],
}


def build_html():
    body = render_area_page(DATA)
    schema = breadcrumb_schema([("Home", SITE_URL + "/"), ("Areas", SITE_URL + "/areas"), (DATA["name"], CANONICAL_URL)])
    return render_page(TITLE, DESCRIPTION, CANONICAL_URL, body, active_nav="AREAS", extra_schema=schema)


handler = make_handler(build_html)
