import json

from batch.tools import meme_profile_pilot as pilot


def _candidate(index, stratum):
    return {"image_id": f"id-{index}", "filename": f"{index}.jpg", "stratum": stratum, "path": "text",
            "bundle": {"note": None, "descriptions": [], "ocr": f"text {index}"}}


class TestStratify:
    def test_takes_up_to_the_quota_per_stratum_deterministically(self):
        candidates = [_candidate(i, "ocr+description") for i in range(30)] + \
                     [_candidate(100 + i, "ocr only") for i in range(12)] + \
                     [_candidate(200 + i, "vision") for i in range(2)]
        quotas = {"ocr+description": 20, "ocr only": 10, "description only": 5, "vision": 5}

        first = pilot.stratify(candidates, seed=7, quotas=quotas)
        second = pilot.stratify(candidates, seed=7, quotas=quotas)

        assert first == second
        counts = {s: sum(1 for item in first if item["stratum"] == s) for s in quotas}
        assert counts == {"ocr+description": 20, "ocr only": 10, "description only": 0, "vision": 2}

    def test_a_different_seed_gives_a_different_sample(self):
        candidates = [_candidate(i, "ocr only") for i in range(40)]
        quotas = {"ocr only": 10}

        assert pilot.stratify(candidates, 1, quotas) != pilot.stratify(candidates, 2, quotas)


class TestShuffledLabels:
    def test_every_model_gets_exactly_one_label_and_it_is_stable_per_item(self):
        models = ["qwen2", "qwen2.5vl:7b", "other"]

        labels = pilot.shuffled_labels(models, seed=3, item_id="id-1")

        assert sorted(labels) == ["A", "B", "C"]
        assert sorted(labels.values()) == sorted(models)
        assert labels == pilot.shuffled_labels(models, seed=3, item_id="id-1")

    def test_the_order_differs_between_items(self):
        models = ["m1", "m2", "m3", "m4"]
        orders = {tuple(pilot.shuffled_labels(models, 3, f"id-{i}").values()) for i in range(20)}
        assert len(orders) > 1


def _run(ok=True, seconds=1.0, emotion="joy"):
    return {"ok": ok, "error": None if ok else "bad", "seconds": seconds,
            "profile": {"emotion": [{"key": emotion}], "stance": [], "topic": [], "tone": [], "situation": "s"},
            "use_when": "u"}


def _results():
    return [
        {"image_id": "i1", "runs": {"good": [_run(seconds=2.0), _run(seconds=2.0)],
                                     "fast": [_run(seconds=1.0), _run(seconds=1.0, emotion="anger")]}},
        {"image_id": "i2", "runs": {"good": [_run(seconds=2.0), _run(seconds=2.0)],
                                     "fast": [_run(ok=False, seconds=1.0), _run(seconds=1.0)]}},
    ]


class TestSummarize:
    def test_valid_rate_stability_seconds_and_mean_rating(self):
        labels = {"i1": {"A": "good", "B": "fast"}, "i2": {"A": "fast", "B": "good"}}
        ratings = {"i1": {"A": "best", "B": "poor"}, "i2": {"A": "acceptable", "B": "best"}}

        stats = pilot.summarize(_results(), ratings, labels)

        assert stats["good"] == {"valid_rate": 1.0, "stability": 1.0, "mean_seconds": 2.0,
                                 "mean_rating": 2.0, "rated": 2}
        assert stats["fast"]["valid_rate"] == 0.75
        assert stats["fast"]["stability"] == 0.0          # i1 differs between runs, i2 had an invalid run
        assert stats["fast"]["mean_seconds"] == 1.0
        assert stats["fast"]["mean_rating"] == 0.5        # poor (0) and acceptable (1)
        assert stats["fast"]["rated"] == 2

    def test_unrated_outputs_do_not_count_towards_the_mean(self):
        labels = {"i1": {"A": "good", "B": "fast"}}
        stats = pilot.summarize(_results()[:1], {"i1": {"A": "best"}}, labels)

        assert stats["good"]["rated"] == 1 and stats["fast"]["rated"] == 0
        assert stats["fast"]["mean_rating"] is None


class TestPickWinner:
    def test_highest_mean_rating_among_models_with_enough_valid_output(self):
        stats = {"a": {"valid_rate": 1.0, "mean_rating": 1.5, "mean_seconds": 5.0},
                 "b": {"valid_rate": 0.97, "mean_rating": 2.0, "mean_seconds": 1.0},
                 "c": {"valid_rate": 0.99, "mean_rating": 1.2, "mean_seconds": 1.0}}

        assert pilot.pick_winner(stats) == "a"          # b is disqualified (97% < 98%)

    def test_ties_go_to_the_faster_model(self):
        stats = {"slow": {"valid_rate": 1.0, "mean_rating": 1.5, "mean_seconds": 5.0},
                 "quick": {"valid_rate": 1.0, "mean_rating": 1.5, "mean_seconds": 2.0}}

        assert pilot.pick_winner(stats) == "quick"

    def test_none_when_nobody_qualifies_or_nothing_was_rated(self):
        assert pilot.pick_winner({"a": {"valid_rate": 0.5, "mean_rating": 2.0, "mean_seconds": 1.0}}) is None
        assert pilot.pick_winner({"a": {"valid_rate": 1.0, "mean_rating": None, "mean_seconds": 1.0}}) is None


class TestRenderSheet:
    def test_blinded_labels_only_and_an_export_button(self):
        items = [{"image_id": "id-1", "filename": "1.jpg", "stratum": "ocr only", "path": "text",
                  "bundle": {"note": None, "descriptions": [], "ocr": "HELLO <b>"},
                  "runs": {"secret-model-name": [_run(), _run()], "other-model": [_run(emotion="anger"), _run()]}}]
        labels = {"id-1": {"A": "secret-model-name", "B": "other-model"}}

        html = pilot.render_sheet(items, labels, thumbnails={"id-1": "data:image/jpeg;base64,AAAA"})

        assert "secret-model-name" not in html and "other-model" not in html
        assert "Output A" in html and "Output B" in html
        assert "HELLO &lt;b&gt;" in html                    # inputs are HTML-escaped
        assert "data:image/jpeg;base64,AAAA" in html
        assert "ratings.json" in html and "best" in html and "acceptable" in html and "poor" in html
        assert "anger" in html and "joy" in html
