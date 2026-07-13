from batch.trends.processing import Processor


class _FakeModel:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def predict_entities(self, text, labels):
        self.calls.append((text, labels))
        return [{"text": "Sleep Token", "label": "band"}]


def test_process_loads_model_once_per_name(monkeypatch):
    created = []

    def _fake_from_pretrained(name):
        model = _FakeModel(name)
        created.append(model)
        return model

    monkeypatch.setattr("batch.trends.processing.GLiNER.from_pretrained", _fake_from_pretrained)

    processor = Processor()
    processor.process("text one", "model-a", ["band"])
    processor.process("text two", "model-a", ["band"])
    processor.process("text three", "model-b", ["person"])

    assert len(created) == 2  # model-a loaded once and reused; model-b loaded separately


def test_process_returns_text_label_tuples(monkeypatch):
    monkeypatch.setattr("batch.trends.processing.GLiNER.from_pretrained", lambda name: _FakeModel(name))

    processor = Processor()
    result = processor.process("Sleep Token released a new track.", "model-a", ["band"])

    assert result == [("Sleep Token", "band")]
