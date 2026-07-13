from gliner import GLiNER


class Processor:

    def __init__(self):
        self._models: dict[str, GLiNER] = {}

    def _get_model(self, model_name: str) -> GLiNER:
        if model_name not in self._models:
            self._models[model_name] = GLiNER.from_pretrained(model_name)
        return self._models[model_name]

    def process(self, text: str, model_name: str, labels: list[str]) -> list[tuple[str, str]]:
        model = self._get_model(model_name)
        entities = model.predict_entities(text, labels)
        return [(entity["text"], entity["label"]) for entity in entities]