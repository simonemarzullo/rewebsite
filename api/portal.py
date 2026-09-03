"""Client dashboard (/clientaccess) and admin panel (/admin) that feeds it,
combined into one Vercel Python function. Routed internally by a `section`
query param (dashboard|admin) set in vercel.json's rewrite destinations --
the same pattern api/areas.py already uses for its own multiple routes.
(The internal section value stays "dashboard" -- only the public URL
changed to /clientaccess.)

Combined into a single file deliberately: Vercel's Hobby plan caps a
deployment at 12 Serverless Functions, and this site already had 11 --
two more self-contained files (one per route, matching every other page
here) would have pushed it to 13 and failed to deploy
(exceeded_serverless_functions_per_deployment). One file for both keeps
the total at 12.

Auth model: each client gets their own row in the `clients` table (email
+ a password Simone assigns via /admin) -- same individual-account model
now used for off-market buyers (also managed from here, see the
"Off-market buyers + listings" section below). Admin itself still uses a
single shared ADMIN_PASSWORD, since there's only ever one admin. Session
cookies are HMAC-signed and
role-scoped (a client token can't be replayed as an admin token or
someone else's client session). Both sessions last 24h -- sellers re-enter
their password daily, which also naturally caps the FollowUpBoss login
notification below to about once per day per seller instead of needing
its own throttling/schema.

Every successful seller login pushes a FollowUpBoss event (same
push-to-FollowUpBoss pattern as api/offmarket.py and api/submit-lead.py,
using the FUB_API_KEY/FUB_SOURCE/FUB_SYSTEM* env vars already set for
those) so Simone knows when a seller checks their dashboard.

"Cancel account" sets active=false rather than deleting -- a client's
data stays intact and shows up in admin's History filter, never gone for
good.

Requires the `clients`/`listings`/`feedback_notes`/`offers`/`open_houses`/
`offmarket_buyers`/`offmarket_listings` tables from db/schema.sql,
POSTGRES_URL (or DATABASE_URL), ADMIN_PASSWORD, and SESSION_SECRET.

This admin page also manages the off-market buyers + listings that feed
api/offmarket.py's /off-market page and its public /flyer/<id> pages --
those two files share the `offmarket_buyers`/`offmarket_listings` tables
but, per this project's no-cross-import rule, duplicate their own copies
of the DB helpers and password/session code rather than importing them
from here.

Team member / resource hub support was dropped from this build to fit
the function-count limit -- may come back as its own file later if the
site drops below 12 functions, or once this project is on a paid plan.
"""

import base64
import hashlib
import hmac
import html
import html.parser
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from http.server import BaseHTTPRequestHandler

import psycopg2
import psycopg2.errors
import psycopg2.extras

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CLIENT_COOKIE_NAME = "client_session"
ADMIN_COOKIE_NAME = "admin_session"
SESSION_HOURS = {"client": 24, "admin": 24}  # both: 24h -- sellers re-enter their password daily
MAX_BODY_BYTES = 16 * 1024
MAX_FIELD_LEN = 500
MIN_PASSWORD_LEN = 8
FUB_EVENTS_URL = "https://api.followupboss.com/v1/events"
FUB_API_BASE = "https://api.followupboss.com/v1"

# --- Buyer Match prospecting tool -------------------------------------------
# The fixed set of FollowUpBoss person custom fields the tool matches on and
# (Phase 2) backfills. Referenced in API payloads as "custom" + name, e.g.
# customBedrooms -- names are case-sensitive. label -> FUB field type.
BUYER_MATCH_FIELDS = [
    ("Bedrooms", "number"),
    ("Bathrooms", "number"),
    ("SqFt", "number"),
    ("LotSize", "number"),
    ("YearBuilt", "number"),
    ("PropertyType", "text"),
    ("AskingPrice", "number"),
    ("Area", "text"),
]
BUYER_MATCH_MIN_SCORE = 35  # rows below this are dropped from the results
_MATCH_BEDS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:\+)?\s*(?:bed|bd|br|bedroom)", re.I)
_MATCH_BATHS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:\+)?\s*(?:bath|ba|bthrm|bathroom)", re.I)
_MATCH_SQFT_RE = re.compile(r"([\d,]{3,})\s*(?:sq\.?\s*ft|sqft|sf)\b", re.I)
_ZIP_RE = re.compile(r"\b(9[0-3]\d{3})\b")

# --- LA-area market -> ZIP directory --------------------------------------
# Ported 2026-09-03 from Agent Circle (src/lib/constants.ts + locations.ts,
# extracted 2026-08-24). Buyer needs and FUB prospects match on location by
# resolving BOTH sides to a set of ZIPs (a market name expands to all its
# ZIPs; a bare 5-digit token is itself) and testing for overlap -- so
# "Santa Monica" on a buyer need matches a prospect recorded only as 90403.
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
LA_MARKETS = list(ZIPS_BY_AREA.keys())
ALL_LA_ZIPS = {z for zs in ZIPS_BY_AREA.values() for z in zs}
_AREA_BY_ZIP = {}
for _a, _zs in ZIPS_BY_AREA.items():
    for _z in _zs:
        _AREA_BY_ZIP.setdefault(_z, []).append(_a)
_MARKET_LC = {a.lower(): a for a in LA_MARKETS}


def resolve_zips(tokens):
    """(zips:set, texts:list) -- a recognized market name -> all its ZIPs;
    a bare 5-digit token -> itself; anything else -> a lowercased text token
    kept for a substring fallback."""
    zips, texts = set(), []
    for raw in tokens or []:
        t = str(raw or "").strip()
        if not t:
            continue
        canon = _MARKET_LC.get(t.lower())
        if canon:
            zips.update(ZIPS_BY_AREA[canon])
            continue
        m = _ZIP_RE.search(t) or re.search(r"\b(\d{5})\b", t)
        if m and len(re.sub(r"\D", "", t)) == 5:
            zips.add(m.group(1))
        else:
            texts.append(t.lower())
    return zips, texts


def zip_area_label(z):
    names = _AREA_BY_ZIP.get(z)
    return f"{names[0]} ({z})" if names else z


def _market_datalist_options():
    return "".join(f'<option value="{html.escape(a)}">' for a in LA_MARKETS)


# --- Property type: one canonical set for buyer needs + enrichment --------
# LA County's UseType is just "Residential" for every home -- useless for
# matching. The finer split is in UseCode (01xx single, 02-05xx 2/3/4/5+
# units, 06xx condo) and the Units count. "Residential" is treated as blank
# so a re-enrichment run replaces it with the real type.
PROP_TYPES = ["Single Family Home", "Condo/Townhome", "Multifamily", "Land/Lot", "Commercial"]
PROP_TYPE_STALE = {"", "residential"}  # values a re-enrichment run should overwrite


def norm_property_type(use_code, use_desc, units=0):
    """LA County UseCode/UseDescription/Units -> one of PROP_TYPES, or ''."""
    uc = re.sub(r"\s", "", str(use_code or "")).upper()[:2]
    ud = str(use_desc or "").strip().lower()
    try:
        u = float(units or 0)
    except (TypeError, ValueError):
        u = 0
    if uc in ("02", "03", "04", "05") or u >= 2 or "unit" in ud or "apartment" in ud:
        return "Multifamily"
    if uc == "06" or "condo" in ud or "town" in ud:
        return "Condo/Townhome"
    if uc == "01" or ud == "single":
        return "Single Family Home"
    if any(k in ud for k in ("store", "office", "warehous", "commercial", "industrial",
                             "church", "school", "restaurant", "parking", "service station",
                             "manf", "shop", "hotel", "motel")):
        return "Commercial"
    if "vacant" in ud or "land" in ud:
        return "Land/Lot"
    return ""


def norm_buyer_type(s):
    """A free-typed property type -> canonical PROP_TYPES value (kept as-is
    if it matches nothing, so an odd entry still gets a loose substring test)."""
    t = str(s or "").strip().lower()
    if not t:
        return ""
    if any(k in t for k in ("condo", "town", "loft")):
        return "Condo/Townhome"
    if any(k in t for k in ("multi", "duplex", "triplex", "fourplex", "plex", "apartment", "income", " unit", "units")):
        return "Multifamily"
    if any(k in t for k in ("single", "sfr", "sfd", "detached", "house")):
        return "Single Family Home"
    if any(k in t for k in ("land", "lot", "vacant")):
        return "Land/Lot"
    if any(k in t for k in ("commercial", "retail", "office", "industrial")):
        return "Commercial"
    return str(s).strip()


def _prop_type_datalist_options():
    return "".join(f'<option value="{html.escape(t)}">' for t in PROP_TYPES)

# --- Phase 2: enrich FUB contacts from LA County Assessor public records ----
# Free, no key, official. ArcGIS REST layer, ~2.4M parcels, updated monthly.
# Building attributes repeat 1..5 for parcels with multiple structures.
LACOUNTY_PARCEL_URL = (
    "https://public.gis.lacounty.gov/public/rest/services/"
    "LACounty_Cache/LACounty_Parcel/MapServer/0/query"
)
_ENRICH_BATCH_DEFAULT = 10   # FUB contacts per batch; run_enrich_batch also self-limits to ~40s wall-clock
_STREET_SUFFIX_RE = re.compile(
    r"\b(st|str|street|ave|av|avenue|blvd|boulevard|dr|drive|rd|road|ln|lane|ct|court|"
    r"pl|place|way|ter|terrace|cir|circle|hwy|highway|pkwy|parkway|trl|trail)\b\.?\s*$", re.I)
_UNIT_RE = re.compile(
    r"(?:\s(?:apt|apartment|unit|ste|suite|rm|room|fl|floor|no|bldg|lot|sp|space)\.?\s*[\w-]+"
    r"|\s?#\s*[\w-]+)\s*$", re.I)
_LEADING_DIR_RE = re.compile(r"^(N|S|E|W|NE|NW|SE|SW)\s+", re.I)

LISTING_STATUSES = ["Active", "Under Contract", "Sold", "Expired", "Withdrawn"]
OFFMARKET_STATUSES = ["Available", "Pending", "Sold"]
MAX_PHOTO_URLS = 20

# Reused for both the "Add New Listing" and every existing listing's edit
# form -- a contenteditable rich-text box (see .adm-rte-editor JS) sits
# right below this and is kept in sync with a hidden `description` input.
_RTE_TOOLBAR_HTML = """<div class="adm-rte-toolbar">
            <button type="button" class="adm-rte-btn" data-cmd="bold" title="Bold"><strong>B</strong></button>
            <button type="button" class="adm-rte-btn" data-cmd="italic" title="Italic"><em>I</em></button>
            <span class="adm-rte-sep" aria-hidden="true"></span>
            <button type="button" class="adm-rte-btn" data-cmd="insertUnorderedList" title="Bullet list">&bull; List</button>
            <button type="button" class="adm-rte-btn" data-cmd="insertOrderedList" title="Numbered list">1. List</button>
            <span class="adm-rte-sep" aria-hidden="true"></span>
            <button type="button" class="adm-rte-btn" data-cmd="justifyLeft" title="Align left">Left</button>
            <button type="button" class="adm-rte-btn" data-cmd="justifyCenter" title="Align center">Center</button>
            <button type="button" class="adm-rte-btn" data-cmd="justifyRight" title="Align right">Right</button>
          </div>"""
# Selectable feedback categories -- "pricing_agent" and "buyer_feedback" used
# to be split further into pricing-specific vs. general buyer feedback
# ("Pricing Feedback (Agent/Buyer)" + a separate plain "Buyer Feedback");
# simplified down to just who it came from, dropping the pricing framing.
FEEDBACK_CATEGORIES = {
    "showing": "Showing Feedback",
    "pricing_agent": "Buyer's Agent Feedback",
    "buyer_feedback": "Buyer's Feedback",
}
# "pricing_buyer" is no longer offered in the Type dropdown (folded into the
# broader "Buyer's Feedback" above) but existing notes saved under it still
# need a real label instead of the raw DB value.
LEGACY_FEEDBACK_LABELS = {"pricing_buyer": "Buyer's Feedback"}


def feedback_category_label(category):
    return FEEDBACK_CATEGORIES.get(category) or LEGACY_FEEDBACK_LABELS.get(category) or category

# Shared tab-toggle script for the 4-tab layout on each listing (Activity /
# Number of Offers / Open Houses & Showings / Feedbacks) -- used on both
# the client dashboard and the admin panel, each listing scoped independently
# via the nearest [data-tabscope] ancestor.
DB_TABS_SCRIPT = """
document.querySelectorAll('[data-tabscope]').forEach(function (scope) {
  var tabs = scope.querySelectorAll(':scope > .db-tabs > .db-tab');
  var panels = scope.querySelectorAll(':scope > .db-tab-panel');
  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      tabs.forEach(function (t) { t.classList.toggle('active', t === tab); });
      panels.forEach(function (p) { p.style.display = (p.dataset.tabPanel === tab.dataset.tab) ? '' : 'none'; });
    });
  });
});
"""

# Client-side glue for the /admin "Buyer Match" section. Kept as its own
# module constant (single braces) and dropped in via <script>{BUYER_MATCH_SCRIPT}</script>
# so it doesn't fight the doubled-brace f-string in build_admin_html. Reuses
# the adminPost() helper defined in that page's main script block.
BUYER_MATCH_SCRIPT = r"""
(function () {
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c];
    });
  }
  function chip(check) {
    var cls = check.status === 'hit' ? 'bm-chip-hit' : (check.status === 'miss' ? 'bm-chip-miss' : 'bm-chip-unknown');
    return '<span class="bm-chip ' + cls + '">' + esc(check.label) + '</span>';
  }
  function matchRow(m) {
    var prof = m.prof || {};
    var bits = [];
    if (prof.area) bits.push(esc(prof.area));
    if (prof.asking_price) bits.push('$' + Number(prof.asking_price).toLocaleString());
    var bb = [];
    if (prof.beds) bb.push(esc(prof.beds) + ' bd');
    if (prof.baths) bb.push(esc(prof.baths) + ' ba');
    if (prof.sqft) bb.push(Number(prof.sqft).toLocaleString() + ' sqft');
    if (bb.length) bits.push(bb.join(' / '));
    var name = m.fub_url
      ? '<a href="' + esc(m.fub_url) + '" target="_blank" rel="noopener noreferrer">' + esc(m.name) + '</a>'
      : esc(m.name);
    var chips = (m.checks || []).map(chip).join('');
    return '<div class="bm-match">'
      + '<div class="bm-match-head"><span class="bm-score">' + Math.round(m.score) + '</span>'
      + '<span class="bm-match-name">' + name + '</span>'
      + '<span class="bm-match-meta">' + esc(bits.join('  ·  ')) + '</span></div>'
      + '<div class="bm-chips">' + chips + '</div></div>';
  }
  function renderMatches(container, matches, emptyMsg) {
    if (!matches || !matches.length) {
      container.innerHTML = '<p class="db-empty-note">' + esc(emptyMsg || 'No matching prospects.') + '</p>';
      return;
    }
    container.innerHTML = matches.map(matchRow).join('');
  }

  var form = document.getElementById('bm-need-form');
  if (form) {
    var sourceSel = form.querySelector('[name="buyer_source"]');
    var agentBox = document.getElementById('bm-agent-fields');
    function syncAgent() {
      if (agentBox) agentBox.style.display = (sourceSel && sourceSel.value === 'other_agent') ? '' : 'none';
    }
    if (sourceSel) sourceSel.addEventListener('change', syncAgent);
    syncAgent();

    form.addEventListener('submit', async function (e) {
      e.preventDefault();
      var btn = form.querySelector('button[type="submit"]');
      var out = document.getElementById('bm-need-result');
      var payload = {action: 'buyer_need_run'};
      new FormData(form).forEach(function (v, k) { payload[k] = v; });
      payload.log_matches = form.querySelector('[name="log_matches"]').checked;
      btn.disabled = true;
      var prev = btn.textContent;
      btn.textContent = 'Scanning FollowUpBoss…';
      out.innerHTML = '<p class="db-empty-note">Scanning your Nurture pipeline…</p>';
      try {
        var data = await adminPost(payload);
        var head = '<p class="bm-note">' + esc(data.save_note || '') + '</p>';
        if (data.notice) head += '<p class="bm-note bm-note-warn">' + esc(data.notice) + '</p>';
        head += '<h4 class="bm-results-title">' + (data.matches ? data.matches.length : 0)
          + ' matching prospect(s) — scanned ' + (data.scanned || 0) + '</h4>';
        out.innerHTML = head + '<div id="bm-need-matches"></div>';
        renderMatches(document.getElementById('bm-need-matches'), data.matches);
        var tbl = document.getElementById('bm-saved-needs');
        if (tbl) setTimeout(function () { window.location.reload(); }, 1200);
      } catch (err) {
        out.innerHTML = '<p class="om-error" style="display:block">' + esc(err.message) + '</p>';
      } finally {
        btn.disabled = false;
        btn.textContent = prev;
      }
    });
  }

  var rematchBtn = document.getElementById('bm-rematch-all');
  if (rematchBtn) {
    rematchBtn.addEventListener('click', async function () {
      var out = document.getElementById('bm-rematch-result');
      rematchBtn.disabled = true;
      var prev = rematchBtn.textContent;
      rematchBtn.textContent = 'Re-matching…';
      out.innerHTML = '<p class="db-empty-note">Re-scanning for every saved buyer need…</p>';
      try {
        var data = await adminPost({action: 'rematch_buyer_needs'});
        if (!data.report || !data.report.length) {
          out.innerHTML = '<p class="db-empty-note">No active buyer needs to re-match.</p>';
        } else {
          out.innerHTML = data.report.map(function (r) {
            var body = '<div class="bm-report-matches"></div>';
            return '<details class="adm-client"><summary><span class="adm-client-email">'
              + esc(r.buyer_name) + '</span><span class="om-status">' + r.match_count + ' match(es)</span></summary>'
              + '<div class="adm-client-body" data-matches=\'' + esc(JSON.stringify(r.matches || [])) + '\'>' + body + '</div></details>';
          }).join('');
          out.querySelectorAll('.adm-client-body[data-matches]').forEach(function (el) {
            var matches = [];
            try { matches = JSON.parse(el.getAttribute('data-matches')); } catch (e) {}
            renderMatches(el.querySelector('.bm-report-matches'), matches);
          });
        }
      } catch (err) {
        out.innerHTML = '<p class="om-error" style="display:block">' + esc(err.message) + '</p>';
      } finally {
        rematchBtn.disabled = false;
        rematchBtn.textContent = prev;
      }
    });
  }

  var setupBtn = document.getElementById('bm-fub-setup');
  if (setupBtn) {
    setupBtn.addEventListener('click', async function () {
      var out = document.getElementById('bm-fub-setup-result');
      setupBtn.disabled = true;
      var prev = setupBtn.textContent;
      setupBtn.textContent = 'Checking FollowUpBoss…';
      try {
        var data = await adminPost({action: 'fub_setup_fields'});
        out.innerHTML = '<p class="bm-note">' + esc(data.summary) + '</p>';
      } catch (err) {
        out.innerHTML = '<p class="om-error" style="display:block">' + esc(err.message) + '</p>';
      } finally {
        setupBtn.disabled = false;
        setupBtn.textContent = prev;
      }
    });
  }

  // --- Phase 2: LA County enrichment sweep (loops small batches) ---------
  var enStart = document.getElementById('bm-enrich-start');
  if (enStart) {
    var enStop = document.getElementById('bm-enrich-stop');
    var enReset = document.getElementById('bm-enrich-reset');
    var enOut = document.getElementById('bm-enrich-result');
    var enLine = document.getElementById('bm-enrich-state');
    var enBusy = false;
    var enStopReq = false;
    var enCtrl = null;

    function enIdle() {
      enBusy = false;
      enStart.disabled = false; enStart.textContent = 'Start / resume sweep';
      enStop.style.display = 'none';
      enReset.style.display = ''; enReset.disabled = false;
    }

    async function enBatch() {
      enCtrl = new AbortController();
      var res = await fetch('/api/portal?section=admin', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: 'fub_enrich_run'}), signal: enCtrl.signal,
      });
      var d = await res.json();
      if (!res.ok || !d.ok) throw new Error(d.error || 'Enrichment batch failed.');
      return d;
    }

    async function enLoop() {
      if (enBusy) return;
      enBusy = true; enStopReq = false;
      enStart.disabled = true; enStart.textContent = 'Sweeping…';
      enStop.style.display = ''; enStop.disabled = false; enStop.textContent = 'Stop';
      enReset.style.display = 'none';
      var totUpd = 0, totProc = 0;
      try {
        while (!enStopReq) {
          var d = await enBatch();
          totProc += d.processed || 0; totUpd += d.updated || 0;
          var ex = (d.examples || []).map(function (e) {
            return '<li>' + esc(e.name) + ' — filled ' + esc((e.filled || []).join(', '))
              + (e.matched ? ' <span class="bm-match-meta">(' + esc(e.matched) + ')</span>' : '') + '</li>';
          }).join('');
          enOut.innerHTML = '<p class="bm-note">This run: filled ' + totUpd + ' of ' + totProc
            + ' scanned. Last batch: ' + (d.updated || 0) + ' filled, ' + (d.no_match || 0)
            + ' no LA County match, ' + (d.no_address || 0) + ' without a street address.</p>'
            + (ex ? '<ul class="bm-note" style="margin-top:0">' + ex + '</ul>' : '');
          if (d.totals) {
            enLine.textContent = 'Resume at contact #' + d.next_offset + ' · ' + d.totals.passes
              + ' full pass(es) done · ' + d.totals.updated + ' filled / ' + d.totals.seen
              + ' scanned · ' + d.totals.no_match + ' with no LA County match';
          }
          if (d.done) {
            enOut.innerHTML += '<p class="bm-note">Completed a full pass over the database.</p>';
            break;
          }
          if (enStopReq) break;
          await new Promise(function (r) { setTimeout(r, 300); });
        }
        if (enStopReq) enOut.innerHTML += '<p class="bm-note">Stopped. Progress up to the last finished batch is saved — Start resumes from there.</p>';
      } catch (err) {
        if (err && err.name === 'AbortError') {
          enOut.innerHTML += '<p class="bm-note">Stopped. The batch in progress may still finish server-side; Start picks up from the saved point.</p>';
        } else {
          enOut.innerHTML += '<p class="om-error" style="display:block">' + esc(err.message) + '</p>';
        }
      } finally {
        enIdle();
      }
    }

    enStart.addEventListener('click', enLoop);
    enStop.addEventListener('click', function () {
      enStopReq = true;
      enStop.disabled = true; enStop.textContent = 'Stopping…';
      if (enCtrl) { try { enCtrl.abort(); } catch (e) {} }
    });
    enReset.addEventListener('click', async function () {
      if (enBusy) { alert('Stop the sweep first.'); return; }
      if (!confirm('Reset the sweep back to the start of the database?')) return;
      try {
        await adminPost({action: 'fub_enrich_reset'});
        enLine.textContent = 'Not run yet.';
        enOut.innerHTML = '<p class="bm-note">Progress reset.</p>';
      } catch (err) {
        enOut.innerHTML = '<p class="om-error" style="display:block">' + esc(err.message) + '</p>';
      }
    });
  }
})();
"""

# --- Public /match page: styles + client script (single braces) ----------
MATCH_PAGE_CSS = """<style>
  .mt-wrap{max-width:600px;margin:0 auto}
  .mt-row{display:flex;gap:14px;flex-wrap:wrap}
  .mt-row > .om-field{flex:1 1 150px}
  .mt-rep{border:1px solid var(--g3);padding:16px 18px;margin:6px 0}
  .mt-rep-q{font-size:.9rem;color:var(--white);margin-bottom:10px}
  .mt-rep label{display:inline-flex;align-items:center;gap:8px;margin-right:22px;color:var(--g6);font-size:.9rem;cursor:pointer}
  #mt-agent{margin-top:12px}
  #mt-agent[hidden]{display:none}
  .mt-note{font-size:.78rem;color:var(--g5);line-height:1.65;margin-top:4px}
  .mt-result{text-align:center;padding:20px 0}
  .mt-count{font-family:var(--serif);font-size:clamp(3rem,12vw,5rem);color:var(--red);line-height:1}
  .mt-result h2{font-family:var(--serif);font-weight:400;font-size:1.4rem;color:var(--white);margin:10px 0 6px}
  .mt-result p{color:var(--g5);font-size:.92rem;line-height:1.7;max-width:44ch;margin:0 auto}
  .mt-msg{margin-top:26px;border-top:1px solid var(--g3);padding-top:22px;text-align:left}
  .mt-msg .om-field-label{margin-bottom:8px}
  .mt-thanks{color:var(--red);font-size:.85rem;letter-spacing:.06em;text-transform:uppercase;margin-top:12px}
  #mt-hp{position:absolute;left:-9999px;width:1px;height:1px;opacity:0}
</style>"""

