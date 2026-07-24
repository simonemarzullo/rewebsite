"""Single serverless function serving the /areas hub and all nine area guide
pages, routed by a ?slug= query param appended in vercel.json's rewrites.

Consolidated into one file (rather than one function per area) after the
initial multi-file version -- with its shared helpers in a separate
api/_layout.py imported via sys.path -- returned 404s in production even
though it worked in local testing. Rather than debug Vercel's Python function
build behavior further, this keeps everything in a single, self-contained
function with no cross-file imports, matching the pattern already proven to
work for every other page in api/*.py.

"""

import json
from html import escape
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

SITE_URL = "https://www.marzullore.com"
AGENT_SCHEMA = {
    "@type": "RealEstateAgent",
    "name": "Simone Marzullo",
    "url": SITE_URL + "/",
    "image": SITE_URL + "/assets/headshot.jpg",
    "telephone": "+1-310-696-6596",
    "email": "Simone@SimoneMarzullo.com",
    "address": {
        "@type": "PostalAddress",
        "streetAddress": "331 Foothill Rd. #100",
        "addressLocality": "Beverly Hills",
        "addressRegion": "CA",
        "postalCode": "90210",
        "addressCountry": "US",
    },
    "areaServed": {"@type": "City", "name": "Los Angeles"},
    "knowsLanguage": ["en", "it"],
    "memberOf": {"@type": "Organization", "name": "The Agency", "url": "https://www.theagencyre.com/"},
}

AREAS = [
    {"slug": "santa-monica", "name": "Santa Monica", "region_label": "Coastal City",
     "tags": "COAST · DOWNTOWN", "photo": "/assets/neighborhoods/santa-monica.jpg",
     "photo_alt": "Santa Monica Pier, California at dusk"},
    {"slug": "venice", "name": "Venice", "region_label": "Canals & Coast",
     "tags": "WALK-STREETS · CREATIVE", "photo": "/assets/neighborhoods/venice.jpg",
     "photo_alt": "White bridge over the Venice Canals with Mediterranean-style homes and palm trees"},
    {"slug": "beverly-hills", "name": "Beverly Hills", "region_label": "Global Address",
     "tags": "FLATS · TROUSDALE", "photo": "/assets/neighborhoods/beverly-hills.jpg",
     "photo_alt": "Rodeo Drive in Beverly Hills, California"},
    {"slug": "bel-air", "name": "Bel Air", "region_label": "Westside Hills",
     "tags": "ESTATES · VIEWS", "photo": "/assets/neighborhoods/bel-air.jpg",
     "photo_alt": "Gated hillside home with a hedge and mountain backdrop in Bel Air, Los Angeles",
     "photo_position": "50% 68%"},
    {"slug": "malibu", "name": "Malibu", "region_label": "Coastal Estates",
     "tags": "COAST · COMPOUNDS", "photo": "/assets/neighborhoods/malibu.jpg",
     "photo_alt": "Malibu coastline along the Pacific Coast Highway, California"},
    {"slug": "culver-city", "name": "Culver City", "region_label": "Media & Downtown",
     "tags": "MEDIA · DOWNTOWN", "photo": "/assets/neighborhoods/culver-city.jpg",
     "photo_alt": "The Culver Hotel in downtown Culver City, California"},
    {"slug": "century-city", "name": "Century City", "region_label": "Towers & Amenities",
     "tags": "TOWERS · AMENITIES", "photo": "/assets/neighborhoods/century-city.jpg",
     "photo_alt": "Century City skyline, Los Angeles"},
    {"slug": "mar-vista", "name": "Mar Vista", "region_label": "Character & Community",
     "tags": "CHARACTER · COMMUNITY", "photo": "/assets/neighborhoods/mar-vista.jpg",
     "photo_alt": "Shoppers and vendor tents at the Mar Vista Farmers Market in Los Angeles"},
    {"slug": "hollywood", "name": "Hollywood", "region_label": "Hills & Iconic",
     "tags": "HILLS · ICONIC", "photo": "/assets/neighborhoods/hollywood.jpg",
     "photo_alt": "The Hollywood Sign in the Hollywood Hills, Los Angeles"},
]
AREAS_BY_SLUG = {a["slug"]: a for a in AREAS}

