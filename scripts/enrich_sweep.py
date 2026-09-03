#!/usr/bin/env python3
"""Bulk one-shot version of the /admin "Fill missing property data" sweep.

The in-browser tool does one LA County API call per contact -- fine for a few
hundred, far too slow for ~50k. This script instead:

  1. Downloads the whole LA County Assessor residential parcel roll (~2.2M
     rows) once into a local SQLite index (~15 min, cached by GitHub Actions).
  2. Walks every FollowUpBoss contact and, for any with a street address but a
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
PARCEL_FIELDS = (
    "OBJECTID,SitusHouseNo,SitusStreet,SitusCity,SitusZIP,UseType,"
    "YearBuilt1,YearBuilt2,YearBuilt3,YearBuilt4,YearBuilt5,"
    "Bedrooms1,Bedrooms2,Bedrooms3,Bedrooms4,Bedrooms5,"
    "Bathrooms1,Bathrooms2,Bathrooms3,Bathrooms4,Bathrooms5,"
    "SQFTmain1,SQFTmain2,SQFTmain3,SQFTmain4,SQFTmain5"
)
STD_FIELDS = ["Bedrooms", "Bathrooms", "SqFt", "YearBuilt", "PropertyType"]
ENRICH_TAG = "Enriched: LA County Assessor"

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
    return (
        int(sum(beds)) if beds else None,
        int(sum(baths)) if baths else None,
        int(sum(sqft)) if sqft else None,
        int(max(yrs)) if yrs else None,
        (attrs.get("UseType") or "").strip() or None,
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


def person_needs_work(person, fieldmap):
    return any(str(person.get(name) or "").strip() in ("", "0") for name in fieldmap.values())


def fill_person(person, fieldmap, found, headers, dry_run):
    payload, filled = {}, []
    for label, name in fieldmap.items():
        val = found.get(label)
        if val in (None, "", 0):
            continue
        if str(person.get(name) or "").strip() not in ("", "0"):
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

    next_link, counted, _passes = pg_get_state(pg)
    first_qs = urllib.parse.urlencode(
        {"limit": 100, "fields": "allFields,allCustom", "includeTrash": "false",
         **({"stage": stage} if stage else {})})
    url = next_link if (next_link or "").startswith(f"{FUB_API}/people") else f"{FUB_API}/people?{first_qs}"
    if next_link:
        log(f"Resuming FollowUpBoss walk (~{counted:,} contacts done this pass).")
    else:
        log("Starting a fresh FollowUpBoss walk.")

    seen = upd = nomatch = noaddr = skipped = 0
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

        page_upd = page_nomatch = page_noaddr = page_skip = 0
        for person in people:
            seen += 1
            if not person_needs_work(person, fieldmap):
                skipped += 1
                page_skip += 1
                continue
            addrs = person.get("addresses") or []
            a0 = addrs[0] if isinstance(addrs, list) and addrs and isinstance(addrs[0], dict) else {}
            street = a0.get("street") or ""
            street = _UNIT_RE.sub("", re.sub(r"\s+", " ", street.strip().upper())).strip()
            m = re.match(r"^(\d+)\s+(.*)$", street)
            if not m:
                noaddr += 1
                page_noaddr += 1
                continue
            found = parcel_lookup(con, m.group(1), m.group(2), a0.get("city"), a0.get("code"))
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
        pg_save(pg, None if done else page_new_link, counted,
                len(people), page_upd, page_nomatch, total, done)
        log(f"page: +{page_upd} filled, {page_nomatch} no-match, {page_noaddr} no-addr, "
            f"{page_skip} already-done · running total filled {upd:,}{dry}")

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
        f"elapsed {(time.time()-started)/60:.1f} min")


if __name__ == "__main__":
    main()
