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
CREATE TABLE IF NOT EXISTS listings (
    id                 SERIAL PRIMARY KEY,
    client_id          INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    address            TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'Active',
    showings_count     INTEGER NOT NULL DEFAULT 0,
    emails_sent_count  INTEGER NOT NULL DEFAULT 0,
    calls_made_count   INTEGER NOT NULL DEFAULT 0,
    texts_sent_count   INTEGER NOT NULL DEFAULT 0,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
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

-- Admin-managed master list of contingency types, so Simone can add new
-- ones (e.g. "Sale of Buyer's Property") without a code change.
CREATE TABLE IF NOT EXISTS contingency_types (
    id     SERIAL PRIMARY KEY,
    name   TEXT NOT NULL UNIQUE,
    active BOOLEAN NOT NULL DEFAULT TRUE
);
INSERT INTO contingency_types (name) VALUES
    ('Inspection'), ('Appraisal'), ('Loan/Financing'), ('Sale of Buyer''s Property')
ON CONFLICT (name) DO NOTHING;

-- Offers received on a listing. `contingencies` stores the selected
-- contingency-type names at the time the offer was entered (denormalized on
-- purpose -- an offer keeps its terms even if a contingency type is later
-- renamed or deactivated).
CREATE TABLE IF NOT EXISTS offers (
    id               SERIAL PRIMARY KEY,
    listing_id       INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    price            NUMERIC(12, 2) NOT NULL,
    financing_type   TEXT NOT NULL CHECK (financing_type IN ('cash', 'loan')),
    close_of_escrow  DATE,
    contingencies    TEXT[] NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_offers_listing_id ON offers(listing_id);

-- Admin-defined marketing metric names (e.g. "Online Reactions", "Zillow
-- Saves") -- same pattern as contingency_types, so Simone can add whatever
-- metric she wants tracked without a code change.
CREATE TABLE IF NOT EXISTS metric_types (
    id     SERIAL PRIMARY KEY,
    name   TEXT NOT NULL UNIQUE,
    active BOOLEAN NOT NULL DEFAULT TRUE
);

-- One current value per metric type per listing (updated in place, like
-- showings_count etc. -- not a timestamped log).
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

-- team_members / resource_tiles (for a team resource hub) were designed
-- alongside this but held back for a later phase -- see README's
-- "Client dashboard and admin panel" section. Add them back here when
-- that feature is actually built.