MATCH_PAGE_SCRIPT = r"""
(function () {
  var form = document.getElementById('mt-form');
  if (!form) return;
  var errEl = document.getElementById('mt-error');
  var resultEl = document.getElementById('mt-result');
  var agentBox = document.getElementById('mt-agent');
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c];
    });
  }
  function syncRep() {
    var yes = form.querySelector('input[name="represented"][value="yes"]');
    agentBox.hidden = !(yes && yes.checked);
  }
  form.querySelectorAll('input[name="represented"]').forEach(function (r) {
    r.addEventListener('change', syncRep);
  });
  syncRep();

  async function post(payload) {
    var res = await fetch('/api/portal?section=match', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    var data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Something went wrong.');
    return data;
  }

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    errEl.textContent = ''; errEl.style.display = 'none';
    var fd = new FormData(form);
    var payload = {action: 'search'};
    fd.forEach(function (v, k) { payload[k] = v; });
    var btn = form.querySelector('button[type="submit"]');
    var prev = btn.textContent;
    btn.disabled = true; btn.textContent = 'Checking our network…';
    try {
      var data = await post(payload);
      var first = (payload.name || '').trim().split(' ')[0];
      var body;
      if (data.count > 0) {
        body = '<div class="mt-count">' + data.count + '</div>'
          + '<h2>' + (data.count === 1 ? 'home matches' : 'homes match') + ' what you’re looking for</h2>'
          + '<p>These aren’t listed publicly. Contact Simone Marzullo directly for details — or send a note below and he’ll be in touch.</p>';
      } else {
        body = '<h2>Thank you' + (first ? ', ' + esc(first) : '') + '.</h2>'
          + '<p>Nothing in our network matches this search right now. Simone will follow up personally as soon as something fits — or send him a note below.</p>';
      }
      resultEl.innerHTML = body + msgFormHtml(payload);
      form.hidden = true;
      resultEl.hidden = false;
      resultEl.scrollIntoView({block: 'center', behavior: 'smooth'});
      wireMsgForm(payload);
    } catch (err) {
      errEl.textContent = err.message; errEl.style.display = 'block';
    } finally {
      btn.disabled = false; btn.textContent = prev;
    }
  });

  function msgFormHtml() {
    return '<div class="mt-msg"><form id="mt-msg-form">'
      + '<label class="om-field"><span class="om-field-label">Your message for Simone (optional)</span>'
      + '<textarea name="message" class="om-input" rows="4" style="width:100%;font-family:inherit;resize:vertical"></textarea></label>'
      + '<button type="submit" class="btn-primary" style="margin-top:12px">Send message</button>'
      + '<div class="mt-thanks" id="mt-msg-done" hidden>Message sent — thank you.</div>'
      + '</form></div>';
  }
  function wireMsgForm(lead) {
    var mf = document.getElementById('mt-msg-form');
    if (!mf) return;
    mf.addEventListener('submit', async function (e) {
      e.preventDefault();
      var msg = mf.message.value.trim();
      if (!msg) return;
      var b = mf.querySelector('button');
      b.disabled = true; b.textContent = 'Sending…';
      try {
        await post({action: 'message', name: lead.name, email: lead.email, phone: lead.phone, message: msg});
        mf.message.disabled = true;
        b.style.display = 'none';
        document.getElementById('mt-msg-done').hidden = false;
      } catch (err) {
        b.disabled = false; b.textContent = 'Send message';
        alert(err.message);
      }
    });
  }
})();
"""

NAV_ITEMS = [
    ("AREAS", "/areas"),
    ("OFF-MARKET", "/off-market"),
    ("NEWS", "/news"),
    ("ABOUT", "/about"),
    ("THE AGENCY", "/#the-agency"),
    ("CALCULATOR", "/flipcalculator"),
    ("CONTACT", "/contact"),
    ("LOG IN", "/clientaccess"),
]

THEME_INIT_SCRIPT = """<script>if ('scrollRestoration' in history) history.scrollRestoration = 'manual'; window.scrollTo(0, 0);</script>
<script>window.addEventListener('pageshow', function () { window.scrollTo(0, 0); });</script>
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


def _nav_html():
    links = [f'<li><a href="{href}">{label}</a></li>' for label, href in NAV_ITEMS]
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


def render_page(body_html, title):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{THEME_INIT_SCRIPT}
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>{html.escape(title)}</title>
<meta name="robots" content="noindex, nofollow">
<meta name="theme-color" content="#000000">
<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">
<link rel="icon" type="image/png" sizes="32x32" href="/assets/favicon-32.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=EB+Garamond:ital,wght@0,400;0,500;1,400;1,500&family=Inter:wght@300;400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/assets/areas.css">
</head>
<body>
{_nav_html()}
{body_html}
{_footer_html()}
{FOOTER_AND_SCRIPTS}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Password hashing (PBKDF2-HMAC-SHA256, stdlib only). Format mirrors
# Django's: "pbkdf2_sha256$<iters>$<salt>$<hash>".
# ---------------------------------------------------------------------------
PBKDF2_ITERATIONS = 600_000


def hash_password(password):
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password, stored_hash):
    try:
        algo, iterations, salt_hex, hash_hex = stored_hash.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
    return hmac.compare_digest(dk, expected)


def clean(value, max_len=MAX_FIELD_LEN):
    return str(value).strip()[:max_len] if value is not None else ""


_GDRIVE_FILE_ID_RE = re.compile(r"drive\.google\.com/file/d/([a-zA-Z0-9_-]+)")
_GDRIVE_ID_PARAM_RE = re.compile(r"[?&]id=([a-zA-Z0-9_-]+)")
_PRICE_NUMERIC_RE = re.compile(r"^\$?\s*[\d,]+(?:\.\d+)?\s*([mMkK])?$")
_PRICE_RANGE_SPLIT_RE = re.compile(r"\s*(?:[–—-]|\bto\b)\s*", re.I)

# Listing descriptions are edited as rich text (bold/italic/lists/alignment)
# in the admin panel and stored as HTML, so they need a real allowlist
# sanitizer rather than a plain-text clean() -- this runs on every save.
# api/offmarket.py runs the same allowlist again at render time (duplicated,
# not imported, per this project's no-cross-import rule) as defense in depth
# and so any pre-existing plain-text description (saved before this feature
# existed) degrades safely into escaped plain text instead of raw HTML.
MAX_DESCRIPTION_HTML_LEN = 6000
_DESC_ALLOWED_TAGS = {"b", "strong", "i", "em", "u", "ul", "ol", "li", "br", "div", "p", "span"}
_DESC_VOID_TAGS = {"br"}
_DESC_ALIGN_STYLE_RE = re.compile(r"^text-align:\s*(left|center|right|justify)\s*;?$", re.IGNORECASE)


class _DescriptionHTMLSanitizer(html.parser.HTMLParser):
    """Strips everything except a small allowlisted subset of formatting
    tags, and the `style` attribute only when it is exactly a text-align
    rule -- no other attribute (href, src, onclick, class, arbitrary style,
    etc.) survives. Unknown tags are dropped but their text content is kept
    (escaped), so e.g. a stray <script> just becomes visible plain text,
    never executes."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.open_stack = []

    def handle_starttag(self, tag, attrs):
        self._open(tag, attrs)

    def handle_startendtag(self, tag, attrs):
        self._open(tag, attrs, self_close=True)

    def _open(self, tag, attrs, self_close=False):
        if tag not in _DESC_ALLOWED_TAGS:
            return
        attr_html = ""
        if tag in ("div", "p", "span", "li"):
            for name, value in attrs:
                if name == "style" and value and _DESC_ALIGN_STYLE_RE.match(value.strip()):
                    attr_html = f' style="{value.strip()}"'
                    break
        if tag in _DESC_VOID_TAGS:
            self.out.append(f"<{tag}>")
        elif self_close:
            self.out.append(f"<{tag}{attr_html}></{tag}>")
        else:
            self.out.append(f"<{tag}{attr_html}>")
            self.open_stack.append(tag)

    def handle_endtag(self, tag):
        if tag not in _DESC_ALLOWED_TAGS or tag in _DESC_VOID_TAGS:
            return
        if tag not in self.open_stack:
            return
        while self.open_stack and self.open_stack[-1] != tag:
            self.out.append(f"</{self.open_stack.pop()}>")
        if self.open_stack:
            self.open_stack.pop()
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        self.out.append(html.escape(data))

    def close(self):
        super().close()
        while self.open_stack:
            self.out.append(f"</{self.open_stack.pop()}>")

    def get_html(self):
        return "".join(self.out)


def _sanitize_description_html(raw):
    if not raw:
        return ""
    parser = _DescriptionHTMLSanitizer()
    parser.feed(str(raw)[:MAX_DESCRIPTION_HTML_LEN])
    parser.close()
    return parser.get_html()


def _normalize_photo_url(url):
    """Rewrites a pasted Google Drive or Dropbox "share" link (a viewer
    page, not an image) into the direct-file-content URL that actually
    works in an <img src>. Anything else is left untouched -- this only
    ever tries to fix these two specific, common paste mistakes."""
    if "drive.google.com" in url and "/uc?" not in url:
        m = _GDRIVE_FILE_ID_RE.search(url) or _GDRIVE_ID_PARAM_RE.search(url)
        if m:
            return f"https://drive.google.com/uc?export=view&id={m.group(1)}"
    if "dropbox.com" in url:
        if "dl=0" in url:
            return url.replace("dl=0", "dl=1")
        if "dl=1" not in url:
            return url + ("&dl=1" if "?" in url else "?dl=1")
    return url


def _parse_photo_urls(raw):
    urls = [clean(u, 2000) for u in str(raw or "").splitlines()]
    return [_normalize_photo_url(u) for u in urls if u][:MAX_PHOTO_URLS]


def _normalize_one_price(part):
    """'4995000' / '$4,995,000' / '4.5M' / '850k' -> '$4,995,000', or None
    if it isn't a plain figure."""
    m = _PRICE_NUMERIC_RE.match((part or "").strip())
    if not m:
        return None
    body = re.sub(r"[^\d.]", "", part)
    if not body:
        return None
    try:
        n = float(body)
    except ValueError:
        return None
    suffix = (m.group(1) or "").lower()
    if suffix == "m":
        n *= 1_000_000
    elif suffix == "k":
        n *= 1_000
    return f"${n:,.0f}"


def _normalize_price(raw):
    """Reformats a plain figure ("4995000", "$4.5M", "850k") to "$1,234,567".
    A two-part range separated by - / – / — / "to" becomes "$X – $Y". Anything
    else ("Price upon request") is left exactly as typed."""
    raw = (raw or "").strip()
    if not raw:
        return raw
    parts = _PRICE_RANGE_SPLIT_RE.split(raw, maxsplit=1)
    if len(parts) == 2:
        lo, hi = _normalize_one_price(parts[0]), _normalize_one_price(parts[1])
        return f"{lo} – {hi}" if (lo and hi) else raw
    return _normalize_one_price(raw) or raw


# ---------------------------------------------------------------------------
# Signed session cookies. Role-scoped ("client" carries an id, "admin"
# doesn't) and signed with a dedicated server-only secret (SESSION_SECRET)
# -- never a client's own password or ADMIN_PASSWORD itself, so either can
# be rotated independently of active sessions signed the other way.
# ---------------------------------------------------------------------------
def _session_secret():
    return os.environ.get("SESSION_SECRET", "")


def make_session_token(role, entity_id=None):
    secret = _session_secret()
    expiry = int(time.time()) + SESSION_HOURS[role] * 3600
    payload = f"{role}:{entity_id}:{expiry}" if entity_id is not None else f"{role}:{expiry}"
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(token, expected_role):
    """Returns True for a valid admin token, a client id (int) for a valid
    client token, or None if missing/forged/expired/wrong role."""
    secret = _session_secret()
    if not token or not secret or "." not in token:
        return None
    payload, _, sig = token.rpartition(".")
    expected_sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, sig):
        return None
    parts = payload.split(":")
    if not parts or parts[0] != expected_role:
        return None
    try:
        expiry = int(parts[-1])
    except ValueError:
        return None
    if time.time() >= expiry:
        return None
    if expected_role == "admin":
        return len(parts) == 2
    if len(parts) != 3:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def get_cookie(cookie_header, name):
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


# ---------------------------------------------------------------------------
# Database access -- every query is parameterized (%s placeholders); user
# input is never formatted directly into SQL text.
# ---------------------------------------------------------------------------
_LIBPQ_QUERY_PARAMS = {"sslmode", "connect_timeout", "application_name", "options"}


def _clean_dsn(dsn):
    """Some providers (Supabase's pooler included) append extra query
    params to the connection URL for their own routing purposes (e.g.
    "supa=base-pooler.x") that aren't real libpq options -- psycopg2's DSN
    parser rejects the whole URL outright over a single param it doesn't
    recognize. Keep only the handful of params libpq actually understands."""
    parsed = urllib.parse.urlsplit(dsn)
    query = urllib.parse.parse_qs(parsed.query)
    safe_query = {k: v for k, v in query.items() if k in _LIBPQ_QUERY_PARAMS}
    new_query = urllib.parse.urlencode(safe_query, doseq=True)
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, parsed.fragment))


def get_conn():
    dsn = os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    return psycopg2.connect(_clean_dsn(dsn), connect_timeout=5)


def push_dashboard_login_to_followupboss(email, name):
    """Best-effort notification that a seller just logged into their
    client-access dashboard -- same pattern as offmarket.py's FollowUpBoss
    push. Never raises; a FollowUpBoss outage should never break a login."""
    api_key = os.environ.get("FUB_API_KEY")
    if not api_key:
        print("portal: FUB_API_KEY is not configured")
        return
    who = name or email
    event_payload = {
        "source": os.environ.get("FUB_SOURCE", "Simone Marzullo Website"),
        "system": os.environ.get("FUB_SYSTEM", "Simone Marzullo Website"),
        "type": "General Inquiry",
        "message": f"{who} logged into their Client Access dashboard.",
        "person": {"emails": [{"value": email}], "tags": ["Client Dashboard Login"]},
    }
    req = urllib.request.Request(
        FUB_EVENTS_URL,
        data=json.dumps(event_payload).encode("utf-8"),
        method="POST",
    )
    auth = base64.b64encode(f"{api_key}:".encode("utf-8")).decode("ascii")
    req.add_header("Authorization", f"Basic {auth}")
    req.add_header("Content-Type", "application/json")
    system_name = os.environ.get("FUB_SYSTEM")
    system_key = os.environ.get("FUB_SYSTEM_KEY")
    if system_name and system_key:
        req.add_header("X-System", system_name)
        req.add_header("X-System-Key", system_key)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        print(f"portal: FollowUpBoss API error {e.code}: {e.read().decode('utf-8', errors='replace')}")
    except Exception as e:
        print(f"portal: unexpected error calling FollowUpBoss: {e}")


# ---------------------------------------------------------------------------
# Buyer Match prospecting tool. Reads seller prospects out of FollowUpBoss
# (the first FUB *read* in this codebase -- everything else here only POSTs
# events) and scores them against a buyer's stated needs so Simone knows who
# to call with an "I have a buyer" listing pitch. Every network call below is
# best-effort: it logs and returns an empty/failure value rather than
# raising, so a FollowUpBoss outage degrades the tool instead of 500-ing.
# ---------------------------------------------------------------------------
def _fub_headers():
    api_key = os.environ.get("FUB_API_KEY")
    if not api_key:
        return None
    headers = {
        "Authorization": "Basic " + base64.b64encode(f"{api_key}:".encode("utf-8")).decode("ascii"),
        "Content-Type": "application/json",
    }
    system_name = os.environ.get("FUB_SYSTEM")
    system_key = os.environ.get("FUB_SYSTEM_KEY")
    if system_name and system_key:
        headers["X-System"] = system_name
        headers["X-System-Key"] = system_key
    return headers


def _fub_request(method, url, payload=None):
    """Returns (status_int, parsed_json_or_None, error_str_or_None). Never raises."""
    headers = _fub_headers()
    if headers is None:
        return (0, None, "FollowUpBoss API key is not configured.")
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            body = json.loads(raw) if raw.strip() else {}
            return (getattr(resp, "status", 200), body, None)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        print(f"portal(buyer-match): FUB {method} {url} -> {e.code}: {detail}")
        return (e.code, None, f"FollowUpBoss returned {e.code}.")
    except Exception as e:
        print(f"portal(buyer-match): FUB {method} {url} failed: {e}")
        return (0, None, "Couldn't reach FollowUpBoss.")


def fub_list_custom_fields():
    """{name: field_dict} for all person custom fields, or None on failure."""
    _status, body, _err = _fub_request("GET", f"{FUB_API_BASE}/customFields?limit=100")
    if not body:
        return None
    items = body.get("customfields") or body.get("customFields") or []
    return {f.get("name"): f for f in items if f.get("name")}


def fub_create_custom_field(label, ftype):
    status, body, err = _fub_request(
        "POST", f"{FUB_API_BASE}/customFields", {"label": label, "type": ftype}
    )
    if body is not None and status in (200, 201):
        return (True, None)
    return (False, err or "create failed")


def fub_setup_custom_fields():
    """Create any BUYER_MATCH_FIELDS field that doesn't exist yet. Returns a
    human summary string. Needs an account-owner API key to create fields."""
    existing = fub_list_custom_fields()
    if existing is None:
        return "Couldn't read custom fields from FollowUpBoss -- check that the API key is valid."
    have, made, failed = [], [], []
    for label, ftype in BUYER_MATCH_FIELDS:
        if ("custom" + label) in existing:
            have.append(label)
            continue
        ok, msg = fub_create_custom_field(label, ftype)
        if ok:
            made.append(label)
        else:
            failed.append(f"{label} ({msg})")
    parts = []
    if made:
        parts.append("Created: " + ", ".join(made) + ".")
    if have:
        parts.append(f"Already set up: {len(have)} of {len(BUYER_MATCH_FIELDS)}.")
    if failed:
        parts.append("Could not create (needs an account-owner API key): " + ", ".join(failed) + ".")
    return "  ".join(parts) or "All property fields are already set up."


def _fub_fieldmap():
    """{label: real custom-field name or None}. Resolved fresh on every scan
    (one extra GET) so fields added straight in the FollowUpBoss UI are picked
    up without waiting for the serverless instance to recycle."""
    existing = fub_list_custom_fields()
    out = {}
    for label, _ftype in BUYER_MATCH_FIELDS:
        guess = "custom" + label
        if existing is None:
            out[label] = guess  # can't reach FUB to confirm -- assume default naming
        elif guess in existing:
            out[label] = guess
        else:
            out[label] = next((n for n in existing if n.lower() == guess.lower()), None)
    return out


