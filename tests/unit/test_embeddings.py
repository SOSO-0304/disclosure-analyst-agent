from __future__ import annotations

import math

import httpx
import pytest

from disclosure_agent.retrieval.embeddings import (
    EMBEDDING_INPUT_VERSION_V1,
    EMBEDDING_INPUT_VERSION_V2,
    EMBEDDING_INPUT_VERSION_V3,
    ClovaStudioEmbeddingClient,
    EmbeddingConfig,
    EmbeddingDocumentContext,
    GlobalRateLimiter,
    compose_embedding_input,
    embedding_input_sha256,
    embedding_run_id,
    vector_literal,
)


def test_approved_embedding_input_defaults_to_v2() -> None:
    assert EmbeddingConfig().input_version == EMBEDDING_INPUT_VERSION_V2


def test_compose_embedding_input_adds_provenance_context() -> None:
    value = compose_embedding_input(
        "계약금액은 100억원입니다.",
        ["주요사항보고서", "단일판매·공급계약"],
    )

    assert value == ("[문맥] 주요사항보고서 > 단일판매·공급계약\n\n계약금액은 100억원입니다.")
    assert embedding_input_sha256(value) == embedding_input_sha256(value)


def test_compose_v2_embedding_input_adds_retrieval_metadata() -> None:
    value = compose_embedding_input(
        "매출액은 100억원입니다.",
        ["II. 사업의 내용", "매출 및 수주상황"],
        input_version=EMBEDDING_INPUT_VERSION_V2,
        context=EmbeddingDocumentContext(
            corp_name="테스트주식회사",
            listed_name="테스트",
            stock_code="123456",
            report_name="2026년 반기보고서",
            document_subtype="반기보고서",
            document_title="반기보고서 본문",
            is_correction=True,
            table_caption="부문별 매출액",
        ),
    )

    assert value == (
        "[기업] 테스트 (123456)\n"
        "[공시] 2026년 반기보고서\n"
        "[유형] 반기보고서\n"
        "[문서] 반기보고서 본문\n"
        "[상태] 정정공시\n"
        "[문맥] II. 사업의 내용 > 매출 및 수주상황\n"
        "[표제목] 부문별 매출액\n\n"
        "매출액은 100억원입니다."
    )


def test_compose_v2_embedding_input_requires_metadata() -> None:
    with pytest.raises(ValueError, match="requires document context"):
        compose_embedding_input(
            "매출액은 100억원입니다.",
            [],
            input_version=EMBEDDING_INPUT_VERSION_V2,
        )


def test_compose_v3_embedding_input_keeps_only_discriminative_context() -> None:
    value = compose_embedding_input(
        "매출액은 100억원입니다.",
        ["II. 사업의 내용", "매출 및 수주상황"],
        input_version=EMBEDDING_INPUT_VERSION_V3,
        context=EmbeddingDocumentContext(
            corp_name="테스트주식회사",
            listed_name="테스트",
            stock_code="123456",
            report_name="2026년 반기보고서",
            document_subtype="반기보고서",
            document_title="반기보고서 본문",
            is_correction=True,
            table_caption="부문별 매출액",
        ),
    )

    assert value == (
        "[기업] 테스트 (123456)\n"
        "[상태] 정정공시\n"
        "[문맥] II. 사업의 내용 > 매출 및 수주상황\n"
        "[표제목] 부문별 매출액\n\n"
        "매출액은 100억원입니다."
    )
    assert "[공시]" not in value
    assert "[유형]" not in value
    assert "[문서]" not in value


def test_embedding_run_identity_changes_with_input_contract() -> None:
    left = embedding_run_id(
        "chunk-run",
        EmbeddingConfig(input_version=EMBEDDING_INPUT_VERSION_V1),
    )
    right = embedding_run_id(
        "chunk-run",
        EmbeddingConfig(input_version=EMBEDDING_INPUT_VERSION_V2),
    )

    assert left != right


def test_vector_literal_validates_dimensions_and_finiteness() -> None:
    value = vector_literal([0.25] * 1024)

    assert value.startswith("[0.25,0.25")
    assert value.endswith("]")
    with pytest.raises(ValueError, match="dimensions"):
        vector_literal([0.25])
    invalid = [0.25] * 1024
    invalid[-1] = math.inf
    with pytest.raises(ValueError, match="non-finite"):
        vector_literal(invalid)


def test_clova_client_parses_native_v2_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret"
        assert request.url.path.endswith("/embedding/v2")
        return httpx.Response(
            200,
            json={
                "status": {"code": "20000", "message": "OK"},
                "result": {
                    "embedding": [0.125] * 1024,
                    "inputTokens": 7,
                },
            },
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    config = EmbeddingConfig(max_retries=0)
    client = ClovaStudioEmbeddingClient("secret", config, client=http_client)

    result = client.embed("공급계약")

    assert result.input_tokens == 7
    assert len(result.vector) == 1024
    assert result.vector[0] == 0.125
    assert result.request_id
    http_client.close()


def test_rate_limiter_clamps_to_provider_advertised_qpm() -> None:
    limiter = GlobalRateLimiter(target_qpm=480, startup_qpm=54)

    limiter.observe_headers(httpx.Headers({"x-ratelimit-limit-requests": "60"}))

    assert limiter.observed_limit_qpm == 60
    assert limiter.effective_qpm == 54


def test_clova_client_retries_429_with_shared_telemetry() -> None:
    calls = 0
    request_ids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        request_ids.append(request.headers["X-NCP-CLOVASTUDIO-REQUEST-ID"])
        if calls == 1:
            return httpx.Response(
                429,
                headers={
                    "Retry-After": "0.001",
                    "x-ratelimit-limit-requests": "60000",
                },
            )
        return httpx.Response(
            200,
            headers={"x-ratelimit-limit-requests": "60000"},
            json={
                "status": {"code": "20000", "message": "OK"},
                "result": {
                    "embedding": [0.125] * 1024,
                    "inputTokens": 7,
                },
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    limiter = GlobalRateLimiter(target_qpm=60000, startup_qpm=60000)
    client = ClovaStudioEmbeddingClient(
        "secret",
        EmbeddingConfig(max_retries=1),
        client=http_client,
        rate_limiter=limiter,
    )

    result = client.embed("공급계약")
    telemetry = client.telemetry.snapshot()

    assert len(result.vector) == 1024
    assert calls == 2
    assert len(set(request_ids)) == 2
    assert telemetry["http_requests"] == 2
    assert telemetry["retries"] == 1
    assert telemetry["status_counts"] == {"http_200": 1, "http_429": 1}
    http_client.close()


def test_retry_delay_uses_rate_limit_reset_header() -> None:
    response = httpx.Response(
        429,
        headers={"x-ratelimit-reset-requests": "12.5s"},
    )

    assert ClovaStudioEmbeddingClient._retry_delay(response, 0) == 12.5


def test_clova_client_rejects_provider_error() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "status": {"code": "40001", "message": "Invalid parameter"},
                "result": {},
            },
        )
    )
    http_client = httpx.Client(transport=transport)
    client = ClovaStudioEmbeddingClient(
        "secret",
        EmbeddingConfig(max_retries=0),
        client=http_client,
    )

    with pytest.raises(RuntimeError, match="40001"):
        client.embed("공급계약")
    http_client.close()


def test_config_rejects_wrong_dimension() -> None:
    with pytest.raises(ValueError, match="1024"):
        EmbeddingConfig(dimensions=768)


def test_config_rejects_unknown_input_version() -> None:
    with pytest.raises(ValueError, match="Unsupported embedding input version"):
        EmbeddingConfig(input_version="retrieval-embedding-unknown")
