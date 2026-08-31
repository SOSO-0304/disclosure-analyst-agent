#!/usr/bin/env python3
"""Compare two embedding inputs on the same deterministic chunk sample."""

from __future__ import annotations

import argparse
import os
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import orjson
from sqlalchemy import Connection, text
from sqlalchemy.engine import make_url

from disclosure_agent.retrieval.embeddings import (
    DEFAULT_TARGET_QPM,
    EMBEDDING_INPUT_VERSION_V1,
    EMBEDDING_INPUT_VERSION_V2,
    SUPPORTED_INPUT_VERSIONS,
    ClovaStudioEmbeddingClient,
    EmbeddingConfig,
    EmbeddingResult,
    EmbeddingTelemetry,
    GlobalRateLimiter,
    embedding_run_id,
    vector_literal,
)
from disclosure_agent.retrieval.evaluation import (
    BenchmarkCase,
    aggregate_metrics,
    automated_recommendation,
    build_benchmark_cases,
    metrics_by_dimension,
    metrics_by_suite,
    score_ranking,
    select_balanced_targets,
)
from disclosure_agent.storage.database import get_engine

SAMPLE_SQL = """
    WITH ranked AS (
        SELECT
            c.chunk_id,
            c.chunk_run_id,
            c.content,
            c.content_sha256,
            c.filing_id,
            c.document_id,
            c.section_id,
            c.source_table_id,
            c.document_group,
            c.chunk_type,
            c.heading_path,
            c.metadata,
            f.corp_code,
            sc.corp_name,
            sc.listed_name,
            coalesce(sc.stock_code, '') AS stock_code,
            f.report_name,
            coalesce(f.document_subtype, '') AS document_subtype,
            coalesce(d.title_normalized, '') AS document_title,
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
    SELECT *
    FROM ranked
    WHERE sample_rank <= :sample_per_stratum
    ORDER BY chunk_id
"""

RUNS_SQL = """
    SELECT *
    FROM public.embedding_runs
    WHERE embedding_run_id = ANY(CAST(:run_ids AS varchar[]))
"""

COVERAGE_SQL = """
    SELECT
        e.embedding_run_id,
        count(*)::bigint AS embedded_chunks,
        count(*) FILTER (
            WHERE e.chunk_content_sha256 = c.content_sha256
        )::bigint AS current_chunks
    FROM public.retrieval_embeddings e
    JOIN public.retrieval_chunks c
      ON c.chunk_run_id = e.chunk_run_id
     AND c.chunk_id = e.chunk_id
    WHERE e.embedding_run_id = ANY(CAST(:run_ids AS varchar[]))
      AND e.chunk_id = ANY(CAST(:sample_chunk_ids AS varchar[]))
    GROUP BY e.embedding_run_id
"""

SEARCH_SQL = """
    WITH candidates AS MATERIALIZED (
        SELECT
            e.chunk_id,
            e.embedding,
            f.corp_code,
            c.document_group,
            c.chunk_type
        FROM public.retrieval_embeddings e
        JOIN public.retrieval_chunks c
          ON c.chunk_run_id = e.chunk_run_id
         AND c.chunk_id = e.chunk_id
        JOIN public.source_filings f ON f.filing_id = c.filing_id
        WHERE e.embedding_run_id = :embedding_run_id
          AND e.chunk_id = ANY(CAST(:sample_chunk_ids AS varchar[]))
          AND (
            CAST(:corp_code AS varchar) IS NULL
            OR f.corp_code = CAST(:corp_code AS varchar)
          )
    )
    SELECT
        chunk_id,
        corp_code,
        document_group,
        chunk_type,
        1 - (embedding <=> CAST(:query_vector AS vector)) AS similarity
    FROM candidates
    ORDER BY embedding <=> CAST(:query_vector AS vector), chunk_id
    LIMIT :top_k
"""


def _assert_perf_database(database_url: str) -> None:
    url = make_url(database_url)
    if url.database != "disclosure_perf" or url.port != 55432:
        raise SystemExit(
            "Embedding evaluation is restricted to disclosure_perf on "
            "localhost:55432; "
            f"got database={url.database!r}, port={url.port!r}"
        )


