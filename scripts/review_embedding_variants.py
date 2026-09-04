#!/usr/bin/env python3
"""Produce a finite 24-question side-by-side embedding retrieval review."""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from typing import Any

import evaluate_embedding_variants as proxy
import orjson
from sqlalchemy import text

from disclosure_agent.retrieval.embeddings import (
    DEFAULT_TARGET_QPM,
    EMBEDDING_INPUT_VERSION_V1,
    EMBEDDING_INPUT_VERSION_V2,
    SUPPORTED_INPUT_VERSIONS,
)
from disclosure_agent.retrieval.evaluation import (
    BenchmarkCase,
    build_benchmark_cases,
    score_ranking,
    select_balanced_targets,
)
from disclosure_agent.retrieval.runtime import add_runtime_arguments, runtime_from_args
from disclosure_agent.storage.database import get_engine

REVIEW_QUESTIONS = 24
REVIEW_SEED = "embedding-manual-review-20260830"

DETAILS_SQL = """
    SELECT
        c.chunk_id,
        c.filing_id,
        c.document_id,
        c.section_id,
        c.source_table_id,
        c.document_group,
        c.chunk_type,
        c.heading_path,
        c.content,
        f.corp_code,
        sc.listed_name,
        sc.corp_name,
        f.report_name,
        f.receipt_number,
        f.receipt_date,
        coalesce(f.document_subtype, '') AS document_subtype,
        f.is_correction
    FROM public.retrieval_chunks c
    JOIN public.source_filings f ON f.filing_id = c.filing_id
    JOIN public.source_companies sc ON sc.corp_code = f.corp_code
    WHERE c.chunk_run_id = :chunk_run_id
      AND c.chunk_id = ANY(CAST(:chunk_ids AS varchar[]))
"""


def _review_cases(
    sample_rows: list[dict[str, Any]],
) -> list[BenchmarkCase]:
    targets = select_balanced_targets(
        sample_rows,
        limit=REVIEW_QUESTIONS,
        seed=REVIEW_SEED,
    )
    all_cases = build_benchmark_cases(sample_rows, targets)
    by_target: dict[str, dict[str, BenchmarkCase]] = defaultdict(dict)
    for case in all_cases:
        by_target[case.target_chunk_id][case.suite] = case

    lane_offsets: dict[tuple[str, str], int] = defaultdict(int)
    modes = (
        "company_context",
        "company_context",
        "topic_filtered",
        "content_anchor",
    )
    selected: list[BenchmarkCase] = []
    for target in targets:
        lane = (str(target["document_group"]), str(target["chunk_type"]))
        mode = modes[lane_offsets[lane] % len(modes)]
        lane_offsets[lane] += 1
        choices = by_target[str(target["chunk_id"])]
        selected.append(choices.get(mode) or choices["topic_filtered"])
    if len(selected) != REVIEW_QUESTIONS:
        raise RuntimeError(f"Expected {REVIEW_QUESTIONS} review questions, got {len(selected)}")
    return selected


def _details(
    connection: Any,
    *,
    chunk_run_id: str,
    chunk_ids: set[str],
) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        text(DETAILS_SQL),
        {
            "chunk_run_id": chunk_run_id,
            "chunk_ids": sorted(chunk_ids),
        },
    ).mappings()
    return {str(row["chunk_id"]): dict(row) for row in rows}


def _automatic_verdict(
    left: dict[str, Any],
    right: dict[str, Any],
) -> str:
    left_rank = int(left["first_relevant_rank"] or 6)
    right_rank = int(right["first_relevant_rank"] or 6)
    if right_rank + 1 < left_rank:
        return "right_proxy_win"
    if left_rank + 1 < right_rank:
        return "left_proxy_win"
    return "proxy_tie"


