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
            "dense_diagnostics": {"pool_attempts": [], "index_usage": "not_observed"},
            "lexical_terms": ["매출"],
            "lexical_diagnostics": {"rank_policy": "candidate_midrank"},
            "timing_seconds": {"dense_initial": 0.1, "lexical": 0.2, "hydration": 0.03},
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
    monkeypatch.setattr(
        sys, "argv", ["search_retrieval.py", "매출", "--env-file", str(path), "--mode", "hybrid"]
    )
    cli.main()
    assert provider_calls == ["매출"]
    assert captured["mode"] == "hybrid" and captured["vector"].startswith("[")
    output = capsys.readouterr().out
    assert "file-key" not in output and "secret" not in output
    assert "dense_initial=0.100s" in output and "lexical=0.200s" in output
    assert "hydration=0.030s" in output and "candidate_midrank" in output


def test_cli_defaults_to_dense(setup_cli, monkeypatch, capsys):
    path, _, captured = setup_cli

    class Client(Context):
        def __init__(self, *args):
            pass

        def embed(self, query):
            return EmbeddingResult(vector=tuple([0.1] * 1024), input_tokens=3, request_id="test")

    monkeypatch.setattr(cli, "ClovaStudioEmbeddingClient", Client)
    monkeypatch.setattr(sys, "argv", ["search_retrieval.py", "매출", "--env-file", str(path)])
    cli.main()
    assert captured["mode"] == "dense"


def test_explain_mode_writes_new_report_without_normal_search(setup_cli, tmp_path, monkeypatch):
    path, _, captured = setup_cli
    destination = tmp_path / "plans.json"
    observed = {}

    def explain(connection, **kwargs):
        observed.update(kwargs)
        return {"status": "analyzed", "stages": {}}

    monkeypatch.setattr(cli, "explain_retrieval", explain)
    monkeypatch.setattr(
        cli,
        "ClovaStudioEmbeddingClient",
        lambda *a, **k: pytest.fail("lexical diagnostics must not call provider"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "search_retrieval.py",
            "매출",
            "--env-file",
            str(path),
            "--mode",
            "lexical",
            "--explain",
            "--analyze",
            "--explain-report",
            str(destination),
        ],
    )
    cli.main()
    assert not captured
    assert observed["analyze"] is True and observed["vector"] is None
    assert '"status": "analyzed"' in destination.read_text(encoding="utf-8")


def test_existing_diagnostic_report_is_preserved_before_api(setup_cli, tmp_path, monkeypatch):
    path, _, captured = setup_cli
    destination = tmp_path / "plans.json"
    destination.write_text("keep me", encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "ClovaStudioEmbeddingClient",
        lambda *a, **k: pytest.fail("must reject existing file before API"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "search_retrieval.py",
            "매출",
            "--env-file",
            str(path),
            "--explain",
            "--explain-report",
            str(destination),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2 and not captured
    assert destination.read_text(encoding="utf-8") == "keep me"


def test_hybrid_explain_embeds_once_and_never_runs_normal_retrieval(
    setup_cli, tmp_path, monkeypatch
):
    path, _, captured = setup_cli
    calls = []
    destination = tmp_path / "hybrid-plan.json"

    class Client(Context):
        def __init__(self, key, config):
            assert key == "file-key"

        def embed(self, query):
            calls.append(query)
            return EmbeddingResult(vector=tuple([0.1] * 1024), input_tokens=3, request_id="test")

    def explain(connection, **kwargs):
        assert kwargs["mode"] == "hybrid" and kwargs["vector"].startswith("[")
        assert kwargs["analyze"] is False
        return {"status": "planned", "stages": {}}

    monkeypatch.setattr(cli, "ClovaStudioEmbeddingClient", Client)
    monkeypatch.setattr(cli, "explain_retrieval", explain)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "search_retrieval.py",
            "매출",
            "--env-file",
            str(path),
            "--mode",
            "hybrid",
            "--explain",
            "--explain-report",
            str(destination),
        ],
    )
    cli.main()
    assert calls == ["매출"] and not captured
    value = destination.read_text(encoding="utf-8")
    assert "file-key" not in value and "secret" not in value and "vector" not in value


def test_partial_diagnostic_writes_report_then_exits_nonzero(setup_cli, tmp_path, monkeypatch):
    path, _, captured = setup_cli
    destination = tmp_path / "partial.json"
    monkeypatch.setattr(
        cli, "explain_retrieval", lambda *a, **k: {"status": "partial", "stages": {}}
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "search_retrieval.py",
            "매출",
            "--env-file",
            str(path),
            "--mode",
            "lexical",
            "--explain",
            "--analyze",
            "--explain-report",
            str(destination),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1 and not captured
    assert '"status": "partial"' in destination.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "flags", [["--analyze"], ["--explain-report", "new.json"], ["--explain", "--json"]]
)
def test_invalid_explain_options_fail_before_configuration(monkeypatch, flags):
    monkeypatch.setattr(
        cli, "runtime_from_args", lambda args: pytest.fail("must fail before runtime")
    )
    monkeypatch.setattr(sys, "argv", ["search_retrieval.py", "매출", *flags])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


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
