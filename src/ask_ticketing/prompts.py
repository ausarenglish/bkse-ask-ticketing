"""Runtime model context: schema, business definitions, instructions, output contracts.

Nothing here comes from evals/ (no evaluation questions, answers, or reference SQL).
"""

from __future__ import annotations

import json
import re
from typing import Any

from ask_ticketing.data import AS_OF, schema_sql
from ask_ticketing.model import ToolSpec

BUSINESS_DEFINITIONS = f"""\
- All data is SYNTHETIC demo data. "Today" is the fixed as-of date {AS_OF.isoformat()}.
- Tickets sold = COUNT of tickets with status = 'sold'. Refunded tickets are never counted.
- Ticket revenue = SUM(price_cents) over status = 'sold' tickets. Money is integer US cents, excluding taxes and fees.
- Average ticket price = ticket-weighted: SUM(price_cents) / COUNT(*) over sold tickets (not an average of per-event averages).
- Refunded = money returned and the seat went back to inventory. Refund dates are NOT recorded.
- Event date (events.event_date) is when the game or show happens. Purchase date (tickets.purchase_date) is when the ticket was bought, often in an earlier month.
- Remaining inventory = events.capacity minus tickets sold for that event. It is never stored.
- Upcoming = event_date > '{AS_OF.isoformat()}'. Past = event_date <= '{AS_OF.isoformat()}'.
- "Last month" = the calendar month before the as-of date. Relative periods resolve against {AS_OF.isoformat()}.
- Home games: events.home_team = 'Brooklyn Nets' or 'New York Liberty' (basketball only). Venues are 'Barclays Center' and the fictional 'Harborview Arena'.
- Data that does NOT exist: customers/buyers, refund dates, sales channels or reps, seat sections, taxes/fees, costs/profit, marketing, attendance/scans.
"""

INSTRUCTIONS = """\
You translate a ticketing employee's question into ONE read-only SQLite query over the schema below, or decide that you must ask a clarifying question or explain that the data cannot answer it.

Decide with the `decide` tool:
- action "clarify": the answer would materially change depending on an unstated choice. For the date basis of a time period, apply these rules in order:
  1. The period describes the games or events (e.g. "games in July", "events from January through September"): use event_date and state it in `assumptions`.
  2. The question explicitly says the tickets were bought or purchased in the period (e.g. "bought in March", "purchased during May"): use purchase_date and state it in `assumptions`.
  3. Otherwise, a period attached only to selling or sales (e.g. "tickets sold in August", "sales in June") leaves the date basis unresolved. "Sold" or "sales" alone does NOT mean purchase date: ticket sales are routinely reported by event date too. Ask which date basis is meant; do not pick one and record it as an assumption.
  Also clarify when a ranking/"best"/"performance" question does not say which metric. Ask one short question offering the concrete options.
- action "unsupported": the question needs data that does not exist (see definitions). Say briefly what is missing and what related question could be answered instead. Do not approximate missing data with other columns.
- action "sql": otherwise. Rules:
  - SQLite dialect. A single SELECT (CTEs allowed). Only tables venues, events, tickets.
  - Follow the business definitions exactly; filter status = 'sold' for counts, revenue and averages.
  - Use explicit date ranges with BETWEEN 'YYYY-MM-DD' AND 'YYYY-MM-DD' (dates are ISO text).
  - Keep money in cents in SQL, name money columns with a _cents suffix; do all arithmetic in SQL.
  - Include identifying columns (e.g. event name, date) for per-event results.
  - Rankings: ORDER BY the metric plus deterministic tie-breakers (e.g. event_date, event_id) and LIMIT when a top-N is asked.
  - Ticket counts or revenue totals for events chosen by event-level filters (team, venue, category, event name, event dates): set `event_filtered_total` to true and also return `matching_events`, the number of events that match the event-level filters, counted independently of ticket status. Use `events e LEFT JOIN tickets t ON t.event_id = e.event_id AND <ticket-level conditions>` with `COUNT(DISTINCT e.event_id) AS matching_events`. Ticket-level conditions (status = 'sold', purchase dates, prices) go in the JOIN condition, never in WHERE, so events without qualifying tickets are still counted.
  - Otherwise set `event_filtered_total` to false: totals not scoped to an event filter (e.g. all purchases in a purchase-date period), rankings, and lists. For lists, return the matching rows.
  - State every interpretation you made (date basis, period, metric, inclusions) in `assumptions`.
"""

