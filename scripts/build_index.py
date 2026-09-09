#!/usr/bin/env python3
"""
Build / refresh `contact_properties` -- a local Postgres mirror of every
FollowUpBoss contact that has a property address, carrying the property data
already enriched onto it (Bedrooms/Bathrooms/SqFt/YearBuilt/PropertyType) plus
the ZIP / area it falls in.

Read-only against FollowUpBoss. The only writes are UPSERTs into Postgres.
Buyer Match and the public /match count read this table instead of paging the
FollowUpBoss API on every request.

Run it from GitHub Actions ("build-index" -> Run workflow) or the "Refresh
index" button in /admin. A run walks every contact once (~58k / 100 per page
= ~590 pages, a few minutes); if it is interrupted it resumes from
contact_index_state.next_link, and a completed full pass prunes rows for
contacts that no longer exist in FollowUpBoss.

Env:
  POSTGRES_URL / DATABASE_URL   (required)
  FUB_API_KEY                   (required)
  FUB_SYSTEM / FUB_SYSTEM_KEY   (optional, X-System headers)
  FUB_ENRICH_STAGE             (optional, limit the walk to one stage)
  INDEX_MAX_MINUTES            (optional, default 60)
"""
from __future__ import annotations

import os
import re
import sys
import time
import urllib.parse

import psycopg2
import psycopg2.extras

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))
from zip_areas import ZIPS_BY_AREA  # noqa: E402  -- shared ZIP -> market directory

# Reuse the FollowUpBoss / address plumbing already written for the sweep.
from enrich_sweep import (  # noqa: E402
    FUB_API, _http, _fub_headers, _first_addr, _person_name, _zip5, _num,
    _area_tag_for_zip, fub_fieldmap, log,
)

# A contact name that looks like a company / trust rather than a human.
_ENTITY_RE = re.compile(
    r"\b(llc|l\.l\.c|inc|incorporated|corp|corporation|company|ltd|lp|l\.p|llp|"
    r"trust|trustee|foundation|partners|partnership|holdings|properties|property|"
    r"investments|investment|ventures|capital|group|fund|estate|association|"
    r"realty|realtors|management|enterprises|dev|development)\b", re.I)


def _owner_type(name: str) -> str:
    return "entity" if name and _ENTITY_RE.search(name) else "person"


def _area_for(person: dict, zip5: str) -> str:
    """Prefer a market name already tagged on the contact (single name or a
    '/'-combo of directory markets); otherwise derive it from the ZIP."""
    for t in (person.get("tags") or []):
        parts = [p.strip() for p in str(t).split("/")]
        if parts and all(p in ZIPS_BY_AREA for p in parts):
            return str(t).strip()
    return _area_tag_for_zip(zip5) or ""


def _as_int(v):
    n = _num(v)
    return int(n) if n is not None else None


def _as_dec(v):
    return _num(v)


# columns written per row (seen_at / indexed_at are appended by the template)
_COLS = ("fub_person_id", "name", "owner_type", "stage", "street", "city",
         "zip5", "area", "beds", "baths", "sqft", "year_built",
         "property_type", "tags", "fub_updated_at")
_TEMPLATE = "(" + ",".join(["%s"] * len(_COLS)) + ",now(),now())"
_UPSERT = f"""
INSERT INTO contact_properties ({",".join(_COLS)}, seen_at, indexed_at)
VALUES %s
ON CONFLICT (fub_person_id) DO UPDATE SET
  name = EXCLUDED.name, owner_type = EXCLUDED.owner_type, stage = EXCLUDED.stage,
  street = EXCLUDED.street, city = EXCLUDED.city, zip5 = EXCLUDED.zip5,
  area = EXCLUDED.area, beds = EXCLUDED.beds, baths = EXCLUDED.baths,
  sqft = EXCLUDED.sqft, year_built = EXCLUDED.year_built,
  property_type = EXCLUDED.property_type, tags = EXCLUDED.tags,
  fub_updated_at = EXCLUDED.fub_updated_at, seen_at = now(), indexed_at = now()
"""