PAGE_DATA = {
    "santa-monica": {
        "name": "Santa Monica",
        "title": "Santa Monica Real Estate | Simone Marzullo, The Agency",
        "description": "Santa Monica homes for sale and area guide — beachfront estates, North of Montana, and walkable downtown living from Simone Marzullo, The Agency.",
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
    },
    "venice": {
        "name": "Venice",
        "title": "Venice Real Estate | Simone Marzullo, The Agency",
        "description": "Venice homes for sale and area guide — canals, walk-streets, and creative energy by the beach, from Simone Marzullo, The Agency.",
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
    },
    "beverly-hills": {
        "name": "Beverly Hills",
        "title": "Beverly Hills Real Estate | Simone Marzullo, The Agency",
        "description": "Beverly Hills homes for sale and area guide — the Flats, Trousdale Estates, and a globally recognized address, from Simone Marzullo, The Agency.",
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
    },
    "bel-air": {
        "name": "Bel Air",
        "title": "Bel Air Real Estate | Simone Marzullo, The Agency",
        "description": "Bel Air homes for sale and area guide — estates, privacy, and hillside views in one of Los Angeles' most secluded neighborhoods.",
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
    },
    "malibu": {
        "name": "Malibu",
        "title": "Malibu Real Estate | Simone Marzullo, The Agency",
        "description": "Malibu homes for sale and area guide — coastal estates, gated compounds, and Santa Monica Mountains living, from Simone Marzullo, The Agency.",
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
    },
    "culver-city": {
        "name": "Culver City",
        "title": "Culver City Real Estate | Simone Marzullo, The Agency",
        "description": "Culver City homes for sale and area guide — studio-town roots, a walkable downtown, and Metro E Line access, from Simone Marzullo, The Agency.",
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
    },
    "century-city": {
        "name": "Century City",
        "title": "Century City Real Estate | Simone Marzullo, The Agency",
        "description": "Century City condos and area guide — full-service towers at the center of the Westside's business and retail core, from Simone Marzullo, The Agency.",
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
    },
    "mar-vista": {
        "name": "Mar Vista",
        "title": "Mar Vista Real Estate | Simone Marzullo, The Agency",
        "description": "Mar Vista homes for sale and area guide — mid-century Eichler homes and a family-friendly community feel, from Simone Marzullo, The Agency.",
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
    },
    "hollywood": {
        "name": "Hollywood",
        "title": "Hollywood Real Estate | Simone Marzullo, The Agency",
        "description": "Hollywood homes for sale and area guide — hillside view estates and an iconic, entertainment-industry core, from Simone Marzullo, The Agency.",
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
    },
}

NAV_ITEMS = [
    ("START", "/home"),
    ("AREAS", "/areas"),
    ("ABOUT", "/about"),
    ("THE AGENCY", "/#the-agency"),
    ("CALCULATOR", "/flipcalculator"),
    ("CONTACT", "/contact"),
]

THEME_INIT_SCRIPT = """<script>if ('scrollRestoration' in history) history.scrollRestoration = 'manual';</script>
<script>
(function () {
  var choice = localStorage.getItem('themeChoice') || 'auto';
  var resolved = choice === 'auto' ? (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark') : choice;
  document.documentElement.setAttribute('data-theme', resolved);
  document.documentElement.setAttribute('data-theme-choice', choice);
})();
</script>"""