DECIDE_TOOL = ToolSpec(
    name="decide",
    description="Report how to handle the question: run one SQL query, ask a clarifying question, or explain it is unsupported.",
    input_schema={
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["sql", "clarify", "unsupported"]},
            "sql": {"type": "string", "description": "The single SQLite SELECT query (action=sql only)."},
            "message": {"type": "string", "description": "Clarifying question (clarify) or explanation (unsupported)."},
            "assumptions": {"type": "array", "items": {"type": "string"}, "description": "Interpretations made."},
            "event_filtered_total": {"type": "boolean", "description": "true when the SQL returns ticket counts/revenue totals for events chosen by event-level filters; the SQL must then return a matching_events column."},
        },
        "required": ["action", "assumptions", "event_filtered_total"],
    },
)

SUMMARIZE_TOOL = ToolSpec(
    name="answer",
    description="Give the employee a short, plain-English answer grounded only in the query result.",
    input_schema={
        "type": "object",
        "properties": {"answer": {"type": "string", "description": "1-3 sentences."}},
        "required": ["answer"],
    },
)

SUMMARIZE_INSTRUCTIONS = """\
Write a short, plain-English answer (1-3 sentences) for a nontechnical ticketing employee.
Use ONLY the question, the stated assumptions and the query result provided. Do not compute new numbers; quote values as given (money is already formatted in dollars). Zero values are real zeros for the stated scope. If the result has a matching_events column, say how many events matched; never imply that events took place unless the result shows they did.
If the result is marked truncated, say the list is partial. Mention that the data is synthetic only if relevant. Do not mention SQL.
"""


def schema_context() -> str:
    """CREATE TABLE statements only (no triggers/indexes), comments included."""
    tables = re.findall(r"CREATE TABLE .*?\) STRICT;", schema_sql(), flags=re.S)
    return "\n\n".join(tables)


def decide_system_prompt() -> str:
    return f"{INSTRUCTIONS}\nBusiness definitions:\n{BUSINESS_DEFINITIONS}\nSchema:\n{schema_context()}\n"


def decide_user_prompt(question: str, feedback: str | None = None) -> str:
    prompt = f"Question: {question}"
    if feedback:
        prompt += f"\n\nYour previous attempt failed: {feedback}\nReturn a corrected decision."
    return prompt


def format_cell(column: str, value: Any) -> Any:
    if column.endswith("_cents") and isinstance(value, (int, float)):
        return f"${value / 100:,.2f}"
    return value


def display_rows(columns: list[str], rows: list[list[Any]]) -> list[list[Any]]:
    return [[format_cell(c, v) for c, v in zip(columns, row)] for row in rows]


def display_columns(columns: list[str]) -> list[str]:
    return [c[: -len("_cents")] + "_usd" if c.endswith("_cents") else c for c in columns]


SUMMARY_MAX_ROWS = 50
SUMMARY_MAX_CHARS = 12000  # hard bound on the summary payload, independent of row width


def summarize_user_prompt(question: str, assumptions: list[str], columns: list[str], rows: list[list[Any]], truncated: bool, limit: int = SUMMARY_MAX_ROWS) -> str:
    """The model sees at most `limit` rows (of the up-to-200 rows the user sees) and at most
    SUMMARY_MAX_CHARS characters; anything left out is reported as truncated."""
    shown = display_rows(columns, rows[:limit])

    def payload(n: int) -> str:
        return json.dumps({
            "question": question,
            "assumptions": assumptions,
            "columns": display_columns(columns),
            "rows": shown[:n],
            "rows_shown": n,
            "result_truncated": truncated or len(rows) > n,
        }, default=str)

    n = len(shown)
    text = payload(n)
    while len(text) > SUMMARY_MAX_CHARS and n > 0:
        n -= 1
        text = payload(n)
    if len(text) > SUMMARY_MAX_CHARS:  # e.g. huge assumptions; cut hard rather than exceed the bound
        text = text[:SUMMARY_MAX_CHARS]
    return text
