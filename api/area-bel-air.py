import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _layout import render_page, render_area_page, make_handler, breadcrumb_schema, SITE_URL

SLUG = "bel-air"
TITLE = "Bel Air Real Estate | Simone Marzullo, The Agency"
DESCRIPTION = "Bel Air homes for sale and area guide — estates, privacy, and hillside views in one of Los Angeles' most secluded neighborhoods."
CANONICAL_URL = SITE_URL + "/areas/" + SLUG

DATA = {
    "slug": SLUG,
    "name": "Bel Air",
    "tagline": "Land, privacy, and a retreat above the city.",
    "tags": ["ESTATES", "VIEWS", "SECLUSION"],
    "intro": [
        "Bel Air is Los Angeles at its most secluded: elevation, larger parcels, and homes designed around privacy. Architecture ranges from classic Spanish estates to glass-and-steel contemporaries. Buyers trade walkable retail for space, discretion, and a quieter residential tempo — still minutes from Beverly Hills, Westwood, and major corridors.",
    ],
    "who_thrives": "Buyers who want space and discretion over walkability — relocating executives, creative principals, and anyone trading density for canyon calm.",
    "market_pulse": [
        ("DEMAND", "Selective & high-end"),
        ("INVENTORY STYLE", "Low volume, high variance"),
        ("BUYER MIX", "Estate & privacy-focused"),
    ],
    "market_pulse_note": "Bel Air inventory is thin and highly property-specific. Pricing and days-on-market vary by street, view, and condition — get a tailored comp set before you tour.",
    "why_choose": [
        ("Privacy as the product", "Gated approaches, setbacks, and canyon topography make Bel Air a natural fit for high-profile or privacy-first households."),
        ("Estate-scale living", "Many properties support guest houses, motor courts, and outdoor programs that feel more compound than single-lot."),
        ("View-oriented value", "City-light, canyon, and occasional ocean-glimpse orientations create meaningful differences between otherwise similar homes — comps must be street-specific."),
        ("Long-hold estate mindset", "Bel Air often attracts buyers thinking in decades: land, architecture, and seclusion rather than nightlife adjacency."),
    ],
    "what_stands_out": [
        ("Compound living", "Larger lots and multi-structure sites for multi-generational living or high-security lifestyles."),
        ("View orientation", "Homes sited for city lights, canyon greenery, or long-range vistas on clear days."),
        ("Quiet by design", "Limited commercial intrusion — a retreat feel without leaving Los Angeles."),
    ],
    "day_to_day": [
        "Maximum privacy and security options",
        "Estate-scale indoor-outdoor living",
        "Proximity to UCLA, Beverly Hills, and the Valley",
        "Architectural showpieces and classic estates",
    ],
    "what_youll_find": [
        "Hillside estates and view homes",
        "Gated compounds with guest facilities",
        "Contemporary architectural residences",
        "Select land / rebuild opportunities",
    ],
    "faq": [
        ("Is Bel Air fully gated?", "No — Bel Air includes both guard-gated enclaves and ungated streets; privacy comes as much from lot size, setbacks, and topography as from gates themselves."),
        ("How does Bel Air compare to Beverly Hills?", "Bel Air trades Beverly Hills' walkable retail core for larger lots, more topography, and a quieter, more secluded residential feel."),
    ],
}


def build_html():
    body = render_area_page(DATA)
    schema = breadcrumb_schema([("Home", SITE_URL + "/"), ("Areas", SITE_URL + "/areas"), (DATA["name"], CANONICAL_URL)])
    return render_page(TITLE, DESCRIPTION, CANONICAL_URL, body, active_nav="AREAS", extra_schema=schema)


handler = make_handler(build_html)
