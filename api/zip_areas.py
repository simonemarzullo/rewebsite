"""LA-area market -> ZIP directory. One source of truth.

Imported by api/portal.py (for /match + Buyer Match location matching) and by
scripts/enrich_sweep.py (to tag each FollowUpBoss contact with its ZIP and
area). Pure data, zero dependencies -- keep it that way so both callers can
import it cleanly.

Ported 2026-09-03 from Agent Circle (src/lib/constants.ts + locations.ts).
A market name expands to all its ZIPs; a bare 5-digit token is itself; two
locations match on ZIP-set overlap -- so "Santa Monica" on a buyer need
matches a prospect recorded only as 90403.
"""

ZIPS_BY_AREA = {
    "Bel Air": ["90077", "90049"], "Beverly Hills": ["90210", "90211", "90212"],
    "Brentwood": ["90049"], "Pacific Palisades": ["90272"],
    "Santa Monica": ["90401", "90402", "90403", "90404", "90405"],
    "Venice": ["90291", "90292"], "Marina del Rey": ["90292"],
    "Playa Vista": ["90094"], "Playa del Rey": ["90293"], "Mar Vista": ["90066"],
    "Del Rey": ["90230", "90066"], "Culver City": ["90230", "90232"],
    "West Los Angeles": ["90025", "90064"], "Century City": ["90067"],
    "Westwood": ["90024", "90095"], "Holmby Hills": ["90024"],
    "Cheviot Hills": ["90034", "90064"], "Rancho Park": ["90064"],
    "Beverlywood": ["90034", "90035"], "Palms": ["90034"],
    "West Hollywood": ["90046", "90048", "90069"],
    "Hancock Park": ["90004", "90020", "90036"], "Miracle Mile": ["90036"],
    "Beverly Grove": ["90036", "90048"],
    "Mid-Wilshire": ["90010", "90019", "90020", "90036"],
    "Fairfax": ["90036", "90046", "90048"], "Carthay": ["90035"],
    "Pico-Robertson": ["90035"], "Hollywood": ["90028", "90038", "90068"],
    "Hollywood Hills": ["90046", "90068", "90069"], "Los Feliz": ["90027"],
    "Silver Lake": ["90026", "90039"], "Echo Park": ["90026"],
    "Studio City": ["91602", "91604", "91607"],
    "Sherman Oaks": ["91403", "91411", "91423"], "Valley Village": ["91607"],
    "Toluca Lake": ["91602"],
    "North Hollywood": ["91601", "91602", "91605", "91606", "91607"],
    "Encino": ["91316", "91436"], "Tarzana": ["91356"],
    "Woodland Hills": ["91364", "91367"], "Calabasas": ["91302"],
    "Hidden Hills": ["91302"], "Porter Ranch": ["91326"],
    "Granada Hills": ["91344"], "Northridge": ["91324", "91325"],
    "Burbank": ["91501", "91502", "91504", "91505", "91506"],
    "Glendale": ["91201", "91202", "91203", "91204", "91205", "91206", "91207", "91208", "91210"],
    "Pasadena": ["91101", "91103", "91104", "91105", "91106", "91107"],
    "South Pasadena": ["91030"], "San Marino": ["91108"],
    "Arcadia": ["91006", "91007"], "Sierra Madre": ["91024"],
    "Monrovia": ["91016"], "San Gabriel": ["91775", "91776"],
    "Manhattan Beach": ["90266"], "Hermosa Beach": ["90254"],
    "Redondo Beach": ["90277", "90278"], "El Segundo": ["90245"],
    "Torrance": ["90501", "90503", "90504", "90505", "90510"],
    "Palos Verdes Estates": ["90274"], "Rancho Palos Verdes": ["90275"],
    "Rolling Hills": ["90274"], "Rolling Hills Estates": ["90274"],
    "Long Beach": ["90802", "90803", "90804", "90805", "90806", "90807", "90808", "90810", "90813", "90814", "90815"],
    "Malibu": ["90265"], "Topanga": ["90290"], "Agoura Hills": ["91301"],
    "Westlake Village": ["91361", "91362"],
}
