"""Question -> (clarify | unsupported | SQL -> safe execution -> answer), as a small LangGraph.

Depends only on the `ModelProvider` port. At most one repair attempt is made,
shared between malformed model output and repairable SQL errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ask_ticketing import prompts
from ask_ticketing.budget import BudgetExceededError
from ask_ticketing.model import (
    ModelAccessError,
    ModelAuthError,
    ModelConfigError,
    ModelError,
    ModelOutputError,
    ModelProvider,
    ModelUnavailableError,
)
from ask_ticketing.sql_guard import DatabaseMissingError, QueryError, QueryLimitError, UnsafeQueryError, run_query

MAX_REPAIRS = 1
MAX_QUESTION_CHARS = 1000  # bounds model input (and cost) per question
DECIDE_MAX_TOKENS = 1500
SUMMARY_MAX_TOKENS = 400
SYNTHETIC_NOTICE = "Synthetic demo data (as of 2026-10-01)."
SUPPORT_COLUMN = "matching_events"
NO_MATCH_ANSWER = "No events matched this question's event filters in this synthetic dataset, so there are no tickets or revenue to report."


class Decision(BaseModel):
    """Validated form of the model's `decide` tool output."""

    model_config = ConfigDict(extra="forbid")
    action: Literal["sql", "clarify", "unsupported"]
    sql: str | None = None
    message: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    event_filtered_total: bool = False  # SQL must then return a matching_events support count

    @model_validator(mode="after")
    def _consistent(self):
        if self.action == "sql" and not (self.sql or "").strip():
            raise ValueError("action 'sql' requires non-empty 'sql'")
        if self.action != "sql" and not (self.message or "").strip():
            raise ValueError(f"action '{self.action}' requires 'message'")
        return self


class Summary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1)


Status = Literal["answered", "clarify", "unsupported", "error"]


@dataclass
class AskResult:
    question: str
    status: Status
    answer: str | None = None  # answer, clarifying question, or unsupported explanation
    sql: str | None = None  # exactly what was executed
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    truncated: bool = False
    assumptions: list[str] = field(default_factory=list)
    error_kind: str | None = None
    error: str | None = None
    repairs: int = 0
    matching_events: int | None = None  # verified support count, for event-filtered totals only
    input_tokens: int = 0
    output_tokens: int = 0
    notice: str = SYNTHETIC_NOTICE


class State(TypedDict, total=False):
    question: str
    decision: Decision
    feedback: str  # set when a repair is pending
    repairs: int
    result: AskResult


def _error_kind(exc: Exception) -> str:
    return {
        ModelConfigError: "config",
        ModelAuthError: "auth",
        ModelAccessError: "access",
        ModelUnavailableError: "unavailable",
        ModelOutputError: "malformed_output",
        UnsafeQueryError: "unsafe_sql",
        QueryLimitError: "limit",
        QueryError: "invalid_sql",
        DatabaseMissingError: "database_missing",
        BudgetExceededError: "budget",
    }.get(type(exc), "provider_request")


def check_support(columns: list[str], rows: list[list[Any]]) -> tuple[int | None, str | None]:
    """Validate the matching_events support count of an event-filtered total.
    Returns (total matching events, problem); a problem triggers the bounded repair."""
    names = [c.lower() for c in columns]
    if SUPPORT_COLUMN not in names:
        return None, f"the result has no {SUPPORT_COLUMN} column; event-filtered totals must return COUNT(DISTINCT e.event_id) AS {SUPPORT_COLUMN}, counted independently of ticket status (LEFT JOIN, ticket conditions in the JOIN)"
    i = names.index(SUPPORT_COLUMN)
    total = 0
    for row in rows:
        value = row[i]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None, f"{SUPPORT_COLUMN} must be a non-negative integer, got {value!r}"
        if value == 0 and any(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for j, v in enumerate(row) if j != i):
            return None, f"{SUPPORT_COLUMN} is 0 but the same row reports positive ticket values; count events independently of ticket status (LEFT JOIN, ticket conditions in the JOIN)"
        total += value
    return total, None


