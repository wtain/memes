import pytest

from batch.eval_ocr_language_filter import (
    _classify,
    _score_golden_items,
    evaluate_at_threshold,
    threshold_sweep,
)


def test_classify_none_score_never_flagged():
    assert _classify(None, threshold=0.9) is False


def test_classify_low_score_flagged():
    assert _classify(0.1, threshold=0.3) is True


def test_classify_high_score_not_flagged():
    assert _classify(0.9, threshold=0.3) is False


def test_evaluate_at_threshold_basic_counts():
    scored_items = [
        (1.0, False),   # TN
        (0.0, True),    # TP
        (None, False),  # TN (pass-through)
        (0.1, False),   # FP - genuine row scored low, wrongly flagged
    ]
    result = evaluate_at_threshold(scored_items, threshold=0.3)

    assert result["tp"] == 1
    assert result["fp"] == 1
    assert result["fn"] == 0
    assert result["tn"] == 2
    assert result["precision"] == pytest.approx(0.5)
    assert result["recall"] == pytest.approx(1.0)
    assert result["false_suppression_rate"] == pytest.approx(1 / 3)


def test_evaluate_at_threshold_no_positives_defaults_to_perfect_precision_recall():
    scored_items = [(1.0, False), (0.9, False)]
    result = evaluate_at_threshold(scored_items, threshold=0.3)

    assert result["tp"] == 0
    assert result["fp"] == 0
    assert result["fn"] == 0
    assert result["precision"] == pytest.approx(1.0)
    assert result["recall"] == pytest.approx(1.0)
    assert result["false_suppression_rate"] == pytest.approx(0.0)


def test_threshold_sweep_returns_one_result_per_threshold():
    scored_items = [(1.0, False), (0.0, True)]
    thresholds = [0.1, 0.3, 0.5]

    results = threshold_sweep(scored_items, thresholds)

    assert [r["threshold"] for r in results] == thresholds


def test_score_golden_items_uses_real_lang_plausibility_score():
    items = [
        {"text": "when your friends finally get the joke", "language": "en", "is_garbage": False},
        {"text": "lol", "language": "en", "is_garbage": False},
    ]

    scored = _score_golden_items(items)

    assert scored[0][0] == pytest.approx(1.0)
    assert scored[0][1] is False
    assert scored[1][0] is None
    assert scored[1][1] is False
