#!/usr/bin/env python3
"""Bulk one-shot version of the /admin "Fill missing property data" sweep.

The in-browser tool does one LA County API call per contact -- fine for a few
hundred, far too slow for ~50k. This script instead:

  1. Downloads the whole LA County Assessor residential parcel roll (~2.2M
     rows) once into a local SQLite index (~15 min, cached by GitHub Actions).
  2. Runs each contact's mailing address through the free US Census batch
     geocoder to standardise it (spelling, directionals, ZIP+4) so more of
     them key-match a parcel. Results are cached in the same SQLite file.
  3. Walks every FollowUpBoss contact and, for any with a street address but a
     blank Bedrooms/Bathrooms/SqFt/YearBuilt/PropertyType, fills the gaps from
     the local index -- instant per contact.

It writes to the SAME `fub_enrich_state` row the admin dashboard reads, so the
progress ring / counters there reflect this job too. It only ever fills blank
fields, never overwrites, and tags each contact it touches
"Enriched: LA County Assessor" -- identical rules to the in-app tool.

Designed to run headless in GitHub Actions (manual "Run workflow"), but it also
runs fine locally:  POSTGRES_URL=... FUB_API_KEY=... python3 scripts/enrich_sweep.py

Env:
  POSTGRES_URL          required -- same DB the site uses (Supabase)
  FUB_API_KEY           required -- account-owner key
  FUB_SYSTEM            optional -- X-System header
  FUB_SYSTEM_KEY        optional -- X-System-Key header
  FUB_ENRICH_STAGE      optional -- limit the walk to one stage; blank = all
  ENRICH_MAX_MINUTES    optional -- hard stop (default 300, GH job limit is 360)
  ENRICH_PARCEL_DB      optional -- sqlite cache path (default ./parcels.sqlite)
  ENRICH_DRY_RUN        optional -- "1" to skip every FollowUpBoss write
  ENRICH_SKIP_GEOCODE   optional -- "1" to skip Census address standardisation
  ENRICH_REBUILD_INDEX  optional -- "1" to rebuild the parcel cache even if fresh
  ENRICH_PARCEL_MAX_PAGES optional -- stop the parcel download after N pages
                          (1000 rows each); for a quick partial run / testing
"""
from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import psycopg2

FUB_API = "https://api.followupboss.com/v1"
PARCEL_URL = ("https://public.gis.lacounty.gov/public/rest/services/"
              "LACounty_Cache/LACounty_Parcel/MapServer/0/query")
CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/addressbatch"
PARCEL_FIELDS = (
    "OBJECTID,SitusHouseNo,SitusStreet,SitusCity,SitusZIP,UseCode,UseType,UseDescription,"
    "Units1,Units2,Units3,Units4,Units5,"
    "YearBuilt1,YearBuilt2,YearBuilt3,YearBuilt4,YearBuilt5,"
    "Bedrooms1,Bedrooms2,Bedrooms3,Bedrooms4,Bedrooms5,"
    "Bathrooms1,Bathrooms2,Bathrooms3,Bathrooms4,Bathrooms5,"
    "SQFTmain1,SQFTmain2,SQFTmain3,SQFTmain4,SQFTmain5"
)
STD_FIELDS = ["Bedrooms", "Bathrooms", "SqFt", "YearBuilt", "PropertyType"]
# "Residential" is LA County's coarse UseType -- carries no real info, so a
# re-run should overwrite it with the fine type (see norm_property_type).
PROP_TYPE_STALE = {"", "residential"}
ENRICH_TAG = "Enriched: LA County Assessor"


def norm_property_type(use_code, use_desc, units=0):
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

_SUFFIX_RE = re.compile(
    r"\b(st|str|street|ave|av|avenue|blvd|boulevard|dr|drive|rd|road|ln|lane|ct|court|"
    r"pl|place|way|ter|terrace|cir|circle|hwy|highway|pkwy|parkway|trl|trail)\b\.?\s*$", re.I)
_UNIT_RE = re.compile(
    r"(?:\s(?:apt|apartment|unit|ste|suite|rm|room|fl|floor|no|bldg|lot|sp|space)\.?\s*[\w-]+"
    r"|\s?#\s*[\w-]+)\s*$", re.I)
