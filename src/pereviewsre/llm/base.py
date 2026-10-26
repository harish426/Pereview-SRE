"""Provider-neutral LLM interface so the agent works with any backend.

Neutral conversation format (a list of dicts):
  {"role": "user", "content": str}
  {"role": "assistant", "content": str, "tool_calls": [ToolCall], "raw": Any}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}

`raw` carries the provider's own message object (e.g. Gemini thought signatures) and is
passed back untouched on the next turn.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pereviewsre.logging_setup import get_logger


@dataclass
class ToolCall:
  id: str
  name: str
  arguments: str  # JSON string


@dataclass
class LLMResponse:
  content: str = ""
  tool_calls: list[ToolCall] = field(default_factory=list)
  prompt_tokens: int | None = None
  completion_tokens: int | None = None
  raw: Any = None

  def as_message(self) -> dict[str, Any]:
    return {
      "role": "assistant",
      "content": self.content,
      "tool_calls": self.tool_calls,
      "raw": self.raw,
    }


class LLMProvider(ABC):
  """Implement `chat` to add a new model backend."""

  name: str = "base"

  def __init__(self) -> None:
    self._log = get_logger(f"{__name__}.{self.name}")

  @abstractmethod
  def chat(
    self,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
  ) -> LLMResponse:
    """Send one turn. `tools` use the OpenAI function-spec format."""
