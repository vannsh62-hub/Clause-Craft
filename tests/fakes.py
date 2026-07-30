"""A fake `Model`, so sub-agents can be driven end to end without a network call.

This is what makes the isolation tests possible. `capture` records every system prompt and
input list the model was handed, which is the only honest way to assert that the judge never
saw its own prior score.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agents.items import ModelResponse, TResponseInputItem
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)


def text_message(text: str) -> ResponseOutputMessage:
    return ResponseOutputMessage(
        id="msg_1",
        content=[ResponseOutputText(annotations=[], text=text, type="output_text")],
        role="assistant",
        status="completed",
        type="message",
    )


def tool_call(
    name: str, arguments: dict[str, Any], call_id: str = "call_1"
) -> ResponseFunctionToolCall:
    return ResponseFunctionToolCall(
        id="fc_1",
        call_id=call_id,
        name=name,
        arguments=json.dumps(arguments),
        type="function_call",
    )


@dataclass
class Turn:
    """One scripted model response."""

    output: list[Any]
    input_tokens: int = 100
    output_tokens: int = 20


@dataclass
class Capture:
    system_instructions: str | None = None
    input: list[TResponseInputItem] | str = field(default_factory=list)
    tool_names: list[str] = field(default_factory=list)

    def as_text(self) -> str:
        """Everything the model could possibly have read, flattened."""
        parts = [self.system_instructions or ""]
        if isinstance(self.input, str):
            parts.append(self.input)
        else:
            parts.extend(json.dumps(item, default=str) for item in self.input)
        return "\n".join(parts)


class FakeModel(Model):
    """Replays `turns` in order. Records what it was shown each time."""

    def __init__(self, turns: list[Turn]) -> None:
        self._turns = list(turns)
        self.captures: list[Capture] = []

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: Any,
        tools: list[Any],
        output_schema: Any,
        handoffs: list[Any],
        tracing: Any,
        *,
        previous_response_id: str | None = None,
        conversation_id: str | None = None,
        prompt: Any = None,
    ) -> ModelResponse:
        self.captures.append(
            Capture(
                system_instructions=system_instructions,
                input=input if isinstance(input, str) else list(input),
                tool_names=[getattr(t, "name", "?") for t in tools],
            )
        )
        if not self._turns:
            raise AssertionError("FakeModel ran out of scripted turns")

        turn = self._turns.pop(0)
        return ModelResponse(
            output=turn.output,
            usage=Usage(
                requests=1,
                input_tokens=turn.input_tokens,
                output_tokens=turn.output_tokens,
                total_tokens=turn.input_tokens + turn.output_tokens,
            ),
            response_id="resp_1",
        )

    def stream_response(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise NotImplementedError("FakeModel does not stream")