def _num(value):
    """Parse a loose numeric string ($2,500,000 / 2.5M / 800k / 4+ / 3-4 /
    1,800) into a float, or None. A range returns its low end."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lower().replace(",", "").replace("$", "")
    if not s:
        return None
    rng = re.search(r"(\d+(?:\.\d+)?)\s*[-–]\s*\d+(?:\.\d+)?", s)
    if rng:
        try:
            return float(rng.group(1))
        except ValueError:
            return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*([mk])?", s)
    if not m:
        return None
    try:
        n = float(m.group(1))
    except ValueError:
        return None
    return n * (1_000_000.0 if m.group(2) == "m" else 1_000.0 if m.group(2) == "k" else 1.0)


def _first(*vals):
    for v in vals:
        if v not in (None, "", []):
            return v
    return None


def _clean_str(v):
    return str(v).strip() if v not in (None, "") else ""


def fub_fetch_nurture_people():
    """(people_list, scanned_count, error_or_None). Paginated, capped at FUB_SCAN_MAX."""
    stage = os.environ.get("FUB_NURTURE_STAGE", "Nurture")
    try:
        cap = int(os.environ.get("FUB_SCAN_MAX", "600"))
    except ValueError:
        cap = 600
    cap = max(1, min(cap, 5000))
    people = []
    # FollowUpBoss rejects offset paging past ~2000 rows, so follow the
    # _metadata.nextLink cursor URL instead of incrementing an offset.
    url = f"{FUB_API_BASE}/people?" + urllib.parse.urlencode({
        "stage": stage, "limit": 100, "fields": "allFields,allCustom",
        "includeTrash": "false",
    })
    while len(people) < cap and url:
        _status, body, err = _fub_request("GET", url)
        if not body:
            return (people, len(people), err or "FollowUpBoss request failed.")
        batch = body.get("people") or []
        people.extend(batch)
        url = (body.get("_metadata") or {}).get("nextLink") or ""
        if len(batch) < 100:
            break
    return (people[:cap], min(len(people), cap), None)


def fub_prop_profile(person, fieldmap):
    """Best-effort property profile for a FUB contact: standard custom fields
    first, then the built-in price / mailing address, then a light regex over
    the background/notes text for anything still blank."""
    def cf(label):
        name = fieldmap.get(label)
        return person.get(name) if name else None

    background = " ".join(str(person.get(f) or "") for f in ("background", "notes")).strip()
    addrs = person.get("addresses") or []
    addr0 = addrs[0] if isinstance(addrs, list) and addrs else {}
    if not isinstance(addr0, dict):
        addr0 = {}

    beds = _num(cf("Bedrooms"))
    if beds is None:
        m = _MATCH_BEDS_RE.search(background)
        beds = _num(m.group(1)) if m else None
    baths = _num(cf("Bathrooms"))
    if baths is None:
        m = _MATCH_BATHS_RE.search(background)
        baths = _num(m.group(1)) if m else None
    sqft = _num(cf("SqFt"))
    if sqft is None:
        m = _MATCH_SQFT_RE.search(background)
        sqft = _num(m.group(1)) if m else None
    asking = _num(cf("AskingPrice"))
    if asking is None:
        asking = _num(person.get("price"))

    zip_code = _clean_str(addr0.get("code"))
    if not zip_code:
        m = _ZIP_RE.search(" ".join(str(addr0.get(k) or "") for k in ("full", "value")) + " " + background)
        zip_code = m.group(1) if m else ""

    return {
        "beds": beds, "baths": baths, "sqft": sqft,
        "lot_size": _num(cf("LotSize")),
        "year_built": _num(cf("YearBuilt")),
        "property_type": _clean_str(cf("PropertyType")),
        "asking_price": asking,
        "area": _clean_str(_first(cf("Area"), addr0.get("city"), addr0.get("code"))),
        "city": _clean_str(_first(cf("Area"), addr0.get("city"))),
        "zip": zip_code,
        "address": _clean_str(_first(addr0.get("street"), addr0.get("full"))),
        "text": background,
    }


def _budget_band(pmin, pmax):
    def m(n):
        if n >= 1_000_000:
            return (f"${n / 1_000_000:.1f}M").replace(".0M", "M")
        return f"${n / 1000:.0f}K"
    if pmin and pmax:
        return f"{m(pmin)}–{m(pmax)}"
    if pmax:
        return f"Under {m(pmax)}"
    if pmin:
        return f"{m(pmin)}+"
    return ""


def _criteria_sentence(c):
    bits = []
    if c.get("beds"):
        bits.append(f"{c['beds']:g}+ bd")
    if c.get("baths"):
        bits.append(f"{c['baths']:g}+ ba")
    sq_lo, sq_hi = c.get("sqft"), c.get("sqft_max")
    if sq_lo and sq_hi:
        bits.append(f"{sq_lo:g}-{sq_hi:g} sqft")
    elif sq_lo:
        bits.append(f"{sq_lo:g}+ sqft")
    elif sq_hi:
        bits.append(f"up to {sq_hi:g} sqft")
    if c.get("types"):
        bits.append("/".join(c["types"]))
    band = _budget_band(c.get("price_min"), c.get("price_max"))
    if band:
        bits.append(band)
    if c.get("areas"):
        bits.append("in " + ", ".join(c["areas"]))
    return ", ".join(bits) or "unspecified"


def score_buyer_match(criteria, prof):
    """-> {score: 0-100, checks: [{label, status}]}. status is hit|miss|unknown.
    A missed hard gate (price/beds/baths/sqft/area) forces the score below the
    display threshold; missing prospect data is 'unknown', not a rejection."""
    checks = []
    score = 0.0
    weight_total = 0.0
    gate_ok = True

    def add(label, status, w, got):
        nonlocal score, weight_total
        checks.append({"label": label, "status": status})
        weight_total += w
        if status == "hit":
            score += w * got
        elif status == "unknown":
            score += w * 0.35

    pmin, pmax = criteria.get("price_min"), criteria.get("price_max")
    ask = prof.get("asking_price")
    if pmin or pmax:
        if ask is None:
            add("Price (no data)", "unknown", 3, 0)
        elif (pmin or 0) <= ask <= (pmax or float("inf")) * 1.05:
            add(f"Price ${ask:,.0f}", "hit", 3, 0.7 if (pmax and ask > pmax) else 1.0)
        else:
            add(f"Price ${ask:,.0f}", "miss", 3, 0)
            gate_ok = False

    for label, key, w in (("Beds", "beds", 2.0), ("Baths", "baths", 1.5)):
        want = criteria.get(key)
        if not want:
            continue
        got_val = prof.get(key)
        if got_val is None:
            add(f"{label} (no data)", "unknown", w, 0)
        elif got_val + 1e-9 >= want:
            add(f"{label} {got_val:g}+", "hit", w, 1.0)
        else:
            add(f"{label} {got_val:g} (< {want:g})", "miss", w, 0)
            gate_ok = False

    sq_min, sq_max = criteria.get("sqft"), criteria.get("sqft_max")
    if sq_min or sq_max:
        sq = prof.get("sqft")
        if sq is None:
            add("Sq ft (no data)", "unknown", 1.5, 0)
        elif (sq_min or 0) - 1 <= sq <= (sq_max or float("inf")) + 1:
            lbl = f"{sq:,.0f} sq ft"
            add(lbl, "hit", 1.5, 1.0)
        else:
            bound = f"< {sq_min:,.0f}" if (sq_min and sq < sq_min) else f"> {sq_max:,.0f}"
            add(f"{sq:,.0f} sq ft ({bound})", "miss", 1.5, 0)
            gate_ok = False

    areas = criteria.get("areas") or []
    if areas:
        want_zips, want_text = resolve_zips(areas)
        # resolve the prospect's location the same way: its market/city name +
        # ZIP field, plus any 5-digit ZIP sitting in the address or notes.
        have_zips, _ = resolve_zips([prof.get("city", ""), prof.get("zip", ""), prof.get("area", "")])
        for z in _ZIP_RE.findall(" ".join([prof.get("address", ""), prof.get("text", "")])):
            have_zips.add(z)
        hay = " ".join(filter(None, [prof.get("area", ""), prof.get("city", ""),
                                     prof.get("address", ""), prof.get("text", "")])).lower()
        if want_zips and have_zips:
            common = want_zips & have_zips
            if common:
                add(f"Area: {zip_area_label(sorted(common)[0])}", "hit", 3, 1.0)
            else:
                add("Area mismatch (ZIP)", "miss", 3, 0)
                gate_ok = False
        elif want_text and hay.strip() and any(t in hay for t in want_text):
            add(f"Area: {next(t for t in want_text if t in hay).title()}", "hit", 3, 1.0)
        elif want_zips and hay.strip() and any(a.lower() in hay for a in areas):
            # buyer gave a market; prospect ZIP unknown but its text names it
            add(f"Area: {next(a for a in areas if a.lower() in hay)}", "hit", 3, 1.0)
        elif not hay.strip() and not have_zips:
            add("Area (no data)", "unknown", 3, 0)
        elif want_zips or want_text:
            # both sides have *some* location but nothing lines up
            add("Area mismatch", "miss", 3, 0)
            gate_ok = False
        else:
            add("Area (unclear)", "unknown", 3, 0)

    # Property type is a soft nudge only -- never gates a row. LA County's
    # type data is fuzzy (condo vs SFR especially), so beds + area + sq ft
    # carry the match; a type mismatch just trims the score a little.
    types = criteria.get("types") or []
    if types:
        pt_raw = (prof.get("property_type") or "").strip()
        pt = norm_buyer_type(pt_raw)
        want = {norm_buyer_type(t) for t in types}
        if not pt_raw or pt_raw.lower() in PROP_TYPE_STALE:
            pass  # unknown -> ignore entirely
        elif pt in want or any(w and (w.lower() in pt.lower() or pt.lower() in w.lower()) for w in want):
            add(f"Type: {pt_raw}", "hit", 1, 1.0)
        else:
            add(f"Type: {pt_raw} (wanted {'/'.join(sorted(want))})", "miss", 1, 0)

    notes = (criteria.get("notes") or "").lower()
    if notes:
        words = set(re.findall(r"[a-z]{4,}", notes))
        text = (prof.get("text") or "").lower()
        overlap = sorted(w for w in words if w in text)
        if overlap:
            add("Keywords: " + ", ".join(overlap[:4]), "hit", 1, min(1.0, len(overlap) / 3.0))

    pct = 100.0 * score / weight_total if weight_total else 0.0
    if not gate_ok:
        pct = min(pct, BUYER_MATCH_MIN_SCORE - 1)
    return {"score": round(pct, 1), "checks": checks, "gate_ok": gate_ok}


def run_buyer_match(criteria):
    """-> {matches: [...], scanned: int, error: str|None}. Pure FUB read; safe
    to call without a DB connection."""
    people, scanned, err = fub_fetch_nurture_people()
    fieldmap = _fub_fieldmap()
    acct = os.environ.get("FUB_ACCOUNT_URL", "").rstrip("/")
    rows = []
    for p in people:
        prof = fub_prop_profile(p, fieldmap)
        res = score_buyer_match(criteria, prof)
        if res["score"] < BUYER_MATCH_MIN_SCORE:
            continue
        # Skip prospects we know nothing relevant about -- a row needs at least
        # one real "hit", not just a pile of "no data" checks.
        if not any(c["status"] == "hit" for c in res["checks"]):
            continue
        name = (_clean_str(p.get("name"))
                or " ".join(filter(None, [p.get("firstName"), p.get("lastName")])).strip()
                or f"Contact #{p.get('id')}")
        rows.append({
            "fub_id": p.get("id"),
            "name": name,
            "fub_url": f"{acct}/2/people/view/{p.get('id')}" if acct and p.get("id") else "",
            "score": res["score"],
            "checks": res["checks"],
            "prof": {
                "area": prof["area"], "asking_price": prof["asking_price"],
                "beds": prof["beds"], "baths": prof["baths"], "sqft": prof["sqft"],
                "property_type": prof["property_type"],
            },
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    return {"matches": rows[:50], "scanned": scanned, "error": err}


def push_buyer_need_to_fub(need, criteria):
    """Create/merge the buyer as a FollowUpBoss contact via an event.
    -> (fub_person_id_or_None, note_string)."""
    if not os.environ.get("FUB_API_KEY"):
        return (None, "Saved locally. Not sent to FollowUpBoss (no API key configured).")
    emails = [{"value": need["buyer_email"]}] if need["buyer_email"] else []
    phones = [{"value": need["buyer_phone"]}] if need["buyer_phone"] else []
    if not emails and not phones:
        return (None, "Saved locally and matched. Not added to FollowUpBoss -- a new contact needs an email or phone.")

    tags = ["Buyer Need", "Prospecting - Buyer Match"]
    tags.append("Buyer Source: Other Agent" if need["buyer_source"] == "other_agent" else "Buyer Source: My Client")
    if need["buyer_source"] == "other_agent" and need["agent_name"]:
        tags.append(f"Referring Agent: {need['agent_name']}")
    for a in criteria.get("areas", []):
        tags.append(f"Buyer Area: {a}")
    band = _budget_band(criteria.get("price_min"), criteria.get("price_max"))
    if band:
        tags.append(f"Buyer Budget: {band}")

    summary = _criteria_sentence(criteria)
    bg = ["Buyer need logged via the Buyer Match tool.", f"Looking for: {summary}"]
    if need["buyer_source"] == "other_agent":
        bg.append("Represented by agent: " + " ".join(filter(None, [
            need["agent_name"], need["agent_brokerage"], need["agent_contact"]])).strip())
    if criteria.get("timeline"):
        bg.append(f"Timeline: {criteria['timeline']}")
    if criteria.get("notes"):
        bg.append(f"Notes: {criteria['notes']}")

    person = {"tags": tags, "background": "\n".join(bg)}
    if need["buyer_name"]:
        parts = need["buyer_name"].split()
        person["firstName"] = parts[0]
        if len(parts) > 1:
            person["lastName"] = " ".join(parts[1:])
    if emails:
        person["emails"] = emails
    if phones:
        person["phones"] = phones

    payload = {
        "source": os.environ.get("FUB_SOURCE", "Simone Marzullo Website"),
        "system": os.environ.get("FUB_SYSTEM", "Simone Marzullo Website"),
        "type": "Property Inquiry",
        "message": f"New buyer need (Buyer Match tool): {summary}",
        "person": person,
    }
    _status, body, err = _fub_request("POST", FUB_EVENTS_URL, payload)
    if isinstance(body, dict):
        pid = body.get("personId") or (body.get("person") or {}).get("id")
        return (pid, f"Added {need['buyer_name'] or 'the buyer'} to FollowUpBoss · tags: {', '.join(tags)}.")
    return (None, f"Saved locally and matched. FollowUpBoss add failed: {err or 'unknown error'}.")


def log_match_to_fub(person_id, criteria_line, buyer_label):
    """Drop a note on a matched prospect so Simone remembers why he's calling."""
    if not person_id:
        return False
    _status, body, _err = _fub_request("POST", f"{FUB_API_BASE}/notes", {
        "personId": person_id,
        "subject": "Buyer Match",
        "body": f"Possible fit for a buyer need: {criteria_line} — buyer: {buyer_label}.",
    })
    return body is not None


# ---------------------------------------------------------------------------
# Public buyer-search page (/match) -- a shareable link + open-house form.
# A visitor submits contact info + what they want; we count matches across
# the off-market listings AND the FUB Nurture pipeline, show only the count
# (never addresses), and push the lead to FollowUpBoss.
# ---------------------------------------------------------------------------
def _offmarket_profile(l):
    """Shape one offmarket_listings row like a score_buyer_match `prof`."""
    zm = _ZIP_RE.search(f"{l.get('address') or ''} {l.get('area') or ''}")
    return {
        "beds": _num(l.get("beds")), "baths": _num(l.get("baths")),
        "sqft": _num(l.get("sqft")), "year_built": None, "property_type": "",
        "asking_price": _num(l.get("price")),
        "area": l.get("area") or "", "city": l.get("area") or "",
        "zip": zm.group(1) if zm else "",
        "address": l.get("address") or "", "text": l.get("description") or "",
    }


def run_public_match(conn, criteria):
    """(total_count, note_or_None). Off-market listings that clear the gate +
    FUB Nurture prospects that clear it."""
    total = 0
    note = None
    try:
        for l in fetch_all_offmarket_listings(conn):
            if not l.get("active"):
                continue
            r = score_buyer_match(criteria, _offmarket_profile(l))
            if r["gate_ok"] and r["score"] >= BUYER_MATCH_MIN_SCORE:
                total += 1
    except Exception as e:
        print(f"portal(match): off-market scan failed: {e}")
    fub = run_buyer_match(criteria)
    total += len(fub.get("matches") or [])
    if fub.get("error"):
        note = fub["error"]
    return total, note


def _match_lead_tags(lead, criteria, count, oh):
    tags = ["Buyer Inquiry", "Match Tool"]
    if oh:
        tags += ["Open House", f"Open House: {oh}"[:100]]
    if lead.get("represented"):
        tags.append("Buyer - Agent Represented")
        if lead.get("agent_name"):
            tags.append(f"Buyer's Agent: {lead['agent_name']}"[:100])
    else:
        tags.append("Buyer - Unrepresented")
    for a in (criteria.get("areas") or [])[:6]:
        tags.append(f"Buyer Area: {a}"[:100])
    band = _budget_band(criteria.get("price_min"), criteria.get("price_max"))
    if band:
        tags.append(f"Buyer Budget: {band}")
    if count > 0:
        tags.append("Has Inventory Match")
    return tags


def _match_person(lead):
    person = {}
    if lead.get("name"):
        parts = lead["name"].split()
        person["firstName"] = parts[0]
        if len(parts) > 1:
            person["lastName"] = " ".join(parts[1:])
    if lead.get("email"):
        person["emails"] = [{"value": lead["email"]}]
    if lead.get("phone"):
        person["phones"] = [{"value": lead["phone"]}]
    return person


def push_match_lead_to_fub(lead, criteria, count, oh):
    """Create/merge the buyer as a FollowUpBoss contact. -> note string."""
    if not os.environ.get("FUB_API_KEY"):
        return "saved (FollowUpBoss not configured)"
    summary = _criteria_sentence(criteria)
    rep = (f"Exclusively represented by {lead.get('agent_name') or 'a buyer’s agent'}"
           if lead.get("represented") else "Not exclusively represented by a buyer's agent")
    bg = [
        f"Buyer search via marzullore.com/match{f' (Open House: {oh})' if oh else ''}.",
        f"Looking for: {summary}",
        f"Matches in our network: {count}",
        rep,
    ]
    person = _match_person(lead)
    person["tags"] = _match_lead_tags(lead, criteria, count, oh)
    person["background"] = "\n".join(bg)
    payload = {
        "source": os.environ.get("FUB_SOURCE", "Simone Marzullo Website"),
        "system": os.environ.get("FUB_SYSTEM", "Simone Marzullo Website"),
        "type": "Property Inquiry",
        "message": f"New buyer search (/match): {summary} — {count} match(es) in our network. {rep}.",
        "person": person,
    }
    _status, body, err = _fub_request("POST", FUB_EVENTS_URL, payload)
    return "sent to FollowUpBoss" if body is not None else f"FollowUpBoss push failed ({err})"


def push_match_message_to_fub(lead, message):
    """A follow-up 'message Simone' note from the same buyer -- keyed by email
    so FollowUpBoss attaches it to the contact created above."""
    if not os.environ.get("FUB_API_KEY") or not message:
        return False
    person = _match_person(lead)
    person["tags"] = ["Buyer Inquiry", "Match Tool", "Sent a Message"]
    payload = {
        "source": os.environ.get("FUB_SOURCE", "Simone Marzullo Website"),
        "system": os.environ.get("FUB_SYSTEM", "Simone Marzullo Website"),
        "type": "General Inquiry",
        "message": f"Message from buyer search (/match): {message}",
        "person": person,
    }
    _status, body, _err = _fub_request("POST", FUB_EVENTS_URL, payload)
    return body is not None


def build_match_page_html(oh=""):
    oh_tag = (f'<span class="area-tag" style="margin-top:14px;display:inline-block">Open House · {html.escape(oh)}</span>'
              if oh else "")
    body = f"""
<section class="area-hero" style="min-height:60vh">
  <img class="area-hero-img" src="/assets/hero-skyline-day.jpg" alt="Los Angeles skyline at dusk" loading="eager" style="object-position:50% 55%">
  <div class="area-hero-scrim"></div>
  <div class="area-hero-content">
    <div class="area-eyebrow"><span class="area-eyebrow-line"></span><span class="area-eyebrow-text">Private Buyer Search</span></div>
    <h1 class="area-h1">Tell us what you're looking for</h1>
    <p class="area-tagline">Share your criteria and we'll tell you, on the spot, how many homes in our network match — including properties never listed publicly.</p>
    {oh_tag}
  </div>
</section>

<section class="section">
  <div class="wrap mt-wrap">
    {MATCH_PAGE_CSS}
    <form id="mt-form" class="om-form" novalidate>
      <input id="mt-hp" type="text" name="website" tabindex="-1" autocomplete="off" aria-hidden="true">
      <input type="hidden" name="oh" value="{html.escape(oh)}">
      <div class="mt-row">
        <label class="om-field"><span class="om-field-label">Full name</span>
          <input type="text" name="name" class="om-input" required autocomplete="name"></label>
        <label class="om-field"><span class="om-field-label">Email</span>
          <input type="email" name="email" class="om-input" required autocomplete="email"></label>
        <label class="om-field"><span class="om-field-label">Phone</span>
          <input type="tel" name="phone" class="om-input" required autocomplete="tel"></label>
      </div>

      <div class="mt-rep">
        <div class="mt-rep-q">Are you exclusively represented by a buyer's agent?</div>
        <label><input type="radio" name="represented" value="no" checked> No</label>
        <label><input type="radio" name="represented" value="yes"> Yes</label>
        <label class="om-field" id="mt-agent" hidden><span class="om-field-label">Your agent's name</span>
          <input type="text" name="agent_name" class="om-input"></label>
      </div>

      <label class="om-field"><span class="om-field-label">Areas — markets or ZIP codes (comma-separated)</span>
        <input type="text" name="areas" class="om-input" list="match-market-list" autocomplete="off"
               placeholder="e.g. Santa Monica, 90291, Beverly Hills">
        <span class="mt-note">Pick a market and it covers every ZIP inside it. Add specific ZIPs if you have them.</span>
      </label>

      <div class="mt-row">
        <label class="om-field"><span class="om-field-label">Price min</span>
          <input type="text" name="price_min" class="om-input" placeholder="e.g. 1.5M"></label>
        <label class="om-field"><span class="om-field-label">Price max</span>
          <input type="text" name="price_max" class="om-input" placeholder="e.g. 3M"></label>
      </div>
      <div class="mt-row">
        <label class="om-field"><span class="om-field-label">Min bedrooms</span>
          <input type="text" name="beds" class="om-input" placeholder="3"></label>
        <label class="om-field"><span class="om-field-label">Min sq ft</span>
          <input type="text" name="sqft" class="om-input" placeholder="1800"></label>
        <label class="om-field"><span class="om-field-label">Max sq ft</span>
          <input type="text" name="sqft_max" class="om-input" placeholder="3500"></label>
      </div>

      <label class="om-field"><span class="om-field-label">Property type — optional</span>
        <input type="text" name="types" class="om-input" list="match-type-list" autocomplete="off"
               placeholder="Single Family Home, Condo/Townhome, Multifamily"></label>

      <button type="submit" class="btn-primary" style="margin-top:20px">See how many match</button>
    </form>

    <div id="mt-error" class="om-error"></div>
    <div id="mt-result" class="mt-result" hidden></div>

    <datalist id="match-market-list">{_market_datalist_options()}</datalist>
    <datalist id="match-type-list">{_prop_type_datalist_options()}</datalist>
  </div>
</section>
<script>{MATCH_PAGE_SCRIPT}</script>
"""
    return render_page(body, "Buyer Search | Simone Marzullo")


def fetch_all_buyer_needs(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT id, buyer_name, buyer_email, buyer_phone, buyer_source, agent_name,
                      agent_brokerage, agent_contact, criteria, fub_person_id,
                      last_match_count, last_matched_at, active, created_at
               FROM buyer_needs ORDER BY active DESC, created_at DESC"""
        )
        return cur.fetchall()


def create_buyer_need(conn, need, criteria, fub_person_id, match_count):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO buyer_needs
               (buyer_name, buyer_email, buyer_phone, buyer_source, agent_name,
                agent_brokerage, agent_contact, criteria, fub_person_id,
                last_match_count, last_matched_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now()) RETURNING id""",
            (need["buyer_name"], need["buyer_email"], need["buyer_phone"], need["buyer_source"],
             need["agent_name"], need["agent_brokerage"], need["agent_contact"],
             psycopg2.extras.Json(criteria), fub_person_id, match_count),
        )
        new_id = cur.fetchone()[0]
    conn.commit()
    return new_id


def update_buyer_need_match(conn, need_id, match_count):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE buyer_needs SET last_match_count = %s, last_matched_at = now(), updated_at = now() WHERE id = %s",
            (match_count, need_id),
        )
    conn.commit()


def toggle_buyer_need_active(conn, need_id):
    with conn.cursor() as cur:
        cur.execute("UPDATE buyer_needs SET active = NOT active, updated_at = now() WHERE id = %s", (need_id,))
    conn.commit()


def parse_buyer_criteria(data):
    """Normalize the raw Buyer Match form fields into the criteria dict stored
    in buyer_needs.criteria and consumed by run_buyer_match / score_buyer_match."""
    def numf(key):
        return _num(data.get(key)) if str(data.get(key) or "").strip() else None

    def listf(key):
        return [clean(x, 60) for x in re.split(r"[,\n;]+", str(data.get(key) or "")) if clean(x, 60)][:20]

    return {
        "price_min": numf("price_min"),
        "price_max": numf("price_max"),
        "beds": numf("beds"),
        "baths": numf("baths"),
        "sqft": numf("sqft"),          # min
        "sqft_max": numf("sqft_max"),
        "areas": listf("areas"),
        "types": listf("types"),
        "timeline": clean(data.get("timeline"), 120),
        "notes": clean(data.get("notes"), 1000),
    }


# ---------------------------------------------------------------------------
# Phase 2 -- backfill blank property fields on FollowUpBoss contacts from the
# LA County Assessor's public parcel data. Opt-in, batched (a full sweep
# can't finish in one request), and only ever *fills* a blank field -- it
# never overwrites data already in FollowUpBoss.
# ---------------------------------------------------------------------------
def _http_get_json(url, params, timeout=8):
    """Plain GET -> parsed JSON, or None. For the keyless LA County service."""
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as e:
        print(f"portal(enrich): GET {url} failed: {e}")
        return None


def _split_street_address(raw):
    """'123 N Grand View Blvd Apt 4' -> ('123 N GRAND VIEW BLVD', house_no='123').
    Returns (normalized_street, house_no) or (None, None) if no leading number."""
    s = re.sub(r"\s+", " ", str(raw or "").strip().upper())
    s = _UNIT_RE.sub("", s).strip().rstrip(",")
    m = re.match(r"^(\d+)\s+(.*)$", s)
    if not m:
        return (None, None)
    return (f"{m.group(1)} {m.group(2)}".strip(), m.group(1))


def _arcgis_esc(v):
    return str(v).replace("'", "''")


def _coalesce_building_attrs(attrs):
    """Sum/rollup the 1..5 building columns LA County uses for multi-structure parcels."""
    def nums(prefix):
        out = []
        for i in range(1, 6):
            v = _num(attrs.get(f"{prefix}{i}"))
            if v:
                out.append(v)
        return out

    beds = nums("Bedrooms")
    baths = nums("Bathrooms")
    sqft = nums("SQFTmain")
    years = nums("YearBuilt")
    units = max(nums("Units") or [0])
    land = _num(attrs.get("Roll_LandValue")) or 0
    imp = _num(attrs.get("Roll_ImpValue")) or 0
    return {
        "beds": sum(beds) if beds else None,
        "baths": sum(baths) if baths else None,
        "sqft": sum(sqft) if sqft else None,
        "year_built": max(years) if years else None,
        "property_type": norm_property_type(attrs.get("UseCode"), attrs.get("UseDescription"), units),
        "assessed_value": (land + imp) or None,
        "matched_address": _clean_str(attrs.get("SitusFullAddress") or attrs.get("SitusAddress")),
    }


_LACOUNTY_OUT_FIELDS = (
    "AIN,SitusHouseNo,SitusStreet,SitusAddress,SitusFullAddress,SitusCity,SitusZIP,"
    "UseCode,UseType,UseDescription,Units1,Units2,Units3,Units4,Units5,"
    "YearBuilt1,YearBuilt2,YearBuilt3,YearBuilt4,YearBuilt5,"
    "Bedrooms1,Bedrooms2,Bedrooms3,Bedrooms4,Bedrooms5,"
    "Bathrooms1,Bathrooms2,Bathrooms3,Bathrooms4,Bathrooms5,"
    "SQFTmain1,SQFTmain2,SQFTmain3,SQFTmain4,SQFTmain5,"
    "Roll_LandValue,Roll_ImpValue"
)


