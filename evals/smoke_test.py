"""One small PAID structured-output call, under a hard spend cap.

    uv run python evals/smoke_test.py --confirm-paid --max-usd 0.10
"""

from __future__ import annotations

import argparse
import sys

from run_eval import guarded_model

from ask_ticketing.model import ModelError, ToolSpec

SMOKE_TOOL = ToolSpec("smoke", 'Return {"ok": true}.', {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]})


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-paid", action="store_true")
    parser.add_argument("--max-usd", type=float, required=True)
    args = parser.parse_args(argv)
    if not args.confirm_paid:
        parser.error("this makes one paid model call; pass --confirm-paid")
    model = guarded_model(args.max_usd)
    try:
        r = model.call_tool(system="You are a connectivity check.", prompt='Reply with {"ok": true}.', tool=SMOKE_TOOL, max_tokens=50)
    except ModelError as exc:
        print(f"FAILED ({type(exc).__name__}): {exc}")
        return 1
    print(f"OK model={model.inner.model_id} data={r.data} in={r.input_tokens} out={r.output_tokens} spend=${model.spent_usd:.5f}")
    return 0 if r.data == {"ok": True} else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
