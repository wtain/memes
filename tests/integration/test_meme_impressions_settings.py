"""popularity.impressions.* settings: shipped values load, a missing or invalid key is a startup error."""
import pytest

from repository import meme_impressions as mi


class FakeSettings:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


def _fake(monkeypatch, **values):
    monkeypatch.setattr(mi, "settings", FakeSettings(
        {f"popularity.impressions.{name}": value for name, value in values.items()}))


def test_shipped_values_load():
    assert mi.impressions_enabled() is True
    assert mi.window_days() == 30
    mi.validate_impression_settings()


def test_valid_values_are_returned(monkeypatch):
    _fake(monkeypatch, enabled=False, window_days=7)
    assert mi.impressions_enabled() is False
    assert mi.window_days() == 7


def test_missing_window_days_raises(monkeypatch):
    _fake(monkeypatch, enabled=True)
    with pytest.raises(ValueError, match="window_days"):
        mi.window_days()


@pytest.mark.parametrize("bad", [0, -1, True, "30", 1.5, None])
def test_invalid_window_days_raises(monkeypatch, bad):
    _fake(monkeypatch, enabled=True, window_days=bad)
    with pytest.raises(ValueError, match="window_days"):
        mi.window_days()
    with pytest.raises(ValueError):
        mi.validate_impression_settings()


@pytest.mark.parametrize("bad", ["yes", 1, None])
def test_invalid_enabled_raises(monkeypatch, bad):
    _fake(monkeypatch, enabled=bad, window_days=30)
    with pytest.raises(ValueError, match="enabled"):
        mi.impressions_enabled()
    with pytest.raises(ValueError):
        mi.validate_impression_settings()
