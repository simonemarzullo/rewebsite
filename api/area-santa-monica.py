import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _layout import render_page, render_area_page, make_handler, breadcrumb_schema, SITE_URL

SLUG = "santa-monica"
TITLE = "Santa Monica Real Estate | Simone Marzullo, The Agency"
DESCRIPTION = "Santa Monica homes for sale and area guide — beachfront estates, North of Montana, and walkable downtown living from Simone Marzullo, The Agency."
CANONICAL_URL = SITE_URL + "/areas/" + SLUG

DATA = {
    "slug": SLUG,
    "name": "Santa Monica",
    "tagline": "A complete city by the sea, from beachfront estates to walkable downtown living.",
    "tags": ["COAST", "WALKABLE", "DOWNTOWN"],
    "intro": [
        "Santa Monica is one of the few Los Angeles submarkets where you can genuinely live without a car — the Third Street Promenade, the Pier, and the beach all sit within a compact, walkable core. Housing ranges from beachfront estates north of the pier to mid-rise downtown condos and classic bungalows in the residential neighborhoods further inland.",
        "North of Montana is the city's most prestigious residential pocket — larger lots and a short walk to Montana Avenue's boutiques. Ocean Park and Sunset Park trade some of that polish for a more relaxed, architecturally eclectic feel, while downtown towers cater to buyers who want low-maintenance living within steps of transit and retail.",
        "Coastal proximity brings real tradeoffs: marine-layer humidity, stricter coastal-zone permitting on renovations, and premium pricing for ocean views or beach access. Buyers should weigh those against the walkability, mild climate, and direct beach access that define the city.",
    ],
    "who_thrives": "Buyers who want to walk to the beach, dining, and transit without giving up single-family living — from young professionals in downtown condos to families settling north of Montana.",
    "market_pulse": [
        ("DEMAND", "Consistently strong, coastal premium"),
        ("INVENTORY STYLE", "Beachfront, downtown condo, inland single-family"),
        ("BUYER MIX", "Owner-occupants, second-home buyers, downsizers"),
    ],
    "market_pulse_note": "Santa Monica pricing varies sharply by proximity to the beach and by neighborhood — a tailored comp set matters more here than almost anywhere on the Westside.",
    "why_choose": [
        ("Walkability as a lifestyle", "The Promenade, the Pier, and Montana Avenue put daily errands, dining, and the beach within walking distance of most residential pockets."),
        ("Coastal climate", "The marine layer keeps summers cooler and winters milder than inland LA — a meaningful draw for buyers prioritizing comfort."),
        ("Transit and commute options", "Metro's E Line and proximity to the 10 Freeway give Santa Monica some of the Westside's best access to Downtown LA and the Valley."),
        ("School and family draw", "Santa Monica-Malibu Unified schools consistently draw families willing to pay a premium for the address."),
    ],
    "what_stands_out": [
        ("Beach proximity premium", "Distance to sand measured in blocks, not miles, drives meaningful price differences between otherwise similar homes."),
        ("Coastal permitting", "Renovations and additions near the coastal zone can involve additional Coastal Commission review — a real timeline consideration."),
        ("Architectural range", "Craftsman bungalows, Spanish revivals, and contemporary rebuilds sit block to block, especially in the older inland neighborhoods."),
    ],
    "day_to_day": [
        "Walk to the Promenade, Pier, and beach",
        "Metro E Line access to Downtown LA",
        "Year-round mild, marine-layer climate",
        "Santa Monica-Malibu Unified school options",
    ],
    "what_youll_find": [
        "Beachfront and near-beach single-family homes",
        "Downtown condos and mid-rise buildings",
        "Craftsman and Spanish-revival bungalows",
        "North of Montana estate lots",
    ],
    "faq": [
        ("Is Santa Monica walkable?", "Much of it, yes — downtown and the neighborhoods closest to the beach are built around walking and biking, though inland pockets lean more car-dependent."),
        ("What's the difference between North of Montana and Ocean Park?", "North of Montana is the more formal, higher-priced residential pocket near Montana Avenue's shops; Ocean Park has a more relaxed, architecturally eclectic character closer to the beach."),
    ],
}


def build_html():
    body = render_area_page(DATA)
    schema = breadcrumb_schema([("Home", SITE_URL + "/"), ("Areas", SITE_URL + "/areas"), (DATA["name"], CANONICAL_URL)])
    return render_page(TITLE, DESCRIPTION, CANONICAL_URL, body, active_nav="AREAS", extra_schema=schema)


handler = make_handler(build_html)
