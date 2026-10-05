import json
from pathlib import Path

from ask_ticketing import prompts

CASES = json.loads((Path(__file__).parents[1] / "evals" / "cases.json").read_text())["cases"]


def test_runtime_prompts_contain_no_evaluation_material():
    runtime = prompts.decide_system_prompt() + prompts.SUMMARIZE_INSTRUCTIONS
    for case in CASES:
        assert case["question"] not in runtime
        assert case["id"] not in runtime
        if case["reference_sql"]:
            assert case["reference_sql"] not in runtime


def test_schema_context_has_tables_but_not_triggers():
    ctx = prompts.schema_context()
    for table in ("venues", "events", "tickets"):
        assert f"CREATE TABLE {table}" in ctx
    assert "TRIGGER" not in ctx and "SYNTHETIC" in ctx


def test_definitions_state_date_basis_and_as_of():
    text = prompts.decide_system_prompt()
    assert "2026-10-01" in text and "purchase date" in text.lower() and "event date" in text.lower()
    assert "status = 'sold'" in text


def test_money_display_formatting_is_done_in_python():
    assert prompts.display_rows(["revenue_cents", "n"], [[123456, 0]]) == [["$1,234.56", 0]]
    assert prompts.display_columns(["revenue_cents", "n"]) == ["revenue_usd", "n"]


def test_runtime_prompts_contain_no_regression_check_questions():
    """The date-basis and provider checks are evaluation material too: their questions must
    never appear in the runtime prompt (no matching evaluation strings)."""
    runtime = prompts.decide_system_prompt() + prompts.SUMMARIZE_INSTRUCTIONS
    evals = Path(__file__).parents[1] / "evals"
    for name in ("date_basis_checks.json", "provider_checks.json"):
        for check in json.loads((evals / name).read_text())["checks"]:
            assert check["question"] not in runtime
            assert check["question"].rstrip("?").lower() not in runtime.lower()
