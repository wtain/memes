from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _group():
    return yaml.safe_load((ROOT / "environments/settings.yaml").read_text(encoding="utf-8"))["comics"]


def test_comics_group_has_exactly_jpeg_quality_in_the_common_file():
    assert set(_group()) == {"jpeg_quality"}


def test_jpeg_quality_is_a_valid_quality():
    assert 1 <= _group()["jpeg_quality"] <= 100
