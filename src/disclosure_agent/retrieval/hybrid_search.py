"""Hybrid retrieval across structured SQL rails and semantic chunks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from disclosure_agent.retrieval.evidence_pack import EvidenceItem
from disclosure_agent.retrieval.query_router import QueryRoute, RetrievalRail
from disclosure_agent.retrieval.reranker import RerankedSemanticHit, rerank_semantic_hits
from disclosure_agent.storage.db_models import (
    DisclosureRow,
    SourceBlockRow,
    SourceFilingRow,
    SourceTableRow,
    SupplyContractEventRow,
    SupplyContractTerminationEventRow,
)
from disclosure_agent.storage.facility_investment_query_repository import (
    FacilityInvestmentQueryRepository,
)
from disclosure_agent.storage.fundraising_repository import FundraisingRepository
from disclosure_agent.storage.generic_fact_models import GenericFactRow
from disclosure_agent.storage.retrieval_embedding_repository import (
    RetrievalEmbeddingRepository,
)
from disclosure_agent.storage.revenue_repository import RevenueRepository
from disclosure_agent.storage.source_event_models import (
    FacilityInvestmentEventRow,
    SourceEventEvidenceRow,
)
from disclosure_agent.storage.supply_contract_query_repository import (
    SupplyContractQueryRepository,
)


@dataclass(frozen=True, slots=True)
class HybridSearchResult:
    """Combined retrieval result before evidence-pack budgeting."""

    route: QueryRoute
    structured_items: tuple[EvidenceItem, ...]
    semantic_hits: tuple[RerankedSemanticHit, ...]
    skipped_rails: tuple[RetrievalRail, ...]


def _money(value: int | None) -> str:
    return f"{value:,}원" if value is not None else "확인되지 않음"


def _date_text(value: date | None) -> str:
    return value.isoformat() if value is not None else "확인되지 않음"


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


class HybridRetriever:
    """Execute the deterministic SQL rails and semantic retrieval in one session."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def retrieve(
        self,
        *,
        query: str,
        route: QueryRoute,
        query_vector: tuple[float, ...] | None,
        company_name: str,
        year: int | None,
        filing_id: str | None = None,
        report_name: str | None = None,
        top_k: int = 5,
        candidate_k: int = 40,
    ) -> HybridSearchResult:
        if candidate_k < top_k:
            raise ValueError("candidate_k must be greater than or equal to top_k")
        if candidate_k > 50:
            raise ValueError("candidate_k must be at most 50")

        exact_scope = filing_id is not None or report_name is not None
        structured_items: list[EvidenceItem] = []
        skipped_rails: list[RetrievalRail] = []

        for rail in route.rails:
            if rail is RetrievalRail.SEMANTIC:
                continue
            if exact_scope and rail is not RetrievalRail.REVENUE:
                skipped_rails.append(rail)
                continue
            if year is None:
                skipped_rails.append(rail)
                continue

            if rail is RetrievalRail.REVENUE:
                items = self._revenue_items(
                    company_name=company_name,
                    year=year,
                    filing_id=filing_id,
                    report_name=report_name,
                )
            elif rail is RetrievalRail.FUNDRAISING:
                items = self._fundraising_items(
                    company_name=company_name,
                    year=year,
                )
            elif rail is RetrievalRail.FACILITY_INVESTMENT:
                items = self._facility_items(
                    company_name=company_name,
                    year=year,
                )
            elif rail is RetrievalRail.SUPPLY_CONTRACT:
                items = self._supply_contract_items(
                    company_name=company_name,
                    year=year,
                )
            else:
                items = ()
            structured_items.extend(items)

        semantic_hits: tuple[RerankedSemanticHit, ...] = ()
        if route.uses_semantic:
            if query_vector is None:
                raise ValueError("query_vector is required for semantic retrieval")
            candidates = RetrievalEmbeddingRepository(self.session).search(
                query_vector=query_vector,
                company_name=company_name,
                filing_id=filing_id,
                report_name=report_name,
                year=year if not exact_scope else None,
                top_k=candidate_k,
            )
            semantic_hits = rerank_semantic_hits(
                query,
                candidates,
                company_name=company_name,
                year=year,
                top_k=top_k,
            )

        return HybridSearchResult(
            route=route,
            structured_items=tuple(structured_items),
            semantic_hits=semantic_hits,
            skipped_rails=tuple(skipped_rails),
        )

    def _revenue_items(
        self,
        *,
        company_name: str,
        year: int,
        filing_id: str | None,
        report_name: str | None,
    ) -> tuple[EvidenceItem, ...]:
        result = RevenueRepository(self.session).query_annual_consolidated_revenue(
            company_name=company_name,
            year=year,
        )
        candidate = result.candidate
        if candidate is None:
            return ()
        if filing_id is not None and candidate.filing_id != filing_id:
            return ()
        if report_name is not None and candidate.report_name != report_name:
            return ()

        fact = self.session.get(GenericFactRow, candidate.fact_id)
        document_id = fact.document_id if fact is not None else None
        section_id = fact.section_id if fact is not None else None
        content = "\n".join(
            (
                f"status: {result.status}",
                f"연결기준 매출액: {_money(result.amount_krw)}",
                f"원문 값: {candidate.raw_value}",
                f"확정 단위: {result.resolved_unit or '확인되지 않음'}",
                f"항목: {candidate.label_text}",
                f"헤더: {candidate.header_text}",
                f"경로: {candidate.path_text}",
            )
        )
        return (
            EvidenceItem(
                evidence_id=f"sql:revenue:{candidate.fact_id}",
                source_kind="sql_revenue",
                rank=0,
                score=1.0,
                semantic_score=0.0,
                lexical_score=0.0,
                company_name=candidate.company_name,
                filing_id=candidate.filing_id,
                report_name=candidate.report_name,
                document_id=document_id,
                section_id=section_id,
                content_text=content,
                truncated=False,
                matched_terms=(),
                block_ids=(candidate.block_id,),
                table_ids=(candidate.table_id,),
                fact_ids=(candidate.fact_id,),
            ),
        )

    def _fundraising_items(
        self,
        *,
        company_name: str,
        year: int,
    ) -> tuple[EvidenceItem, ...]:
        rows = FundraisingRepository(self.session).query_events(
            company_name=company_name,
            year=year,
        )
        items = []
        for row in rows:
            table = self.session.get(SourceTableRow, row.representative_table_id)
            block = None
            if table is not None:
                block = self.session.get(SourceBlockRow, table.block_id)
            filing = self.session.get(SourceFilingRow, row.representative_filing_id)
            content = "\n".join(
                (
                    f"유형: {row.instrument_type}",
                    f"발행일: {_date_text(row.issue_date)}",
                    f"조달금액: {_money(row.amount_krw)}",
                    f"증권명: {row.security_name or '확인되지 않음'}",
                    f"회차: {row.series or '확인되지 않음'}",
                    f"발행방법: {row.issuance_method or '확인되지 않음'}",
                    f"근거 공시 반복횟수: {row.source_count}",
                )
            )
            items.append(
                EvidenceItem(
                    evidence_id=f"sql:fundraising:{row.event_id}",
                    source_kind="sql_fundraising",
                    rank=0,
                    score=1.0,
                    semantic_score=0.0,
                    lexical_score=0.0,
                    company_name=row.company_name,
                    filing_id=row.representative_filing_id,
                    report_name=filing.report_name if filing is not None else "",
                    document_id=table.document_id if table is not None else None,
                    section_id=block.section_id if block is not None else None,
                    content_text=content,
                    truncated=False,
                    matched_terms=(),
                    block_ids=(table.block_id,) if table is not None else (),
                    table_ids=(row.representative_table_id,),
                    event_ids=(row.event_id,),
                )
            )
        return tuple(items)

    def _facility_items(
        self,
        *,
        company_name: str,
        year: int,
    ) -> tuple[EvidenceItem, ...]:
        start = date(year, 1, 1)
        end = date(year + 1, 1, 1)
        rows = FacilityInvestmentQueryRepository(self.session).list_latest(
            company_names=(company_name,),
            decision_date_from=start,
            decision_date_to=end,
            limit=20,
        )
        items = []
        for row in rows:
            event = self.session.get(
                FacilityInvestmentEventRow,
                row.latest_filing_id,
            )
            if event is None:
                continue

            fact_ids = tuple(
                self.session.scalars(
                    select(SourceEventEvidenceRow.fact_id)
                    .where(SourceEventEvidenceRow.event_id == event.event_id)
                    .order_by(SourceEventEvidenceRow.attribute)
                ).all()
            )
            facts = ()
            if fact_ids:
                facts = tuple(
                    self.session.scalars(
                        select(GenericFactRow)
                        .where(GenericFactRow.fact_id.in_(fact_ids))
                        .order_by(GenericFactRow.fact_id)
                    ).all()
                )
            block_ids = _unique([fact.block_id for fact in facts])
            table_ids = _unique([fact.table_id for fact in facts])
            document_id = facts[0].document_id if facts else None
            section_id = facts[0].section_id if facts else None
            content = "\n".join(
                (
                    f"투자구분: {row.investment_type or '확인되지 않음'}",
                    f"투자대상: {row.investment_subject or '확인되지 않음'}",
                    f"투자금액: {_money(row.investment_amount_krw)}",
                    f"투자목적: {row.purpose or '확인되지 않음'}",
                    f"투자시작일: {_date_text(row.investment_start_date)}",
                    f"투자종료일: {_date_text(row.investment_end_date)}",
                    f"이사회결정일: {_date_text(row.decision_date)}",
                    f"정정계보상태: {row.lineage_status}",
                )
            )
            items.append(
                EvidenceItem(
                    evidence_id=f"sql:facility:{event.event_id}",
                    source_kind="sql_facility_investment",
                    rank=0,
                    score=1.0,
                    semantic_score=0.0,
                    lexical_score=0.0,
                    company_name=row.company_name,
                    filing_id=row.latest_filing_id,
                    report_name=row.report_name,
                    document_id=document_id,
                    section_id=section_id,
                    content_text=content,
                    truncated=False,
                    matched_terms=(),
                    block_ids=block_ids,
                    table_ids=table_ids,
                    fact_ids=fact_ids,
                    event_ids=(event.event_id,),
                )
            )
        return tuple(items)

    def _supply_contract_items(
        self,
        *,
        company_name: str,
        year: int,
    ) -> tuple[EvidenceItem, ...]:
        repository = SupplyContractQueryRepository(self.session)
        rows = repository.find_terminated_contracts_formed_in_year(
            year=year,
            company_name=company_name,
        )
        items = []
        for row in rows:
            formation = self.session.get(
                SupplyContractEventRow,
                row.latest_formation_filing_id,
            )
            termination = self.session.get(
                SupplyContractTerminationEventRow,
                row.termination_filing_id,
            )
            event_ids = tuple(
                event.event_id
                for event in (formation, termination)
                if event is not None
            )
            evidence = []
            for event_id in event_ids:
                evidence.extend(repository.evidence_for_event(event_id))
            table_ids = _unique([item.table_id for item in evidence])
            document_id = evidence[0].document_id if evidence else None
            filing = self.session.get(DisclosureRow, row.termination_filing_id)
            content = "\n".join(
                (
                    f"계약체결일: {row.contract_date.isoformat()}",
                    f"계약명: {row.contract_name or '확인되지 않음'}",
                    f"계약금액: {_money(row.contract_amount)}",
                    f"계약상대방: {row.counterparty or '확인되지 않음'}",
                    "후속상태: 해지",
                    f"해지일: {_date_text(row.termination_date)}",
                    f"해지사유: {row.termination_reason or '확인되지 않음'}",
                    f"원계약 공시: {row.root_filing_id}",
                    f"해지 공시: {row.termination_filing_id}",
                )
            )
            items.append(
                EvidenceItem(
                    evidence_id=f"sql:supply:{row.root_filing_id}",
                    source_kind="sql_supply_contract",
                    rank=0,
                    score=1.0,
                    semantic_score=0.0,
                    lexical_score=0.0,
                    company_name=row.company_name,
                    filing_id=row.termination_filing_id,
                    report_name=filing.report_name if filing is not None else "",
                    document_id=document_id,
                    section_id=None,
                    content_text=content,
                    truncated=False,
                    matched_terms=(),
                    block_ids=(),
                    table_ids=table_ids,
                    event_ids=event_ids,
                )
            )
        return tuple(items)
