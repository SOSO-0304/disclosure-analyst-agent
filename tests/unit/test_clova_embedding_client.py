import pytest

from disclosure_agent.llm.clova_embedding_client import (
    CLOVA_EMBEDDING_DIMENSION,
    parse_embedding_payload,
    parse_rate_limit_reset,
    retry_delay_seconds,
)


def test_parse_embedding_payload_accepts_documented_shape() -> None:
    payload = {
        "status": {"code": "20000", "message": "OK"},
        "result": {
            "embedding": [0.25] * CLOVA_EMBEDDING_DIMENSION,
            "inputTokens": 17,
        },
    }

    result = parse_embedding_payload(payload)

    assert len(result.vector) == CLOVA_EMBEDDING_DIMENSION
    assert result.vector[0] == 0.25
    assert result.input_tokens == 17


def test_parse_embedding_payload_rejects_wrong_dimension() -> None:
    payload = {
        "status": {"code": "20000", "message": "OK"},
        "result": {"embedding": [0.25], "inputTokens": 1},
    }

    with pytest.raises(RuntimeError, match="dimension mismatch"):
        parse_embedding_payload(payload)


def test_parse_rate_limit_reset_accepts_documented_seconds() -> None:
    assert parse_rate_limit_reset("23s") == 23.0
    assert parse_rate_limit_reset("1500ms") == 1.5


def test_retry_delay_prefers_longest_documented_reset_window() -> None:
    headers = {
        "x-ratelimit-reset-requests": "12s",
        "x-ratelimit-reset-tokens": "23s",
    }

    assert retry_delay_seconds(headers, 0) == 24.0


def test_retry_delay_falls_back_to_exponential_backoff() -> None:
    assert retry_delay_seconds({}, 0) == 1.0
    assert retry_delay_seconds({}, 3) == 8.0
