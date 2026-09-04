"""Explicit, secret-safe configuration for the isolated perf retrieval tools."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

DEPLOYMENT_DATABASE_URL_ENV = "DISCLOSURE_DATABASE_URL"
DEPLOYMENT_DATABASE_HOST_ENV = "DISCLOSURE_DATABASE_HOST"
DEPLOYMENT_DATABASE_NAME = "disclosure_perf"


@dataclass(frozen=True)
class RetrievalRuntime:
    database_url: str = field(repr=False)
    api_key: str = field(repr=False)
    env_file: Path


def assert_perf_database(database_url: str) -> None:
    try:
        url = make_url(database_url)
    except Exception:
        raise ValueError("Invalid perf database URL; credentials are not displayed") from None
    if (
        url.get_backend_name() != "postgresql"
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or url.port != 55432
        or url.database != "disclosure_perf"
    ):
        raise ValueError("Only localhost:55432/disclosure_perf is allowed")


def assert_deployment_database(database_url: str, *, allowed_host: str) -> None:
    """Allow one explicitly named deployment database and no implicit fallback.

    The API container needs to reach PostgreSQL by its private Docker DNS name rather
    than localhost.  Requiring the operator to repeat that exact hostname separately
    prevents a copied URL from silently pointing the service at an unrelated database.
    """

    try:
        url = make_url(database_url)
    except Exception:
        raise ValueError("Invalid deployment database URL; credentials are not displayed") from None
    expected_host = allowed_host.strip()
    if not expected_host:
        raise ValueError(f"Set {DEPLOYMENT_DATABASE_HOST_ENV} for deployment")
    if (
        url.get_backend_name() != "postgresql"
        or url.host != expected_host
        or url.port != 5432
        or url.database != DEPLOYMENT_DATABASE_NAME
    ):
        raise ValueError("Deployment database does not match the explicit host/port/name boundary")


def load_runtime(
    database_url: str | None = None,
    *,
    env_file: str | Path | None = None,
    api_key_env: str = "CLOVASTUDIO_API_KEY",
) -> RetrievalRuntime:
    # Never discover parent .env files or read the unrelated production DATABASE_URL.
    path = (
        Path(env_file)
        if env_file is not None
        else (Path(__file__).resolve().parents[3] / ".env.perf")
    )
    if env_file is not None and not path.is_file():
        raise ValueError("The explicit --env-file does not exist")
    values = dotenv_values(path, encoding="utf-8-sig", interpolate=False) if path.is_file() else {}
    url = database_url or os.environ.get("PERF_DATABASE_URL") or values.get("PERF_DATABASE_URL")
    if not url or not url.strip():
        raise ValueError("Set PERF_DATABASE_URL in .env.perf or pass --database-url")
    assert_perf_database(url.strip())
    key = os.environ.get(api_key_env) or values.get(api_key_env) or ""
    return RetrievalRuntime(url.strip(), key.strip(), path)


def load_api_runtime(
    *,
    env_file: str | Path | None = None,
    api_key_env: str = "CLOVASTUDIO_API_KEY",
) -> RetrievalRuntime:
    """Load local perf settings or an explicit private deployment target.

    Local development keeps the strict localhost:55432 guard.  Deployment is enabled
    only when both ``DISCLOSURE_DATABASE_URL`` and ``DISCLOSURE_DATABASE_HOST`` are
    present, so the generic ``DATABASE_URL`` is never consumed accidentally.
    """

    path = (
        Path(env_file)
        if env_file is not None
        else (Path(__file__).resolve().parents[3] / ".env.perf")
    )
    if env_file is not None and not path.is_file():
        raise ValueError("The explicit API env file does not exist")
    values = dotenv_values(path, encoding="utf-8-sig", interpolate=False) if path.is_file() else {}
    deployment_url = os.environ.get(DEPLOYMENT_DATABASE_URL_ENV) or values.get(
        DEPLOYMENT_DATABASE_URL_ENV
    )
    if not deployment_url:
        return load_runtime(env_file=path if path.is_file() else None, api_key_env=api_key_env)

    allowed_host = os.environ.get(DEPLOYMENT_DATABASE_HOST_ENV) or values.get(
        DEPLOYMENT_DATABASE_HOST_ENV
    )
    assert_deployment_database(deployment_url.strip(), allowed_host=str(allowed_host or ""))
    key = os.environ.get(api_key_env) or values.get(api_key_env) or ""
    return RetrievalRuntime(deployment_url.strip(), key.strip(), path)


def add_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database-url", help="Overrides PERF_DATABASE_URL in .env.perf")
    parser.add_argument("--env-file", type=Path, help="Default: repository-root .env.perf")


def runtime_from_args(args: argparse.Namespace) -> RetrievalRuntime:
    return load_runtime(
        args.database_url,
        env_file=args.env_file,
        api_key_env=getattr(args, "api_key_env", "CLOVASTUDIO_API_KEY"),
    )
