import sqlite3
from collections import Counter

import pytest

from ask_ticketing.data import AS_OF, build_database, connect, generate_dataset, schema_sql


def test_row_counts_are_modest(db):
    counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("venues", "events", "tickets")}
    assert counts["venues"] == 2
    assert 30 <= counts["events"] <= 80
    assert 2000 <= counts["tickets"] <= 8000


def test_referential_integrity(db):
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_non_refunded_tickets_never_exceed_capacity(dataset):
    sold = Counter(t.event_id for t in dataset.tickets if t.status == "sold")
    for e in dataset.events:
        assert sold[e.event_id] <= e.capacity, e.name


def test_purchase_dates_on_or_before_event_and_as_of(dataset):
    event_date = {e.event_id: e.event_date for e in dataset.events}
    for t in dataset.tickets:
        assert t.purchase_date <= event_date[t.event_id]
        assert t.purchase_date <= AS_OF.isoformat()


def test_regeneration_is_deterministic(tmp_path):
    assert generate_dataset() == generate_dataset()
    a = connect(build_database(tmp_path / "a.db"))
    b = connect(build_database(tmp_path / "b.db"))
    assert list(a.iterdump()) == list(b.iterdump())


def test_existing_database_not_overwritten_without_rebuild(tmp_path):
    path = tmp_path / "t.db"
    path.write_text("keep me")
    with pytest.raises(FileExistsError):
        build_database(path)
    assert path.read_text() == "keep me"
    build_database(path, rebuild=True)
    assert connect(path).execute("SELECT COUNT(*) FROM venues").fetchone()[0] == 2


def test_dataset_covers_required_scenarios(dataset):
    names = {e.name: e for e in dataset.events}
    by_event = {e.event_id: [t for t in dataset.tickets if t.event_id == e.event_id] for e in dataset.events}
    sold = lambda e: sum(t.status == "sold" for t in by_event[e.event_id])
    # Sold-out event: non-refunded tickets exactly equal capacity.
    sold_out = names["Velvet Static: One Night Only"]
    assert sold(sold_out) == sold_out.capacity
    # Zero sales, no tickets at all.
    assert by_event[names["Open Mic Poetry Night"].event_id] == []
    # Zero sales, but tickets exist and were all refunded.
    refunded_only = by_event[names["Indie Showcase"].event_id]
    assert refunded_only and all(t.status == "refunded" for t in refunded_only)
    # Barclays plus fictional comparison venue; Nets and Liberty home events; 2024 and upcoming events.
    assert {v.name for v in dataset.venues} == {"Barclays Center", "Harborview Arena"}
    assert {e.home_team for e in dataset.events} >= {"Brooklyn Nets", "New York Liberty"}
    assert any(e.event_date.startswith("2024") for e in dataset.events)
    assert any(e.event_date > AS_OF.isoformat() for e in dataset.events)
    assert not any(e.event_date == AS_OF.isoformat() for e in dataset.events)  # no boundary ambiguity
    # Some purchases fall in a different month from their event.
    event_month = {e.event_id: e.event_date[:7] for e in dataset.events}
    assert any(t.purchase_date[:7] != event_month[t.event_id] for t in dataset.tickets)


def test_refunds_excluded_from_counts_and_revenue(db, dataset):
    sold = [t for t in dataset.tickets if t.status == "sold"]
    refunded = [t for t in dataset.tickets if t.status == "refunded"]
    assert refunded, "dataset should contain refunds"
    n, rev = db.execute("SELECT COUNT(*), SUM(price_cents) FROM tickets WHERE status = 'sold'").fetchone()
    assert (n, rev) == (len(sold), sum(t.price_cents for t in sold))
    assert n == len(dataset.tickets) - len(refunded)


def test_purchase_date_and_event_date_bases_differ(dataset):
    """September 2026 by purchase date and by event date are different ticket sets."""
    event_date = {e.event_id: e.event_date for e in dataset.events}
    sold = [t for t in dataset.tickets if t.status == "sold"]
    by_purchase = {t.ticket_id for t in sold if t.purchase_date.startswith("2026-09")}
    by_event = {t.ticket_id for t in sold if event_date[t.event_id].startswith("2026-09")}
    assert by_purchase - by_event, "some September purchases are for events in other months"
    assert by_event - by_purchase, "some September-event tickets were bought earlier"


def test_weighted_average_differs_from_mean_of_event_means(db):
    """Average ticket price is ticket-weighted; averaging per-event averages is a different number."""
    where = "home_team = 'Brooklyn Nets' AND event_date LIKE '2024-%' AND status = 'sold'"
    weighted = db.execute(f"SELECT AVG(price_cents) FROM tickets JOIN events USING (event_id) WHERE {where}").fetchone()[0]
    rows = db.execute(f"SELECT SUM(price_cents), COUNT(*) FROM tickets JOIN events USING (event_id) WHERE {where} GROUP BY event_id").fetchall()
    assert weighted == pytest.approx(sum(s for s, _ in rows) / sum(n for _, n in rows))
    assert weighted != pytest.approx(sum(s / n for s, n in rows) / len(rows))


@pytest.fixture
def empty_db(tmp_path):
    conn = connect(tmp_path / "empty.db")
    conn.executescript(schema_sql())
    conn.execute("INSERT INTO venues VALUES (1, 'Test Venue')")
    conn.execute("INSERT INTO events VALUES (1, 1, 'Tiny Show', 'concert', NULL, '2026-09-10', 1)")
    conn.commit()
    yield conn
    conn.close()


def test_schema_rejects_capacity_overflow(empty_db):
    empty_db.execute("INSERT INTO tickets VALUES (1, 1, '2026-09-01', 1000, 'sold')")
    empty_db.execute("INSERT INTO tickets VALUES (2, 1, '2026-09-01', 1000, 'refunded')")  # refunds don't use capacity
    with pytest.raises(sqlite3.IntegrityError, match="capacity"):
        empty_db.execute("INSERT INTO tickets VALUES (3, 1, '2026-09-02', 1000, 'sold')")
    with pytest.raises(sqlite3.IntegrityError, match="capacity"):
        empty_db.execute("UPDATE tickets SET status = 'sold' WHERE ticket_id = 2")


@pytest.mark.parametrize(
    "row",
    [
        (9, 99, "2026-09-01", 1000, "refunded"),  # unknown event
        (9, 1, "2026-09-11", 1000, "refunded"),  # purchased after event
        (9, 1, "2026-13-01", 1000, "refunded"),  # invalid date
        (9, 1, "2026-09-01", 0, "refunded"),  # non-positive price
        (9, 1, "2026-09-01", 10.5, "refunded"),  # money must be integer cents
        (9, 1, "2026-09-01", 1000, "pending"),  # unknown status
    ],
)
def test_schema_rejects_invalid_tickets(empty_db, row):
    with pytest.raises(sqlite3.IntegrityError):
        empty_db.execute("INSERT INTO tickets VALUES (?, ?, ?, ?, ?)", row)
