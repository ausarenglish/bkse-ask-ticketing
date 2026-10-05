"""Offline tests for the date-basis regression scorer (no model calls)."""

import json
from pathlib import Path

from run_date_basis import score_date_basis

CHECKS = {c["id"]: c for c in json.loads((Path(__file__).parents[1] / "evals" / "date_basis_checks.json").read_text())["checks"]}


def test_answered_check_needs_value_and_the_right_date_column_only():
    c = CHECKS["event_period_nets_sep"]
    sql_event = "SELECT COUNT(*) FROM tickets t JOIN events e USING (event_id) WHERE e.event_date BETWEEN '2026-09-01' AND '2026-09-30'"
    assert score_date_basis(c, "answered", [[269]], sql_event)[0]
    assert not score_date_basis(c, "answered", [[308]], sql_event)[0]  # the other basis' value
    assert not score_date_basis(c, "answered", [[269]], sql_event.replace("event_date", "purchase_date"))[0]
    assert not score_date_basis(c, "clarify", [], None)[0]


def test_clarify_check_fails_when_the_app_answers():
    c = CHECKS["unresolved_sold_last_month"]
    assert score_date_basis(c, "clarify", [], None)[0]
    assert not score_date_basis(c, "answered", [[668]], "SELECT ... purchase_date ...")[0]


def test_frozen_ground_truth_is_unchanged():
    assert {k: (c["expected_branch"], c["expected_value"]) for k, c in CHECKS.items()} == {
        "event_period_nets_sep": ("answered", 269), "event_period_nets_sep_paraphrase": ("answered", 269),
        "purchase_period_sep": ("answered", 668), "purchase_period_sep_paraphrase": ("answered", 668),
        "unresolved_sold_last_month": ("clarify", None), "unresolved_sales_sep_paraphrase": ("clarify", None)}
