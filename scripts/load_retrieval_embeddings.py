#!/usr/bin/env python3
"""Resumably embed the active retrieval chunks with CLOVA Studio bge-m3."""

from __future__ import annotations

import argparse
import time
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import orjson
from sqlalchemy import Connection, text
from sqlalchemy.engine import make_url

from disclosure_agent.retrieval.embeddings import (
    DEFAULT_TARGET_QPM,
    EMBEDDING_INPUT_VERSION,
    EMBEDDING_INPUT_VERSION_V1,
    EMBEDDING_INPUT_VERSION_V2,
    EMBEDDING_INPUT_VERSION_V3,
    ClovaStudioEmbeddingClient,
    EmbeddingConfig,
    EmbeddingDocumentContext,
    EmbeddingResult,
    EmbeddingTelemetry,
    GlobalRateLimiter,
    compose_embedding_input,
    embedding_input_sha256,
    embedding_run_id,
    vector_literal,
)
from disclosure_agent.retrieval.runtime import add_runtime_arguments, runtime_from_args
from disclosure_agent.storage.database import get_engine

PENDING_SQL = """
    SELECT
        c.chunk_id,
        c.chunk_run_id,
        c.content,
        c.content_sha256,
        c.heading_path,
        c.metadata,
        sc.corp_name,
        sc.listed_name,
        coalesce(sc.stock_code, '') AS stock_code,
        f.report_name,
        coalesce(f.document_subtype, '') AS document_subtype,
        coalesce(d.title_normalized, '') AS document_title,
        f.is_correction
    FROM public.retrieval_chunks c
    JOIN public.source_filings f ON f.filing_id = c.filing_id
    JOIN public.source_companies sc ON sc.corp_code = f.corp_code
    JOIN public.source_documents d ON d.document_id = c.document_id
    LEFT JOIN public.retrieval_embeddings e
      ON e.embedding_run_id = :embedding_run_id
     AND e.chunk_id = c.chunk_id
    WHERE c.chunk_run_id = :chunk_run_id
      AND c.chunk_id > :after_chunk_id
      AND (
        e.embedding_run_id IS NULL
        OR e.chunk_content_sha256 <> c.content_sha256
      )
    ORDER BY c.chunk_id
    LIMIT :page_size
"""

SAMPLE_PENDING_SQL = """
    WITH ranked AS (
        SELECT
            c.chunk_id,
            c.chunk_run_id,
            c.content,
            c.content_sha256,
            c.heading_path,
            c.metadata,
            sc.corp_name,
            sc.listed_name,
            coalesce(sc.stock_code, '') AS stock_code,
            f.report_name,
            coalesce(f.document_subtype, '') AS document_subtype,
            coalesce(d.title_normalized, '') AS document_title,
            f.is_correction,
            row_number() OVER (
                PARTITION BY f.corp_code, c.document_group, c.chunk_type
                ORDER BY md5(c.chunk_id || :sample_seed), c.chunk_id
            ) AS sample_rank
        FROM public.retrieval_chunks c
        JOIN public.source_filings f ON f.filing_id = c.filing_id
        JOIN public.source_companies sc ON sc.corp_code = f.corp_code
        JOIN public.source_documents d ON d.document_id = c.document_id
        WHERE c.chunk_run_id = :chunk_run_id
    )
    SELECT ranked.*
    FROM ranked
    LEFT JOIN public.retrieval_embeddings e
      ON e.embedding_run_id = :embedding_run_id
     AND e.chunk_id = ranked.chunk_id
    WHERE ranked.sample_rank <= :sample_per_stratum
      AND ranked.chunk_id > :after_chunk_id
      AND (
        e.embedding_run_id IS NULL
        OR e.chunk_content_sha256 <> ranked.content_sha256
      )
    ORDER BY ranked.chunk_id
    LIMIT :page_size
"""