_DIR_RE = re.compile(r"^(N|S|E|W|NE|NW|SE|SW)\s+", re.I)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower().replace(",", "").replace("$", "")
    m = re.search(r"(\d+(?:\.\d+)?)", s)
    return float(m.group(1)) if m else None


def _street_core(street):
    s = _DIR_RE.sub("", re.sub(r"\s+", " ", str(street or "").strip().upper()))
    s = _SUFFIX_RE.sub("", s).strip().rstrip(".").strip()
    return re.sub(r"\s+", " ", s)


def _zip5(z):
    d = re.sub(r"\D", "", str(z or ""))
    return d[:5] if len(d) >= 5 else ""


def _keys(house, street, city, zip_code):
    """(zip_key, city_key) -- either may be '' if not derivable."""
    house = re.sub(r"\D", "", str(house or ""))
    core = _street_core(street)
    if not house or not core:
        return ("", "")
    z = _zip5(zip_code)
    c = re.sub(r"\s+[A-Z]{2}$", "", str(city or "").strip().upper()).strip()
    return (f"{house}|{core}|{z}" if z else "",
            f"{house}|{core}|@{c}" if c else "")


def _rollup(attrs):
    def nums(prefix):
        return [n for i in range(1, 6) if (n := _num(attrs.get(f"{prefix}{i}")))]
    beds, baths, sqft, yrs = nums("Bedrooms"), nums("Bathrooms"), nums("SQFTmain"), nums("YearBuilt")
    units = max(nums("Units") or [0])
    return (
        int(sum(beds)) if beds else None,
        int(sum(baths)) if baths else None,
        int(sum(sqft)) if sqft else None,
        int(max(yrs)) if yrs else None,
        norm_property_type(attrs.get("UseCode"), attrs.get("UseDescription"), units) or None,
    )


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def _http(method, url, payload=None, headers=None, timeout=40):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                return resp.status, (json.loads(raw) if raw.strip() else {}), None
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:400]
            if e.code == 429 or e.code >= 500:
                wait = 2 ** attempt * 3
                log(f"  {e.code} from {urllib.parse.urlsplit(url).path} -- retry in {wait}s")
                time.sleep(wait)
                continue
            return e.code, None, f"{e.code}: {body}"
        except Exception as e:
            time.sleep(2 ** attempt * 2)
            last = str(e)
    return 0, None, f"unreachable ({last if 'last' in dir() else 'error'})"


def _fub_headers(api_key):
    h = {"Authorization": "Basic " + base64.b64encode(f"{api_key}:".encode()).decode(),
         "Content-Type": "application/json"}
    if os.environ.get("FUB_SYSTEM") and os.environ.get("FUB_SYSTEM_KEY"):
        h["X-System"] = os.environ["FUB_SYSTEM"]
        h["X-System-Key"] = os.environ["FUB_SYSTEM_KEY"]
    return h


# --------------------------------------------------------------------------
# US Census batch geocoder -- standardises FollowUpBoss addresses so more of
# them key-match a parcel. Free, no key, 10k rows/request. Results live in the
# same parcels.sqlite the Actions cache persists, so re-runs only geocode
# addresses they haven't seen before.
# --------------------------------------------------------------------------
def ensure_addr_cache(con):
    con.execute(
        "CREATE TABLE IF NOT EXISTS addr_std ("
        "raw TEXT PRIMARY KEY, house TEXT, street TEXT, city TEXT, zip TEXT, "
        "lat REAL, lon REAL, status TEXT)")
    con.commit()


def _first_addr(person):
    addrs = person.get("addresses") or []
    return addrs[0] if isinstance(addrs, list) and addrs and isinstance(addrs[0], dict) else {}


def _addr_key(street, city, state, zip_code):
    """Stable per-address cache key from the raw FollowUpBoss parts."""
    st = _UNIT_RE.sub("", re.sub(r"\s+", " ", str(street or "").strip())).strip().upper()
    ci = re.sub(r"\s+", " ", str(city or "").strip()).upper()
    stt = re.sub(r"[^A-Z]", "", str(state or "").upper())[:2]
    return f"{st}|{ci}|{stt}|{_zip5(zip_code)}" if st else ""


