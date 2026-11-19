"""Turn the researcher export into tables, statistics, figures and a report.

python -m disclosure_analysis.report responses.csv --out report/
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from disclosure_analysis import METRICS
from disclosure_analysis.stats import friedman, mixed_model, pairwise_wilcoxon, participant_means, voice_summary
from disclosure_analysis.text_features import add_features

REQUIRED = {"participant", "kind", "block", "voice", "transcript"}


def load(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = REQUIRED - set(df.columns)
    if missing:
        raise ValueError(f"export is missing columns: {sorted(missing)}")
    questions = df[df["kind"] == "question"].copy()  # warm-up small talk is not analysed
    return add_features(questions)


def analyse(df: pd.DataFrame, seed: int = 42) -> dict[str, Any]:
    results: dict[str, Any] = {
        "participants": int(df["participant"].nunique()),
        "answers": len(df),
        "metrics": {},
    }
    for metric, label in METRICS.items():
        table = participant_means(df, metric)
        summary = voice_summary(table, seed=seed)
        results["metrics"][metric] = {
            "label": label,
            "complete_participants": len(table),
            "by_voice": json.loads(summary.round(3).to_json(orient="index")),
            "friedman": friedman(table),
            "pairwise_wilcoxon_holm": pairwise_wilcoxon(table),
            "mixed_model": mixed_model(df, metric),
        }
    if "native_english" in df.columns:
        native = df.groupby(["native_english", "voice"])["words"].mean().unstack()
        results["words_by_native_english"] = json.loads(native.round(2).to_json(orient="index"))
    return results


def _boxplot(df: pd.DataFrame, metric: str, path: Path) -> None:
    table = participant_means(df, metric)
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.boxplot([table[v] for v in table.columns], tick_labels=list(table.columns), showmeans=True)
    for i, voice in enumerate(table.columns, start=1):  # one dot per participant
        ax.scatter(np.full(len(table), i) + np.linspace(-0.08, 0.08, len(table)), table[voice], s=10, alpha=0.5)
    ax.set_title(METRICS[metric])
    ax.set_xlabel("Interviewer voice")
    ax.set_ylabel("Participant mean")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _fmt_p(p: float | None) -> str:
    """Format a p-value with its relation sign, e.g. "p = 0.012" or "p < 0.001"."""
    if p is None:
        return "p = n/a"
    return "p < 0.001" if p < 0.001 else f"p = {p:.3f}"


def markdown(results: dict[str, Any]) -> str:
    lines = [
        "# Impact of interviewer voice on disclosure",
        "",
        f"Participants: {results['participants']} · answers analysed: {results['answers']}",
        "",
    ]
    for metric, info in results["metrics"].items():
        fr = info["friedman"]
        lines += [f"## {info['label']}", "", "| Voice | Mean | 95% CI (bootstrap) |", "|---|---|---|"]
        for voice, row in info["by_voice"].items():
            lines.append(f"| {voice} | {row['mean']:.2f} | {row['ci_low']:.2f} - {row['ci_high']:.2f} |")
        lines += [
            "",
            f"Friedman χ²({len(info['by_voice']) - 1}) = "
            f"{fr['statistic']:.2f}, {_fmt_p(fr['p_value'])}, Kendall's W = {fr['kendalls_w']:.3f} "
            f"(n = {fr['n']})."
            if fr["statistic"] is not None
            else "Friedman test: not enough data.",
            "",
        ]
        significant = [r for r in info["pairwise_wilcoxon_holm"] if r["p_holm"] < 0.05]
        if significant:
            lines.append(
                "Pairwise differences (Wilcoxon, Holm-corrected p < .05): "
                + "; ".join(
                    f"{r['a']} vs {r['b']} (Δ = {r['mean_difference']:.2f}, {_fmt_p(r['p_holm'])})" for r in significant
                )
                + "."
            )
        model = info["mixed_model"]
        if model.get("estimated"):
            effects = "; ".join(
                f"{v}: {e['estimate']:+.2f} [{e['ci_low']:.2f}, {e['ci_high']:.2f}], {_fmt_p(e['p_value'])}"
                for v, e in model["voice_effects"].items()
            )
            lines.append(f"Mixed model (vs {model['reference_voice']}, controlling for topic block): {effects}.")
        lines += ["", f"![{info['label']}](figures/{metric}.png)", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export", type=Path, help="CSV from /api/researcher/export.csv")
    parser.add_argument("--out", type=Path, default=Path("report"))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    df = load(args.export)
    results = analyse(df, seed=args.seed)
    figures = args.out / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    for metric in METRICS:
        _boxplot(df, metric, figures / f"{metric}.png")
        participant_means(df, metric).round(3).to_csv(args.out / f"participant_means_{metric}.csv")
    df.to_csv(args.out / "answers_with_features.csv", index=False)
    (args.out / "results.json").write_text(json.dumps(results, indent=2))
    (args.out / "report.md").write_text(markdown(results))
    print(f"report written to {args.out}/report.md")


if __name__ == "__main__":
    main()
