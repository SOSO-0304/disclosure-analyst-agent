"""Small synchronous client for CLOVA Studio Chat Completions v3.

This module is intentionally independent from retrieval. It receives only the
bounded prompt assembled by the grounded-answer layer and never reads the
database or canonical corpus directly.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx

DEFAULT_CHAT_PROVIDER = "clova-studio"
DEFAULT_CHAT_MODEL = "HCX-005"
DEFAULT_CHAT_ENDPOINT_BASE = "https://clovastudio.stream.ntruss.com/v3/chat-completions"
_MODEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class ClovaStudioChatError(RuntimeError):
    """A secret-safe provider or response-contract failure."""


@dataclass(frozen=True, slots=True)
class ChatConfig:
    provider: str = DEFAULT_CHAT_PROVIDER
    model: str = DEFAULT_CHAT_MODEL
    endpoint_base: str = DEFAULT_CHAT_ENDPOINT_BASE
    timeout_seconds: float = 60.0
    max_retries: int = 2
    max_tokens: int = 800
    temperature: float = 0.1
    top_p: float = 0.2
    top_k: int = 0
    repetition_penalty: float = 1.0

    def __post_init__(self) -> None:
        if self.provider != DEFAULT_CHAT_PROVIDER:
            raise ValueError("Unsupported chat provider")
        if not _MODEL_RE.fullmatch(self.model):
            raise ValueError("Invalid CLOVA Studio chat model name")
        if not self.endpoint_base.startswith("https://"):
            raise ValueError("Chat endpoint must use HTTPS")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not 0 <= self.max_retries <= 6:
            raise ValueError("max_retries must be between 0 and 6")
        if not 1 <= self.max_tokens <= 4_096:
            raise ValueError("max_tokens must be between 1 and 4096")
        if not 0 <= self.temperature <= 1:
            raise ValueError("temperature must be between 0 and 1")
        if not 0 < self.top_p <= 1:
            raise ValueError("top_p must be between 0 and 1")
        if not 0 <= self.top_k <= 128:
            raise ValueError("top_k must be between 0 and 128")

    @property
    def endpoint(self) -> str:
        return f"{self.endpoint_base.rstrip('/')}/{self.model}"


@dataclass(frozen=True, slots=True)
class ChatResult:
    content: str
    model: str
    request_id: str
    finish_reason: str | None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ClovaStudioChatClient:
    """Non-streaming Chat Completions v3 client with bounded retries."""

    _RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

    def __init__(
        self,
        api_key: str,
        config: ChatConfig | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("CLOVA Studio API key must not be empty")
        self.config = config or ChatConfig()
        self._api_key = api_key.strip()
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=self.config.timeout_seconds)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> ClovaStudioChatClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def complete(self, *, system: str, user: str) -> ChatResult:
        if not system.strip() or not user.strip():
            raise ValueError("Chat messages must not be empty")
        payload = {
            "messages": [
                {"role": "system", "content": system.strip()},
                {"role": "user", "content": user.strip()},
            ],
            "topP": self.config.top_p,
            "topK": self.config.top_k,
            "maxTokens": self.config.max_tokens,
            "temperature": self.config.temperature,
            "repetitionPenalty": self.config.repetition_penalty,
            "stop": [],
        }

        for attempt in range(self.config.max_retries + 1):
            request_id = str(uuid4())
            try:
                response = self._client.post(
                    self.config.endpoint,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "X-NCP-CLOVASTUDIO-REQUEST-ID": request_id,
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                    json=payload,
                )
                if response.status_code in self._RETRYABLE_STATUS:
                    if attempt >= self.config.max_retries:
                        response.raise_for_status()
                    time.sleep(self._retry_delay(response, attempt))
                    continue
                response.raise_for_status()
                return self._parse(response.json(), request_id)
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt >= self.config.max_retries:
                    raise
                time.sleep(self._retry_delay(None, attempt))
        raise ClovaStudioChatError("CLOVA Studio chat request did not complete")

    def _parse(self, payload: dict[str, Any], request_id: str) -> ChatResult:
        status = payload.get("status") or {}
        status_code = str(status.get("code") or "")
        if status_code and status_code not in {"200", "20000"}:
            raise ClovaStudioChatError("CLOVA Studio chat returned a provider error")
        result = payload.get("result") or {}
        message = result.get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ClovaStudioChatError("CLOVA Studio chat response has no answer content")
        usage = result.get("usage") or {}
        prompt_tokens = self._nonnegative_int(usage.get("promptTokens"))
        completion_tokens = self._nonnegative_int(usage.get("completionTokens"))
        total_tokens = self._nonnegative_int(usage.get("totalTokens"))
        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens
        return ChatResult(
            content=content.strip(),
            model=self.config.model,
            request_id=request_id,
            finish_reason=str(result["finishReason"]) if result.get("finishReason") else None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )

    @staticmethod
    def _nonnegative_int(value: Any) -> int:
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError):
            return 0
        return max(parsed, 0)

    @staticmethod
    def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
        if response is not None:
            raw = response.headers.get("Retry-After")
            if raw:
                try:
                    return min(max(float(raw.strip().removesuffix("s")), 0.0), 10.0)
                except ValueError:
                    pass
        return min(0.5 * (2**attempt), 4.0) + random.uniform(0.0, 0.2)