def _lacounty_query(where):
    """Run one where-clause against the parcel layer. -> features list, or None
    on transport/query error (distinct from an empty [] = 'no such parcel')."""
    body = _http_get_json(LACOUNTY_PARCEL_URL, {
        "where": where, "outFields": _LACOUNTY_OUT_FIELDS,
        "returnGeometry": "false", "resultRecordCount": 8, "f": "json",
    })
    if body is None:
        return None
    if body.get("error"):
        print(f"portal(enrich): LA County query error for [{where}]: {body['error']}")
        return None
    return body.get("features") or []


def lacounty_lookup(street, city, zip_code):
    """Look one property up in the LA County parcel layer. -> attrs dict or None."""
    norm, house = _split_street_address(street)
    if not norm or not house:
        return None
    rest = _LEADING_DIR_RE.sub("", norm[len(house):].strip())
    core = re.sub(r"\s+", " ", _STREET_SUFFIX_RE.sub("", rest)).strip().rstrip(".").strip() or rest
    z = re.sub(r"\D", "", str(zip_code or ""))[:5]
    loc = ""
    if z:
        loc = f" AND SitusZIP LIKE '{z}%'"          # SitusZIP is ZIP+4, so prefix-match
    elif city:
        loc = f" AND UPPER(SitusCity) LIKE '{_arcgis_esc(str(city).strip().upper())}%'"  # SitusCity is "CITY ST"

    he = _arcgis_esc(house)
    # 1) exact house number + loose street-name match (best precision)
    feats = _lacounty_query(f"SitusHouseNo = '{he}' AND UPPER(SitusStreet) LIKE '%{_arcgis_esc(core.upper())}%'{loc}")
    if not feats:
        # 2) prefix match on the full situs address -- anchored so "905 X" never
        #    matches "1905 X" the way a leading-wildcard LIKE would
        feats = _lacounty_query(f"UPPER(SitusAddress) LIKE '{_arcgis_esc(norm)}%'{loc}")
    if not feats:
        return None
    best = feats[0]
    if z:
        best = next((f for f in feats if str(f.get("attributes", {}).get("SitusZIP", "")).startswith(z)), feats[0])
    return _coalesce_building_attrs(best.get("attributes", {}))


def fub_fill_blank_person_fields(person, fieldmap, found):
    """PUT only the standard custom fields that are currently blank on `person`.
    -> (updated_field_labels list, error_or_None). Never overwrites."""
    src = {
        "Bedrooms": found.get("beds"),
        "Bathrooms": found.get("baths"),
        "SqFt": found.get("sqft"),
        "YearBuilt": found.get("year_built"),
        "PropertyType": found.get("property_type"),
        "AskingPrice": None,  # never write a value guess into AskingPrice
    }
    payload = {}
    filled = []
    for label, value in src.items():
        if value in (None, "", 0):
            continue
        name = fieldmap.get(label)
        if not name:
            continue
        current = str(person.get(name) or "").strip()
        stale = label == "PropertyType" and current.lower() in PROP_TYPE_STALE
        if current not in ("", "0") and not stale:
            continue  # already has real data -- leave it
        payload[name] = value
        filled.append(label)
    if not payload:
        return ([], None)
    existing_tags = person.get("tags") or []
    if "Enriched: LA County Assessor" not in existing_tags:
        payload["tags"] = existing_tags + ["Enriched: LA County Assessor"]
    _status, body, err = _fub_request("PUT", f"{FUB_API_BASE}/people/{person['id']}", payload)
    if body is None:
        return ([], err or "update failed")
    return (filled, None)


def fetch_enrich_state(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM fub_enrich_state WHERE id = 1")
        row = cur.fetchone()
        if not row:
            cur.execute("INSERT INTO fub_enrich_state (id) VALUES (1) RETURNING *")
            row = cur.fetchone()
            conn.commit()
        return row


def save_enrich_state(conn, next_offset, seen_inc, updated_inc, nomatch_inc, wrapped,
                      db_total=None, next_link=None):
    # next_link is set explicitly every call (None clears it -> next run starts
    # a fresh pass), so it is NOT COALESCE'd the way db_total is.
    with conn.cursor() as cur:
        if wrapped:
            cur.execute(
                """UPDATE fub_enrich_state
                   SET next_offset = 0, next_link = NULL, last_run_at = now(), passes = passes + 1,
                       total_seen = total_seen + %s, total_updated = total_updated + %s,
                       total_no_match = total_no_match + %s,
                       db_total = COALESCE(%s, db_total)
                   WHERE id = 1""",
                (seen_inc, updated_inc, nomatch_inc, db_total),
            )
        else:
            cur.execute(
                """UPDATE fub_enrich_state
                   SET next_offset = %s, next_link = %s, last_run_at = now(),
                       total_seen = total_seen + %s, total_updated = total_updated + %s,
                       total_no_match = total_no_match + %s,
                       db_total = COALESCE(%s, db_total)
                   WHERE id = 1""",
                (next_offset, next_link, seen_inc, updated_inc, nomatch_inc, db_total),
            )
    conn.commit()


def reset_enrich_state(conn):
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE fub_enrich_state
               SET next_offset = 0, next_link = NULL, passes = 0, total_seen = 0, total_updated = 0,
                   total_no_match = 0, last_run_at = NULL, db_total = NULL WHERE id = 1"""
        )
    conn.commit()


def fetch_admin_counts(conn):
    """Headline numbers for the Overview dashboard. Best-effort -- a missing
    table (older DB) returns 0 for that row rather than failing the page."""
    out = {"sellers_active": 0, "sellers_archived": 0, "listings_onmkt": 0,
           "om_buyers": 0, "om_listings": 0, "om_available": 0, "buyer_needs": 0}
    queries = {
        "sellers_active": "SELECT count(*) FROM clients WHERE active",
        "sellers_archived": "SELECT count(*) FROM clients WHERE NOT active",
        "listings_onmkt": "SELECT count(*) FROM listings WHERE active AND status = 'Active'",
        "om_buyers": "SELECT count(*) FROM offmarket_buyers WHERE active",
        "om_listings": "SELECT count(*) FROM offmarket_listings WHERE active",
        "om_available": "SELECT count(*) FROM offmarket_listings WHERE active AND status = 'Available'",
        "buyer_needs": "SELECT count(*) FROM buyer_needs WHERE active",
    }
    for key, q in queries.items():
        try:
            with conn.cursor() as cur:
                cur.execute(q)
                out[key] = cur.fetchone()[0]
        except Exception:
            conn.rollback()
    return out


def fetch_admin_activity(conn, limit=6):
    """A small merged 'recent activity' feed for the Overview screen, built
    from created_at timestamps across the main tables + the last enrich run."""
    rows = []
    picks = [
        ("seller", "SELECT name, created_at FROM clients ORDER BY created_at DESC LIMIT 4",
         lambda r: f"New seller account — {r[0] or 'unnamed'}"),
        ("listing", "SELECT address, created_at FROM offmarket_listings ORDER BY created_at DESC LIMIT 4",
         lambda r: f"Off-market listing added — {r[0]}"),
        ("buyer", "SELECT buyer_name, last_match_count, created_at FROM buyer_needs ORDER BY created_at DESC LIMIT 4",
         lambda r: f"Buyer need — {r[0] or 'unnamed'} → {r[1]} prospect(s)"),
    ]
    for kind, q, fmt in picks:
        try:
            with conn.cursor() as cur:
                cur.execute(q)
                for r in cur.fetchall():
                    ts = r[-1]
                    rows.append({"kind": kind, "text": fmt(r), "ts": ts})
        except Exception:
            conn.rollback()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT last_run_at, total_updated, total_seen FROM fub_enrich_state WHERE id = 1")
            r = cur.fetchone()
            if r and r[0]:
                rows.append({"kind": "enrich", "ts": r[0],
                             "text": f"Enrichment run — {r[1]} filled / {r[2]} scanned"})
    except Exception:
        conn.rollback()
    rows = [r for r in rows if r.get("ts")]
    rows.sort(key=lambda r: r["ts"], reverse=True)
    return rows[:limit]


def _fub_people_first_page_url(batch_size):
    stage = os.environ.get("FUB_ENRICH_STAGE", "").strip()
    params = {"limit": batch_size, "fields": "allFields,allCustom", "includeTrash": "false"}
    if stage:
        params["stage"] = stage
    return f"{FUB_API_BASE}/people?{urllib.parse.urlencode(params)}"


def run_enrich_batch(conn, batch_size):
    """Process one page of FollowUpBoss contacts: for each with a street address
    but a blank bed/bath/sqft/year/type, look it up in LA County and fill the
    gaps. Returns a summary dict; the caller loops until done=True.

    FollowUpBoss disables offset paging past ~2000 rows, so pagination follows
    the _metadata.nextLink cursor URL (persisted in fub_enrich_state.next_link).
    next_offset is kept only as a human 'resume at #N' counter."""
    state = fetch_enrich_state(conn)
    counted = state.get("next_offset") or 0
    nl = state.get("next_link") or ""
    url = nl if nl.startswith(f"{FUB_API_BASE}/people") else _fub_people_first_page_url(batch_size)

    _status, body, err = _fub_request("GET", url)
    if not body:
        if err and "400" in err and nl:
            # A stale / rejected cursor -- start the pass over rather than wedging.
            save_enrich_state(conn, 0, 0, 0, 0, True)
            return {"ok": True, "processed": 0, "updated": 0, "no_match": 0, "no_address": 0,
                    "offset": counted, "next_offset": 0, "total": state.get("db_total"),
                    "done": True, "examples": [],
                    "notice": "FollowUpBoss rejected the saved page cursor — restarted from the top."}
        return {"ok": False, "error": err or "FollowUpBoss request failed."}
    people = body.get("people") or []
    meta = body.get("_metadata") or {}
    total = meta.get("total")
    next_link = meta.get("nextLink") or ""
    fieldmap = _fub_fieldmap()

    # Wall-clock guard so a slow page can't trip the 60s function limit. If we
    # do run out of time mid-page we keep the SAME cursor (don't advance to
    # next_link) so the rest of the page is retried on the next click.
    deadline = time.time() + 42
    ran_out = False
    updated, no_match, no_address, filled_examples = 0, 0, 0, []
    processed = 0
    for p in people:
        if processed and time.time() > deadline:
            ran_out = True
            break
        processed += 1
        prof = fub_prop_profile(p, fieldmap)
        if (prof["beds"] and prof["baths"] and prof["sqft"] and prof["year_built"]
                and str(prof["property_type"] or "").lower() not in PROP_TYPE_STALE):
            continue  # already complete
        addrs = p.get("addresses") or []
        a0 = addrs[0] if isinstance(addrs, list) and addrs and isinstance(addrs[0], dict) else {}
        street = a0.get("street") or ""
        if not _split_street_address(street)[0]:
            no_address += 1
            continue
        found = lacounty_lookup(street, a0.get("city"), a0.get("code"))
        if not found:
            no_match += 1
            continue
        filled, _e = fub_fill_blank_person_fields(p, fieldmap, found)
        if filled:
            updated += 1
            if len(filled_examples) < 5:
                filled_examples.append({"name": _clean_str(p.get("name")) or f"#{p.get('id')}",
                                        "filled": filled, "matched": found.get("matched_address")})

    page_len = len(people)
    finished_page = processed >= page_len
    new_count = counted + processed
    # End of the list = we finished the page and FollowUpBoss gave no nextLink.
    done = finished_page and not next_link
    if not finished_page:
        # ran out of time mid-page -- keep the cursor we came in on, retry it
        cursor_to_save = nl if nl.startswith(f"{FUB_API_BASE}/people") else _fub_people_first_page_url(batch_size)
    elif done:
        cursor_to_save = None
    else:
        cursor_to_save = next_link
    save_enrich_state(conn, 0 if done else new_count, processed, updated, no_match, done,
                      db_total=total if isinstance(total, int) else None,
                      next_link=cursor_to_save)
    return {
        "ok": True, "processed": processed, "updated": updated, "no_match": no_match,
        "no_address": no_address, "offset": counted, "next_offset": 0 if done else new_count,
        "total": total, "done": done, "examples": filled_examples,
        "timed_out": ran_out,
    }


def fetch_client_by_email(conn, email):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, email, password_hash, name, active FROM clients WHERE email = %s", (email,))
        return cur.fetchone()


def fetch_dashboard_data(conn, client_id):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, name FROM clients WHERE id = %s AND active = TRUE", (client_id,))
        client = cur.fetchone()
        if not client:
            return None

        cur.execute(
            """SELECT id, address, status, agents_reached_count
               FROM listings WHERE client_id = %s AND active = TRUE ORDER BY created_at DESC""",
            (client_id,),
        )
        listings = cur.fetchall()

        for listing in listings:
            cur.execute(
                """SELECT price, financing_type, close_of_escrow, created_at
                   FROM offers WHERE listing_id = %s AND active = TRUE ORDER BY created_at ASC""",
                (listing["id"],),
            )
            listing["offers"] = cur.fetchall()

            cur.execute(
                "SELECT event_date, groups_count, notes FROM open_houses WHERE listing_id = %s AND active = TRUE ORDER BY event_date DESC, created_at DESC",
                (listing["id"],),
            )
            listing["open_houses"] = cur.fetchall()

            cur.execute(
                "SELECT category, note, created_at FROM feedback_notes WHERE listing_id = %s ORDER BY created_at DESC",
                (listing["id"],),
            )
            listing["feedback"] = cur.fetchall()

        return {"client": client, "listings": listings}


def fetch_all_clients(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, email, name, active, created_at FROM clients ORDER BY created_at DESC")
        clients = cur.fetchall()

        for client in clients:
            cur.execute(
                """SELECT id, address, status, active, agents_reached_count
                   FROM listings WHERE client_id = %s ORDER BY created_at DESC""",
                (client["id"],),
            )
            listings = cur.fetchall()
            for listing in listings:
                cur.execute(
                    "SELECT id, category, note, created_at FROM feedback_notes WHERE listing_id = %s ORDER BY created_at DESC",
                    (listing["id"],),
                )
                listing["feedback"] = cur.fetchall()

                cur.execute(
                    "SELECT id, price, financing_type, close_of_escrow, active, created_at FROM offers WHERE listing_id = %s ORDER BY created_at ASC",
                    (listing["id"],),
                )
                listing["offers"] = cur.fetchall()

                cur.execute(
                    "SELECT id, event_date, groups_count, notes, active FROM open_houses WHERE listing_id = %s ORDER BY event_date DESC, created_at DESC",
                    (listing["id"],),
                )
                listing["open_houses"] = cur.fetchall()
            client["listings"] = listings
        return clients


def create_client(conn, email, name, password):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO clients (email, name, password_hash) VALUES (%s, %s, %s) RETURNING id",
            (email, name, hash_password(password)),
        )
        client_id = cur.fetchone()[0]
    conn.commit()
    return client_id


def toggle_client_active(conn, client_id):
    with conn.cursor() as cur:
        cur.execute("UPDATE clients SET active = NOT active WHERE id = %s", (client_id,))
    conn.commit()


def reset_client_password(conn, client_id, password):
    with conn.cursor() as cur:
        cur.execute("UPDATE clients SET password_hash = %s WHERE id = %s", (hash_password(password), client_id))
    conn.commit()


def update_client_email(conn, client_id, email):
    with conn.cursor() as cur:
        cur.execute("UPDATE clients SET email = %s WHERE id = %s", (email, client_id))
    conn.commit()


def create_listing(conn, client_id, address):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO listings (client_id, address) VALUES (%s, %s)", (client_id, address))
    conn.commit()


def update_listing_status(conn, listing_id, status):
    with conn.cursor() as cur:
        cur.execute("UPDATE listings SET status = %s, updated_at = now() WHERE id = %s", (status, listing_id))
    conn.commit()


def update_listing_address(conn, listing_id, address):
    with conn.cursor() as cur:
        cur.execute("UPDATE listings SET address = %s, updated_at = now() WHERE id = %s", (address, listing_id))
    conn.commit()


def toggle_listing_active(conn, listing_id):
    with conn.cursor() as cur:
        cur.execute("UPDATE listings SET active = NOT active, updated_at = now() WHERE id = %s", (listing_id,))
    conn.commit()


def update_marketing(conn, listing_id, agents_reached):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE listings SET agents_reached_count = %s, updated_at = now() WHERE id = %s",
            (agents_reached, listing_id),
        )
    conn.commit()


def create_open_house(conn, listing_id, event_date, groups_count, notes):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO open_houses (listing_id, event_date, groups_count, notes) VALUES (%s, %s, %s, %s)",
            (listing_id, event_date, groups_count, notes),
        )
    conn.commit()


def toggle_open_house_active(conn, open_house_id):
    with conn.cursor() as cur:
        cur.execute("UPDATE open_houses SET active = NOT active WHERE id = %s", (open_house_id,))
    conn.commit()


def add_feedback(conn, listing_id, category, note):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO feedback_notes (listing_id, category, note) VALUES (%s, %s, %s)",
            (listing_id, category, note),
        )
    conn.commit()


def update_feedback(conn, feedback_id, category, note):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE feedback_notes SET category = %s, note = %s WHERE id = %s",
            (category, note, feedback_id),
        )
    conn.commit()


def delete_feedback(conn, feedback_id):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM feedback_notes WHERE id = %s", (feedback_id,))
    conn.commit()


def add_offer(conn, listing_id, price, financing_type, close_of_escrow):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO offers (listing_id, price, financing_type, close_of_escrow)
               VALUES (%s, %s, %s, %s)""",
            (listing_id, price, financing_type, close_of_escrow),
        )
    conn.commit()


def toggle_offer_active(conn, offer_id):
    with conn.cursor() as cur:
        cur.execute("UPDATE offers SET active = NOT active WHERE id = %s", (offer_id,))
    conn.commit()


def fetch_all_toolbox_links(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, name, url, sort_order, active FROM toolbox_links ORDER BY sort_order, created_at")
        return cur.fetchall()


def create_toolbox_link(conn, name, url, sort_order):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO toolbox_links (name, url, sort_order) VALUES (%s, %s, %s)",
            (name, url, sort_order),
        )
    conn.commit()


def update_toolbox_link(conn, link_id, name, url, sort_order):
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE toolbox_links SET name = %s, url = %s, sort_order = %s WHERE id = %s",
            (name, url, sort_order, link_id),
        )
    conn.commit()


def toggle_toolbox_link_active(conn, link_id):
    with conn.cursor() as cur:
        cur.execute("UPDATE toolbox_links SET active = NOT active WHERE id = %s", (link_id,))
    conn.commit()


def delete_toolbox_link(conn, link_id):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM toolbox_links WHERE id = %s", (link_id,))
    conn.commit()


# ---------------------------------------------------------------------------
# Off-market buyers + listings -- feeds api/offmarket.py's /off-market page
# and its public per-listing /flyer/<id> page. Same auth pattern as sellers
# (hash_password/verify_password above), but buyers don't have their own
# listings nested under them -- every active buyer sees every active
# offmarket_listings row, there's no per-buyer assignment.
# ---------------------------------------------------------------------------
def fetch_all_offmarket_buyers(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, email, name, active, created_at FROM offmarket_buyers ORDER BY created_at DESC")
        return cur.fetchall()


def create_offmarket_buyer(conn, email, name, password):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO offmarket_buyers (email, name, password_hash) VALUES (%s, %s, %s) RETURNING id",
            (email, name, hash_password(password)),
        )
        buyer_id = cur.fetchone()[0]
    conn.commit()
    return buyer_id


def toggle_offmarket_buyer_active(conn, buyer_id):
    with conn.cursor() as cur:
        cur.execute("UPDATE offmarket_buyers SET active = NOT active WHERE id = %s", (buyer_id,))
    conn.commit()


def reset_offmarket_buyer_password(conn, buyer_id, password):
    with conn.cursor() as cur:
        cur.execute("UPDATE offmarket_buyers SET password_hash = %s WHERE id = %s", (hash_password(password), buyer_id))
    conn.commit()


def update_offmarket_buyer_email(conn, buyer_id, email):
    with conn.cursor() as cur:
        cur.execute("UPDATE offmarket_buyers SET email = %s WHERE id = %s", (email, buyer_id))
    conn.commit()


def fetch_all_offmarket_listings(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT id, address, area, status, price, beds, baths, sqft, lot_size, description,
                      photo_urls, photo_alt, hide_address, media_link, hide_media_link, active, created_at
               FROM offmarket_listings ORDER BY created_at DESC"""
        )
        return cur.fetchall()


def create_offmarket_listing(conn, address, area, status, price, beds, baths, sqft, lot_size, description, photo_urls, photo_alt, hide_address, media_link, hide_media_link):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO offmarket_listings
               (address, area, status, price, beds, baths, sqft, lot_size, description, photo_urls, photo_alt, hide_address, media_link, hide_media_link)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (address, area, status, price, beds, baths, sqft, lot_size, description, photo_urls, photo_alt, hide_address, media_link, hide_media_link),
        )
    conn.commit()


def update_offmarket_listing(conn, listing_id, address, area, status, price, beds, baths, sqft, lot_size, description, photo_urls, photo_alt, hide_address, media_link, hide_media_link):
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE offmarket_listings SET address = %s, area = %s, status = %s, price = %s, beds = %s,
               baths = %s, sqft = %s, lot_size = %s, description = %s, photo_urls = %s, photo_alt = %s,
               hide_address = %s, media_link = %s, hide_media_link = %s, updated_at = now()
               WHERE id = %s""",
            (address, area, status, price, beds, baths, sqft, lot_size, description, photo_urls, photo_alt, hide_address, media_link, hide_media_link, listing_id),
        )
    conn.commit()


def toggle_offmarket_listing_active(conn, listing_id):
    with conn.cursor() as cur:
        cur.execute("UPDATE offmarket_listings SET active = NOT active WHERE id = %s", (listing_id,))
    conn.commit()


def delete_offmarket_listing(conn, listing_id):
    with conn.cursor() as cur:
        cur.execute("DELETE FROM offmarket_listings WHERE id = %s", (listing_id,))
    conn.commit()


def build_error_html(message, title):
    body = f"""
<section class="section" style="text-align:center;padding-top:140px">
  <div class="wrap"><p class="om-empty">{html.escape(message)}</p></div>
</section>
"""
    return render_page(body, title)


# ---------------------------------------------------------------------------
# Client dashboard rendering
# ---------------------------------------------------------------------------
def build_client_login_html(error=None):
    error_html = f'<div class="om-error" style="display:block">{html.escape(error)}</div>' if error else '<div class="om-error" id="dash-error"></div>'
    body = f"""
<section class="area-hero dash-hero">
  <img class="area-hero-img" src="/assets/hero-skyline-day.jpg" alt="Los Angeles skyline at dusk" loading="eager" style="object-position:50% 50%">
  <div class="area-hero-scrim"></div>
  <div class="area-hero-content">
    <div class="area-eyebrow"><span class="area-eyebrow-line"></span><span class="area-eyebrow-text">Client Access</span></div>
    <h1 class="area-h1">Your Listing Dashboard</h1>
    <p class="area-tagline">Sign in with the email and password Simone gave you to check on your listing's progress.</p>
  </div>
</section>

<section class="section" style="text-align:center">
  <div class="wrap" style="max-width:440px">
    <form id="dash-form" novalidate>
      <div class="om-form">
        <label class="om-field">
          <span class="om-field-label">Email</span>
          <input type="email" id="dash-email" class="om-input" required autocomplete="username">
        </label>
        <label class="om-field">
          <span class="om-field-label">Password</span>
          <input type="password" id="dash-password" class="om-input" required autocomplete="current-password">
        </label>
      </div>
      {error_html}
      <button type="submit" class="btn-primary" id="dash-submit" style="width:100%;justify-content:center;margin-top:22px">Sign In</button>
    </form>
    <p style="font-size:.75rem;color:var(--g5);margin-top:20px;line-height:1.7">Don't have login details? <a href="/contact" style="color:var(--white);text-decoration:underline">Contact Simone</a>.</p>
    <p style="margin-top:28px"><a href="/admin" class="om-logout">Admin</a></p>
  </div>
</section>

<script>
document.getElementById('dash-form').addEventListener('submit', async function (e) {{
  e.preventDefault();
  const email = document.getElementById('dash-email').value.trim();
  const password = document.getElementById('dash-password').value;
  const errEl = document.getElementById('dash-error');
  const btn = document.getElementById('dash-submit');
  errEl.style.display = 'none';
  btn.textContent = 'Signing in…';
  try {{
    const res = await fetch('/api/portal?section=dashboard', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{action: 'login', email, password}}),
    }});
    const data = await res.json();
    if (res.ok && data.ok) {{
      window.location.href = '/clientaccess';
    }} else {{
      errEl.textContent = data.error || 'Something went wrong. Please try again.';
      errEl.style.display = 'block';
      btn.textContent = 'Sign In';
    }}
  }} catch (err) {{
    errEl.textContent = 'Network error. Please try again.';
    errEl.style.display = 'block';
    btn.textContent = 'Sign In';
  }}
}});
</script>
"""
    return render_page(body, "Client Dashboard | Simone Marzullo")


