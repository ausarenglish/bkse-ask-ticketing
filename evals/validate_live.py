"""One-shot PAID validation batch under a single hard spend cap (dev cases only).

    uv run python evals/validate_live.py --confirm-paid --max-usd 2.50

Runs, in order, stopping on a budget stop or a smoke-test failure:
  1. a structured-output smoke test,
  2. one CLI question (rendered exactly as the CLI prints it),
  3. the 8 dev cases.
SDK retries are disabled; every request is pre-checked against the cap.
Writes runs/validate-<timestamp>.json. Held-out cases are never run here.

    ASK_TICKETING_PROVIDER=openai uv run python evals/validate_live.py --confirm-paid --max-usd 0.50 --provider-check

`--provider-check` is the small batch for a newly added provider: the smoke test plus the
four frozen checks in provider_checks.json (a normal answer, a clarification, no matching
events, and a legitimate zero). Three are known DEV regression cases and one was added for
the legitimate-zero contrast; they check that the provider works end to end, not unseen
accuracy.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from run_eval import CASES_PATH, guarded_model, run_case, summary_check

from ask_ticketing.cli import render
from ask_ticketing.model import ModelError, ToolSpec
from ask_ticketing.workflow import ask

CLI_QUESTION = "How many tickets were sold for Brooklyn Nets home games with an event date in September 2026? Exclude refunded tickets."
PROVIDER_CHECKS_PATH = Path(__file__).with_name("provider_checks.json")  # frozen expectations
SMOKE_TOOL = ToolSpec("smoke", 'Return {"ok": true}.', {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]})


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-paid", action="store_true")
    parser.add_argument("--max-usd", type=float, required=True)
    parser.add_argument("--db", default="data/local/ticketing.db")
    parser.add_argument("--provider-check", action="store_true", help="smoke test + the 4 frozen checks in provider_checks.json; no CLI question")
    args = parser.parse_args(argv)
    if not args.confirm_paid:
        parser.error("this makes paid model calls; pass --confirm-paid")

    model = guarded_model(args.max_usd)
    provider = type(model.inner).__name__
    print(f"provider={provider} model={model.inner.model_id} effort={model.inner.effort} sdk_retries={model.inner.max_retries} cap=${args.max_usd:.2f}\n")
    out: dict = {"provider": provider, "model": model.inner.model_id, "effort": model.inner.effort, "cap_usd": args.max_usd,
                 "mode": "provider-check" if args.provider_check else "full"}

    # 1. Smoke test
    t = time.monotonic()
    try:
        r = model.call_tool(system="You are a connectivity check.", prompt='Reply with {"ok": true}.', tool=SMOKE_TOOL, max_tokens=50)
        out["smoke"] = {"ok": r.data == {"ok": True}, "data": r.data, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "latency_s": round(time.monotonic() - t, 2)}
    except ModelError as exc:
        out["smoke"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print("SMOKE", json.dumps(out["smoke"]))
    if not out["smoke"]["ok"]:
        return _finish(out, model)

    # 2. One CLI question, rendered as the CLI would (skipped for --provider-check)
    if args.provider_check:
        return _run_cases(json.loads(PROVIDER_CHECKS_PATH.read_text())["checks"], out, model, args.db, stop_on_failure=True)
    t = time.monotonic()
    result = ask(CLI_QUESTION, model, args.db)
    summary_ok, unmatched = summary_check(result)
    out["cli"] = {"latency_s": round(time.monotonic() - t, 2), "status": result.status, "repairs": result.repairs,
                  "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                  "summary_numbers_ok": summary_ok, "summary_unmatched_numbers": unmatched, "rendered": render(result)}
    print("\n--- CLI ---\n" + render(result) + "\n")
    if result.error_kind == "budget":
        return _finish(out, model)

    # 3. Dev cases
    return _run_cases(_dev_cases(), out, model, args.db)


def _dev_cases() -> list[dict]:
    return [c for c in json.loads(CASES_PATH.read_text())["cases"] if c["split"] == "dev"]


def _run_cases(cases: list[dict], out: dict, model, db: str, stop_on_failure: bool = False) -> int:
    out["cases"] = []
    for case in cases:
        row = run_case(case, model, db)
        out["cases"].append(row)
        print(f"{'PASS' if row['passed'] else 'FAIL'} {case['id']:<38} branch_ok={row['branch_ok']} summary_ok={row['summary_numbers_ok']} "
              f"repairs={row['repairs']} {row['latency_s']}s in={row['input_tokens']} out={row['output_tokens']}  {row['reason']}")
        if row["result"]["error_kind"] == "budget":
            print("STOP: spend cap reached")
            break
        if stop_on_failure and not row["passed"]:
            out["stopped_after"] = case["id"]
            print(f"STOP: first unexpected failure ({case['id']}); remaining checks not run")
            break
    return _finish(out, model)


def _finish(out: dict, model) -> int:
    out["spent_usd"] = model.spent_usd
    out["calls"] = model.calls
    path = Path("runs") / f"validate-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nspend ${model.spent_usd:.4f} of cap ${out['cap_usd']:.2f}; report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
