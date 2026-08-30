#!/usr/bin/env python3
"""Resumably embed the active retrieval chunks with CLOVA Studio bge-m3."""

from __future__ import annotations

import argparse
import os
import time
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson
from sqlalchemy import Connection, text
from sqlalchemy.engine import make_url

from disclosure_agent.retrieval.embeddings import (
    ClovaStudioEmbeddingClient,
    EmbeddingConfig,
    EmbeddingResult,
    compose_embedding_input,
    embedding_input_sha256,
    embedding_run_id,
    vector_literal,
)
from disclosure_agent.storage.database import get_engine

PENDING_SQL = """
    SELECT
        c.chunk_id,
        c.chunk_run_id,
        c.content,
        c.content_sha256,
        c.heading_path
    FROM public.retrieval_chunks c
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


def _assert_perf_database(database_url: str) -> None:
    url = make_url(database_url)
    if url.database != "disclosure_perf" or url.port != 55432:
        raise SystemExit(
            "Embedding loader is restricted to disclosure_perf on localhost:55432; "
            f"got database={url.database!r}, port={url.port!r}"
        )


def _active_chunk_run(connection: Connection) -> dict[str, Any]:
    row = connection.execute(
        text(
            """
            SELECT chunk_run_id, counts
            FROM public.retrieval_chunk_runs
            WHERE is_active AND status = 'completed'
            """
        )
    ).mappings().one_or_none()
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
    row = connection.execute(
        text("SELECT * FROM public.embedding_runs WHERE embedding_run_id = :run_id"),
        {"run_id": run_id},
    ).mappings().one()
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
        key: (row[key], expected)
        for key, expected in contract.items()
        if row[key] != expected
    }
    if mismatches:
        raise RuntimeError(f"Existing embedding run contract mismatch: {mismatches}")
    return dict(row)


def _run_counts(connection: Connection, run_id: str, chunk_run_id: str) -> dict[str, int]:
    row = connection.execute(
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
    ).mappings().one()
    values = {key: int(value or 0) for key, value in row.items()}
    values["pending_chunks"] = (
        values["total_chunks"]
        - values["embedded_chunks"]
        + values["stale_embeddings"]
    )
    return values


def _tasks(
    connection: Connection,
    *,
    run_id: str,
    chunk_run_id: str,
    after_chunk_id: str,
    page_size: int,
) -> list[EmbeddingTask]:
    rows = connection.execute(
        text(PENDING_SQL),
        {
            "embedding_run_id": run_id,
            "chunk_run_id": chunk_run_id,
            "after_chunk_id": after_chunk_id,
            "page_size": page_size,
        },
    ).mappings()
    tasks: list[EmbeddingTask] = []
    for row in rows:
        input_text = compose_embedding_input(
            str(row["content"]),
            list(row["heading_path"] or []),
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
) -> tuple[str, dict[str, int], str]:
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

    attempts = prior["provider_attempts"]
    failures_total = prior["provider_failures"]
    processed_this_call = 0
    successful_this_call = 0
    last_error: str | None = None
    after_chunk_id = ""
    started = time.perf_counter()

    with ClovaStudioEmbeddingClient(api_key, config) as client:
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

            with engine.begin() as connection:
                _save_page(connection, run_id, successes)
                counts = _run_counts(connection, run_id, chunk_run_id)
                counts["provider_attempts"] = attempts
                counts["provider_failures"] = failures_total
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
            eta_seconds = counts["pending_chunks"] / rate if rate else 0.0
            print(
                f"embedded={counts['embedded_chunks']}/{counts['total_chunks']} "
                f"pending={counts['pending_chunks']} failures={failures_total} "
                f"rate={rate:.2f}/s eta={eta_seconds / 3600:.2f}h",
                flush=True,
            )
            if failures and not successes:
                break

    with engine.begin() as connection:
        counts = _run_counts(connection, run_id, chunk_run_id)
        counts["provider_attempts"] = attempts
        counts["provider_failures"] = failures_total
        completed = counts["pending_chunks"] == 0
        status = "completed" if completed else "partial"
        _update_run(
            connection,
            run_id=run_id,
            counts=counts,
            status=status,
            last_error=last_error,
            activate=completed,
        )
    return run_id, counts, status


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--api-key-env", default="CLOVASTUDIO_API_KEY")
    parser.add_argument("--endpoint", default=EmbeddingConfig().endpoint)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--page-size", type=int, default=64)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.workers <= 0 or args.page_size <= 0:
        parser.error("--workers and --page-size must be positive")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    _assert_perf_database(args.database_url)

    config = EmbeddingConfig(endpoint=args.endpoint.rstrip("/"))
    engine = get_engine(args.database_url)
    with engine.begin() as connection:
        chunk_run = _active_chunk_run(connection)
        run_id = embedding_run_id(str(chunk_run["chunk_run_id"]), config)
        existing = connection.execute(
            text(
                "SELECT counts FROM public.embedding_runs "
                "WHERE embedding_run_id = :run_id"
            ),
            {"run_id": run_id},
        ).scalar_one_or_none()
        counts = dict(existing or {})
        total = int((chunk_run["counts"] or {}).get("total_chunks", 0))
        embedded = int(counts.get("embedded_chunks", 0))

    print("=== retrieval embedding contract ===")
    print(f"chunk run                       {chunk_run['chunk_run_id']}")
    print(f"embedding run                   {run_id}")
    print(f"provider                        {config.provider}")
    print(f"model                           {config.model}")
    print(f"dimensions                      {config.dimensions}")
    print(f"distance metric                 {config.distance_metric}")
    print(f"total chunks                    {total}")
    print(f"already embedded                {embedded}")
    print(f"pending estimate                {max(total - embedded, 0)}")
    if args.dry_run:
        print("provider calls                  0")
        print("database writes                 0")
        return

    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        raise SystemExit(f"Environment variable {args.api_key_env} is missing")
    run_id, counts, status = _load(
        database_url=args.database_url,
        config=config,
        api_key=api_key,
        workers=args.workers,
        page_size=args.page_size,
        limit=args.limit,
    )
    report = {
        "embedding_run_id": run_id,
        "status": status,
        "config": {
            "provider": config.provider,
            "model": config.model,
            "dimensions": config.dimensions,
            "distance_metric": config.distance_metric,
            "input_version": config.input_version,
        },
        "counts": counts,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_bytes(orjson.dumps(report, option=orjson.OPT_INDENT_2) + b"\n")
    print("\n=== retrieval embedding load ===")
    print(f"embedding run                  {run_id}")
    for key, value in counts.items():
        print(f"{key:32} {value}")
    print(f"status                         {status}")


if __name__ == "__main__":
    main()