def _stat_tile(label, value):
    return f"""<div class="db-stat">
      <div class="db-stat-value">{value}</div>
      <div class="db-stat-label">{html.escape(label)}</div>
    </div>"""


def _offer_html(offer, number):
    price = f"${offer['price']:,.0f}"
    financing_label = "Cash" if offer["financing_type"] == "cash" else "Loan"
    close_label = offer["close_of_escrow"].strftime("%b %-d, %Y") if offer["close_of_escrow"] else "Not specified"
    return f"""<div class="db-offer-row">
      <div class="db-offer-main">
        <span class="db-offer-number">Offer #{number}</span>
        <span class="db-offer-price">{price}</span>
        <span class="om-status">{financing_label}</span>
      </div>
      <div class="db-offer-meta">Close of escrow: {close_label}</div>
    </div>"""


def _open_house_html(oh):
    date_label = oh["event_date"].strftime("%b %-d, %Y") if oh["event_date"] else ""
    groups = oh["groups_count"] or 0
    groups_label = f"{groups} group{'s' if groups != 1 else ''} through"
    note_html = f'<div class="db-note-text">{html.escape(oh["notes"])}</div>' if oh.get("notes") else ""
    return f"""<div class="db-note">
      <div class="db-note-date">{date_label}<span class="db-note-sub">{groups_label}</span></div>
      {note_html}
    </div>"""


def _open_house_admin_html(oh):
    status_html = ' <span class="om-status">Canceled</span>' if not oh["active"] else ""
    toggle_label = "Restore" if not oh["active"] else "Cancel"
    return f"""<div class="adm-oh-entry">
      {_open_house_html(oh)}
      <div class="adm-list-row-actions" style="margin-left:0">{status_html}
        <button type="button" class="om-logout adm-toggle-active" data-action="toggle_open_house_active" data-id="{oh["id"]}">{toggle_label}</button>
      </div>
    </div>"""


def _feedback_html(notes, empty_message):
    if not notes:
        return f'<p class="db-empty-note">{html.escape(empty_message)}</p>'
    items = []
    for n in notes:
        sub = feedback_category_label(n["category"])
        items.append(f"""<div class="db-note">
          <div class="db-note-date">{n["created_at"].strftime("%b %-d, %Y")}<span class="db-note-sub">{html.escape(sub)}</span></div>
          <div class="db-note-text">{html.escape(n["note"])}</div>
        </div>""")
    return "".join(items)


def _listing_html(listing):
    groups_total = sum(oh["groups_count"] or 0 for oh in listing["open_houses"])
    marketing_stats = "".join([
        _stat_tile("Showings", len(listing["open_houses"])),
        _stat_tile("Open House Groups", groups_total),
        _stat_tile("Agents Reached", listing["agents_reached_count"]),
        _stat_tile("Offers Received", len(listing["offers"])),
    ])

    offers_html = "".join(_offer_html(o, i + 1) for i, o in enumerate(listing["offers"])) or '<p class="db-empty-note">No offers received yet.</p>'
    open_houses_html = "".join(_open_house_html(oh) for oh in listing["open_houses"]) or '<p class="db-empty-note">No open houses logged yet.</p>'
    feedback_html = _feedback_html(listing["feedback"], "No feedback logged yet.")

    return f"""
    <div class="db-listing" data-tabscope>
      <div class="db-listing-head">
        <div class="db-listing-address">{html.escape(listing["address"])}</div>
        <span class="om-status">{html.escape(listing["status"])}</span>
      </div>
      <div class="db-tabs">
        <button type="button" class="db-tab active" data-tab="marketing">Activity</button>
        <button type="button" class="db-tab" data-tab="offers">Number of Offers</button>
        <button type="button" class="db-tab" data-tab="openhouses">Open Houses &amp; Showings</button>
        <button type="button" class="db-tab" data-tab="feedback">Feedbacks</button>
      </div>
      <div class="db-tab-panel" data-tab-panel="marketing">
        <div class="db-stats">{marketing_stats}</div>
      </div>
      <div class="db-tab-panel" data-tab-panel="offers" style="display:none">
        {offers_html}
      </div>
      <div class="db-tab-panel" data-tab-panel="openhouses" style="display:none">
        {open_houses_html}
      </div>
      <div class="db-tab-panel" data-tab-panel="feedback" style="display:none">
        {feedback_html}
      </div>
    </div>"""


def build_client_dashboard_html(data):
    client = data["client"]
    listings = data["listings"]
    name_html = f", {html.escape(client['name'])}" if client.get("name") else ""

    if listings:
        listings_html = "".join(_listing_html(l) for l in listings)
    else:
        listings_html = '<div class="om-empty">No listings on your account yet -- check back once Simone has one set up.</div>'

    body = f"""
<section class="area-hero dash-hero">
  <img class="area-hero-img" src="/assets/hero-skyline-day.jpg" alt="Los Angeles skyline at dusk" loading="eager" style="object-position:50% 50%">
  <div class="area-hero-scrim"></div>
  <div class="area-hero-content">
    <div class="area-eyebrow"><span class="area-eyebrow-line"></span><span class="area-eyebrow-text">Client Access</span></div>
    <h1 class="area-h1">Welcome{name_html}</h1>
    <p class="area-tagline">Here's the latest on your listing.</p>
  </div>
</section>

<section class="section">
  <div class="wrap">
    {listings_html}
    <div style="text-align:center;margin-top:36px"><a href="/clientaccess?logout=1" class="om-logout">Log out</a></div>
  </div>
</section>
<script>{DB_TABS_SCRIPT}</script>
"""
    return render_page(body, "Client Dashboard | Simone Marzullo")


# ---------------------------------------------------------------------------
# Admin rendering
# ---------------------------------------------------------------------------
def build_admin_login_html(error=None):
    error_html = f'<div class="om-error" style="display:block">{html.escape(error)}</div>' if error else '<div class="om-error" id="adm-error"></div>'
    body = f"""
<section class="section" style="text-align:center;padding-top:140px">
  <div class="wrap" style="max-width:380px">
    <h1 class="adm-h1" style="margin-bottom:24px">Admin</h1>
    <form id="adm-login-form" novalidate>
      <div class="om-form">
        <label class="om-field">
          <span class="om-field-label">Password</span>
          <input type="password" id="adm-password" class="om-input" required autocomplete="current-password">
        </label>
      </div>
      {error_html}
      <button type="submit" class="btn-primary" id="adm-login-submit" style="width:100%;justify-content:center;margin-top:22px">Sign In</button>
    </form>
  </div>
</section>
<script>
document.getElementById('adm-login-form').addEventListener('submit', async function (e) {{
  e.preventDefault();
  const password = document.getElementById('adm-password').value;
  const errEl = document.getElementById('adm-error');
  errEl.style.display = 'none';
  try {{
    const res = await fetch('/api/portal?section=admin', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{action: 'login', password}}),
    }});
    const data = await res.json();
    if (res.ok && data.ok) {{
      window.location.href = '/admin';
    }} else {{
      errEl.textContent = data.error || 'Something went wrong.';
      errEl.style.display = 'block';
    }}
  }} catch (err) {{
    errEl.textContent = 'Network error. Please try again.';
    errEl.style.display = 'block';
  }}
}});
</script>
"""
    return render_page(body, "Admin | Simone Marzullo")


def _fmt_date(d):
    return d.strftime("%Y-%m-%d") if d else ""


def _status_options(current):
    return "".join(f'<option value="{s}"{" selected" if s == current else ""}>{s}</option>' for s in LISTING_STATUSES)




def _offer_admin_html(offer, number):
    price = f"${offer['price']:,.0f}"
    financing_label = "Cash" if offer["financing_type"] == "cash" else "Loan"
    close_label = offer["close_of_escrow"].strftime("%b %-d, %Y") if offer["close_of_escrow"] else "Not specified"
    canceled_label = " (Canceled)" if not offer["active"] else ""
    toggle_label = "Restore" if not offer["active"] else "Cancel"
    return f"""<details class="adm-offer">
      <summary>Offer #{number} &mdash; {price}{canceled_label}</summary>
      <div class="db-offer-meta">Financing: {financing_label} &middot; Close: {close_label}</div>
      <button type="button" class="om-logout adm-toggle-active" data-action="toggle_offer_active" data-id="{offer["id"]}" style="margin-top:10px">{toggle_label}</button>
    </details>"""


def _category_options(selected=None):
    return "".join(f'<option value="{k}"{" selected" if k == selected else ""}>{v}</option>' for k, v in FEEDBACK_CATEGORIES.items())


def _feedback_admin_html(f):
    return f"""
    <div class="adm-feedback-entry">
      <div class="db-note-date">{f["created_at"].strftime("%b %-d, %Y")}</div>
      <form class="adm-inline-form" data-action="update_feedback" data-id="{f["id"]}">
        <label class="om-field"><span class="om-field-label">Type</span>
          <select name="category" class="om-input">{_category_options(selected=f["category"])}</select>
        </label>
        <input type="text" name="note" class="om-input" value="{html.escape(f["note"])}" maxlength="2000" required>
        <button type="submit" class="btn-primary adm-btn-sm">Save</button>
        <button type="button" class="om-logout adm-delete-btn" data-action="delete_feedback" data-id="{f["id"]}">Delete</button>
      </form>
    </div>"""


def _listing_admin_html(listing):
    active_offers = [o for o in listing["offers"] if o["active"]]
    active_open_houses = [oh for oh in listing["open_houses"] if oh["active"]]
    offers_html = "".join(_offer_admin_html(o, i + 1) for i, o in enumerate(listing["offers"])) or '<p class="db-empty-note">No offers yet.</p>'
    open_houses_html = "".join(_open_house_admin_html(oh) for oh in listing["open_houses"]) or '<p class="db-empty-note">No open houses logged yet.</p>'
    groups_total = sum(oh["groups_count"] or 0 for oh in active_open_houses)
    summary_stats_html = "".join([
        _stat_tile("Showings", len(active_open_houses)),
        _stat_tile("Open House Groups", groups_total),
        _stat_tile("Agents Reached", listing["agents_reached_count"]),
        _stat_tile("Offers Received", len(active_offers)),
    ])
    feedback_html = "".join(_feedback_admin_html(f) for f in listing["feedback"]) or '<p class="db-empty-note">No feedback yet.</p>'
    listing_status_label = "Active" if listing["active"] else "Canceled"
    listing_toggle_label = "Reactivate" if not listing["active"] else "Cancel"

    return f"""
    <div class="adm-listing">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap">
        <span class="om-status">{listing_status_label}</span>
        <button type="button" class="om-logout adm-toggle-active" data-action="toggle_listing_active" data-id="{listing["id"]}">{listing_toggle_label} this listing</button>
      </div>
      <form class="adm-inline-form" data-action="update_listing_address" data-listing-id="{listing["id"]}" style="margin-bottom:14px">
        <input type="text" name="address" class="om-input" value="{html.escape(listing["address"])}" maxlength="{MAX_FIELD_LEN}" required style="flex:1 1 300px">
        <button type="submit" class="btn-primary adm-btn-sm">Save Address</button>
      </form>
      <form class="adm-inline-form" data-action="update_listing_status" data-listing-id="{listing["id"]}">
        <label class="om-field"><span class="om-field-label">Status</span>
          <select name="status" class="om-input">{_status_options(listing["status"])}</select>
        </label>
        <button type="submit" class="btn-primary adm-btn-sm">Save</button>
      </form>

      <div class="adm-listing-body" data-tabscope>
        <div class="db-tabs">
          <button type="button" class="db-tab active" data-tab="marketing">Activity</button>
          <button type="button" class="db-tab" data-tab="offers">Number of Offers</button>
          <button type="button" class="db-tab" data-tab="openhouses">Open Houses &amp; Showings</button>
          <button type="button" class="db-tab" data-tab="feedback">Feedbacks</button>
        </div>

        <div class="db-tab-panel" data-tab-panel="marketing">
          <div class="db-stats" style="margin-bottom:18px">{summary_stats_html}</div>
          <p class="db-empty-note" style="margin-bottom:14px">Showings, groups, and offers come from the Open Houses &amp; Showings and Number of Offers tabs -- log them there and these update automatically.</p>
          <form class="adm-inline-form" data-action="update_marketing" data-listing-id="{listing["id"]}">
            <label class="om-field"><span class="om-field-label">Agents Reached</span>
              <input type="number" min="0" name="agents_reached_count" class="om-input" value="{listing["agents_reached_count"]}">
            </label>
            <button type="submit" class="btn-primary adm-btn-sm">Save</button>
          </form>
        </div>

        <div class="db-tab-panel" data-tab-panel="offers" style="display:none">
          {offers_html}
          <form class="adm-inline-form" data-action="add_offer" data-listing-id="{listing["id"]}">
            <input type="number" name="price" class="om-input" placeholder="Price" min="0" step="1" required>
            <label class="om-field"><span class="om-field-label">Financing</span>
              <select name="financing_type" class="om-input"><option value="cash">Cash</option><option value="loan">Loan</option></select>
            </label>
            <label class="om-field"><span class="om-field-label">Close of Escrow</span>
              <input type="date" name="close_of_escrow" class="om-input">
            </label>
            <button type="submit" class="btn-primary adm-btn-sm">+ Add Offer</button>
          </form>
        </div>

        <div class="db-tab-panel" data-tab-panel="openhouses" style="display:none">
          {open_houses_html}
          <form class="adm-inline-form" data-action="add_open_house" data-listing-id="{listing["id"]}">
            <label class="om-field"><span class="om-field-label">Date</span>
              <input type="date" name="event_date" class="om-input" required>
            </label>
            <label class="om-field"><span class="om-field-label">Groups Through</span>
              <input type="number" min="0" name="groups_count" class="om-input" value="0">
            </label>
            <input type="text" name="notes" class="om-input" placeholder="Notes (optional)" maxlength="2000">
            <button type="submit" class="btn-primary adm-btn-sm">+ Log Open House</button>
          </form>
        </div>

        <div class="db-tab-panel" data-tab-panel="feedback" style="display:none">
          {feedback_html}
          <form class="adm-inline-form" data-action="add_feedback" data-listing-id="{listing["id"]}">
            <label class="om-field"><span class="om-field-label">Type</span>
              <select name="category" class="om-input">{_category_options()}</select>
            </label>
            <input type="text" name="note" class="om-input" placeholder="Feedback note…" maxlength="2000" required>
            <button type="submit" class="btn-primary adm-btn-sm">Add</button>
          </form>
        </div>
      </div>
    </div>"""


def _client_admin_html(client):
    active_listings = [l for l in client["listings"] if l["active"]]
    canceled_listings = [l for l in client["listings"] if not l["active"]]
    listings_html = "".join(_listing_admin_html(l) for l in active_listings) or '<p class="db-empty-note">No listings yet.</p>'
    canceled_listings_html = "".join(_listing_admin_html(l) for l in canceled_listings)
    history_html = f"""
      <details class="adm-history">
        <summary>History (canceled listings)</summary>
        {canceled_listings_html}
      </details>""" if canceled_listings else ""
    status_label = "Active" if client["active"] else "Deactivated"
    return f"""
  <details class="adm-client">
    <summary>
      <span class="adm-client-email">{html.escape(client["email"])}</span>
      {f'<span class="adm-client-name">{html.escape(client["name"])}</span>' if client["name"] else ''}
      <span class="om-status">{status_label}</span>
    </summary>
    <div class="adm-client-body">
      <form class="adm-inline-form" data-action="update_client_email" data-id="{client["id"]}" style="margin-bottom:20px">
        <label class="om-field"><span class="om-field-label">Seller Email</span>
          <input type="email" name="email" class="om-input" value="{html.escape(client["email"])}" required>
        </label>
        <button type="submit" class="btn-primary adm-btn-sm">Save Email</button>
      </form>
      {listings_html}
      {history_html}
      <form class="adm-inline-form" data-action="create_listing" data-client-id="{client["id"]}">
        <input type="text" name="address" class="om-input" placeholder="New listing address" maxlength="{MAX_FIELD_LEN}" required>
        <button type="submit" class="btn-primary adm-btn-sm">Add Listing</button>
      </form>
      <form class="adm-inline-form" data-action="reset_client_password" data-id="{client["id"]}" style="margin-top:14px">
        <input type="text" name="password" class="om-input" placeholder="New password for this seller" required minlength="{MIN_PASSWORD_LEN}">
        <button type="submit" class="btn-primary adm-btn-sm">Reset Password</button>
      </form>
      <button type="button" class="om-logout adm-toggle-active" data-action="toggle_client_active" data-id="{client["id"]}" style="margin-top:14px">{"Deactivate" if client["active"] else "Reactivate"} this seller</button>
    </div>
  </details>"""


def _toolbox_link_admin_html(link):
    status_label = "Active" if link["active"] else "Hidden"
    return f"""
  <form class="adm-inline-form" data-action="update_toolbox_link" data-id="{link["id"]}">
    <input type="text" name="name" class="om-input" value="{html.escape(link['name'])}" placeholder="Button name" required>
    <input type="url" name="url" class="om-input" value="{html.escape(link['url'])}" placeholder="https://..." required>
    <input type="number" name="sort_order" class="om-input" value="{link['sort_order']}" style="max-width:80px" title="Sort order">
    <button type="submit" class="btn-primary adm-btn-sm">Save</button>
    <span class="om-status">{status_label}</span>
    <button type="button" class="om-logout adm-toggle-active" data-action="toggle_toolbox_link_active" data-id="{link["id"]}">{"Hide" if link["active"] else "Show"}</button>
    <button type="button" class="om-logout adm-delete-btn" data-action="delete_toolbox_link" data-id="{link["id"]}">Delete</button>
  </form>"""


def _offmarket_buyer_admin_html(buyer):
    status_label = "Active" if buyer["active"] else "Deactivated"
    return f"""
  <details class="adm-client">
    <summary>
      <span class="adm-client-email">{html.escape(buyer["email"])}</span>
      {f'<span class="adm-client-name">{html.escape(buyer["name"])}</span>' if buyer["name"] else ''}
      <span class="om-status">{status_label}</span>
    </summary>
    <div class="adm-client-body">
      <form class="adm-inline-form" data-action="update_offmarket_buyer_email" data-id="{buyer["id"]}" style="margin-bottom:14px">
        <label class="om-field"><span class="om-field-label">Buyer Email</span>
          <input type="email" name="email" class="om-input" value="{html.escape(buyer["email"])}" required>
        </label>
        <button type="submit" class="btn-primary adm-btn-sm">Save Email</button>
      </form>
      <form class="adm-inline-form" data-action="reset_offmarket_buyer_password" data-id="{buyer["id"]}">
        <input type="text" name="password" class="om-input" placeholder="New password for this buyer" required minlength="{MIN_PASSWORD_LEN}">
        <button type="submit" class="btn-primary adm-btn-sm">Reset Password</button>
      </form>
      <button type="button" class="om-logout adm-toggle-active" data-action="toggle_offmarket_buyer_active" data-id="{buyer["id"]}" style="margin-top:14px">{"Deactivate" if buyer["active"] else "Reactivate"} this buyer</button>
    </div>
  </details>"""


def _offmarket_status_options(current):
    return "".join(f'<option value="{s}"{" selected" if s == current else ""}>{s}</option>' for s in OFFMARKET_STATUSES)


def _offmarket_listing_admin_html(listing):
    status_label = "Active" if listing["active"] else "Hidden"
    toggle_label = "Hide" if listing["active"] else "Show"
    photo_urls_text = "\n".join(listing.get("photo_urls") or [])
    flyer_path = f"/flyer/{listing['id']}"
    display_address = "Address Available Upon Request" if listing.get("hide_address") else listing["address"]
    return f"""
  <details class="adm-client">
    <summary>
      <span class="adm-client-email">{html.escape(display_address)}</span>
      <span class="om-status">{status_label}</span>
    </summary>
    <div class="adm-client-body">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px;flex-wrap:wrap">
        <a href="{flyer_path}" target="_blank" rel="noopener noreferrer" class="om-logout">View Flyer</a>
        <button type="button" class="om-logout adm-copy-link-btn" data-path="{flyer_path}">Copy Flyer Link</button>
        <button type="button" class="om-logout adm-toggle-active" data-action="toggle_offmarket_listing_active" data-id="{listing["id"]}">{toggle_label}</button>
        <button type="button" class="om-logout adm-delete-btn" data-action="delete_offmarket_listing" data-id="{listing["id"]}">Delete</button>
      </div>
      <form class="adm-inline-form" data-action="update_offmarket_listing" data-id="{listing["id"]}">
        <input type="text" name="address" class="om-input" value="{html.escape(listing['address'])}" placeholder="Address" required style="flex-basis:100%">
        <label class="db-checkbox" style="flex-basis:100%">
          <input type="checkbox" name="hide_address" value="on" {"checked" if listing.get('hide_address') else ""}>
          Hide address from buyers (shows the area instead until you're ready to reveal it)
        </label>
        <input type="text" name="area" class="om-input" value="{html.escape(listing.get('area') or '')}" placeholder="Area / neighborhood">
        <label class="om-field"><span class="om-field-label">Status</span>
          <select name="status" class="om-input">{_offmarket_status_options(listing['status'])}</select>
        </label>
        <input type="text" name="price" class="om-input" value="{html.escape(listing.get('price') or '')}" placeholder="Price — one figure or a range (e.g. $4,995,000  ·  $4.5M – $5M)">
        <input type="text" name="beds" class="om-input" value="{html.escape(listing.get('beds') or '')}" placeholder="Beds" style="max-width:100px">
        <input type="text" name="baths" class="om-input" value="{html.escape(listing.get('baths') or '')}" placeholder="Baths" style="max-width:100px">
        <input type="text" name="sqft" class="om-input" value="{html.escape(listing.get('sqft') or '')}" placeholder="Sqft" style="max-width:120px">
        <input type="text" name="lot_size" class="om-input" value="{html.escape(listing.get('lot_size') or '')}" placeholder="Lot Size" style="max-width:120px">
        <label class="om-field" style="flex-basis:100%"><span class="om-field-label">Description</span>
          {_RTE_TOOLBAR_HTML}
          <div class="om-input adm-rte-editor" contenteditable="true" data-placeholder="Describe the property...">{_sanitize_description_html(listing.get('description'))}</div>
          <input type="hidden" name="description" class="adm-rte-hidden">
        </label>
        <label class="om-field" style="flex-basis:100%"><span class="om-field-label">Photo URLs (one per line -- first is the main photo)</span>
          <textarea name="photo_urls" class="om-input" rows="3" style="width:100%;font-family:inherit;resize:vertical">{html.escape(photo_urls_text)}</textarea>
        </label>
        <input type="text" name="photo_alt" class="om-input" value="{html.escape(listing.get('photo_alt') or '')}" placeholder="Photo description (for accessibility)" style="flex-basis:100%">
        <input type="url" name="media_link" class="om-input" value="{html.escape(listing.get('media_link') or '')}" placeholder="Link to more photos/video (optional, e.g. a Drive folder)" style="flex-basis:100%">
        <label class="db-checkbox" style="flex-basis:100%">
          <input type="checkbox" name="hide_media_link" value="on" {"checked" if listing.get('hide_media_link') else ""}>
          Hide the "View Photos &amp; Videos" button (keeps the link saved above, just doesn't show it yet)
        </label>
        <button type="submit" class="btn-primary adm-btn-sm">Save Listing</button>
      </form>
    </div>
  </details>"""


