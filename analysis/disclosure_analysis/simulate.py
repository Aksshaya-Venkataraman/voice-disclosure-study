"""Generate a SYNTHETIC export in the same format as the gateway's CSV.

For demonstrations, pipeline tests and power analysis only - it is not study
data. Voice effects are multipliers on answer length, e.g. robot=0.8 means
answers to the robotic voice are 20% shorter on average.

    python -m disclosure_analysis.simulate --participants 24 --out synthetic.csv
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np

from interview.script import BLOCKS, voice_order

DEFAULT_EFFECTS = {"william": 1.0, "megan": 1.08, "pixie": 0.97, "robot": 0.82}
_FILLER = "the a and to of in it that was for on with as at so but we they this be have".split()
_CONTENT = (
    "project team work deadline client data report problem decision manager colleague system budget "
    "patient result feedback plan idea experience pressure support learning family health risk"
).split()
_SELF = "i i me my myself i'm i've".split()
_AFFECT = "stressed proud nervous happy worried frustrated relieved tired excited upset".split()
COLUMNS = [
    "participant",
    "native_english",
    "voice_order",
    "answer_id",
    "prompt_id",
    "kind",
    "block",
    "voice",
    "position",
    "modality",
    "duration_seconds",
    "asr_confidence",
    "prompt_text",
    "transcript",
    "created_at",
]


def _answer(rng: np.random.Generator, length: int, self_share: float, affect_share: float) -> str:
    kinds = rng.choice(4, size=length, p=[0.45, 0.55 - self_share - affect_share, self_share, affect_share])
    pools = (_FILLER, _CONTENT, _SELF, _AFFECT)
    return " ".join(str(rng.choice(pools[k])) for k in kinds)


def simulate(participants: int = 24, effects: dict[str, float] | None = None, seed: int = 7) -> list[dict]:
    effects = effects or DEFAULT_EFFECTS
    rng = np.random.default_rng(seed)
    rows, answer_id = [], 0
    for index in range(participants):
        order = voice_order(index)
        talkativeness = rng.lognormal(mean=np.log(45), sigma=0.35)  # people differ a lot
        native = bool(rng.random() < 0.6)
        position = 0
        for block, voice in zip(BLOCKS, order, strict=True):
            for question in block.questions:
                position += 1
                answer_id += 1
                expected = talkativeness * effects[voice] * rng.uniform(0.9, 1.1)
                length = max(3, int(rng.normal(expected, expected * 0.25)))
                self_share = float(np.clip(0.10 * effects[voice], 0.02, 0.2))
                text = _answer(rng, length, self_share, 0.03)
                rows.append(
                    {
                        "participant": f"P{index + 1:03d}",
                        "native_english": native,
                        "voice_order": " ".join(order),
                        "answer_id": answer_id,
                        "prompt_id": question.id,
                        "kind": "question",
                        "block": block.id,
                        "voice": voice,
                        "position": position,
                        "modality": "voice",
                        "duration_seconds": round(length / rng.uniform(2.0, 2.8), 2),
                        "asr_confidence": 0.9,
                        "prompt_text": question.text,
                        "transcript": text,
                        "created_at": "2026-01-01T00:00:00+00:00",
                    }
                )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--participants", type=int, default=24)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--no-effect", action="store_true", help="all voices identical (null model)")
    parser.add_argument("--out", type=Path, default=Path("synthetic_responses.csv"))
    args = parser.parse_args()
    effects = dict.fromkeys(DEFAULT_EFFECTS, 1.0) if args.no_effect else DEFAULT_EFFECTS
    rows = simulate(args.participants, effects, args.seed)
    with args.out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} SYNTHETIC answers to {args.out}")


if __name__ == "__main__":
    main()