def _split_census_addr(matched):
    """'123 N MAIN ST, LOS ANGELES, CA, 90012' -> (house, street, city, zip5)."""
    parts = re.split(r"\s*,\s*", str(matched or "").strip())
    if len(parts) < 4:
        return None
    m = re.match(r"^(\d[\w-]*)\s+(.*)$", parts[0].strip())
    if not m:
        return None
    return (re.sub(r"\D", "", m.group(1)), re.sub(r"\s+", " ", m.group(2).strip().upper()),
            parts[-3].strip().upper(), _zip5(parts[-1]))


def _post_multipart(url, fields, file_name, file_bytes, timeout=240):
    boundary = "----enrichsweep" + os.urandom(8).hex()
    chunks = []
    for k, v in fields.items():
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    chunks.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="addressFile"; '
        f'filename="{file_name}"\r\nContent-Type: text/csv\r\n\r\n'.encode() + file_bytes + b"\r\n")
    chunks.append(f'--{boundary}--\r\n'.encode())
    req = urllib.request.Request(url, data=b"".join(chunks), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    last = ""
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            last = str(e)
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    raise RuntimeError(last or "census request failed")


def geocode_pending(con, pending):
    """pending: iterable of (key, street, city, state, zip). Geocodes the keys
    not already cached and stores the results. Returns (requested, matched)."""
    import csv as _csv
    import io as _io

    todo, seen = [], set()
    for key, street, city, state, zc in pending:
        if not key or key in seen:
            continue
        seen.add(key)
        if not con.execute("SELECT 1 FROM addr_std WHERE raw=?", (key,)).fetchone():
            todo.append((key, street, city, state, zc))
    if not todo:
        return (0, 0)

    buf = _io.StringIO()
    w = _csv.writer(buf)
    for i, (_k, street, city, state, zc) in enumerate(todo):
        w.writerow([i, str(street or "")[:100], str(city or "")[:50],
                    re.sub(r"[^A-Za-z]", "", str(state or ""))[:2], _zip5(zc)])
    try:
        out = _post_multipart(CENSUS_URL, {"benchmark": "Public_AR_Current"},
                              "a.csv", buf.getvalue().encode())
    except Exception as e:  # noqa: BLE001
        log(f"  census geocoder unavailable ({e}) -- raw addresses for this page")
        return (0, 0)
    if out.lstrip()[:1] in ("<", ""):
        log("  census geocoder returned no CSV -- raw addresses for this page")
        return (0, 0)

    rows, matched = [], 0
    for rec in _csv.reader(_io.StringIO(out)):
        if len(rec) < 3:
            continue
        try:
            idx = int(rec[0])
        except ValueError:
            continue
        if not 0 <= idx < len(todo):
            continue
        key = todo[idx][0]
        if rec[2] == "Match" and len(rec) >= 6:
            sp = _split_census_addr(rec[4])
            lat = lon = None
            try:
                lon, lat = (float(x) for x in rec[5].split(","))
            except (ValueError, IndexError):
                pass
            if sp:
                rows.append((key, sp[0], sp[1], sp[2], sp[3], lat, lon, "Match"))
                matched += 1
                continue
        rows.append((key, "", "", "", "", None, None, rec[2] or "No_Match"))
    con.executemany(
        "INSERT OR REPLACE INTO addr_std (raw,house,street,city,zip,lat,lon,status) "
        "VALUES (?,?,?,?,?,?,?,?)", rows)
    con.commit()
    return (len(todo), matched)


def std_lookup(con, key):
    if not key:
        return None
    r = con.execute("SELECT house, street, city, zip FROM addr_std "
                    "WHERE raw=? AND status='Match'", (key,)).fetchone()
    return {"house": r[0], "street": r[1], "city": r[2], "zip": r[3]} if r and r[0] and r[1] else None


# --------------------------------------------------------------------------
# Parcel index
# --------------------------------------------------------------------------
def build_parcel_index(db_path):
    fresh = (os.path.exists(db_path)
             and time.time() - os.path.getmtime(db_path) < 7 * 86400
             and os.environ.get("ENRICH_REBUILD_INDEX") != "1")
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE IF NOT EXISTS parcels (k TEXT, beds INT, baths INT, sqft INT, yr INT, ptype TEXT)")
    if fresh:
        n = con.execute("SELECT count(*) FROM parcels").fetchone()[0]
        if n > 1_500_000:
            log(f"Parcel index is fresh ({n:,} keys) -- reusing.")
            con.execute("CREATE INDEX IF NOT EXISTS idx_parcels_k ON parcels(k)")
            con.commit()
            return con
        log(f"Parcel index looks incomplete ({n:,} keys) -- rebuilding.")
    con.execute("DROP INDEX IF EXISTS idx_parcels_k")
    con.execute("DELETE FROM parcels")
    con.commit()

    log("Downloading LA County residential parcel roll (~2.2M rows, ~15 min)...")
    max_pages = int(os.environ.get("ENRICH_PARCEL_MAX_PAGES", "0")) or None
    last_oid, pages, rows = 0, 0, 0
    t0 = time.time()
    while True:
        if max_pages and pages >= max_pages:
            log(f"  stopping parcel download early at {pages} pages (ENRICH_PARCEL_MAX_PAGES)")
            break
        qs = urllib.parse.urlencode({
            "where": f"OBJECTID>{last_oid} AND SQFTmain1>0",
            "orderByFields": "OBJECTID", "resultRecordCount": 1000,
            "outFields": PARCEL_FIELDS, "returnGeometry": "false", "f": "json",
        })
        st, body, err = _http("GET", f"{PARCEL_URL}?{qs}", timeout=60)
        if not body or body.get("error"):
            log(f"  parcel page error ({err or body.get('error')}) -- retrying once")
            time.sleep(5)
            st, body, err = _http("GET", f"{PARCEL_URL}?{qs}", timeout=60)
            if not body or body.get("error"):
                raise SystemExit(f"parcel download failed at OBJECTID>{last_oid}: {err or body}")
        feats = body.get("features") or []
        if not feats:
            break
        batch = []
        for f in feats:
            a = f["attributes"]
            last_oid = max(last_oid, a["OBJECTID"])
            beds, baths, sqft, yr, ptype = _rollup(a)
            if not any((beds, baths, sqft, yr, ptype)):
                continue
            zk, ck = _keys(a.get("SitusHouseNo"), a.get("SitusStreet"), a.get("SitusCity"), a.get("SitusZIP"))
            for k in (zk, ck):
                if k:
                    batch.append((k, beds, baths, sqft, yr, ptype))
        con.executemany("INSERT INTO parcels VALUES (?,?,?,?,?,?)", batch)
        rows += len(batch)
        pages += 1
        if pages % 100 == 0:
            con.commit()
            log(f"  {pages} pages · {rows:,} keys · OBJECTID {last_oid:,} · {time.time()-t0:.0f}s")
        if not body.get("exceededTransferLimit") and len(feats) < 1000:
            break
    con.commit()
    log(f"Parcel index built: {rows:,} keys from {pages} pages in {time.time()-t0:.0f}s")
    log("Indexing...")
    con.execute("CREATE INDEX idx_parcels_k ON parcels(k)")
    con.commit()
    return con


def parcel_lookup(con, house, street, city, zip_code):
    zk, ck = _keys(house, street, city, zip_code)
    for k in (zk, ck):
        if not k:
            continue
        r = con.execute(
            "SELECT beds, baths, sqft, yr, ptype FROM parcels WHERE k=? LIMIT 1", (k,)
        ).fetchone()
        if r:
            return {"Bedrooms": r[0], "Bathrooms": r[1], "SqFt": r[2],
                    "YearBuilt": r[3], "PropertyType": r[4]}
    return None


# --------------------------------------------------------------------------
# FollowUpBoss
# --------------------------------------------------------------------------
def fub_fieldmap(headers):
    st, body, err = _http("GET", f"{FUB_API}/customFields?limit=100", headers=headers)
    if not body:
        raise SystemExit(f"couldn't read FollowUpBoss custom fields: {err}")
    have = {f.get("name") for f in (body.get("customfields") or body.get("customFields") or []) if f.get("name")}
    missing = [lbl for lbl in STD_FIELDS if f"custom{lbl}" not in have]
    if missing:
        log(f"WARNING: FollowUpBoss is missing custom fields {missing} -- "
            f"click 'Check / set up FollowUpBoss fields' in /admin first. Skipping those.")
    return {lbl: f"custom{lbl}" for lbl in STD_FIELDS if f"custom{lbl}" in have}


def _is_stale(label, current):
    """A field counts as fillable if it's empty/'0', or -- for PropertyType --
    still holds LA County's coarse 'Residential' from an earlier run."""
    c = str(current or "").strip()
    return c in ("", "0") or (label == "PropertyType" and c.lower() in PROP_TYPE_STALE)


def person_needs_work(person, fieldmap):
    return any(_is_stale(lbl, person.get(name)) for lbl, name in fieldmap.items())


def fill_person(person, fieldmap, found, headers, dry_run):
    payload, filled = {}, []
    for label, name in fieldmap.items():
        val = found.get(label)
        if val in (None, "", 0):
            continue
        if not _is_stale(label, person.get(name)):
            continue
        payload[name] = val
        filled.append(label)
    if not payload:
        return []
    tags = person.get("tags") or []
    if ENRICH_TAG not in tags:
        payload["tags"] = tags + [ENRICH_TAG]
    if dry_run:
        return filled
    st, body, err = _http("PUT", f"{FUB_API}/people/{person['id']}", payload, headers)
    return filled if body is not None else []


# --------------------------------------------------------------------------
# Progress row (shared with the admin dashboard)
# --------------------------------------------------------------------------
def pg_get_state(pg):
    with pg.cursor() as cur:
        cur.execute("SELECT next_link, next_offset, passes FROM fub_enrich_state WHERE id=1")
        r = cur.fetchone()
        if not r:
            cur.execute("INSERT INTO fub_enrich_state (id) VALUES (1)")
            pg.commit()
            return (None, 0, 0)
        return r


def pg_save(pg, next_link, counted, seen_inc, upd_inc, nomatch_inc, db_total, done):
    with pg.cursor() as cur:
        if done:
            cur.execute(
                """UPDATE fub_enrich_state SET next_offset=0, next_link=NULL, last_run_at=now(),
                       passes=passes+1, total_seen=total_seen+%s, total_updated=total_updated+%s,
                       total_no_match=total_no_match+%s, db_total=COALESCE(%s, db_total) WHERE id=1""",
                (seen_inc, upd_inc, nomatch_inc, db_total))
        else:
            cur.execute(
                """UPDATE fub_enrich_state SET next_offset=%s, next_link=%s, last_run_at=now(),
                       total_seen=total_seen+%s, total_updated=total_updated+%s,
                       total_no_match=total_no_match+%s, db_total=COALESCE(%s, db_total) WHERE id=1""",
                (counted, next_link, seen_inc, upd_inc, nomatch_inc, db_total))
    pg.commit()


# --------------------------------------------------------------------------
def main():
    pg_url = os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL")
    api_key = os.environ.get("FUB_API_KEY")
    if not pg_url or not api_key:
        raise SystemExit("POSTGRES_URL and FUB_API_KEY are required.")
    dry_run = os.environ.get("ENRICH_DRY_RUN") == "1"
    max_secs = int(os.environ.get("ENRICH_MAX_MINUTES", "300")) * 60
    db_path = os.environ.get("ENRICH_PARCEL_DB", "parcels.sqlite")
    stage = os.environ.get("FUB_ENRICH_STAGE", "").strip()
    headers = _fub_headers(api_key)
    started = time.time()

    con = build_parcel_index(db_path)
    ensure_addr_cache(con)
    skip_geo = os.environ.get("ENRICH_SKIP_GEOCODE") == "1"
    fieldmap = fub_fieldmap(headers)
    if not fieldmap:
        raise SystemExit("No usable FollowUpBoss custom fields -- nothing to write. Run field setup in /admin.")

    # clean the DSN of non-libpq params (Supabase pooler adds its own)
    p = urllib.parse.urlsplit(pg_url)
    q = {k: v for k, v in urllib.parse.parse_qs(p.query).items()
         if k in ("sslmode", "connect_timeout", "application_name")}
    pg = psycopg2.connect(urllib.parse.urlunsplit(
        (p.scheme, p.netloc, p.path, urllib.parse.urlencode(q, doseq=True), p.fragment)),
        connect_timeout=10)

    # A dry run reads + reports only -- it never reads/advances the shared
    # fub_enrich_state cursor, so it can't make a later real run skip contacts.
    next_link, counted = (None, 0) if dry_run else pg_get_state(pg)[:2]
    first_qs = urllib.parse.urlencode(
        {"limit": 100, "fields": "allFields,allCustom", "includeTrash": "false",
         **({"stage": stage} if stage else {})})
    url = next_link if (next_link or "").startswith(f"{FUB_API}/people") else f"{FUB_API}/people?{first_qs}"
    if dry_run:
        log("DRY RUN -- reading + matching only, no writes to FollowUpBoss or the progress row.")
    elif next_link:
        log(f"Resuming FollowUpBoss walk (~{counted:,} contacts done this pass).")
    else:
        log("Starting a fresh FollowUpBoss walk.")

    seen = upd = nomatch = noaddr = skipped = geo_req = geo_hit = 0
    dry = " [DRY RUN]" if dry_run else ""
    while url:
        st, body, err = _http("GET", url, headers=headers)
        if not body:
            log(f"FollowUpBoss GET failed: {err}. Progress saved; re-run to resume.")
            break
        people = body.get("people") or []
        meta = body.get("_metadata") or {}
        total = meta.get("total") if isinstance(meta.get("total"), int) else None
        page_new_link = meta.get("nextLink") or ""

        # standardise this page's addresses first, so parcel_lookup gets the
        # Census-corrected street/ZIP (cached, so re-runs skip known ones)
        page_geo_req = page_geo_hit = 0
        if not skip_geo:
            pending = []
            for person in people:
                if not person_needs_work(person, fieldmap):
                    continue
                a = _first_addr(person)
                if a.get("street"):
                    pending.append((_addr_key(a.get("street"), a.get("city"), a.get("state"), a.get("code")),
                                    a.get("street"), a.get("city"), a.get("state"), a.get("code")))
            if pending:
                page_geo_req, page_geo_hit = geocode_pending(con, pending)
                geo_req += page_geo_req
                geo_hit += page_geo_hit
                if page_geo_req:
                    time.sleep(0.3)

        page_upd = page_nomatch = page_noaddr = page_skip = 0
        for person in people:
            seen += 1
            if not person_needs_work(person, fieldmap):
                skipped += 1
                page_skip += 1
                continue
            a0 = _first_addr(person)
            house = street = None
            city, zc = a0.get("city"), a0.get("code")
            if not skip_geo:
                std = std_lookup(con, _addr_key(a0.get("street"), a0.get("city"),
                                                a0.get("state"), a0.get("code")))
                if std:
                    house, street, city, zc = std["house"], std["street"], std["city"], std["zip"]
            if not house:
                raw = _UNIT_RE.sub("", re.sub(r"\s+", " ", (a0.get("street") or "").strip().upper())).strip()
                m = re.match(r"^(\d+)\s+(.*)$", raw)
                if m:
                    house, street = m.group(1), m.group(2)
            if not house or not street:
                noaddr += 1
                page_noaddr += 1
                continue
            found = parcel_lookup(con, house, street, city, zc)
            if not found:
                nomatch += 1
                page_nomatch += 1
                continue
            got = fill_person(person, fieldmap, found, headers, dry_run)
            if got:
                upd += 1
                page_upd += 1
                if not dry_run:
                    time.sleep(0.12)  # keep well under FollowUpBoss' rate limit

        done = not page_new_link and len(people) < 100
        counted = 0 if done else counted + len(people)
        if not dry_run:
            pg_save(pg, None if done else page_new_link, counted,
                    len(people), page_upd, page_nomatch, total, done)
        log(f"page: +{page_upd} {'would fill' if dry_run else 'filled'}, {page_nomatch} no-match, "
            f"{page_noaddr} no-addr, {page_skip} already-done"
            + (f", geo {page_geo_hit}/{page_geo_req}" if page_geo_req else "")
            + f" · running total {upd:,}{dry}")

        if done:
            log("Reached the end of the list -- full pass complete.")
            break
        if time.time() - started > max_secs:
            log(f"Hit the {max_secs//60}-minute limit. Progress saved -- re-run 'enrich-sweep' to continue.")
            break
        url = page_new_link

    pg.close()
    con.close()
    log("=" * 60)
    log(f"DONE{dry}. scanned {seen:,} · filled {upd:,} · already-complete {skipped:,} · "
        f"no LA County match {nomatch:,} · no street address {noaddr:,} · "
        + (f"geocoded {geo_req:,} (matched {geo_hit:,}) · " if geo_req else "")
        + f"elapsed {(time.time()-started)/60:.1f} min")


if __name__ == "__main__":
    main()