def build_graph(model: ModelProvider, db_path: str | Path, *, max_rows: int = 200, timeout_s: float = 2.0):
    usage = {"in": 0, "out": 0}

    def call(system: str, prompt: str, tool, max_tokens: int) -> dict:
        response = model.call_tool(system=system, prompt=prompt, tool=tool, max_tokens=max_tokens)
        usage["in"] += response.input_tokens
        usage["out"] += response.output_tokens
        return response.data

    def finish(state: State, **kwargs) -> dict:
        result = AskResult(question=state["question"], repairs=state.get("repairs", 0), **kwargs)
        result.input_tokens, result.output_tokens = usage["in"], usage["out"]
        return {"result": result}

    def interpret(state: State) -> dict:
        system = prompts.decide_system_prompt()
        user = prompts.decide_user_prompt(state["question"], state.get("feedback"))
        try:
            decision = Decision.model_validate(call(system, user, prompts.DECIDE_TOOL, DECIDE_MAX_TOKENS))
        except ModelOutputError as exc:
            return {"feedback": f"malformed output: {exc}"}
        except ValidationError as exc:
            return {"feedback": f"output failed validation: {exc.errors()[0]['msg']}"}
        except ModelError as exc:
            return finish(state, status="error", error_kind=_error_kind(exc), error=str(exc))
        update: dict = {"decision": decision, "feedback": None}
        if decision.action != "sql":
            update.update(finish(state, status=decision.action, answer=decision.message.strip(), assumptions=decision.assumptions))
        return update

    def execute(state: State) -> dict:
        decision = state["decision"]
        try:
            qr = run_query(db_path, decision.sql, max_rows=max_rows, timeout_s=timeout_s)
        except QueryError as exc:
            return {"feedback": f"SQLite error for this SQL:\n{decision.sql}\nError: {exc}"}
        except (UnsafeQueryError, QueryLimitError, DatabaseMissingError) as exc:
            return finish(state, status="error", error_kind=_error_kind(exc), error=str(exc), sql=decision.sql.strip(), assumptions=decision.assumptions)
        matching = None
        if decision.event_filtered_total and qr.rows:
            matching, problem = check_support(qr.columns, qr.rows)
            if problem:
                return {"feedback": f"Result check failed for this SQL:\n{decision.sql}\nProblem: {problem}"}
        return {"result": AskResult(question=state["question"], status="answered", sql=qr.sql, columns=qr.columns, rows=qr.rows,
                                    truncated=qr.truncated, assumptions=decision.assumptions, matching_events=matching)}

    def summarize(state: State) -> dict:
        r = state["result"]
        r.repairs = state.get("repairs", 0)
        if not r.rows:
            r.answer = "No rows matched this question in the data. (This means nothing matched, not a total of zero.)"
        elif r.matching_events == 0:
            r.answer = NO_MATCH_ANSWER  # deterministic: verified support count says no events matched
        else:
            prompt = prompts.summarize_user_prompt(r.question, r.assumptions, r.columns, r.rows, r.truncated)
            try:
                r.answer = Summary.model_validate(call(prompts.SUMMARIZE_INSTRUCTIONS, prompt, prompts.SUMMARIZE_TOOL, SUMMARY_MAX_TOKENS)).answer.strip()
            except (ModelError, ValidationError) as exc:
                reason = str(exc) if isinstance(exc, ModelError) else "the summary failed validation"
                r.answer = f"The query ran, but a written summary is unavailable ({reason}). See the rows below."
            if r.truncated:
                r.answer += f" (Only the first {len(r.rows)} rows are shown; the full result is longer.)"
        r.input_tokens, r.output_tokens = usage["in"], usage["out"]
        return {"result": r}

    def repair(state: State) -> dict:
        return {"repairs": state.get("repairs", 0) + 1}

    def give_up(state: State) -> dict:
        feedback = state.get("feedback", "")
        kind = ("invalid_sql" if feedback.startswith("SQLite error")
                else "invalid_result" if feedback.startswith("Result check") else "malformed_output")
        decision = state.get("decision")
        return finish(
            state,
            status="error",
            error_kind=kind,
            error="The model could not produce a valid answer after one repair attempt: " + feedback.splitlines()[-1],
            sql=decision.sql.strip() if kind in ("invalid_sql", "invalid_result") and decision and decision.sql else None,
        )

    def route(state: State) -> str:
        if state.get("feedback"):
            return "repair" if state.get("repairs", 0) < MAX_REPAIRS else "give_up"
        if state.get("result") is not None:
            return "end"
        return "next"

    def route_after_execute(state: State) -> str:
        if state.get("feedback"):
            return route(state)
        return "end" if state["result"].status == "error" else "summarize"

    graph = StateGraph(State)
    for name, fn in [("interpret", interpret), ("execute", execute), ("summarize", summarize), ("repair", repair), ("give_up", give_up)]:
        graph.add_node(name, fn)
    graph.add_edge(START, "interpret")
    graph.add_conditional_edges("interpret", route, {"repair": "repair", "give_up": "give_up", "end": END, "next": "execute"})
    graph.add_conditional_edges("execute", route_after_execute, {"repair": "repair", "give_up": "give_up", "end": END, "summarize": "summarize"})
    graph.add_edge("repair", "interpret")
    graph.add_edge("give_up", END)
    graph.add_edge("summarize", END)
    return graph.compile()


def ask(question: str, model: ModelProvider, db_path: str | Path, **kwargs) -> AskResult:
    if len(question) > MAX_QUESTION_CHARS:
        return AskResult(question, "error", error_kind="question_too_long", error=f"Questions are limited to {MAX_QUESTION_CHARS} characters.")
    if not Path(db_path).is_file():
        return AskResult(question, "error", error_kind="database_missing", error=f"Database not found at {db_path}. Run `uv run ask-ticketing-generate-data`.")
    final = build_graph(model, db_path, **kwargs).invoke({"question": question, "repairs": 0})
    return final["result"]
