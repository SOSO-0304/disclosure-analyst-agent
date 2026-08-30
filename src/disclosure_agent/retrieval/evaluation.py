"""Deterministic proxy benchmarks for versioned retrieval embeddings."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    """One query with an explicit relevance set and optional company filter."""

    case_id: str
    suite: str
    query: str
    target_chunk_id: str
    relevant_chunk_ids: tuple[str, ...]
    corp_code: str
    document_group: str
    chunk_type: str
    filter_corp_code: bool


def select_balanced_targets(
    rows: Sequence[Mapping[str, Any]],
    *,
    limit: int,
    seed: str,
) -> list[Mapping[str, Any]]:
    """Select one chunk per company/group/type, balanced across corpus lanes."""

    if limit <= 0:
        raise ValueError("limit must be positive")
    strata: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = (
            str(row["corp_code"]),
            str(row["document_group"]),
            str(row["chunk_type"]),
        )
        current = strata.get(key)
        if current is None or _stable_key(row, seed) < _stable_key(current, seed):
            strata[key] = row

    lanes: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in strata.values():
        lanes[(str(row["document_group"]), str(row["chunk_type"]))].append(row)
    for values in lanes.values():
        values.sort(key=lambda row: _stable_key(row, seed))

    selected: list[Mapping[str, Any]] = []
    lane_keys = sorted(lanes)
    offset = 0
    while len(selected) < limit:
        added = False
        for lane in lane_keys:
            values = lanes[lane]
            if offset < len(values):
                selected.append(values[offset])
                added = True
                if len(selected) == limit:
                    break
        if not added:
            break
        offset += 1
    return selected


def build_benchmark_cases(
    sample_rows: Sequence[Mapping[str, Any]],
    targets: Sequence[Mapping[str, Any]],
) -> list[BenchmarkCase]:
    """Build company-routing, topic, and semantic-preservation proxy cases."""

    company_relevant: dict[str, list[str]] = defaultdict(list)
    topic_relevant: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in sample_rows:
        corp_code = str(row["corp_code"])
        chunk_id = str(row["chunk_id"])
        company_relevant[corp_code].append(chunk_id)
        topic_relevant[(corp_code, _topic_relevance_key(row))].append(chunk_id)

    cases: list[BenchmarkCase] = []
    for row in targets:
        corp_code = str(row["corp_code"])
        company = _company_name(row)
        topic = topic_from_row(row)
        probe = content_probe(str(row.get("content") or ""))
        target_chunk_id = str(row["chunk_id"])
        company_ids = tuple(sorted(set(company_relevant[corp_code])))
        topic_ids = tuple(
            sorted(set(topic_relevant[(corp_code, _topic_relevance_key(row))]))
        ) or (target_chunk_id,)
        common = {
            "target_chunk_id": target_chunk_id,
            "corp_code": corp_code,
            "document_group": str(row["document_group"]),
            "chunk_type": str(row["chunk_type"]),
        }
        cases.append(
            BenchmarkCase(
                case_id=_case_id("company_context", target_chunk_id),
                suite="company_context",
                query=f"{company}의 {topic} 관련 공시",
                relevant_chunk_ids=company_ids,
                filter_corp_code=False,
                **common,
            )
        )
        cases.append(
            BenchmarkCase(
                case_id=_case_id("topic_filtered", target_chunk_id),
                suite="topic_filtered",
                query=f"{topic} 관련 내용",
                relevant_chunk_ids=topic_ids,
                filter_corp_code=True,
                **common,
            )
        )
        if is_informative_probe(probe):
            cases.append(
                BenchmarkCase(
                    case_id=_case_id("content_anchor", target_chunk_id),
                    suite="content_anchor",
                    query=probe,
                    relevant_chunk_ids=(target_chunk_id,),
                    filter_corp_code=True,
                    **common,
                )
            )
    return cases


def topic_from_row(row: Mapping[str, Any]) -> str:
    """Prefer table captions/headings over broad filing titles."""

    metadata = dict(row.get("metadata") or {})
    headings = [
        str(value).strip()
        for value in (row.get("heading_path") or [])
        if str(value).strip()
    ]
    candidates = [
        str(metadata.get("caption") or "").strip(),
        headings[-1] if headings else "",
        str(row.get("document_subtype") or "").strip(),
        str(row.get("report_name") or "").strip(),
        str(row.get("document_title") or "").strip(),
    ]
    parts: list[str] = []
    for value in candidates:
        if value and value not in parts:
            parts.append(value)
        if len(parts) == 2:
            break
    return " ".join(parts) or "공시 내용"


def content_probe(value: str, *, maximum: int = 140) -> str:
    """Return a short deterministic semantic anchor without changing its words."""

    compact = re.sub(r"\s*\|\s*", " ", value)
    compact = re.sub(r"\s+", " ", compact).strip()
    return compact[:maximum].rstrip()


def is_informative_probe(value: str) -> bool:
    """Reject punctuation-only and placeholder anchors from quality metrics."""

    informative = re.findall(r"[0-9A-Za-z가-힣]", value)
    return len(informative) >= 8 and len(set(informative)) >= 3


def score_ranking(
    case: BenchmarkCase,
    hits: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Score one ranked result list against a possibly multi-chunk relevance set."""

    relevant = set(case.relevant_chunk_ids)
    first_rank = next(
        (
            index
            for index, hit in enumerate(hits, 1)
            if str(hit["chunk_id"]) in relevant
        ),
        None,
    )
    top_corp = str(hits[0]["corp_code"]) if hits else None
    return {
        "case_id": case.case_id,
        "suite": case.suite,
        "document_group": case.document_group,
        "chunk_type": case.chunk_type,
        "first_relevant_rank": first_rank,
        "hit_at_1": int(first_rank is not None and first_rank <= 1),
        "hit_at_5": int(first_rank is not None and first_rank <= 5),
        "hit_at_10": int(first_rank is not None and first_rank <= 10),
        "reciprocal_rank_at_10": (
            1.0 / first_rank if first_rank is not None and first_rank <= 10 else 0.0
        ),
        "company_match_at_1": (
            int(top_corp == case.corp_code) if not case.filter_corp_code else None
        ),
        "top_chunk_id": str(hits[0]["chunk_id"]) if hits else None,
        "top_corp_code": top_corp,
        "top_similarity": float(hits[0]["similarity"]) if hits else None,
    }


