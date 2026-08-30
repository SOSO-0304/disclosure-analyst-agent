from __future__ import annotations

from disclosure_agent.retrieval.evaluation import (
    BenchmarkCase,
    aggregate_metrics,
    automated_recommendation,
    build_benchmark_cases,
    is_informative_probe,
    metrics_by_dimension,
    score_ranking,
    select_balanced_targets,
    topic_from_row,
)


def _row(
    chunk_id: str,
    *,
    corp_code: str = "001",
    document_group: str = "periodic",
    chunk_type: str = "narrative",
    section_id: str = "section-1",
) -> dict[str, object]:
    return {
        "chunk_id": chunk_id,
        "corp_code": corp_code,
        "corp_name": "테스트주식회사",
        "listed_name": "테스트",
        "document_group": document_group,
        "chunk_type": chunk_type,
        "document_id": "document-1",
        "filing_id": "filing-1",
        "section_id": section_id,
        "source_table_id": None,
        "heading_path": ["사업의 내용", "매출 및 수주상황"],
        "metadata": {},
        "document_subtype": "사업보고서",
        "report_name": "2025년 사업보고서",
        "document_title": "사업보고서 본문",
        "content": "매출액은 전년 대비 증가했습니다.",
    }


def test_balanced_targets_use_one_company_lane_before_repeating() -> None:
    rows = [
        _row("p1", corp_code="001", document_group="periodic"),
        _row("p2", corp_code="002", document_group="periodic"),
        _row(
            "m1",
            corp_code="001",
            document_group="major",
            chunk_type="table",
        ),
        _row(
            "m2",
            corp_code="002",
            document_group="major",
            chunk_type="table",
        ),
    ]

    selected = select_balanced_targets(rows, limit=2, seed="seed")

    assert {row["document_group"] for row in selected} == {"periodic", "major"}


def test_build_cases_creates_three_independent_retrieval_checks() -> None:
    rows = [_row("a"), _row("b")]

    cases = build_benchmark_cases(rows, [rows[0]])

    assert [case.suite for case in cases] == [
        "company_context",
        "topic_filtered",
        "content_anchor",
    ]
    assert cases[0].query.startswith("테스트의 ")
    assert cases[0].filter_corp_code is False
    assert cases[1].filter_corp_code is True
    assert cases[0].relevant_chunk_ids == ("a", "b")
    assert cases[2].relevant_chunk_ids == ("a",)


def test_low_information_content_anchor_is_omitted() -> None:
    row = _row("dash")
    row["content"] = "-"

    cases = build_benchmark_cases([row], [row])

    assert [case.suite for case in cases] == [
        "company_context",
        "topic_filtered",
    ]
    assert is_informative_probe("-") is False
    assert is_informative_probe("매출액은 100억원") is True


def test_topic_prefers_table_caption_and_heading() -> None:
    row = _row("table", chunk_type="table")
    row["metadata"] = {"caption": "부문별 매출액"}

    assert topic_from_row(row) == "부문별 매출액 매출 및 수주상황"


def test_score_and_aggregate_ranking_metrics() -> None:
    case = BenchmarkCase(
        case_id="case",
        suite="company_context",
        query="테스트 매출",
        target_chunk_id="wanted",
        relevant_chunk_ids=("wanted",),
        corp_code="001",
        document_group="periodic",
        chunk_type="narrative",
        filter_corp_code=False,
    )
    outcome = score_ranking(
        case,
        [
            {"chunk_id": "other", "corp_code": "999", "similarity": 0.9},
            {"chunk_id": "wanted", "corp_code": "001", "similarity": 0.8},
        ],
    )
    metrics = aggregate_metrics([outcome])

    assert outcome["first_relevant_rank"] == 2
    assert metrics["hit_at_1"] == 0
    assert metrics["hit_at_5"] == 1
    assert metrics["reciprocal_rank_at_10"] == 0.5
    assert metrics["wrong_company_at_1"] == 1


def test_automated_gate_never_allows_full_embedding() -> None:
    v1 = {
        "company_context": {
            "company_accuracy_at_1": 0.60,
            "hit_at_10": 0.70,
        },
        "topic_filtered": {
            "hit_at_10": 0.80,
            "reciprocal_rank_at_10": 0.50,
        },
        "content_anchor": {"hit_at_10": 0.90},
    }
    v2 = {
        "company_context": {
            "company_accuracy_at_1": 0.75,
            "hit_at_10": 0.80,
        },
        "topic_filtered": {
            "hit_at_10": 0.80,
            "reciprocal_rank_at_10": 0.49,
        },
        "content_anchor": {"hit_at_10": 0.90},
    }

    result = automated_recommendation(v1, v2)

    assert result["recommendation"] == "v2_candidate"
    assert result["manual_review_required"] is True
    assert result["full_embedding_allowed"] is False


def test_metrics_can_be_split_by_corpus_lane() -> None:
    outcomes = [
        {
            "suite": "topic_filtered",
            "document_group": "periodic",
            "chunk_type": "narrative",
            "hit_at_1": 1,
            "hit_at_5": 1,
            "hit_at_10": 1,
            "reciprocal_rank_at_10": 1.0,
            "company_match_at_1": None,
        },
        {
            "suite": "topic_filtered",
            "document_group": "major",
            "chunk_type": "table",
            "hit_at_1": 0,
            "hit_at_5": 0,
            "hit_at_10": 0,
            "reciprocal_rank_at_10": 0.0,
            "company_match_at_1": None,
        },
    ]

    result = metrics_by_dimension(outcomes, "document_group")

    assert result["periodic"]["overall"]["hit_at_10"] == 1
    assert result["major"]["overall"]["hit_at_10"] == 0