SAMPLE_COUNTS_SQL = """
    WITH ranked AS (
        SELECT
            c.chunk_id,
            c.content_sha256,
            row_number() OVER (
                PARTITION BY f.corp_code, c.document_group, c.chunk_type
                ORDER BY md5(c.chunk_id || :sample_seed), c.chunk_id
            ) AS sample_rank
        FROM public.retrieval_chunks c
        JOIN public.source_filings f ON f.filing_id = c.filing_id
        WHERE c.chunk_run_id = :chunk_run_id
    ), sampled AS (
        SELECT chunk_id, content_sha256
        FROM ranked
        WHERE sample_rank <= :sample_per_stratum
    )
    SELECT
        count(*)::bigint AS sample_chunks,
        count(*) FILTER (
            WHERE e.embedding_run_id IS NULL
               OR e.chunk_content_sha256 <> sampled.content_sha256
        )::bigint AS sample_pending
    FROM sampled
    LEFT JOIN public.retrieval_embeddings e
      ON e.embedding_run_id = :embedding_run_id
     AND e.chunk_id = sampled.chunk_id
"""

UPSERT_SQL = """
    INSERT INTO public.retrieval_embeddings (
        embedding_run_id,
        chunk_run_id,
        chunk_id,
        chunk_content_sha256,
        input_sha256,
        embedding,
        input_tokens,
        provider_request_id,
        embedded_at
    ) VALUES (
        :embedding_run_id,
        :chunk_run_id,
        :chunk_id,
        :chunk_content_sha256,
        :input_sha256,
        CAST(:embedding AS vector),
        :input_tokens,
        :provider_request_id,
        :embedded_at
    )
    ON CONFLICT (embedding_run_id, chunk_id) DO UPDATE SET
        chunk_content_sha256 = EXCLUDED.chunk_content_sha256,
        input_sha256 = EXCLUDED.input_sha256,
        embedding = EXCLUDED.embedding,
        input_tokens = EXCLUDED.input_tokens,
        provider_request_id = EXCLUDED.provider_request_id,
        embedded_at = EXCLUDED.embedded_at
"""


@dataclass(frozen=True, slots=True)
class EmbeddingTask:
    chunk_id: str
    chunk_run_id: str
    content_sha256: str
    input_text: str
    input_sha256: str


@dataclass(frozen=True, slots=True)
class EmbeddedTask:
    task: EmbeddingTask
    result: EmbeddingResult


@dataclass(frozen=True, slots=True)
class LoadResult:
    run_id: str
    counts: dict[str, int]
    status: str
    call_counts: dict[str, int]
    failure_categories: dict[str, int]
    failure_examples: list[dict[str, str]]
    provider_telemetry: dict[str, Any]
    rate_limit: dict[str, int | float | None]
    scope: dict[str, Any]


def _assert_perf_database(database_url: str) -> None:
    url = make_url(database_url)
    if url.database != "disclosure_perf" or url.port != 55432:
        raise SystemExit(
            "Embedding loader is restricted to disclosure_perf on localhost:55432; "
            f"got database={url.database!r}, port={url.port!r}"
        )


