-- Run this once against the Vercel Postgres database (Vercel dashboard ->
-- Storage -> your database -> Query, or any Postgres client using
-- POSTGRES_URL) before using /admin or /dashboard. Safe to re-run --
-- every statement is idempotent.

CREATE TABLE IF NOT EXISTS clients (
    id            SERIAL PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name          TEXT NOT NULL DEFAULT '',
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per property a client is selling. A client can have more than one
-- listing at a time.
-- showings_count/emails_sent_count/calls_made_count/texts_sent_count are
-- unused leftovers from earlier iterations of the Marketing tab (showings
-- moved to the open_houses log below; emails/calls/texts were replaced by
-- the single agents_reached_count) -- kept, never read or written, so
-- nothing breaks for anyone still on an older deployment.
CREATE TABLE IF NOT EXISTS listings (
    id                  SERIAL PRIMARY KEY,
    client_id           INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    address             TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'Active',
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    showings_count      INTEGER NOT NULL DEFAULT 0,
    emails_sent_count   INTEGER NOT NULL DEFAULT 0,
    calls_made_count    INTEGER NOT NULL DEFAULT 0,
    texts_sent_count    INTEGER NOT NULL DEFAULT 0,
    agents_reached_count INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE listings ADD COLUMN IF NOT EXISTS agents_reached_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE;
CREATE INDEX IF NOT EXISTS idx_listings_client_id ON listings(client_id);

-- Showing feedback, pricing feedback (from agents or buyers), and general
-- buyer feedback (not pricing-specific, lives under the Marketing section)
-- -- one flexible table, distinguished by category.
CREATE TABLE IF NOT EXISTS feedback_notes (
    id         SERIAL PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    category   TEXT NOT NULL CHECK (category IN ('showing', 'pricing_agent', 'pricing_buyer', 'buyer_feedback')),
    note       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_feedback_notes_listing_id ON feedback_notes(listing_id);
-- Widen the category check for databases created before "buyer_feedback"
-- existed (re-running this file is how an existing database picks it up).
ALTER TABLE feedback_notes DROP CONSTRAINT IF EXISTS feedback_notes_category_check;
ALTER TABLE feedback_notes ADD CONSTRAINT feedback_notes_category_check
    CHECK (category IN ('showing', 'pricing_agent', 'pricing_buyer', 'buyer_feedback'));

-- contingency_types existed for an "add contingencies to an offer" feature
-- that was dropped from the UI -- the table (and offers.contingencies below)
-- are kept only so nothing breaks for anyone still on an older deployment;
-- neither is written to or read by the app anymore.
CREATE TABLE IF NOT EXISTS contingency_types (
    id     SERIAL PRIMARY KEY,
    name   TEXT NOT NULL UNIQUE,
    active BOOLEAN NOT NULL DEFAULT TRUE
);
INSERT INTO contingency_types (name) VALUES
    ('Inspection'), ('Appraisal'), ('Loan/Financing'), ('Sale of Buyer''s Property')
ON CONFLICT (name) DO NOTHING;

-- Offers received on a listing. `active = false` means canceled/withdrawn
-- -- kept (not deleted) so a listing's full offer history stays intact.
CREATE TABLE IF NOT EXISTS offers (
    id               SERIAL PRIMARY KEY,
    listing_id       INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    price            NUMERIC(12, 2) NOT NULL,
    financing_type   TEXT NOT NULL CHECK (financing_type IN ('cash', 'loan')),
    close_of_escrow  DATE,
    contingencies    TEXT[] NOT NULL DEFAULT '{}',
    active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE offers ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE;
CREATE INDEX IF NOT EXISTS idx_offers_listing_id ON offers(listing_id);

-- One row per open house / showing event logged against a listing -- a
-- dated log (not a running counter like showings_count used to be).
-- `active = false` means canceled, same convention as offers above.
CREATE TABLE IF NOT EXISTS open_houses (
    id           SERIAL PRIMARY KEY,
    listing_id   INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    event_date   DATE NOT NULL,
    groups_count INTEGER NOT NULL DEFAULT 0,
    notes        TEXT NOT NULL DEFAULT '',
    active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE open_houses ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE;
CREATE INDEX IF NOT EXISTS idx_open_houses_listing_id ON open_houses(listing_id);

-- metric_types/listing_metrics were the custom "Marketing Metrics" feature
-- (admin-defined per-listing counters like "Online Reactions") -- removed
-- from the UI as unnecessary, same treatment as contingency_types above:
-- kept here, untouched, so no existing data is lost and nothing breaks for
-- anyone still on an older deployment; neither is read or written anymore.
CREATE TABLE IF NOT EXISTS metric_types (
    id     SERIAL PRIMARY KEY,
    name   TEXT NOT NULL UNIQUE,
    active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS listing_metrics (
    id             SERIAL PRIMARY KEY,
    listing_id     INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    metric_type_id INTEGER NOT NULL REFERENCES metric_types(id) ON DELETE CASCADE,
    value          INTEGER NOT NULL DEFAULT 0,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (listing_id, metric_type_id)
);
CREATE INDEX IF NOT EXISTS idx_listing_metrics_listing_id ON listing_metrics(listing_id);

-- Admin-only shortcuts to other tools Simone builds -- shown at the top of
-- /admin, not visible to clients.
CREATE TABLE IF NOT EXISTS toolbox_links (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    url        TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_toolbox_links_sort_order ON toolbox_links(sort_order);

-- Individual off-market buyer accounts (replaces the old single shared
-- OFFMARKET_PASSWORD gate) -- same shape/auth pattern as clients above.
-- Every active buyer sees the same pool of active offmarket_listings; there
-- is no per-buyer assignment.
CREATE TABLE IF NOT EXISTS offmarket_buyers (
    id            SERIAL PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name          TEXT NOT NULL DEFAULT '',
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Off-market listings, admin-managed from /admin. photo_urls holds
-- shareable image links (Simone hosts photos elsewhere and pastes the
-- URLs in) -- the first one is the card/flyer's primary photo, any others
-- are additional flyer gallery photos. beds/baths/sqft/lot_size are free
-- text (not numeric) so an admin can enter "4+", a range, or leave one
-- blank.
-- hide_address: when true, the real street address is never shown to
-- buyers (card title / flyer heading fall back to a generic placeholder)
-- -- common for off-market listings kept discreet until under contract.
-- media_link: an optional single link to more photos/video hosted
-- elsewhere (a Drive/Dropbox folder, a video, a Matterport tour, etc.) --
-- shown as a "View More Photos & Video" link on the flyer page when set
-- and hide_media_link is false (lets Simone save the link ahead of time
-- without showing the button until she's ready, without clearing the URL).
CREATE TABLE IF NOT EXISTS offmarket_listings (
    id               SERIAL PRIMARY KEY,
    address          TEXT NOT NULL,
    area             TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'Available',
    price            TEXT NOT NULL DEFAULT '',
    beds             TEXT NOT NULL DEFAULT '',
    baths            TEXT NOT NULL DEFAULT '',
    sqft             TEXT NOT NULL DEFAULT '',
    lot_size         TEXT NOT NULL DEFAULT '',
    description      TEXT NOT NULL DEFAULT '',
    photo_urls       TEXT[] NOT NULL DEFAULT '{}',
    photo_alt        TEXT NOT NULL DEFAULT '',
    hide_address     BOOLEAN NOT NULL DEFAULT FALSE,
    media_link       TEXT NOT NULL DEFAULT '',
    hide_media_link  BOOLEAN NOT NULL DEFAULT FALSE,
    active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE offmarket_listings ADD COLUMN IF NOT EXISTS hide_address BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE offmarket_listings ADD COLUMN IF NOT EXISTS media_link TEXT NOT NULL DEFAULT '';
ALTER TABLE offmarket_listings ADD COLUMN IF NOT EXISTS lot_size TEXT NOT NULL DEFAULT '';
ALTER TABLE offmarket_listings ADD COLUMN IF NOT EXISTS hide_media_link BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS idx_offmarket_listings_created ON offmarket_listings(created_at);

-- Buyer needs entered through the /admin "Buyer Match" prospecting tool.
-- Simone types in what a buyer wants (his own buyer or one represented by
-- another agent) and the tool scans his FollowUpBoss "Nurture" stage for
-- seller prospects whose property fits -- i.e. who to call with an
-- "I have a buyer" listing pitch. Rows are kept so every saved need can be
-- re-matched against the pipeline later as it grows / gets enriched.
-- criteria holds the raw search: price_min/max, beds, baths, sqft, areas,
-- types, timeline, notes. fub_person_id is the buyer contact this tool
-- created in FollowUpBoss. active = false soft-archives a need.
CREATE TABLE IF NOT EXISTS buyer_needs (
    id               SERIAL PRIMARY KEY,
    buyer_name       TEXT NOT NULL DEFAULT '',
    buyer_email      TEXT NOT NULL DEFAULT '',
    buyer_phone      TEXT NOT NULL DEFAULT '',
    buyer_source     TEXT NOT NULL DEFAULT 'self',   -- 'self' | 'other_agent'
    agent_name       TEXT NOT NULL DEFAULT '',
    agent_brokerage  TEXT NOT NULL DEFAULT '',
    agent_contact    TEXT NOT NULL DEFAULT '',
    criteria         JSONB NOT NULL DEFAULT '{}',
    fub_person_id    BIGINT,
    last_match_count INTEGER NOT NULL DEFAULT 0,
    last_matched_at  TIMESTAMPTZ,
    active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_buyer_needs_active ON buyer_needs(active, created_at DESC);

-- Phase 2 of the Buyer Match tool: a single-row cursor for the "enrich the
-- FollowUpBoss database from LA County Assessor public records" sweep. The
-- sweep runs in small batches (a full pass can't finish inside one
-- serverless request), so next_offset remembers where to resume; it wraps
-- back to 0 and bumps `passes` after each complete pass. Counters are
-- cumulative across passes. Only ever fills blank property custom fields in
-- FollowUpBoss -- never overwrites existing data.
CREATE TABLE IF NOT EXISTS fub_enrich_state (
    id             INTEGER PRIMARY KEY DEFAULT 1,
    next_offset    INTEGER NOT NULL DEFAULT 0,
    passes         INTEGER NOT NULL DEFAULT 0,
    total_seen     INTEGER NOT NULL DEFAULT 0,
    total_updated  INTEGER NOT NULL DEFAULT 0,
    total_no_match INTEGER NOT NULL DEFAULT 0,
    last_run_at    TIMESTAMPTZ,
    CONSTRAINT fub_enrich_state_singleton CHECK (id = 1)
);
INSERT INTO fub_enrich_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
-- db_total: the FollowUpBoss contact count as of the last batch, so the
-- admin dashboard can show a real "% of the way through" progress ring.
ALTER TABLE fub_enrich_state ADD COLUMN IF NOT EXISTS db_total INTEGER;

-- team_members / resource_tiles (for a team resource hub) were designed
-- alongside this but held back for a later phase -- see README's
-- "Client dashboard and admin panel" section. Add them back here when
-- that feature is actually built.
