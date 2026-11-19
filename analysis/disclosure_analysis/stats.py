"""Within-subject statistics for the effect of interviewer voice.

Each participant hears every voice (one per topic block), so voices are
compared *within* participants:

1. Friedman test on participant-level means across the four voices, with
   Kendall's W as effect size.
2. Pairwise Wilcoxon signed-rank tests with Holm correction.
3. A linear mixed model, metric ~ voice + block + (1 | participant), which
   uses every answer and separates voice from topic.
4. Percentile bootstrap confidence intervals (resampling participants).
"""

from __future__ import annotations

import itertools
import warnings
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

REFERENCE_VOICE = "william"


def participant_means(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    """participants x voices table of mean metric values (complete cases only)."""
    table = df.pivot_table(index="participant", columns="voice", values=metric, aggfunc="mean")
    return table.dropna()


def friedman(table: pd.DataFrame) -> dict[str, Any]:
    n, k = table.shape
    if n < 3 or k < 3:
        return {"n": n, "statistic": None, "p_value": None, "kendalls_w": None}
    statistic, p_value = stats.friedmanchisquare(*[table[c].to_numpy() for c in table.columns])
    return {
        "n": n,
        "statistic": float(statistic),
        "p_value": float(p_value),
        "kendalls_w": float(statistic / (n * (k - 1))),
    }


def holm(p_values: list[float]) -> list[float]:
    """Holm-Bonferroni adjusted p-values (same order as the input)."""
    m = len(p_values)
    order = np.argsort(p_values)
    adjusted = np.empty(m)
    running = 0.0
    for rank, idx in enumerate(order):
        running = max(running, (m - rank) * p_values[idx])
        adjusted[idx] = min(1.0, running)
    return adjusted.tolist()


def pairwise_wilcoxon(table: pd.DataFrame) -> list[dict[str, Any]]:
    pairs = list(itertools.combinations(table.columns, 2))
    rows, raw = [], []
    for a, b in pairs:
        diff = table[a] - table[b]
        if len(diff) < 3 or np.allclose(diff, 0):
            p_value, statistic = 1.0, 0.0
        else:
            res = stats.wilcoxon(table[a], table[b], zero_method="wilcox")
            p_value, statistic = float(res.pvalue), float(res.statistic)
        raw.append(p_value)
        rows.append({"a": a, "b": b, "mean_difference": float(diff.mean()), "statistic": statistic, "p_value": p_value})
    for row, adjusted in zip(rows, holm(raw), strict=True):
        row["p_holm"] = adjusted
    return rows


def bootstrap_ci(values: np.ndarray, *, reps: int = 5000, seed: int = 42, level: float = 0.95) -> tuple[float, float]:
    values = np.asarray(values, dtype=float)
    if values.size < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    samples = rng.choice(values, size=(reps, values.size), replace=True).mean(axis=1)
    tail = (1 - level) / 2 * 100
    low, high = np.percentile(samples, [tail, 100 - tail])
    return float(low), float(high)


def voice_summary(table: pd.DataFrame, seed: int = 42) -> pd.DataFrame:
    rows = []
    for voice in table.columns:
        values = table[voice].to_numpy()
        low, high = bootstrap_ci(values, seed=seed)
        rows.append(
            {
                "voice": voice,
                "participants": len(values),
                "mean": values.mean(),
                "sd": values.std(ddof=1) if len(values) > 1 else np.nan,
                "ci_low": low,
                "ci_high": high,
            }
        )
    return pd.DataFrame(rows).set_index("voice")


def mixed_model(df: pd.DataFrame, metric: str) -> dict[str, Any]:
    """Answer-level mixed model; returns voice coefficients against the reference voice."""
    import statsmodels.formula.api as smf

    data = df[["participant", "voice", "block", metric]].dropna()
    if data["participant"].nunique() < 4 or data["voice"].nunique() < 2:
        return {"estimated": False, "reason": "not enough participants"}
    reference = REFERENCE_VOICE if REFERENCE_VOICE in set(data["voice"]) else sorted(data["voice"].unique())[0]
    formula = f"{metric} ~ C(voice, Treatment('{reference}')) + C(block)"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fit = smf.mixedlm(formula, data, groups=data["participant"]).fit(reml=True, method="lbfgs")
    except (ValueError, np.linalg.LinAlgError) as exc:
        return {"estimated": False, "reason": str(exc)}
    effects = {}
    for name, value in fit.params.items():
        if name.startswith("C(voice"):
            voice = name.split("[T.")[1].rstrip("]")
            low, high = fit.conf_int().loc[name]
            effects[voice] = {
                "estimate": float(value),
                "ci_low": float(low),
                "ci_high": float(high),
                "p_value": float(fit.pvalues[name]),
            }
    return {
        "estimated": True,
        "reference_voice": reference,
        "converged": bool(fit.converged),
        "n_answers": len(data),
        "voice_effects": effects,
    }
