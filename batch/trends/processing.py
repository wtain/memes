# gliner pulls in torch/transformers, and this module is imported transitively by
# tests/integration/test_trends_batch_tracking.py, which only exercises DB tracking
# logic and never calls process()/_get_model() (see that test file's docstring).
# Tolerating the ImportError here keeps that workflow's dependency install free of
# the heavy ML stack, while GLiNER stays a module-level name so tests/batch/
# test_trends_processing.py can still monkeypatch GLiNER.from_pretrained where
# gliner is actually installed.
try:
    from gliner import GLiNER
except ImportError:
    GLiNER = None


class Processor:

    def __init__(self):
        self._models: "dict[str, GLiNER]" = {}

    def _get_model(self, model_name: str) -> "GLiNER":
        if model_name not in self._models:
            self._models[model_name] = GLiNER.from_pretrained(model_name)
        return self._models[model_name]

    def process(self, text: str, model_name: str, labels: list[str]) -> list[tuple[str, str]]:
        model = self._get_model(model_name)
        entities = model.predict_entities(text, labels)
        return [(entity["text"], entity["label"]) for entity in entities]