def _markdown(
    *,
    chunk_run_id: str,
    run_ids: dict[str, str],
    cases: list[BenchmarkCase],
    outcomes: dict[str, list[dict[str, Any]]],
    hits: dict[str, dict[str, list[dict[str, Any]]]],
    details: dict[str, dict[str, Any]],
    query_telemetry: dict[str, Any],
    versions: tuple[str, str],
) -> str:
    left_version, right_version = versions
    outcome_by_version = {
        version: {row["case_id"]: row for row in outcomes[version]} for version in versions
    }
    lines = [
        f"# Embedding {left_version} / {right_version} manual review",
        "",
        "이 문서는 전체 임베딩 전 마지막 24문항 판정표입니다. 자동 proxy 정답은 참고만 하고,",
        "실제 질문에 답할 근거가 Top-5 안에 있는지 원문 preview와 provenance로 확인합니다.",
        "",
        "## Contract",
        "",
        f"- Chunk run: `{chunk_run_id}`",
        f"- Left run ({left_version}): `{run_ids[left_version]}`",
        f"- Right run ({right_version}): `{run_ids[right_version]}`",
        f"- Questions: {len(cases)}",
        f"- Unique query calls: {query_telemetry['unique_queries']}",
        f"- Provider failures: {sum(query_telemetry['transport_errors'].values())}",
        "- DB writes: 0",
        "",
        "## Decision rule",
        "",
        "- 잘못된 회사 Top-1은 허용하지 않습니다.",
        "- 실제 답의 근거가 Top-5에 없으면 critical miss입니다.",
        "- 오른쪽 후보의 명백한 패배가 2건 이하면 오른쪽 후보를 승인합니다.",
        "- `Relevant`는 자동 proxy 표시이며 최종 사람 판정을 대신하지 않습니다.",
        "",
        "## Summary",
        "",
        "| # | Lane | Suite | Query | Left rank | Right rank | Proxy | Human |",
        "|---:|---|---|---|---:|---:|---|---|",
    ]
    for index, case in enumerate(cases, 1):
        left = outcome_by_version[left_version][case.case_id]
        right = outcome_by_version[right_version][case.case_id]
        lines.append(
            "| "
            f"{index} | {case.document_group}/{case.chunk_type} | {case.suite} | "
            f"{_escape(case.query)} | {_rank(left)} | {_rank(right)} | "
            f"{_automatic_verdict(left, right)} | TODO |"
        )

    lines.extend(["", "## Top-5 evidence", ""])
    for index, case in enumerate(cases, 1):
        filter_value = case.corp_code if case.filter_corp_code else "none"
        lines.extend(
            [
                f"### Q{index:02d}. {_escape(case.query)}",
                "",
                f"- Lane: `{case.document_group}/{case.chunk_type}`",
                f"- Suite: `{case.suite}`",
                f"- Expected corp code: `{case.corp_code}`",
                f"- Applied corp filter: `{filter_value}`",
                f"- Target chunk: `{case.target_chunk_id}`",
                "",
            ]
        )
        for version in versions:
            outcome = outcome_by_version[version][case.case_id]
            lines.extend(
                [
                    f"#### {version}",
                    "",
                    f"Proxy first relevant rank: `{_rank(outcome)}`",
                    "",
                    "| Rank | Sim | Relevant | Company | Report | Type | Heading / preview |",
                    "|---:|---:|---|---|---|---|---|",
                ]
            )
            relevant = set(case.relevant_chunk_ids)
            for rank, hit in enumerate(hits[version][case.case_id], 1):
                row = details[str(hit["chunk_id"])]
                company = row.get("listed_name") or row.get("corp_name") or ""
                heading = " > ".join(str(value) for value in row.get("heading_path") or [])
                preview = " ".join(str(row["content"]).split())[:240]
                context = f"{heading} — {preview}" if heading else preview
                lines.append(
                    "| "
                    f"{rank} | {float(hit['similarity']):.4f} | "
                    f"{'Y' if str(hit['chunk_id']) in relevant else 'N'} | "
                    f"{_escape(str(company))} | {_escape(str(row['report_name']))} | "
                    f"{row['chunk_type']} | {_escape(context)} |"
                )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _rank(outcome: dict[str, Any]) -> str:
    value = outcome.get("first_relevant_rank")
    return str(value) if value is not None else ">5"


