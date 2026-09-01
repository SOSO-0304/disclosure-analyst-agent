#!/usr/bin/env python3
"""Read-only filtered hybrid search with source citations; no re-embedding."""

from __future__ import annotations

import argparse
import time

import orjson
from sqlalchemy import text

from disclosure_agent.retrieval.embeddings import (
    ClovaStudioEmbeddingClient,
    EmbeddingConfig,
    vector_literal,
)
from disclosure_agent.retrieval.hybrid import resolve_company, validate_dates
from disclosure_agent.retrieval.runtime import add_runtime_arguments, runtime_from_args
from disclosure_agent.retrieval.search import company_catalog, completed_run, retrieve
from disclosure_agent.storage.database import get_engine


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    add_runtime_arguments(parser)
    parser.add_argument("--api-key-env", default="CLOVASTUDIO_API_KEY")
    parser.add_argument("--embedding-run-id")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidates", type=int, default=100)
    parser.add_argument("--mode", choices=("hybrid", "dense", "lexical"), default="hybrid")
    parser.add_argument("--company", help="Exact company name, stock code or corp code")
    parser.add_argument("--corp-code")
    parser.add_argument("--no-auto-company", action="store_true")
    parser.add_argument("--date-from", help="Inclusive filing receipt date, YYYY-MM-DD")
    parser.add_argument("--date-to", help="Inclusive filing receipt date, YYYY-MM-DD")
    parser.add_argument("--document-group", choices=("exchange", "holding", "major", "periodic"))
    parser.add_argument("--chunk-type", choices=("narrative", "table"))
    parser.add_argument("--corrections", choices=("all", "only", "exclude"), default="all")
    parser.add_argument("--max-per-filing", type=int, default=1)
    parser.add_argument(
        "--company-cap", type=int, default=2, help="Soft cap for unfiltered searches"
    )
    parser.add_argument(
        "--exact", action="store_true", help="Use exact dense search even without filters"
    )
    parser.add_argument(
        "--json", action="store_true", help="JSON results list, with source citations"
    )
    args = parser.parse_args()
    if not args.query.strip() or len(args.query) > 10000:
        parser.error("query must contain 1..10000 characters")
    if not 1 <= args.top_k <= 100 or not args.top_k <= args.candidates <= 1000:
        parser.error("Require 1 <= top-k <= 100 and top-k <= candidates <= 1000")
    if min(args.max_per_filing, args.company_cap) <= 0:
        parser.error("selection limits must be positive")
    try:
        runtime = runtime_from_args(args)
        start_date, end_date = validate_dates(args.date_from, args.date_to)
    except ValueError as exc:
        parser.error(str(exc))
    started = time.monotonic()
    engine = get_engine(runtime.database_url)
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(text("SET LOCAL statement_timeout = '60s'"))
        try:
            run = completed_run(connection, args.embedding_run_id)
            company = resolve_company(
                company_catalog(connection),
                args.query,
                company=args.company,
                corp_code=args.corp_code,
                auto=not args.no_auto_company,
            )
        except ValueError as exc:
            parser.error(str(exc))
    vector = None
    query_tokens = 0
    api_seconds = 0.0
    if args.mode != "lexical":
        if not runtime.api_key:
            parser.error(f"Set {args.api_key_env} in .env.perf; lexical mode needs no key")
        config = EmbeddingConfig(
            provider=str(run["provider"]),
            model=str(run["model"]),
            dimensions=int(run["dimensions"]),
            distance_metric=str(run["distance_metric"]),
            endpoint=str(run["endpoint"]),
            input_version=str(run["input_version"]),
        )
        api_start = time.monotonic()
        with ClovaStudioEmbeddingClient(runtime.api_key, config) as client:
            result = client.embed(args.query)
        vector = vector_literal(result.vector)
        query_tokens = result.input_tokens or 0
        api_seconds = time.monotonic() - api_start
    filters = {
        "corp_code": str(company["corp_code"]) if company else None,
        "date_from": start_date,
        "date_to": end_date,
        "document_group": args.document_group,
        "chunk_type": args.chunk_type,
        "corrections": args.corrections,
    }
    search_start = time.monotonic()
    with engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION READ ONLY"))
        connection.execute(text("SET LOCAL statement_timeout = '60s'"))
        payload = retrieve(
            connection,
            run=run,
            query=args.query,
            vector=vector,
            mode=args.mode,
            top_k=args.top_k,
            candidate_limit=args.candidates,
            exact=args.exact,
            max_per_filing=args.max_per_filing,
            company_cap=args.company_cap,
            filters=filters,
        )
    search_seconds = time.monotonic() - search_start
    if args.json:
        print(orjson.dumps(payload["results"], option=orjson.OPT_INDENT_2).decode())
        return
    print("=== retrieval search ===")
    print(f"embedding run     {run['embedding_run_id']}")
    print(f"input version     {run['input_version']}")
    print(f"mode / strategy   {args.mode} / {payload['dense_strategy']}")
    print(
        f"company filter    {company['listed_name'] if company else 'none'} / "
        f"{filters['corp_code']}"
    )
    print(f"receipt date      {start_date or '*'} .. {end_date or '*'} (not contract dates)")
    print(f"corrections       {args.corrections}")
    print(f"query tokens      {query_tokens}")
    print(f"candidate counts  {payload['candidate_counts']}")
    print(
        f"timing seconds    api={api_seconds:.2f} search={search_seconds:.2f} "
        f"total={time.monotonic() - started:.2f}"
    )
    print(f"results           {len(payload['results'])}")
    print("database writes   0")
    for warning in payload["warnings"]:
        print(f"note              {warning}")
    for index, row in enumerate(payload["results"], 1):
        status = "정정공시" if row["is_correction"] else "비정정공시"
        print(f"\n{index}. {row['company_name']} ({row['corp_code']}) / {status}")
        print(f"   RRF      : {row['rrf_score']:.6f} (not a probability)")
        print(f"   ranks    : dense={row['dense_rank']} lexical={row['lexical_rank']}")
        print(f"   report   : {row['report_name']} / {row['receipt_date']}")
        print(f"   source   : {row['citation']['url']}")
        print(f"   chunk    : {row['chunk_id']}")
        print(f"   table    : {row['source_table_id']}")
        print("   content  : " + str(row["content"]).replace("\n", " ")[:600])


if __name__ == "__main__":
    main()
