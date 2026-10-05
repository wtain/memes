import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from parse.client import (
    OllamaChatClient, ParserBadResponse, ParserError, ParserTimeout, ParserUnavailable,
)

MESSAGES = [{"role": "user", "content": "hello"}]
SCHEMA = {"type": "object", "properties": {"language": {"type": "string"}}}


@pytest.fixture
def server():
    state = {"mode": "ok", "delay": 0.0, "requests": [], "content": '{"language": "en"}'}

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
                pass  # the client gave up (timeout test)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            state["requests"].append((self.path, json.loads(self.rfile.read(length) or b"{}")))
            if state["mode"] == "slow":
                time.sleep(state["delay"])
            if state["mode"] == "404":
                return self._send(404, b'{"error": "model not found"}')
            if state["mode"] == "500":
                return self._send(500, b'{"error": "boom"}')
            if state["mode"] == "garbage":
                return self._send(200, b"this is not json")
            if state["mode"] == "no_message":
                return self._send(200, b'{"done": true}')
            if state["mode"] == "bad_content":
                return self._send(200, b'{"message": {"content": 5}}')
            self._send(200, json.dumps({"message": {"role": "assistant", "content": state["content"]}}).encode())

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield state, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _client(url):
    return OllamaChatClient(url, seed=7, num_ctx=4096, keep_alive="30m")


def test_success_returns_the_message_content_and_sends_the_expected_request(server):
    state, url = server

    content = _client(url + "/").chat("m1", MESSAGES, SCHEMA, timeout=2.0)

    assert content == '{"language": "en"}'
    path, body = state["requests"][0]
    assert path == "/api/chat"
    assert body["model"] == "m1" and body["messages"] == MESSAGES and body["format"] == SCHEMA
    assert body["stream"] is False and body["keep_alive"] == "30m"
    assert body["options"] == {"temperature": 0, "seed": 7, "num_ctx": 4096}


def test_a_slow_answer_raises_a_timeout(server):
    state, url = server
    state.update(mode="slow", delay=1.5)

    started = time.monotonic()
    with pytest.raises(ParserTimeout):
        _client(url).chat("m", MESSAGES, SCHEMA, timeout=0.3)
    assert time.monotonic() - started < 1.2


def test_http_404_means_unavailable_and_mentions_the_code(server):
    state, url = server
    state["mode"] = "404"
    with pytest.raises(ParserUnavailable, match="404"):
        _client(url).chat("m", MESSAGES, SCHEMA, timeout=2.0)


def test_http_500_means_unavailable(server):
    state, url = server
    state["mode"] = "500"
    with pytest.raises(ParserUnavailable, match="500"):
        _client(url).chat("m", MESSAGES, SCHEMA, timeout=2.0)


@pytest.mark.parametrize("mode", ["garbage", "no_message", "bad_content"])
def test_a_malformed_response_is_a_bad_response(server, mode):
    state, url = server
    state["mode"] = mode
    with pytest.raises(ParserBadResponse):
        _client(url).chat("m", MESSAGES, SCHEMA, timeout=2.0)


def test_connection_refused_means_unavailable():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    # On Windows, a closed port may timeout instead of refuse; both are unavailable
    with pytest.raises((ParserUnavailable, ParserTimeout)):
        _client(f"http://127.0.0.1:{port}").chat("m", MESSAGES, SCHEMA, timeout=2.0)


def test_no_time_left_raises_a_timeout_without_calling_the_server(server):
    state, url = server
    with pytest.raises(ParserTimeout):
        _client(url).chat("m", MESSAGES, SCHEMA, timeout=0)
    assert state["requests"] == []


def test_all_client_errors_share_a_base_class():
    assert issubclass(ParserUnavailable, ParserError) and issubclass(ParserTimeout, ParserError)
    assert issubclass(ParserBadResponse, ParserError)


@pytest.mark.asyncio
async def test_achat_runs_the_call_in_a_thread(server):
    _state, url = server
    assert await _client(url).achat("m", MESSAGES, SCHEMA, timeout=2.0) == '{"language": "en"}'
