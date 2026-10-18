"""Test doubles. ScriptedLLM replays queued structured outputs and records every prompt it gets.

This is a test double only: it says nothing about how a real model behaves.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ScriptedLLM:
    def __init__(self, outputs: dict[type[BaseModel], list[BaseModel]]) -> None:
        self.queues = {k: list(v) for k, v in outputs.items()}
        self.calls: list[tuple[str, list[Any]]] = []

    def with_structured_output(self, schema: type[BaseModel]) -> _Bound:
        return _Bound(self, schema)


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
