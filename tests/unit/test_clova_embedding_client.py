import pytest

from disclosure_agent.llm.clova_embedding_client import (
    CLOVA_EMBEDDING_DIMENSION,
    parse_embedding_payload,
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