FOOTER_AND_SCRIPTS = """
<div class="mcf" id="mcf">
  <div class="mcf-menu" id="mcfMenu">
    <a class="mcf-item" href="sms:+14243639227" aria-label="Text Simone Marzullo">
      <svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2z"/></svg>
      <span>Text</span>
    </a>
    <a class="mcf-item" href="tel:+14243639227" aria-label="Call Simone Marzullo">
      <svg viewBox="0 0 24 24"><path d="M6.62 10.79a15.05 15.05 0 006.59 6.59l2.2-2.2a1 1 0 011.01-.24c1.12.37 2.33.57 3.58.57a1 1 0 011 1V20a1 1 0 01-1 1C10.61 21 3 13.39 3 4a1 1 0 011-1h3.5a1 1 0 011 1c0 1.25.2 2.46.57 3.58a1 1 0 01-.25 1.01z"/></svg>
      <span>Call</span>
    </a>
  </div>
  <button type="button" class="mcf-toggle" id="mcfToggle" aria-haspopup="true" aria-expanded="false" aria-label="Call or text Simone">
    <svg class="mcf-icon-phone" viewBox="0 0 24 24"><path d="M6.62 10.79a15.05 15.05 0 006.59 6.59l2.2-2.2a1 1 0 011.01-.24c1.12.37 2.33.57 3.58.57a1 1 0 011 1V20a1 1 0 01-1 1C10.61 21 3 13.39 3 4a1 1 0 011-1h3.5a1 1 0 011 1c0 1.25.2 2.46.57 3.58a1 1 0 01-.25 1.01z"/></svg>
    <svg class="mcf-icon-close" viewBox="0 0 24 24"><path d="M18.3 5.71L12 12.01l-6.3-6.3-1.41 1.42 6.29 6.29-6.29 6.29 1.41 1.42L12 14.84l6.3 6.29 1.41-1.42-6.29-6.29 6.29-6.29z"/></svg>
  </button>
</div>
<script>
(function () {
  var fab = document.getElementById('mcf');
  var toggle = document.getElementById('mcfToggle');
  if (!fab || !toggle) return;
  toggle.addEventListener('click', function (e) {
    e.stopPropagation();
    var open = fab.classList.toggle('on');
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  document.addEventListener('click', function (e) {
    if (fab.classList.contains('on') && !fab.contains(e.target)) {
      fab.classList.remove('on');
      toggle.setAttribute('aria-expanded', 'false');
    }
  });
})();
function updateThemePickerUI(choice) {
  document.querySelectorAll('.theme-opt').forEach(function (btn) {
    btn.classList.toggle('active', btn.dataset.themeChoice === choice);
  });
}
function applyThemeChoice(choice) {
  const resolved = choice === 'auto'
    ? (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark')
    : choice;
  document.documentElement.setAttribute('data-theme', resolved);
  document.documentElement.setAttribute('data-theme-choice', choice);
  updateThemePickerUI(choice);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', resolved === 'light' ? '#FFFFFF' : '#000000');
}
function setThemeChoice(choice) {
  localStorage.setItem('themeChoice', choice);
  applyThemeChoice(choice);
}
if (window.matchMedia) {
  window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', function () {
    if ((localStorage.getItem('themeChoice') || 'auto') === 'auto') applyThemeChoice('auto');
  });
}
updateThemePickerUI(document.documentElement.getAttribute('data-theme-choice') || 'auto');
function toggleMobileNav() {
  const menu = document.getElementById('nav-mobile');
  const btn = document.querySelector('.nav-toggle');
  const willOpen = !menu.classList.contains('on');
  menu.classList.toggle('on', willOpen);
  btn.setAttribute('aria-expanded', String(willOpen));
}
function closeMobileNav() {
  document.getElementById('nav-mobile').classList.remove('on');
  document.querySelector('.nav-toggle').setAttribute('aria-expanded', 'false');
}
</script>
"""


def _theme_picker_html():
    return """<div class="theme-picker" role="group" aria-label="Theme">
      <button type="button" class="theme-opt" data-theme-choice="light" onclick="setThemeChoice('light')" aria-label="Light theme">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2M12 20v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M2 12h2M20 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"></path></svg>
      </button>
      <button type="button" class="theme-opt" data-theme-choice="dark" onclick="setThemeChoice('dark')" aria-label="Dark theme">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg>
      </button>
      <button type="button" class="theme-opt" data-theme-choice="auto" onclick="setThemeChoice('auto')" aria-label="Match device setting">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"></circle><path d="M12 3a9 9 0 010 18z" fill="currentColor" stroke="none"></path></svg>
      </button>
    </div>"""


