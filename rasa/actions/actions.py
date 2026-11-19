"""Custom action: advance the scripted interview by one step.

The gateway sends the authoritative interview state in the message metadata
on every turn, so a restarted Rasa server never loses a participant's place.
Slots mirror the state for Rasa's own tracker.
"""

from __future__ import annotations

from typing import Any

from rasa_sdk import Action, Tracker
from rasa_sdk.events import SlotSet
from rasa_sdk.executor import CollectingDispatcher

from interview.script import State, advance


def _state_from(tracker: Tracker) -> State:
    metadata = tracker.latest_message.get("metadata") or {}
    if isinstance(metadata.get("state"), dict):
        return State.from_dict(metadata["state"])
    return State(
        stage=tracker.get_slot("interview_stage") or "mood",
        block=int(tracker.get_slot("block_index") or 0),
        question=int(tracker.get_slot("question_index") or 0),
    )


class ActionInterviewStep(Action):
    def name(self) -> str:
        return "action_interview_step"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: dict[str, Any]) -> list[dict[str, Any]]:
        state = _state_from(tracker)
        intent = (tracker.latest_message.get("intent") or {}).get("name")
        step = advance(state, intent, tracker.latest_message.get("text") or "")
        dispatcher.utter_message(
            text=step.prompt.text,
            json_message={"prompt": step.prompt.to_dict(), "state": step.state.to_dict(), "intent": intent},
        )
        return [
            SlotSet("interview_stage", step.state.stage),
            SlotSet("block_index", float(step.state.block)),
            SlotSet("question_index", float(step.state.question)),
        ]
