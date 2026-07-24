import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _layout import render_page, render_area_page, make_handler, breadcrumb_schema, SITE_URL

SLUG = "malibu"
TITLE = "Malibu Real Estate | Simone Marzullo, The Agency"
DESCRIPTION = "Malibu homes for sale and area guide — coastal estates, gated compounds, and Santa Monica Mountains living, from Simone Marzullo, The Agency."
CANONICAL_URL = SITE_URL + "/areas/" + SLUG

DATA = {
    "slug": SLUG,
    "name": "Malibu",
    "tagline": "Twenty-one miles of coastline, from beachfront colonies to mountain-view compounds.",
    "tags": ["COAST", "COMPOUNDS", "PRIVACY"],
    "intro": [
        "Malibu stretches roughly 21 miles along the Pacific Coast Highway, and the market varies enormously along that length — from the gated Malibu Colony and Point Dume's bluff-top estates to the more rural canyons and ridgelines of the Santa Monica Mountains further inland.",
        "Much of Malibu's appeal is privacy and scale: larger parcels, gated compounds, and a rural, low-density character that's increasingly rare elsewhere in Los Angeles County. Surf breaks, beach access, and mountain trail access all factor into how specific streets and pockets are valued.",
        "Coastal and hillside living here carries real, well-documented considerations — wildfire risk in the canyons and hillsides, bluff erosion and geological review on some coastal parcels, and Coastal Commission permitting on new construction and major renovations. These are worth discussing property-by-property rather than assuming they apply uniformly across the city.",
    ],
    "who_thrives": "Buyers prioritizing privacy, scale, and coastal or mountain access above walkability or density — from beachfront second-home buyers to full-time residents in the canyons.",
    "market_pulse": [
        ("DEMAND", "Selective, privacy- and scale-driven"),
        ("INVENTORY STYLE", "Low volume; wide variance by coastal vs. canyon location"),
        ("BUYER MIX", "Estate and second-home buyers, privacy-focused"),
    ],
    "market_pulse_note": "Malibu inventory is thin and highly location-specific — coastal, bluff, and canyon parcels each carry distinct considerations that a general market average can't capture.",
    "why_choose": [
        ("Coastline access", "Beachfront and near-beach parcels offer direct access to some of the county's most recognized surf breaks and beaches."),
        ("Scale and privacy", "Larger lots and gated compounds support a level of privacy that's increasingly difficult to find elsewhere in Los Angeles."),
        ("Mountain and canyon options", "The Santa Monica Mountains give buyers a rural, low-density alternative to coastal-front living within the same city."),
        ("Long-hold estate mindset", "Many Malibu purchases are generational holds rather than short-term flips, reflecting the scarcity of comparable coastal land."),
    ],
    "what_stands_out": [
        ("Coastal Commission review", "New construction and significant renovations on coastal-zone parcels typically involve additional permitting and review — a real timeline factor."),
        ("Fire and geological considerations", "Canyon and hillside parcels warrant a clear-eyed look at defensible space, insurance, and geological reports as part of due diligence."),
        ("Low-density rural character", "Much of Malibu remains genuinely rural in feel, with limited commercial development outside a few corridors."),
    ],
    "day_to_day": [
        "Direct beach and surf-break access",
        "Santa Monica Mountains trail access",
        "Santa Monica-Malibu Unified school options",
        "PCH as the primary coastal corridor",
    ],
    "what_youll_find": [
        "Gated beachfront colonies",
        "Bluff-top and ocean-view estates",
        "Canyon and ranch-style compounds",
        "Select land and rebuild opportunities",
    ],
    "faq": [
        ("What should I know about buying near the Malibu coast?", "Coastal-zone parcels can involve Coastal Commission review for new construction or major renovation, and bluff-front lots may require geological assessment — both worth discussing early with your agent and a qualified inspector."),
        ("Is Malibu only beachfront homes?", "No — the city also includes canyon and mountain areas in the Santa Monica Mountains with a more rural, inland character."),
    ],
}


def build_html():
    body = render_area_page(DATA)
    schema = breadcrumb_schema([("Home", SITE_URL + "/"), ("Areas", SITE_URL + "/areas"), (DATA["name"], CANONICAL_URL)])
    return render_page(TITLE, DESCRIPTION, CANONICAL_URL, body, active_nav="AREAS", extra_schema=schema)


handler = make_handler(build_html)
