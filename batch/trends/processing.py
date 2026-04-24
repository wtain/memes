from gliner import GLiNER


class Processor:

    def __init__(self, labels=None):
        if labels is None:
            labels = ["band", "music genre", "person"]
        self.model = GLiNER.from_pretrained("urchade/gliner_medium-v2.1")
        self.labels = labels

    def process(self, text):
        entities = self.model.predict_entities(text, self.labels)

        return [(entity['text'], entity['label']) for entity in entities]