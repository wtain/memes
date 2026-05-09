import time

import cloudscraper
import feedparser
from bs4 import BeautifulSoup

# todo: check https://www.reddit.com/r/webscraping/comments/187ji5f/cloudscraper_with_asyncio/
# todo: retries

class RSSScraper:

    def __init__(self, sourceName, rssFeedUrl, cssSelector, delaySeconds=1):
        self.sourceName = sourceName
        self.rssFeedUrl = rssFeedUrl
        self.cssSelector = cssSelector
        self.delaySeconds = delaySeconds

    def _fetch_rss(self):
        feed = feedparser.parse(self.rssFeedUrl)
        return feed.entries

    def _extract_article_content(self, url):
        try:
            time.sleep(self.delaySeconds)  # be polite
            scraper = cloudscraper.create_scraper()
            r = scraper.get(url)

            if r.status_code // 100 != 2:
                raise RuntimeError(f"{url}: {r.status_code}")
            soup = BeautifulSoup(r.text, "html.parser")

            paragraphs = soup.select(self.cssSelector)

            text = "\n".join(p.get_text() for p in paragraphs)
            return text.strip()

        except Exception as e:
            print(f"Error fetching article {url}: {e}")
            return ""

    def parse(self):
        entries = self._fetch_rss()
        results = []

        for entry in entries:
            title = entry.get("title", "")
            link = entry.get("link", "")
            pubDate = entry.get("pubDate", "")

            print(f"Processing: {title}")

            content = entry.get("content:encoded", "")
            if not content:
                content = self._extract_article_content(link)

            item = {
                "source": self.sourceName,
                "title": title,
                "url": link,
                "published": pubDate,
                "text": content
            }

            results.append(item)

        return results