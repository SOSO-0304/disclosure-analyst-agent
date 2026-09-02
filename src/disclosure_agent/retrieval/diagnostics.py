"""Allowlisted EXPLAIN reports for generated SELECTs; never record bound inputs."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from time import perf_counter
from typing import Any

from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError

from disclosure_agent.retrieval.hybrid import lexical_terms
from disclosure_agent.retrieval.search import (
    configure_dense_search,
    dense_sql,
    lexical_sql,
    scope_sql,
    vector_pool_limits,
)

_TEXT = {
    "Node Type",
    "Relation Name",
    "Index Name",
    "Join Type",
    "Parent Relationship",
    "Strategy",
    "Scan Direction",
    "Sort Method",
    "Sort Space Type",
}
_NUMERIC = {
    "Startup Cost",
    "Total Cost",
    "Plan Rows",
    "Plan Width",
    "Actual Rows",
    "Actual Loops",
    "Actual Startup Time",
    "Actual Total Time",
    "Planning Time",
    "Execution Time",
    "Rows Removed by Filter",
    "Rows Removed by Index Recheck",
    "Rows Removed by Join Filter",
    "Heap Fetches",
    "Shared Hit Blocks",
    "Shared Read Blocks",
    "Shared Dirtied Blocks",
    "Shared Written Blocks",
    "Local Hit Blocks",
    "Local Read Blocks",
    "Local Dirtied Blocks",
    "Local Written Blocks",
    "Temp Read Blocks",
    "Temp Written Blocks",
    "I/O Read Time",
    "I/O Write Time",
    "Temp I/O Read Time",
    "Temp I/O Write Time",
    "Sort Space Used",
    "Hash Buckets",
    "Original Hash Buckets",
    "Hash Batches",
    "Original Hash Batches",
    "Peak Memory Usage",
    "Workers Planned",
    "Workers Launched",
    "Worker Number",
    "Functions",
    "Generation",
    "Inlining",
    "Optimization",
    "Emission",
    "Total",
}
_BOOLEAN = {
    "Parallel Aware",
    "Async Capable",
    "Inner Unique",
    "Expressions",
    "Deforming",
    "Inlining",
    "Optimization",
}
_OBJECT = {"Plan", "Planning", "JIT", "Timing", "Options"}
_ARRAY = {"Plans", "Workers"}


def sanitize_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    """Drop Filter/Index Cond/Order By/Output/SQL and every unknown field recursively."""
    result = {}
    for key, item in value.items():
        if key in _OBJECT and isinstance(item, dict):
            result[key] = sanitize_plan(item)
        elif key in _ARRAY and isinstance(item, list):
            result[key] = [sanitize_plan(child) for child in item if isinstance(child, dict)]
        elif key in _TEXT and isinstance(item, str):
            result[key] = item
        elif key in _BOOLEAN and isinstance(item, bool):
            result[key] = item
        elif key in _NUMERIC and isinstance(item, (int, float)) and math.isfinite(item):
            result[key] = item
    return result


def summarize_plan(explain: Mapping[str, Any]) -> dict[str, Any]:
    root = explain.get("Plan", {})
    scans, indexes, disk_sorts = [], set(), []

    def visit(node):
        if "Scan" in node.get("Node Type", ""):
            scans.append(
                {
                    k: node[k]
                    for k in (
                        "Node Type",
                        "Relation Name",
                        "Index Name",
                        "Plan Rows",
                        "Actual Rows",
                        "Actual Loops",
                        "Rows Removed by Filter",
                        "Rows Removed by Index Recheck",
                    )
                    if k in node
                }
            )
        if node.get("Index Name"):
            indexes.add(node["Index Name"])
        if node.get("Sort Space Type") == "Disk":
            disk_sorts.append(
                {
                    k: node[k]
                    for k in (
                        "Node Type",
                        "Sort Method",
                        "Sort Space Used",
                        "Sort Space Type",
                    )
                    if k in node
                }
            )
        for child in node.get("Plans", []) + node.get("Workers", []):
            visit(child)

    visit(root)
    return {
        "planning_ms": explain.get("Planning Time"),
        "execution_ms": explain.get("Execution Time"),
        "indexes": sorted(indexes),
        "scans": scans,
        # Parent buffer counters already include children. Never sum them recursively.
        "root_buffers": {
            k: root[k]
            for k in (
                "Shared Hit Blocks",
                "Shared Read Blocks",
                "Temp Read Blocks",
                "Temp Written Blocks",
            )
            if k in root
        },
        "disk_sorts": disk_sorts,
        "jit": explain.get("JIT"),
    }


def _failure(exc: SQLAlchemyError) -> dict[str, Any]:
    code = getattr(getattr(exc, "orig", None), "sqlstate", None)
    return {
        "status": "failed",
        "error_type": type(exc).__name__,
        "sqlstate": code if isinstance(code, str) and len(code) == 5 and code.isalnum() else None,
        "message": "Database operation failed; SQL, parameters and raw errors were omitted",
    }


def vector_index_check(
    plan: Mapping[str, Any],
    indexes: list[dict[str, Any]] | None,
    *,
    analyze: bool,
) -> dict[str, Any]:
    """Use catalog access methods, not an index-name substring, to identify HNSW."""
    if indexes is None:
        return {"status": "unknown_catalog", "index_names": []}
    hnsw = {
        row["index_name"]
        for row in indexes
        if row["access_method"] == "hnsw" and row["is_valid"] and row["is_ready"]
    }
    matched = set()

    def visit(node):
        if node.get("Index Name") in hnsw and (not analyze or node.get("Actual Loops", 0) > 0):
            matched.add(node["Index Name"])
        for child in node.get("Plans", []):
            visit(child)

    visit(plan.get("Plan", {}))
    return {
        "status": ("hnsw_used" if matched else "hnsw_not_used")
        if analyze
        else ("hnsw_planned" if matched else "hnsw_not_planned"),
        "index_names": sorted(matched),
    }


def explain_retrieval(
    connection: Connection,
    *,
    run: Mapping[str, Any],
    query: str,
    vector: str | None,
    mode: str = "dense",
    candidate_limit: int = 100,
    exact: bool = False,
    filters: Mapping[str, Any] | None = None,
    analyze: bool = False,
) -> dict[str, Any]:
    """Call in a fresh transaction. ANALYZE is opt-in and runs each SELECT once.

    Stage-local savepoints preserve a completed plan if another stage times out.
    No index/statistics changes, settings persistence, fallback query or result fetch.
    """
    if mode not in {"dense", "lexical", "hybrid"} or not 1 <= candidate_limit <= 1000:
        raise ValueError("Invalid diagnostic mode or candidate limit")
    if mode != "lexical" and vector is None:
        raise ValueError("A real query vector is required for a representative dense plan")
    filters = dict(filters or {})
    where, params = scope_sql(run, **filters)
    params.update(vector=vector, candidate_limit=candidate_limit)
    narrowed = any(v is not None for k, v in filters.items() if k != "corrections") or (
        filters.get("corrections", "all") != "all"
    )
    strategy = "exact_filtered" if narrowed else "exact" if exact else "vector_first"
    params["pool_limit"] = vector_pool_limits(candidate_limit)[0]
    terms = lexical_terms(query)
    queries = []
    if mode != "lexical":
        queries.append(("dense_initial", dense_sql(where, exact=exact or narrowed), params))
    if mode != "dense" and terms:
        queries.append(
            (
                "lexical",
                lexical_sql(where, terms),
                {**params, **{f"term_{i}": t for i, t in enumerate(terms)}},
            )
        )
    report = {
        "schema_version": "retrieval-explain-v2",
        "embedding_run_id": run["embedding_run_id"],
        "chunk_run_id": run["chunk_run_id"],
        "input_version": run.get("input_version"),
        "mode": mode,
        "dense_strategy": strategy if mode != "lexical" else "not_used",
        "analyze": analyze,
        "candidate_limit": candidate_limit,
        "initial_vector_pool_limit": (
            params["pool_limit"] if mode != "lexical" and not (exact or narrowed) else None
        ),
        "filters": filters,
        "database_writes": 0,
        "statement_timeout_seconds": 60,
        "candidate_select_attempts": 0,
        "stages": {},
        "notes": [
            "Only the initial pool query is explained; no pool expansion, fallback or hydration",
            "ANALYZE runs selected queries and can use temporary disk/cache; data is read-only",
            "Plans omit expressions, input text/vector, SQL, credentials and connection URLs",
            "Node timing is off; execution_ms is server time, not end-to-end search latency",
        ],
    }
    connection.execute(text("SET TRANSACTION READ ONLY"))
    connection.execute(text("SET LOCAL statement_timeout = '60s'"))
    configure_dense_search(connection, iterative=mode != "lexical" and not (exact or narrowed))
    try:
        with connection.begin_nested():
            report["environment"] = dict(
                connection.execute(
                    text("""
                SELECT current_setting('server_version') AS postgres_version,
                    (SELECT extversion FROM pg_extension WHERE extname = 'vector')
                        AS pgvector_version,
                    current_setting('work_mem') AS work_mem,
                    current_setting('shared_buffers') AS shared_buffers,
                    current_setting('effective_cache_size') AS effective_cache_size,
                    current_setting('track_io_timing') AS track_io_timing,
                    current_setting('hnsw.ef_search') AS hnsw_ef_search,
                    current_setting('hnsw.iterative_scan', true) AS hnsw_iterative_scan
            """)
                )
                .mappings()
                .one()
            )
    except SQLAlchemyError as exc:
        report["environment"] = _failure(exc)
    indexes = None
    try:
        with connection.begin_nested():
            indexes = [
                dict(row)
                for row in connection.execute(
                    text("""
                SELECT i.relname AS index_name, am.amname AS access_method,
                    ix.indisvalid AS is_valid, ix.indisready AS is_ready
                FROM pg_index ix
                JOIN pg_class t ON t.oid = ix.indrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                JOIN pg_class i ON i.oid = ix.indexrelid
                JOIN pg_am am ON am.oid = i.relam
                WHERE n.nspname = 'public' AND t.relname = 'retrieval_embeddings'
                ORDER BY i.relname
            """)
                ).mappings()
            ]
            report["vector_indexes"] = indexes
    except SQLAlchemyError as exc:
        report["vector_indexes"] = _failure(exc)
    prefix = (
        "EXPLAIN (ANALYZE TRUE, BUFFERS TRUE, TIMING FALSE, FORMAT JSON) "
        if analyze
        else "EXPLAIN (FORMAT JSON) "
    )
    for stage, sql, stage_params in queries:
        started = perf_counter()
        try:
            with connection.begin_nested():
                if analyze:
                    report["candidate_select_attempts"] += 1
                raw = connection.execute(text(prefix + sql), stage_params).scalar_one()
                raw = json.loads(raw) if isinstance(raw, str) else raw
                if (
                    not isinstance(raw, list)
                    or len(raw) != 1
                    or not isinstance(raw[0], dict)
                    or not isinstance(raw[0].get("Plan"), dict)
                ):
                    raise ValueError("Unexpected EXPLAIN JSON shape; raw content was omitted")
                plan = sanitize_plan(raw[0])
                report["stages"][stage] = {
                    "status": "analyzed" if analyze else "planned",
                    "plan": plan,
                    "summary": summarize_plan(plan),
                }
                if stage == "dense_initial":
                    report["stages"][stage]["summary"]["vector_index_check"] = vector_index_check(
                        plan, indexes, analyze=analyze
                    )
        except SQLAlchemyError as exc:
            report["stages"][stage] = _failure(exc)
        report["stages"][stage]["elapsed_seconds"] = perf_counter() - started
    report["status"] = (
        "partial"
        if report["environment"].get("status") == "failed"
        or indexes is None
        or any(stage["status"] == "failed" for stage in report["stages"].values())
        else "analyzed"
        if analyze
        else "planned"
    )
    if mode != "dense" and not terms:
        report["notes"].append("Lexical stage skipped: no terms extracted")
    return report