# Admin dashboard shell + Buyer Match styles, in one admin-only stylesheet
# (the public pages never load this). Colours all come from areas.css tokens
# (--black ground, --white ink, --g1 surface, --g2 inset, --g3 hairline,
# --g5 dim, --g6 secondary, --red accent) so light/dark already work; only
# the two semantic hues are defined here.
_ADMIN_CSS = """<style>
  :root{--ac-ok:#5AA982;--ac-warn:#CB9440;--ac-shadow:0 16px 48px rgba(0,0,0,.5)}
  :root[data-theme="light"]{--ac-ok:#3B7D5F;--ac-warn:#9C6E22;--ac-shadow:0 16px 48px rgba(120,108,90,.18)}

  body{overflow-x:hidden}
  .ac-eyebrow{font-size:.58rem;letter-spacing:.22em;text-transform:uppercase;color:var(--g5);font-weight:500}
  .ac-num{font-family:var(--serif);font-variant-numeric:tabular-nums;line-height:1}

  /* rows: auto (topbar) + auto (body). No 1fr / min-height:100vh -- forcing
     the body row to fill the viewport is what left dead space below a short
     section on Chrome + Safari. body's own background covers any short page. */
  .ac-shell{display:grid;grid-template-columns:1fr;grid-template-rows:auto auto}
  @media (min-width:900px){ .ac-shell{grid-template-columns:216px 1fr;align-items:start} }

  .ac-top{
    grid-column:1/-1;position:sticky;top:0;z-index:40;display:flex;align-items:center;gap:12px;
    padding:calc(11px + env(safe-area-inset-top)) 16px 11px;background:var(--black);border-bottom:1px solid var(--g3);
  }
  @media (max-width:480px){
    .ac-top{gap:8px;padding:10px 12px}
    .ac-top .theme-picker .theme-opt{width:26px;height:26px}
  }
  .ac-badge{width:30px;height:30px;flex:none;display:grid;place-items:center;background:var(--red);color:#fff;font-family:var(--serif);font-size:1rem;border-radius:6px}
  .ac-brand{display:flex;align-items:center;gap:10px;min-width:0}
  .ac-brand b{font-family:var(--serif);font-weight:400;font-size:1rem;letter-spacing:.02em;display:block;line-height:1.15;white-space:nowrap}
  .ac-brand small{font-size:.52rem;letter-spacing:.24em;text-transform:uppercase;color:var(--g5);display:block;white-space:nowrap}
  @media (max-width:400px){ .ac-brand small{display:none} }
  .ac-top-sp{flex:1}
  .ac-logout{font-size:.58rem;letter-spacing:.16em;text-transform:uppercase;color:var(--g5);white-space:nowrap}
  .ac-logout:hover{color:var(--white)}

  .ac-rail{display:none;grid-row:2;border-right:1px solid var(--g3);padding:14px 10px;position:sticky;top:53px;align-self:start;max-height:calc(100dvh - 53px);overflow-y:auto}
  @media (min-width:900px){ .ac-rail{display:flex;flex-direction:column;gap:2px} }
  .ac-nav{
    display:flex;align-items:center;gap:12px;width:100%;text-align:left;padding:11px 12px;border-radius:8px;
    font-size:.66rem;letter-spacing:.14em;text-transform:uppercase;color:var(--g5);font-family:var(--sans);
  }
  .ac-nav svg{width:17px;height:17px;flex:none}
  .ac-nav:hover{color:var(--white);background:var(--g1)}
  .ac-nav[aria-current="true"]{color:var(--white);background:var(--g1);box-shadow:inset 2px 0 0 var(--red)}
  .ac-rail-sp{flex:1}
  .ac-rail-foot{color:var(--g5);font-size:.52rem;letter-spacing:.14em;text-transform:uppercase;padding:0 12px}

  .ac-main{grid-row:2;min-width:0;padding:20px 16px 40px;max-width:1140px;width:100%;margin:0 auto}
  @media (min-width:900px){ .ac-main{padding:24px 32px 48px} }
  @media (max-width:899px){ .ac-main{padding-bottom:calc(62px + env(safe-area-inset-bottom))} }

  .ac-toolbox{display:flex;align-items:center;gap:9px;overflow-x:auto;padding-bottom:16px;margin-bottom:22px;border-bottom:1px solid var(--g3);scrollbar-width:none}
  .ac-toolbox::-webkit-scrollbar{display:none}
  .ac-toolbox .ac-eyebrow{flex:none}
  .ac-toolbox .adm-toolbox-btn{flex:none;white-space:nowrap;padding:10px 16px;font-size:.62rem}
  .ac-toolbox .adm-toolbox-add-btn{width:34px;height:34px;flex:none;font-size:1.1rem}
  .ac-toolbox-manage{flex:none;margin-left:4px}
  .ac-toolbox-manage summary{font-size:.56rem;letter-spacing:.14em;text-transform:uppercase;color:var(--g5);cursor:pointer;list-style:none;white-space:nowrap}
  .ac-toolbox-manage summary::-webkit-details-marker{display:none}
  .ac-toolbox-manage[open]{position:relative}
  .ac-toolbox-manage[open] .adm-tiles{position:absolute;right:0;top:26px;z-index:30;width:min(90vw,420px);background:var(--black);border:1px solid var(--g3);padding:14px;box-shadow:var(--ac-shadow)}

  .ac-view[hidden]{display:none}
  .ac-view{animation:acfade .16s ease}
  @keyframes acfade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
  @media (prefers-reduced-motion:reduce){.ac-view{animation:none}}

  .ac-vhead{margin-bottom:18px}
  .ac-vhead h1{font-family:var(--serif);font-weight:400;font-size:clamp(1.7rem,4vw,2.3rem);margin:0;letter-spacing:.01em}
  .ac-vhead p{margin:5px 0 0;font-size:.82rem;color:var(--g5);max-width:56ch;line-height:1.6}

  .ac-kpis{display:grid;grid-template-columns:repeat(2,1fr);gap:1px;background:var(--g3);border:1px solid var(--g3);margin-bottom:20px}
  @media (min-width:640px){ .ac-kpis{grid-template-columns:repeat(3,1fr)} }
  @media (min-width:1120px){ .ac-kpis{grid-template-columns:repeat(6,1fr)} }
  .ac-kpi{background:var(--g1);padding:15px 14px}
  .ac-kpi .ac-eyebrow{display:block;margin-bottom:8px}
  .ac-kpi .ac-num{font-size:1.9rem;color:var(--red);display:block}
  .ac-kpi .sub{display:block;margin-top:6px;font-size:.6rem;color:var(--g5)}

  .ac-actions{display:flex;flex-wrap:wrap;gap:9px;margin-bottom:22px}
  .ac-actions .btn-primary{padding:11px 16px;font-size:.62rem}
  .ac-ghostbtn{border:1px solid var(--g3);background:var(--g1);color:var(--white);padding:11px 16px;font-size:.62rem;letter-spacing:.16em;text-transform:uppercase;border-radius:4px}
  .ac-ghostbtn:hover{border-color:var(--red)}

  .ac-grid2{display:grid;gap:16px}
  @media (min-width:980px){ .ac-grid2{grid-template-columns:1.3fr 1fr;align-items:start} }

  .ac-engine{display:grid;gap:20px}
  @media (min-width:520px){ .ac-engine{grid-template-columns:1fr auto;align-items:center} }
  .ac-statlist{display:flex;flex-direction:column}
  .ac-statlist div{display:flex;align-items:baseline;justify-content:space-between;gap:14px;padding:10px 0;border-top:1px solid var(--g3)}
  .ac-statlist div:first-child{border-top:none}
  .ac-statlist dt{font-size:.64rem;letter-spacing:.06em;text-transform:uppercase;color:var(--g5)}
  .ac-statlist dd{margin:0;font-family:var(--serif);font-size:1.1rem;font-variant-numeric:tabular-nums}

  .ac-ring{position:relative;width:132px;height:132px;margin:0 auto}
  .ac-ring svg{transform:rotate(-90deg)}
  .ac-ring .rt{fill:none;stroke:var(--g3);stroke-width:9}
  .ac-ring .rp{fill:none;stroke:var(--red);stroke-width:9;stroke-linecap:round;transition:stroke-dashoffset 1s cubic-bezier(.4,0,.1,1)}
  .ac-ring .lbl{position:absolute;inset:0;display:grid;place-content:center;text-align:center}
  .ac-ring .lbl b{font-family:var(--serif);font-size:1.7rem;font-variant-numeric:tabular-nums;line-height:1}
  .ac-ring .lbl span{font-size:.5rem;letter-spacing:.14em;text-transform:uppercase;color:var(--g5);margin-top:3px}

  .ac-feed{display:flex;flex-direction:column}
  .ac-feed-row{display:flex;gap:11px;align-items:flex-start;padding:11px 0;border-top:1px solid var(--g3)}
  .ac-feed-row:first-child{border-top:none}
  .ac-dot{width:7px;height:7px;border-radius:50%;flex:none;margin-top:6px;background:var(--g5)}
  .ac-dot.ok{background:var(--ac-ok)} .ac-dot.accent{background:var(--red)} .ac-dot.warn{background:var(--ac-warn)}
  .ac-feed-row p{margin:0;font-size:.8rem;line-height:1.5}
  .ac-feed-row time{margin-left:auto;flex:none;font-size:.58rem;letter-spacing:.08em;text-transform:uppercase;color:var(--g5);white-space:nowrap;padding-top:2px}
  .ac-feed-empty{font-size:.8rem;color:var(--g5)}

  .ac-tabbar{
    display:none;position:fixed;left:0;right:0;bottom:0;z-index:50;justify-content:space-around;
    background:var(--black);border-top:1px solid var(--g3);
    padding:6px 4px calc(6px + env(safe-area-inset-bottom));
    backdrop-filter:saturate(140%) blur(6px);
  }
  @media (max-width:899px){ .ac-tabbar{display:flex} }
  .ac-tabbar button{position:relative;flex:1;display:flex;flex-direction:column;align-items:center;gap:4px;padding:8px 2px 4px;color:var(--g5);font-family:var(--sans);transition:color .15s}
  .ac-tabbar button svg{width:20px;height:20px}
  .ac-tabbar button span{font-size:.5rem;letter-spacing:.1em;text-transform:uppercase}
  .ac-tabbar button[aria-current="true"]{color:var(--red)}
  .ac-tabbar button[aria-current="true"]::before{content:"";position:absolute;top:0;left:22%;right:22%;height:2px;background:var(--red)}

  .ac-view .db-section-title{font-size:.82rem;letter-spacing:.1em;text-transform:uppercase;color:var(--g5);margin-bottom:12px}
  .ac-view > .db-section-title{margin-top:8px}

  /* --- Buyer Match --- */
  .bm-wrap{display:grid;gap:16px}
  .bm-grid{display:flex;flex-wrap:wrap;gap:10px}
  .bm-grid .om-field{flex:1 1 150px}
  .bm-note{font-size:.82rem;color:var(--g5);margin:10px 0 4px;line-height:1.6}
  .bm-note-warn{color:var(--ac-warn)}
  .bm-results-title{font-size:.86rem;letter-spacing:.08em;text-transform:uppercase;color:var(--g5);margin:16px 0 8px}
  .bm-match{border:1px solid var(--g3);border-radius:6px;padding:12px 14px;margin-bottom:8px;background:var(--black)}
  .bm-match-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .bm-score{display:inline-flex;align-items:center;justify-content:center;min-width:32px;height:24px;border-radius:5px;background:var(--red);color:#fff;font-weight:600;font-size:.78rem;font-family:var(--serif)}
  .bm-match-name{font-weight:500}
  .bm-match-meta{color:var(--g5);font-size:.82rem}
  .bm-chips{margin-top:8px;display:flex;flex-wrap:wrap;gap:6px}
  .bm-chip{font-size:.66rem;padding:3px 9px;border-radius:999px;border:1px solid var(--g3);white-space:nowrap;color:var(--g6)}
  .bm-chip-hit{color:var(--ac-ok);border-color:var(--ac-ok)}
  .bm-chip-miss{color:var(--red);border-color:var(--red)}
  .bm-chip-unknown{color:var(--g5)}
  #bm-agent-fields{border-left:2px solid var(--g3);padding-left:12px;margin:4px 0}
</style>"""

# Tiny stroke icons for the rail + bottom tab bar (no braces -> f-string safe).
_ICON = {
    "overview": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="3" y="3" width="8" height="8" rx="1"/><rect x="13" y="3" width="8" height="8" rx="1"/><rect x="3" y="13" width="8" height="8" rx="1"/><rect x="13" y="13" width="8" height="8" rx="1"/></svg>',
    "sellers": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="9" cy="8" r="3.2"/><path d="M3.5 20c.6-3.4 3-5.3 5.5-5.3s4.9 1.9 5.5 5.3"/><path d="M16 5.5a3 3 0 0 1 0 5.6M17.5 20c-.3-2-1-3.6-2-4.7 2.3-.2 4.3 1.5 5 4.7"/></svg>',
    "offmarket": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M4 13 11 6h7v7l-7 7z"/><circle cx="14.5" cy="9.5" r="1.4"/></svg>',
    "match": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="12" r="8.2"/><circle cx="12" cy="12" r="4.4"/><circle cx="12" cy="12" r="1"/></svg>',
    "enrich": '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M20 11a8 8 0 0 0-14-4.5M4 5v3.5h3.5"/><path d="M4 13a8 8 0 0 0 14 4.5M20 19v-3.5h-3.5"/></svg>',
}

_ADMIN_SHELL_JS = r"""
(function () {
  // ---- Section switching (rail + bottom tab bar) ----
  var views = document.querySelectorAll('.ac-view');
  var navs = document.querySelectorAll('[data-nav]');
  function go(name) {
    var hit = false;
    views.forEach(function (v) { var on = v.dataset.view === name; v.hidden = !on; if (on) hit = true; });
    if (!hit) return;
    navs.forEach(function (b) { b.setAttribute('aria-current', b.dataset.nav === name ? 'true' : 'false'); });
    try { history.replaceState(null, '', '#' + name); } catch (e) {}
    window.scrollTo(0, 0);
    drawRings();
  }
  navs.forEach(function (b) { b.addEventListener('click', function () { go(b.dataset.nav); }); });
  var start = (location.hash || '').replace('#', '');
  if (start && document.querySelector('.ac-view[data-view="' + start + '"]')) go(start);

  // ---- Progress rings ----
  var reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function drawRings() {
    document.querySelectorAll('.ac-ring').forEach(function (r) {
      var pct = Math.max(0, Math.min(100, parseFloat(r.dataset.pct) || 0));
      var c = r.querySelector('.rp');
      if (!c) return;
      var len = 2 * Math.PI * 52;
      c.style.strokeDasharray = len;
      if (reduce) { c.style.strokeDashoffset = len * (1 - pct / 100); return; }
      c.style.strokeDashoffset = len;
      requestAnimationFrame(function () { requestAnimationFrame(function () {
        c.style.strokeDashoffset = len * (1 - pct / 100);
      }); });
    });
  }
  drawRings();

  // ---- Theme picker (same behaviour as the public site) ----
  function updateThemePickerUI(choice) {
    document.querySelectorAll('.theme-opt').forEach(function (btn) {
      btn.classList.toggle('active', btn.dataset.themeChoice === choice);
    });
  }
  function applyThemeChoice(choice) {
    var resolved = choice === 'auto'
      ? (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark')
      : choice;
    document.documentElement.setAttribute('data-theme', resolved);
    document.documentElement.setAttribute('data-theme-choice', choice);
    updateThemePickerUI(choice);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', resolved === 'light' ? '#FFFFFF' : '#000000');
  }
  window.setThemeChoice = function (choice) {
    try { localStorage.setItem('themeChoice', choice); } catch (e) {}
    applyThemeChoice(choice);
  };
  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', function () {
      var c = 'auto';
      try { c = localStorage.getItem('themeChoice') || 'auto'; } catch (e) {}
      if (c === 'auto') applyThemeChoice('auto');
    });
  }
  var cur = 'auto';
  try { cur = localStorage.getItem('themeChoice') || 'auto'; } catch (e) {}
  updateThemePickerUI(cur);

  // ---- Quick-action buttons on Overview jump to a section ----
  document.querySelectorAll('[data-goto]').forEach(function (b) {
    b.addEventListener('click', function () { go(b.dataset.goto); });
  });
})();
"""


def render_admin_page(body_html, title="Admin | Simone Marzullo"):
    """Dedicated app-shell wrapper for /admin -- no public marketing nav or
    footer, its own stylesheet, but the same theme tokens (areas.css) and
    pre-paint theme stamp as the rest of the site."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
{THEME_INIT_SCRIPT}
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
<title>{html.escape(title)}</title>
<meta name="robots" content="noindex, nofollow">
<meta name="theme-color" content="#000000">
<link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">
<link rel="icon" type="image/png" sizes="32x32" href="/assets/favicon-32.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=EB+Garamond:ital,wght@0,400;0,500;1,400;1,500&family=Inter:wght@300;400;500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/assets/areas.css">
{_ADMIN_CSS}
</head>
<body>
{body_html}
</body>
</html>"""


def _relative_time(ts):
    try:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        secs = (now - ts).total_seconds()
    except Exception:
        return ""
    if secs < 90:
        return "now"
    mins = secs / 60
    if mins < 60:
        return f"{int(mins)}m"
    hrs = mins / 60
    if hrs < 24:
        return f"{int(hrs)}h"
    days = hrs / 24
    if days < 14:
        return f"{int(days)}d"
    return f"{int(days / 7)}w"


def _activity_feed_html(activity):
    if not activity:
        return '<p class="ac-feed-empty">Nothing yet — activity from the tools shows up here.</p>'
    dot = {"buyer": "ok", "enrich": "accent", "listing": "", "seller": ""}
    rows = []
    for a in activity:
        rows.append(
            f'<div class="ac-feed-row"><span class="ac-dot {dot.get(a["kind"], "")}"></span>'
            f'<p>{html.escape(a["text"])}</p><time>{html.escape(_relative_time(a["ts"]))}</time></div>'
        )
    return "".join(rows)


def _buyer_need_row_html(n):
    crit = n.get("criteria") or {}
    src = "My client" if n.get("buyer_source") != "other_agent" else f"Agent: {html.escape(n.get('agent_name') or '—')}"
    band = _budget_band(crit.get("price_min"), crit.get("price_max")) or "—"
    areas = ", ".join(crit.get("areas") or []) or "—"
    when = n["last_matched_at"].strftime("%b %-d") if n.get("last_matched_at") else "never"
    toggle = "Archive" if n.get("active") else "Restore"
    return f"""
  <details class="adm-client">
    <summary>
      <span class="adm-client-email">{html.escape(n.get('buyer_name') or f"Need #{n['id']}")}</span>
      <span class="adm-client-name">{src} · {html.escape(band)} · {html.escape(areas)}</span>
      <span class="om-status">{n.get('last_match_count', 0)} match(es), {when}</span>
    </summary>
    <div class="adm-client-body">
      <p class="bm-note">Requirements: {html.escape(_criteria_sentence(crit))}</p>
      <button type="button" class="om-logout adm-toggle-active" data-action="toggle_buyer_need_active" data-id="{n['id']}">{toggle} this buyer need</button>
    </div>
  </details>"""


def _enrich_state_line(st):
    if not st:
        return "Not run yet."
    pos = st["next_offset"] or 0
    where = "at the start of a new pass" if not pos else f"~{pos:,} contacts into this pass"
    return (f"Resumes {where} · {st['passes']} full pass(es) done · "
            f"{st['total_updated']} contacts filled / {st['total_seen']} scanned · "
            f"{st['total_no_match']} with no LA County match")


def _buyer_match_panels_html(buyer_needs):
    active_needs = [n for n in buyer_needs if n.get("active")]
    history_needs = [n for n in buyer_needs if not n.get("active")]
    needs_html = "".join(_buyer_need_row_html(n) for n in active_needs) or '<p class="db-empty-note">No saved buyer needs yet -- add one above.</p>'
    history_html = "".join(_buyer_need_row_html(n) for n in history_needs) or '<p class="db-empty-note">Nothing archived.</p>'
    return f"""
    <div class="bm-wrap">
      <div class="adm-panel">
        <div class="db-section-title">FollowUpBoss property fields</div>
        <button type="button" class="btn-primary adm-btn-sm" id="bm-fub-setup">Check / set up FollowUpBoss fields</button>
        <div id="bm-fub-setup-result"></div>
      </div>

      <div class="adm-panel">
        <div class="db-section-title">Add a buyer need &amp; find prospects</div>
        <form id="bm-need-form" class="adm-inline-form" novalidate>
          <label class="om-field"><span class="om-field-label">Buyer name</span>
            <input type="text" name="buyer_name" class="om-input" required></label>
          <label class="om-field"><span class="om-field-label">Buyer email</span>
            <input type="email" name="buyer_email" class="om-input" autocomplete="off"></label>
          <label class="om-field"><span class="om-field-label">Buyer phone</span>
            <input type="text" name="buyer_phone" class="om-input" autocomplete="off"></label>
          <label class="om-field"><span class="om-field-label">Buyer is</span>
            <select name="buyer_source" class="om-input">
              <option value="self">My own buyer</option>
              <option value="other_agent">Represented by another agent</option>
            </select></label>
          <div id="bm-agent-fields" style="flex-basis:100%">
            <div class="bm-grid">
              <label class="om-field"><span class="om-field-label">Agent name</span>
                <input type="text" name="agent_name" class="om-input"></label>
              <label class="om-field"><span class="om-field-label">Agent brokerage</span>
                <input type="text" name="agent_brokerage" class="om-input"></label>
              <label class="om-field"><span class="om-field-label">Agent contact</span>
                <input type="text" name="agent_contact" class="om-input"></label>
            </div>
          </div>
          <div class="bm-grid" style="flex-basis:100%">
            <label class="om-field"><span class="om-field-label">Price min</span>
              <input type="text" name="price_min" class="om-input" placeholder="e.g. 1.5M"></label>
            <label class="om-field"><span class="om-field-label">Price max</span>
              <input type="text" name="price_max" class="om-input" placeholder="e.g. 3M"></label>
            <label class="om-field"><span class="om-field-label">Min beds</span>
              <input type="text" name="beds" class="om-input" placeholder="3"></label>
            <label class="om-field"><span class="om-field-label">Min baths</span>
              <input type="text" name="baths" class="om-input" placeholder="2"></label>
            <label class="om-field"><span class="om-field-label">Min sq ft</span>
              <input type="text" name="sqft" class="om-input" placeholder="1800"></label>
            <label class="om-field"><span class="om-field-label">Max sq ft</span>
              <input type="text" name="sqft_max" class="om-input" placeholder="3500"></label>
          </div>
          <label class="om-field" style="flex-basis:100%"><span class="om-field-label">Markets &amp; ZIP codes (comma-separated)</span>
            <input type="text" name="areas" class="om-input" list="bm-market-list" autocomplete="off"
                   placeholder="e.g. Santa Monica, 90291, Mar Vista — a market name matches every ZIP it covers">
            <datalist id="bm-market-list">{_market_datalist_options()}</datalist></label>
          <label class="om-field" style="flex-basis:100%"><span class="om-field-label">Property type(s) — optional, comma-separated</span>
            <input type="text" name="types" class="om-input" list="bm-type-list" autocomplete="off"
                   placeholder="Optional. Single Family Home, Condo/Townhome, Multifamily — a nudge only, never hides a match">
            <datalist id="bm-type-list">{_prop_type_datalist_options()}</datalist></label>
          <label class="om-field"><span class="om-field-label">Timeline</span>
            <input type="text" name="timeline" class="om-input" placeholder="e.g. 60–90 days"></label>
          <label class="om-field" style="flex-basis:100%"><span class="om-field-label">Must-haves / notes</span>
            <textarea name="notes" class="om-input" rows="2" style="width:100%;font-family:inherit;resize:vertical"></textarea></label>
          <label class="db-checkbox" style="flex-basis:100%">
            <input type="checkbox" name="log_matches" value="on">
            Log each match as a note on the prospect in FollowUpBoss
          </label>
          <button type="submit" class="btn-primary adm-btn-sm">Save buyer &amp; find prospects</button>
        </form>
        <div id="bm-need-result"></div>
      </div>

      <div class="adm-panel">
        <div class="db-section-title">Saved buyer needs</div>
        <button type="button" class="btn-primary adm-btn-sm" id="bm-rematch-all">Re-match all against the pipeline</button>
        <div id="bm-rematch-result" style="margin-top:10px"></div>
        <div id="bm-saved-needs" class="adm-clients" style="margin-top:14px">{needs_html}</div>
        <details class="adm-history"><summary>Archived buyer needs</summary>
          <div class="adm-clients">{history_html}</div>
        </details>
      </div>
    </div>"""