def _row(person: dict, fmap: dict):
    a = _first_addr(person)
    z = _zip5(a.get("code"))
    name = _person_name(person)

    def cf(label):
        k = fmap.get(label)
        return person.get(k) if k else None

    return (
        int(person["id"]),
        name[:200],
        _owner_type(name),
        (person.get("stage") or "")[:80],
        (a.get("street") or "")[:200],
        (a.get("city") or "")[:120],
        z,
        _area_for(person, z)[:120],
        _as_dec(cf("Bedrooms")),
        _as_dec(cf("Bathrooms")),
        _as_int(cf("SqFt")),
        _as_int(cf("YearBuilt")),
        (str(cf("PropertyType") or "").strip())[:80],
        [str(t)[:80] for t in (person.get("tags") or [])][:60],
        person.get("updated") or person.get("created"),
    )


def _pg_connect(url: str):
    p = urllib.parse.urlsplit(url)
    q = {k: v for k, v in urllib.parse.parse_qs(p.query).items()
         if k in ("sslmode", "connect_timeout", "application_name")}
    return psycopg2.connect(
        urllib.parse.urlunsplit((p.scheme, p.netloc, p.path,
                                 urllib.parse.urlencode(q, doseq=True), p.fragment)),
        connect_timeout=10)


# The two tables this job owns -- kept in sync with db/schema.sql. Created
# here too (idempotently) so the first run needs no manual DB step.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS contact_properties (
    fub_person_id  BIGINT PRIMARY KEY,
    name           TEXT NOT NULL DEFAULT '',
    owner_type     TEXT NOT NULL DEFAULT '',
    stage          TEXT NOT NULL DEFAULT '',
    street         TEXT NOT NULL DEFAULT '',
    city           TEXT NOT NULL DEFAULT '',
    zip5           TEXT NOT NULL DEFAULT '',
    area           TEXT NOT NULL DEFAULT '',
    beds           NUMERIC,
    baths          NUMERIC,
    sqft           INTEGER,
    year_built     INTEGER,
    property_type  TEXT NOT NULL DEFAULT '',
    tags           TEXT[] NOT NULL DEFAULT '{}',
    fub_updated_at TIMESTAMPTZ,
    seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    indexed_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_contact_properties_zip5 ON contact_properties(zip5);
