"""`ChatClient` against a local `http.server` stub: pinned sampling, model
discovery, retry-on-length, and the one-typed-exception error surface. No
real network; every server is loopback on an ephemeral port and torn down."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from empire.ai.general.client import (
    MAX_TOKENS,
    MIN_P,
    PRESENCE_PENALTY,
    TEMPERATURE,
    TOP_K,
    TOP_P,
    ChatClient,
    GeneralUnavailableError,
)
from empire.config import LlmConnection

ANSWER_TEXT = "TF 1: CONTINUE | hold the line"


class _StubServer(ThreadingHTTPServer):
    """An OpenAI-shaped stub whose behavior the test scripts via fields.

    Handler threads are non-daemon and joined by `server_close` (the
    ThreadingMixIn default), so no socket or thread outlives the fixture;
    `handle_error` is silenced because the timeout test deliberately leaves
    a handler writing to a client that already hung up."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _StubHandler)
        self.posts: list[dict[str, Any]] = []  # each: {"body": ..., "auth": ...}
        self.gets: list[str] = []
        # finish_reason per POST, in order; the last entry repeats.
        self.finishes: list[str] = ["stop"]
        self.status: int = 200
        self.raw_response: bytes | None = None  # overrides the JSON body
        self.delay_s: float = 0.0
        self.model_id: str = "stub-model-q8"

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/v1"

    def handle_error(self, request: object, client_address: object) -> None:
        pass  # expected broken pipes from the timeout test


class _StubHandler(BaseHTTPRequestHandler):
    @property
    def stub(self) -> _StubServer:
        assert isinstance(self.server, _StubServer)
        return self.server

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # keep pytest output clean

    def do_GET(self) -> None:
        self.stub.gets.append(self.path)
        self._respond({"object": "list", "data": [{"id": self.stub.model_id}]})

    def do_POST(self) -> None:
        srv = self.stub
        if srv.delay_s:
            time.sleep(srv.delay_s)
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        srv.posts.append({"body": body, "auth": self.headers.get("Authorization")})
        finish = srv.finishes[min(len(srv.posts) - 1, len(srv.finishes) - 1)]
        self._respond(
            {
                "object": "chat.completion",
                "model": srv.model_id,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": ANSWER_TEXT},
                        "finish_reason": finish,
                    }
                ],
            }
        )

    def _respond(self, payload: dict[str, Any]) -> None:
        raw = self.stub.raw_response
        body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        self.send_response(self.stub.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def server() -> Iterator[_StubServer]:
    srv = _StubServer()
    # Small poll interval so shutdown() returns promptly at teardown.
    thread = threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.02), daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def _client(
    server: _StubServer, *, model: str = "", api_key: str = "", timeout_s: float = 10.0
) -> ChatClient:
    connection = LlmConnection(
        enabled=True, base_url=server.base_url, api_key=api_key, model=model
    )
    return ChatClient(connection, timeout_s=timeout_s)


# ---- happy path -----------------------------------------------------------------


def test_happy_path_delivers_with_pinned_sampling(server: _StubServer) -> None:
    """One completion carries the pinned delivery profile verbatim — the
    sampling params are part of the model pin, not knobs."""
    answer = _client(server, model="pinned-model").complete("hello general", seed=7)
    assert answer.delivered
    assert answer.text == ANSWER_TEXT
    assert answer.attempts == 1
    body = server.posts[0]["body"]
    assert body["model"] == "pinned-model"
    assert body["messages"] == [{"role": "user", "content": "hello general"}]
    assert body["temperature"] == TEMPERATURE
    assert body["top_p"] == TOP_P
    assert body["presence_penalty"] == PRESENCE_PENALTY
    assert body["max_tokens"] == MAX_TOKENS
    assert body["seed"] == 7
    assert body["top_k"] == TOP_K
    assert body["min_p"] == MIN_P
    assert body["chat_template_kwargs"] == {"enable_thinking": True}


def test_blank_model_is_discovered_once_and_recorded(server: _StubServer) -> None:
    """An empty configured model triggers ONE /models discovery; the id the
    server claims is used for requests and recorded on the answer (the BYO
    trust boundary: record, don't verify)."""
    client = _client(server, model="")
    first = client.complete("a", seed=1)
    second = client.complete("b", seed=2)
    assert server.gets == ["/v1/models"]  # discovered once, cached
    assert server.posts[0]["body"]["model"] == "stub-model-q8"
    assert first.model == "stub-model-q8"
    assert second.model == "stub-model-q8"


def test_configured_model_skips_discovery(server: _StubServer) -> None:
    _client(server, model="pinned-model").complete("a", seed=1)
    assert server.gets == []


def test_api_key_rides_as_bearer_header(server: _StubServer) -> None:
    _client(server, model="m", api_key="sekrit").complete("a", seed=1)
    assert server.posts[0]["auth"] == "Bearer sekrit"


def test_blank_api_key_sends_no_auth_header(server: _StubServer) -> None:
    _client(server, model="m").complete("a", seed=1)
    assert server.posts[0]["auth"] is None


# ---- retry-on-length -------------------------------------------------------------


def test_finish_length_retries_with_shifted_seeds(server: _StubServer) -> None:
    """finish=length re-rolls at seed + 1000·attempt, up to two retries —
    the delivery pipeline that survived handshake (b)."""
    server.finishes = ["length", "length", "stop"]
    answer = _client(server, model="m").complete("a", seed=7)
    assert answer.delivered
    assert answer.attempts == 3
    assert [p["body"]["seed"] for p in server.posts] == [7, 1007, 2007]


def test_exhausted_retries_return_undelivered_not_raise(server: _StubServer) -> None:
    """Non-convergence is an epoch-failure SIGNAL, not a transport error:
    the capped answer comes back with delivered=False."""
    server.finishes = ["length"]
    answer = _client(server, model="m").complete("a", seed=7)
    assert not answer.delivered
    assert answer.finish_reason == "length"
    assert answer.attempts == 3  # 1 + RETRY_ON_LENGTH


# ---- the one typed exception ------------------------------------------------------


def test_timeout_raises_typed_exception(server: _StubServer) -> None:
    server.delay_s = 0.5
    with pytest.raises(GeneralUnavailableError):
        _client(server, model="m", timeout_s=0.05).complete("a", seed=1)


def test_garbage_json_raises_typed_exception(server: _StubServer) -> None:
    server.raw_response = b"this is not json {{"
    with pytest.raises(GeneralUnavailableError):
        _client(server, model="m").complete("a", seed=1)


def test_http_error_status_raises_typed_exception(server: _StubServer) -> None:
    server.status = 500
    with pytest.raises(GeneralUnavailableError):
        _client(server, model="m").complete("a", seed=1)


def test_malformed_shape_raises_typed_exception(server: _StubServer) -> None:
    server.raw_response = b'{"choices": []}'
    with pytest.raises(GeneralUnavailableError):
        _client(server, model="m").complete("a", seed=1)


def test_connection_refused_raises_typed_exception() -> None:
    # Grab a loopback port and close it so nothing listens there.
    probe = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = probe.server_address[1]
    probe.server_close()
    connection = LlmConnection(enabled=True, base_url=f"http://127.0.0.1:{port}/v1", model="m")
    with pytest.raises(GeneralUnavailableError):
        ChatClient(connection, timeout_s=2.0).complete("a", seed=1)


def test_blank_base_url_raises_typed_exception() -> None:
    with pytest.raises(GeneralUnavailableError):
        ChatClient(LlmConnection(), timeout_s=1.0).complete("a", seed=1)
