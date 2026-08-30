from __future__ import annotations

import math

import httpx
import pytest

from disclosure_agent.retrieval.embeddings import (
    ClovaStudioEmbeddingClient,
    EmbeddingConfig,
    compose_embedding_input,
    embedding_input_sha256,
    embedding_run_id,
    vector_literal,
)


def test_compose_embedding_input_adds_provenance_context() -> None:
    value = compose_embedding_input(
        "계약금액은 100억원입니다.",
        ["주요사항보고서", "단일판매·공급계약"],
    )

    assert value == (
        "[문맥] 주요사항보고서 > 단일판매·공급계약\n\n"
        "계약금액은 100억원입니다."
    )
    assert embedding_input_sha256(value) == embedding_input_sha256(value)


def test_embedding_run_identity_changes_with_input_contract() -> None:
    left = embedding_run_id("chunk-run", EmbeddingConfig())
    right = embedding_run_id(
        "chunk-run",
        EmbeddingConfig(input_version="retrieval-embedding-v2"),
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
