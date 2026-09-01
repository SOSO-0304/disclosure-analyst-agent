from decimal import Decimal
from types import SimpleNamespace

from disclosure_agent.domain.metrics import (
    MetricAnalysisResult,
    MetricName,
    MetricObservation,
    MetricOperation,
    MetricRank,
    MetricSource,
    MetricTarget,
)
from disclosure_agent.retrieval.evidence_pack import render_evidence_pack
from disclosure_agent.retrieval.metric_evidence import build_metric_evidence_pack
from disclosure_agent.storage.db_models import SourceFilingRow
from disclosure_agent.storage.generic_fact_models import GenericFactRow


def _observation(company: str, amount: int, fact_id: str, filing_id: str) -> MetricObservation:
    return MetricObservation(
        target=MetricTarget(company_name=company, year=2025),
        status="ANSWERABLE",
        amount_krw=amount,
        filing_id=filing_id,
        fact_id=fact_id,
        raw_value=str(amount),
        resolved_unit="원",
    )


class FakeSession:
    def __init__(self) -> None:
        self.facts = {
            "fact:1": SimpleNamespace(
                fact_id="fact:1",
                document_id="doc:1",
                section_id="section:1",
                block_id="block:1",
                table_id="table:1",
            ),
            "fact:2": SimpleNamespace(
                fact_id="fact:2",
                document_id="doc:2",
                section_id="section:2",
                block_id="block:2",
                table_id="table:2",
            ),
            "fact:3": SimpleNamespace(
                fact_id="fact:3",
                document_id="doc:3",
                section_id="section:3",
                block_id="block:3",
                table_id="table:3",
            ),
        }
        self.filings = {
            "filing:1": SimpleNamespace(report_name="사업보고서 (2025.12)"),
            "filing:2": SimpleNamespace(report_name="신규시설투자등"),
            "filing:3": SimpleNamespace(report_name="신규시설투자등"),
        }

    def get(self, model, key):
        if model is GenericFactRow:
            return self.facts.get(key)
        if model is SourceFilingRow:
            return self.filings.get(key)
        return None


def test_sum_pack_keeps_source_items_and_separate_deterministic_result() -> None:
    observations = (
        _observation("A", 100, "fact:1", "filing:1"),
        _observation("B", 200, "fact:2", "filing:2"),
    )
    result = MetricAnalysisResult(
        status="ANSWERABLE",
        metric=MetricName.REVENUE,
        operation=MetricOperation.SUM,
        observations=observations,
        derived_value=Decimal(300),
    )

    pack = build_metric_evidence_pack(FakeSession(), query="A와 B의 매출 합계", result=result)
    rendered = render_evidence_pack(pack)

    assert len(pack.items) == 2
    assert pack.items[0].fact_ids == ("fact:1",)
    assert pack.items[1].fact_ids == ("fact:2",)
    assert "=== DETERMINISTIC ANALYSIS ===" in rendered
    assert "derived_from: [E1],[E2]" in rendered
    assert "관측값: A 2025 100 원 [E1]" in rendered
    assert "관측값: B 2025 200 원 [E2]" in rendered
    assert "deterministic_result: 300 원" in rendered


def test_ranking_analysis_points_each_rank_to_its_source_evidence() -> None:
    first = _observation("A", 100, "fact:1", "filing:1")
    second = _observation("B", 200, "fact:2", "filing:2")
    result = MetricAnalysisResult(
        status="ANSWERABLE",
        metric=MetricName.REVENUE,
        operation=MetricOperation.RANKING,
        observations=(first, second),
        ranking=(
            MetricRank(position=1, observation=second),
            MetricRank(position=2, observation=first),
        ),
    )

    pack = build_metric_evidence_pack(FakeSession(), query="A와 B 매출 순위", result=result)
    rendered = render_evidence_pack(pack)

    assert "1위: B 2025 200 원 [E2]" in rendered
    assert "2위: A 2025 100 원 [E1]" in rendered


def test_facility_investment_observation_preserves_all_source_filings() -> None:
    first = MetricObservation(
        target=MetricTarget(company_name="A", year=2025),
        status="ANSWERABLE",
        amount_krw=300,
        sources=(
            MetricSource(
                filing_id="filing:1",
                fact_ids=("fact:1",),
                event_ids=("event:1",),
                amount_krw=100,
                resolved_unit="원",
                description="1공장 증설",
            ),
            MetricSource(
                filing_id="filing:2",
                fact_ids=("fact:2",),
                event_ids=("event:2",),
                amount_krw=200,
                resolved_unit="원",
                description="2공장 증설",
            ),
        ),
    )
    second = MetricObservation(
        target=MetricTarget(company_name="B", year=2025),
        status="ANSWERABLE",
        amount_krw=50,
        sources=(
            MetricSource(
                filing_id="filing:3",
                fact_ids=("fact:3",),
                event_ids=("event:3",),
                amount_krw=50,
                resolved_unit="원",
                description="신규 설비",
            ),
        ),
    )
    result = MetricAnalysisResult(
        status="ANSWERABLE",
        metric=MetricName.FACILITY_INVESTMENT,
        operation=MetricOperation.RANKING,
        observations=(first, second),
        ranking=(
            MetricRank(position=1, observation=first),
            MetricRank(position=2, observation=second),
        ),
    )

    pack = build_metric_evidence_pack(
        FakeSession(),
        query="A와 B 중 2025년 설비투자 규모가 더 큰 기업은?",
        result=result,
    )
    rendered = render_evidence_pack(pack)

    assert len(pack.items) == 3
    assert pack.items[0].event_ids == ("event:1",)
    assert pack.items[1].event_ids == ("event:2",)
    assert pack.items[2].event_ids == ("event:3",)
    assert "이 공시의 신규시설투자 결정 금액: 100 원" in pack.items[0].content_text
    assert "이 공시의 신규시설투자 결정 금액: 200 원" in pack.items[1].content_text
    assert "300 원" not in pack.items[0].content_text
    assert "300 원" not in pack.items[1].content_text
    assert "회사·연도 합계는 DETERMINISTIC ANALYSIS의 관측값을 사용해야 함" in rendered
    assert "실제 집행액을 의미하지 않음" in rendered
    assert "derived_from: [E1],[E2],[E3]" in rendered
    assert "관측값: A 2025 300 원 [E1],[E2]" in rendered
    assert "관측값: B 2025 50 원 [E3]" in rendered
    assert "1위: A 2025 300 원 [E1],[E2]" in rendered
    assert "2위: B 2025 50 원 [E3]" in rendered