CREATE INDEX IF NOT EXISTS idx_contact_properties_area ON contact_properties(area);
CREATE INDEX IF NOT EXISTS idx_contact_properties_beds ON contact_properties(beds, baths);
CREATE INDEX IF NOT EXISTS idx_contact_properties_type ON contact_properties(property_type);
CREATE INDEX IF NOT EXISTS idx_contact_properties_stage ON contact_properties(stage);
CREATE TABLE IF NOT EXISTS contact_index_state (
    id            INTEGER PRIMARY KEY DEFAULT 1,
    next_link     TEXT,
    pass_started  TIMESTAMPTZ,
    last_run_at   TIMESTAMPTZ,
    last_full_at  TIMESTAMPTZ,
    total_seen    INTEGER NOT NULL DEFAULT 0,
    total_indexed INTEGER NOT NULL DEFAULT 0,
    running       BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT contact_index_state_singleton CHECK (id = 1)
);
INSERT INTO contact_index_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
"""


def main():
    pg_url = os.environ.get("POSTGRES_URL") or os.environ.get("DATABASE_URL")
    api_key = os.environ.get("FUB_API_KEY")
    if not pg_url or not api_key:
        raise SystemExit("POSTGRES_URL and FUB_API_KEY are required.")
    max_secs = int(os.environ.get("INDEX_MAX_MINUTES", "60")) * 60
    stage = os.environ.get("FUB_ENRICH_STAGE", "").strip()
    headers = _fub_headers(api_key)
    started = time.time()

    pg = _pg_connect(pg_url)
    with pg.cursor() as cur:
        cur.execute(_SCHEMA)
    pg.commit()
    fmap = fub_fieldmap(headers)  # {'Bedrooms': 'customBedrooms', ...}; may be {}

    with pg.cursor() as cur:
        cur.execute("SELECT next_link FROM contact_index_state WHERE id = 1")
        r = cur.fetchone()
        if not r:
            cur.execute("INSERT INTO contact_index_state (id) VALUES (1)")
            pg.commit()
            next_link = None
        else:
            next_link = r[0]

    fresh = not (next_link or "").startswith(f"{FUB_API}/people")
    with pg.cursor() as cur:
        if fresh:
            cur.execute("UPDATE contact_index_state SET pass_started = now(), running = TRUE, "
                        "last_run_at = now(), total_seen = 0, total_indexed = 0 WHERE id = 1")
        else:
            cur.execute("UPDATE contact_index_state SET running = TRUE, last_run_at = now() WHERE id = 1")
        pg.commit()
        cur.execute("SELECT pass_started FROM contact_index_state WHERE id = 1")
        pass_started = cur.fetchone()[0]

    first_qs = urllib.parse.urlencode(
        {"limit": 100, "fields": "allFields,allCustom", "includeTrash": "false",
         **({"stage": stage} if stage else {})})
    url = next_link if not fresh else f"{FUB_API}/people?{first_qs}"
    log(("Fresh" if fresh else "Resuming") + " FollowUpBoss walk to build contact_properties"
        + (f" (stage: {stage})" if stage else "") + ".")

    seen = indexed = 0
    done = False
    while url:
        st, body, err = _http("GET", url, headers=headers)
        if not body:
            log(f"FollowUpBoss GET failed: {err}. Progress saved -- re-run to resume.")
            break
        people = body.get("people") or []
        nl = (body.get("_metadata") or {}).get("nextLink") or ""

        rows = []
        for person in people:
            seen += 1
            if not person.get("id"):
                continue
            try:
                rows.append(_row(person, fmap))
            except Exception as e:  # noqa: BLE001
                log(f"  skipped contact {person.get('id')}: {e}")
        if rows:
            with pg.cursor() as cur:
                psycopg2.extras.execute_values(cur, _UPSERT, rows,
                                               template=_TEMPLATE, page_size=200)
            indexed += len(rows)

        done = not nl and len(people) < 100
        with pg.cursor() as cur:
            cur.execute("UPDATE contact_index_state SET next_link = %s, last_run_at = now(), "
                        "total_seen = total_seen + %s, total_indexed = total_indexed + %s WHERE id = 1",
                        (None if done else nl, len(people), len(rows)))
        pg.commit()
        log(f"page: +{len(rows)} indexed  ·  running total {indexed:,}")

        if done:
            break
        if time.time() - started > max_secs:
            log(f"Hit the {max_secs // 60}-minute limit -- progress saved, re-run to finish.")
            break
        url = nl
        time.sleep(0.15)  # stay well under FollowUpBoss' rate limit

    pruned = 0
    with pg.cursor() as cur:
        if done and pass_started:
            cur.execute("DELETE FROM contact_properties WHERE seen_at < %s", (pass_started,))
            pruned = cur.rowcount
            cur.execute("UPDATE contact_index_state SET last_full_at = now(), running = FALSE WHERE id = 1")
            log(f"Full pass complete. Pruned {pruned:,} contacts no longer in FollowUpBoss.")
        else:
            cur.execute("UPDATE contact_index_state SET running = FALSE WHERE id = 1")
    pg.commit()
    pg.close()

    log("=" * 60)
    log(f"DONE. seen {seen:,} · indexed {indexed:,} · pruned {pruned:,} · "
        f"elapsed {(time.time() - started) / 60:.1f} min")


if __name__ == "__main__":
    main()
