"""Dev-only evaluation runner for the live workflow (makes PAID model calls).

    uv run python evals/run_eval.py --confirm-paid               # dev cases only
    uv run python evals/run_eval.py --confirm-paid --held-out    # only once, for the held-out evaluation

Only the question text is sent to the workflow; expected rows and reference SQL
stay here. Results are compared on the decision branch and on result values
(column names and SQL text are not compared). Reports go to runs/ (gitignored).
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

CASES_PATH = Path(__file__).with_name("cases.json")
NUMBER = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?")
EXPECTED_STATUS = {"answerable": "answered", "no_match": "answered", "ambiguous": "clarify", "unsupported": "unsupported"}


# Explicit, inspectable column aliases: semantic field -> {result column name: factor},
# where result value = expected value x factor (e.g. revenue in dollars = cents x 0.01).
# Defined from the schema and business vocabulary, before any held-out run.
FIELD_ALIASES: dict[str, dict[str, float]] = {
    "event_id": {"event_id": 1},
    "name": {"name": 1, "event_name": 1, "event": 1},
    "event_date": {"event_date": 1, "date": 1},
    "venue": {"venue": 1, "venue_name": 1},
    "home_team": {"home_team": 1, "team": 1},
    "category": {"category": 1, "event_category": 1},
    "capacity": {"capacity": 1},
    "games": {"games": 1, "home_games": 1, "num_games": 1, "game_count": 1, "events": 1, "event_count": 1, "num_events": 1},
    "tickets_sold": {"tickets_sold": 1, "sold_tickets": 1, "ticket_count": 1, "total_tickets_sold": 1, "num_tickets_sold": 1},
    "revenue_cents": {"revenue_cents": 1, "ticket_revenue_cents": 1, "total_revenue_cents": 1,
                      "revenue_usd": 0.01, "ticket_revenue_usd": 0.01, "total_revenue_usd": 0.01},
    "matching_events": {"matching_events": 1},
    "remaining_inventory": {"remaining_inventory": 1, "remaining": 1, "remaining_tickets": 1, "remaining_capacity": 1},
    "refunded_tickets": {"refunded_tickets": 1, "refunded": 1},
    "avg_ticket_price_usd": {"avg_ticket_price_usd": 1, "avg_price_usd": 1, "average_ticket_price_usd": 1,
                             "avg_ticket_price_cents": 100, "avg_price_cents": 100, "average_ticket_price_cents": 100},
}


def _norm(value: Any, factor: float = 1) -> Any:
    """Convert a result value back to expected units and round numbers to cents precision."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(value / factor, 2)
    return str(value).strip()


def compare(case: dict, status: str, columns: list[str], rows: list[list[Any]], truncated: bool) -> tuple[bool, str]:
    """Score one result against the case's contract (decided from the question text):
    - the decision branch must match;
    - every required field must be present (by name or an explicit alias), with correct
      values for every row; extra columns are ignored;
    - the row multiset must match exactly (missing, extra or duplicate rows fail);
    - if the contract is ordered (a ranking was asked for), the row order must match."""
    want = EXPECTED_STATUS[case["kind"]]
    if status != want:
        return False, f"branch {status!r}, expected {want!r}"
    if case["kind"] in ("ambiguous", "unsupported"):
        return True, "branch ok"
    if truncated:
        return False, "result truncated"
    contract = case["contract"]
    expected = case["expected_rows"]
    if contract.get("no_match"):
        if not rows:
            return True, "empty result: no events matched"
        names = [c.lower() for c in columns]
        if "matching_events" in names and all(r[names.index("matching_events")] == 0 for r in rows):
            return True, "verified matching_events = 0: no events matched"
        return False, f"expected no matching events; got {len(rows)} row(s) without matching_events = 0"
    if len(rows) != len(expected):
        return False, f"{len(rows)} rows, expected {len(expected)}"
    if not expected:
        return True, "empty result as expected"
    lowered = [c.lower() for c in columns]
    picks = []  # (result column index, expected column index, factor)
    for field in contract["required_fields"]:
        found = [(i, f) for alias, f in FIELD_ALIASES[field].items() for i, c in enumerate(lowered) if c == alias]
        if not found:
            return False, f"missing required field {field!r} (columns: {columns})"
        i, factor = found[0]
        picks.append((i, case["expected_columns"].index(field), factor))
    got = [tuple(_norm(r[i], f) for i, _, f in picks) for r in rows]
    exp = [tuple(_norm(e[j]) for _, j, _ in picks) for e in expected]
    if contract["ordered"]:
        for n, (g, e) in enumerate(zip(got, exp)):
            if g != e:
                return False, f"row {n + 1}: got {g}, expected {e} (order matters)"
        return True, "required fields match, in order"
    if Counter(got) != Counter(exp):
        missing = list((Counter(exp) - Counter(got)).elements())
        extra = list((Counter(got) - Counter(exp)).elements())
        return False, f"row set differs; missing {missing}, unexpected {extra}"
    return True, "required fields match"


