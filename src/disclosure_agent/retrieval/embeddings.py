"""Versioned embedding inputs and the CLOVA Studio Embedding v2 client."""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol
from uuid import uuid4

import httpx

EMBEDDING_INPUT_VERSION = "retrieval-embedding-v1"
DEFAULT_PROVIDER = "clova-studio"
DEFAULT_MODEL = "bge-m3"
DEFAULT_DIMENSIONS = 1_024
DEFAULT_DISTANCE_METRIC = "cosine"
DEFAULT_ENDPOINT = (
    "https://clovastudio.stream.ntruss.com/v1/api-tools/embedding/v2"
)
MAX_INPUT_CHARS = 10_000


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    """Provider/model identity that makes one embedding run reproducible."""

    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_MODEL
    dimensions: int = DEFAULT_DIMENSIONS
    distance_metric: str = DEFAULT_DISTANCE_METRIC
    endpoint: str = DEFAULT_ENDPOINT
    input_version: str = EMBEDDING_INPUT_VERSION
    timeout_seconds: float = 60.0
    max_retries: int = 6

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise ValueError("provider and model must not be empty")
        if self.dimensions != DEFAULT_DIMENSIONS:
            raise ValueError("CLOVA Studio bge-m3 requires 1024 dimensions")
        if self.distance_metric != DEFAULT_DISTANCE_METRIC:
            raise ValueError("CLOVA Studio bge-m3 uses cosine distance")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_retries < 0:
            raise ValueError("max_retries must not be negative")


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """One validated provider response."""

    vector: tuple[float, ...]
    input_tokens: int | None
    request_id: str


class EmbeddingProvider(Protocol):
    """Minimal provider contract used by the resumable loader and query CLI."""

    config: EmbeddingConfig

    def embed(self, text: str) -> EmbeddingResult: ...


def embedding_run_id(chunk_run_id: str, config: EmbeddingConfig) -> str:
    """Build a stable identity for one chunk snapshot and embedding contract."""

    values = (
        chunk_run_id,
        config.provider,
        config.model,
        str(config.dimensions),
        config.distance_metric,
        config.endpoint.rstrip("/"),
        config.input_version,
    )
    return sha256("\x1f".join(values).encode()).hexdigest()[:32]


def compose_embedding_input(content: str, heading_path: list[str] | tuple[str, ...]) -> str:
    """Add canonical heading context without mutating the stored chunk text."""

    body = content.strip()
    headings = [value.strip() for value in heading_path if value and value.strip()]
    if headings:
        value = f"[문맥] {' > '.join(headings)}\n\n{body}"
    else:
        value = body
    if not value:
        raise ValueError("Embedding input must not be empty")
    if len(value) > MAX_INPUT_CHARS:
        raise ValueError(
            f"Embedding input exceeds {MAX_INPUT_CHARS} characters: {len(value)}"
        )
    return value


def embedding_input_sha256(value: str) -> str:
    """Hash the exact provider input for cache and integrity checks."""

    return sha256(value.encode("utf-8")).hexdigest()


def vector_literal(vector: tuple[float, ...] | list[float]) -> str:
    """Serialize a finite dense vector for PostgreSQL pgvector input."""

    if len(vector) != DEFAULT_DIMENSIONS:
        raise ValueError(f"Expected {DEFAULT_DIMENSIONS} dimensions, got {len(vector)}")
    if not all(math.isfinite(value) for value in vector):
        raise ValueError("Embedding contains a non-finite value")
    return "[" + ",".join(format(float(value), ".9g") for value in vector) + "]"


class ClovaStudioEmbeddingClient:
    """Synchronous, retrying client for the native Embedding v2 endpoint."""

    _RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

    def __init__(
        self,
        api_key: str,
        config: EmbeddingConfig | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("CLOVA Studio API key must not be empty")
        self.config = config or EmbeddingConfig()
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=self.config.timeout_seconds)
        self._api_key = api_key.strip()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> ClovaStudioEmbeddingClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def embed(self, text: str) -> EmbeddingResult:
        value = text.strip()
        if not value:
            raise ValueError("Embedding input must not be empty")
        if len(value) > MAX_INPUT_CHARS:
            raise ValueError("Embedding input exceeds the API character limit")

        request_id = str(uuid4())
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                response = self._client.post(
                    self.config.endpoint,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "X-NCP-CLOVASTUDIO-REQUEST-ID": request_id,
                        "Content-Type": "application/json",
                    },
                    json={"text": value},
                )
                if response.status_code in self._RETRYABLE_STATUS:
                    if attempt >= self.config.max_retries:
                        response.raise_for_status()
                    self._sleep(response, attempt)
                    continue
                response.raise_for_status()
                return self._parse(response.json(), request_id)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    raise
                self._sleep(None, attempt)
        assert last_error is not None
        raise last_error

    def _parse(self, payload: dict[str, Any], request_id: str) -> EmbeddingResult:
        status = payload.get("status") or {}
        if str(status.get("code")) != "20000":
            raise RuntimeError(
                f"CLOVA Studio embedding failed: {status.get('code')} "
                f"{status.get('message')}"
            )
        result = payload.get("result") or {}
        raw_vector = result.get("embedding")
        if not isinstance(raw_vector, list):
            raise RuntimeError("CLOVA Studio response has no embedding vector")
        vector = tuple(float(value) for value in raw_vector)
        vector_literal(vector)
        raw_tokens = result.get("inputTokens")
        input_tokens = int(raw_tokens) if raw_tokens is not None else None
        return EmbeddingResult(
            vector=vector,
            input_tokens=input_tokens,
            request_id=request_id,
        )

    @staticmethod
    def _sleep(response: httpx.Response | None, attempt: int) -> None:
        retry_after = None if response is None else response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after else 0.0
        except ValueError:
            delay = 0.0
        if delay <= 0:
            delay = min(0.5 * (2**attempt), 10.0) + random.uniform(0.0, 0.25)
        time.sleep(delay)
