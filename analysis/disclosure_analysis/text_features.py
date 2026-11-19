"""Disclosure measures computed from answer transcripts.

* words / content_words - how much people say (answer length with and
  without stop words, as in the original study).
* first_person_rate - self-references (I, me, my...) per 100 words, a common
  marker of self-disclosure.
* affect_rate - emotion words per 100 words.

Word lists are bundled so results are reproducible offline.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

STOP_WORDS = frozenset(
    """a about above after again against all am an and any are aren't as at be because been before being below
    between both but by can can't cannot could couldn't did didn't do does doesn't doing don't down during each few
    for from further had hadn't has hasn't have haven't having he he'd he'll he's her here here's hers herself him
    himself his how how's i i'd i'll i'm i've if in into is isn't it it's its itself let's me more most mustn't my
    myself no nor not of off on once only or other ought our ours ourselves out over own same shan't she she'd
    she'll she's should shouldn't so some such than that that's the their theirs them themselves then there there's
    these they they'd they'll they're they've this those through to too under until up very was wasn't we we'd
    we'll we're we've were weren't what what's when when's where where's which while who who's whom why why's with
    won't would wouldn't you you'd you'll you're you've your yours yourself yourselves um uh like just really""".split()
)
FIRST_PERSON = frozenset("i i'm i've i'd i'll me my mine myself".split())
AFFECT = frozenset(
    """afraid angry anxious ashamed awful bad calm cried cry depressed disappointed embarrassed enjoy enjoyed excited
    fear frustrated glad grateful guilty happy hate hated hurt jealous joy lonely love loved nervous overwhelmed
    panic proud relieved sad scared shame stress stressed stressful struggled struggling terrible upset worried worry
    hopeless hopeful exhausted tired insecure confident unhappy happiness sadness""".split()
)
_WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")


def tokenize(text: str) -> list[str]:
    return _WORD.findall(str(text).lower().replace("’", "'"))


def features(text: str) -> dict[str, float]:
    tokens = tokenize(text)
    n = len(tokens)
    first_person = sum(t in FIRST_PERSON for t in tokens)
    affect = sum(t in AFFECT for t in tokens)
    return {
        "words": float(n),
        "content_words": float(sum(t not in STOP_WORDS for t in tokens)),
        "first_person_rate": 100.0 * first_person / n if n else 0.0,
        "affect_rate": 100.0 * affect / n if n else 0.0,
        "unique_word_ratio": len(set(tokens)) / n if n else 0.0,
    }


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of ``df`` with one column per measure (and speech rate when timed)."""
    measured = pd.DataFrame([features(t) for t in df["transcript"].fillna("")], index=df.index)
    out = pd.concat([df, measured], axis=1)
    duration = pd.to_numeric(out.get("duration_seconds"), errors="coerce")
    with np.errstate(divide="ignore", invalid="ignore"):
        out["speech_rate_wpm"] = np.where(duration > 0, out["words"] / duration * 60.0, np.nan)
    return out
