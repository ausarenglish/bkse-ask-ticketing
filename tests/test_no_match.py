"""No-match vs. zero-sales semantics, on a tiny independent fixture with hand-known answers.

Fixture (all Harborview concerts in September 2026, capacity 10):
  E1 "No Tickets Show"   - no ticket rows at all
  E2 "Refunded Show"     - 2 tickets, both refunded
  E3 "Selling Show"      - 2 sold ($10 + $20) and 1 refunded ($50)
No comedy events exist.
"""

import pytest
from fakes import ScriptedModel

from ask_ticketing.data import connect, schema_sql
from ask_ticketing.sql_guard import run_query
from ask_ticketing.workflow import NO_MATCH_ANSWER, ask

PATTERN = (
    "SELECT COUNT(DISTINCT e.event_id) AS matching_events, COUNT(t.ticket_id) AS tickets_sold, "
    "COALESCE(SUM(t.price_cents), 0) AS revenue_cents "
    "FROM events e LEFT JOIN tickets t ON t.event_id = e.event_id AND t.status = 'sold' WHERE {where}"
)
# The defect pattern: the sold filter in WHERE (inner join) removes events that have no sold tickets.
NAIVE = (
    "SELECT COUNT(DISTINCT e.event_id) AS matching_events, COUNT(t.ticket_id) AS tickets_sold "
    "FROM tickets t JOIN events e ON e.event_id = t.event_id WHERE t.status = 'sold' AND {where}"
)
FILTERS = {
    "no_matching_events": ("e.category = 'comedy'", [[0, 0, 0]]),
    "match_no_ticket_rows": ("e.name = 'No Tickets Show'", [[1, 0, 0]]),
    "match_only_refunded": ("e.name = 'Refunded Show'", [[1, 0, 0]]),
    "match_with_sales": ("e.name = 'Selling Show'", [[1, 2, 3000]]),
    "all_concerts": ("e.category = 'concert'", [[3, 2, 3000]]),
}


@pytest.fixture(scope="module")
def fixture_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("nomatch") / "fixture.db"
    conn = connect(path)
    conn.executescript(schema_sql())
    conn.execute("INSERT INTO venues VALUES (1, 'Harborview Arena')")
    conn.executemany(
        "INSERT INTO events VALUES (?, 1, ?, 'concert', NULL, ?, 10)",
        [(1, "No Tickets Show", "2026-09-05"), (2, "Refunded Show", "2026-09-12"), (3, "Selling Show", "2026-09-19")],
    )
    conn.executemany(
        "INSERT INTO tickets (event_id, purchase_date, price_cents, status) VALUES (?, ?, ?, ?)",
        [(2, "2026-09-01", 4000, "refunded"), (2, "2026-09-02", 4000, "refunded"),
         (3, "2026-09-01", 1000, "sold"), (3, "2026-09-02", 2000, "sold"), (3, "2026-09-03", 5000, "refunded")],
    )
    conn.commit()
    conn.close()
    return path


@pytest.mark.parametrize("name", FILTERS)
def test_left_join_pattern_counts_events_independently_of_sales(fixture_db, name):
    where, expected = FILTERS[name]
    assert run_query(fixture_db, PATTERN.format(where=where)).rows == expected


def test_sold_filter_in_where_loses_events_without_sales(fixture_db):
    """Documents the defect: the naive form reports 0 matching events for a real event."""
    assert run_query(fixture_db, NAIVE.format(where="e.name = 'Refunded Show'")).rows == [[0, 0]]


def total(where, assumptions=("Event-date basis.",)):
    return {"action": "sql", "sql": PATTERN.format(where=where), "event_filtered_total": True, "assumptions": list(assumptions)}


def test_no_matching_events_uses_deterministic_wording_without_summary_call(fixture_db):
    model = ScriptedModel(total("e.category = 'comedy'"))
    r = ask("How many comedy tickets were sold?", model, fixture_db)
    assert r.status == "answered" and r.rows == [[0, 0, 0]] and r.matching_events == 0
    assert r.answer == NO_MATCH_ANSWER and "No events matched" in r.answer
    assert model.tools_called == ["decide"]


@pytest.mark.parametrize("name", ["match_no_ticket_rows", "match_only_refunded"])
def test_real_event_with_zero_sales_keeps_the_legitimate_zero(fixture_db, name):
    where, expected = FILTERS[name]
    model = ScriptedModel(total(where), {"answer": "That event exists but sold 0 tickets."})
    r = ask("How many tickets were sold?", model, fixture_db)
    assert r.rows == expected and r.matching_events == 1
    assert r.answer == "That event exists but sold 0 tickets." and model.tools_called == ["decide", "answer"]
    assert '"matching_events"' in model.calls[1]["prompt"]


def test_matching_events_with_sales(fixture_db):
    model = ScriptedModel(total("e.name = 'Selling Show'"), {"answer": "2 tickets, $30.00."})
    r = ask("How did Selling Show do?", model, fixture_db)
    assert r.rows == [[1, 2, 3000]] and r.matching_events == 1 and r.answer == "2 tickets, $30.00."


def test_missing_support_count_is_repaired_once(fixture_db):
    no_support = {"action": "sql", "sql": "SELECT COUNT(*) AS tickets_sold FROM tickets WHERE status = 'sold'",
                  "event_filtered_total": True, "assumptions": []}
    model = ScriptedModel(no_support, total("e.category = 'concert'"), {"answer": "3 events, 2 tickets."})
    r = ask("How many concert tickets were sold?", model, fixture_db)
    assert (r.status, r.repairs, r.matching_events) == ("answered", 1, 3)
    assert "matching_events" in model.calls[1]["prompt"]  # repair feedback names the missing field


def test_missing_support_count_twice_is_an_error(fixture_db):
    no_support = {"action": "sql", "sql": "SELECT COUNT(*) AS tickets_sold FROM tickets", "event_filtered_total": True, "assumptions": []}
    r = ask("q", ScriptedModel(no_support, no_support), fixture_db)
    assert (r.status, r.error_kind, r.repairs) == ("error", "invalid_result", 1)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT NULL AS matching_events, 0 AS tickets_sold",
        "SELECT -1 AS matching_events, 0 AS tickets_sold",
        "SELECT 0 AS matching_events, 5 AS tickets_sold",  # sales without matching events is inconsistent
    ],
)
def test_invalid_support_count_triggers_repair(fixture_db, sql):
    bad = {"action": "sql", "sql": sql, "event_filtered_total": True, "assumptions": []}
    model = ScriptedModel(bad, total("e.category = 'concert'"), {"answer": "ok"})
    r = ask("q", model, fixture_db)
    assert (r.status, r.repairs) == ("answered", 1)


def test_unscoped_zero_aggregate_is_not_called_a_no_match(fixture_db):
    """A purchase-date total across all events is not event-scoped: its zero stays a zero."""
    model = ScriptedModel(
        {"action": "sql", "sql": "SELECT COUNT(*) AS tickets_sold FROM tickets WHERE status = 'sold' AND purchase_date > '2026-10-01'",
         "event_filtered_total": False, "assumptions": []},
        {"answer": "0 tickets were purchased after October 1."},
    )
    r = ask("Tickets purchased after Oct 1?", model, fixture_db)
    assert r.rows == [[0]] and r.matching_events is None and r.answer != NO_MATCH_ANSWER