def _nav_html(active):
    links = []
    for label, href in NAV_ITEMS:
        cls = ' class="active"' if label == active else ""
        links.append(f'<li><a href="{href}"{cls}>{label}</a></li>')
    mobile_links = [f'<a href="{href}">{label.title()}</a>' for label, href in NAV_ITEMS]
    return f"""<nav id="nav">
  <a href="/" class="nav-brand" aria-label="Go to homepage">
    <img src="/assets/agency-logo.png" alt="The Agency" class="nav-agency-logo" onerror="this.style.display='none'">
    <div class="nav-brand-text">
      <span class="nav-name">Simone Marzullo</span>
      <span class="nav-sub">REALTOR® · The Agency · Los Angeles</span>
    </div>
  </a>
  <ul class="nav-links">
    {''.join(links)}
  </ul>
  <a href="/home" class="nav-cta">Get Started</a>
  <button type="button" class="nav-toggle" aria-expanded="false" aria-label="Menu" onclick="toggleMobileNav()">
    <span></span><span></span><span></span>
  </button>
</nav>
<div class="nav-mobile" id="nav-mobile">
  {''.join(mobile_links)}
  <a href="/home" class="nav-cta">Get Started</a>
  <div class="nav-mobile-theme-row">
    {_theme_picker_html()}
  </div>
</div>"""


def _footer_html():
    return f"""<footer>
  <div class="footer-inner">
    <div>
      <img src="/assets/agency-logo.png" alt="The Agency" style="height:28px;margin-bottom:8px" onerror="this.style.display='none';document.getElementById('f-agency-text').style.display='block'">
      <div class="f-agency-name" id="f-agency-text" style="display:none">The Agency</div>
      <div class="f-line">DRE# 01904054</div>
      <div class="f-line">331 Foothill Rd. #100</div>
      <div class="f-line">Beverly Hills, CA 90210</div>
    </div>
    <div class="f-legal">
      <div class="f-eq">⌂</div>
      <div class="f-copy">Equal Housing Opportunity<br>© 2026 Simone Marzullo. All rights reserved.<br>Information deemed reliable but not guaranteed.<br>CA DRE# 02174253</div>
      <div class="f-copy" style="margin-top:10px"><a href="/privacy.html" style="color:var(--g5);text-decoration:underline">Privacy Policy</a></div>
    </div>
  </div>
  <div class="f-disclaimer">Simone Marzullo | REALTOR® | DRE#02174253 is a real estate salesperson licensed by the state of California affiliated with The Agency. The Agency is a real estate broker licensed by the state of California and abides by equal housing opportunity laws. All material presented herein is intended for informational purposes only. Information is compiled from sources deemed reliable but is subject to errors, omissions, changes in price, condition, sale, or withdrawal without notice. No statement is made as to accuracy of any description. All measurements and square footages are approximate. This is not intended to solicit property already listed. Nothing herein shall be construed as legal, accounting or other professional advice outside the realm of real estate brokerage.</div>
  <div class="theme-toggle-row">
    {_theme_picker_html()}
  </div>
</footer>"""


def breadcrumb_html(trail, dark=False):
    cls = "breadcrumb breadcrumb-dark" if dark else "breadcrumb"
    parts = []
    for i, (label, href) in enumerate(trail):
        if i:
            parts.append('<span class="sep">/</span>')
        if href:
            parts.append(f'<a href="{href}">{escape(label)}</a>')
        else:
            parts.append(f"<span>{escape(label)}</span>")
    return f'<div class="{cls}">{"".join(parts)}</div>'


def breadcrumb_schema(trail_with_urls):
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": name, "item": url}
            for i, (name, url) in enumerate(trail_with_urls)
        ],
    }


