"""Deterministic SYNTHETIC ticketing data for Ask Ticketing.

Every schedule, price, and sale produced here is invented for a demo. Opponent
team names are used only as labels; none of the dates are real schedules.
"Harborview Arena" is a fictional venue.

The fixed demo "as of" date is 2026-10-01: no purchase is later than it, and
"upcoming" means an event date strictly after it.
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from importlib import resources
from pathlib import Path

AS_OF = date(2026, 10, 1)
SEED = 20261001
DEFAULT_DB_PATH = Path("data/local/ticketing.db")

BARCLAYS = 1
HARBORVIEW = 2
NETS = "Brooklyn Nets"
LIBERTY = "New York Liberty"

# Base ticket price in cents before the per-event factor and per-ticket tier.
BASE_PRICE_CENTS = {NETS: 9500, LIBERTY: 5500, "concert": 11000, "comedy": 6500, "family": 4500, "other": 2500}
CAPACITY_RANGE = {"basketball": (150, 250), "concert": (120, 220), "comedy": (80, 140), "family": (90, 160), "other": (60, 100)}
# (price multiplier, weight): upper bowl, lower bowl, premium, courtside/floor.
PRICE_TIERS = ((0.6, 4), (1.0, 5), (1.6, 2), (2.5, 1))
REFUND_RATE = 0.05


@dataclass(frozen=True)
class Venue:
    venue_id: int
    name: str


@dataclass(frozen=True)
class Event:
    event_id: int
    venue_id: int
    name: str
    category: str
    home_team: str | None
    event_date: str
    capacity: int


@dataclass(frozen=True)
class Ticket:
    ticket_id: int
    event_id: int
    purchase_date: str
    price_cents: int
    status: str


@dataclass(frozen=True)
class Dataset:
    venues: tuple[Venue, ...]
    events: tuple[Event, ...]
    tickets: tuple[Ticket, ...]


def _schedule() -> list[tuple]:
    """(venue_id, name, category, home_team, event_date, profile). Synthetic."""
    s: list[tuple] = []

    def nets(d, opponent, suffix=""):
        s.append((BARCLAYS, f"{NETS} vs. {opponent}{suffix}", "basketball", NETS, d, "normal"))

    def liberty(d, opponent):
        s.append((BARCLAYS, f"{LIBERTY} vs. {opponent}", "basketball", LIBERTY, d, "normal"))

    def show(venue_id, d, name, category, profile="normal"):
        s.append((venue_id, name, category, None, d, profile))

    # 2024
    nets("2024-01-12", "Boston Celtics")
    nets("2024-02-09", "Miami Heat")
    nets("2024-03-22", "Chicago Bulls")
    nets("2024-11-08", "Toronto Raptors")
    nets("2024-12-14", "Philadelphia 76ers")
    liberty("2024-05-25", "Las Vegas Aces")
    liberty("2024-06-30", "Chicago Sky")
    liberty("2024-08-17", "Seattle Storm")
    liberty("2024-09-14", "Connecticut Sun")
    show(BARCLAYS, "2024-03-02", "Laugh Track Comedy Tour", "comedy")
    show(BARCLAYS, "2024-04-13", "Neon Tides Live", "concert")
    show(BARCLAYS, "2024-07-19", "The Velvet Static World Tour", "concert")
    show(BARCLAYS, "2024-10-26", "Orchestra of Lights: Film Scores", "concert")
    show(BARCLAYS, "2024-12-21", "Winter Wonder Ice Spectacular", "family")
    show(HARBORVIEW, "2024-05-10", "Harbor Sounds Folk Night", "concert")
    show(HARBORVIEW, "2024-09-07", "Stand-Up at the Pier", "comedy")
    show(HARBORVIEW, "2024-11-23", "Puppet Parade", "family")
    # 2026, January-July (no Liberty games before May)
    nets("2026-01-16", "New York Knicks")
    nets("2026-02-13", "Milwaukee Bucks")
    nets("2026-03-06", "Orlando Magic")
    nets("2026-04-03", "Atlanta Hawks")
    liberty("2026-05-23", "Washington Mystics")
    liberty("2026-07-11", "Minnesota Lynx")
    show(BARCLAYS, "2026-06-20", "Neon Tides Reunion", "concert")
    # August 2026
    liberty("2026-08-01", "Indiana Fever")
    liberty("2026-08-15", "Atlanta Dream")
    liberty("2026-08-29", "Phoenix Mercury")
    show(BARCLAYS, "2026-08-08", "Velvet Static: One Night Only", "concert", "sold_out")
    show(BARCLAYS, "2026-08-22", "Midsummer Hip-Hop Showcase", "concert")
    show(HARBORVIEW, "2026-08-14", "Harbor Sounds Summer Session", "concert")
    show(HARBORVIEW, "2026-08-28", "Family Science Live", "family")
    # September 2026
    liberty("2026-09-05", "Dallas Wings")
    liberty("2026-09-19", "Los Angeles Sparks")
    nets("2026-09-26", "Detroit Pistons", " (Preseason)")
    nets("2026-09-30", "Charlotte Hornets", " (Preseason)")
    show(BARCLAYS, "2026-09-12", "Laugh Track Comedy Tour 2026", "comedy")
    show(BARCLAYS, "2026-09-25", "Orchestra of Lights: Space Odyssey", "concert")
    show(HARBORVIEW, "2026-09-11", "Open Mic Poetry Night", "other", "no_sales")
    show(HARBORVIEW, "2026-09-18", "Indie Showcase", "concert", "all_refunded")
    show(HARBORVIEW, "2026-09-26", "Stand-Up at the Pier 2026", "comedy")
    # Upcoming (after the as-of date)
    nets("2026-10-23", "Cleveland Cavaliers")
    nets("2026-11-06", "Golden State Warriors")
    nets("2026-11-20", "Denver Nuggets")
    nets("2026-12-11", "Los Angeles Lakers")
    show(BARCLAYS, "2026-10-17", "Neon Tides: Afterglow Tour", "concert")
    show(BARCLAYS, "2026-11-14", "Velvet Static Holiday Show", "concert")
    show(BARCLAYS, "2026-12-19", "Winter Wonder Ice Spectacular 2026", "family")
    show(HARBORVIEW, "2026-10-10", "Harbor Sounds Autumn Session", "concert")
    show(HARBORVIEW, "2026-11-07", "Puppet Parade 2026", "family")
    show(HARBORVIEW, "2026-12-05", "Community Craft Fair", "other", "no_sales")
    return sorted(s, key=lambda row: (row[4], row[1]))


def _purchase_date(rng: random.Random, event_day: date) -> date:
    if event_day <= AS_OF:
        # Most tickets sell close to the event; a long tail sells months ahead.
        return event_day - timedelta(days=min(int(rng.expovariate(1 / 25)), 120))
    # Upcoming event: presales between 120 days out and the as-of date.
    start = event_day - timedelta(days=120)
    return start + timedelta(days=rng.randint(0, (AS_OF - start).days))


def generate_dataset(seed: int = SEED) -> Dataset:
    """Build the full synthetic dataset in memory. Same seed, same output."""
    rng = random.Random(seed)
    venues = (Venue(BARCLAYS, "Barclays Center"), Venue(HARBORVIEW, "Harborview Arena"))
    events: list[Event] = []
    tickets: list[Ticket] = []
    tier_mults = [m for m, _ in PRICE_TIERS]
    tier_weights = [w for _, w in PRICE_TIERS]

    for event_id, (venue_id, name, category, home_team, d, profile) in enumerate(_schedule(), start=1):
        event_day = date.fromisoformat(d)
        low, high = CAPACITY_RANGE[category]
        capacity = rng.randint(low, high)
        if venue_id == HARBORVIEW:
            capacity = int(capacity * 0.6)
        events.append(Event(event_id, venue_id, name, category, home_team, d, capacity))

        base = BASE_PRICE_CENTS[home_team or category] * rng.uniform(0.8, 1.4)
        if profile == "no_sales":
            n_sold, n_refunded = 0, 0
        elif profile == "all_refunded":
            n_sold, n_refunded = 0, rng.randint(20, 40)
        else:
            if profile == "sold_out":
                sell_through = 1.0
            elif event_day <= AS_OF:
                sell_through = rng.uniform(0.35, 0.95)
            else:
                sell_through = rng.uniform(0.05, 0.55)
            n_sold = round(capacity * sell_through)
            n_refunded = sum(rng.random() < REFUND_RATE for _ in range(n_sold))

        rows = []
        for status in ["sold"] * n_sold + ["refunded"] * n_refunded:
            tier = rng.choices(tier_mults, tier_weights)[0]
            price_cents = round(base * tier / 100) * 100  # whole dollars
            rows.append((_purchase_date(rng, event_day).isoformat(), price_cents, status))
        rows.sort(key=lambda r: r[0])  # stable sort keeps generation order on ties
        for purchase_date, price_cents, status in rows:
            tickets.append(Ticket(len(tickets) + 1, event_id, purchase_date, price_cents, status))

    return Dataset(venues, tuple(events), tuple(tickets))


def schema_sql() -> str:
    return resources.files("ask_ticketing").joinpath("schema.sql").read_text()


def connect(path: str | os.PathLike) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def write_database(dataset: Dataset, path: str | os.PathLike) -> None:
    """Create a fresh SQLite database at `path` (must not already exist)."""
    if Path(path).exists():
        raise FileExistsError(path)
    conn = connect(path)
    try:
        conn.executescript(schema_sql())
        with conn:
            conn.executemany("INSERT INTO venues VALUES (?, ?)", [(v.venue_id, v.name) for v in dataset.venues])
            conn.executemany(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(e.event_id, e.venue_id, e.name, e.category, e.home_team, e.event_date, e.capacity) for e in dataset.events],
            )
            conn.executemany(
                "INSERT INTO tickets VALUES (?, ?, ?, ?, ?)",
                [(t.ticket_id, t.event_id, t.purchase_date, t.price_cents, t.status) for t in dataset.tickets],
            )
    finally:
        conn.close()


def build_database(path: str | os.PathLike, rebuild: bool = False, seed: int = SEED) -> Path:
    """Generate the database. Refuses to replace an existing file unless rebuild=True."""
    path = Path(path)
    if path.exists() and not rebuild:
        raise FileExistsError(f"{path} already exists; pass --rebuild to replace it or --out for a new file")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    write_database(generate_dataset(seed), tmp)
    os.replace(tmp, path)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the SYNTHETIC Ask Ticketing SQLite database.")
    parser.add_argument("--out", default=str(DEFAULT_DB_PATH), help=f"database path (default: {DEFAULT_DB_PATH})")
    parser.add_argument("--rebuild", action="store_true", help="replace the database if it already exists")
    args = parser.parse_args(argv)
    try:
        path = build_database(args.out, rebuild=args.rebuild)
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    conn = connect(path)
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("venues", "events", "tickets")}
    conn.close()
    print(f"Wrote SYNTHETIC demo data (seed {SEED}, as of {AS_OF}) to {path}: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
