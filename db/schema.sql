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

-- Showing feedback and pricing feedback (from agents or buyers) -- one
-- flexible table, distinguished by category.
CREATE TABLE IF NOT EXISTS feedback_notes (
    id         SERIAL PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    category   TEXT NOT NULL CHECK (category IN ('showing', 'pricing_agent', 'pricing_buyer')),
    note       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_feedback_notes_listing_id ON feedback_notes(listing_id);

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

-- team_members / resource_tiles (for a team resource hub) were designed
-- alongside this but held back for a later phase -- see README's
-- "Client dashboard and admin panel" section. Add them back here when
-- that feature is actually built.
