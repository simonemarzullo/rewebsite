import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _layout import render_page, render_area_page, make_handler, breadcrumb_schema, SITE_URL

SLUG = "beverly-hills"
TITLE = "Beverly Hills Real Estate | Simone Marzullo, The Agency"
DESCRIPTION = "Beverly Hills homes for sale and area guide — the Flats, Trousdale Estates, and a globally recognized address, from Simone Marzullo, The Agency."
CANONICAL_URL = SITE_URL + "/areas/" + SLUG

DATA = {
    "slug": SLUG,
    "name": "Beverly Hills",
    "tagline": "Flats elegance and Trousdale privacy, anchored by a name recognized worldwide.",
    "tags": ["FLATS", "TROUSDALE", "PRESTIGE"],
    "intro": [
        "Beverly Hills splits broadly into two distinct markets: the Flats, a flat, palm-lined grid of streets walkable to Rodeo Drive and the city's luxury retail core, and the hillside neighborhoods above Sunset — including Trousdale Estates, prized for its mid-century modern architecture and gated privacy.",
        "The Flats favor traditional and Mediterranean-style estates on generous, regular lots, while the hillside areas offer view-oriented, architecturally significant homes with more topographical variety and privacy from the street.",
        "Beverly Hills carries a globally recognized address, which factors into both pricing and buyer profile — many purchasers are drawn as much to the name and the Beverly Hills Unified school district as to any specific property type.",
    ],
    "who_thrives": "Buyers who want a globally recognized address alongside genuine walkability to world-class retail and dining, or hillside privacy in Trousdale's architecturally significant homes.",
    "market_pulse": [
        ("DEMAND", "Consistently high, name-driven"),
        ("INVENTORY STYLE", "Flats: regular lots. Hillside: view-driven, irregular"),
        ("BUYER MIX", "Domestic and international luxury buyers"),
    ],
    "market_pulse_note": "Beverly Hills pricing is driven as much by street, lot regularity, and school-district boundaries as by square footage — a granular, street-level comp review matters.",
    "why_choose": [
        ("Global brand recognition", "Few residential addresses carry the same immediate international recognition, which can matter for resale and for buyers relocating from abroad."),
        ("Walkable luxury core", "The Flats put Rodeo Drive, fine dining, and Beverly Hills' civic core within walking distance for many residents."),
        ("Trousdale's architectural pedigree", "A concentrated collection of mid-century modern homes, prized for both design and hillside privacy."),
        ("School district draw", "Beverly Hills Unified is a significant factor for family buyers evaluating this market against neighboring areas."),
    ],
    "what_stands_out": [
        ("The Flats grid", "Wide, palm-lined streets on a regular grid, walkable to the retail core and civic center."),
        ("Trousdale privacy", "Gated, hillside lots with view orientation and architecturally significant mid-century homes."),
        ("Retail and dining core", "Rodeo Drive and the surrounding blocks concentrate luxury retail unmatched elsewhere on the Westside."),
    ],
    "day_to_day": [
        "Walk to Rodeo Drive and the retail core",
        "Beverly Hills Unified school district access",
        "Hillside driving routes to the Valley",
        "Civic amenities: library, parks, City Hall",
    ],
    "what_youll_find": [
        "Flats traditional and Mediterranean estates",
        "Trousdale mid-century modern homes",
        "Luxury condos near the retail core",
        "Gated hillside view properties",
    ],
    "faq": [
        ("What's the difference between the Flats and Trousdale?", "The Flats are the flat, walkable grid south of Sunset near Rodeo Drive; Trousdale Estates is a hillside neighborhood above Sunset known for mid-century modern architecture and gated privacy."),
        ("Are Beverly Hills schools open to non-residents?", "Beverly Hills Unified primarily serves residents; verify current enrollment boundaries and policies directly with the district."),
    ],
}


def build_html():
    body = render_area_page(DATA)
    schema = breadcrumb_schema([("Home", SITE_URL + "/"), ("Areas", SITE_URL + "/areas"), (DATA["name"], CANONICAL_URL)])
    return render_page(TITLE, DESCRIPTION, CANONICAL_URL, body, active_nav="AREAS", extra_schema=schema)


handler = make_handler(build_html)
