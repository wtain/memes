"""A missing or invalid search ranking setting stops the backend at startup instead of failing a later request."""
import pytest

from repository import search_ranking


def test_shipped_settings_validate():
    search_ranking.validate_search_settings()


@pytest.mark.parametrize("loader", ["get_weights", "get_popularity_boost", "get_trending_min_popularity"])
def test_every_search_loader_is_checked(monkeypatch, loader):
    def broken():
        raise ValueError(f"{loader} is broken")

    monkeypatch.setattr(search_ranking, loader, broken)

    with pytest.raises(ValueError, match=loader):
        search_ranking.validate_search_settings()


async def test_lifespan_propagates_a_settings_error(monkeypatch):
    from Backend.app import main

    def broken():
        raise ValueError("search.ranking.popularity missing")

    monkeypatch.setattr(main, "validate_search_settings", broken)

    with pytest.raises(ValueError, match="search.ranking.popularity"):
        async with main.lifespan(main.app):
            pass


async def test_lifespan_propagates_an_impression_settings_error(monkeypatch):
    from Backend.app import main

    def broken():
        raise ValueError("popularity.impressions.window_days missing")

    monkeypatch.setattr(main, "validate_impression_settings", broken)

    with pytest.raises(ValueError, match="popularity.impressions.window_days"):
        async with main.lifespan(main.app):
            pass
