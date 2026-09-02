from __future__ import annotations

from pathlib import Path

from disclosure_agent.evaluation.regression import (
    RegressionCase,
    evaluate_answer,
    evaluation_error,
    load_regression_cases,
)
from disclosure_agent.retrieval.answer_query_planner import plan_answer_query
from disclosure_agent.retrieval.evidence_pack import EvidenceItem, EvidencePack
from disclosure_agent.services.answer_service import AnswerResult


def _item(rank: int, report_name: str) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=f"evidence-{rank}",
        source_kind="semantic_chunk",
        rank=rank,
        score=1.0,
        semantic_score=1.0,
        lexical_score=1.0,
        company_name="삼성전자",
        filing_id=f"filing-{rank}",
        report_name=report_name,
        document_id=f"document-{rank}",
        section_id=f"section-{rank}",
        content_text="grounded evidence",
        truncated=False,
        matched_terms=(),
        block_ids=(),
        table_ids=(),
    )


def _result(
    query: str,
    answer: str,
    *,
    reports: tuple[str, ...],
    status: str = "ANSWERABLE",
) -> AnswerResult:
    items = tuple(_item(index, report) for index, report in enumerate(reports, 1))
    return AnswerResult(
        query=query,
        plan=plan_answer_query(query),
        status=status,
        answer=answer,
        generator="HCX-007",
        evidence_pack=EvidencePack(
            query=query,
            retrieval_status="MATCHES_FOUND" if items else "NO_MATCH",
            items=items,
            total_chars=sum(len(item.content_text) for item in items),
        ),
        source_references=(),
    )


def test_repository_regression_cases_load() -> None:
    cases = load_regression_cases(Path("evals/regression_cases.jsonl"))

    assert len(cases) == 14
    assert len({case.case_id for case in cases}) == len(cases)
    assert {case.tier for case in cases} == {"smoke", "challenge"}


def test_repository_extended_cases_load_and_ids_do_not_overlap() -> None:
    baseline = load_regression_cases(Path("evals/regression_cases.jsonl"))
    extended = load_regression_cases(Path("evals/extended_cases.jsonl"))
    baseline_ids = {case.case_id for case in baseline}
    extended_ids = {case.case_id for case in extended}

    assert len(extended) == 30
    assert {case.tier for case in extended} == {"extended"}
    assert baseline_ids.isdisjoint(extended_ids)
    assert len(baseline) + len(extended) == 44


def test_evaluate_answer_passes_multi_year_grounded_case() -> None:
    query = "삼성전자의 2023년과 2025년 사업보고서에서 핵심 사업 변화를 비교해줘"
    case = RegressionCase.from_dict(
        {
            "id": "HYB-TEST",
            "tier": "smoke",
            "category": "multi_period_comparison",
            "query": query,
            "expected_mode": "hybrid_grounded",
            "expected_status": "ANSWERABLE",
            "expected_rails": ["semantic"],
            "min_evidence": 2,
            "must_include": ["2023년", "2025년"],
            "must_not_include": ["대표이사"],
            "evidence_report_contains": "사업보고서",
            "min_evidence_per_year": {"2023": 1, "2025": 1},
            "require_citations": True,
        }
    )
    result = _result(
        query,
        "2023년에는 제품 경쟁력을 강화했습니다 [E1]. "
        "2025년에는 AI 적용을 확대했습니다 [E2].",
        reports=("사업보고서 (2023.12)", "사업보고서 (2025.12)"),
    )

    evaluation = evaluate_answer(case, result)

    assert evaluation.verdict == "PASS"
    assert evaluation.hard_passed is True
    assert evaluation.failure_types == ()


def test_evaluate_answer_classifies_retrieval_and_grounding_failures() -> None:
    query = "삼성전자의 2023년과 2025년 사업보고서에서 핵심 사업 변화를 비교해줘"
    case = RegressionCase.from_dict(
        {
            "id": "HYB-FAIL",
            "tier": "smoke",
            "category": "multi_period_comparison",
            "query": query,
            "expected_mode": "hybrid_grounded",
            "min_evidence": 2,
            "must_not_include": ["대표이사"],
            "min_evidence_per_year": {"2023": 1, "2025": 1},
            "require_citations": True,
        }
    )
    result = _result(
        query,
        "대표이사 변동을 핵심 사업 변화로 봅니다.",
        reports=("사업보고서 (2023.12)",),
    )

    evaluation = evaluate_answer(case, result)

    assert evaluation.verdict == "FAIL"
    assert "retrieval" in evaluation.failure_types
    assert "grounding" in evaluation.failure_types


def test_manual_review_case_keeps_hard_pass_separate() -> None:
    query = "삼성전자의 2025년 사업보고서를 기준으로 주요 투자 계획을 정리해줘"
    case = RegressionCase.from_dict(
        {
            "id": "MANUAL-1",
            "tier": "challenge",
            "category": "narrative",
            "query": query,
            "expected_mode": "hybrid_grounded",
            "manual_review": True,
        }
    )
    result = _result(
        query,
        "투자 방향이 확인됩니다 [E1].",
        reports=("사업보고서 (2025.12)",),
    )

    evaluation = evaluate_answer(case, result)

    assert evaluation.hard_passed is True
    assert evaluation.verdict == "MANUAL_REVIEW"


def test_evaluation_error_records_exception_without_raising() -> None:
    case = RegressionCase.from_dict(
        {
            "id": "ERR-1",
            "tier": "challenge",
            "category": "execution",
            "query": "삼성전자의 2025년 매출액은?",
        }
    )

    evaluation = evaluation_error(case, RuntimeError("boom"), duration_ms=12)

    assert evaluation.verdict == "ERROR"
    assert evaluation.failure_types == ("execution",)
    assert evaluation.to_record()["failure_reason"] == "RuntimeError: boom"
