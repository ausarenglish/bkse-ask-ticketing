"""An application-enforced spend bound around any ModelProvider (used for paid validation runs).

The bound is computed from the CONFIGURED prices and token limits below. It is not a
guarantee about the provider's final bill: if a configured price is wrong, a provider bills
something not counted here, or the byte-based input bound fails, actual charges can differ.
Configurations without verified prices are refused by the evaluation scripts.

Before each request is dispatched, the guard reserves its worst-case cost:

    attempts x (input_upper_bound x input_price + max_output_tokens x output_price)

and refuses to dispatch if `spent + reservation > cap`.
- input_upper_bound = UTF-8 bytes of everything the adapter sends (system, prompt,
  JSON schema, wrapper text) + a fixed overhead for API-side formatting. Assumption:
  a token covers at least one byte, so bytes bound the token count.
- max_output_tokens = the max_tokens the adapter actually sends (thinking included).
- attempts = 1 + the adapter's SDK retries (set retries to 0 for exact accounting).
After the call, the actual reported cost is charged. If the call fails, the whole
reservation is charged, because a failed attempt may still have been billed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ask_ticketing.model import ModelError, ModelProvider, ModelResponse, ToolSpec

INPUT_OVERHEAD_TOKENS = 1500


class BudgetExceededError(ModelError):
    """Dispatching the next request could exceed the approved spend cap."""


@dataclass
class SpendGuard:
    inner: ModelProvider
    cap_usd: float
    usd_per_input_token: float
    usd_per_output_token: float
    attempts: int = 1
    spent_usd: float = 0.0
    calls: list[dict] = field(default_factory=list)

    def reservation(self, *, system: str, prompt: str, tool: ToolSpec, max_tokens: int) -> tuple[float, int, int]:
        sent_bytes = len((system + prompt + tool.name + tool.description + json.dumps(tool.input_schema)).encode())
        input_bound = sent_bytes + 200 + INPUT_OVERHEAD_TOKENS  # +200 for the adapter's wrapper sentence
        output_cap = getattr(self.inner, "output_cap", lambda m: m)(max_tokens)
        cost = self.attempts * (input_bound * self.usd_per_input_token + output_cap * self.usd_per_output_token)
        return cost, input_bound, output_cap

    def call_tool(self, *, system: str, prompt: str, tool: ToolSpec, max_tokens: int) -> ModelResponse:
        reserve, input_bound, output_cap = self.reservation(system=system, prompt=prompt, tool=tool, max_tokens=max_tokens)
        if self.spent_usd + reserve > self.cap_usd:
            raise BudgetExceededError(
                f"Spend cap reached: ${self.spent_usd:.4f} spent; the next call could cost up to ${reserve:.4f} (cap ${self.cap_usd:.2f})."
            )
        record = {"tool": tool.name, "reserved_usd": reserve, "input_bound": input_bound, "output_cap": output_cap}
        try:
            response = self.inner.call_tool(system=system, prompt=prompt, tool=tool, max_tokens=max_tokens)
        except Exception as exc:
            self.spent_usd += reserve
            self.calls.append({**record, "charged_usd": reserve, "error": type(exc).__name__})
            raise
        actual = response.input_tokens * self.usd_per_input_token + response.output_tokens * self.usd_per_output_token
        charged = max(actual, 0.0)
        self.spent_usd += charged
        self.calls.append({**record, "charged_usd": charged, "input_tokens": response.input_tokens, "output_tokens": response.output_tokens,
                           "bound_held": response.input_tokens <= input_bound and response.output_tokens <= output_cap})
        return response
