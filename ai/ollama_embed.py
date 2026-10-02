import numpy as np
import ollama


class OllamaEmbedder:
    """Embeds text with an Ollama-served embedding model (P1 spec, decision 6).

    Vectors are L2-normalised so cosine similarity is a plain dot product.
    """

    def __init__(self, model: str):
        self.model = model

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        response = ollama.embed(model=self.model, input=texts)
        vectors = np.asarray(response["embeddings"], dtype=np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vectors / norms

    def model_digest(self) -> str:
        wanted = {self.model, f"{self.model}:latest"}
        for entry in ollama.list()["models"]:
            if entry["model"] in wanted:
                return entry["digest"]
        raise RuntimeError(f"Ollama model {self.model!r} not found; run `ollama pull {self.model}`")