def _active_chunk_run(connection: Connection) -> str:
    value = connection.execute(
        text(
            "SELECT chunk_run_id FROM public.retrieval_chunk_runs "
            "WHERE is_active AND status = 'completed'"
        )
    ).scalar_one_or_none()
    if value is None:
        raise RuntimeError("No completed active retrieval chunk run")
    return str(value)


def _sample_rows(
    connection: Connection,
    *,
    chunk_run_id: str,
    sample_per_stratum: int,
    sample_seed: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        text(SAMPLE_SQL),
        {
            "chunk_run_id": chunk_run_id,
            "sample_per_stratum": sample_per_stratum,
            "sample_seed": sample_seed,
        },
    ).mappings()
    return [dict(row) for row in rows]


def _validate_runs(
    connection: Connection,
    *,
    chunk_run_id: str,
    sample_ids: list[str],
    versions: tuple[str, str] = (
        EMBEDDING_INPUT_VERSION_V1,
        EMBEDDING_INPUT_VERSION_V2,
    ),
) -> tuple[dict[str, str], dict[str, dict[str, int]]]:
    run_ids = {
        version: embedding_run_id(
            chunk_run_id,
            EmbeddingConfig(input_version=version),
        )
        for version in versions
    }
    rows = connection.execute(
        text(RUNS_SQL),
        {"run_ids": list(run_ids.values())},
    ).mappings()
    by_version = {str(row["input_version"]): dict(row) for row in rows}
    missing = sorted(set(run_ids) - set(by_version))
    if missing:
        raise RuntimeError(f"Embedding runs are missing: {missing}")
    for version, row in by_version.items():
        if (
            str(row["chunk_run_id"]) != chunk_run_id
            or str(row["model"]) != "bge-m3"
            or int(row["dimensions"]) != 1024
            or str(row["distance_metric"]) != "cosine"
        ):
            raise RuntimeError(f"Embedding run contract mismatch: {version}")

    coverage_rows = connection.execute(
        text(COVERAGE_SQL),
        {
            "run_ids": list(run_ids.values()),
            "sample_chunk_ids": sample_ids,
        },
    ).mappings()
    coverage_by_run = {
        str(row["embedding_run_id"]): {
            "embedded_chunks": int(row["embedded_chunks"]),
            "current_chunks": int(row["current_chunks"]),
        }
        for row in coverage_rows
    }
    coverage: dict[str, dict[str, int]] = {}
    for version, run_id in run_ids.items():
        values = coverage_by_run.get(
            run_id,
            {"embedded_chunks": 0, "current_chunks": 0},
        )
        coverage[version] = values
        if values["current_chunks"] != len(sample_ids):
            raise RuntimeError(
                f"{version} sample coverage is incomplete: "
                f"{values['current_chunks']}/{len(sample_ids)}"
            )
    return run_ids, coverage


def _embed_queries(
    queries: list[str],
    *,
    api_key: str,
    workers: int,
    requests_per_minute: int,
) -> tuple[dict[str, EmbeddingResult], dict[str, Any]]:
    limiter = GlobalRateLimiter(target_qpm=requests_per_minute)
    telemetry = EmbeddingTelemetry()
    results: dict[str, EmbeddingResult] = {}
    errors: list[tuple[str, Exception]] = []
    with ClovaStudioEmbeddingClient(
        api_key,
        EmbeddingConfig(),
        rate_limiter=limiter,
        telemetry=telemetry,
    ) as client, ThreadPoolExecutor(max_workers=workers) as executor:
        futures: dict[Future[EmbeddingResult], str] = {
            executor.submit(client.embed, query): query for query in queries
        }
        for future in as_completed(futures):
            query = futures[future]
            try:
                results[query] = future.result()
            except Exception as exc:  # noqa: BLE001 - fail the benchmark atomically
                errors.append((query, exc))
    if errors:
        query, error = errors[0]
        raise RuntimeError(
            f"Query embedding failed ({len(errors)} failures), "
            f"example={query!r}: {error}"
        )
    return results, {
        **telemetry.snapshot(),
        "unique_queries": len(queries),
        "input_tokens": sum(result.input_tokens or 0 for result in results.values()),
        "observed_limit_qpm": limiter.observed_limit_qpm,
        "effective_qpm": limiter.effective_qpm,
    }


