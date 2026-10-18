"""Test doubles. ScriptedLLM replays queued structured outputs and records every prompt it gets.

This is a test double only: it says nothing about how a real model behaves.
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel


class ScriptedLLM:
    def __init__(self, outputs: dict[type[BaseModel], list[BaseModel]]) -> None:
        self.queues = {k: list(v) for k, v in outputs.items()}
        self.calls: list[tuple[str, list[Any]]] = []

    def with_structured_output(self, schema: type[BaseModel]) -> _Bound:
        return _Bound(self, schema)


class FakeToolModel(BaseChatModel):
    """Chat model that replays scripted AIMessages (with tool calls) for create_agent tests."""

    script: list[AIMessage]
    seen: list[list[Any]] = []

    @property
    def _llm_type(self) -> str:
        return "fake-tool-model"

    def bind_tools(self, tools: Any, **kwargs: Any) -> FakeToolModel:
        return self

    def _generate(
        self, messages: list[Any], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        self.seen.append(list(messages))
        if not self.script:
            raise AssertionError("FakeToolModel script exhausted")
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])


class _Bound:
    def __init__(self, parent: ScriptedLLM, schema: type[BaseModel]) -> None:
        self.parent = parent
        self.schema = schema

    def invoke(self, messages: list[Any], config: Any = None) -> Any:
        self.parent.calls.append((self.schema.__name__, messages))
        queue = self.parent.queues[self.schema]
        if not queue:
            raise AssertionError(f"ScriptedLLM has no more {self.schema.__name__} outputs queued")
        return queue.pop(0)
