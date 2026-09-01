"""Read-only retrieval over an immutable, completed embedding run."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from time import perf_counter
from typing import Any

from sqlalchemy import Connection, text

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
        SELECT e.chunk_id, 1 - (e.embedding <=> CAST(:vector AS vector)) AS similarity
        {JOINS} WHERE {where}
        ORDER BY e.embedding <=> CAST(:vector AS vector)
        LIMIT :candidate_limit
    """


def lexical_sql(where: str, terms: Sequence[str]) -> str:
    if not terms:
        raise ValueError("Lexical query requires terms")
    score = " + ".join(
        f"CASE WHEN strpos(lower(c.content), :term_{i}) > 0 THEN 1 ELSE 0 END"
        for i in range(len(terms))
    )
    return f"""
        WITH lexical AS MATERIALIZED (
            SELECT e.chunk_id, ({score}) AS lexical_score {JOINS} WHERE {where}
        ), score_counts AS (
            SELECT lexical_score, count(*) AS tie_count
            FROM lexical WHERE lexical_score > 0 GROUP BY lexical_score
        ), ranked_scores AS (
            SELECT lexical_score, tie_count,
                COALESCE(SUM(tie_count) OVER (
                    ORDER BY lexical_score DESC
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ), 0) + (tie_count + 1) / 2.0 AS lexical_rank
            FROM score_counts
        )
        SELECT l.chunk_id, l.lexical_score, r.lexical_rank,
            r.tie_count AS lexical_tie_count
        FROM lexical l JOIN ranked_scores r USING (lexical_score)
        ORDER BY l.lexical_score DESC, md5(l.chunk_id), l.chunk_id
        LIMIT :candidate_limit
    """


def retrieve(
    connection: Connection,
    *,
    run: Mapping[str, Any],
    query: str,
    vector: str | None,
    mode: str = "hybrid",
    top_k: int = 5,
    candidate_limit: int = 100,
    max_per_filing: int = 1,
    company_cap: int = 2,
    exact: bool = False,
    filters: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    started = perf_counter()
    timings = dict.fromkeys(
        ("dense_initial", "dense_fallback", "lexical", "fusion", "hydration", "selection"), 0.0
    )
    if mode not in {"hybrid", "dense", "lexical"}:
        raise ValueError("Unknown retrieval mode")
    if not 1 <= top_k <= candidate_limit <= 1000:
        raise ValueError("Require 1 <= top-k <= candidate-limit <= 1000")
    if mode != "lexical" and vector is None:
        raise ValueError("Dense search requires a query vector")
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
    if mode != "lexical":
        strategy = "exact_filtered" if narrowed else "exact" if exact else "ann"
        phase_start = perf_counter()
        connection.execute(text("SET LOCAL hnsw.ef_search = 200"))
        dense = [
            dict(r)
            for r in connection.execute(text(dense_sql(where, exact=use_exact)), params).mappings()
        ]
        timings["dense_initial"] = perf_counter() - phase_start
        if not use_exact and len(dense) < candidate_limit:
            phase_start = perf_counter()
            dense = [
                dict(r)
                for r in connection.execute(text(dense_sql(where, exact=True)), params).mappings()
            ]
            timings["dense_fallback"] = perf_counter() - phase_start
            strategy = "exact_fallback"
            warnings.append("ANN candidate underfill: exact search used for this query")
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
    phase_start = perf_counter()
    ranked = fuse_rankings(dense, lexical)
    overlap = len({r["chunk_id"] for r in dense} & {r["chunk_id"] for r in lexical})
    boundary_ties = int(lexical[-1].get("lexical_tie_count", 1)) if lexical else 0
    boundary_returned = sum(r["lexical_score"] == lexical[-1]["lexical_score"] for r in lexical)
    lexical_diagnostics = {
        "rank_policy": "scope_midrank",
        "boundary_tie_count": boundary_ties,
        "boundary_tie_returned": boundary_returned,
        "boundary_tie_truncated": boundary_ties > boundary_returned,
    }
    if lexical_diagnostics["boundary_tie_truncated"]:
        warnings.append(
            "Lexical cutoff intersects a tie group; hash-selected candidates share "
            "the full scoped group's midrank (which can exceed the candidate limit)"
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
        "lexical_terms": terms,
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
