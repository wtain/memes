from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
KEYS = {"min_removed_share", "max_removed_share", "flat_tolerance", "ocr_overlap_max", "commit_interval"}


def _group():
    return yaml.safe_load((ROOT / "environments/settings.yaml").read_text(encoding="utf-8"))["recut"]


def test_recut_group_has_every_key_in_the_common_file():
    assert set(_group()) == KEYS


def test_recut_values_are_sane():
    g = _group()
    assert 0 < g["min_removed_share"] < g["max_removed_share"] < 1
    assert g["flat_tolerance"] > 0
    assert 0 < g["ocr_overlap_max"] <= 1
    assert g["commit_interval"] >= 1
