from types import SimpleNamespace

from batch.trends.resolution import resolve_labels, resolve_model


class _FakeSettings:
    def __init__(self, data):
        self._data = data

    def get(self, key, default=None):
        return self._data.get(key, default)


def test_resolve_labels_uses_source_override_when_present():
    source = SimpleNamespace(extraction={"labels": ["band"]})
    settings = _FakeSettings({"trends.labels": ["person"]})

    assert resolve_labels(source, settings) == ["band"]


def test_resolve_labels_falls_back_to_env_default_when_extraction_is_none():
    source = SimpleNamespace(extraction=None)
    settings = _FakeSettings({"trends.labels": ["person"]})

    assert resolve_labels(source, settings) == ["person"]


def test_resolve_labels_falls_back_when_extraction_has_no_labels_key():
    source = SimpleNamespace(extraction={"model": "some-model"})
    settings = _FakeSettings({"trends.labels": ["person"]})

    assert resolve_labels(source, settings) == ["person"]


def test_resolve_model_uses_source_override_when_present():
    source = SimpleNamespace(extraction={"model": "custom-model"})
    settings = _FakeSettings({"trends.model": "default-model"})

    assert resolve_model(source, settings) == "custom-model"


def test_resolve_model_falls_back_to_env_default():
    source = SimpleNamespace(extraction=None)
    settings = _FakeSettings({"trends.model": "default-model"})

    assert resolve_model(source, settings) == "default-model"
