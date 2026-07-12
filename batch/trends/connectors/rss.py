import time

import cloudscraper
import feedparser
from bs4 import BeautifulSoup

# todo: check https://www.reddit.com/r/webscraping/comments/187ji5f/cloudscraper_with_asyncio/
# todo: retries


class RSSConnector:

    def __init__(self, name: str, config: dict, delay_seconds: float = 1):
        self.name = name
        self.rss_feed_url = config["url"]
        self.css_selector = config["selector"]
        self.delay_seconds = delay_seconds

    def _fetch_rss(self):
        feed = feedparser.parse(self.rss_feed_url)
        return feed.entries

    def _extract_article_content(self, url):
        try:
            time.sleep(self.delay_seconds)  # be polite
            scraper = cloudscraper.create_scraper()
            r = scraper.get(url)

            if r.status_code // 100 != 2:
                raise RuntimeError(f"{url}: {r.status_code}")
            soup = BeautifulSoup(r.text, "html.parser")

            paragraphs = soup.select(self.css_selector)

            text = "\n".join(p.get_text() for p in paragraphs)
            return text.strip()

        except Exception as e:
            print(f"Error fetching article {url}: {e}")
            return ""

    def fetch(self) -> list[dict]:
        entries = self._fetch_rss()
        results = []

        for entry in entries:
            title = entry.get("title", "")
            link = entry.get("link", "")
            pub_date = entry.get("pubDate", "")

            print(f"Processing: {title}")

            content = entry.get("content:encoded", "")
            if not content:
                content = self._extract_article_content(link)

            results.append({
                "source": self.name,
                "title": title,
                "url": link,
                "published": pub_date,
                "text": content,
            })

        return results
