"""Conversation engines: Rasa (NLU + dialogue) with a scripted fallback.

The gateway owns the interview state and sends it to Rasa on every turn, so
nothing is lost if Rasa restarts. If Rasa is unreachable the shared script is
used directly for that turn: the wording and order of questions are identical,
only the warm-up small talk is interpreted by keywords instead of NLU.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx2
from pydantic import BaseModel, ValidationError

from interview.script import State, advance

log = logging.getLogger(__name__)


class PromptModel(BaseModel):
    id: str
    text: str
    kind: Literal["warmup", "question", "end"]
    block: str | None
    position: int
    total: int


class StateModel(BaseModel):
    stage: Literal["mood", "nervous", "begin", "question", "done"]
    block: int
    question: int


@dataclass
class Turn:
    state: dict[str, Any]
    prompt: dict[str, Any]
    engine: str


class Engine(Protocol):
    def step(self, sender: str, state: dict[str, Any], text: str) -> Turn: ...


class ScriptedEngine:
    name = "scripted"

    def step(self, sender: str, state: dict[str, Any], text: str) -> Turn:
        result = advance(State.from_dict(state), None, text)
        return Turn(result.state.to_dict(), result.prompt.to_dict(), self.name)


class RasaEngine:
    name = "rasa"

    def __init__(self, base_url: str, timeout: float, transport: httpx2.BaseTransport | None = None) -> None:
        self._client = httpx2.Client(base_url=base_url, timeout=timeout, transport=transport, follow_redirects=False)
        self._fallback = ScriptedEngine()

    def close(self) -> None:
        self._client.close()

    def step(self, sender: str, state: dict[str, Any], text: str) -> Turn:
        payload = {"sender": sender, "message": text, "metadata": {"state": state}}
        for attempt in range(2):
            try:
                response = self._client.post("/webhooks/rest/webhook", json=payload)
                response.raise_for_status()
                custom = next(m["custom"] for m in response.json() if isinstance(m, dict) and "custom" in m)
                prompt = PromptModel.model_validate(custom["prompt"]).model_dump()
                new_state = StateModel.model_validate(custom["state"]).model_dump()
                return Turn(new_state, prompt, self.name)
            except (httpx2.HTTPError, ValueError, KeyError, StopIteration, TypeError, ValidationError) as exc:
                log.warning("rasa turn failed (attempt %d): %s", attempt + 1, type(exc).__name__)
        return self._fallback.step(sender, state, text)