def _enrichment_panel_html(enrich_state):
    enrich_stage = os.environ.get("FUB_ENRICH_STAGE", "").strip()
    enrich_scope = f'the <strong>{html.escape(enrich_stage)}</strong> stage' if enrich_stage else "your whole FollowUpBoss database"
    return f"""
      <div class="adm-panel">
        <div class="db-section-title">Fill missing property data from public records</div>
        <p class="adm-tagline" style="margin:0 0 12px">Sweeps {enrich_scope} in small batches and, for any contact with a street address but blank beds / baths / sq ft / year / type, looks the parcel up in the <strong>LA County Assessor</strong> public records and fills the gaps in FollowUpBoss. It only fills blanks — it never overwrites data you already have — and tags each contact it touches <em>Enriched: LA County Assessor</em>. LA County only.</p>
        <p class="bm-note" id="bm-enrich-state">{html.escape(_enrich_state_line(enrich_state))}</p>
        <div style="display:flex;gap:10px;flex-wrap:wrap">
          <button type="button" class="btn-primary adm-btn-sm" id="bm-enrich-start">Start / resume sweep</button>
          <button type="button" class="om-logout" id="bm-enrich-stop" style="display:none">Stop</button>
          <button type="button" class="om-logout" id="bm-enrich-reset">Reset progress</button>
        </div>
        <div id="bm-enrich-result" style="margin-top:10px"></div>
        <p class="bm-note" style="margin-top:14px;border-top:1px solid var(--g3);padding-top:12px">
          This runs only while this tab is open. For a large database, run it hands-off instead:
          GitHub &rarr; the repo &rarr; <strong>Actions</strong> tab &rarr; <strong>enrich-sweep</strong> &rarr;
          <strong>Run workflow</strong>. That version downloads the whole county parcel roll once and
          finishes tens of thousands of contacts in one ~2-hour run, with your computer off. The
          progress shown above updates either way.
        </p>
      </div>"""


