import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from retrieval.embed import EmbedBadResponse, EmbedError, EmbedTimeout, EmbedUnavailable, OllamaEmbedClient


@pytest.fixture
def server():
    state = {"mode": "ok", "delay": 0.0, "requests": [], "embeddings": [[3.0, 4.0], [0.0, 2.0]]}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body: bytes):
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            state["requests"].append((self.path, json.loads(self.rfile.read(length) or b"{}")))
            mode = state["mode"]
            if mode == "slow":
                time.sleep(state["delay"])
            if mode == "404":
                return self._send(404, b'{"error": "model not found"}')
            if mode == "500":
                return self._send(500, b'{"error": "boom"}')
            if mode == "garbage":
                return self._send(200, b"not json")
            if mode == "no_embeddings":
                return self._send(200, b'{"done": true}')
            if mode == "nan":
                return self._send(200, b'{"embeddings": [[NaN, 1.0]]}')
            if mode == "wrong_count":
                return self._send(200, json.dumps({"embeddings": [[1.0, 0.0]]}).encode())
            if mode == "incomplete_read":
                self.send_response(200)
                self.send_header("Content-Length", "1000")
                self.end_headers()
                self.wfile.write(b'{"embeddings": [')
                return
            self._send(200, json.dumps({"embeddings": state["embeddings"]}).encode())

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield state, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_success_returns_normalised_vectors_and_sends_the_expected_request(server):
    state, url = server

    vectors = OllamaEmbedClient(url + "/").embed("bge-large", ["a", "b"], timeout=2.0)

    assert vectors[0] == pytest.approx([0.6, 0.8]) and vectors[1] == pytest.approx([0.0, 1.0])
    path, body = state["requests"][0]
    assert path == "/api/embed" and body == {"model": "bge-large", "input": ["a", "b"]}


def test_a_zero_vector_stays_zero():
    from retrieval.embed import normalise
    assert normalise([0.0, 0.0]) == [0.0, 0.0]


def test_a_slow_answer_is_a_timeout(server):
    state, url = server
    state.update(mode="slow", delay=3.0)
    started = time.monotonic()
    with pytest.raises(EmbedTimeout):
        OllamaEmbedClient(url).embed("m", ["a", "b"], timeout=0.3)
    assert time.monotonic() - started < 2.0


@pytest.mark.parametrize("mode, error", [("404", EmbedUnavailable), ("500", EmbedUnavailable),
                                         ("incomplete_read", EmbedUnavailable)])
def test_http_failures_are_unavailable(server, mode, error):
    state, url = server
    state["mode"] = mode
    with pytest.raises(error):
        OllamaEmbedClient(url).embed("m", ["a", "b"], timeout=2.0)


@pytest.mark.parametrize("mode", ["garbage", "no_embeddings", "wrong_count"])
def test_malformed_responses_are_bad_responses(server, mode):
    state, url = server
    state["mode"] = mode
    with pytest.raises(EmbedBadResponse):
        OllamaEmbedClient(url).embed("m", ["a", "b"], timeout=2.0)


def test_connection_refused_is_unavailable_or_a_timeout_on_windows():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    started = time.monotonic()
    # On Windows a closed loopback port may be reported as a timeout instead of an immediate refusal.
    with pytest.raises((EmbedUnavailable, EmbedTimeout)):
        OllamaEmbedClient(f"http://127.0.0.1:{port}").embed("m", ["a"], timeout=0.3)
    assert time.monotonic() - started < 1.3


def test_no_time_left_is_a_timeout_without_a_request(server):
    state, url = server
    with pytest.raises(EmbedTimeout):
        OllamaEmbedClient(url).embed("m", ["a"], timeout=0)
    assert state["requests"] == []


def test_an_empty_input_returns_no_vectors_without_a_request(server):
    state, url = server
    assert OllamaEmbedClient(url).embed("m", [], timeout=1.0) == []
    assert state["requests"] == []


def test_all_errors_share_a_base_class():
    assert all(issubclass(e, EmbedError) for e in (EmbedUnavailable, EmbedTimeout, EmbedBadResponse))


@pytest.mark.asyncio
async def test_aembed_runs_in_a_thread(server):
    _state, url = server
    vectors = await OllamaEmbedClient(url).aembed("m", ["a", "b"], timeout=2.0)
    assert len(vectors) == 2


def test_non_finite_values_are_a_bad_response(server):
    state, url = server
    state["mode"] = "nan"
    with pytest.raises(EmbedBadResponse, match="non-finite"):
        OllamaEmbedClient(url).embed("m", ["a"], 2.0)
