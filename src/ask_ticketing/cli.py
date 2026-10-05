"""Command-line entry point. Composition root: builds the model adapter and injects it."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

from ask_ticketing.data import DEFAULT_DB_PATH
from ask_ticketing.model import ModelError
from ask_ticketing.prompts import display_columns, display_rows
from ask_ticketing.providers import PROVIDERS, ProviderSettings, build_model
from ask_ticketing.workflow import AskResult, ask

EXIT_CODES = {"answered": 0, "clarify": 0, "unsupported": 0, "error": 1}


def _table(columns: list[str], rows: list[list]) -> str:
    cells = [[str(c) for c in display_columns(columns)]] + [["" if v is None else str(v) for v in r] for r in display_rows(columns, rows)]
    widths = [max(len(row[i]) for row in cells) for i in range(len(columns))]
    lines = ["  ".join(v.ljust(w) for v, w in zip(row, widths)) for row in cells]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def render(result: AskResult) -> str:
    out = [result.notice, ""]
    label = {"answered": "Answer", "clarify": "Clarification needed", "unsupported": "Not supported by this data", "error": "Error"}[result.status]
    out.append(f"{label}: {result.answer if result.status != 'error' else result.error}")
    if result.assumptions:
        out += ["", "Assumptions:"] + [f"  - {a}" for a in result.assumptions]
    if result.sql:
        out += ["", "SQL executed:" if result.status == "answered" else "SQL (not executed successfully):", result.sql]
    if result.status == "answered":
        out += ["", f"Rows ({len(result.rows)}{', truncated' if result.truncated else ''}):"]
        out.append(_table(result.columns, result.rows) if result.rows else "  (no rows)")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ask a plain-English question about the SYNTHETIC ticketing data.")
    parser.add_argument("question", help="e.g. \"How many tickets were sold for Nets home games with an event date in September 2026?\"")
    parser.add_argument("--db", default=os.environ.get("ASK_TICKETING_DB", str(DEFAULT_DB_PATH)))
    parser.add_argument("--provider", choices=PROVIDERS, help="default: $ASK_TICKETING_PROVIDER or anthropic")
    parser.add_argument("--model-id", help="default: $ASK_TICKETING_MODEL_ID or the provider's default model")
    parser.add_argument("--region", help="bedrock only: AWS region (default: $ASK_TICKETING_REGION, AWS config, or us-east-1)")
    parser.add_argument("--profile", help="bedrock only: named AWS profile (default: $ASK_TICKETING_AWS_PROFILE or the standard AWS credential chain)")
    parser.add_argument("--max-rows", type=int, default=200)
    parser.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = parser.parse_args(argv)

    try:
        model = build_model(ProviderSettings.from_env(provider=args.provider, model_id=args.model_id, region=args.region, profile=args.profile))
    except ModelError as exc:
        print(f"Model provider setup needed: {exc}", file=sys.stderr)
        return 2
    result = ask(args.question, model, args.db, max_rows=args.max_rows)
    print(json.dumps(asdict(result), indent=2, default=str) if args.json else render(result))
    return EXIT_CODES[result.status]


if __name__ == "__main__":
    raise SystemExit(main())
