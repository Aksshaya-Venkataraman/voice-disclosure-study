"""The interview: warm-up, four topic blocks, four interviewer voices.

Design (within-subjects): every participant hears all four voices, one per
topic block. The voice-to-block assignment follows a balanced Latin square, so
across every group of four participants each voice appears once in each
block and in each serial position, and each voice follows every other voice
equally often (first-order carry-over balance). Voice is therefore not
confounded with topic or with fatigue.

This module has no third-party dependencies so it can run inside the Rasa
action server and the gateway alike.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

VOICES: tuple[str, ...] = ("william", "megan", "pixie", "robot")

# Balanced Latin square (Williams design) for four conditions.
LATIN_SQUARE: tuple[tuple[int, ...], ...] = (
    (0, 1, 3, 2),
    (1, 2, 0, 3),
    (2, 3, 1, 0),
    (3, 0, 2, 1),
)


@dataclass(frozen=True)
class Question:
    id: str
    text: str


@dataclass(frozen=True)
class Block:
    id: str
    title: str
    intro: str
    questions: tuple[Question, ...]


BLOCKS: tuple[Block, ...] = (
    Block(
        "technical",
        "Technical interview",
        "Let's start with the technical part of the interview.",
        (
            Question("tech_project", "Tell me about a technical project you are proud of. What was your role in it?"),
            Question(
                "tech_setback", "Describe a time a technical decision you made turned out badly. What happened next?"
            ),
            Question("tech_learning", "How do you keep your skills up to date, and what do you find hardest to learn?"),
            Question(
                "tech_pressure", "Tell me about a deadline that put you under real pressure. How did it affect you?"
            ),
        ),
    ),
    Block(
        "marketing",
        "Marketing interview",
        "Thank you. Now let's move on to the marketing interview.",
        (
            Question("mkt_campaign", "Describe a campaign or idea you pushed for that others disagreed with."),
            Question("mkt_team", "How do you handle a team where everyone has a different view of the plan?"),
            Question(
                "mkt_failure", "Tell me about a piece of work that did not land the way you hoped. How did you feel?"
            ),
            Question("mkt_solo", "Do you prefer working on your own or with a team? Why?"),
        ),
    ),
    Block(
        "financial",
        "Financial interview",
        "Thank you. Next is the financial interview.",
        (
            Question(
                "fin_contradiction",
                "If two reports gave you contradicting figures, how would you decide what to trust?",
            ),
            Question(
                "fin_mistake", "Tell me about a mistake you made with numbers or money, at work or in your own life."
            ),
            Question("fin_risk", "How comfortable are you taking risks? Give me an example."),
            Question("fin_assumptions", "When solving a problem, how do you check that your assumptions are right?"),
        ),
    ),
    Block(
        "healthcare",
        "Healthcare interview",
        "Thank you. The last part is the healthcare interview.",
        (
            Question("hc_bad_news", "How would you tell a client some very bad news?"),
            Question("hc_stress", "What do you do when work starts to affect your health or wellbeing?"),
            Question("hc_balance", "What does work-life balance mean to you personally?"),
            Question("hc_support", "Tell me about a time you needed support from someone. How did you ask for it?"),
        ),
    ),
)

GREETING = "Hello! How are you doing today?"
NERVOUS_GOOD = "Glad to hear that. Are you nervous about the interview?"
NERVOUS_BAD = "Sorry to hear that. Are you nervous about the interview?"
BEGIN_NERVOUS = "Don't worry, there are no right or wrong answers. Shall we begin?"
BEGIN_CALM = "Fantastic. Shall we begin?"
GOODBYE = "That was the last question. Thank you for taking part!"

Stage = Literal["mood", "nervous", "begin", "question", "done"]
Kind = Literal["warmup", "question", "end"]

POSITIVE_WORDS = frozenset("good great fine well ok okay alright happy excellent wonderful fantastic yes yeah".split())
NEGATIVE_WORDS = frozenset("bad not tired sad awful terrible stressed anxious nervous no nope worried".split())


@dataclass
class State:
    stage: Stage = "mood"
    block: int = 0
    question: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> State:
        if not data:
            return cls()
        return cls(
            stage=data.get("stage", "mood"), block=int(data.get("block", 0)), question=int(data.get("question", 0))
        )


@dataclass(frozen=True)
class Prompt:
    id: str
    text: str
    kind: Kind
    block: str | None
    position: int = 0  # 1-based question number within the whole interview
    total: int = 16

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Step:
    state: State
    prompt: Prompt


def voice_order(participant_index: int) -> list[str]:
    """Voice for each block (in block order) for the n-th participant (0-based)."""
    row = LATIN_SQUARE[participant_index % len(LATIN_SQUARE)]
    return [VOICES[i] for i in row]


def validate_voice_order(order: list[str]) -> list[str]:
    if sorted(order) != sorted(VOICES):
        raise ValueError(f"voice order must be a permutation of {VOICES}")
    return list(order)


def voice_for(prompt: Prompt | dict, order: list[str]) -> str:
    """Which voice speaks a prompt: the block's voice; warm-up uses the first block's voice."""
    block = prompt["block"] if isinstance(prompt, dict) else prompt.block
    if block is None:
        kind = prompt["kind"] if isinstance(prompt, dict) else prompt.kind
        return order[-1] if kind == "end" else order[0]
    return order[[b.id for b in BLOCKS].index(block)]


TOTAL_QUESTIONS = sum(len(b.questions) for b in BLOCKS)


def _question_prompt(state: State, with_intro: bool) -> Prompt:
    block = BLOCKS[state.block]
    question = block.questions[state.question]
    position = sum(len(b.questions) for b in BLOCKS[: state.block]) + state.question + 1
    text = f"{block.intro} {question.text}" if with_intro else question.text
    return Prompt(question.id, text, "question", block.id, position, TOTAL_QUESTIONS)


def first_prompt() -> Prompt:
    return Prompt("warmup_greeting", GREETING, "warmup", None, 0, TOTAL_QUESTIONS)


def end_prompt() -> Prompt:
    return Prompt("end", GOODBYE, "end", None, TOTAL_QUESTIONS, TOTAL_QUESTIONS)


def classify_mood(text: str) -> bool:
    """Fallback when no NLU intent is available: True means a positive reply."""
    words = {w.strip(".,!?").lower() for w in text.split()}
    return len(words & POSITIVE_WORDS) >= len(words & NEGATIVE_WORDS)


def advance(state: State, intent: str | None, text: str) -> Step:
    """Move the conversation on by one user turn.

    ``intent`` comes from Rasa NLU when available (mood_great, mood_unhappy,
    affirm, deny, ...); otherwise a keyword heuristic is used for the warm-up.
    Every reply during the question stage simply moves to the next question:
    the interviewer never judges or comments on answers, so all voices behave
    identically except for how they sound.
    """
    if state.stage == "mood":
        positive = intent == "mood_great" if intent in ("mood_great", "mood_unhappy") else classify_mood(text)
        return Step(
            State("nervous"),
            Prompt("warmup_nervous", NERVOUS_GOOD if positive else NERVOUS_BAD, "warmup", None, 0, TOTAL_QUESTIONS),
        )

    if state.stage == "nervous":
        nervous = intent == "affirm" if intent in ("affirm", "deny") else not classify_mood(text)
        return Step(
            State("begin"),
            Prompt("warmup_begin", BEGIN_NERVOUS if nervous else BEGIN_CALM, "warmup", None, 0, TOTAL_QUESTIONS),
        )

    if state.stage == "begin":
        new = State("question", 0, 0)
        return Step(new, _question_prompt(new, with_intro=True))

    if state.stage == "question":
        block, question = state.block, state.question + 1
        if question >= len(BLOCKS[block].questions):
            block, question = block + 1, 0
        if block >= len(BLOCKS):
            return Step(State("done", len(BLOCKS) - 1, 0), end_prompt())
        new = State("question", block, question)
        return Step(new, _question_prompt(new, with_intro=question == 0))

    return Step(state, end_prompt())  # finished: keep saying goodbye
