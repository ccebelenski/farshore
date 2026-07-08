"""`ChatClient`: the game's stdlib-only OpenAI-compatible chat client.

The deployment architecture (planning/08 §Deployment) gives the game exactly
ONE seam to the model: chat completions against a configured `base_url`. The
`openai` package is a lab dependency; in the shipped game that seam is this
thin urllib client. It carries the pinned delivery profile (sampling params +
seed-shifted retry-on-length) proven in handshake (b), and honors the BYO
trust boundary: the model id the server CLAIMS is discovered once, logged,
and recorded on every answer — never verified.

Totality contract: every transport-level failure — refused connection,
timeout, HTTP error status, garbage or misshapen JSON — surfaces as the ONE
typed exception `GeneralUnavailableError`. Callers never see urllib internals,
so the controller's fail-safe needs exactly one except clause.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from typing import Any, cast

from empire.config import LlmConnection

_LOG = logging.getLogger(__name__)

# Pinned sampling (the production delivery profile; planning/08 wrinkle #4).
# These ship with the model pin and are not configurable.
TEMPERATURE = 1.0
TOP_P = 0.95
TOP_K = 20
MIN_P = 0.0
PRESENCE_PENALTY = 1.5
MAX_TOKENS = 12288
# Seed-shifted re-rolls when the model hits the token cap still thinking
# (finish=length): up to this many retries, each at seed + 1000·attempt.
RETRY_ON_LENGTH = 2
# Generous but FINITE: a thinking epoch at the CPU floor is minutes, not
# hours. The controller degrades to the executor when this expires.
DEFAULT_TIMEOUT_S = 1800.0


class GeneralUnavailableError(Exception):
    """The general's endpoint could not deliver: network, timeout, HTTP
    error, or an unreadable response. The single transport-error type the
    rest of the game ever sees."""


@dataclass(frozen=True, slots=True)
class ChatAnswer:
    """One delivered completion: the answer text plus delivery facts.

    `model` is what the server CLAIMED for this response (BYO boundary:
    recorded, never verified). `attempts` counts completions actually made,
    including length-capped ones that triggered a retry.
    """

    text: str
    finish_reason: str
    attempts: int
    model: str
    reasoning: str = ""

    @property
    def delivered(self) -> bool:
        return self.finish_reason == "stop"


class ChatClient:
    """Chat completions over urllib against one configured endpoint.

    The model id comes from the connection config when set; otherwise it is
    discovered ONCE via `/models` (first listed) and logged. Sampling is the
    pinned profile; only the seed varies per call.
    """

    def __init__(
        self, connection: LlmConnection, timeout_s: float = DEFAULT_TIMEOUT_S
    ) -> None:
        self._base = connection.base_url.rstrip("/")
        self._api_key = connection.api_key
        self._timeout_s = timeout_s
        # Cached model id; configured wins, else discovered on first use.
        self._model: str | None = connection.model or None

    @property
    def model(self) -> str:
        """The model id used in requests; discovers (and logs) it if the
        config left it blank. Raises `GeneralUnavailableError` on failure."""
        if self._model is None:
            self._model = self._discover_model()
        return self._model

    def complete(
        self, prompt: str, *, seed: int, system: str | None = None
    ) -> ChatAnswer:
        """One completion under the pinned delivery profile. `system`, when
        given, is sent as a leading system message (identity grounding) ahead
        of the user prompt.

        Retries on finish=length with seed + 1000·attempt (reproducible
        re-rolls); a still-capped final attempt is RETURNED (delivered is
        False) — non-convergence is the caller's epoch-failure signal, not a
        transport error. Raises `GeneralUnavailableError` for transport failures.
        """
        answer = self._one_completion(prompt, seed, attempt=1, system=system)
        for retry in range(1, 1 + RETRY_ON_LENGTH):
            if answer.finish_reason != "length":
                break
            answer = self._one_completion(
                prompt, seed + 1000 * retry, attempt=retry + 1, system=system
            )
        return answer

    # ---- one request ----------------------------------------------------------

    def _one_completion(
        self, prompt: str, seed: int, attempt: int, system: str | None = None
    ) -> ChatAnswer:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload = self._request(
            "/chat/completions",
            {
                "model": self.model,
                "messages": messages,
                "temperature": TEMPERATURE,
                "top_p": TOP_P,
                "presence_penalty": PRESENCE_PENALTY,
                "max_tokens": MAX_TOKENS,
                "seed": seed,
                # llama.cpp-family extras ride at the top level (urllib has no
                # extra_body distinction); OpenAI-strict servers ignore them.
                "top_k": TOP_K,
                "min_p": MIN_P,
                "chat_template_kwargs": {"enable_thinking": True},
            },
        )
        try:
            choice = payload["choices"][0]
            message = choice["message"]
            text = message.get("content") or ""
            # Thinking arrives either in a separate `reasoning_content` field
            # (llama.cpp / vLLM reasoning parsers) or inline as <think>…</think>
            # in the content. Capture it for the war diary either way, and keep
            # `text` the answer only.
            reasoning = message.get("reasoning_content") or ""
            if not reasoning and "</think>" in text:
                pre, _, post = text.partition("</think>")
                reasoning = pre.replace("<think>", "").strip()
                text = post.strip()
            finish = str(choice.get("finish_reason"))
        except (KeyError, IndexError, TypeError) as exc:
            raise GeneralUnavailableError(f"malformed chat response: {exc!r}") from exc
        return ChatAnswer(
            text=text,
            finish_reason=finish,
            attempts=attempt,
            model=str(payload.get("model", self.model)),
            reasoning=reasoning,
        )

    def _discover_model(self) -> str:
        """First model the server lists — the BYO record of what it claims."""
        payload = self._request("/models", None)
        try:
            model_id = str(payload["data"][0]["id"])
        except (KeyError, IndexError, TypeError) as exc:
            raise GeneralUnavailableError(f"malformed /models response: {exc!r}") from exc
        _LOG.info(
            "LLM general: endpoint %s reports model %r (recorded, not verified)",
            self._base,
            model_id,
        )
        return model_id

    def _request(self, path: str, body: dict[str, Any] | None) -> dict[str, Any]:
        """One HTTP round-trip, JSON in / JSON object out. GET when `body`
        is None. Every failure mode raises `GeneralUnavailableError`."""
        if not self._base:
            raise GeneralUnavailableError("no base_url configured")
        url = self._base + path
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                raw = response.read()
            parsed: Any = json.loads(raw)
        except (OSError, ValueError) as exc:
            # URLError/HTTPError/timeouts are OSError; JSONDecodeError is
            # ValueError — the whole transport surface, one typed exception.
            raise GeneralUnavailableError(f"{url}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise GeneralUnavailableError(f"{url}: response is not a JSON object")
        return cast(dict[str, Any], parsed)
