"""Versioned embedding inputs and the CLOVA Studio Embedding v2 client."""

from __future__ import annotations

import math
import random
import threading
import time
from collections import Counter
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
DEFAULT_TARGET_QPM = 480
DEFAULT_STARTUP_QPM = 54
RATE_LIMIT_SAFETY_RATIO = 0.9


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


class GlobalRateLimiter:
    """Thread-safe, evenly spaced QPM limiter shared by all embedding workers.

    The limiter starts at the test-key-safe rate and adapts after CLOVA returns
    the account's actual request limit. ``target_qpm`` remains a hard ceiling.
    """

    def __init__(
        self,
        target_qpm: int = DEFAULT_TARGET_QPM,
        *,
        startup_qpm: int = DEFAULT_STARTUP_QPM,
    ) -> None:
        if target_qpm <= 0 or startup_qpm <= 0:
            raise ValueError("QPM values must be positive")
        self._target_qpm = float(target_qpm)
        self._effective_qpm = min(float(startup_qpm), self._target_qpm)
        self._next_request_at = 0.0
        self._blocked_until = 0.0
        self._observed_limit_qpm: int | None = None
        self._lock = threading.Lock()

    @property
    def effective_qpm(self) -> float:
        with self._lock:
            return self._effective_qpm

    @property
    def observed_limit_qpm(self) -> int | None:
        with self._lock:
            return self._observed_limit_qpm

    def wait(self) -> None:
        """Wait for the next globally permitted request slot."""

        with self._lock:
            now = time.monotonic()
            request_at = max(now, self._next_request_at, self._blocked_until)
            delay = request_at - now
            if delay > 0:
                time.sleep(delay)
                now = time.monotonic()
            interval = 60.0 / self._effective_qpm
            self._next_request_at = max(request_at, now) + interval

    def observe_headers(self, headers: httpx.Headers) -> None:
        """Clamp the target to 90% of the provider-advertised QPM limit."""

        raw_limit = headers.get("x-ratelimit-limit-requests")
        if raw_limit is None:
            return
        try:
            observed = int(raw_limit)
        except ValueError:
            return
        if observed <= 0:
            return
        safe_qpm = max(1.0, observed * RATE_LIMIT_SAFETY_RATIO)
        with self._lock:
            self._observed_limit_qpm = observed
            self._effective_qpm = min(self._target_qpm, safe_qpm)

    def defer(self, delay_seconds: float) -> None:
        """Apply one shared cooldown so retrying workers cannot stampede."""

        if delay_seconds <= 0:
            return
        with self._lock:
            self._blocked_until = max(
                self._blocked_until,
                time.monotonic() + delay_seconds,
            )


class EmbeddingTelemetry:
    """Thread-safe provider HTTP telemetry for one loader invocation."""

    def __init__(self) -> None:
        self._statuses: Counter[str] = Counter()
        self._transport_errors: Counter[str] = Counter()
        self._http_requests = 0
        self._retries = 0
        self._lock = threading.Lock()

    def record_response(self, status_code: int, *, retry: bool) -> None:
        with self._lock:
            self._http_requests += 1
            self._statuses[f"http_{status_code}"] += 1
            if retry:
                self._retries += 1

    def record_transport_error(self, error: Exception, *, retry: bool) -> None:
        with self._lock:
            self._http_requests += 1
            self._transport_errors[type(error).__name__] += 1
            if retry:
                self._retries += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "http_requests": self._http_requests,
                "retries": self._retries,
                "status_counts": dict(sorted(self._statuses.items())),
                "transport_errors": dict(sorted(self._transport_errors.items())),
            }


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
        rate_limiter: GlobalRateLimiter | None = None,
        telemetry: EmbeddingTelemetry | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("CLOVA Studio API key must not be empty")
        self.config = config or EmbeddingConfig()
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=self.config.timeout_seconds)
        self._api_key = api_key.strip()
        self._rate_limiter = rate_limiter or GlobalRateLimiter()
        self.telemetry = telemetry or EmbeddingTelemetry()

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

        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            self._rate_limiter.wait()
            request_id = str(uuid4())
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
                retryable = response.status_code in self._RETRYABLE_STATUS
                self.telemetry.record_response(
                    response.status_code,
                    retry=retryable and attempt < self.config.max_retries,
                )
                self._rate_limiter.observe_headers(response.headers)
                if response.status_code in self._RETRYABLE_STATUS:
                    if attempt >= self.config.max_retries:
                        response.raise_for_status()
                    self._rate_limiter.defer(self._retry_delay(response, attempt))
                    continue
                response.raise_for_status()
                return self._parse(response.json(), request_id)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                retrying = attempt < self.config.max_retries
                self.telemetry.record_transport_error(exc, retry=retrying)
                if not retrying:
                    raise
                self._rate_limiter.defer(self._retry_delay(None, attempt))
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
    def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
        delay = 0.0
        if response is not None:
            for header_name in (
                "Retry-After",
                "x-ratelimit-reset-requests",
            ):
                raw_delay = response.headers.get(header_name)
                if raw_delay is None:
                    continue
                normalized = raw_delay.strip().lower().removesuffix("s")
                try:
                    delay = max(delay, float(normalized))
                except ValueError:
                    continue
        if delay <= 0:
            delay = min(0.5 * (2**attempt), 10.0) + random.uniform(0.0, 0.25)
        return delay
