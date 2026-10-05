import json
from collections import Counter
from pathlib import Path

import pytest

import ground_truth
from ask_ticketing.data import Dataset, Event, Ticket, Venue, connect, schema_sql

CASES = json.loads((Path(__file__).parents[1] / "evals" / "cases.json").read_text())["cases"]
BY_ID = {c["id"]: c for c in CASES}
WITH_SQL = [c for c in CASES if c["reference_sql"]]


def _rows(conn, sql):
    cur = conn.execute(sql)
    return [d[0] for d in cur.description], [list(r) for r in cur.fetchall()]


def _same(a, b):
    assert len(a) == len(b)
    for ra, rb in zip(a, b):
        assert ra == pytest.approx(rb, abs=0.005) if any(isinstance(x, float) for x in ra + rb) else ra == rb


def test_case_breakdown():
    assert len(CASES) == 13 and len(BY_ID) == 13
    assert Counter(c["kind"] for c in CASES) == {"answerable": 8, "ambiguous": 2, "unsupported": 1, "no_match": 2}
    assert Counter(c["split"] for c in CASES) == {"dev": 9, "held_out": 4}
    held_out_kinds = {c["kind"] for c in CASES if c["split"] == "held_out"}
    assert len(held_out_kinds) >= 3, "held-out set should span several behaviors"
    for c in CASES:
        assert c["question"] and c["expected_behavior"]
        assert (c["reference_sql"] is None) == (c["kind"] in ("ambiguous", "unsupported"))


def test_stored_expectations_match_independent_python():
    computed = ground_truth.compute_all()
    assert set(computed) == {c["id"] for c in WITH_SQL}
    for case_id, (cols, rows) in computed.items():
        assert BY_ID[case_id]["expected_columns"] == cols, case_id
        assert BY_ID[case_id]["expected_rows"] == rows, case_id


@pytest.mark.parametrize("case", WITH_SQL, ids=lambda c: c["id"])
def test_reference_sql_matches_expected_rows(db, case):
    cols, rows = _rows(db, case["reference_sql"])
    assert cols == case["expected_columns"]
    _same(rows, case["expected_rows"])


def test_no_match_case_really_has_no_events(dataset):
    assert BY_ID["no_match_liberty_jan_2026"]["expected_rows"] == []
    assert not [e for e in dataset.events if e.home_team == "New York Liberty" and e.event_date.startswith("2026-01")]


# --- Hand-checked fixture: tiny database with answers worked out by hand. ---

HAND_EVENTS = [
    (1, 1, "Nets Sep Game", "basketball", "Brooklyn Nets", "2026-09-10", 10),
    (2, 1, "Nets Oct Game", "basketball", "Brooklyn Nets", "2026-10-05", 4),
    (3, 1, "Liberty Sep Game", "basketball", "New York Liberty", "2026-09-20", 10),
    (4, 2, "Empty Show", "other", None, "2026-09-15", 5),
    (5, 2, "Refund Show", "concert", None, "2026-09-16", 5),
    (6, 1, "Concert 2024 A", "concert", None, "2024-06-01", 10),
    (7, 1, "Comedy 2024 B", "comedy", None, "2024-07-01", 10),
    (8, 2, "Pricey Harborview 2024", "concert", None, "2024-05-01", 10),
    (9, 1, "Upcoming Show", "concert", None, "2026-11-01", 4),
]
HAND_TICKETS = [
    (1, "2026-08-20", 5000, "sold"), (1, "2026-09-01", 5000, "sold"), (1, "2026-09-09", 7000, "sold"),
    (1, "2026-09-02", 9000, "refunded"),
    (2, "2026-09-15", 8000, "sold"), (2, "2026-09-30", 8000, "sold"), (2, "2026-10-01", 8000, "sold"),
    (3, "2026-09-05", 3000, "sold"),
    (5, "2026-09-01", 4000, "refunded"), (5, "2026-09-02", 4000, "refunded"),
    (6, "2024-05-01", 10000, "sold"), (6, "2024-05-02", 2000, "sold"),  # avg $60
    (7, "2024-06-01", 5000, "sold"), (7, "2024-06-01", 5000, "sold"), (7, "2024-06-01", 5000, "sold"),
    (7, "2024-06-02", 20000, "refunded"),  # would make avg $87.50 (and win) if refunds counted
    (8, "2024-04-01", 30000, "sold"),  # wrong venue for the Barclays question
]
HAND_EXPECTED = {
    "nets_tickets_sep_2026_by_event_date": [[3]],
    # Sep purchases: 5000 + 7000 (event 1) + 8000 + 8000 (event 2, October game) + 3000 (event 3)
    "purchases_sep_2026_any_event": [[5, 31000]],
    "top5_barclays_revenue_2026_jan_sep": [[1, "Nets Sep Game", "2026-09-10", 3, 17000], [3, "Liberty Sep Game", "2026-09-20", 1, 3000]],
    "nets_vs_liberty_2026_jan_sep": [["Brooklyn Nets", 1, 3, 17000], ["New York Liberty", 1, 1, 3000]],
    "upcoming_by_remaining_inventory": [
        [9, "Upcoming Show", "Barclays Center", "2026-11-01", 4, 0, 4],
        [2, "Nets Oct Game", "Barclays Center", "2026-10-05", 4, 3, 1],
    ],
    "category_revenue_2026_jan_sep": [["basketball", 4, 20000], ["concert", 0, 0], ["other", 0, 0]],
    "barclays_2024_highest_avg_price": [[6, "Concert 2024 A", "2024-06-01", 2, 60.0]],
    "zero_sales_sep_2026": [[4, "Empty Show", "Harborview Arena", "2026-09-15", 0], [5, "Refund Show", "Harborview Arena", "2026-09-16", 2]],
    "no_match_liberty_jan_2026": [],
    "no_match_nets_jul_2026": [],
}


@pytest.fixture(scope="module")
def hand_db(tmp_path_factory):
    conn = connect(tmp_path_factory.mktemp("hand") / "hand.db")
    conn.executescript(schema_sql())
    conn.executemany("INSERT INTO venues VALUES (?, ?)", [(1, "Barclays Center"), (2, "Harborview Arena")])
    conn.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)", HAND_EVENTS)
    conn.executemany("INSERT INTO tickets (event_id, purchase_date, price_cents, status) VALUES (?, ?, ?, ?)", HAND_TICKETS)
    conn.commit()
    yield conn
    conn.close()


def test_hand_fixture_covers_every_sql_case():
    assert set(HAND_EXPECTED) == {c["id"] for c in WITH_SQL}


@pytest.mark.parametrize("case_id", sorted(HAND_EXPECTED))
def test_reference_sql_on_hand_fixture(hand_db, case_id):
    _, rows = _rows(hand_db, BY_ID[case_id]["reference_sql"])
    _same(rows, HAND_EXPECTED[case_id])


def test_python_ground_truth_on_hand_fixture():
    """The independent Python calculators also reproduce the hand-worked answers."""
    ds = Dataset(
        venues=(Venue(1, "Barclays Center"), Venue(2, "Harborview Arena")),
        events=tuple(Event(*e) for e in HAND_EVENTS),
        tickets=tuple(Ticket(i, *t) for i, t in enumerate(HAND_TICKETS, start=1)),
    )
    for case_id, (_, rows) in ground_truth.compute_all(ds).items():
        _same(rows, HAND_EXPECTED[case_id])
