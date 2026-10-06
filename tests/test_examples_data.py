"""The UI's example questions must be answerable from the existing synthetic data.

Expected results are computed in plain Python from generate_dataset() and compared with
reference SQL run on the built database. This verifies data support only, not live NL-to-SQL
performance for the example wording (no model is involved). Reference SQL lives here, in
tests; nothing is hardcoded into runtime code.
"""

import math

UPCOMING_SQL = """
SELECT e.name, e.event_date, v.name AS venue,
       COUNT(t.ticket_id) AS tickets_sold,
       e.capacity - COUNT(t.ticket_id) AS remaining_capacity,
       100.0 * COUNT(t.ticket_id) / e.capacity AS sell_through_pct,
       AVG(t.price_cents) AS avg_ticket_price_cents
FROM events e
JOIN venues v ON v.venue_id = e.venue_id
LEFT JOIN tickets t ON t.event_id = e.event_id AND t.status = 'sold'
WHERE e.event_date > '2026-10-01'
GROUP BY e.event_id
ORDER BY e.event_date, e.event_id
"""

VENUE_REVENUE_SQL = """
SELECT v.name AS venue, SUM(t.price_cents) AS revenue_cents
FROM events e
JOIN venues v ON v.venue_id = e.venue_id
JOIN tickets t ON t.event_id = e.event_id AND t.status = 'sold'
WHERE e.event_date BETWEEN '2026-08-01' AND '2026-08-31'
GROUP BY v.venue_id
ORDER BY revenue_cents DESC, venue
"""


def expected_upcoming(dataset):
    venue = {v.venue_id: v.name for v in dataset.venues}
    rows = []
    for e in sorted((e for e in dataset.events if e.event_date > "2026-10-01"), key=lambda e: (e.event_date, e.event_id)):
        sold = [t.price_cents for t in dataset.tickets if t.event_id == e.event_id and t.status == "sold"]
        rows.append([e.name, e.event_date, venue[e.venue_id], len(sold), e.capacity - len(sold),
                     100.0 * len(sold) / e.capacity, (sum(sold) / len(sold)) if sold else None])
    return rows


def expected_venue_revenue(dataset):
    venue = {v.venue_id: v.name for v in dataset.venues}
    totals: dict[str, int] = {}
    for e in dataset.events:
        if "2026-08-01" <= e.event_date <= "2026-08-31":
            totals[venue[e.venue_id]] = totals.get(venue[e.venue_id], 0) + sum(
                t.price_cents for t in dataset.tickets if t.event_id == e.event_id and t.status == "sold")
    return [[name, cents] for name, cents in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))]


def same(a, b):
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) or isinstance(b, float):
        return math.isclose(a, b, rel_tol=1e-9)
    return a == b


def test_upcoming_event_overview_is_supported_and_matches_plain_python(db, dataset):
    got = [list(r) for r in db.execute(UPCOMING_SQL).fetchall()]
    want = expected_upcoming(dataset)
    assert len(got) == len(want) == 10
    assert all(same(g, w) for gr, wr in zip(got, want) for g, w in zip(gr, wr))
    no_sales = [r for r in want if r[3] == 0]
    assert len(no_sales) == 1 and no_sales[0][6] is None  # unavailable average, never $0


def test_revenue_by_venue_august_is_supported_and_matches_plain_python(db, dataset):
    got = [list(r) for r in db.execute(VENUE_REVENUE_SQL).fetchall()]
    assert got == expected_venue_revenue(dataset) == [["Barclays Center", 6347200], ["Harborview Arena", 1068000]]


def test_nets_example_matches_the_verified_evaluation_value(dataset):
    nets = {e.event_id for e in dataset.events if e.home_team == "Brooklyn Nets" and "2026-09-01" <= e.event_date <= "2026-09-30"}
    assert sum(1 for t in dataset.tickets if t.event_id in nets and t.status == "sold") == 269