def _search(
    connection: Connection,
    *,
    run_id: str,
    sample_ids: list[str],
    case: BenchmarkCase,
    embedding: EmbeddingResult,
    top_k: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        text(SEARCH_SQL),
        {
            "embedding_run_id": run_id,
            "sample_chunk_ids": sample_ids,
            "corp_code": case.corp_code if case.filter_corp_code else None,
            "query_vector": vector_literal(embedding.vector),
            "top_k": top_k,
        },
    ).mappings()
    return [dict(row) for row in rows]


def _comparison(
    left: dict[str, dict[str, Any]],
    right: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    comparison: dict[str, Any] = {}
    for suite in sorted(left):
        metrics: dict[str, float] = {}
        for key, value in left[suite].items():
            if key == "cases" or key not in right[suite]:
                continue
            metrics[f"delta_{key}"] = round(float(right[suite][key]) - float(value), 6)
        comparison[suite] = metrics
    return comparison


def _examples(
    cases: list[BenchmarkCase],
    outcomes: dict[str, list[dict[str, Any]]],
    *,
    left_version: str,
    right_version: str,
) -> dict[str, Any]:
    case_by_id = {case.case_id: case for case in cases}
    left = {row["case_id"]: row for row in outcomes[left_version]}
    right = {row["case_id"]: row for row in outcomes[right_version]}
    rows: list[dict[str, Any]] = []
    for case_id, v1 in left.items():
        v2 = right[case_id]
        v1_rank = int(v1["first_relevant_rank"] or 11)
        v2_rank = int(v2["first_relevant_rank"] or 11)
        case = case_by_id[case_id]
        rows.append(
            {
                "case_id": case_id,
                "suite": case.suite,
                "query": case.query,
                "corp_code": case.corp_code,
                "document_group": case.document_group,
                "chunk_type": case.chunk_type,
                "left_version": left_version,
                "right_version": right_version,
                "left_rank": None if v1_rank == 11 else v1_rank,
                "right_rank": None if v2_rank == 11 else v2_rank,
                "rank_gain": v1_rank - v2_rank,
                "left_top_corp": v1["top_corp_code"],
                "right_top_corp": v2["top_corp_code"],
                "left_top_chunk": v1["top_chunk_id"],
                "right_top_chunk": v2["top_chunk_id"],
            }
        )
    return {
        "largest_right_gains": sorted(
            rows,
            key=lambda row: (-int(row["rank_gain"]), str(row["case_id"])),
        )[:10],
        "largest_right_regressions": sorted(
            rows,
            key=lambda row: (int(row["rank_gain"]), str(row["case_id"])),
        )[:10],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--api-key-env", default="CLOVASTUDIO_API_KEY")
    parser.add_argument("--sample-per-stratum", type=int, default=5)
    parser.add_argument(
        "--left-input-version",
        choices=sorted(SUPPORTED_INPUT_VERSIONS),
        default=EMBEDDING_INPUT_VERSION_V1,
    )
    parser.add_argument(
        "--right-input-version",
        choices=sorted(SUPPORTED_INPUT_VERSIONS),
        default=EMBEDDING_INPUT_VERSION_V2,
    )
    parser.add_argument(
        "--sample-seed",
        default="embedding-context-eval-20260830",
    )
    parser.add_argument(
        "--benchmark-seed",
        default="embedding-ranking-eval-20260830",
    )
    parser.add_argument("--max-targets", type=int, default=60)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--requests-per-minute",
        type=int,
        default=DEFAULT_TARGET_QPM,
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if min(
        args.sample_per_stratum,
        args.max_targets,
        args.top_k,
        args.workers,
        args.requests_per_minute,
    ) <= 0:
        parser.error("numeric arguments must be positive")
    if args.top_k < 10:
        parser.error("--top-k must be at least 10 for Recall@10")
    if args.left_input_version == args.right_input_version:
        parser.error("left and right input versions must be different")
    _assert_perf_database(args.database_url)
    versions = (args.left_input_version, args.right_input_version)

    engine = get_engine(args.database_url)
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        chunk_run_id = _active_chunk_run(connection)
        sample_rows = _sample_rows(
            connection,
            chunk_run_id=chunk_run_id,
            sample_per_stratum=args.sample_per_stratum,
            sample_seed=args.sample_seed,
        )
        sample_ids = [str(row["chunk_id"]) for row in sample_rows]
        run_ids, coverage = _validate_runs(
            connection,
            chunk_run_id=chunk_run_id,
            sample_ids=sample_ids,
            versions=versions,
        )
    targets = select_balanced_targets(
        sample_rows,
        limit=args.max_targets,
        seed=args.benchmark_seed,
    )
    cases = build_benchmark_cases(sample_rows, targets)
    unique_queries = sorted({case.query for case in cases})

    print("=== embedding variant evaluation contract ===")
    print(f"chunk run                       {chunk_run_id}")
    print(f"sample chunks                   {len(sample_ids)}")
    print(f"targets                         {len(targets)}")
    print(f"benchmark cases                 {len(cases)}")
    print(f"unique query calls              {len(unique_queries)}")
    for version in versions:
        print(f"{version} run      {run_ids[version]}")
        print(
            f"{version} coverage {coverage[version]['current_chunks']}/"
            f"{len(sample_ids)}"
        )
    if args.dry_run:
        print("provider calls                  0")
        print("database writes                 0")
        return

    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        raise SystemExit(f"Environment variable {args.api_key_env} is missing")
    embeddings, query_telemetry = _embed_queries(
        unique_queries,
        api_key=api_key,
        workers=args.workers,
        requests_per_minute=args.requests_per_minute,
    )

    outcomes: dict[str, list[dict[str, Any]]] = {
        version: [] for version in versions
    }
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        for version, run_id in run_ids.items():
            for case in cases:
                hits = _search(
                    connection,
                    run_id=run_id,
                    sample_ids=sample_ids,
                    case=case,
                    embedding=embeddings[case.query],
                    top_k=args.top_k,
                )
                outcomes[version].append(score_ranking(case, hits))

    metrics = {
        version: {
            "overall": aggregate_metrics(values),
            "by_suite": metrics_by_suite(values),
            "by_document_group": metrics_by_dimension(values, "document_group"),
            "by_chunk_type": metrics_by_dimension(values, "chunk_type"),
        }
        for version, values in outcomes.items()
    }
    suite_metrics = {
        version: values["by_suite"] for version, values in metrics.items()
    }
    decision = automated_recommendation(
        suite_metrics[versions[0]],
        suite_metrics[versions[1]],
    )
    if decision["recommendation"] == "v2_candidate":
        decision["recommendation"] = f"{versions[1]}_candidate"
    elif decision["recommendation"] == "retain_v1_or_revise_v2":
        decision["recommendation"] = (
            f"retain_{versions[0]}_or_revise_{versions[1]}"
        )
    report = {
        "contract": {
            "benchmark_version": "retrieval-proxy-v2",
            "chunk_run_id": chunk_run_id,
            "sample_per_stratum": args.sample_per_stratum,
            "sample_seed": args.sample_seed,
            "benchmark_seed": args.benchmark_seed,
            "sample_chunks": len(sample_ids),
            "targets": len(targets),
            "benchmark_cases": len(cases),
            "top_k": args.top_k,
            "run_ids": run_ids,
            "coverage": coverage,
            "left_input_version": versions[0],
            "right_input_version": versions[1],
        },
        "query_embedding": query_telemetry,
        "metrics": metrics,
        "comparison": _comparison(
            suite_metrics[versions[0]],
            suite_metrics[versions[1]],
        ),
        "decision": decision,
        "examples": _examples(
            cases,
            outcomes,
            left_version=versions[0],
            right_version=versions[1],
        ),
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_bytes(
            orjson.dumps(report, option=orjson.OPT_INDENT_2) + b"\n"
        )

    print("\n=== embedding variant evaluation ===")
    for version in versions:
        print(f"\n{version}")
        for suite, values in metrics[version]["by_suite"].items():
            print(
                f"  {suite:18} cases={values['cases']:3} "
                f"R@1={values['hit_at_1']:.3f} "
                f"R@5={values['hit_at_5']:.3f} "
                f"R@10={values['hit_at_10']:.3f} "
                f"MRR@10={values['reciprocal_rank_at_10']:.3f}"
            )
            if "company_accuracy_at_1" in values:
                print(
                    "  "
                    f"{'company@1':18} "
                    f"{values['company_accuracy_at_1']:.3f} "
                    f"wrong-company@1={values['wrong_company_at_1']:.3f}"
                )
    print(f"\nautomated recommendation       {decision['recommendation']}")
    print(f"proxy checks                    {decision['checks']}")
    print("manual review required          True")
    print("full embedding allowed          False")


if __name__ == "__main__":
    main()
