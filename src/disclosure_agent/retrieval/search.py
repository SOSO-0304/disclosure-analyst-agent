"""Read-only retrieval over an immutable, completed embedding run."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from time import perf_counter
from typing import Any

from sqlalchemy import Connection, text

from disclosure_agent.retrieval.contract_query import quantity_pattern
from disclosure_agent.retrieval.hybrid import (
    citation,
    fuse_rankings,
    lexical_terms,
    select_evidence,
)

JOINS = """
    FROM public.retrieval_embeddings e
    JOIN public.retrieval_chunks c
      ON c.chunk_id = e.chunk_id AND c.chunk_run_id = e.chunk_run_id
    JOIN public.source_filings f ON f.filing_id = c.filing_id
"""


def completed_run(connection: Connection, requested: str | None) -> dict[str, Any]:
    rows = list(
        connection.execute(
            text("""
        SELECT e.* FROM public.embedding_runs e
        JOIN public.retrieval_chunk_runs c ON c.chunk_run_id = e.chunk_run_id
        WHERE c.is_active AND c.status = 'completed' AND e.status = 'completed'
          AND ((CAST(:run_id AS varchar) IS NULL AND e.is_active)
               OR e.embedding_run_id = CAST(:run_id AS varchar))
    """),
            {"run_id": requested},
        ).mappings()
    )
    if len(rows) != 1:
        raise ValueError("Exactly one completed embedding run for the active chunk run is required")
    run = dict(rows[0])
    if (
        run["model"] != "bge-m3"
        or int(run["dimensions"]) != 1024
        or run["distance_metric"] != "cosine"
    ):
        raise ValueError("Embedding run model/dimension/distance contract mismatch")
    return run


def company_catalog(connection: Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            text("""
        SELECT corp_code, stock_code, corp_name, listed_name
        FROM public.source_companies ORDER BY corp_code
    """)
        ).mappings()
    ]


def scope_sql(
    run: Mapping[str, Any],
    *,
    corp_code: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    document_group: str | None = None,
    chunk_type: str | None = None,
    corrections: str = "all",
    document_subtype: str | None = None,
) -> tuple[str, dict[str, Any]]:
    conditions = [
        "e.embedding_run_id = :run_id",
        "e.chunk_run_id = :chunk_run_id",
        "e.chunk_content_sha256 = c.content_sha256",
        "c.content ~ '[0-9A-Za-z가-힣]'",
    ]
    params: dict[str, Any] = {
        "run_id": run["embedding_run_id"],
        "chunk_run_id": run["chunk_run_id"],
    }
    for name, value, column, op in (
        ("corp_code", corp_code, "f.corp_code", "="),
        ("date_from", date_from, "f.receipt_date", ">="),
        ("date_to", date_to, "f.receipt_date", "<="),
        ("document_group", document_group, "c.document_group", "="),
        ("chunk_type", chunk_type, "c.chunk_type", "="),
        ("document_subtype", document_subtype, "f.document_subtype", "="),
    ):
        if value is not None:
            conditions.append(f"{column} {op} :{name}")
            params[name] = value
    if corrections not in {"all", "only", "exclude"}:
        raise ValueError("Invalid correction filter")
    if corrections != "all":
        conditions.append("f.is_correction = :is_correction")
        params["is_correction"] = corrections == "only"
    return " AND ".join(conditions), params


def dense_sql(where: str, *, exact: bool) -> str:
    if exact:
        # Prevent HNSW post-filter underfill on narrow company/date scopes.
        return f"""
            WITH candidates AS MATERIALIZED (
                SELECT e.chunk_id, e.embedding {JOINS} WHERE {where}
            )
            SELECT chunk_id, 1 - (embedding <=> CAST(:vector AS vector)) AS similarity
            FROM candidates
            ORDER BY embedding <=> CAST(:vector AS vector), chunk_id
            LIMIT :candidate_limit
        """
    return f"""
        WITH vector_candidates AS MATERIALIZED (
            SELECT e.chunk_id, e.chunk_run_id, e.embedding_run_id, e.chunk_content_sha256,
                e.embedding <=> CAST(:vector AS vector) AS distance
            FROM public.retrieval_embeddings e
            WHERE e.embedding_run_id = :run_id AND e.chunk_run_id = :chunk_run_id
            ORDER BY e.embedding <=> CAST(:vector AS vector)
            LIMIT :pool_limit
        )
        SELECT e.chunk_id, 1 - e.distance AS similarity
        FROM vector_candidates e
        JOIN public.retrieval_chunks c
          ON c.chunk_id = e.chunk_id AND c.chunk_run_id = e.chunk_run_id
        JOIN public.source_filings f ON f.filing_id = c.filing_id
        WHERE {where}
        ORDER BY e.distance, e.chunk_id
        LIMIT :candidate_limit
    """


def vector_pool_limits(candidate_limit: int) -> tuple[int, ...]:
    """At most three bounded attempts; never an unbounded ANN retry loop."""
    if not 1 <= candidate_limit <= 1000:
        raise ValueError("Candidate limit must be between 1 and 1000")
    first = max(32, 2 * candidate_limit)
    last = min(3200, first * 16)
    return tuple(dict.fromkeys((first, min(last, first * 4), last)))


def configure_dense_search(connection: Connection, *, iterative: bool) -> None:
    connection.execute(text("SET LOCAL hnsw.ef_search = 200"))
    if iterative:
        # pgvector >= 0.8; current verified perf environment is 0.8.6.
        connection.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))


def lexical_sql(where: str, terms: Sequence[str]) -> str:
    if not terms:
        raise ValueError("Lexical query requires terms")
    score = " + ".join(
        f"CASE WHEN strpos(lower(c.content), :term_{i}) > 0 THEN 1 ELSE 0 END"
        for i in range(len(terms))
    )
    return f"""
        WITH lexical AS (
            SELECT e.chunk_id, ({score}) AS lexical_score {JOINS} WHERE {where}
        ), candidates AS MATERIALIZED (
            SELECT chunk_id, lexical_score FROM lexical WHERE lexical_score > 0
            ORDER BY lexical_score DESC, md5(chunk_id), chunk_id
            LIMIT :candidate_limit
        )
        SELECT chunk_id, lexical_score,
            RANK() OVER (ORDER BY lexical_score DESC)
                + (COUNT(*) OVER (PARTITION BY lexical_score) - 1) / 2.0 AS lexical_rank,
            COUNT(*) OVER (PARTITION BY lexical_score) AS lexical_tie_count
        FROM candidates
        ORDER BY lexical_score DESC, md5(chunk_id), chunk_id
    """


def quantity_sql(where: str, probes: Sequence[tuple[str, str]], *, has_vector: bool) -> str:
    """Bounded exact-quantity candidates; every snapshot/scope guard remains applied."""
    if not 1 <= len(probes) <= 3:
        raise ValueError("Require 1..3 quantity probes")
    for probe in probes:
        quantity_pattern(probe)
    normalized = "regexp_replace(lower(c.content), '[[:space:],]', '', 'g')"
    score = " + ".join(
        f"CASE WHEN {normalized} ~ :quantity_{i} THEN 1 ELSE 0 END" for i in range(len(probes))
    )
    distance = "e.embedding <=> CAST(:vector AS vector)" if has_vector else "0.0"
    return f"""
        WITH quantity_candidates AS (
            SELECT e.chunk_id, ({score}) AS quantity_matches, {distance} AS distance
            {JOINS} WHERE {where}
        )
        SELECT chunk_id, quantity_matches, 1 - distance AS similarity
        FROM quantity_candidates WHERE quantity_matches > 0
        ORDER BY quantity_matches DESC, distance, md5(chunk_id), chunk_id
        LIMIT :candidate_limit
    """


def promote_quantity_candidates(ranked, candidates):
    """Literal quantities are stronger than semantic similarity, not hard filters."""
    values = {row["chunk_id"]: dict(row) for row in ranked}
    for position, row in enumerate(candidates, 1):
        item = values.setdefault(
            row["chunk_id"],
            {
                "chunk_id": row["chunk_id"],
                "rrf_score": 0.0,
                "dense_rank": None,
                "lexical_rank": None,
                "similarity": row["similarity"],
                "lexical_score": None,
                "lexical_tie_count": None,
            },
        )
        item.update(quantity_matches=row["quantity_matches"], quantity_rank=position)
    for row in values.values():
        row.setdefault("quantity_matches", 0)
        row.setdefault("quantity_rank", None)
    original_order = {key: index for index, key in enumerate(values)}
    return sorted(
        values.values(),
        key=lambda r: (
            -r["quantity_matches"],
            -r["rrf_score"],
            r["quantity_rank"] or 1_000_000,
            # Preserve original ranking for candidates without a quantity match.
            original_order[r["chunk_id"]],
        ),
    )


def retrieve(
    connection: Connection,
    *,
    run: Mapping[str, Any],
    query: str,
    vector: str | None,
    mode: str = "dense",
    top_k: int = 5,
    candidate_limit: int = 100,
    max_per_filing: int = 1,
    company_cap: int = 2,
    exact: bool = False,
    filters: Mapping[str, Any] | None = None,
    quantity_probes: Sequence[tuple[str, str]] = (),
) -> dict[str, Any]:
    started = perf_counter()
    timings = dict.fromkeys(
        (
            "dense_initial",
            "dense_expansion",
            "dense_fallback",
            "lexical",
            "fusion",
            "hydration",
            "selection",
        ),
        0.0,
    )
    if mode not in {"hybrid", "dense", "lexical"}:
        raise ValueError("Unknown retrieval mode")
    if not 1 <= top_k <= candidate_limit <= 1000:
        raise ValueError("Require 1 <= top-k <= candidate-limit <= 1000")
    if mode != "lexical" and vector is None:
        raise ValueError("Dense search requires a query vector")
    if len(quantity_probes) > 3:
        raise ValueError("At most three quantity probes")
    for probe in quantity_probes:
        quantity_pattern(probe)
    filters = dict(filters or {})
    where, params = scope_sql(run, **filters)
    params.update(vector=vector, candidate_limit=candidate_limit)
    dense: list[dict[str, Any]] = []
    lexical: list[dict[str, Any]] = []
    warnings = []
    terms = lexical_terms(query)
    narrowed = any(v is not None for k, v in filters.items() if k != "corrections") or (
        filters.get("corrections", "all") != "all"
    )
    use_exact = exact or narrowed
    strategy = "not_used"
    pool_attempts = []
    if mode != "lexical":
        strategy = "exact_filtered" if narrowed else "exact" if exact else "vector_first"
        phase_start = perf_counter()
        configure_dense_search(connection, iterative=not use_exact)
        if use_exact:
            dense = [
                dict(r)
                for r in connection.execute(text(dense_sql(where, exact=True)), params).mappings()
            ]
            timings["dense_initial"] = perf_counter() - phase_start
        else:
            for index, pool_limit in enumerate(vector_pool_limits(candidate_limit)):
                if index:
                    phase_start = perf_counter()
                # Re-rank the expanded pool, never append stale/duplicate earlier candidates.
                dense = [
                    dict(r)
                    for r in connection.execute(
                        text(dense_sql(where, exact=False)), {**params, "pool_limit": pool_limit}
                    ).mappings()
                ]
                elapsed = perf_counter() - phase_start
                timings["dense_expansion" if index else "dense_initial"] += elapsed
                pool_attempts.append({"pool_limit": pool_limit, "eligible_candidates": len(dense)})
                if len(dense) >= candidate_limit:
                    break
            if len(pool_attempts) > 1:
                strategy = "vector_first_expanded"
        if not use_exact and len(dense) < candidate_limit:
            phase_start = perf_counter()
            dense = [
                dict(r)
                for r in connection.execute(text(dense_sql(where, exact=True)), params).mappings()
            ]
            timings["dense_fallback"] = perf_counter() - phase_start
            strategy = "exact_fallback"
            warnings.append("Vector pool underfill after bounded expansion: exact search used")
    if mode != "dense" and terms:
        lexical_params = {**params, **{f"term_{i}": t for i, t in enumerate(terms)}}
        phase_start = perf_counter()
        lexical = [
            dict(r)
            for r in connection.execute(text(lexical_sql(where, terms)), lexical_params).mappings()
        ]
        timings["lexical"] = perf_counter() - phase_start
    elif mode != "dense":
        warnings.append("No lexical terms were extracted")
    quantity_candidates = []
    if quantity_probes:
        phase_start = perf_counter()
        quantity_candidates = [
            dict(row)
            for row in connection.execute(
                text(quantity_sql(where, quantity_probes, has_vector=vector is not None)),
                {
                    **params,
                    **{f"quantity_{i}": quantity_pattern(p) for i, p in enumerate(quantity_probes)},
                },
            ).mappings()
        ]
        timings["quantity"] = perf_counter() - phase_start
        if not quantity_candidates:
            warnings.append(
                "No exact quantity candidates; semantic results kept within the same scope"
            )
    phase_start = perf_counter()
    ranked = fuse_rankings(dense, lexical)
    if quantity_probes:
        ranked = promote_quantity_candidates(ranked, quantity_candidates)
    overlap = len({r["chunk_id"] for r in dense} & {r["chunk_id"] for r in lexical})
    boundary_returned = sum(r["lexical_score"] == lexical[-1]["lexical_score"] for r in lexical)
    lexical_diagnostics = {
        "rank_policy": "candidate_midrank" if mode != "dense" else "not_used",
        "tie_scope": "returned_candidates",
        "boundary_tie_count": boundary_returned,
        "candidate_limit_reached": len(lexical) == candidate_limit,
        "full_scope_tie_count": None,
    }
    if lexical_diagnostics["candidate_limit_reached"]:
        warnings.append(
            "Lexical candidate limit reached; tied midranks describe returned candidates only. "
            "Full-scope tie counts are not computed; omitted candidates may have equal scores"
        )
    timings["fusion"] = perf_counter() - phase_start
    details: dict[str, dict[str, Any]] = {}
    if ranked:
        phase_start = perf_counter()
        rows = connection.execute(
            text(f"""
            SELECT c.chunk_id, c.chunk_type, c.document_group, c.content,
                c.content_sha256, c.filing_id, c.document_id, c.section_id,
                c.heading_path, c.source_block_ids, c.source_table_id,
                f.corp_code, f.report_name, f.receipt_number, f.receipt_date, f.is_correction,
                coalesce(nullif(sc.listed_name, ''), sc.corp_name) AS company_name
            {JOINS}
            JOIN public.source_companies sc ON sc.corp_code = f.corp_code
            WHERE {where} AND e.chunk_id = ANY(CAST(:ids AS varchar[]))
        """),
            {**params, "ids": [r["chunk_id"] for r in ranked]},
        ).mappings()
        details = {str(r["chunk_id"]): dict(r) for r in rows}
        timings["hydration"] = perf_counter() - phase_start
    phase_start = perf_counter()
    combined = [{**details[r["chunk_id"]], **r} for r in ranked if r["chunk_id"] in details]
    selected = select_evidence(
        combined,
        top_k=top_k,
        max_per_filing=max_per_filing,
        company_cap=None if filters.get("corp_code") else company_cap,
    )
    if len(selected) < top_k:
        warnings.append("Fewer distinct eligible sources than requested; no filters were relaxed")
    for row in selected:
        row["citation"] = citation(row)
        row["embedding_run_id"] = run["embedding_run_id"]
    warnings.append("Correction status is available; original/correction lineage is not resolved")
    timings["selection"] = perf_counter() - phase_start
    timings["total"] = perf_counter() - started
    return {
        "mode": mode,
        "dense_strategy": strategy,
        "dense_diagnostics": {
            "pool_attempts": pool_attempts,
            "index_usage": "not_observed"
            if not use_exact and mode != "lexical"
            else "not_applicable",
            "index_verification": "Use --explain --analyze; strategy names do not prove HNSW use",
        },
        "lexical_terms": terms,
        "quantity_diagnostics": {
            "probes": list(quantity_probes),
            "candidate_count": len(quantity_candidates),
            "policy": "literal_quantity_then_rrf" if quantity_probes else "not_used",
            "scope_relaxed": False,
        },
        "candidate_counts": {
            "dense": len(dense),
            "lexical": len(lexical),
            "overlap": overlap,
            "fused": len(ranked),
        },
        "lexical_diagnostics": lexical_diagnostics,
        "timing_seconds": timings,
        "filters": filters,
        "warnings": warnings,
        "results": selected,
    }
