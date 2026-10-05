import json
from pathlib import Path

import pytest
from run_eval import compare, main

# Dev cases only: held-out cases are not inspected before the first held-out evaluation.
CASES = {c["id"]: c for c in json.loads((Path(__file__).parents[1] / "evals" / "cases.json").read_text())["cases"] if c["split"] == "dev"}


ALL_CASES = json.loads((Path(__file__).parents[1] / "evals" / "cases.json").read_text())["cases"]


def test_every_sql_case_has_a_valid_contract():
    from run_eval import FIELD_ALIASES

    for c in ALL_CASES:  # structure only; held-out values are not used here
        if c["kind"] in ("answerable", "no_match"):
            req = c["contract"]["required_fields"]
            assert set(req) <= set(c["expected_columns"]) and set(req) <= set(FIELD_ALIASES), c["id"]
            assert isinstance(c["contract"]["ordered"], bool) and c["contract"]["basis"]


TOP5 = CASES["top5_barclays_revenue_2026_jan_sep"]
EXP = TOP5["expected_rows"]  # columns: event_id, name, event_date, tickets_sold, revenue_cents


def top5_rows(rows=EXP, cols=("name", "revenue_cents")):
    idx = [TOP5["expected_columns"].index(c) for c in cols]
    return [[r[i] for i in idx] for r in rows]


def test_required_fields_with_optional_fields_omitted_pass():
    assert compare(TOP5, "answered", ["name", "revenue_cents"], top5_rows(), False)[0]


def test_aliases_units_and_reordered_columns_pass():
    rows = [[r[4] / 100, r[1], "extra"] for r in EXP]  # dollars, reordered, plus an extra column
    assert compare(TOP5, "answered", ["revenue_usd", "event_name", "note"], rows, False)[0]


def test_missing_required_field_fails():
    ok, reason = compare(TOP5, "answered", ["name", "tickets_sold"], top5_rows(cols=("name", "tickets_sold")), False)
    assert not ok and "missing required field 'revenue_cents'" in reason


def test_unaliased_column_name_is_not_guessed():
    assert not compare(TOP5, "answered", ["name", "money"], top5_rows(), False)[0]


def test_wrong_value_or_entity_fails():
    wrong_value = top5_rows(); wrong_value[2][1] += 100
    wrong_entity = top5_rows(); wrong_entity[0][0] = "Some Other Event"
    assert not compare(TOP5, "answered", ["name", "revenue_cents"], wrong_value, False)[0]
    assert not compare(TOP5, "answered", ["name", "revenue_cents"], wrong_entity, False)[0]


def test_missing_extra_and_duplicate_rows_fail():
    rows = top5_rows()
    assert not compare(TOP5, "answered", ["name", "revenue_cents"], rows[:4], False)[0]
    assert not compare(TOP5, "answered", ["name", "revenue_cents"], rows + [["X", 1]], False)[0]
    unordered = CASES["zero_sales_sep_2026"]
    names = [[r[1]] for r in unordered["expected_rows"]]
    assert compare(unordered, "answered", ["name"], list(reversed(names)), False)[0]  # order not asked
    dup = [names[0], names[0]]
    assert not compare(unordered, "answered", ["name"], dup, False)[0]  # same count, duplicate row


def test_wrong_order_fails_when_ranking_is_asked():
    rows = top5_rows()
    swapped = [rows[1], rows[0]] + rows[2:]
    ok, reason = compare(TOP5, "answered", ["name", "revenue_cents"], swapped, False)
    assert not ok and "order matters" in reason


def test_tie_break_order_is_enforced():
    case = CASES["upcoming_by_remaining_inventory"]
    rows = [[r[1], r[6]] for r in case["expected_rows"]]
    tie = next(i for i in range(len(rows) - 1) if rows[i][1] == rows[i + 1][1])
    flipped = rows[:tie] + [rows[tie + 1], rows[tie]] + rows[tie + 2:]
    assert compare(case, "answered", ["name", "remaining_inventory"], rows, False)[0]
    assert not compare(case, "answered", ["name", "remaining_inventory"], flipped, False)[0]


def test_branch_and_empty_and_truncation_checks():
    assert compare(CASES["ambiguous_last_month_date_basis"], "clarify", [], [], False)[0]
    assert not compare(CASES["ambiguous_last_month_date_basis"], "answered", ["n"], [[1]], False)[0]
    empty_case = {"kind": "no_match", "expected_columns": [], "expected_rows": [], "contract": {"required_fields": [], "ordered": False}}
    assert compare(empty_case, "answered", ["event_id"], [], False)[0]
    assert not compare(empty_case, "answered", ["event_id"], [[1]], False)[0]
    case = CASES["nets_tickets_sep_2026_by_event_date"]
    assert not compare(case, "answered", ["tickets_sold"], case["expected_rows"], True)[0]


def test_average_in_cents_matches_dollars_via_explicit_alias():
    case = {"kind": "answerable", "expected_columns": ["name", "avg_ticket_price_usd"], "expected_rows": [["E", 147.5]],
            "contract": {"required_fields": ["name", "avg_ticket_price_usd"], "ordered": True}}
    assert compare(case, "answered", ["name", "avg_ticket_price_cents"], [["E", 14750.0]], False)[0]
    assert compare(case, "answered", ["event_name", "avg_ticket_price_usd"], [["E", 147.4987]], False)[0]
    assert not compare(case, "answered", ["name", "avg_ticket_price_cents"], [["E", 15000.0]], False)[0]


def test_runner_refuses_without_paid_confirmation():
    with pytest.raises(SystemExit):
        main([])


def test_summary_check_flags_numbers_not_in_rows():
    from run_eval import summary_check

    from ask_ticketing.workflow import AskResult

    r = AskResult("How many tickets in September 2026?", "answered", answer="269 tickets were sold, earning $6,333.50.",
                  columns=["tickets_sold", "revenue_cents"], rows=[[269, 633350]])
    assert summary_check(r) == (True, [])
    r.answer = "270 tickets were sold."
    assert summary_check(r) == (False, [270.0])
    r.status, r.answer = "clarify", "Purchase date or event date?"
    assert summary_check(r)[0]


def test_no_match_contract_accepts_empty_or_verified_zero_support_only():
    case = CASES["no_match_nets_jul_2026"]
    assert compare(case, "answered", ["event_id"], [], False)[0]
    assert compare(case, "answered", ["matching_events", "tickets_sold"], [[0, 0]], False)[0]
    assert not compare(case, "answered", ["tickets_sold"], [[0]], False)[0]  # bare zero: the original defect
    assert not compare(case, "answered", ["matching_events", "tickets_sold"], [[2, 0]], False)[0]
