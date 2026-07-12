import pytest

from batch.trends.connectors.api import MeduzaConnector
from batch.trends.connectors.registry import get_connector
from batch.trends.connectors.rss import RSSConnector


def test_rss_connector_uses_content_encoded_without_scraping(monkeypatch):
    entry = {
        "title": "New Album Announced",
        "link": "https://example.com/article",
        "pubDate": "2026-07-01",
        "content:encoded": "Full article body already in the feed.",
    }

    class _FakeFeed:
        entries = [entry]

    monkeypatch.setattr("batch.trends.connectors.rss.feedparser.parse", lambda url: _FakeFeed())

    def _fail_scraper():
        raise AssertionError("should not scrape when content:encoded is present")

    monkeypatch.setattr("batch.trends.connectors.rss.cloudscraper.create_scraper", _fail_scraper)

    connector = RSSConnector("LoudWire", {"url": "https://example.com/feed", "selector": "div.pod-content p"})
    items = connector.fetch()

    assert items == [{
        "source": "LoudWire",
        "title": "New Album Announced",
        "url": "https://example.com/article",
        "published": "2026-07-01",
        "text": "Full article body already in the feed.",
    }]


def test_rss_connector_scrapes_article_when_no_content_encoded(monkeypatch):
    entry = {
        "title": "New Album Announced",
        "link": "https://example.com/article",
        "pubDate": "2026-07-01",
    }

    class _FakeFeed:
        entries = [entry]

    monkeypatch.setattr("batch.trends.connectors.rss.feedparser.parse", lambda url: _FakeFeed())
    monkeypatch.setattr("batch.trends.connectors.rss.time.sleep", lambda seconds: None)

    class _FakeResponse:
        status_code = 200
        text = "<div class='pod-content'><p>Scraped paragraph.</p></div>"

    class _FakeScraper:
        def get(self, url):
            return _FakeResponse()

    monkeypatch.setattr("batch.trends.connectors.rss.cloudscraper.create_scraper", lambda: _FakeScraper())

    connector = RSSConnector("LoudWire", {"url": "https://example.com/feed", "selector": "div.pod-content p"})
    items = connector.fetch()

    assert items[0]["text"] == "Scraped paragraph."


def test_meduza_connector_paginates_until_empty_page(monkeypatch):
    pages = {
        1: {"documents": {"a": {"title": "Title A"}, "b": {"title": "Title B", "second_title": "Sub B"}}},
        2: {"documents": {}},
    }

    class _FakeResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def json(self):
            return self._payload

    def _fake_get(url, headers, params):
        return _FakeResponse(pages[params["page"]])

    monkeypatch.setattr("batch.trends.connectors.api.requests.get", _fake_get)
    monkeypatch.setattr("batch.trends.connectors.api.time.sleep", lambda seconds: None)

    connector = MeduzaConnector("Meduza", {"base_url": "https://meduza.io/api/w5/new_search", "num_pages": 5})
    items = connector.fetch()

    titles = {item["title"] for item in items}
    assert titles == {"Title A", "Title B: Sub B"}
    assert all(item["source"] == "Meduza" for item in items)


def test_meduza_connector_raises_on_non_200(monkeypatch):
    class _FakeResponse:
        status_code = 429

    monkeypatch.setattr(
        "batch.trends.connectors.api.requests.get",
        lambda url, headers, params: _FakeResponse(),
    )

    connector = MeduzaConnector("Meduza", {"base_url": "https://meduza.io/api/w5/new_search"})

    with pytest.raises(RuntimeError):
        connector.fetch()


def test_registry_returns_rss_connector():
    connector = get_connector("LoudWire", "rss", {"url": "https://example.com/feed", "selector": "p"})
    assert isinstance(connector, RSSConnector)


def test_registry_returns_api_connector():
    connector = get_connector("Meduza", "api", {"base_url": "https://meduza.io/api/w5/new_search"})
    assert isinstance(connector, MeduzaConnector)


def test_registry_raises_for_unknown_connector_type():
    with pytest.raises(ValueError):
        get_connector("Mystery", "carrier-pigeon", {})
