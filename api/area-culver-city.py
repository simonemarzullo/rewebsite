import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _layout import render_page, render_area_page, make_handler, breadcrumb_schema, SITE_URL

SLUG = "culver-city"
TITLE = "Culver City Real Estate | Simone Marzullo, The Agency"
DESCRIPTION = "Culver City homes for sale and area guide — studio-town roots, a walkable downtown, and Metro E Line access, from Simone Marzullo, The Agency."
CANONICAL_URL = SITE_URL + "/areas/" + SLUG

DATA = {
    "slug": SLUG,
    "name": "Culver City",
    "tagline": "A walkable studio town where historic bungalows meet a growing creative-office core.",
    "tags": ["MEDIA", "DOWNTOWN", "WALKABLE"],
    "intro": [
        "Culver City has long been a studio town — Sony Pictures and Amazon Studios both maintain a major presence here — and that industry base has anchored a walkable downtown built around restaurants, theaters, and a growing Arts District.",
        "Housing ranges from early-20th-century bungalows in the established residential pockets to newer townhomes and infill construction near downtown and the Hayden Tract, a converted industrial area now home to creative offices and design firms.",
        "The Metro E Line runs directly through downtown Culver City, giving residents transit access toward Santa Monica in one direction and Downtown LA in the other — a genuine commute advantage relative to many surrounding neighborhoods.",
    ],
    "who_thrives": "Buyers who want walkable downtown amenities and media-industry proximity without Santa Monica or Venice pricing — from studio employees to families in the established bungalow neighborhoods.",
    "market_pulse": [
        ("DEMAND", "Steady, industry- and transit-driven"),
        ("INVENTORY STYLE", "Mix of historic bungalows and newer infill"),
        ("BUYER MIX", "Media professionals, families, first-time buyers"),
    ],
    "market_pulse_note": "Culver City pricing varies by proximity to downtown and the E Line — a block-by-block comparison matters more than a citywide average.",
    "why_choose": [
        ("Studio and media proximity", "Sony Pictures, Amazon Studios, and a cluster of production companies anchor local employment."),
        ("Walkable downtown", "Restaurants, theaters, and the Arts District sit within a compact, walkable core."),
        ("Metro E Line access", "Direct rail access toward Santa Monica and Downtown LA is a genuine differentiator versus nearby car-dependent neighborhoods."),
        ("Relative value", "Culver City has historically offered more house for the price than adjacent Westside beach cities, though this narrows as the area continues to develop."),
    ],
    "what_stands_out": [
        ("Hayden Tract creative offices", "A converted industrial district now home to architecturally distinctive creative-office buildings."),
        ("Historic bungalow streets", "Established early-20th-century residential blocks with a strong sense of neighborhood character."),
        ("Downtown Arts District", "A concentrated stretch of galleries, theaters, and independent restaurants."),
    ],
    "day_to_day": [
        "Walk to downtown restaurants and theaters",
        "Metro E Line to Santa Monica or Downtown LA",
        "Proximity to major studio employers",
        "Access to the Culver City Stairs and local parks",
    ],
    "what_youll_find": [
        "Early-20th-century bungalows",
        "Newer townhomes and infill condos",
        "Creative-office live-work spaces",
        "Single-family homes in established residential pockets",
    ],
    "faq": [
        ("Is Culver City good for a studio-industry commute?", "Yes — Sony Pictures and Amazon Studios are both based in Culver City, and it sits centrally for commuting to other major studio lots across the Westside and Hollywood."),
        ("Does the Metro E Line run through Culver City?", "Yes, with a station in downtown Culver City connecting toward Santa Monica and Downtown LA."),
    ],
}


def build_html():
    body = render_area_page(DATA)
    schema = breadcrumb_schema([("Home", SITE_URL + "/"), ("Areas", SITE_URL + "/areas"), (DATA["name"], CANONICAL_URL)])
    return render_page(TITLE, DESCRIPTION, CANONICAL_URL, body, active_nav="AREAS", extra_schema=schema)


handler = make_handler(build_html)
