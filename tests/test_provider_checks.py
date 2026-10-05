"""The frozen provider-check batch (evals/provider_checks.json) must stay consistent with
the data and the dev cases. Offline: no model calls."""

import json
from pathlib import Path

from run_eval import compare

from ask_ticketing.workflow import NO_MATCH_ANSWER, ask

EVALS = Path(__file__).parents[1] / "evals"
FROZEN = json.loads((EVALS / "provider_checks.json").read_text())
CHECKS = {c["id"]: c for c in FROZEN["checks"]}
CASES = {c["id"]: c for c in json.loads((EVALS / "cases.json").read_text())["cases"]}


def test_batch_covers_the_four_behaviours_and_no_held_out_case():
    assert [c["id"] for c in FROZEN["checks"]] == [
        "nets_tickets_sep_2026_by_event_date", "ambiguous_last_month_date_basis", "no_match_nets_jul_2026", "legit_zero_indie_showcase"]
    assert {c["kind"] for c in FROZEN["checks"]} == {"answerable", "ambiguous", "no_match"}
    assert all(c["split"] != "held_out" for c in FROZEN["checks"])
    assert CHECKS["legit_zero_indie_showcase"]["question"] == "How many tickets were sold for Indie Showcase? Exclude refunded tickets."


def test_reused_checks_are_exact_copies_of_dev_cases():
    for cid in ("nets_tickets_sep_2026_by_event_date", "ambiguous_last_month_date_basis", "no_match_nets_jul_2026"):
        assert CHECKS[cid] == CASES[cid] and CASES[cid]["split"] == "dev"


def test_indie_showcase_is_a_legitimate_zero_in_plain_python(dataset):
    events = [e for e in dataset.events if e.name == "Indie Showcase"]
    assert len(events) == 1  # matching_events = 1
    tickets = [t for t in dataset.tickets if t.event_id == events[0].event_id]
    assert tickets and sum(t.status == "sold" for t in tickets) == 0  # tickets exist, all refunded
    assert CHECKS["legit_zero_indie_showcase"]["expected_rows"] == [[1, 0]]


def test_indie_showcase_reference_sql_agrees(db):
    sql = CHECKS["legit_zero_indie_showcase"]["reference_sql"]
    assert [list(r) for r in db.execute(sql).fetchall()] == [[1, 0]]


def test_scorer_separates_legitimate_zero_from_no_match():
    zero, no_match = CHECKS["legit_zero_indie_showcase"], CHECKS["no_match_nets_jul_2026"]
    assert compare(zero, "answered", ["matching_events", "tickets_sold"], [[1, 0]], False)[0]
    assert not compare(zero, "answered", ["matching_events", "tickets_sold"], [[0, 0]], False)[0]  # "no events" is wrong here
    assert not compare(zero, "answered", ["tickets_sold"], [[0]], False)[0]  # the support count is required
    assert compare(no_match, "answered", ["matching_events", "tickets_sold"], [[0, 0]], False)[0]


def test_workflow_answers_a_legitimate_zero_without_no_match_wording(db_path):
    from fakes import ScriptedModel

    sql = CHECKS["legit_zero_indie_showcase"]["reference_sql"]
    model = ScriptedModel({"action": "sql", "sql": sql, "assumptions": [], "event_filtered_total": True},
                          {"answer": "Indie Showcase sold 0 tickets (1 event matched)."})
    r = ask(CHECKS["legit_zero_indie_showcase"]["question"], model, db_path)
    assert r.status == "answered" and r.rows == [[1, 0]] and r.answer != NO_MATCH_ANSWER