def _escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    add_runtime_arguments(parser)
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
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--requests-per-minute",
        type=int,
        default=DEFAULT_TARGET_QPM,
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("data/quality/embedding-manual-review.md"),
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        default=Path("data/quality/embedding-manual-review.json"),
    )
    args = parser.parse_args()
    try:
        runtime = runtime_from_args(args)
    except ValueError as exc:
        parser.error(str(exc))
    args.database_url = runtime.database_url
    if (
        min(
            args.sample_per_stratum,
            args.workers,
            args.requests_per_minute,
            args.top_k,
        )
        <= 0
    ):
        parser.error("numeric arguments must be positive")
    if args.top_k < 5:
        parser.error("--top-k must be at least 5")
    if args.left_input_version == args.right_input_version:
        parser.error("left and right input versions must be different")
    proxy._assert_perf_database(args.database_url)
    versions = (args.left_input_version, args.right_input_version)

    engine = get_engine(args.database_url)
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        chunk_run_id = proxy._active_chunk_run(connection)
        sample_rows = proxy._sample_rows(
            connection,
            chunk_run_id=chunk_run_id,
            sample_per_stratum=args.sample_per_stratum,
            sample_seed=args.sample_seed,
        )
        sample_ids = [str(row["chunk_id"]) for row in sample_rows]
        run_ids, coverage = proxy._validate_runs(
            connection,
            chunk_run_id=chunk_run_id,
            sample_ids=sample_ids,
            versions=versions,
        )
    cases = _review_cases(sample_rows)
    unique_queries = sorted({case.query for case in cases})

    print("=== embedding manual review contract ===")
    print(f"chunk run                       {chunk_run_id}")
    print(f"sample chunks                   {len(sample_ids)}")
    print(f"review questions                {len(cases)}")
    print(f"unique query calls              {len(unique_queries)}")
    for version in versions:
        print(f"{version} coverage {coverage[version]['current_chunks']}/{len(sample_ids)}")
    if args.dry_run:
        print("provider calls                  0")
        print("database writes                 0")
        return

    api_key = runtime.api_key
    if not api_key:
        raise SystemExit(f"Environment variable {args.api_key_env} is missing")
    embeddings, query_telemetry = proxy._embed_queries(
        unique_queries,
        api_key=api_key,
        workers=args.workers,
        requests_per_minute=args.requests_per_minute,
    )

    outcomes: dict[str, list[dict[str, Any]]] = {version: [] for version in versions}
    all_hits: dict[str, dict[str, list[dict[str, Any]]]] = {version: {} for version in versions}
    hit_ids: set[str] = set()
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        for version, run_id in run_ids.items():
            for case in cases:
                values = proxy._search(
                    connection,
                    run_id=run_id,
                    sample_ids=sample_ids,
                    case=case,
                    embedding=embeddings[case.query],
                    top_k=args.top_k,
                )
                all_hits[version][case.case_id] = values
                hit_ids.update(str(row["chunk_id"]) for row in values)
                outcomes[version].append(score_ranking(case, values))
        details = _details(
            connection,
            chunk_run_id=chunk_run_id,
            chunk_ids=hit_ids,
        )

    markdown = _markdown(
        chunk_run_id=chunk_run_id,
        run_ids=run_ids,
        cases=cases,
        outcomes=outcomes,
        hits=all_hits,
        details=details,
        query_telemetry=query_telemetry,
        versions=versions,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(markdown, encoding="utf-8")
    json_payload = {
        "contract": {
            "review_version": "embedding-manual-review-v1",
            "chunk_run_id": chunk_run_id,
            "questions": len(cases),
            "sample_chunks": len(sample_ids),
            "run_ids": run_ids,
            "coverage": coverage,
            "left_input_version": versions[0],
            "right_input_version": versions[1],
        },
        "query_embedding": query_telemetry,
        "cases": [
            {
                "case_id": case.case_id,
                "suite": case.suite,
                "query": case.query,
                "corp_code": case.corp_code,
                "document_group": case.document_group,
                "chunk_type": case.chunk_type,
                "filter_corp_code": case.filter_corp_code,
                "outcomes": {
                    version: next(
                        row for row in outcomes[version] if row["case_id"] == case.case_id
                    )
                    for version in outcomes
                },
            }
            for case in cases
        ],
    }
    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_bytes(orjson.dumps(json_payload, option=orjson.OPT_INDENT_2) + b"\n")
    print("\n=== embedding manual review generated ===")
    print(f"markdown report                 {args.report}")
    print(f"json report                     {args.json_report}")
    print(f"query calls                     {query_telemetry['unique_queries']}")
    print(f"provider retries                {query_telemetry['retries']}")
    print("database writes                 0")
    print("status                          ready for final review")


if __name__ == "__main__":
    main()
