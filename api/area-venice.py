import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _layout import render_page, render_area_page, make_handler, breadcrumb_schema, SITE_URL

SLUG = "venice"
TITLE = "Venice Real Estate | Simone Marzullo, The Agency"
DESCRIPTION = "Venice homes for sale and area guide — canals, walk-streets, and creative energy by the beach, from Simone Marzullo, The Agency."
CANONICAL_URL = SITE_URL + "/areas/" + SLUG

DATA = {
    "slug": SLUG,
    "name": "Venice",
    "tagline": "Walk-streets, working canals, and a creative, tech-adjacent energy by the sand.",
    "tags": ["CANALS", "CREATIVE", "TECH-ADJACENT"],
    "intro": [
        "Venice's identity is shaped by its walk-streets and historic canals — narrow pedestrian lanes and waterways lined with everything from original bungalows to striking contemporary rebuilds. Abbot Kinney Boulevard anchors the neighborhood's retail and dining, drawing a design- and creative-industry crowd.",
        "Silicon Beach's tech presence, including major offices within Venice and neighboring Playa Vista, has reshaped demand over the past decade, bringing younger, higher-income buyers into a neighborhood that was historically more bohemian and artist-driven.",
        "Lot sizes in the canals and walk-street areas tend to be small and irregular, and many homes require creative architectural solutions for parking and outdoor space — a real consideration for buyers used to standard suburban lots.",
    ],
    "who_thrives": "Buyers drawn to walkable, design-forward living near the beach — tech professionals, creatives, and anyone comfortable trading lot size for character and location.",
    "market_pulse": [
        ("DEMAND", "Strong among creative and tech buyers"),
        ("INVENTORY STYLE", "Small, irregular lots; high renovation variance"),
        ("BUYER MIX", "Tech-adjacent professionals, creatives, investors"),
    ],
    "market_pulse_note": "Venice's canal and walk-street lots vary enormously in size and buildable footprint — pricing should be evaluated property by property rather than by neighborhood average.",
    "why_choose": [
        ("Walk-street character", "Car-free pedestrian lanes create a genuinely distinct living experience found in few other LA neighborhoods."),
        ("Canal living", "A small, tightly held pocket of homes fronting the historic Venice canals, with waterside patios and walking paths."),
        ("Creative and tech energy", "Proximity to Abbot Kinney and Silicon Beach offices keeps demand anchored to a design- and tech-forward buyer pool."),
        ("Beach access", "Direct access to the boardwalk and beach without Santa Monica's downtown density."),
    ],
    "what_stands_out": [
        ("Abbot Kinney corridor", "A concentrated strip of independent retail, galleries, and restaurants that shapes the neighborhood's daily rhythm."),
        ("Architectural contrast", "Original bungalows sit alongside striking modern rebuilds, often on the same block."),
        ("Silicon Beach proximity", "Major tech and media offices within or adjacent to Venice keep the buyer pool younger and commute-focused."),
    ],
    "day_to_day": [
        "Walk Abbot Kinney's shops and restaurants",
        "Bike or walk to the boardwalk and beach",
        "Short commute to Silicon Beach offices",
        "Canal-front paths for walking and running",
    ],
    "what_youll_find": [
        "Original walk-street bungalows",
        "Canal-front homes",
        "Contemporary architectural rebuilds",
        "Live-work and creative-industry spaces",
    ],
    "faq": [
        ("What is a Venice walk-street?", "A pedestrian-only lane, closed to cars, that homes front directly onto — a distinct format found in a few historic pockets of Venice."),
        ("Is Venice good for a tech commute?", "Yes for many roles — several major tech and media employers sit within or directly adjacent to Venice, though commute times elsewhere in LA vary widely."),
    ],
}


def build_html():
    body = render_area_page(DATA)
    schema = breadcrumb_schema([("Home", SITE_URL + "/"), ("Areas", SITE_URL + "/areas"), (DATA["name"], CANONICAL_URL)])
    return render_page(TITLE, DESCRIPTION, CANONICAL_URL, body, active_nav="AREAS", extra_schema=schema)


handler = make_handler(build_html)