def render_page(title, description, canonical, body_html, active_nav=None, extra_schema=None):
    schema_graph = [AGENT_SCHEMA]
    if extra_schema:
        schema_graph.extend(extra_schema if isinstance(extra_schema, list) else [extra_schema])
    schema_json = json.dumps({"@context": "https://schema.org", "@graph": schema_graph}, indent=None)
    og_image = SITE_URL + "/assets/agency-logo.png"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{THEME_INIT_SCRIPT}
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>{escape(title)}</title>
<meta name="description" content="{escape(description)}">
<link rel="canonical" href="{canonical}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Simone Marzullo | The Agency">
<meta property="og:title" content="{escape(title)}">
<meta property="og:description" content="{escape(description)}">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{og_image}">
<meta name="twitter:card" content="summary">
<meta name="twitter:image" content="{og_image}">
<meta name="theme-color" content="#000000">
<link rel="manifest" href="/manifest.json">
<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">
<link rel="icon" type="image/png" sizes="32x32" href="/assets/favicon-32.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=EB+Garamond:ital,wght@0,400;0,500;1,400;1,500&family=Inter:wght@300;400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/assets/areas.css">
<script type="application/ld+json">{schema_json}</script>
</head>
<body>
{_nav_html(active_nav)}
{body_html}
{_footer_html()}
{FOOTER_AND_SCRIPTS}
</body>
</html>"""


def related_areas_html(current_slug, count=3):
    others = [a for a in AREAS if a["slug"] != current_slug][:count]
    cards = "".join(f"""
      <a class="area-related-card" href="/areas/{a['slug']}">
        <div class="area-related-kicker">{a['region_label']}</div>
        <div class="area-related-name">{a['name']}</div>
        <div class="area-related-tags">{a['tags']}</div>
      </a>""" for a in others)
    return f"""<section class="section section-alt">
  <div class="wrap">
    <span class="label label-red">Nearby</span>
    <h2 class="action-title" style="margin-bottom:28px;margin-top:14px">Related Westside areas</h2>
    <div class="area-related-grid">{cards}</div>
  </div>
</section>"""


def render_area_page(slug):
    data = PAGE_DATA[slug]
    area = AREAS_BY_SLUG[slug]
    tags_html = "".join(f'<span class="area-tag">{t}</span>' for t in data["tags"])
    intro_html = "".join(f"<p>{p}</p>" for p in data["intro"])
    pulse_rows = "".join(f"""
      <div class="market-pulse-row">
        <div class="market-pulse-lbl">{lbl}</div>
        <div class="market-pulse-val">{val}</div>
      </div>""" for lbl, val in data["market_pulse"])
    why_cards = "".join(f"""
      <div class="area-card">
        <div class="area-card-kicker">Why buyers choose {data['name']}</div>
        <div class="area-card-title">{title}</div>
        <div class="area-card-body">{body}</div>
      </div>""" for title, body in data["why_choose"])
    stand_out_cards = "".join(f"""
      <div class="area-card">
        <div class="area-card-title">{title}</div>
        <div class="area-card-body">{body}</div>
      </div>""" for title, body in data["what_stands_out"])
    day_items = "".join(f'<div class="area-list-item"><span class="area-list-dot"></span>{i}</div>' for i in data["day_to_day"])
    find_items = "".join(f'<div class="area-list-item"><span class="area-list-dot"></span>{i}</div>' for i in data["what_youll_find"])
    faq_items = "".join(f"""
      <div class="area-faq-item">
        <div class="area-faq-q">{q}</div>
        <div class="area-faq-a">{a}</div>
      </div>""" for q, a in data["faq"])

    return f"""
<section class="area-hero">
  <img class="area-hero-img" src="{area['photo']}" alt="{area['photo_alt']}" loading="eager" style="object-position:{area.get('photo_position', '50% 20%')}">
  <div class="area-hero-scrim"></div>
  <div class="area-hero-content">
    {breadcrumb_html([("Home", "/"), ("Areas", "/areas"), (data['name'], None)])}
    <div class="area-eyebrow"><span class="area-eyebrow-line"></span><span class="area-eyebrow-text">{area['region_label']}</span></div>
    <h1 class="area-h1">{data['name']}</h1>
    <p class="area-tagline">{data['tagline']}</p>
    <div class="area-tags">{tags_html}</div>
    <div class="area-hero-actions">
      <a href="/buy" class="btn-primary">Homes in {data['name']}</a>
      <a href="/sell" class="btn-hero-outline">Sell Here</a>
    </div>
  </div>
</section>

