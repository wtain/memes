from collections import Counter
from types import SimpleNamespace

from batch.trends_batch import process_source


class _FakeConnector:
    def __init__(self, items):
        self._items = items

    def fetch(self):
        return self._items


class _FakeProcessor:
    def __init__(self, entities_by_text):
        self._entities_by_text = entities_by_text

    def process(self, text, model_name, labels):
        return self._entities_by_text.get(text, [])


def test_process_source_tallies_entities_across_items():
    source = SimpleNamespace(name="LoudWire")
    connector = _FakeConnector([
        {"title": "A", "text": "Sleep Token dominate the charts"},
        {"title": "B", "text": "Sleep Token again"},
    ])
    processor = _FakeProcessor({
        "Sleep Token dominate the charts": [("Sleep Token", "band")],
        "Sleep Token again": [("Sleep Token", "band")],
    })

    trends = process_source(source, connector, processor, ["band"], "model-a")

    assert trends == Counter({"band:Sleep Token": 2})


def test_process_source_handles_entity_text_containing_colon():
    source = SimpleNamespace(name="Blabbermouth")
    connector = _FakeConnector([{"title": "A", "text": "Tribute: Vinnie Paul remembered"}])
    processor = _FakeProcessor({
        "Tribute: Vinnie Paul remembered": [("Tribute: Vinnie Paul", "person")],
    })

    trends = process_source(source, connector, processor, ["person"], "model-a")

    label, name = next(iter(trends)).split(":", 1)
    assert (label, name) == ("person", "Tribute: Vinnie Paul")
