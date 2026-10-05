"""PAID date-basis regression run under a hard spend cap (frozen expectations, no tuning).

    ASK_TICKETING_PROVIDER=<provider> uv run python evals/run_date_basis.py --confirm-paid --max-usd 0.40

Runs, in order:
  1. the provider check `ambiguous_last_month_date_basis` (scored with the standard contract scorer);
  2. the 6 checks in date_basis_checks.json, scored on branch, value and date column:
     - answered checks: the expected value appears in the rows, the contrast value (the other
       date basis) does not, and the SQL filters on the expected date column only;
     - clarify checks: the app asked instead of answering (no SQL run).
SDK retries are disabled; every request is pre-checked against the cap. Expectations are read
from the frozen files and never edited here. Writes runs/date-basis-<timestamp>.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

from run_eval import compare, guarded_model, summary_check

from ask_ticketing.workflow import ask

DATE_BASIS_PATH = Path(__file__).with_name("date_basis_checks.json")
PROVIDER_CHECKS_PATH = Path(__file__).with_name("provider_checks.json")
DATE_COLUMNS = ("event_date", "purchase_date")


def score_date_basis(check: dict, status: str, rows: list[list], sql: str | None) -> tuple[bool, str]:
    if status != check["expected_branch"]:
        return False, f"branch {status!r}, expected {check['expected_branch']!r}"
    if status == "clarify":
        return True, "asked for clarification"
    numbers = {v for row in rows for v in row if isinstance(v, (int, float)) and not isinstance(v, bool)}
    if check["expected_value"] not in numbers:
        return False, f"expected value {check['expected_value']} not in rows {rows}"
    if check.get("contrast_value_other_basis") in numbers:
        return False, f"contrast value {check['contrast_value_other_basis']} (other date basis) in rows"
    used = [c for c in DATE_COLUMNS if c in (sql or "")]
    if used != [check["expected_date_column"]]:
        return False, f"date columns in SQL {used}, expected only {check['expected_date_column']!r}"
    return True, f"value {check['expected_value']} via {check['expected_date_column']}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-paid", action="store_true")
    parser.add_argument("--max-usd", type=float, required=True)
    parser.add_argument("--db", default="data/local/ticketing.db")
    args = parser.parse_args(argv)
    if not args.confirm_paid:
        parser.error("this makes paid model calls; pass --confirm-paid")

    model = guarded_model(args.max_usd)
    provider = type(model.inner).__name__
    print(f"provider={provider} model={model.inner.model_id} effort={model.inner.effort} sdk_retries={model.inner.max_retries} cap=${args.max_usd:.2f}\n")
    out: dict = {"provider": provider, "model": model.inner.model_id, "cap_usd": args.max_usd, "checks": []}

    clarify_case = next(c for c in json.loads(PROVIDER_CHECKS_PATH.read_text())["checks"] if c["id"] == "ambiguous_last_month_date_basis")
    items = [("provider_check", clarify_case)] + [("date_basis", c) for c in json.loads(DATE_BASIS_PATH.read_text())["checks"]]
    for source, check in items:
        start = time.monotonic()
        result = ask(check["question"], model, args.db)
        latency = round(time.monotonic() - start, 2)
        if source == "provider_check":
            ok, reason = compare(check, result.status, result.columns, result.rows, result.truncated)
        else:
            ok, reason = score_date_basis(check, result.status, result.rows, result.sql)
        summary_ok, unmatched = summary_check(result)
        out["checks"].append({"id": check["id"], "source": source, "passed": ok, "reason": reason, "latency_s": latency,
                              "summary_numbers_ok": summary_ok, "summary_unmatched_numbers": unmatched, "result": asdict(result)})
        print(f"{'PASS' if ok else 'FAIL'} {check['id']:<36} {latency}s in={result.input_tokens} out={result.output_tokens}  {reason}")
        if result.error_kind == "budget":
            print("STOP: spend cap reached")
            break

    out["spent_usd"] = model.spent_usd
    out["calls"] = model.calls
    path = Path("runs") / f"date-basis-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, indent=2, default=str))
    passed = sum(c["passed"] for c in out["checks"])
    print(f"\n{passed}/{len(out['checks'])} passed; spend ${model.spent_usd:.4f} of cap ${args.max_usd:.2f}; report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