def _numbers(text: str) -> list[float]:
    return [float(m.replace("$", "").replace(",", "")) for m in NUMBER.findall(text or "")]


def summary_check(result) -> tuple[bool, list[float]]:
    """Every number in the answer must appear in the returned rows (raw, or cents shown
    as dollars), the question, or the assumptions. Returns (ok, unmatched numbers).
    A heuristic aid for review, not a full faithfulness judge."""
    allowed: set[float] = {float(len(result.rows))}
    for row in result.rows:
        for col, v in zip(result.columns, row):
            if isinstance(v, (int, float)):
                allowed.add(float(v))
                if col.endswith("_cents"):
                    allowed.add(round(v / 100, 2))
            elif isinstance(v, str):
                allowed.update(_numbers(v))
    allowed.update(_numbers(result.question))
    for a in result.assumptions:
        allowed.update(_numbers(a))
    unmatched = [n for n in _numbers(result.answer if result.status == "answered" else "") if not any(math.isclose(n, a, abs_tol=0.005) for a in allowed)]
    return not unmatched, unmatched


def run_case(case: dict, model, db: str) -> dict:
    """Run one case through the real workflow and score it (question text only goes to the model)."""
    from ask_ticketing.workflow import ask

    start = time.monotonic()
    result = ask(case["question"], model, db)
    latency = time.monotonic() - start
    ok, reason = compare(case, result.status, result.columns, result.rows, result.truncated)
    summary_ok, unmatched = summary_check(result)
    return {
        "id": case["id"], "kind": case["kind"], "passed": ok, "reason": reason,
        "branch_ok": result.status == EXPECTED_STATUS[case["kind"]],
        "summary_numbers_ok": summary_ok, "summary_unmatched_numbers": unmatched,
        "latency_s": round(latency, 2), "repairs": result.repairs,
        "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
        "result": asdict(result),
    }


def guarded_model(max_usd: float):
    """The configured provider with SDK retries off, wrapped in a hard pre-dispatch spend cap."""
    from ask_ticketing.budget import SpendGuard
    from ask_ticketing.providers import ProviderSettings, build_model

    inner = build_model(ProviderSettings.from_env(max_retries=0))
    if inner.usd_per_token is None:
        raise SystemExit(f"Pricing is unverified for model '{inner.model_id}' in this configuration, so a dollar budget cannot be "
                         "enforced; refusing to run. Use a configuration with verified prices (see README).")
    pin, pout = inner.usd_per_token
    return SpendGuard(inner, cap_usd=max_usd, usd_per_input_token=pin, usd_per_output_token=pout, attempts=1 + inner.max_retries)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-paid", action="store_true", help="required: each case makes 1-3 paid model calls")
    parser.add_argument("--held-out", action="store_true", help="run held-out cases instead of dev cases")
    parser.add_argument("--db", default="data/local/ticketing.db")
    parser.add_argument("--max-usd", type=float, required=False, default=None, help="hard spend cap, enforced before every request")
    args = parser.parse_args(argv)
    if not args.confirm_paid:
        parser.error("this runner calls the live model; pass --confirm-paid to proceed")
    if args.max_usd is None:
        parser.error("pass --max-usd (hard spend cap)")

    from ask_ticketing.budget import BudgetExceededError  # noqa: F401  (budget stops surface as error_kind 'budget')

    split = "held_out" if args.held_out else "dev"
    cases = [c for c in json.loads(CASES_PATH.read_text())["cases"] if c["split"] == split]
    model = guarded_model(args.max_usd)
    report = []
    for case in cases:
        row = run_case(case, model, args.db)
        report.append(row)
        print(f"{'PASS' if row['passed'] else 'FAIL'}  {case['id']:<40} {row['reason']}")
        if row["result"]["error_kind"] == "budget":
            print("STOP: spend cap reached")
            break
    passed = sum(r["passed"] for r in report)
    print(f"\n{passed}/{len(cases)} {split} cases passed; spend ${model.spent_usd:.4f} (cap ${args.max_usd:.2f})")
    out = Path("runs") / f"eval-{split}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"spent_usd": model.spent_usd, "calls": model.calls, "cases": report}, indent=2, default=str))
    print(f"report: {out}")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
