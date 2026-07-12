from typing import Protocol


class Connector(Protocol):
    def fetch(self) -> list[dict]:
        """Return items shaped {source, title, url, published, text}."""
        ...
