from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load(group):
    return yaml.safe_load((ROOT / "environments/settings.yaml").read_text(encoding="utf-8"))[group]


def _group():
    return _load("comics")


def test_comics_group_has_exactly_jpeg_quality_in_the_common_file():
    assert set(_group()) == {"jpeg_quality"}


def test_jpeg_quality_is_a_valid_quality():
    assert 1 <= _group()["jpeg_quality"] <= 100


def test_comics_candidates_group_has_exactly_the_required_keys_with_sane_values():
    group = _load("comics_candidates")
    assert set(group) == {"gap_seconds", "min_distance", "max_distance", "max_group", "max_candidates_per_run"}
    assert group["gap_seconds"] > 0 and group["max_candidates_per_run"] > 0
    assert 0 <= group["min_distance"] < group["max_distance"] <= 1
    assert group["max_group"] >= 2


def test_comics_candidates_settings_are_read_through_the_settings_object():
    from config.settings import settings
    assert settings.COMICS_CANDIDATES.GAP_SECONDS == _load("comics_candidates")["gap_seconds"]
