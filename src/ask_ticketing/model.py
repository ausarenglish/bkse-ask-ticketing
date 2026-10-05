"""The one model capability the application needs, as a small port.

Application code depends on `ModelProvider` and the errors below, never on a
specific SDK. Adapters (e.g. `ask_ticketing.bedrock.BedrockModel`) translate
their provider's responses and failures into these types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolSpec:
    """A structured-output contract: the model must return one JSON object
    matching `input_schema` (JSON Schema)."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ModelResponse:
    data: dict[str, Any]  # unvalidated; callers validate before use
    input_tokens: int = 0
    output_tokens: int = 0


class ModelProvider(Protocol):
    def call_tool(self, *, system: str, prompt: str, tool: ToolSpec, max_tokens: int) -> ModelResponse:
        """Ask the model to answer by filling in `tool`'s input. Raises ModelError."""
        ...


@dataclass
class ModelError(Exception):
    """Base class. `message` is safe to show to users (no credentials or raw payloads)."""

    message: str
    retryable: bool = field(default=False)

    def __str__(self) -> str:
        return self.message


class ModelConfigError(ModelError):
    """Local configuration problem (unknown profile, missing dependency)."""


class ModelAuthError(ModelError):
    """Credentials missing or expired."""


class ModelAccessError(ModelError):
    """The provider refused access for this account or model."""


class ModelUnavailableError(ModelError):
    """Throttling, timeouts, or service errors that persisted after bounded retries."""


class ModelRequestError(ModelError):
    """The provider rejected the request itself."""


class ModelOutputError(ModelError):
    """The model answered, but not in the required structured form."""