def _active_chunk_run(connection: Connection) -> dict[str, Any]:
    row = (
        connection.execute(
            text(
                """
            SELECT chunk_run_id, counts
            FROM public.retrieval_chunk_runs
            WHERE is_active AND status = 'completed'
            """
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise RuntimeError("No completed active retrieval chunk run")
    return dict(row)


def _prepare_run(
    connection: Connection,
    *,
    chunk_run_id: str,
    run_id: str,
    config: EmbeddingConfig,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    connection.execute(
        text(
            """
            INSERT INTO public.embedding_runs (
                embedding_run_id,
                chunk_run_id,
                provider,
                model,
                dimensions,
                distance_metric,
                endpoint,
                input_version,
                status,
                counts,
                started_at,
                updated_at,
                is_active
            ) VALUES (
                :run_id,
                :chunk_run_id,
                :provider,
                :model,
                :dimensions,
                :distance_metric,
                :endpoint,
                :input_version,
                'running',
                '{}'::jsonb,
                :now,
                :now,
                false
            )
            ON CONFLICT (embedding_run_id) DO NOTHING
            """
        ),
        {
            "run_id": run_id,
            "chunk_run_id": chunk_run_id,
            "provider": config.provider,
            "model": config.model,
            "dimensions": config.dimensions,
            "distance_metric": config.distance_metric,
            "endpoint": config.endpoint,
            "input_version": config.input_version,
            "now": now,
        },
    )
    row = (
        connection.execute(
            text("SELECT * FROM public.embedding_runs WHERE embedding_run_id = :run_id"),
            {"run_id": run_id},
        )
        .mappings()
        .one()
    )
    contract = {
        "chunk_run_id": chunk_run_id,
        "provider": config.provider,
        "model": config.model,
        "dimensions": config.dimensions,
        "distance_metric": config.distance_metric,
        "endpoint": config.endpoint,
        "input_version": config.input_version,
    }
    mismatches = {
        key: (row[key], expected) for key, expected in contract.items() if row[key] != expected
    }
    if mismatches:
        raise RuntimeError(f"Existing embedding run contract mismatch: {mismatches}")
    return dict(row)


def _run_counts(connection: Connection, run_id: str, chunk_run_id: str) -> dict[str, int]:
    row = (
        connection.execute(
            text(
                """
            SELECT
                count(*)::bigint AS embedded_chunks,
                coalesce(sum(e.input_tokens), 0)::bigint AS input_tokens,
                count(*) FILTER (
                    WHERE e.chunk_content_sha256 <> c.content_sha256
                )::bigint AS stale_embeddings,
                (
                    SELECT count(*)::bigint
                    FROM public.retrieval_chunks
                    WHERE chunk_run_id = :chunk_run_id
                ) AS total_chunks
            FROM public.retrieval_embeddings e
            JOIN public.retrieval_chunks c
              ON c.chunk_run_id = e.chunk_run_id
             AND c.chunk_id = e.chunk_id
            WHERE e.embedding_run_id = :run_id
            """
            ),
            {"run_id": run_id, "chunk_run_id": chunk_run_id},
        )
        .mappings()
        .one()
    )
    values = {key: int(value or 0) for key, value in row.items()}
    values["pending_chunks"] = (
        values["total_chunks"] - values["embedded_chunks"] + values["stale_embeddings"]
    )
    return values


def _tasks(
    connection: Connection,
    *,
    run_id: str,
    chunk_run_id: str,
    after_chunk_id: str,
    page_size: int,
    input_version: str,
    sample_per_stratum: int | None,
    sample_seed: str,
) -> list[EmbeddingTask]:
    query = SAMPLE_PENDING_SQL if sample_per_stratum is not None else PENDING_SQL
    rows = connection.execute(
        text(query),
        {
            "embedding_run_id": run_id,
            "chunk_run_id": chunk_run_id,
            "after_chunk_id": after_chunk_id,
            "page_size": page_size,
            "sample_per_stratum": sample_per_stratum,
            "sample_seed": sample_seed,
        },
    ).mappings()
    tasks: list[EmbeddingTask] = []
    for row in rows:
        metadata = dict(row["metadata"] or {})
        context = EmbeddingDocumentContext(
            corp_name=str(row["corp_name"] or ""),
            listed_name=str(row["listed_name"] or ""),
            stock_code=str(row["stock_code"] or ""),
            report_name=str(row["report_name"] or ""),
            document_subtype=str(row["document_subtype"] or ""),
            document_title=str(row["document_title"] or ""),
            is_correction=bool(row["is_correction"]),
            table_caption=str(metadata.get("caption") or ""),
        )
        input_text = compose_embedding_input(
            str(row["content"]),
            list(row["heading_path"] or []),
            input_version=input_version,
            context=context,
        )
        tasks.append(
            EmbeddingTask(
                chunk_id=str(row["chunk_id"]),
                chunk_run_id=str(row["chunk_run_id"]),
                content_sha256=str(row["content_sha256"]),
                input_text=input_text,
                input_sha256=embedding_input_sha256(input_text),
            )
        )
    return tasks


def _sample_counts(
    connection: Connection,
    *,
    run_id: str,
    chunk_run_id: str,
    sample_per_stratum: int,
    sample_seed: str,
) -> dict[str, int]:
    row = (
        connection.execute(
            text(SAMPLE_COUNTS_SQL),
            {
                "embedding_run_id": run_id,
                "chunk_run_id": chunk_run_id,
                "sample_per_stratum": sample_per_stratum,
                "sample_seed": sample_seed,
            },
        )
        .mappings()
        .one()
    )
    return {key: int(value or 0) for key, value in row.items()}


def _embed_page(
    client: ClovaStudioEmbeddingClient,
    tasks: list[EmbeddingTask],
    workers: int,
) -> tuple[list[EmbeddedTask], list[tuple[EmbeddingTask, Exception]]]:
    successes: list[EmbeddedTask] = []
    failures: list[tuple[EmbeddingTask, Exception]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures: dict[Future[EmbeddingResult], EmbeddingTask] = {
            executor.submit(client.embed, task.input_text): task for task in tasks
        }
        for future in as_completed(futures):
            task = futures[future]
            try:
                successes.append(EmbeddedTask(task=task, result=future.result()))
            except Exception as exc:  # noqa: BLE001 - persisted for resumable retry
                failures.append((task, exc))
    return successes, failures


def _failure_category(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return f"http_{error.response.status_code}"
    if isinstance(error, httpx.TimeoutException):
        return "timeout"
    if isinstance(error, httpx.TransportError):
        return "transport_error"
    if isinstance(error, ValueError):
        return "input_validation"
    return type(error).__name__


def _save_page(
    connection: Connection,
    run_id: str,
    values: list[EmbeddedTask],
) -> None:
    if not values:
        return
    now = datetime.now(UTC)
    rows = [
        {
            "embedding_run_id": run_id,
            "chunk_run_id": value.task.chunk_run_id,
            "chunk_id": value.task.chunk_id,
            "chunk_content_sha256": value.task.content_sha256,
            "input_sha256": value.task.input_sha256,
            "embedding": vector_literal(value.result.vector),
            "input_tokens": value.result.input_tokens,
            "provider_request_id": value.result.request_id,
            "embedded_at": now,
        }
        for value in values
    ]
    connection.execute(text(UPSERT_SQL), rows)


def _update_run(
    connection: Connection,
    *,
    run_id: str,
    counts: dict[str, int],
    status: str,
    last_error: str | None,
    activate: bool,
) -> None:
    if activate:
        connection.execute(
            text(
                """
                UPDATE public.embedding_runs
                SET is_active = false
                WHERE chunk_run_id = (
                    SELECT chunk_run_id
                    FROM public.embedding_runs
                    WHERE embedding_run_id = :run_id
                )
                  AND is_active
                """
            ),
            {"run_id": run_id},
        )
    connection.execute(
        text(
            """
            UPDATE public.embedding_runs
            SET status = :status,
                counts = CAST(:counts AS jsonb),
                last_error = :last_error,
                updated_at = :now,
                completed_at = CASE WHEN :activate THEN :now ELSE NULL END,
                is_active = :activate
            WHERE embedding_run_id = :run_id
            """
        ),
        {
            "run_id": run_id,
            "status": status,
            "counts": orjson.dumps(counts, option=orjson.OPT_SORT_KEYS).decode(),
            "last_error": last_error,
            "now": datetime.now(UTC),
            "activate": activate,
        },
    )


def _load(
    *,
    database_url: str,
    config: EmbeddingConfig,
    api_key: str,
    workers: int,
    page_size: int,
    limit: int | None,
    requests_per_minute: int,
    sample_per_stratum: int | None,
    sample_seed: str,
) -> LoadResult:
    engine = get_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('retrieval_embedding_prepare'))")
        )
        chunk_run = _active_chunk_run(connection)
        chunk_run_id = str(chunk_run["chunk_run_id"])
        run_id = embedding_run_id(chunk_run_id, config)
        existing = _prepare_run(
            connection,
            chunk_run_id=chunk_run_id,
            run_id=run_id,
            config=config,
        )
        prior = Counter({key: int(value) for key, value in (existing["counts"] or {}).items()})
        sample_pending_start = None
        if sample_per_stratum is not None:
            sample_pending_start = _sample_counts(
                connection,
                run_id=run_id,
                chunk_run_id=chunk_run_id,
                sample_per_stratum=sample_per_stratum,
                sample_seed=sample_seed,
            )["sample_pending"]

    attempts = prior["provider_attempts"]
    failures_total = prior["provider_failures"]
    processed_this_call = 0
    successful_this_call = 0
    failure_categories: Counter[str] = Counter()
    failure_examples: list[dict[str, str]] = []
    last_error: str | None = None
    after_chunk_id = ""
    started = time.perf_counter()
    rate_limiter = GlobalRateLimiter(target_qpm=requests_per_minute)
    telemetry = EmbeddingTelemetry()

    with ClovaStudioEmbeddingClient(
        api_key,
        config,
        rate_limiter=rate_limiter,
        telemetry=telemetry,
    ) as client:
        while limit is None or processed_this_call < limit:
            current_size = page_size
            if limit is not None:
                current_size = min(current_size, limit - processed_this_call)
            with engine.connect() as connection, connection.begin():
                connection.execute(text("SET TRANSACTION READ ONLY"))
                tasks = _tasks(
                    connection,
                    run_id=run_id,
                    chunk_run_id=chunk_run_id,
                    after_chunk_id=after_chunk_id,
                    page_size=current_size,
                    input_version=config.input_version,
                    sample_per_stratum=sample_per_stratum,
                    sample_seed=sample_seed,
                )
            if not tasks:
                break
            after_chunk_id = tasks[-1].chunk_id
            successes, failures = _embed_page(client, tasks, workers)
            attempts += len(tasks)
            failures_total += len(failures)
            processed_this_call += len(tasks)
            successful_this_call += len(successes)
            if failures:
                last_error = f"{failures[-1][0].chunk_id}: {failures[-1][1]}"[:2_000]
                for failed_task, error in failures:
                    category = _failure_category(error)
                    failure_categories[category] += 1
                    if len(failure_examples) < 5:
                        failure_examples.append(
                            {
                                "chunk_id": failed_task.chunk_id,
                                "category": category,
                                "error": str(error)[:500],
                            }
                        )

            with engine.begin() as connection:
                _save_page(connection, run_id, successes)
                counts = _run_counts(connection, run_id, chunk_run_id)
                current_telemetry = telemetry.snapshot()
                counts["provider_attempts"] = attempts
                counts["provider_failures"] = failures_total
                counts["provider_http_requests"] = prior["provider_http_requests"] + int(
                    current_telemetry["http_requests"]
                )
                counts["provider_retries"] = prior["provider_retries"] + int(
                    current_telemetry["retries"]
                )
                _update_run(
                    connection,
                    run_id=run_id,
                    counts=counts,
                    status="running",
                    last_error=last_error,
                    activate=False,
                )
            elapsed = max(time.perf_counter() - started, 0.001)
            rate = successful_this_call / elapsed
            pending_for_eta = counts["pending_chunks"]
            pending_label = f"pending={pending_for_eta}"
            if sample_pending_start is not None:
                pending_for_eta = max(
                    sample_pending_start - successful_this_call,
                    0,
                )
                pending_label = f"sample_pending~={pending_for_eta}"
            eta_seconds = pending_for_eta / rate if rate else 0.0
            print(
                f"embedded={counts['embedded_chunks']}/{counts['total_chunks']} "
                f"{pending_label} failures={failures_total} "
                f"rate={rate:.2f}/s eta={eta_seconds / 3600:.2f}h "
                f"qpm={rate_limiter.effective_qpm:.0f}",
                flush=True,
            )
            if failures and not successes:
                break

    scope: dict[str, Any] = {"mode": "full"}
    with engine.begin() as connection:
        counts = _run_counts(connection, run_id, chunk_run_id)
        provider_telemetry = telemetry.snapshot()
        counts["provider_attempts"] = attempts
        counts["provider_failures"] = failures_total
        counts["provider_http_requests"] = prior["provider_http_requests"] + int(
            provider_telemetry["http_requests"]
        )
        counts["provider_retries"] = prior["provider_retries"] + int(provider_telemetry["retries"])
        completed = counts["pending_chunks"] == 0
        status = "completed" if completed else "partial"
        if sample_per_stratum is not None:
            sample_counts = _sample_counts(
                connection,
                run_id=run_id,
                chunk_run_id=chunk_run_id,
                sample_per_stratum=sample_per_stratum,
                sample_seed=sample_seed,
            )
            scope = {
                "mode": "stratified_sample",
                "per_stratum": sample_per_stratum,
                "seed": sample_seed,
                **sample_counts,
                "sample_completed": sample_counts["sample_pending"] == 0,
            }
        _update_run(
            connection,
            run_id=run_id,
            counts=counts,
            status=status,
            last_error=last_error,
            activate=completed,
        )
    return LoadResult(
        run_id=run_id,
        counts=counts,
        status=status,
        call_counts={
            "attempted": processed_this_call,
            "succeeded": successful_this_call,
            "failed": processed_this_call - successful_this_call,
        },
        failure_categories=dict(sorted(failure_categories.items())),
        failure_examples=failure_examples,
        provider_telemetry=provider_telemetry,
        rate_limit={
            "configured_qpm": requests_per_minute,
            "observed_limit_qpm": rate_limiter.observed_limit_qpm,
            "effective_qpm": rate_limiter.effective_qpm,
        },
        scope=scope,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    add_runtime_arguments(parser)
    parser.add_argument("--api-key-env", default="CLOVASTUDIO_API_KEY")
    parser.add_argument("--endpoint", default=EmbeddingConfig().endpoint)
    parser.add_argument(
        "--input-version",
        choices=(
            EMBEDDING_INPUT_VERSION_V1,
            EMBEDDING_INPUT_VERSION_V2,
            EMBEDDING_INPUT_VERSION_V3,
        ),
        default=EMBEDDING_INPUT_VERSION,
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--page-size", type=int, default=64)
    parser.add_argument(
        "--requests-per-minute",
        type=int,
        default=DEFAULT_TARGET_QPM,
        help=(
            "hard QPM ceiling; starts at 54 QPM and adapts to 90%% of the "
            "provider-advertised limit (default: %(default)s)"
        ),
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--sample-per-stratum",
        type=int,
        help=(
            "deterministically select up to N chunks per "
            "(company, document group, chunk type) stratum"
        ),
    )
    parser.add_argument(
        "--sample-seed",
        default="embedding-context-eval-20260830",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        runtime = runtime_from_args(args)
    except ValueError as exc:
        parser.error(str(exc))
    args.database_url = runtime.database_url
    if args.workers <= 0 or args.page_size <= 0 or args.requests_per_minute <= 0:
        parser.error("--workers, --page-size, and --requests-per-minute must be positive")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    if args.sample_per_stratum is not None and args.sample_per_stratum <= 0:
        parser.error("--sample-per-stratum must be positive")
    if not args.sample_seed.strip():
        parser.error("--sample-seed must not be empty")
    _assert_perf_database(args.database_url)

    config = EmbeddingConfig(
        endpoint=args.endpoint.rstrip("/"),
        input_version=args.input_version,
    )
    engine = get_engine(args.database_url)
    with engine.begin() as connection:
        chunk_run = _active_chunk_run(connection)
        run_id = embedding_run_id(str(chunk_run["chunk_run_id"]), config)
        existing = connection.execute(
            text("SELECT counts FROM public.embedding_runs WHERE embedding_run_id = :run_id"),
            {"run_id": run_id},
        ).scalar_one_or_none()
        counts = dict(existing or {})
        total = int((chunk_run["counts"] or {}).get("total_chunks", 0))
        embedded = int(counts.get("embedded_chunks", 0))
        sample_counts = None
        if args.sample_per_stratum is not None:
            sample_counts = _sample_counts(
                connection,
                run_id=run_id,
                chunk_run_id=str(chunk_run["chunk_run_id"]),
                sample_per_stratum=args.sample_per_stratum,
                sample_seed=args.sample_seed,
            )

    print("=== retrieval embedding contract ===")
    print(f"chunk run                       {chunk_run['chunk_run_id']}")
    print(f"embedding run                   {run_id}")
    print(f"provider                        {config.provider}")
    print(f"model                           {config.model}")
    print(f"dimensions                      {config.dimensions}")
    print(f"distance metric                 {config.distance_metric}")
    print(f"input version                   {config.input_version}")
    print(f"total chunks                    {total}")
    print(f"already embedded                {embedded}")
    print(f"pending estimate                {max(total - embedded, 0)}")
    print(f"configured QPM ceiling          {args.requests_per_minute}")
    if sample_counts is not None:
        print(f"sample chunks                   {sample_counts['sample_chunks']}")
        print(f"sample pending                  {sample_counts['sample_pending']}")
    if args.dry_run:
        print("provider calls                  0")
        print("database writes                 0")
        return

    api_key = runtime.api_key
    if not api_key:
        raise SystemExit(f"Environment variable {args.api_key_env} is missing")
    result = _load(
        database_url=args.database_url,
        config=config,
        api_key=api_key,
        workers=args.workers,
        page_size=args.page_size,
        limit=args.limit,
        requests_per_minute=args.requests_per_minute,
        sample_per_stratum=args.sample_per_stratum,
        sample_seed=args.sample_seed,
    )
    report = {
        "embedding_run_id": result.run_id,
        "status": result.status,
        "config": {
            "provider": config.provider,
            "model": config.model,
            "dimensions": config.dimensions,
            "distance_metric": config.distance_metric,
            "input_version": config.input_version,
        },
        "counts": result.counts,
        "call_counts": result.call_counts,
        "failure_categories": result.failure_categories,
        "failure_examples": result.failure_examples,
        "provider_telemetry": result.provider_telemetry,
        "rate_limit": result.rate_limit,
        "scope": result.scope,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_bytes(orjson.dumps(report, option=orjson.OPT_INDENT_2) + b"\n")
    print("\n=== retrieval embedding load ===")
    print(f"embedding run                  {result.run_id}")
    for key, value in result.counts.items():
        print(f"{key:32} {value}")
    print(f"call attempted                 {result.call_counts['attempted']}")
    print(f"call succeeded                 {result.call_counts['succeeded']}")
    print(f"call failed                    {result.call_counts['failed']}")
    print(f"provider HTTP requests         {result.provider_telemetry['http_requests']}")
    print(f"provider retries               {result.provider_telemetry['retries']}")
    print(f"failure categories             {result.failure_categories}")
    print(f"observed provider QPM          {result.rate_limit['observed_limit_qpm']}")
    print(f"effective QPM                  {result.rate_limit['effective_qpm']}")
    print(f"scope                          {result.scope}")
    print(f"status                         {result.status}")


if __name__ == "__main__":
    main()
