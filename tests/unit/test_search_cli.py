from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from disclosure_agent.retrieval.embeddings import EmbeddingResult

SPEC = importlib.util.spec_from_file_location(
    "search_cli_test_target", Path(__file__).resolve().parents[2] / "scripts/search_retrieval.py"
)
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


class Context:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Connection(Context):
    def __init__(self):
        self.statements = []

    def begin(self):
        return Context()

    def execute(self, sql):
        self.statements.append(str(sql))


@pytest.fixture
def setup_cli(tmp_path, monkeypatch):
    path = tmp_path / ".env.perf"
    path.write_text(
        "PERF_DATABASE_URL=postgresql+psycopg://user:secret@localhost:55432/disclosure_perf\n"
        "CLOVASTUDIO_API_KEY=file-key\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("PERF_DATABASE_URL", raising=False)
    monkeypatch.delenv("CLOVASTUDIO_API_KEY", raising=False)
    connection = Connection()
    monkeypatch.setattr(cli, "get_engine", lambda url: SimpleNamespace(connect=lambda: connection))
    monkeypatch.setattr(
        cli,
        "completed_run",
        lambda conn, run_id: {
            "embedding_run_id": "v2",
            "chunk_run_id": "chunks",
            "provider": "clova-studio",
            "model": "bge-m3",
            "dimensions": 1024,
            "distance_metric": "cosine",
            "endpoint": "https://example.invalid",
            "input_version": "retrieval-embedding-v2",
        },
    )
    monkeypatch.setattr(
        cli,
        "company_catalog",
        lambda conn: [
            {
                "corp_code": "001",
                "listed_name": "삼성생명",
                "corp_name": "삼성생명",
                "stock_code": "123456",
            }
        ],
    )
    captured = {}

    def search(conn, **kwargs):
        captured.update(kwargs)
        return {
            "results": [],
            "warnings": [],
            "candidate_counts": {},
            "dense_strategy": "not_used" if kwargs["mode"] == "lexical" else "exact_filtered",
        }

    monkeypatch.setattr(cli, "retrieve", search)
    return path, connection, captured


def test_cli_lexical_uses_file_database_and_no_provider(setup_cli, monkeypatch, capsys):
    path, connection, captured = setup_cli
    monkeypatch.setattr(
        cli,
        "ClovaStudioEmbeddingClient",
        lambda *a, **k: pytest.fail("lexical mode must not call provider"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "search_retrieval.py",
            "삼성생명의 매출",
            "--env-file",
            str(path),
            "--mode",
            "lexical",
            "--date-from",
            "2025-01-01",
            "--date-to",
            "2025-12-31",
            "--corrections",
            "only",
            "--json",
        ],
    )
    cli.main()
    assert captured["vector"] is None
    assert captured["filters"]["corp_code"] == "001"
    assert captured["filters"]["date_from"].year == 2025
    assert captured["filters"]["corrections"] == "only"
    assert connection.statements.count("SET TRANSACTION READ ONLY") == 2
    assert capsys.readouterr().out.strip() == "[]"


def test_cli_hybrid_reads_file_key_and_embeds_only_query(setup_cli, monkeypatch, capsys):
    path, _, captured = setup_cli
    provider_calls = []

    class Client(Context):
        def __init__(self, api_key, config):
            assert api_key == "file-key"
            assert config.input_version == "retrieval-embedding-v2"

        def embed(self, query):
            provider_calls.append(query)
            return EmbeddingResult(vector=tuple([0.1] * 1024), input_tokens=3, request_id="test")

    monkeypatch.setattr(cli, "ClovaStudioEmbeddingClient", Client)
    monkeypatch.setattr(sys, "argv", ["search_retrieval.py", "매출", "--env-file", str(path)])
    cli.main()
    assert provider_calls == ["매출"]
    assert captured["mode"] == "hybrid" and captured["vector"].startswith("[")
    assert "file-key" not in capsys.readouterr().out


def test_cli_unknown_explicit_company_fails_before_provider(setup_cli, monkeypatch):
    path, _, captured = setup_cli
    monkeypatch.setattr(
        cli,
        "ClovaStudioEmbeddingClient",
        lambda *a, **k: pytest.fail("must reject unknown company first"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["search_retrieval.py", "매출", "--env-file", str(path), "--company", "모르는회사"],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert not captured
