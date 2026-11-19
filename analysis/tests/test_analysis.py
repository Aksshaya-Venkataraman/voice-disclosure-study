import csv
import json

import numpy as np
import pandas as pd
import pytest

from disclosure_analysis.report import analyse, load, main
from disclosure_analysis.simulate import COLUMNS, DEFAULT_EFFECTS, simulate
from disclosure_analysis.stats import bootstrap_ci, friedman, holm, participant_means
from disclosure_analysis.text_features import add_features, features


def write(rows, path):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_text_features():
    f = features("I'm really proud of my team, and I felt stressed but happy.")
    assert f["words"] == 12
    assert f["first_person_rate"] == pytest.approx(100 * 3 / 12)  # i'm, my, i
    assert f["affect_rate"] == pytest.approx(100 * 3 / 12)  # proud, stressed, happy
    assert f["content_words"] == 5  # proud team felt stressed happy
    assert features("")["words"] == 0 and features("")["first_person_rate"] == 0


def test_speech_rate_only_when_timed():
    df = add_features(pd.DataFrame({"transcript": ["one two three four", "a b"], "duration_seconds": [2.0, None]}))
    assert df.loc[0, "speech_rate_wpm"] == pytest.approx(120)
    assert np.isnan(df.loc[1, "speech_rate_wpm"])


def test_simulation_is_deterministic_and_matches_export_format():
    a, b = simulate(8, seed=3), simulate(8, seed=3)
    assert a == b
    assert set(a[0]) == set(COLUMNS)
    assert len(a) == 8 * 16


def test_holm_correction():
    assert holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert holm([0.5]) == [0.5]


def test_bootstrap_ci_contains_mean():
    values = np.arange(1, 21, dtype=float)
    low, high = bootstrap_ci(values, seed=1)
    assert low < values.mean() < high


def test_detects_a_real_voice_effect(tmp_path):
    df = load(write(simulate(24, seed=11), tmp_path / "effect.csv"))
    results = analyse(df)
    words = results["metrics"]["words"]
    assert words["friedman"]["p_value"] < 0.01
    by_voice = words["by_voice"]
    assert by_voice["robot"]["mean"] < by_voice["megan"]["mean"]
    model = words["mixed_model"]
    assert model["estimated"] and model["voice_effects"]["robot"]["estimate"] < 0


def test_no_false_alarm_without_an_effect(tmp_path):
    rows = simulate(24, effects=dict.fromkeys(DEFAULT_EFFECTS, 1.0), seed=5)
    results = analyse(load(write(rows, tmp_path / "null.csv")))
    assert results["metrics"]["words"]["friedman"]["p_value"] > 0.05


def test_only_complete_participants_enter_the_friedman_test(tmp_path):
    rows = [r for r in simulate(6, seed=2) if not (r["participant"] == "P001" and r["voice"] == "robot")]
    table = participant_means(load(write(rows, tmp_path / "partial.csv")), "words")
    assert "P001" not in table.index and len(table) == 5
    assert friedman(table)["n"] == 5


def test_warmup_answers_are_excluded(tmp_path):
    rows = simulate(4, seed=1)
    rows.append({**rows[0], "kind": "warmup", "block": "", "transcript": "great thanks " * 100})
    df = load(write(rows, tmp_path / "w.csv"))
    assert (df["kind"] == "question").all() and len(df) == 64


def test_missing_columns_rejected(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("participant,transcript\nP001,hello\n")
    with pytest.raises(ValueError, match="missing columns"):
        load(path)


def test_report_cli_writes_outputs(tmp_path):
    export = write(simulate(8, seed=4), tmp_path / "export.csv")
    out = tmp_path / "report"
    main([str(export), "--out", str(out)])
    assert (out / "report.md").read_text().startswith("# Impact of interviewer voice on disclosure")
    assert json.loads((out / "results.json").read_text())["participants"] == 8
    assert (out / "figures" / "words.png").stat().st_size > 1000
    assert (out / "participant_means_words.csv").exists()