<section class="section">
  <div class="wrap">
    <div class="area-intro-grid">
      <div>
        <span class="label label-red">Area Guide</span>
        <h2 class="action-title" style="margin:14px 0 22px">Living in {data['name']}</h2>
        <div class="area-intro-copy">{intro_html}</div>
        <div class="area-callout">
          <div class="area-callout-label">Who Thrives Here</div>
          <p>{data['who_thrives']}</p>
        </div>
      </div>
      <div class="market-pulse">
        <div class="market-pulse-head">
          <span class="label">Market Pulse</span>
          <div class="market-pulse-title">How this market feels</div>
        </div>
        {pulse_rows}
        <div class="market-pulse-note">{data['market_pulse_note']}</div>
        <a href="/contact" class="market-pulse-cta">Ask About Current Inventory</a>
      </div>
    </div>
  </div>
</section>

<section class="section section-alt">
  <div class="wrap">
    <span class="label label-red">For Home Buyers</span>
    <h2 class="action-title" style="margin:14px 0 12px">Why choose {data['name']}</h2>
    <p style="font-size:.85rem;color:var(--g5);font-weight:300;max-width:640px;margin-bottom:32px">Practical reasons buyers put this area on their shortlist — lifestyle, commute, privacy, and long-term fit.</p>
    <div class="area-cards-grid">{why_cards}</div>
  </div>
</section>

<section class="section">
  <div class="wrap">
    <span class="label label-red">Inside the Area</span>
    <h2 class="action-title" style="margin:14px 0 28px">What stands out</h2>
    <div class="area-cards-grid area-cards-grid-3">{stand_out_cards}</div>
  </div>
</section>

<section class="section section-alt">
  <div class="wrap">
    <div class="area-list-grid">
      <div>
        <span class="label">Lifestyle</span>
        <h3 class="action-title" style="font-size:1.4rem;margin:10px 0 20px">Day-to-day</h3>
        <div class="area-list">{day_items}</div>
      </div>
      <div>
        <span class="label">Property Types</span>
        <h3 class="action-title" style="font-size:1.4rem;margin:10px 0 20px">What you'll find</h3>
        <div class="area-list">{find_items}</div>
      </div>
    </div>
  </div>
</section>

<section class="section">
  <div class="wrap">
    <span class="label label-red">Common Questions</span>
    <h2 class="action-title" style="margin:14px 0 28px">{data['name']} FAQ</h2>
    <div class="area-faq">{faq_items}</div>
  </div>
</section>

{related_areas_html(slug)}
"""


def build_hub_html():
    cards = "".join(f"""
    <a class="hub-card" href="/areas/{a['slug']}">
      <img class="hub-card-img" src="{a['photo']}" alt="{a['photo_alt']}" loading="lazy" style="object-position:{a.get('photo_position', '50% 50%')}">
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
    <div style="margin-top:28px"><a href="/contact" class="btn-primary">Ask a Question</a></div>
  </div>
</section>"""

    canonical = SITE_URL + "/areas"
    title = "Los Angeles Westside Area Guides | Simone Marzullo"
    description = "Neighborhood guides to the Los Angeles Westside — Santa Monica, Venice, Beverly Hills, Bel Air, Malibu, Culver City, Century City, Mar Vista, and Hollywood — from Simone Marzullo, The Agency."
    schema = breadcrumb_schema([("Home", SITE_URL + "/"), ("Areas", canonical)])
    return render_page(title, description, canonical, body, active_nav="AREAS", extra_schema=schema)


def build_area_html(slug):
    data = PAGE_DATA[slug]
    canonical = SITE_URL + "/areas/" + slug
    body = render_area_page(slug)
    schema = breadcrumb_schema([("Home", SITE_URL + "/"), ("Areas", SITE_URL + "/areas"), (data["name"], canonical)])
    return render_page(data["title"], data["description"], canonical, body, active_nav="AREAS", extra_schema=schema)


def build_html(slug=None):
    if slug:
        if slug not in PAGE_DATA:
            raise ValueError("unknown area slug")
        return build_area_html(slug)
    return build_hub_html()


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        slug = query.get("slug", [None])[0]
        try:
            html = build_html(slug)
        except ValueError:
            self.send_error(404)
            return
        except Exception:
            self.send_error(500)
            return
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass
