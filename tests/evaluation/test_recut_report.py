import json
from pathlib import Path

import pytest

from evaluation import __main__ as cli
from evaluation.recut_report import BAND_LABELS, band_of, build_report, format_report, load_report, wilson

REAL_GOLD = Path(__file__).resolve().parents[2] / "evaluation" / "gold" / "recut"


@pytest.mark.parametrize("confidence, label", [
    (0.6, "0.6-0.7"), (0.69, "0.6-0.7"), (0.7, "0.7-0.8"), (0.8, "0.8-0.9"), (0.9, "0.9-1.0"),
    (0.99, "0.9-1.0"), (1.0, "1.0"), (0.55, "0.6-0.7")])
def test_band_edges(confidence, label):
    assert band_of(confidence) == label


def test_the_bands_are_the_documented_five():
    assert BAND_LABELS == ("0.6-0.7", "0.7-0.8", "0.8-0.9", "0.9-1.0", "1.0")


def test_wilson_interval_for_known_values():
    low, high = wilson(118, 124)
    assert low == pytest.approx(0.8985, abs=0.001) and high == pytest.approx(0.9776, abs=0.001)
    assert wilson(0, 0) == (0.0, 0.0)


def _fixture():
    round1_key = {"i0": {"id": "a", "box": [0, 0, 1, 1], "by": ["A"], "confidence": 1.0},
                  "i1": {"id": "b", "box": [0, 0, 1, 1], "by": ["C"], "confidence": 0.85},
                  "i2": {"id": "c", "box": [0, 0, 1, 1], "by": ["B"]}, "i3": {"id": "d", "box": [0, 0, 1, 1], "by": ["A"], "confidence": 1.0},
                  "i4": {"id": "e", "box": [0, 0, 1, 1], "by": ["none"]}, "i5": {"id": "f", "box": [0, 0, 1, 1], "by": ["none"]},
                  "i6": {"id": "g", "box": [0, 0, 1, 1], "by": ["none"]}}
    round1 = {"i0": "2", "i1": "1", "i2": "2", "i3": "s", "i4": "n", "i5": "y", "i6": "s"}
    round2_key = {"i0": {"id": "h", "box": [0, 0, 1, 1], "band": "margin 0.6-0.7", "confidence": 0.65},
                  "i1": {"id": "i", "box": [0, 0, 1, 1], "band": "margin 0.7-0.8", "confidence": 0.75},
                  "i2": {"id": "j", "box": [0, 0, 1, 1], "band": "inner", "confidence": 0.4},
                  "i3": {"id": "k", "box": [0, 0, 1, 1], "band": "margin 0.9-1.0", "confidence": 0.95}}
    round2 = {"i0": "2", "i1": "0", "i2": "2", "i3": "2"}
    return round1, round1_key, round2, round2_key


def test_build_report_counts_only_the_shipped_detectors_proposals():
    report = build_report(*_fixture())

    assert report["bands"]["1.0"] == {"graded": 1, "as_is": 1, "slightly_off": 0, "wrong": 0}
    assert report["bands"]["0.8-0.9"] == {"graded": 1, "as_is": 0, "slightly_off": 1, "wrong": 0}
    assert report["bands"]["0.6-0.7"] == {"graded": 1, "as_is": 1, "slightly_off": 0, "wrong": 0}
    assert report["bands"]["0.7-0.8"] == {"graded": 1, "as_is": 0, "slightly_off": 0, "wrong": 1}
    assert report["bands"]["0.9-1.0"] == {"graded": 1, "as_is": 1, "slightly_off": 0, "wrong": 0}
    assert report["unbanded"] == {"graded": 0, "as_is": 0, "slightly_off": 0, "wrong": 0}
    assert report["total"] == {"graded": 5, "as_is": 3, "slightly_off": 1, "wrong": 1}
    assert report["excluded"] == {"inner": 1, "skipped": 1, "ungraded": 0}


def test_recall_is_not_measurable_and_the_evidence_is_given():
    report = build_report(*_fixture())

    assert report["recall"] == {"measurable": False, "judged_no_proposal": 2, "needed_a_cut": 1,
                                "skipped_no_proposal": 1}


def test_an_item_without_a_judgment_is_counted_as_ungraded():
    round1, round1_key, round2, round2_key = _fixture()
    del round1["i0"]

    report = build_report(round1, round1_key, round2, round2_key)

    assert report["excluded"]["ungraded"] == 1
    assert report["bands"]["1.0"]["graded"] == 0


def test_a_round_one_proposal_without_a_recorded_confidence_is_counted_unbanded_not_guessed():
    round1, round1_key, round2, round2_key = _fixture()
    round1_key["i0"].pop("confidence")

    report = build_report(round1, round1_key, round2, round2_key)

    assert report["unbanded"] == {"graded": 1, "as_is": 1, "slightly_off": 0, "wrong": 0}
    assert report["bands"]["1.0"]["graded"] == 0
    assert report["total"]["graded"] == 5
    assert "no recorded confidence" in format_report(report)


def test_format_report_says_recall_is_not_measurable_and_names_the_detector():
    text = format_report(build_report(*_fixture()))

    assert "not measurable" in text
    assert "detector A" in text and "margin_trim" in text
    for label in BAND_LABELS:
        assert label in text


def test_the_real_gold_files_give_the_documented_totals():
    report = load_report(REAL_GOLD)

    assert report["total"] == {"graded": 124, "as_is": 118, "slightly_off": 5, "wrong": 1}
    assert {label: counts["graded"] for label, counts in report["bands"].items()} == {
        "0.6-0.7": 12, "0.7-0.8": 27, "0.8-0.9": 21, "0.9-1.0": 20, "1.0": 44}
    assert report["bands"]["0.7-0.8"]["wrong"] == 1
    assert report["unbanded"]["graded"] == 0
    assert report["recall"]["measurable"] is False
    assert report["recall"]["needed_a_cut"] == 0


def test_the_cli_prints_the_report_without_a_database(capsys):
    assert cli.main(["--gold-dir", str(REAL_GOLD.parent), "recut-report"]) == 0

    assert "not measurable" in capsys.readouterr().out


def test_the_cli_reports_missing_gold_files(tmp_path, capsys):
    assert cli.main(["--gold-dir", str(tmp_path), "recut-report"]) != 0
