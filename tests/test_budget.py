import pytest
from fakes import ScriptedModel

from ask_ticketing.budget import BudgetExceededError, SpendGuard
from ask_ticketing.model import ModelOutputError, ModelResponse, ToolSpec
from ask_ticketing.prompts import SUMMARY_MAX_CHARS, summarize_user_prompt
from ask_ticketing.workflow import MAX_QUESTION_CHARS, ask

TOOL = ToolSpec("t", "d", {"type": "object"})
PIN, POUT = 2e-6, 1e-5


class CappedModel(ScriptedModel):
    def output_cap(self, max_tokens):
        return max_tokens + 4000


def guard(model, cap, attempts=1):
    return SpendGuard(model, cap_usd=cap, usd_per_input_token=PIN, usd_per_output_token=POUT, attempts=attempts)


def test_reservation_counts_bytes_overhead_output_cap_and_attempts():
    g = guard(CappedModel(), cap=10, attempts=2)
    cost, input_bound, output_cap = g.reservation(system="x" * 1000, prompt="y" * 500, tool=TOOL, max_tokens=100)
    assert output_cap == 4100 and input_bound > 1500 + 1500
    assert cost == pytest.approx(2 * (input_bound * PIN + 4100 * POUT))


def test_refuses_before_dispatch_when_next_call_could_exceed_cap():
    model = CappedModel({"ok": True})
    g = guard(model, cap=0.01)  # one worst-case call is ~$0.05
    with pytest.raises(BudgetExceededError):
        g.call_tool(system="s", prompt="p", tool=TOOL, max_tokens=100)
    assert model.calls == [] and g.spent_usd == 0  # nothing was sent


def test_charges_actual_on_success_and_full_reservation_on_failure():
    g = guard(CappedModel({"ok": True}, ModelOutputError("bad")), cap=1.0)
    g.call_tool(system="s", prompt="p", tool=TOOL, max_tokens=100)
    assert g.spent_usd == pytest.approx(10 * PIN + 5 * POUT)  # ScriptedModel reports 10 in / 5 out
    reserve = g.reservation(system="s", prompt="p", tool=TOOL, max_tokens=100)[0]
    with pytest.raises(ModelOutputError):
        g.call_tool(system="s", prompt="p", tool=TOOL, max_tokens=100)
    assert g.spent_usd == pytest.approx(10 * PIN + 5 * POUT + reserve)
    assert g.calls[0]["bound_held"] is True and g.calls[1]["error"] == "ModelOutputError"


class ExpensiveModel(CappedModel):
    """Reports usage at its worst case: input near the byte bound, output at the cap."""

    def call_tool(self, *, system, prompt, tool, max_tokens):
        self.calls.append(tool.name)
        return ModelResponse({"ok": True}, input_tokens=1700, output_tokens=self.output_cap(max_tokens))


def test_total_spend_never_exceeds_cap_across_many_calls():
    model = ExpensiveModel()
    g = guard(model, cap=0.2)
    with pytest.raises(BudgetExceededError):
        for _ in range(100):
            g.call_tool(system="s", prompt="p", tool=TOOL, max_tokens=100)
    assert 0 < len(model.calls) < 100 and g.spent_usd <= 0.2


def test_workflow_reports_budget_stop_as_error(db_path):
    r = ask("How many tickets?", guard(CappedModel(), cap=0.0), db_path)
    assert (r.status, r.error_kind) == ("error", "budget")


def test_question_length_is_bounded(db_path):
    model = ScriptedModel()
    r = ask("x" * (MAX_QUESTION_CHARS + 1), model, db_path)
    assert (r.status, r.error_kind) == ("error", "question_too_long") and model.calls == []


def test_summary_payload_is_bounded_by_rows_and_chars():
    wide = [[i, "N" * 500] for i in range(200)]
    text = summarize_user_prompt("q", [], ["id", "name"], wide, truncated=True)
    assert len(text) <= SUMMARY_MAX_CHARS and '"result_truncated": true' in text
    small = summarize_user_prompt("q", [], ["n"], [[1], [2]], truncated=False)
    assert '"rows_shown": 2' in small and '"result_truncated": false' in small
