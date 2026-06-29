from collections import Counter

import cloudscraper
import feedparser
from bs4 import BeautifulSoup
import time

from gliner import GLiNER

"""
https://feeds.feedburner.com/metalinjection
https://blabbermouth.net/feed
https://www.metaltalk.net/feed
"""

RSS_URL = "https://feeds.feedburner.com/metalinjection"


def fetch_rss():
    feed = feedparser.parse(RSS_URL)
    return feed.entries


def extract_article_content(url):
    try:
        time.sleep(1)  # be polite
        scraper = cloudscraper.create_scraper()
        r = scraper.get(url)

        if r.status_code // 100 != 2:
            print(f"Error: {r.status_code}")
        soup = BeautifulSoup(r.text, "html.parser")

        # Loudwire article content container (may evolve)
        paragraphs = soup.select("div.zox-post-body p")

        text = "\n".join(p.get_text() for p in paragraphs)
        return text.strip()

    except Exception as e:
        print(f"Error fetching article {url}: {e}")
        return ""


def parse_metalinjection():
    entries = fetch_rss()
    results = []

    for entry in entries:
        title = entry.get("title", "")
        link = entry.get("link", "")
        pubDate = entry.get("pubDate", "")

        print(f"Processing: {title}")

        content = extract_article_content(link)

        item = {
            "source": "metalinjection",
            "title": title,
            "url": link,
            "published": pubDate,
            "text": content
        }

        results.append(item)

    return results

"""
dslim/bert-base-NER
tner/roberta-large-music-ner - try this


from gliner import GLiNER

model = GLiNER.from_pretrained("urchade/gliner_medium-v2.1")
text = "The new tracks from Sleep Token show a mix of progressive metal and pop."
labels = ["band", "music genre"]

entities = model.predict_entities(text, labels)
for entity in entities:
    print(f"{entity['text']} => {entity['label']}")

"""

class Processor:

    def __init__(self):
        self.model = GLiNER.from_pretrained("urchade/gliner_medium-v2.1")

    def process(self, text):

        labels = ["band", "music genre"]

        entities = self.model.predict_entities(text, labels)

        return [(entity['text'], entity['label']) for entity in entities]


if __name__ == "__main__":
    data = parse_metalinjection()

    processor = Processor()

    trends = Counter()

    for item in data:
        print("\n---")
        title = item["title"]
        print(title)
        text = item["text"]
        for text, label in processor.process(text):
            trends[f"{label}:{text}"] += 1

    print(trends)