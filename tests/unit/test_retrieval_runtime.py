from __future__ import annotations

import os

import pytest

from disclosure_agent.retrieval.runtime import assert_perf_database, load_runtime

PERF = "postgresql+psycopg://user:secret@localhost:55432/disclosure_perf"


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    for key in ("PERF_DATABASE_URL", "CLOVASTUDIO_API_KEY", "OTHER_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def test_dotenv_bom_quotes_comments_and_no_environment_mutation(tmp_path):
    path = tmp_path / ".env.perf"
    path.write_text(
        f'export PERF_DATABASE_URL="{PERF}"\nCLOVASTUDIO_API_KEY="key#value" # comment\n',
        encoding="utf-8-sig",
    )
    before = dict(os.environ)
    runtime = load_runtime(env_file=path)
    assert runtime.database_url == PERF
    assert runtime.api_key == "key#value"
    assert dict(os.environ) == before
    assert "secret" not in repr(runtime)
    assert "key#value" not in repr(runtime)


def test_explicit_and_environment_precedence(tmp_path, monkeypatch):
    path = tmp_path / ".env.perf"
    path.write_text(f"PERF_DATABASE_URL={PERF}\nCLOVASTUDIO_API_KEY=file-key\n")
    explicit = PERF.replace("user:secret", "explicit:password")
    monkeypatch.setenv("PERF_DATABASE_URL", PERF.replace("user:secret", "env:password"))
    monkeypatch.setenv("CLOVASTUDIO_API_KEY", "env-key")
    runtime = load_runtime(explicit, env_file=path)
    assert runtime.database_url == explicit
    assert runtime.api_key == "env-key"
    assert load_runtime(env_file=path).database_url == os.environ["PERF_DATABASE_URL"]


def test_unrelated_database_url_is_not_used(tmp_path, monkeypatch):
    path = tmp_path / ".env.perf"
    path.write_text("DATABASE_URL=postgresql://prod:secret@remote/prod\n")
    monkeypatch.setenv("DATABASE_URL", "postgresql://prod:secret@remote/prod")
    with pytest.raises(ValueError, match="PERF_DATABASE_URL"):
        load_runtime(env_file=path)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:secret@remote:55432/disclosure_perf",
        "postgresql://user:secret@localhost:5432/disclosure_perf",
        "postgresql://user:secret@localhost:55432/production",
        "sqlite:///production.db",
        "not a url with secret",
    ],
)
def test_perf_guard_rejects_wrong_targets_without_exposing_secrets(url):
    with pytest.raises(ValueError) as exc:
        assert_perf_database(url)
    assert "secret" not in str(exc.value)


def test_missing_explicit_file_is_error(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        load_runtime(PERF, env_file=tmp_path / "missing")


def test_custom_key_name_and_literal_dollar(tmp_path):
    path = tmp_path / ".env.perf"
    path.write_text(f"PERF_DATABASE_URL={PERF}\nOTHER_API_KEY=literal\u0024{{NOT_EXPANDED}}\n")
    assert (
        load_runtime(env_file=path, api_key_env="OTHER_API_KEY").api_key
        == "literal\u0024{NOT_EXPANDED}"
    )


def test_default_file_is_repo_anchored_not_cwd(tmp_path, monkeypatch):
    import disclosure_agent.retrieval.runtime as runtime

    monkeypatch.setattr(
        runtime, "__file__", str(tmp_path / "src/disclosure_agent/retrieval/runtime.py")
    )
    (tmp_path / ".env.perf").write_text(f"PERF_DATABASE_URL={PERF}\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert load_runtime().env_file == tmp_path / ".env.perf"