def build_admin_html(clients, toolbox_links, offmarket_buyers, offmarket_listings,
                     buyer_needs=None, enrich_state=None, counts=None, activity=None):
    active_clients = [c for c in clients if c["active"]]
    history_clients = [c for c in clients if not c["active"]]

    clients_html = "".join(_client_admin_html(c) for c in active_clients) or '<div class="om-empty">No active sellers -- add one above.</div>'
    history_clients_html = "".join(_client_admin_html(c) for c in history_clients) or '<div class="om-empty">No deactivated sellers.</div>'

    active_toolbox_links = [t for t in toolbox_links if t["active"]]
    toolbox_buttons_html = "".join(
        f'<a href="{html.escape(t["url"])}" target="_blank" rel="noopener noreferrer" class="btn-primary adm-toolbox-btn">{html.escape(t["name"])}</a>'
        for t in active_toolbox_links
    )
    toolbox_manage_html = "".join(_toolbox_link_admin_html(t) for t in toolbox_links) or '<p class="db-empty-note">No tools yet.</p>'

    active_offmarket_buyers = [b for b in offmarket_buyers if b["active"]]
    history_offmarket_buyers = [b for b in offmarket_buyers if not b["active"]]
    offmarket_buyers_html = "".join(_offmarket_buyer_admin_html(b) for b in active_offmarket_buyers) or '<div class="om-empty">No active buyers -- add one below.</div>'
    history_offmarket_buyers_html = "".join(_offmarket_buyer_admin_html(b) for b in history_offmarket_buyers) or '<div class="om-empty">No deactivated buyers.</div>'
    offmarket_listings_html = "".join(_offmarket_listing_admin_html(l) for l in offmarket_listings) or '<p class="db-empty-note">No off-market listings yet -- add one below.</p>'

    counts = counts or {}
    seller_archived = counts.get("sellers_archived", 0)
    buyer_match_panels = _buyer_match_panels_html(buyer_needs or [])
    enrichment_panel = _enrichment_panel_html(enrich_state)
    activity_html = _activity_feed_html(activity or [])
    nurture_stage = html.escape(os.environ.get("FUB_NURTURE_STAGE", "Nurture"))

    est = enrich_state or {}
    db_total = est.get("db_total") or 0
    next_off = est.get("next_offset") or 0
    passes = est.get("passes") or 0
    e_filled = est.get("total_updated") or 0
    e_scanned = est.get("total_seen") or 0
    e_nomatch = est.get("total_no_match") or 0
    if passes:
        ring_pct = 100
    elif db_total and next_off:
        ring_pct = min(100, round(100 * next_off / db_total))
    else:
        ring_pct = 0
    ring_txt = f"{ring_pct}%" if (db_total or passes) else "—"
    needs_active = len([n for n in (buyer_needs or []) if n.get("active")])
    last_match = "never"
    _lm = [n.get("last_matched_at") for n in (buyer_needs or []) if n.get("last_matched_at")]
    if _lm:
        _rt = _relative_time(max(_lm))
        last_match = "just now" if _rt == "now" else f"{_rt} ago"

    body = f"""
<div class="ac-shell">
  <header class="ac-top">
    <div class="ac-brand"><span class="ac-badge">M</span><span><b>Simone Marzullo</b><small>Admin Console</small></span></div>
    <div class="ac-top-sp"></div>
    {_theme_picker_html()}
    <a href="/admin?logout=1" class="ac-logout">Log out</a>
  </header>

  <nav class="ac-rail" aria-label="Sections">
    <button type="button" class="ac-nav" data-nav="overview" aria-current="true">{_ICON['overview']}<span>Overview</span></button>
    <button type="button" class="ac-nav" data-nav="sellers">{_ICON['sellers']}<span>Sellers</span></button>
    <button type="button" class="ac-nav" data-nav="offmarket">{_ICON['offmarket']}<span>Off-Market</span></button>
    <button type="button" class="ac-nav" data-nav="match">{_ICON['match']}<span>Buyer Match</span></button>
    <button type="button" class="ac-nav" data-nav="enrich">{_ICON['enrich']}<span>Enrichment</span></button>
    <div class="ac-rail-sp"></div>
    <small class="ac-rail-foot">{nurture_stage} stage · marzullore.com</small>
  </nav>

  <main class="ac-main">
    <div id="adm-notice" class="adm-notice" style="display:none"></div>

    <div class="ac-toolbox" aria-label="Toolbox">
      <span class="ac-eyebrow">Toolbox</span>
      <button type="button" class="adm-toolbox-add-btn" id="adm-toolbox-add-btn" aria-label="Add a tool" title="Add a tool">+</button>
      {toolbox_buttons_html}
      <details class="ac-toolbox-manage"><summary>Manage</summary><div class="adm-tiles">{toolbox_manage_html}</div></details>
    </div>

    <div class="adm-modal-overlay" id="adm-toolbox-modal-overlay" onclick="if(event.target===this)closeToolboxModal()">
      <div class="adm-modal">
        <button type="button" class="adm-modal-close" aria-label="Close" onclick="closeToolboxModal()">✕</button>
        <h2 class="adm-modal-title">Add a Tool</h2>
        <form id="adm-create-toolbox-form" class="adm-inline-form">
          <input type="text" name="name" class="om-input" placeholder="Button name" required style="flex-basis:100%">
          <input type="url" name="url" class="om-input" placeholder="https://..." required style="flex-basis:100%">
          <input type="number" name="sort_order" class="om-input" placeholder="Sort order" value="0" style="flex-basis:100%">
          <button type="submit" class="btn-primary adm-btn-sm" style="width:100%;justify-content:center">Add Tool</button>
        </form>
      </div>
    </div>

    <section class="ac-view" data-view="overview">
      <div class="ac-vhead"><h1>Overview</h1><p>The state of the business at a glance — tap a section in the rail or the bar below to go deeper.</p></div>

      <div class="ac-kpis">
        <div class="ac-kpi"><span class="ac-eyebrow">Sellers</span><span class="ac-num">{counts.get('sellers_active', 0)}</span><span class="sub">{seller_archived} archived</span></div>
        <div class="ac-kpi"><span class="ac-eyebrow">On-Market</span><span class="ac-num">{counts.get('listings_onmkt', 0)}</span><span class="sub">active listings</span></div>
        <div class="ac-kpi"><span class="ac-eyebrow">OM Buyers</span><span class="ac-num">{counts.get('om_buyers', 0)}</span><span class="sub">active</span></div>
        <div class="ac-kpi"><span class="ac-eyebrow">OM Listings</span><span class="ac-num">{counts.get('om_listings', 0)}</span><span class="sub">{counts.get('om_available', 0)} available</span></div>
        <div class="ac-kpi"><span class="ac-eyebrow">Buyer Needs</span><span class="ac-num">{needs_active}</span><span class="sub">last scan {last_match}</span></div>
        <div class="ac-kpi"><span class="ac-eyebrow">Enriched</span><span class="ac-num">{e_filled}</span><span class="sub">of {db_total or '—'} contacts</span></div>
      </div>

      <div class="ac-actions">
        <button type="button" class="btn-primary adm-btn-sm" data-goto="match">+ New buyer need</button>
        <button type="button" class="ac-ghostbtn" data-goto="offmarket">+ Off-market listing</button>
        <button type="button" class="ac-ghostbtn" data-goto="sellers">+ Seller</button>
        <button type="button" class="ac-ghostbtn" data-goto="enrich">Run enrichment</button>
      </div>

      <div class="ac-grid2">
        <div class="adm-panel">
          <div class="db-section-title">Buyer Match engine</div>
          <div class="ac-engine">
            <dl class="ac-statlist">
              <div><dt>Saved buyer needs</dt><dd>{needs_active}</dd></div>
              <div><dt>Last match run</dt><dd>{last_match}</dd></div>
              <div><dt>Contacts filled</dt><dd>{e_filled}</dd></div>
              <div><dt>Nurture stage</dt><dd>{nurture_stage}</dd></div>
            </dl>
            <div class="ac-ring" data-pct="{ring_pct}">
              <svg viewBox="0 0 120 120" width="132" height="132"><circle class="rt" cx="60" cy="60" r="52"/><circle class="rp" cx="60" cy="60" r="52"/></svg>
              <div class="lbl"><b>{ring_txt}</b><span>Enriched</span></div>
            </div>
          </div>
        </div>

        <div class="adm-panel">
          <div class="db-section-title">Recent activity</div>
          <div class="ac-feed">{activity_html}</div>
        </div>
      </div>
    </section>

    <section class="ac-view" data-view="sellers" hidden>
      <div class="ac-vhead"><h1>Sellers</h1><p>Client accounts and the listings tied to them.</p></div>
      <div class="adm-panel">
        <div class="db-section-title">Create new seller</div>
        <form id="adm-create-client-form" class="adm-inline-form">
          <input type="email" name="email" class="om-input" placeholder="Seller email" required>
          <input type="text" name="name" class="om-input" placeholder="Seller name">
          <input type="text" name="password" class="om-input" placeholder="Password to assign" required minlength="{MIN_PASSWORD_LEN}">
          <button type="submit" class="btn-primary adm-btn-sm">Create Seller</button>
        </form>
        <div class="om-error" id="adm-create-client-error"></div>
      </div>
      <div class="adm-clients">{clients_html}</div>
      <details class="adm-history">
        <summary>History (deactivated sellers)</summary>
        <div class="adm-clients">{history_clients_html}</div>
      </details>
    </section>

    <section class="ac-view" data-view="offmarket" hidden>
      <div class="ac-vhead"><h1>Off-Market</h1><p>The private buyer list and the inventory those buyers can see. Each listing has its own shareable flyer page — no login needed to view it.</p></div>

      <h2 class="db-section-title">Buyers</h2>
      <div class="adm-panel">
        <div class="db-section-title">Create new buyer</div>
        <form id="adm-create-offmarket-buyer-form" class="adm-inline-form">
          <input type="email" name="email" class="om-input" placeholder="Buyer email" required>
          <input type="text" name="name" class="om-input" placeholder="Buyer name">
          <input type="text" name="password" class="om-input" placeholder="Password to assign" required minlength="{MIN_PASSWORD_LEN}">
          <button type="submit" class="btn-primary adm-btn-sm">Create Buyer</button>
        </form>
        <div class="om-error" id="adm-create-offmarket-buyer-error"></div>
      </div>
      <div class="adm-clients">{offmarket_buyers_html}</div>
      <details class="adm-history">
        <summary>History (deactivated buyers)</summary>
        <div class="adm-clients">{history_offmarket_buyers_html}</div>
      </details>

      <h2 class="db-section-title" style="margin-top:34px">Listings</h2>
      <div class="adm-panel">
        <div class="db-section-title">Add new listing</div>
        <form id="adm-create-offmarket-listing-form" class="adm-inline-form" data-action="create_offmarket_listing">
        <input type="text" name="address" class="om-input" placeholder="Address" required style="flex-basis:100%">
        <label class="db-checkbox" style="flex-basis:100%">
          <input type="checkbox" name="hide_address" value="on">
          Hide address from buyers (shows the area instead until you're ready to reveal it)
        </label>
        <input type="text" name="area" class="om-input" placeholder="Area / neighborhood">
        <label class="om-field"><span class="om-field-label">Status</span>
          <select name="status" class="om-input">{_offmarket_status_options('Available')}</select>
        </label>
        <input type="text" name="price" class="om-input" placeholder="Price — one figure or a range (e.g. $4,995,000  ·  $4.5M – $5M)">
        <input type="text" name="beds" class="om-input" placeholder="Beds" style="max-width:100px">
        <input type="text" name="baths" class="om-input" placeholder="Baths" style="max-width:100px">
        <input type="text" name="sqft" class="om-input" placeholder="Sqft" style="max-width:120px">
        <input type="text" name="lot_size" class="om-input" placeholder="Lot Size" style="max-width:120px">
        <label class="om-field" style="flex-basis:100%"><span class="om-field-label">Description</span>
          {_RTE_TOOLBAR_HTML}
          <div class="om-input adm-rte-editor" contenteditable="true" data-placeholder="Describe the property..."></div>
          <input type="hidden" name="description" class="adm-rte-hidden">
        </label>
        <label class="om-field" style="flex-basis:100%"><span class="om-field-label">Photo URLs (one per line -- first is the main photo)</span>
          <textarea name="photo_urls" class="om-input" rows="3" style="width:100%;font-family:inherit;resize:vertical"></textarea>
        </label>
        <input type="text" name="photo_alt" class="om-input" placeholder="Photo description (for accessibility)" style="flex-basis:100%">
        <input type="url" name="media_link" class="om-input" placeholder="Link to more photos/video (optional, e.g. a Drive folder)" style="flex-basis:100%">
        <label class="db-checkbox" style="flex-basis:100%">
          <input type="checkbox" name="hide_media_link" value="on">
          Hide the "View Photos &amp; Videos" button (keeps the link saved above, just doesn't show it yet)
        </label>
        <button type="submit" class="btn-primary adm-btn-sm">Add Listing</button>
      </form>
    </div>
    <div class="adm-clients">{offmarket_listings_html}</div>
    </section>

    <section class="ac-view" data-view="match" hidden>
      <div class="ac-vhead"><h1>Buyer Match</h1><p>Enter what a buyer wants — yours or another agent's. The tool saves the buyer to FollowUpBoss and scans your <strong>{nurture_stage}</strong> stage for sellers whose property fits, so you know who to call with an "I have a buyer" pitch.</p></div>
      {buyer_match_panels}
    </section>

    <section class="ac-view" data-view="enrich" hidden>
      <div class="ac-vhead"><h1>Enrichment</h1><p>Backfill blank property fields on your FollowUpBoss contacts from LA County Assessor public records.</p></div>
      <div class="ac-grid2" style="margin-bottom:16px">
        <div class="adm-panel">
          <div class="db-section-title">Progress</div>
          <div class="ac-engine">
            <dl class="ac-statlist">
              <div><dt>Contacts filled</dt><dd>{e_filled}</dd></div>
              <div><dt>Scanned</dt><dd>{e_scanned}</dd></div>
              <div><dt>No LA County match</dt><dd>{e_nomatch}</dd></div>
              <div><dt>Full passes done</dt><dd>{passes}</dd></div>
            </dl>
            <div class="ac-ring" data-pct="{ring_pct}">
              <svg viewBox="0 0 120 120" width="132" height="132"><circle class="rt" cx="60" cy="60" r="52"/><circle class="rp" cx="60" cy="60" r="52"/></svg>
              <div class="lbl"><b>{ring_txt}</b><span>of pass {passes + 1}</span></div>
            </div>
          </div>
        </div>
      </div>
      {enrichment_panel}
    </section>
  </main>

  <nav class="ac-tabbar" aria-label="Sections">
    <button type="button" data-nav="overview" aria-current="true">{_ICON['overview']}<span>Home</span></button>
    <button type="button" data-nav="sellers">{_ICON['sellers']}<span>Sellers</span></button>
    <button type="button" data-nav="offmarket">{_ICON['offmarket']}<span>Off-Mkt</span></button>
    <button type="button" data-nav="match">{_ICON['match']}<span>Match</span></button>
    <button type="button" data-nav="enrich">{_ICON['enrich']}<span>Enrich</span></button>
  </nav>
</div>

<script>
async function adminPost(payload) {{
  const res = await fetch('/api/portal?section=admin', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify(payload),
  }});
  const data = await res.json();
  if (!res.ok || !data.ok) throw new Error(data.error || 'Something went wrong.');
  return data;
}}

function showNotice(msg) {{ sessionStorage.setItem('adminNotice', msg); }}

(function () {{
  const notice = sessionStorage.getItem('adminNotice');
  if (notice) {{
    const el = document.getElementById('adm-notice');
    el.textContent = notice;
    el.style.display = 'block';
    sessionStorage.removeItem('adminNotice');
  }}
}})();

document.getElementById('adm-create-client-form').addEventListener('submit', async function (e) {{
  e.preventDefault();
  const form = e.target;
  const email = form.email.value.trim();
  const password = form.password.value;
  const errEl = document.getElementById('adm-create-client-error');
  errEl.style.display = 'none';
  try {{
    await adminPost({{action: 'create_client', email, name: form.name.value.trim(), password}});
    showNotice(`Seller created — send them: ${{email}} / ${{password}}`);
    window.location.reload();
  }} catch (err) {{
    errEl.textContent = err.message;
    errEl.style.display = 'block';
  }}
}});

document.getElementById('adm-create-offmarket-buyer-form').addEventListener('submit', async function (e) {{
  e.preventDefault();
  const form = e.target;
  const email = form.email.value.trim();
  const password = form.password.value;
  const errEl = document.getElementById('adm-create-offmarket-buyer-error');
  errEl.style.display = 'none';
  try {{
    await adminPost({{action: 'create_offmarket_buyer', email, name: form.name.value.trim(), password}});
    showNotice(`Buyer created — send them: ${{email}} / ${{password}}`);
    window.location.reload();
  }} catch (err) {{
    errEl.textContent = err.message;
    errEl.style.display = 'block';
  }}
}});

function openToolboxModal() {{ document.getElementById('adm-toolbox-modal-overlay').classList.add('on'); }}
function closeToolboxModal() {{ document.getElementById('adm-toolbox-modal-overlay').classList.remove('on'); }}
document.getElementById('adm-toolbox-add-btn').addEventListener('click', openToolboxModal);

document.getElementById('adm-create-toolbox-form').addEventListener('submit', async function (e) {{
  e.preventDefault();
  const form = e.target;
  const payload = {{action: 'create_toolbox_link'}};
  new FormData(form).forEach(function (v, k) {{ payload[k] = v; }});
  try {{
    await adminPost(payload);
    window.location.reload();
  }} catch (err) {{
    alert(err.message);
  }}
}});

document.querySelectorAll('form[data-action]').forEach(function (form) {{
  form.addEventListener('submit', async function (e) {{
    e.preventDefault();
    const action = form.dataset.action;
    const payload = {{action}};
    if (form.dataset.listingId) payload.listingId = form.dataset.listingId;
    if (form.dataset.clientId) payload.clientId = form.dataset.clientId;
    if (form.dataset.id) payload.id = form.dataset.id;
    const fd = new FormData(form);
    fd.forEach(function (value, key) {{ payload[key] = value; }});
    try {{
      await adminPost(payload);
      if (action === 'reset_client_password') {{
        showNotice(`Password reset — send them: ${{payload.password}}`);
      }}
      window.location.reload();
    }} catch (err) {{
      alert(err.message);
    }}
  }});
}});

document.querySelectorAll('.adm-toggle-active[data-action]').forEach(function (btn) {{
  btn.addEventListener('click', async function () {{
    try {{
      await adminPost({{action: btn.dataset.action, id: btn.dataset.id}});
      window.location.reload();
    }} catch (err) {{
      alert(err.message);
    }}
  }});
}});

document.querySelectorAll('.adm-delete-btn[data-action]').forEach(function (btn) {{
  btn.addEventListener('click', async function () {{
    if (!confirm('Delete this? This can\\'t be undone.')) return;
    try {{
      await adminPost({{action: btn.dataset.action, id: btn.dataset.id}});
      window.location.reload();
    }} catch (err) {{
      alert(err.message);
    }}
  }});
}});

document.querySelectorAll('.adm-copy-link-btn[data-path]').forEach(function (btn) {{
  btn.addEventListener('click', async function () {{
    const url = location.origin + btn.dataset.path;
    const original = btn.textContent;
    try {{
      await navigator.clipboard.writeText(url);
      btn.textContent = 'Copied!';
    }} catch (err) {{
      window.prompt('Copy this link:', url);
    }}
    setTimeout(function () {{ btn.textContent = original; }}, 1500);
  }});
}});

function admAutoGrow(el) {{
  el.style.height = 'auto';
  el.style.height = el.scrollHeight + 'px';
}}
document.querySelectorAll('textarea.adm-autogrow').forEach(function (ta) {{
  admAutoGrow(ta);
  ta.addEventListener('input', function () {{ admAutoGrow(ta); }});
}});

// Rich-text description editor: a contenteditable box + a small toolbar
// that calls document.execCommand, kept in sync with a hidden `description`
// input so the generic form[data-action] handler above picks it up like
// any other field. styleWithCSS off so bold/italic/lists come out as plain
// <b>/<i>/<ul>/<ol> tags, which is what the server-side sanitizer allows.
try {{ document.execCommand('styleWithCSS', false, false); }} catch (e) {{}}
document.querySelectorAll('.adm-rte-editor').forEach(function (editor) {{
  const field = editor.closest('.om-field');
  const hidden = field.querySelector('input.adm-rte-hidden');
  const toolbar = field.querySelector('.adm-rte-toolbar');
  function sync() {{ hidden.value = editor.innerHTML; }}
  sync();
  editor.addEventListener('input', sync);
  editor.addEventListener('blur', sync);
  if (toolbar) {{
    toolbar.querySelectorAll('[data-cmd]').forEach(function (btn) {{
      btn.addEventListener('click', function () {{
        editor.focus();
        document.execCommand(btn.dataset.cmd, false, null);
        sync();
      }});
    }});
  }}
}});
</script>
<script>{DB_TABS_SCRIPT}</script>
<script>{BUYER_MATCH_SCRIPT}</script>
<script>{_ADMIN_SHELL_JS}</script>
"""
    return render_admin_page(body, "Admin | Simone Marzullo")


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------
class handler(BaseHTTPRequestHandler):
    def _send_html(self, status, page_html, set_cookie=None):
        body = page_html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status, payload, set_cookie=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def _section(self):
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        return params.get("section", [None])[0], query

    def do_GET(self):
        section, query = self._section()
        is_logout = "logout=1" in query

        if section == "match":
            oh = clean(urllib.parse.parse_qs(query).get("oh", [""])[0], 120)
            self._send_html(200, build_match_page_html(oh))
            return

        if section == "admin":
            if is_logout:
                expired = f"{ADMIN_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"
                self._send_html(200, build_admin_login_html(), set_cookie=expired)
                return
            token = get_cookie(self.headers.get("Cookie", ""), ADMIN_COOKIE_NAME)
            if not verify_session_token(token, "admin"):
                self._send_html(200, build_admin_login_html())
                return
            conn = None
            try:
                conn = get_conn()
                if conn is None:
                    self._send_html(503, build_error_html("The admin panel isn't set up yet -- POSTGRES_URL is missing.", "Admin | Simone Marzullo"))
                    return
                clients = fetch_all_clients(conn)
                toolbox_links = fetch_all_toolbox_links(conn)
                offmarket_buyers = fetch_all_offmarket_buyers(conn)
                offmarket_listings = fetch_all_offmarket_listings(conn)
                # Buyer Match tables (buyer_needs / fub_enrich_state) may not exist
                # yet on a deploy that hasn't had db/schema.sql re-run -- don't let
                # that 503 the whole admin panel.
                try:
                    buyer_needs = fetch_all_buyer_needs(conn)
                except Exception as e:
                    conn.rollback()
                    print(f"portal(admin): buyer_needs unavailable (run db/schema.sql?): {e}")
                    buyer_needs = []
                try:
                    enrich_state = fetch_enrich_state(conn)
                except Exception as e:
                    conn.rollback()
                    print(f"portal(admin): enrich state unavailable (run db/schema.sql?): {e}")
                    enrich_state = None
                try:
                    admin_counts = fetch_admin_counts(conn)
                    admin_activity = fetch_admin_activity(conn)
                except Exception as e:
                    conn.rollback()
                    print(f"portal(admin): overview data unavailable: {e}")
                    admin_counts, admin_activity = {}, []
            except Exception as e:
                print(f"portal(admin): failed to load data: {e}")
                self._send_html(503, build_error_html("Something went wrong loading the admin panel.", "Admin | Simone Marzullo"))
                return
            finally:
                if conn:
                    conn.close()
            self._send_html(200, build_admin_html(clients, toolbox_links, offmarket_buyers, offmarket_listings,
                                                  buyer_needs, enrich_state, admin_counts, admin_activity))
            return

        # Default: client dashboard (section == "dashboard" or unset)
        if is_logout:
            expired = f"{CLIENT_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"
            self._send_html(200, build_client_login_html(), set_cookie=expired)
            return
        token = get_cookie(self.headers.get("Cookie", ""), CLIENT_COOKIE_NAME)
        client_id = verify_session_token(token, "client")
        if not client_id:
            self._send_html(200, build_client_login_html())
            return
        conn = None
        try:
            conn = get_conn()
            if conn is None:
                self._send_html(503, build_error_html("The dashboard isn't set up yet -- please contact Simone directly.", "Client Dashboard | Simone Marzullo"))
                return
            data = fetch_dashboard_data(conn, client_id)
        except Exception as e:
            print(f"portal(dashboard): failed to load data: {e}")
            self._send_html(503, build_error_html("Something went wrong loading your dashboard. Please try again shortly.", "Client Dashboard | Simone Marzullo"))
            return
        finally:
            if conn:
                conn.close()
        if not data:
            expired = f"{CLIENT_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"
            self._send_html(200, build_client_login_html(), set_cookie=expired)
            return
        self._send_html(200, build_client_dashboard_html(data))

    def do_POST(self):
        section, _ = self._section()

        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            length = 0
        if length > MAX_BODY_BYTES:
            self._send_json(413, {"ok": False, "error": "Payload too large"})
            return
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (json.JSONDecodeError, RecursionError):
            self._send_json(400, {"ok": False, "error": "Invalid request."})
            return
        if not isinstance(data, dict):
            self._send_json(400, {"ok": False, "error": "Invalid request."})
            return

        if section == "admin":
            self._handle_admin_post(data)
            return

        if section == "match":
            self._handle_match_post(data)
            return

        # Default: client dashboard login
        email = str(data.get("email", "")).strip().lower()
        password = str(data.get("password", ""))
        if not email or not EMAIL_RE.match(email):
            self._send_json(400, {"ok": False, "error": "Enter a valid email address."})
            return
        if not password:
            self._send_json(400, {"ok": False, "error": "Enter your password."})
            return

        conn = None
        try:
            conn = get_conn()
            if conn is None:
                self._send_json(503, {"ok": False, "error": "The dashboard isn't set up yet -- please contact Simone directly."})
                return
            client = fetch_client_by_email(conn, email)
        except Exception as e:
            print(f"portal(dashboard): login lookup failed: {e}")
            self._send_json(503, {"ok": False, "error": "Something went wrong. Please try again shortly."})
            return
        finally:
            if conn:
                conn.close()

        if not client or not client["active"] or not verify_password(password, client["password_hash"]):
            self._send_json(401, {"ok": False, "error": "Incorrect email or password."})
            return

        push_dashboard_login_to_followupboss(client["email"], client["name"])
        cookie = f"{CLIENT_COOKIE_NAME}={make_session_token('client', client['id'])}; Path=/; Max-Age={SESSION_HOURS['client'] * 3600}; HttpOnly; Secure; SameSite=Lax"
        self._send_json(200, {"ok": True}, set_cookie=cookie)

    def _handle_match_post(self, data):
        """Public /match submissions -- no auth. Honeypot + field caps only."""
        if clean(data.get("website"), 100):
            self._send_json(200, {"ok": True, "count": 0})  # bot: pretend success
            return
        action = clean(data.get("action"), 20) or "search"
        name = clean(data.get("name"), 120)
        email = clean(data.get("email"), 200).lower()
        phone = clean(data.get("phone"), 40)
        if not name or not EMAIL_RE.match(email):
            self._send_json(400, {"ok": False, "error": "Please enter your name and a valid email."})
            return
        lead = {"name": name, "email": email, "phone": phone}

        if action == "message":
            msg = clean(data.get("message"), 2000)
            if not msg:
                self._send_json(400, {"ok": False, "error": "Enter a message first."})
                return
            try:
                push_match_message_to_fub(lead, msg)
            except Exception as e:
                print(f"portal(match): message push failed: {e}")
            self._send_json(200, {"ok": True})
            return

        if not phone:
            self._send_json(400, {"ok": False, "error": "Please enter a phone number."})
            return
        represented = clean(data.get("represented"), 8) == "yes"
        lead["represented"] = represented
        lead["agent_name"] = clean(data.get("agent_name"), 120) if represented else ""
        if represented and not lead["agent_name"]:
            self._send_json(400, {"ok": False, "error": "Please add your agent's name."})
            return
        criteria = parse_buyer_criteria(data)
        if not any([criteria["price_min"], criteria["price_max"], criteria["beds"],
                    criteria["sqft"], criteria["sqft_max"], criteria["areas"], criteria["types"]]):
            self._send_json(400, {"ok": False, "error": "Tell us at least one thing you're looking for — an area, a price, or bedrooms."})
            return
        oh = clean(data.get("oh"), 120)

        count = 0
        conn = None
        try:
            conn = get_conn()
            if conn is not None:
                count, _note = run_public_match(conn, criteria)
        except Exception as e:
            print(f"portal(match): search failed: {e}")
        finally:
            if conn:
                conn.close()
        try:
            push_match_lead_to_fub(lead, criteria, count, oh)
        except Exception as e:
            print(f"portal(match): lead push failed: {e}")
        self._send_json(200, {"ok": True, "count": count})

    def _handle_admin_post(self, data):
        action = clean(data.get("action"), 40)

        if action == "login":
            password = str(data.get("password", ""))
            expected = os.environ.get("ADMIN_PASSWORD")
            if not expected:
                self._send_json(503, {"ok": False, "error": "Admin access isn't set up yet."})
                return
            if not hmac.compare_digest(password.encode("utf-8"), expected.encode("utf-8")):
                self._send_json(401, {"ok": False, "error": "Incorrect password."})
                return
            cookie = f"{ADMIN_COOKIE_NAME}={make_session_token('admin')}; Path=/; Max-Age={SESSION_HOURS['admin'] * 3600}; HttpOnly; Secure; SameSite=Lax"
            self._send_json(200, {"ok": True}, set_cookie=cookie)
            return

        token = get_cookie(self.headers.get("Cookie", ""), ADMIN_COOKIE_NAME)
        if not verify_session_token(token, "admin"):
            self._send_json(401, {"ok": False, "error": "Not signed in."})
            return

        conn = None
        try:
            conn = get_conn()
            if conn is None:
                self._send_json(503, {"ok": False, "error": "Database isn't set up yet."})
                return

            if action == "create_client":
                email = clean(data.get("email")).lower()
                name = clean(data.get("name"))
                password = str(data.get("password", ""))
                if not email or not EMAIL_RE.match(email):
                    self._send_json(400, {"ok": False, "error": "Enter a valid email address."})
                    return
                if len(password) < MIN_PASSWORD_LEN:
                    self._send_json(400, {"ok": False, "error": f"Password must be at least {MIN_PASSWORD_LEN} characters."})
                    return
                try:
                    create_client(conn, email, name, password)
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
                    self._send_json(400, {"ok": False, "error": "A seller with that email already exists."})
                    return
                self._send_json(200, {"ok": True})
                return

            if action == "toggle_client_active":
                toggle_client_active(conn, int(data.get("id")))
                self._send_json(200, {"ok": True})
                return

            if action == "reset_client_password":
                client_id = int(data.get("id"))
                password = str(data.get("password", ""))
                if len(password) < MIN_PASSWORD_LEN:
                    self._send_json(400, {"ok": False, "error": f"Password must be at least {MIN_PASSWORD_LEN} characters."})
                    return
                reset_client_password(conn, client_id, password)
                self._send_json(200, {"ok": True})
                return

            if action == "update_client_email":
                client_id = int(data.get("id"))
                email = clean(data.get("email")).lower()
                if not email or not EMAIL_RE.match(email):
                    self._send_json(400, {"ok": False, "error": "Enter a valid email address."})
                    return
                try:
                    update_client_email(conn, client_id, email)
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
                    self._send_json(400, {"ok": False, "error": "A seller with that email already exists."})
                    return
                self._send_json(200, {"ok": True})
                return

            if action == "create_listing":
                client_id = int(data.get("clientId"))
                address = clean(data.get("address"))
                if not address:
                    self._send_json(400, {"ok": False, "error": "Address is required."})
                    return
                create_listing(conn, client_id, address)
                self._send_json(200, {"ok": True})
                return

            if action == "update_listing_status":
                listing_id = int(data.get("listingId"))
                status = clean(data.get("status"), 40) or "Active"
                if status not in LISTING_STATUSES:
                    status = "Active"
                update_listing_status(conn, listing_id, status)
                self._send_json(200, {"ok": True})
                return

            if action == "update_listing_address":
                listing_id = int(data.get("listingId"))
                address = clean(data.get("address"))
                if not address:
                    self._send_json(400, {"ok": False, "error": "Address is required."})
                    return
                update_listing_address(conn, listing_id, address)
                self._send_json(200, {"ok": True})
                return

            if action == "toggle_listing_active":
                toggle_listing_active(conn, int(data.get("id")))
                self._send_json(200, {"ok": True})
                return

            if action == "update_marketing":
                listing_id = int(data.get("listingId"))
                agents_reached = max(0, int(data.get("agents_reached_count") or 0))
                update_marketing(conn, listing_id, agents_reached)
                self._send_json(200, {"ok": True})
                return

            if action == "add_feedback":
                listing_id = int(data.get("listingId"))
                category = clean(data.get("category"), 40)
                if category not in FEEDBACK_CATEGORIES:
                    self._send_json(400, {"ok": False, "error": "Invalid feedback category."})
                    return
                note = clean(data.get("note"), 2000)
                if not note:
                    self._send_json(400, {"ok": False, "error": "Note can't be empty."})
                    return
                add_feedback(conn, listing_id, category, note)
                self._send_json(200, {"ok": True})
                return

            if action == "update_feedback":
                feedback_id = int(data.get("id"))
                category = clean(data.get("category"), 40)
                if category not in FEEDBACK_CATEGORIES:
                    self._send_json(400, {"ok": False, "error": "Invalid feedback category."})
                    return
                note = clean(data.get("note"), 2000)
                if not note:
                    self._send_json(400, {"ok": False, "error": "Note can't be empty."})
                    return
                update_feedback(conn, feedback_id, category, note)
                self._send_json(200, {"ok": True})
                return

            if action == "delete_feedback":
                delete_feedback(conn, int(data.get("id")))
                self._send_json(200, {"ok": True})
                return

            if action == "add_offer":
                listing_id = int(data.get("listingId"))
                price = float(data.get("price") or 0)
                financing_type = clean(data.get("financing_type"), 10)
                if financing_type not in ("cash", "loan"):
                    self._send_json(400, {"ok": False, "error": "Invalid financing type."})
                    return
                if price <= 0:
                    self._send_json(400, {"ok": False, "error": "Price must be greater than zero."})
                    return
                close_raw = clean(data.get("close_of_escrow"), 20)
                close_of_escrow = date.fromisoformat(close_raw) if close_raw else None
                add_offer(conn, listing_id, price, financing_type, close_of_escrow)
                self._send_json(200, {"ok": True})
                return

            if action == "toggle_offer_active":
                toggle_offer_active(conn, int(data.get("id")))
                self._send_json(200, {"ok": True})
                return

            if action == "add_open_house":
                listing_id = int(data.get("listingId"))
                event_raw = clean(data.get("event_date"), 20)
                if not event_raw:
                    self._send_json(400, {"ok": False, "error": "Date is required."})
                    return
                try:
                    event_date = date.fromisoformat(event_raw)
                except ValueError:
                    self._send_json(400, {"ok": False, "error": "Invalid date."})
                    return
                groups_count = max(0, int(data.get("groups_count") or 0))
                notes = clean(data.get("notes"), 2000)
                create_open_house(conn, listing_id, event_date, groups_count, notes)
                self._send_json(200, {"ok": True})
                return

            if action == "toggle_open_house_active":
                toggle_open_house_active(conn, int(data.get("id")))
                self._send_json(200, {"ok": True})
                return

            if action == "create_toolbox_link":
                name = clean(data.get("name"))
                url = clean(data.get("url"), 2000)
                sort_order = int(data.get("sort_order") or 0)
                if not name or not url:
                    self._send_json(400, {"ok": False, "error": "Name and URL are required."})
                    return
                create_toolbox_link(conn, name, url, sort_order)
                self._send_json(200, {"ok": True})
                return

            if action == "update_toolbox_link":
                link_id = int(data.get("id"))
                name = clean(data.get("name"))
                url = clean(data.get("url"), 2000)
                sort_order = int(data.get("sort_order") or 0)
                if not name or not url:
                    self._send_json(400, {"ok": False, "error": "Name and URL are required."})
                    return
                update_toolbox_link(conn, link_id, name, url, sort_order)
                self._send_json(200, {"ok": True})
                return

            if action == "toggle_toolbox_link_active":
                toggle_toolbox_link_active(conn, int(data.get("id")))
                self._send_json(200, {"ok": True})
                return

            if action == "delete_toolbox_link":
                delete_toolbox_link(conn, int(data.get("id")))
                self._send_json(200, {"ok": True})
                return

            if action == "create_offmarket_buyer":
                email = clean(data.get("email")).lower()
                name = clean(data.get("name"))
                password = str(data.get("password", ""))
                if not email or not EMAIL_RE.match(email):
                    self._send_json(400, {"ok": False, "error": "Enter a valid email address."})
                    return
                if len(password) < MIN_PASSWORD_LEN:
                    self._send_json(400, {"ok": False, "error": f"Password must be at least {MIN_PASSWORD_LEN} characters."})
                    return
                try:
                    create_offmarket_buyer(conn, email, name, password)
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
                    self._send_json(400, {"ok": False, "error": "A buyer with that email already exists."})
                    return
                self._send_json(200, {"ok": True})
                return

            if action == "toggle_offmarket_buyer_active":
                toggle_offmarket_buyer_active(conn, int(data.get("id")))
                self._send_json(200, {"ok": True})
                return

            if action == "reset_offmarket_buyer_password":
                buyer_id = int(data.get("id"))
                password = str(data.get("password", ""))
                if len(password) < MIN_PASSWORD_LEN:
                    self._send_json(400, {"ok": False, "error": f"Password must be at least {MIN_PASSWORD_LEN} characters."})
                    return
                reset_offmarket_buyer_password(conn, buyer_id, password)
                self._send_json(200, {"ok": True})
                return

            if action == "update_offmarket_buyer_email":
                buyer_id = int(data.get("id"))
                email = clean(data.get("email")).lower()
                if not email or not EMAIL_RE.match(email):
                    self._send_json(400, {"ok": False, "error": "Enter a valid email address."})
                    return
                try:
                    update_offmarket_buyer_email(conn, buyer_id, email)
                except psycopg2.errors.UniqueViolation:
                    conn.rollback()
                    self._send_json(400, {"ok": False, "error": "A buyer with that email already exists."})
                    return
                self._send_json(200, {"ok": True})
                return

            if action == "create_offmarket_listing":
                address = clean(data.get("address"))
                if not address:
                    self._send_json(400, {"ok": False, "error": "Address is required."})
                    return
                status = clean(data.get("status"), 40) or "Available"
                if status not in OFFMARKET_STATUSES:
                    status = "Available"
                create_offmarket_listing(
                    conn, address, clean(data.get("area")), status,
                    _normalize_price(clean(data.get("price"), 40)), clean(data.get("beds"), 20), clean(data.get("baths"), 20),
                    clean(data.get("sqft"), 20), clean(data.get("lot_size"), 20), _sanitize_description_html(data.get("description")),
                    _parse_photo_urls(data.get("photo_urls")), clean(data.get("photo_alt"), 300),
                    bool(data.get("hide_address")), clean(data.get("media_link"), 2000), bool(data.get("hide_media_link")),
                )
                self._send_json(200, {"ok": True})
                return

            if action == "update_offmarket_listing":
                listing_id = int(data.get("id"))
                address = clean(data.get("address"))
                if not address:
                    self._send_json(400, {"ok": False, "error": "Address is required."})
                    return
                status = clean(data.get("status"), 40) or "Available"
                if status not in OFFMARKET_STATUSES:
                    status = "Available"
                update_offmarket_listing(
                    conn, listing_id, address, clean(data.get("area")), status,
                    _normalize_price(clean(data.get("price"), 40)), clean(data.get("beds"), 20), clean(data.get("baths"), 20),
                    clean(data.get("sqft"), 20), clean(data.get("lot_size"), 20), _sanitize_description_html(data.get("description")),
                    _parse_photo_urls(data.get("photo_urls")), clean(data.get("photo_alt"), 300),
                    bool(data.get("hide_address")), clean(data.get("media_link"), 2000), bool(data.get("hide_media_link")),
                )
                self._send_json(200, {"ok": True})
                return

            if action == "toggle_offmarket_listing_active":
                toggle_offmarket_listing_active(conn, int(data.get("id")))
                self._send_json(200, {"ok": True})
                return

            if action == "delete_offmarket_listing":
                delete_offmarket_listing(conn, int(data.get("id")))
                self._send_json(200, {"ok": True})
                return

            # --- Buyer Match prospecting tool ---------------------------------
            if action == "fub_setup_fields":
                self._send_json(200, {"ok": True, "summary": fub_setup_custom_fields()})
                return

            if action == "buyer_need_run":
                buyer_name = clean(data.get("buyer_name"), 120)
                if not buyer_name:
                    self._send_json(400, {"ok": False, "error": "Buyer name is required."})
                    return
                source = clean(data.get("buyer_source"), 20)
                if source not in ("self", "other_agent"):
                    source = "self"
                buyer_email = clean(data.get("buyer_email"), 200).lower()
                if buyer_email and not EMAIL_RE.match(buyer_email):
                    self._send_json(400, {"ok": False, "error": "That buyer email doesn't look valid."})
                    return
                need = {
                    "buyer_name": buyer_name,
                    "buyer_email": buyer_email,
                    "buyer_phone": clean(data.get("buyer_phone"), 40),
                    "buyer_source": source,
                    "agent_name": clean(data.get("agent_name"), 120) if source == "other_agent" else "",
                    "agent_brokerage": clean(data.get("agent_brokerage"), 120) if source == "other_agent" else "",
                    "agent_contact": clean(data.get("agent_contact"), 200) if source == "other_agent" else "",
                }
                criteria = parse_buyer_criteria(data)
                if not any([criteria["price_min"], criteria["price_max"], criteria["beds"],
                            criteria["baths"], criteria["sqft"], criteria["sqft_max"],
                            criteria["areas"], criteria["types"]]):
                    self._send_json(400, {"ok": False, "error": "Enter at least one buyer requirement (price, beds, area, ...)."})
                    return
                fub_person_id, save_note = push_buyer_need_to_fub(need, criteria)
                match = run_buyer_match(criteria)
                new_id = create_buyer_need(conn, need, criteria, fub_person_id, len(match["matches"]))
                notice = f"FollowUpBoss scan issue: {match['error']}" if match["error"] else None
                if data.get("log_matches") and fub_person_id:
                    line = _criteria_sentence(criteria)
                    label = buyer_name + ("" if source == "self" else f" (via {need['agent_name'] or 'another agent'})")
                    logged = sum(1 for m in match["matches"] if log_match_to_fub(m["fub_id"], line, label))
                    if logged:
                        save_note += f" Logged the match on {logged} prospect(s) in FollowUpBoss."
                self._send_json(200, {
                    "ok": True, "id": new_id, "fub_person_id": fub_person_id,
                    "save_note": save_note, "notice": notice,
                    "scanned": match["scanned"], "matches": match["matches"],
                })
                return

            if action == "rematch_buyer_needs":
                report = []
                for n in fetch_all_buyer_needs(conn):
                    if not n["active"]:
                        continue
                    res = run_buyer_match(n["criteria"] or {})
                    update_buyer_need_match(conn, n["id"], len(res["matches"]))
                    report.append({
                        "buyer_name": n["buyer_name"] or f"Need #{n['id']}",
                        "match_count": len(res["matches"]),
                        "matches": res["matches"],
                    })
                self._send_json(200, {"ok": True, "report": report})
                return

            if action == "toggle_buyer_need_active":
                toggle_buyer_need_active(conn, int(data.get("id")))
                self._send_json(200, {"ok": True})
                return

            if action == "fub_enrich_run":
                try:
                    batch = int(data.get("batch") or _ENRICH_BATCH_DEFAULT)
                except (TypeError, ValueError):
                    batch = _ENRICH_BATCH_DEFAULT
                batch = max(1, min(batch, 40))
                result = run_enrich_batch(conn, batch)
                state = fetch_enrich_state(conn)
                result["totals"] = {
                    "passes": state["passes"], "seen": state["total_seen"],
                    "updated": state["total_updated"], "no_match": state["total_no_match"],
                }
                self._send_json(200 if result.get("ok") else 502, {"ok": result.get("ok", False), **result})
                return

            if action == "fub_enrich_reset":
                reset_enrich_state(conn)
                self._send_json(200, {"ok": True})
                return

            self._send_json(400, {"ok": False, "error": "Unknown action."})
        except (TypeError, ValueError):
            self._send_json(400, {"ok": False, "error": "Invalid request data."})
        except Exception as e:
            print(f"portal(admin): action '{action}' failed: {e}")
            self._send_json(500, {"ok": False, "error": "Something went wrong."})
        finally:
            if conn:
                conn.close()

    def log_message(self, fmt, *args):
        pass
