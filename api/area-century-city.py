import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _layout import render_page, render_area_page, make_handler, breadcrumb_schema, SITE_URL

SLUG = "century-city"
TITLE = "Century City Real Estate | Simone Marzullo, The Agency"
DESCRIPTION = "Century City condos and area guide — full-service towers at the center of the Westside's business and retail core, from Simone Marzullo, The Agency."
CANONICAL_URL = SITE_URL + "/areas/" + SLUG

DATA = {
    "slug": SLUG,
    "name": "Century City",
    "tagline": "High-rise living at the center of the Westside's business and retail core.",
    "tags": ["TOWERS", "AMENITIES", "CENTRAL"],
    "intro": [
        "Century City is built around its business district and the Westfield Century City retail center, and its residential market reflects that: this is primarily a high-rise and luxury-condo market rather than a single-family neighborhood, with full-service towers offering concierge, fitness, and building amenities.",
        "Its central location — bordering Beverly Hills, Westwood, and Beverly Grove — makes it a genuine commute hub, with relatively fast access to the Westside beach cities, Downtown LA via the 10, and UCLA.",
        "Buyers here are typically prioritizing a lock-and-leave lifestyle, building amenities, and central access over lot size or architectural character — a different value proposition than the single-family neighborhoods surrounding it.",
    ],
    "who_thrives": "Executives, empty-nesters, and buyers who want full-service, low-maintenance living centrally located between Beverly Hills, Westwood, and the Westside beach cities.",
    "market_pulse": [
        ("DEMAND", "Steady among lock-and-leave buyers"),
        ("INVENTORY STYLE", "Predominantly high-rise condo and tower units"),
        ("BUYER MIX", "Executives, empty-nesters, investors"),
    ],
    "market_pulse_note": "Century City pricing varies significantly by building, floor, and view orientation — tower-by-tower comparison is essential here.",
    "why_choose": [
        ("Full-service tower living", "Concierge, fitness centers, and building amenities reduce the maintenance burden of single-family ownership."),
        ("Central Westside location", "Bordering Beverly Hills, Westwood, and Beverly Grove gives fast access across much of the Westside."),
        ("Retail and dining at the door", "Westfield Century City puts shopping, dining, and a movie theater within walking distance for tower residents."),
        ("Commute efficiency", "Proximity to the 10 Freeway and Santa Monica Blvd corridor supports commutes toward Downtown LA, the Valley, and the beach cities."),
    ],
    "what_stands_out": [
        ("Tower amenity packages", "Full-service buildings often include pools, fitness centers, and 24-hour concierge or security."),
        ("Business-district energy", "A significant commercial and entertainment-industry business core operates alongside the residential towers."),
        ("Westfield Century City", "A major retail and dining destination directly within the neighborhood."),
    ],
    "day_to_day": [
        "Walk to Westfield Century City for retail and dining",
        "Building amenities: fitness, concierge, pools",
        "Central commute access via the 10 and Santa Monica Blvd",
        "Proximity to UCLA and Beverly Hills",
    ],
    "what_youll_find": [
        "Full-service luxury condo towers",
        "Executive and investor-owned units",
        "Limited low-rise condo buildings",
        "Very limited single-family inventory",
    ],
    "faq": [
        ("Are there single-family homes in Century City?", "Very few — the neighborhood is predominantly high-rise condo and tower buildings; single-family stock is limited and concentrated at the edges bordering neighboring areas."),
        ("What amenities do Century City towers typically offer?", "Common amenities include concierge service, fitness centers, and pools, though the specific package varies significantly by building."),
    ],
}


def build_html():
    body = render_area_page(DATA)
    schema = breadcrumb_schema([("Home", SITE_URL + "/"), ("Areas", SITE_URL + "/areas"), (DATA["name"], CANONICAL_URL)])
    return render_page(TITLE, DESCRIPTION, CANONICAL_URL, body, active_nav="AREAS", extra_schema=schema)


handler = make_handler(build_html)
