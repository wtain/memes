from collections import Counter

from batch.trends.processing import Processor
from batch.trends.scraping import RSSScraper

if __name__ == "__main__":
    sources = [
        {
            "url": "https://loudwire.com/feed/",
            "name": "LoudWire",
            "selector": "div.pod-content p",
        },
        {
            "url": "https://feeds.feedburner.com/metalinjection",
            "name": "Metal Injection",
            "selector": "div.zox-post-body p",
        },

        {
            "url": "https://blabbermouth.net/feed",
            "name": "Blabbermouth",
            "selector": "div.news-content p",
        },
        {
            "url": "https://www.metaltalk.net/feed",
            "name": "Metaltalk",
            "selector": "div.tdb-block-inner p",
        },
        {
            "url": "https://www.angrymetalguy.com/feed/",
            "name": "Angry Metal Guy",
            "selector": "div.entry-content p",
        },
        {
            "url": "https://www.invisibleoranges.com/feed",
            "name": "Invisible Oranges",
            "selector": "section.ap-main p",
            # in-place: <content:encoded>
        },
    ]

    # FeedSource (id, name, url, selector)
    # TrendsRun (run_id, created_at)
    # TrendsRunResults (id, run_id, source_id, label, name, value)

    processor = Processor()

    trends = Counter()

    for source in sources:
        scraper = RSSScraper(source["name"], source["url"], source["selector"])
        data = scraper.parse()
        print(f"Scraping {source['name']}")
        for item in data:
            print("\n---")
            title = item["title"]
            print(title)
            text = item["text"]
            for text, label in processor.process(text):
                trends[f"{label}:{text}"] += 1

    for topic in sorted(trends.keys(), key=lambda k: trends[k]):
        print(f"{topic} ({trends[topic]})")