def aggregate_metrics(outcomes: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate ranking metrics with explicit denominators."""

    values = list(outcomes)
    if not values:
        return {"cases": 0}
    result: dict[str, Any] = {"cases": len(values)}
    for key in (
        "hit_at_1",
        "hit_at_5",
        "hit_at_10",
        "reciprocal_rank_at_10",
    ):
        result[key] = round(
            sum(float(value[key]) for value in values) / len(values), 6
        )
    company_values = [
        float(value["company_match_at_1"])
        for value in values
        if value.get("company_match_at_1") is not None
    ]
    if company_values:
        result["company_accuracy_at_1"] = round(
            sum(company_values) / len(company_values), 6
        )
        result["wrong_company_at_1"] = round(
            1.0 - result["company_accuracy_at_1"], 6
        )
    return result


def metrics_by_suite(outcomes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for outcome in outcomes:
        grouped[str(outcome["suite"])].append(outcome)
    return {
        suite: aggregate_metrics(values)
        for suite, values in sorted(grouped.items())
    }


def metrics_by_dimension(
    outcomes: Sequence[Mapping[str, Any]],
    dimension: str,
) -> dict[str, Any]:
    """Expose lane-level regressions that corpus-wide averages can hide."""

    if dimension not in {"document_group", "chunk_type"}:
        raise ValueError(f"Unsupported metric dimension: {dimension}")
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for outcome in outcomes:
        grouped[str(outcome[dimension])].append(outcome)
    return {
        value: {
            "overall": aggregate_metrics(rows),
            "by_suite": metrics_by_suite(rows),
        }
        for value, rows in sorted(grouped.items())
    }


def automated_recommendation(
    v1: Mapping[str, Mapping[str, float]],
    v2: Mapping[str, Mapping[str, float]],
) -> dict[str, Any]:
    """Apply conservative proxy gates; manual questions are always still required."""

    checks = {
        "company_accuracy_not_regressed": (
            v2["company_context"]["company_accuracy_at_1"]
            >= v1["company_context"]["company_accuracy_at_1"] - 0.01
        ),
        "company_recall_not_regressed": (
            v2["company_context"]["hit_at_10"]
            >= v1["company_context"]["hit_at_10"] - 0.01
        ),
        "topic_recall_guardrail": (
            v2["topic_filtered"]["hit_at_10"]
            >= v1["topic_filtered"]["hit_at_10"] - 0.02
        ),
        "topic_mrr_guardrail": (
            v2["topic_filtered"]["reciprocal_rank_at_10"]
            >= v1["topic_filtered"]["reciprocal_rank_at_10"] - 0.03
        ),
        "content_recall_guardrail": (
            v2["content_anchor"]["hit_at_10"]
            >= v1["content_anchor"]["hit_at_10"] - 0.02
        ),
    }
    company_gain = max(
        v2["company_context"]["company_accuracy_at_1"]
        - v1["company_context"]["company_accuracy_at_1"],
        v2["company_context"]["hit_at_10"]
        - v1["company_context"]["hit_at_10"],
    )
    if not all(checks.values()):
        recommendation = "retain_v1_or_revise_v2"
    elif company_gain >= 0.02:
        recommendation = "v2_candidate"
    else:
        recommendation = "inconclusive"
    return {
        "recommendation": recommendation,
        "checks": checks,
        "company_gain": round(company_gain, 6),
        "manual_review_required": True,
        "full_embedding_allowed": False,
    }


def _topic_relevance_key(row: Mapping[str, Any]) -> str:
    metadata = dict(row.get("metadata") or {})
    caption = str(metadata.get("caption") or "").strip()
    table_id = str(row.get("source_table_id") or "").strip()
    if caption and table_id:
        return f"table:{table_id}"
    headings = [
        str(value).strip()
        for value in (row.get("heading_path") or [])
        if str(value).strip()
    ]
    section_id = str(row.get("section_id") or "").strip()
    if headings and section_id:
        return f"section:{section_id}"
    filing_id = str(row.get("filing_id") or "").strip()
    if filing_id:
        return f"filing:{filing_id}:{row['chunk_type']}"
    return f"document:{row['document_id']}:{row['chunk_type']}"


def _company_name(row: Mapping[str, Any]) -> str:
    return str(row.get("listed_name") or row.get("corp_name") or "회사").strip()


def _case_id(suite: str, chunk_id: str) -> str:
    return sha256(f"{suite}\x1f{chunk_id}".encode()).hexdigest()[:16]


def _stable_key(row: Mapping[str, Any], seed: str) -> str:
    return sha256(f"{seed}\x1f{row['chunk_id']}".encode()).hexdigest()
