"""Test double for the ModelProvider port. Tests only: it replays a fixed script."""

from __future__ import annotations

from ask_ticketing.model import ModelResponse


class ScriptedModel:
    def __init__(self, *steps):
        self._steps = list(steps)
        self.calls = []

    def call_tool(self, *, system, prompt, tool, max_tokens):
        self.calls.append({"system": system, "prompt": prompt, "tool": tool.name, "max_tokens": max_tokens})
        if not self._steps:
            raise AssertionError("ScriptedModel: no scripted step left for call to " + tool.name)
        step = self._steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return ModelResponse(step, input_tokens=10, output_tokens=5)

    @property
    def tools_called(self):
        return [c["tool"] for c in self.calls]
