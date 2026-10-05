-- Ask Ticketing schema. SYNTHETIC DATA ONLY: every schedule, price, and sale
-- in this database is invented for a demo. Demo "as of" date: 2026-10-01.
-- Dates are ISO-8601 TEXT ('YYYY-MM-DD'). Money is INTEGER cents (USD),
-- excluding taxes and fees. Connections must run PRAGMA foreign_keys = ON.

CREATE TABLE venues (
    -- One row per venue. SYNTHETIC DATA. "Harborview Arena" is fictional.
    venue_id INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE
) STRICT;

CREATE TABLE events (
    -- One row per event (a single dated performance or game). SYNTHETIC DATA.
    -- home_team is set only for home basketball games, otherwise NULL.
    -- capacity = sellable seats for this demo (scaled down; not real venue capacity).
    event_id   INTEGER PRIMARY KEY,
    venue_id   INTEGER NOT NULL REFERENCES venues (venue_id),
    name       TEXT NOT NULL,
    category   TEXT NOT NULL CHECK (category IN ('basketball', 'concert', 'comedy', 'family', 'other')),
    home_team  TEXT CHECK (home_team IN ('Brooklyn Nets', 'New York Liberty')),
    event_date TEXT NOT NULL CHECK (event_date = date(event_date)),
    capacity   INTEGER NOT NULL CHECK (capacity > 0),
    CHECK (home_team IS NULL OR category = 'basketball')
) STRICT;

CREATE TABLE tickets (
    -- One row per purchased ticket. SYNTHETIC DATA.
    -- status 'sold' = ticket is held; 'refunded' = money returned and the seat
    -- went back to inventory. Counts and revenue exclude refunded tickets.
    -- price_cents = price paid in USD cents, excluding taxes and fees.
    ticket_id     INTEGER PRIMARY KEY,
    event_id      INTEGER NOT NULL REFERENCES events (event_id),
    purchase_date TEXT NOT NULL CHECK (purchase_date = date(purchase_date)),
    price_cents   INTEGER NOT NULL CHECK (price_cents > 0),
    status        TEXT NOT NULL CHECK (status IN ('sold', 'refunded'))
) STRICT;

CREATE INDEX tickets_event_id ON tickets (event_id);
CREATE INDEX tickets_purchase_date ON tickets (purchase_date);
CREATE INDEX events_event_date ON events (event_date);

-- Non-refunded tickets may never exceed event capacity.
CREATE TRIGGER tickets_capacity_insert BEFORE INSERT ON tickets
WHEN NEW.status = 'sold'
 AND (SELECT COUNT(*) FROM tickets WHERE event_id = NEW.event_id AND status = 'sold')
     >= (SELECT capacity FROM events WHERE event_id = NEW.event_id)
BEGIN
    SELECT RAISE(ABORT, 'event capacity exceeded');
END;

CREATE TRIGGER tickets_capacity_update BEFORE UPDATE OF status, event_id ON tickets
WHEN NEW.status = 'sold'
 AND (SELECT COUNT(*) FROM tickets
      WHERE event_id = NEW.event_id AND status = 'sold' AND ticket_id <> OLD.ticket_id)
     >= (SELECT capacity FROM events WHERE event_id = NEW.event_id)
BEGIN
    SELECT RAISE(ABORT, 'event capacity exceeded');
END;

-- A ticket cannot be purchased after its event date.
CREATE TRIGGER tickets_purchase_before_event BEFORE INSERT ON tickets
WHEN NEW.purchase_date > (SELECT event_date FROM events WHERE event_id = NEW.event_id)
BEGIN
    SELECT RAISE(ABORT, 'purchase_date after event_date');
END;
