"""Ollama chat client over the standard library (P3 spec, findings: the backend has no HTTP client dependency).

Blocking `chat` is run in a worker thread by `achat`. The timeout is a socket timeout (it bounds each blocking
socket operation); the response is not streamed, so a normal answer arrives in one piece. Swapping to httpx
later changes only this file. The error messages never contain the user's text.
"""
import asyncio
import json
import urllib.error
import urllib.request


class ParserError(Exception):
    pass


class ParserUnavailable(ParserError):
    """Ollama is not reachable, answered with an error status, or does not have the model."""


class ParserTimeout(ParserError):
    """Ollama did not answer within the time budget."""


class ParserBadResponse(ParserError):
    """Ollama answered but the response envelope is not what /api/chat returns."""


class OllamaChatClient:
    def __init__(self, base_url: str, *, seed: int, num_ctx: int, keep_alive: str):
        self._url = base_url.rstrip("/") + "/api/chat"
        self._seed = seed
        self._num_ctx = num_ctx
        self._keep_alive = keep_alive

    def chat(self, model: str, messages: list[dict], schema: dict, timeout: float) -> str:
        if timeout <= 0:
            raise ParserTimeout("no time left for the call")
        body = json.dumps({
            "model": model, "messages": messages, "stream": False, "format": schema,
            "keep_alive": self._keep_alive,
            "options": {"temperature": 0, "seed": self._seed, "num_ctx": self._num_ctx},
        }).encode("utf-8")
        request = urllib.request.Request(
            self._url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise ParserUnavailable("Ollama answered HTTP 404 (model not pulled or wrong endpoint)") from None
            raise ParserUnavailable(f"Ollama answered HTTP {error.code}") from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise ParserTimeout("Ollama did not answer in time") from None
            raise ParserUnavailable("Ollama is not reachable") from None
        except TimeoutError:
            raise ParserTimeout("Ollama did not answer in time") from None
        except OSError:
            raise ParserUnavailable("Ollama is not reachable") from None
        try:
            content = json.loads(payload)["message"]["content"]
        except (ValueError, KeyError, TypeError):
            raise ParserBadResponse("the Ollama response has no message content") from None
        if not isinstance(content, str):
            raise ParserBadResponse("the Ollama message content is not text")
        return content

    async def achat(self, model: str, messages: list[dict], schema: dict, timeout: float) -> str:
        return await asyncio.to_thread(self.chat, model, messages, schema, timeout)
