"""Native HyperCLOVA X Chat Completions v3 client."""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from disclosure_agent.llm.clova_embedding_client import retry_delay_seconds

HCX_MODEL = "HCX-007"
HCX_CHAT_URL = f"https://clovastudio.stream.ntruss.com/v3/chat-completions/{HCX_MODEL}"


@dataclass(frozen=True, slots=True)
class HcxAnswerResult:
    """Validated final answer returned by HyperCLOVA X."""

    content: str
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


def parse_hcx_payload(payload: object) -> HcxAnswerResult:
    """Validate the documented Chat Completions v3 response shape."""

    if not isinstance(payload, dict):
        raise RuntimeError("HCX response must be a JSON object")

    status = payload.get("status")
    if not isinstance(status, dict) or status.get("code") != "20000":
        raise RuntimeError(f"HCX request failed: status={status!r}")

    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("HCX response is missing result")

    message = result.get("message")
    if not isinstance(message, dict):
        raise RuntimeError("HCX response is missing message")

    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("HCX response is missing answer content")

    usage = result.get("usage")
    if not isinstance(usage, dict):
        usage = {}

    finish_reason = result.get("finishReason")
    return HcxAnswerResult(
        content=content.strip(),
        finish_reason=finish_reason if isinstance(finish_reason, str) else "unknown",
        prompt_tokens=_usage_value(usage, "promptTokens"),
        completion_tokens=_usage_value(usage, "completionTokens"),
        total_tokens=_usage_value(usage, "totalTokens"),
    )


def _usage_value(usage: dict[object, object], key: str) -> int:
    value = usage.get(key)
    return value if isinstance(value, int) else 0


class HcxClient:
    """Small synchronous client for grounded answer generation with HCX-007."""

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = HCX_CHAT_URL,
        timeout_seconds: float = 120.0,
        max_retries: int = 3,
    ) -> None:
        if not api_key.strip():
            raise ValueError("CLOVA Studio API key is required")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self.api_key = api_key.strip()
        self.endpoint = endpoint
        self.max_retries = max_retries
        self.client = httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> HcxClient:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def answer(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_completion_tokens: int = 1200,
    ) -> HcxAnswerResult:
        """Generate one answer with bounded retry on transient failures."""

        if not system_prompt.strip():
            raise ValueError("system_prompt must not be empty")
        if not user_prompt.strip():
            raise ValueError("user_prompt must not be empty")
        if max_completion_tokens < 1:
            raise ValueError("max_completion_tokens must be at least 1")

        payload = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "thinking": {"effort": "none"},
            "topP": 0.8,
            "topK": 0,
            "maxCompletionTokens": max_completion_tokens,
            "temperature": 0.1,
            "repetitionPenalty": 1.05,
        }

        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.post(
                    self.endpoint,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4()),
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            except httpx.HTTPError as exc:
                if attempt >= self.max_retries:
                    raise RuntimeError("HCX HTTP request failed") from exc
                self._sleep_before_retry(attempt)
                continue

            if response.status_code == 429:
                if attempt >= self.max_retries:
                    raise RuntimeError(
                        "HCX transient failure: "
                        f"status={response.status_code} body={response.text[:300]!r}"
                    )
                self._sleep_before_retry(attempt, response.headers)
                continue

            if response.status_code >= 500:
                if attempt >= self.max_retries:
                    raise RuntimeError(
                        "HCX transient failure: "
                        f"status={response.status_code} body={response.text[:300]!r}"
                    )
                self._sleep_before_retry(attempt)
                continue

            if response.is_error:
                raise RuntimeError(
                    "HCX request rejected: "
                    f"status={response.status_code} body={response.text[:300]!r}"
                )

            return parse_hcx_payload(response.json())

        raise RuntimeError("HCX retry loop ended unexpectedly")

    @staticmethod
    def _sleep_before_retry(
        attempt: int,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        time.sleep(retry_delay_seconds(headers or {}, attempt))
