import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _layout import render_page, render_area_page, make_handler, breadcrumb_schema, SITE_URL

SLUG = "hollywood"
TITLE = "Hollywood Real Estate | Simone Marzullo, The Agency"
DESCRIPTION = "Hollywood homes for sale and area guide — hillside view estates and an iconic, entertainment-industry core, from Simone Marzullo, The Agency."
CANONICAL_URL = SITE_URL + "/areas/" + SLUG

DATA = {
    "slug": SLUG,
    "name": "Hollywood",
    "tagline": "Iconic entertainment history below, view-driven hillside living above.",
    "tags": ["HILLS", "ICONIC", "ENTERTAINMENT"],
    "intro": [
        "Hollywood is really two markets stacked on top of each other: the flatter commercial core around Hollywood Boulevard and Sunset, home to the Walk of Fame, TCL Chinese Theatre, and a still-revitalizing mix of historic apartment buildings and new mixed-use development; and the Hollywood Hills above, a winding, view-oriented hillside market with a long entertainment-industry history.",
        "Hillside homes range from modest mid-century cottages to significant view estates, with lot shape, road access, and view orientation driving much of the price variation between otherwise similar addresses.",
        "The flatter core has seen substantial reinvestment over the past decade, with new residential towers and a denser mix of dining and nightlife, while the hills retain a quieter, more residential character despite sitting minutes from the commercial corridor.",
    ],
    "who_thrives": "Buyers drawn to hillside views and privacy with quick access to the entertainment industry and Hollywood's commercial core, or to walkable, transit-connected flatland living.",
    "market_pulse": [
        ("DEMAND", "Strong for hillside view properties"),
        ("INVENTORY STYLE", "Hills: irregular view lots. Flats: apartments, condos, new mixed-use"),
        ("BUYER MIX", "Entertainment-industry buyers, investors, urban lifestyle buyers"),
    ],
    "market_pulse_note": "Hollywood Hills pricing depends heavily on view quality, road access, and lot usability — property-specific evaluation matters more than a neighborhood-wide average.",
    "why_choose": [
        ("Hillside views", "City-light and canyon views from the Hollywood Hills are a primary draw, with meaningful price variation by exposure and elevation."),
        ("Entertainment-industry proximity", "Major studios and production offices in Hollywood and neighboring Burbank keep the buyer pool tied to the industry."),
        ("Transit and walkability in the flats", "The Metro Red Line and a growing walkable core give flatland buyers strong transit access toward Downtown LA and the Valley."),
        ("Ongoing revitalization", "Continued investment in Hollywood Boulevard and Sunset has brought new dining, retail, and residential towers to the commercial core."),
    ],
    "what_stands_out": [
        ("Hillside road access", "Narrow, winding hillside streets mean road access and parking are real, property-specific considerations."),
        ("Iconic landmarks", "The Walk of Fame, TCL Chinese Theatre, and the Hollywood Sign anchor the neighborhood's global identity."),
        ("Mixed housing stock", "Historic apartment buildings, new mixed-use towers, and hillside single-family homes all coexist within the same broader area."),
    ],
    "day_to_day": [
        "Metro Red Line access in the flats",
        "Proximity to major studios and production offices",
        "Hillside hiking access near Runyon Canyon and Griffith Park",
        "Walk of Fame and Hollywood Boulevard nightlife and dining",
    ],
    "what_youll_find": [
        "Hollywood Hills view estates",
        "Mid-century hillside cottages",
        "Historic and new-construction apartment buildings",
        "Mixed-use condo developments in the flats",
    ],
    "faq": [
        ("Is Hollywood the same as the Hollywood Hills?", "No — 'Hollywood' typically refers to the flatter commercial core around Hollywood Boulevard, while the 'Hollywood Hills' is the hillside residential area above it, with a distinct view-driven market."),
        ("What should I know about buying in the Hollywood Hills?", "Road access, parking, and lot usability vary block to block in the hills — always evaluate these alongside view quality rather than relying on a general area comparison."),
    ],
}


def build_html():
    body = render_area_page(DATA)
    schema = breadcrumb_schema([("Home", SITE_URL + "/"), ("Areas", SITE_URL + "/areas"), (DATA["name"], CANONICAL_URL)])
    return render_page(TITLE, DESCRIPTION, CANONICAL_URL, body, active_nav="AREAS", extra_schema=schema)


handler = make_handler(build_html)
