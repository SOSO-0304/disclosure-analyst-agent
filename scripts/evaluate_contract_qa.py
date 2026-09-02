#!/usr/bin/env python3
"""Forty real questions against existing vectors; independent gold, read-only DB."""

from __future__ import annotations

import argparse
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import orjson
from sqlalchemy import text

from disclosure_agent.retrieval.contract_answers import contract_findings
from disclosure_agent.retrieval.contract_query import (
    PLANNER_VERSION,
    plan_contract_query,
    stopped_contract_answer,
)
from disclosure_agent.retrieval.embeddings import (
    ClovaStudioEmbeddingClient,
    EmbeddingConfig,
    EmbeddingTelemetry,
    GlobalRateLimiter,
    vector_literal,
)
from disclosure_agent.retrieval.hybrid import resolve_company, validate_dates
from disclosure_agent.retrieval.qa_benchmark import (
    load_benchmark,
    score_case,
    summarize,
    verify_gold_sources,
)
from disclosure_agent.retrieval.runtime import add_runtime_arguments, runtime_from_args
from disclosure_agent.retrieval.search import company_catalog, completed_run, retrieve
from disclosure_agent.storage.database import get_engine

ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = {
    version: ROOT / f"data/benchmarks/supply-contract-qa-{version}.json" for version in ("v1", "v2")
}


@contextmanager
def readonly(engine):
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(text("SET LOCAL statement_timeout = '20s'"))
        yield connection


def request_filters(request: dict[str, Any], companies: list[dict[str, Any]]) -> dict[str, Any]:
    """Same inputs/defaults as search_retrieval.py; never accept expected/gold data."""
    company = resolve_company(
        companies,
        request["query"],
        company=request.get("company"),
        corp_code=request.get("corp_code"),
        auto=True,
    )
    start, end = validate_dates(request.get("date_from"), request.get("date_to"))
    return {
        "corp_code": str(company["corp_code"]) if company else None,
        "date_from": start,
        "date_to": end,
        "document_group": None,
        "chunk_type": None,
        "corrections": request.get("corrections", "all"),
    }


def preflight(connection, benchmark):
    run = completed_run(connection, benchmark["embedding_run_id"])
    if run["chunk_run_id"] != benchmark["chunk_run_id"]:
        raise ValueError("Frozen chunk run does not match")
    companies = company_catalog(connection)
    rows = connection.execute(
        text("""
        SELECT f.receipt_number, f.corp_code, f.receipt_date, f.is_correction,
            f.filing_id, f.document_subtype,
            EXISTS (
                SELECT 1 FROM public.retrieval_chunks c
                JOIN public.retrieval_embeddings e
                  ON e.chunk_id = c.chunk_id AND e.chunk_run_id = c.chunk_run_id
                WHERE c.filing_id = f.filing_id AND c.chunk_run_id = :chunk_run_id
                  AND e.embedding_run_id = :embedding_run_id
                  AND e.chunk_content_sha256 = c.content_sha256
            ) AS has_embedding
        FROM public.source_filings f
        JOIN public.retrieval_chunk_runs cr ON cr.source_load_run_id = f.load_run_id
        WHERE cr.chunk_run_id = :chunk_run_id AND cr.is_active AND cr.status = 'completed'
          AND f.receipt_number = ANY(CAST(:receipts AS varchar[]))
    """),
        {
            "chunk_run_id": run["chunk_run_id"],
            "embedding_run_id": run["embedding_run_id"],
            "receipts": list(benchmark["records"]),
        },
    ).mappings()
    metadata = {str(row["receipt_number"]): dict(row) for row in rows}
    errors = []
    for receipt, record in benchmark["records"].items():
        row = metadata.get(receipt)
        if row is None or not row["has_embedding"]:
            errors.append({"receipt": receipt, "reason": "target_missing_from_frozen_corpus"})
        elif (
            str(row["receipt_date"]) != record["receipt_date"]
            or row["is_correction"] != record["is_correction"]
            or row["filing_id"] != f"exchange_{receipt}"
        ):
            errors.append({"receipt": receipt, "reason": "target_metadata_mismatch"})
    # Prove the negative cases within their explicit UI scope, not by treating a
    # top-k miss as evidence that the original corpus contains no such filing.
    for case in benchmark["cases"]:
        if case["expected"]["response"] != "no_results":
            continue
        filters = request_filters(case["request"], companies)
        if filters["corp_code"] is None or not filters["date_from"] or not filters["date_to"]:
            errors.append({"case": case["id"], "reason": "empty_scope_not_resolved"})
            continue
        exists = connection.execute(
            text("""
            SELECT EXISTS (
                SELECT 1 FROM public.source_filings f
                JOIN public.retrieval_chunk_runs cr ON cr.source_load_run_id = f.load_run_id
                WHERE cr.chunk_run_id = :chunk_run_id AND f.corp_code = :corp_code
                  AND f.receipt_date >= :date_from AND f.receipt_date <= :date_to
            )
        """),
            {**filters, "chunk_run_id": run["chunk_run_id"]},
        ).scalar_one()
        if exists:
            errors.append({"case": case["id"], "reason": "negative_scope_not_empty"})
    return run, companies, metadata, errors


def observe_request(engine, run, client, companies, request):
    """Production search + field extractor. Gold cannot enter this function."""
    started = time.monotonic()
    plan = plan_contract_query(request["query"], request_filters(request, companies))
    filters = plan["filters"]
    if plan["status"] != "ready":
        return {
            "kind": plan["status"],
            "reason": plan["reason"],
            "reason_code": plan["reason_code"],
            "answer": stopped_contract_answer(plan, run),
            "hits": [],
            "filters": {
                k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in filters.items()
            },
            "query_tokens": 0,
            "query_provider_calls": 0,
            "timing_seconds": {"total": time.monotonic() - started},
        }
    api_start = time.monotonic()
    embedded = client.embed(request["query"])
    api_seconds = time.monotonic() - api_start
    with readonly(engine) as connection:
        payload = retrieve(
            connection,
            run=run,
            query=request["query"],
            vector=vector_literal(embedded.vector),
            mode="dense",
            top_k=5,
            candidate_limit=100,
            max_per_filing=1,
            company_cap=2,
            exact=False,
            filters=filters,
            quantity_probes=plan["quantity_probes"],
        )
        extraction_start = time.monotonic()
        answer = contract_findings(connection, run=run, hits=payload["results"], filters=filters)
        answer["schema_version"] = "retrieval-contract-fields-v2"
        answer["query_plan"] = plan
        extraction_seconds = time.monotonic() - extraction_start
    return {
        "kind": "fields",
        "answer": answer,
        "filters": {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in filters.items()},
        "hits": [
            {k: row[k] for k in ("receipt_number", "receipt_date", "corp_code", "chunk_id")}
            for row in payload["results"]
        ],
        "query_tokens": embedded.input_tokens,
        "query_provider_calls": 1,
        "quantity_diagnostics": payload.get("quantity_diagnostics", {}),
        "dense_strategy": payload["dense_strategy"],
        "timing_seconds": {
            "query_api": api_seconds,
            "extraction": extraction_seconds,
            "search_stages": payload["timing_seconds"],
            "total": time.monotonic() - started,
        },
    }


def safe_error(exc: Exception) -> dict[str, str]:
    # Exception strings may contain connection URLs, HTTP headers or SQL parameters.
    return {"kind": "error", "error_type": type(exc).__name__}


def checkpoint(stream, report, telemetry):
    report["summary"] = summarize(report["results"])
    changed = {item["case_id"] for item in report.get("question_changes", [])}
    if changed:
        report["comparison"] = {
            "excluded_changed_question_ids": sorted(changed),
            "unchanged_questions": summarize(
                [r for r in report["results"] if r["id"] not in changed], planned=40 - len(changed)
            ),
            "note": (
                "Compare unchanged questions with v1; changed P3 is not a direct before/after pair."
            ),
        }
    report["provider"] = telemetry.snapshot()
    report["query_tokens"] = sum(r["observed"].get("query_tokens") or 0 for r in report["results"])
    stream.seek(0)
    stream.write(orjson.dumps(report, option=orjson.OPT_INDENT_2))
    stream.truncate()
    stream.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_runtime_arguments(parser)
    parser.add_argument("--api-key-env", default="CLOVASTUDIO_API_KEY")
    parser.add_argument(
        "--dry-run", action="store_true", help="Verify 40 questions and raw gold only; no DB/API"
    )
    parser.add_argument("--report", type=Path, help="New JSON file; required for a live run")
    parser.add_argument(
        "--limit", type=int, default=40, help="1..40; a partial run never passes the full gate"
    )
    parser.add_argument("--requests-per-minute", type=int, default=480)
    parser.add_argument("--benchmark-version", choices=("v1", "v2"), default="v2")
    args = parser.parse_args()
    if not 1 <= args.limit <= 40 or not 1 <= args.requests_per_minute <= 480:
        parser.error("Require limit 1..40 and requests-per-minute 1..480")
    if args.report and args.report.exists():
        parser.error("Report already exists; choose a new filename")
    benchmark, digest = load_benchmark(BENCHMARKS[args.benchmark_version])
    gold = verify_gold_sources(benchmark, ROOT)
    print("=== contract QA benchmark ===", flush=True)
    print(f"benchmark version              {args.benchmark_version}")
    print(f"questions                      {len(benchmark['cases'])}")
    print(f"independent source records     {gold['records']}")
    print(f"raw gold fields verified       {gold['checked_fields']}")
    if gold["status"] != "verified":
        print(orjson.dumps(gold, option=orjson.OPT_INDENT_2).decode())
        parser.error("Raw gold check blocked; no DB or API calls made")
    if args.dry_run:
        print("provider calls                 0")
        print("database connections           0")
        print("database writes                0")
        print("status                         local_gold_verified (not retrieval-tested)")
        return
    if args.report is None:
        parser.error("A live run requires --report pointing to a new file")
    try:
        runtime = runtime_from_args(args)
    except ValueError as exc:
        parser.error(str(exc))
    if not runtime.api_key:
        parser.error(
            "Set CLOVASTUDIO_API_KEY in .env.perf or the selected key environment variable"
        )
    telemetry = EmbeddingTelemetry()
    report = {
        "schema_version": "contract-qa-report-v2",
        "benchmark_version": args.benchmark_version,
        "question_changes": benchmark.get("changes", []),
        "query_planner_version": PLANNER_VERSION,
        "provider_call_policy": (
            "Only ready requests are embedded; refused/clarification requests make zero calls"
        ),
        "benchmark_sha256": digest,
        "embedding_run_id": benchmark["embedding_run_id"],
        "chunk_run_id": benchmark["chunk_run_id"],
        "source_commit": benchmark["source_commit"],
        "gold_verification": gold,
        "evaluation_scope": (
            "development regression of retrieval and fields; not generated-answer QA or holdout"
        ),
        "database_writes": 0,
        "status": "preflight",
        "results": [],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    try:
        stream = args.report.open("xb+")
    except FileExistsError:
        parser.error("Report appeared before execution; nothing was overwritten")
    with stream:
        checkpoint(stream, report, telemetry)
        try:
            engine = get_engine(runtime.database_url)
            with readonly(engine) as connection:
                run, companies, metadata, errors = preflight(connection, benchmark)
            report["corpus_errors"] = errors
            report["target_metadata"] = metadata
            if errors:
                report["status"] = "blocked"
                print(orjson.dumps(errors, option=orjson.OPT_INDENT_2).decode(), flush=True)
            else:
                config = EmbeddingConfig(
                    provider=str(run["provider"]),
                    model=str(run["model"]),
                    dimensions=int(run["dimensions"]),
                    distance_metric=str(run["distance_metric"]),
                    endpoint=str(run["endpoint"]),
                    input_version=str(run["input_version"]),
                    timeout_seconds=30,
                    max_retries=1,
                )
                limiter = GlobalRateLimiter(target_qpm=args.requests_per_minute)
                report["status"] = "running"
                with ClovaStudioEmbeddingClient(
                    runtime.api_key, config, rate_limiter=limiter, telemetry=telemetry
                ) as client:
                    for index, case in enumerate(benchmark["cases"][: args.limit], 1):
                        try:
                            observed = observe_request(
                                engine, run, client, companies, case["request"]
                            )
                        except Exception as exc:
                            observed = safe_error(exc)
                        result = score_case(case, observed, benchmark["records"], metadata)
                        report["results"].append(result)
                        checkpoint(stream, report, telemetry)
                        print(
                            f"[{index}/40] {case['id']} {'PASS' if result['passed'] else 'FAIL'} "
                            f"{','.join(result['failures'])}",
                            flush=True,
                        )
                        if observed["kind"] == "error":
                            report["status"] = "failed"
                            break  # stop systemic faults instead of making 40 failing calls
                    else:
                        report["status"] = "completed" if args.limit == 40 else "partial"
        except KeyboardInterrupt:
            report["status"] = "interrupted"
        except Exception as exc:
            report["status"] = "failed"
            report["error"] = safe_error(exc)
        finally:
            checkpoint(stream, report, telemetry)
    print(orjson.dumps(report["summary"], option=orjson.OPT_INDENT_2).decode())
    print(f"status                         {report['status']}")
    print(f"report                         {args.report}")
    print("database writes                0")
    if report["status"] != "completed":
        raise SystemExit(2)
    if not report["summary"]["all_development_checks_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
