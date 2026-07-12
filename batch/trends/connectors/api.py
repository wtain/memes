import time

import requests


class MeduzaConnector:

    def __init__(self, name: str, config: dict):
        self.name = name
        self.base_url = config["base_url"]
        self.locale = config.get("locale", "ru")
        self.per_page = config.get("per_page", 100)
        self.num_pages = config.get("num_pages", 100)
        self.sleep_every_pages = config.get("sleep_every_pages", 10)
        self.sleep_seconds = config.get("sleep_seconds", 10)

    def _load_page(self, page: int) -> dict:
        print(f"Loading page {page}")
        result = requests.get(
            self.base_url,
            headers={"User-Agent": ""},
            params={
                "chrono": "news",
                "page": page,
                "per_page": self.per_page,
                "locale": self.locale,
            },
        )
        if result.status_code != 200:
            raise RuntimeError(f"{self.base_url}: {result.status_code}")
        return result.json()

    def fetch(self) -> list[dict]:
        results = []

        for page in range(1, self.num_pages + 1):
            documents = self._load_page(page)["documents"]
            print(f"Loaded {len(documents)} documents")
            if not documents:
                break

            for document in documents.values():
                title = document["title"]
                if "second_title" in document:
                    title = f"{title}: {document['second_title']}"

                results.append({
                    "source": self.name,
                    "title": title,
                    "url": document.get("url", ""),
                    "published": document.get("datetime", ""),
                    "text": title,
                })

            if page % self.sleep_every_pages == 0:
                print(f"Sleeping {self.sleep_seconds} seconds")
                time.sleep(self.sleep_seconds)

        return results
