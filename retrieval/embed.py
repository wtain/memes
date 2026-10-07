"""Ollama embedding client over the standard library (P4 spec, decision 2). Pure; mirrors parse/client.py.

The backend has no HTTP client dependency, so this uses urllib in a worker thread with socket timeouts. Error
messages never contain the user's text."""
import asyncio
import http.client
import json
import math
import urllib.error
import urllib.request


class EmbedError(Exception):
    pass


class EmbedUnavailable(EmbedError):
    """Ollama is not reachable, answered with an error status, or does not have the model."""


class EmbedTimeout(EmbedError):
    """Ollama did not answer within the time budget."""


class EmbedBadResponse(EmbedError):
    """Ollama answered but the response is not what /api/embed returns."""


def normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    return list(vector) if norm == 0 else [value / norm for value in vector]


class OllamaEmbedClient:
    def __init__(self, base_url: str, expected_dim: int | None = None):
        self._url = base_url.rstrip("/") + "/api/embed"
        self._expected_dim = expected_dim

    def embed(self, model: str, texts: list[str], timeout: float) -> list[list[float]]:
        if not texts:
            return []
        if timeout <= 0:
            raise EmbedTimeout("no time left for the call")
        body = json.dumps({"model": model, "input": list(texts)}).encode("utf-8")
        request = urllib.request.Request(
            self._url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise EmbedUnavailable("Ollama answered HTTP 404 (model not pulled or wrong endpoint)") from None
            raise EmbedUnavailable(f"Ollama answered HTTP {error.code}") from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise EmbedTimeout("Ollama did not answer in time") from None
            raise EmbedUnavailable("Ollama is not reachable") from None
        except http.client.HTTPException:
            raise EmbedUnavailable("Ollama closed the connection or sent a malformed response") from None
        except TimeoutError:
            raise EmbedTimeout("Ollama did not answer in time") from None
        except OSError:
            raise EmbedUnavailable("Ollama is not reachable") from None
        try:
            embeddings = json.loads(payload)["embeddings"]
            vectors = [[float(value) for value in vector] for vector in embeddings]
        except (ValueError, KeyError, TypeError):
            raise EmbedBadResponse("the Ollama response has no embeddings") from None
        if any(not math.isfinite(value) for vector in vectors for value in vector):
            raise EmbedBadResponse("the Ollama response has non-finite values")
        if self._expected_dim is not None and any(len(vector) != self._expected_dim for vector in vectors):
            raise EmbedBadResponse("the Ollama response has an unexpected embedding dimension")
        if len(vectors) != len(texts):
            raise EmbedBadResponse("the Ollama response has the wrong number of embeddings")
        return [normalise(vector) for vector in vectors]

    async def aembed(self, model: str, texts: list[str], timeout: float) -> list[list[float]]:
        return await asyncio.to_thread(self.embed, model, texts, timeout)
