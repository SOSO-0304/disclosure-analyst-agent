"""CLOVA Studio Embedding v2 client with conservative response validation."""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

CLOVA_EMBEDDING_URL = "https://clovastudio.stream.ntruss.com/v1/api-tools/embedding/v2"
CLOVA_EMBEDDING_MODEL = "embedding-v2"
CLOVA_EMBEDDING_DIMENSION = 1024


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """Validated vector returned by CLOVA Studio Embedding v2."""

    vector: tuple[float, ...]
    input_tokens: int


def parse_embedding_payload(payload: object) -> EmbeddingResult:
    """Validate the documented Embedding v2 response shape."""

    if not isinstance(payload, dict):
        raise RuntimeError("CLOVA embedding response must be a JSON object")

    status = payload.get("status")
    if not isinstance(status, dict) or status.get("code") != "20000":
        raise RuntimeError(f"CLOVA embedding request failed: status={status!r}")

    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("CLOVA embedding response is missing result")

    raw_embedding = result.get("embedding")
    if not isinstance(raw_embedding, list):
        raise RuntimeError("CLOVA embedding response is missing embedding")
    if len(raw_embedding) != CLOVA_EMBEDDING_DIMENSION:
        raise RuntimeError(
            "CLOVA embedding dimension mismatch: "
            f"expected={CLOVA_EMBEDDING_DIMENSION} actual={len(raw_embedding)}"
        )

    input_tokens = result.get("inputTokens")
    if not isinstance(input_tokens, int):
        raise RuntimeError("CLOVA embedding response is missing inputTokens")

    try:
        vector = tuple(float(value) for value in raw_embedding)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("CLOVA embedding response contains a non-numeric value") from exc

    return EmbeddingResult(vector=vector, input_tokens=input_tokens)


def parse_rate_limit_reset(value: str | None) -> float | None:
    """Parse CLOVA reset headers such as ``23s`` into seconds."""

    if value is None:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None

    scale = 1.0
    if normalized.endswith("ms"):
        normalized = normalized[:-2]
        scale = 0.001
    elif normalized.endswith("s"):
        normalized = normalized[:-1]

    try:
        seconds = float(normalized) * scale
    except ValueError:
        return None
    return max(seconds, 0.0)


def retry_delay_seconds(headers: Mapping[str, str], attempt: int) -> float:
    """Prefer CLOVA's documented reset window, else use exponential backoff."""

    if attempt < 0:
        raise ValueError("attempt must be non-negative")

    reset_values = (
        parse_rate_limit_reset(headers.get("x-ratelimit-reset-requests")),
        parse_rate_limit_reset(headers.get("x-ratelimit-reset-tokens")),
    )
    documented = tuple(value for value in reset_values if value is not None)
    if documented:
        return max(documented) + 1.0
    return min(float(2**attempt), 30.0)


class ClovaEmbeddingClient:
    """Small synchronous client for the native CLOVA Studio Embedding v2 API."""

    def __init__(
        self,
        api_key: str,
        *,
        endpoint: str = CLOVA_EMBEDDING_URL,
        timeout_seconds: float = 60.0,
        max_retries: int = 3,
    ) -> None:
        if not api_key.strip():
            raise ValueError("CLOVA Studio API key is required")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self.api_key = api_key.strip()
        self.endpoint = endpoint.rstrip("/")
        self.max_retries = max_retries
        self.client = httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> ClovaEmbeddingClient:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def embed(self, text: str) -> EmbeddingResult:
        """Embed one non-empty text with bounded retry on transient failures."""

        if not text.strip():
            raise ValueError("embedding text must not be empty")

        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.post(
                    self.endpoint,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "X-NCP-CLOVASTUDIO-REQUEST-ID": str(uuid.uuid4()),
                        "Content-Type": "application/json",
                    },
                    json={"text": text},
                )
            except httpx.HTTPError as exc:
                if attempt >= self.max_retries:
                    raise RuntimeError("CLOVA embedding HTTP request failed") from exc
                self._sleep_before_retry(attempt)
                continue

            if response.status_code == 429:
                if attempt >= self.max_retries:
                    raise RuntimeError(
                        "CLOVA embedding transient failure: "
                        f"status={response.status_code} body={response.text[:300]!r}"
                    )
                self._sleep_before_retry(attempt, response.headers)
                continue

            if response.status_code >= 500:
                if attempt >= self.max_retries:
                    raise RuntimeError(
                        "CLOVA embedding transient failure: "
                        f"status={response.status_code} body={response.text[:300]!r}"
                    )
                self._sleep_before_retry(attempt)
                continue

            if response.is_error:
                raise RuntimeError(
                    "CLOVA embedding request rejected: "
                    f"status={response.status_code} body={response.text[:300]!r}"
                )

            return parse_embedding_payload(response.json())

        raise RuntimeError("CLOVA embedding retry loop ended unexpectedly")

    @staticmethod
    def _sleep_before_retry(
        attempt: int,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        time.sleep(retry_delay_seconds(headers or {}, attempt))
