"""Unified execution service for structured and hybrid disclosure answers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from disclosure_agent.domain.fundraising_analysis import FundraisingAnalysisResult
from disclosure_agent.extractors.fundraising import FundraisingInstrument
from disclosure_agent.llm.clova_embedding_client import ClovaEmbeddingClient
from disclosure_agent.llm.grounded_generation import generate_grounded_answer
from disclosure_agent.llm.hcx_client import HCX_MODEL, HcxAnswerResult, HcxClient
from disclosure_agent.llm.prompts import GROUNDING_SYSTEM_PROMPT, build_grounded_answer_prompt
from disclosure_agent.rendering.metric import render_metric_answer
from disclosure_agent.rendering.money import format_krw
from disclosure_agent.rendering.supply_contract import render_supply_contract_termination_answer
from disclosure_agent.retrieval.answer_query_planner import (
    AnswerExecutionMode,
    AnswerQueryPlan,
    plan_answer_query,
)
from disclosure_agent.retrieval.company_resolver import (
    match_query_companies,
    resolve_company,
)
from disclosure_agent.retrieval.evidence_pack import (
    EvidenceItem,
    EvidencePack,
    build_hybrid_evidence_pack,
)
from disclosure_agent.retrieval.fundraising_evidence import (
    INSTRUMENT_LABELS,
    build_fundraising_evidence_pack,
)
from disclosure_agent.retrieval.fundraising_query_resolver import resolve_fundraising_query_target
from disclosure_agent.retrieval.hybrid_search import HybridRetriever
from disclosure_agent.retrieval.metric_evidence import build_metric_evidence_pack
from disclosure_agent.retrieval.metric_query_planner import plan_metric_query
from disclosure_agent.retrieval.metric_target_resolver import (
    extract_query_years,
    resolve_metric_targets,
)
from disclosure_agent.retrieval.source_references import (
    SourceReference,
    build_source_references,
)
from disclosure_agent.retrieval.supply_contract_evidence import (
    build_supply_contract_evidence_pack,
)
from disclosure_agent.retrieval.supply_contract_query_resolver import (
    resolve_supply_contract_query_target,
)
from disclosure_agent.services.fundraising_analysis import FundraisingAnalysisService
from disclosure_agent.services.metric_analysis import MetricAnalysisService
from disclosure_agent.services.supply_contract_analysis import (
    find_terminated_contracts_formed_in_year,
)
from disclosure_agent.storage.db_models import SourceCompanyRow, SourceFilingRow

T = TypeVar("T")
_REPORT_TYPES = ("사업보고서", "반기보고서", "분기보고서")
_FUNDRAISING_REQUEST_TERMS = (
    (FundraisingInstrument.RIGHTS_ISSUE, "유상증자", None),
    (FundraisingInstrument.CONVERTIBLE_BOND, "전환사채", "CB"),
    (FundraisingInstrument.BOND_WITH_WARRANTS, "신주인수권부사채", "BW"),
    (FundraisingInstrument.EXCHANGEABLE_BOND, "교환사채", "EB"),
)


@dataclass(frozen=True, slots=True)
class AnswerResult:
    """Common result shape returned by every answer execution mode."""

    query: str
    plan: AnswerQueryPlan
    status: str
    answer: str
    generator: str
    evidence_pack: EvidencePack
    source_references: tuple[SourceReference, ...]
    model_result: HcxAnswerResult | None = None
    metadata: tuple[tuple[str, str], ...] = ()


def _empty_pack(query: str) -> EvidencePack:
    return EvidencePack(
        query=query,
        retrieval_status="NO_MATCH",
        items=(),
        total_chars=0,
    )


def _metadata(**values: object) -> tuple[tuple[str, str], ...]:
    return tuple((key, str(value)) for key, value in values.items())


def _generation_status(base_status: str, model_result: HcxAnswerResult) -> str:
    """Downgrade a safe grounding fallback so it is never reported as fully answerable."""

    if model_result.finish_reason == "grounding_exhausted":
        return "PARTIAL"
    return base_status


def _requested_fundraising_instruments(query: str) -> tuple[FundraisingInstrument, ...]:
    upper = query.upper()
    requested: list[FundraisingInstrument] = []
    for instrument, korean_name, abbreviation in _FUNDRAISING_REQUEST_TERMS:
        if korean_name in query:
            requested.append(instrument)
            continue
        if abbreviation and re.search(
            rf"(?<![A-Z]){abbreviation}(?![A-Z])",
            upper,
        ):
            requested.append(instrument)
    return tuple(dict.fromkeys(requested))


def _fundraising_event_ref(pack: EvidencePack, event_id: str) -> str:
    for item in pack.items:
        if event_id in item.event_ids:
            return f"[E{item.rank}]"
    return ""


def _render_fundraising_answer(
    query: str,
    analysis: FundraisingAnalysisResult,
    pack: EvidencePack,
) -> str:
    """Render structured fundraising answers directly from deterministic analysis."""

    requested = _requested_fundraising_instruments(query)
    selected = requested or tuple(
        category.instrument_type for category in analysis.categories
    )
    category_by_instrument = {
        category.instrument_type: category for category in analysis.categories
    }
    include_event_details = any(
        marker in query for marker in ("회차", "날짜", "발행일")
    )

    lines: list[str] = []
    has_absence = False
    for instrument in selected:
        category = category_by_instrument.get(instrument)
        if category is None:
            continue
        label = INSTRUMENT_LABELS[instrument]

        if category.status == "NO_MATCH":
            lines.append(f"{label}: 확인된 내역 없음")
            has_absence = True
            continue

        refs = "".join(
            _fundraising_event_ref(pack, event.event_id)
            for event in category.events
        )
        if category.status == "PARTIAL":
            lines.append(
                f"{label}: {category.event_count}건, 총 조달금액 확정 불가 "
                f"(확인 금액 {format_krw(category.known_amount_sum_krw)}) {refs}".rstrip()
            )
        else:
            lines.append(
                f"{label}: {category.event_count}건, 총 조달금액 "
                f"{format_krw(category.total_amount_krw)} {refs}".rstrip()
            )

        if include_event_details:
            for event in category.events:
                details: list[str] = []
                if event.series:
                    details.append(event.series)
                details.append(event.issue_date.isoformat())
                details.append(
                    format_krw(event.amount_krw)
                    if event.amount_krw is not None
                    else "금액 확인 불가"
                )
                if event.security_name and event.security_name not in details:
                    details.append(event.security_name)
                ref = _fundraising_event_ref(pack, event.event_id)
                suffix = f" {ref}" if ref else ""
                lines.append(f"- {' | '.join(details)}{suffix}")

    if has_absence:
        lines.append(
            "확인된 이벤트가 없다는 결과를 조달금액 0원으로 해석하지 않습니다."
        )
        if not any("[E" in line for line in lines):
            citations = "".join(f"[E{item.rank}]" for item in pack.items)
            if citations:
                lines.append(f"집계 범위 근거: {citations}")

    return "\n".join(lines)


def _render_requested_fundraising_absence(
    query: str,
    analysis: FundraisingAnalysisResult,
    pack: EvidencePack,
) -> str | None:
    """Compatibility helper for callers/tests that only want all-zero subsets."""

    requested = _requested_fundraising_instruments(query)
    if not requested:
        return None
    category_by_instrument = {
        category.instrument_type: category for category in analysis.categories
    }
    categories = tuple(category_by_instrument.get(instrument) for instrument in requested)
    if any(category is None for category in categories):
        return None
    if not all(category.status == "NO_MATCH" for category in categories if category):
        return None
    return _render_fundraising_answer(query, analysis, pack)


def _asks_actual_facility_execution(query: str) -> bool:
    compact = "".join(query.split())
    upper = compact.upper()
    investment_context = "투자" in compact or "CAPEX" in upper
    actual_marker = any(
        marker in compact
        for marker in ("실제로", "실제집행", "실제투자", "집행한", "집행액")
    )
    return investment_context and actual_marker


def _facility_decision_amount(item: EvidenceItem) -> int | None:
    match = re.search(r"^투자금액:\s*([\d,]+)원\s*$", item.content_text, re.MULTILINE)
    if match is None:
        return None
    return int(match.group(1).replace(",", ""))


def _render_facility_execution_semantic_answer(
    query: str,
    pack: EvidencePack,
) -> str | None:
    """Separate disclosed facility-investment decisions from actual cash execution."""

    if not _asks_actual_facility_execution(query):
        return None

    facility_items = tuple(
        item for item in pack.items if item.source_kind == "sql_facility_investment"
    )
    if not facility_items:
        return None

    amounts = tuple(_facility_decision_amount(item) for item in facility_items)
    if any(amount is None for amount in amounts):
        return None

    total = sum(amount for amount in amounts if amount is not None)
    years = extract_query_years(query)
    year_text = f"{years[0]}년에 " if len(years) == 1 else ""
    citations = "".join(f"[E{item.rank}]" for item in facility_items)

    return "\n".join(
        (
            (
                f"제공된 공시만으로 {year_text}실제 집행액이 {format_krw(total)}이었다고 "
                f"단정하기는 어렵습니다 {citations}."
            ),
            (
                f"확인되는 것은 {year_text}공시된 신규시설투자 결정 금액 합계 "
                f"{format_krw(total)}입니다 {citations}."
            ),
            (
                "신규시설투자 결정 금액은 투자 결정 내역이므로, 이 근거만으로 "
                "실제 집행액과 동일하다고 볼 수 없습니다."
            ),
        )
    )


def _round_robin(groups: tuple[tuple[T, ...], ...], *, limit: int) -> tuple[T, ...]:
    """Interleave ranked groups so one comparison side cannot consume every slot."""

    if limit < 1:
        raise ValueError("limit must be at least 1")
    merged: list[T] = []
    index = 0
    while len(merged) < limit:
        added = False
        for group in groups:
            if index >= len(group):
                continue
            merged.append(group[index])
            added = True
            if len(merged) >= limit:
                break
        if not added:
            break
        index += 1
    return tuple(merged)


def _dedupe_evidence_items(
    groups: tuple[tuple[EvidenceItem, ...], ...],
) -> tuple[EvidenceItem, ...]:
    items: list[EvidenceItem] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            if item.evidence_id in seen:
                continue
            seen.add(item.evidence_id)
            items.append(item)
    return tuple(items)


def _evidence_report_years(pack: EvidencePack) -> dict[int, int]:
    years: dict[int, int] = {}
    for item in pack.items:
        report_years = extract_query_years(item.report_name)
        if len(report_years) == 1:
            years[item.rank] = report_years[0]
    return years


def _grounding_prompt_for_query(query: str) -> str:
    extra_rules: list[str] = []
    compact = "".join(query.split())
    if "투자계획" in compact or "투자목적" in compact:
        extra_rules.append(
            "사용자가 투자 계획이나 투자 목적을 묻는 경우 배당, 자사주 매입, 주주환원은 "
            "투자 계획으로 분류하지 마세요. 사용자가 주주환원이나 자본배분을 함께 묻는 "
            "경우만 예외입니다."
        )
    years = extract_query_years(query)
    if len(years) > 1 and "사업보고서" in query:
        extra_rules.append(
            "연도별 사업보고서를 비교할 때 한 연도의 Evidence에 적힌 과거 실적이나 미래 계획을 "
            "다른 연도의 사업보고서 내용으로 재귀속하지 마세요. 각 연도 사실을 말하는 문장이나 항목은 같은 연도의 "
            "사업보고서 Evidence를 우선 인용하세요."
        )
    if not extra_rules:
        return GROUNDING_SYSTEM_PROMPT
    return "\n".join((GROUNDING_SYSTEM_PROMPT, "", "추가 질의별 규칙:", *extra_rules))


class AnswerService:
    """Dispatch one query to the safest supported answer engine."""

    def __init__(self, session: Session, *, api_key: str | None = None) -> None:
        self.session = session
        self.api_key = api_key.strip() if api_key and api_key.strip() else None

    def answer(
        self,
        query: str,
        *,
        fallback_company: str | None = None,
        fallback_year: int | None = None,
        filing_id: str | None = None,
        report_name: str | None = None,
        top_k: int = 5,
        candidate_k: int = 40,
        max_total_chars: int = 12000,
        max_completion_tokens: int = 1200,
    ) -> AnswerResult:
        """Plan and execute one answer request."""

        if not query.strip():
            raise ValueError("query must not be empty")
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        if candidate_k < top_k:
            raise ValueError("candidate_k must be greater than or equal to top_k")
        if candidate_k > 50:
            raise ValueError("candidate_k must be at most 50")

        plan = plan_answer_query(query)
        if plan.mode is AnswerExecutionMode.METRIC_STRUCTURED:
            return self._answer_metric(
                query,
                plan,
                fallback_company=fallback_company,
                fallback_year=fallback_year,
                max_total_chars=max_total_chars,
            )
        if plan.mode is AnswerExecutionMode.FUNDRAISING_STRUCTURED:
            return self._answer_fundraising(
                query,
                plan,
                fallback_company=fallback_company,
                fallback_year=fallback_year,
                max_total_chars=max_total_chars,
                max_completion_tokens=max_completion_tokens,
            )
        if plan.mode is AnswerExecutionMode.SUPPLY_CONTRACT_STRUCTURED:
            return self._answer_supply_contract(
                query,
                plan,
                fallback_company=fallback_company,
                fallback_year=fallback_year,
                max_total_chars=max_total_chars,
            )
        return self._answer_hybrid(
            query,
            plan,
            fallback_company=fallback_company,
            fallback_year=fallback_year,
            filing_id=filing_id,
            report_name=report_name,
            top_k=top_k,
            candidate_k=candidate_k,
            max_total_chars=max_total_chars,
            max_completion_tokens=max_completion_tokens,
        )

    def _generate(
        self,
        query: str,
        pack: EvidencePack,
        *,
        max_completion_tokens: int,
    ) -> HcxAnswerResult:
        if self.api_key is None:
            raise RuntimeError("HyperCLOVA X API key is required for grounded generation")
        prompt = build_grounded_answer_prompt(query, pack)
        with HcxClient(self.api_key) as client:
            return generate_grounded_answer(
                client,
                system_prompt=_grounding_prompt_for_query(query),
                user_prompt=prompt,
                evidence_count=len(pack.items),
                max_completion_tokens=max_completion_tokens,
                evidence_report_years=_evidence_report_years(pack),
            )

    def _unresolved(
        self,
        query: str,
        plan: AnswerQueryPlan,
        message: str,
        *,
        reason: str,
    ) -> AnswerResult:
        pack = _empty_pack(query)
        return AnswerResult(
            query=query,
            plan=plan,
            status="UNRESOLVED",
            answer=message,
            generator="deterministic",
            evidence_pack=pack,
            source_references=(),
            metadata=_metadata(reason=reason),
        )

    def _answer_metric(
        self,
        query: str,
        plan: AnswerQueryPlan,
        *,
        fallback_company: str | None,
        fallback_year: int | None,
        max_total_chars: int,
    ) -> AnswerResult:
        intent = plan_metric_query(query)
        if intent.metric is None or intent.operation is None:
            return self._unresolved(
                query,
                plan,
                "질의에서 계산할 지표나 연산을 확정할 수 없습니다.",
                reason="metric_intent_unresolved",
            )

        resolution = resolve_metric_targets(
            self.session,
            query=query,
            operation=intent.operation,
            fallback_company=fallback_company,
            fallback_year=fallback_year,
        )
        if resolution.status != "RESOLVED":
            return self._unresolved(
                query,
                plan,
                "질의에서 분석 대상 기업과 연도를 확정할 수 없습니다.",
                reason=resolution.reason or resolution.status,
            )

        analysis = MetricAnalysisService(self.session).analyze(
            metric=intent.metric,
            targets=resolution.targets,
            operation=intent.operation,
        )
        pack = build_metric_evidence_pack(
            self.session,
            query=query,
            result=analysis,
            max_total_chars=max_total_chars,
        )
        references = build_source_references(self.session, pack)
        metadata = _metadata(
            metric=intent.metric.value,
            operation=intent.operation.value,
            targets=len(resolution.targets),
        )

        if analysis.status != "ANSWERABLE" or pack.retrieval_status == "NO_MATCH":
            return AnswerResult(
                query=query,
                plan=plan,
                status=analysis.status,
                answer="제공된 공시에서 계산에 필요한 값을 모두 확인할 수 없습니다.",
                generator="deterministic",
                evidence_pack=pack,
                source_references=references,
                metadata=metadata,
            )

        return AnswerResult(
            query=query,
            plan=plan,
            status=analysis.status,
            answer=render_metric_answer(analysis, pack),
            generator="deterministic",
            evidence_pack=pack,
            source_references=references,
            metadata=metadata,
        )

    def _answer_fundraising(
        self,
        query: str,
        plan: AnswerQueryPlan,
        *,
        fallback_company: str | None,
        fallback_year: int | None,
        max_total_chars: int,
        max_completion_tokens: int,
    ) -> AnswerResult:
        target = resolve_fundraising_query_target(
            self.session,
            query=query,
            fallback_company=fallback_company,
            fallback_year=fallback_year,
        )
        if target.status != "RESOLVED" or target.company_name is None or target.year is None:
            return self._unresolved(
                query,
                plan,
                "질의에서 자금조달 대상 기업과 연도를 확정할 수 없습니다.",
                reason=target.reason or target.status,
            )

        analysis = FundraisingAnalysisService(self.session).analyze(
            company_name=target.company_name,
            year=target.year,
        )
        pack = build_fundraising_evidence_pack(
            self.session,
            query=query,
            result=analysis,
            max_total_chars=max_total_chars,
        )
        references = build_source_references(self.session, pack)
        metadata = _metadata(company=analysis.company_name, year=analysis.year)

        if analysis.status == "NO_MATCH":
            return AnswerResult(
                query=query,
                plan=plan,
                status=analysis.status,
                answer="제공된 공시에서 해당 연도의 자금조달 이벤트를 확인하지 못했습니다.",
                generator="deterministic",
                evidence_pack=pack,
                source_references=references,
                metadata=metadata,
            )
        if analysis.status == "PARTIAL":
            return AnswerResult(
                query=query,
                plan=plan,
                status=analysis.status,
                answer="제공된 공시에서 자금조달 금액을 모두 확정할 수 없습니다.",
                generator="deterministic",
                evidence_pack=pack,
                source_references=references,
                metadata=metadata,
            )

        return AnswerResult(
            query=query,
            plan=plan,
            status=analysis.status,
            answer=_render_fundraising_answer(query, analysis, pack),
            generator="deterministic",
            evidence_pack=pack,
            source_references=references,
            metadata=metadata,
        )

    def _answer_supply_contract(
        self,
        query: str,
        plan: AnswerQueryPlan,
        *,
        fallback_company: str | None,
        fallback_year: int | None,
        max_total_chars: int,
    ) -> AnswerResult:
        target = resolve_supply_contract_query_target(
            self.session,
            query=query,
            fallback_company=fallback_company,
            fallback_year=fallback_year,
        )
        if target.status != "RESOLVED" or target.company_name is None or target.year is None:
            return self._unresolved(
                query,
                plan,
                "질의에서 공급계약 대상 기업과 원계약 체결연도를 확정할 수 없습니다.",
                reason=target.reason or target.status,
            )

        analysis = find_terminated_contracts_formed_in_year(
            session=self.session,
            year=target.year,
            company_name=target.company_name,
        )
        pack = build_supply_contract_evidence_pack(
            self.session,
            query=query,
            result=analysis,
            max_total_chars=max_total_chars,
        )
        references = build_source_references(self.session, pack)
        metadata = _metadata(company=target.company_name, formation_year=target.year)

        if analysis.status == "NO_MATCH":
            answer = (
                f"제공된 공시에서 {target.company_name}의 {target.year}년 체결 계약 중 "
                "원계약과 확정적으로 연결된 해지 계약을 확인하지 못했습니다."
            )
        elif analysis.status == "PARTIAL":
            answer = (
                f"{target.company_name}의 {target.year}년 체결 계약 중 이후 해지된 계약은 "
                "확인되지만, 일부 계약의 정정공시 계보가 불완전해 최종 계약조건까지는 "
                "확정할 수 없습니다."
            )
        else:
            answer = render_supply_contract_termination_answer(analysis, pack)

        return AnswerResult(
            query=query,
            plan=plan,
            status=analysis.status,
            answer=answer,
            generator="deterministic",
            evidence_pack=pack,
            source_references=references,
            metadata=metadata,
        )

    def _resolve_hybrid_company(
        self,
        query: str,
        fallback_company: str | None,
    ) -> tuple[str | None, str | None]:
        mentions = match_query_companies(self.session, query)
        if len(mentions) == 1:
            return mentions[0].listed_name, None
        if len(mentions) > 1:
            return None, "multiple_companies_in_hybrid_query"
        if fallback_company:
            company = resolve_company(self.session, fallback_company)
            if company is not None:
                return company.listed_name, None
        return None, "company_unresolved"

    @staticmethod
    def _infer_report_type(query: str) -> str | None:
        for report_type in _REPORT_TYPES:
            if report_type in query:
                return report_type
        return None

    def _resolve_report_name(
        self,
        *,
        company_name: str,
        year: int,
        report_type: str,
    ) -> str | None:
        statement = (
            select(SourceFilingRow.report_name)
            .join(SourceCompanyRow, SourceCompanyRow.corp_code == SourceFilingRow.corp_code)
            .where(
                SourceCompanyRow.listed_name == company_name,
                SourceFilingRow.report_name.contains(report_type),
                SourceFilingRow.report_name.contains(str(year)),
            )
            .order_by(
                SourceFilingRow.receipt_date.desc(),
                SourceFilingRow.receipt_number.desc(),
            )
        )
        names = tuple(dict.fromkeys(self.session.scalars(statement).all()))
        return names[0] if names else None

    @staticmethod
    def _hybrid_years(
        query: str,
        *,
        fallback_year: int | None,
        report_name: str | None,
    ) -> tuple[int, ...]:
        years = extract_query_years(query)
        if years:
            return years
        if fallback_year is not None:
            return (fallback_year,)
        if report_name:
            report_years = extract_query_years(report_name)
            if report_years:
                return report_years
        return ()

    @staticmethod
    def _hybrid_year(
        query: str,
        *,
        fallback_year: int | None,
        report_name: str | None,
    ) -> int | None:
        years = AnswerService._hybrid_years(
            query,
            fallback_year=fallback_year,
            report_name=report_name,
        )
        return years[0] if len(years) == 1 else None

    def _missing_report_scope_result(
        self,
        *,
        query: str,
        plan: AnswerQueryPlan,
        company_name: str,
        years: tuple[int, ...],
        report_type: str,
        missing_years: tuple[int, ...],
    ) -> AnswerResult:
        missing_text = ", ".join(str(year) for year in missing_years)
        return AnswerResult(
            query=query,
            plan=plan,
            status="PARTIAL",
            answer=(
                f"제공된 공시에서 {company_name}의 {missing_text}년 {report_type}를 확인하지 못해 "
                "요청한 범위 전체를 비교하거나 정리할 수 없습니다."
            ),
            generator="deterministic",
            evidence_pack=_empty_pack(query),
            source_references=(),
            metadata=_metadata(
                company=company_name,
                year=",".join(str(year) for year in years) or "unfiltered",
                report_scope=report_type,
                missing_years=missing_text,
            ),
        )

    def _answer_hybrid(
        self,
        query: str,
        plan: AnswerQueryPlan,
        *,
        fallback_company: str | None,
        fallback_year: int | None,
        filing_id: str | None,
        report_name: str | None,
        top_k: int,
        candidate_k: int,
        max_total_chars: int,
        max_completion_tokens: int,
    ) -> AnswerResult:
        company_name, company_error = self._resolve_hybrid_company(query, fallback_company)
        if company_name is None:
            return self._unresolved(
                query,
                plan,
                "질의에서 분석 대상 기업을 하나로 확정할 수 없습니다.",
                reason=company_error or "company_unresolved",
            )

        years = self._hybrid_years(
            query,
            fallback_year=fallback_year,
            report_name=report_name,
        )
        report_type = None
        if filing_id is None and report_name is None:
            report_type = self._infer_report_type(query)

        if self.api_key is None:
            raise RuntimeError("HyperCLOVA X API key is required for semantic retrieval")

        query_vector = None
        if plan.route.uses_semantic:
            with ClovaEmbeddingClient(self.api_key) as client:
                query_vector = client.embed(query).vector

        retriever = HybridRetriever(self.session)
        multi_year = len(years) > 1 and filing_id is None and report_name is None

        if multi_year:
            retrievals = []
            missing_years: list[int] = []
            for scoped_year in years:
                scoped_report_name = None
                if report_type is not None:
                    scoped_report_name = self._resolve_report_name(
                        company_name=company_name,
                        year=scoped_year,
                        report_type=report_type,
                    )
                    if scoped_report_name is None:
                        missing_years.append(scoped_year)
                        continue
                retrievals.append(
                    retriever.retrieve(
                        query=query,
                        route=plan.route,
                        query_vector=query_vector,
                        company_name=company_name,
                        year=scoped_year,
                        filing_id=None,
                        report_name=scoped_report_name,
                        top_k=top_k,
                        candidate_k=candidate_k,
                    )
                )

            if missing_years and report_type is not None:
                return self._missing_report_scope_result(
                    query=query,
                    plan=plan,
                    company_name=company_name,
                    years=years,
                    report_type=report_type,
                    missing_years=tuple(missing_years),
                )

            structured_items = _dedupe_evidence_items(
                tuple(retrieval.structured_items for retrieval in retrievals)
            )
            semantic_hits = _round_robin(
                tuple(retrieval.semantic_hits for retrieval in retrievals),
                limit=top_k,
            )
            chars_per_item = min(3200, max(1, max_total_chars // top_k))
            pack = build_hybrid_evidence_pack(
                query,
                structured_items=structured_items,
                semantic_hits=semantic_hits,
                max_semantic_items=top_k,
                max_chars_per_item=chars_per_item,
                max_total_chars=max_total_chars,
            )
        else:
            year = years[0] if len(years) == 1 else None
            effective_report_name = report_name
            if (
                effective_report_name is None
                and report_type is not None
                and year is not None
            ):
                effective_report_name = self._resolve_report_name(
                    company_name=company_name,
                    year=year,
                    report_type=report_type,
                )
                if effective_report_name is None:
                    return self._missing_report_scope_result(
                        query=query,
                        plan=plan,
                        company_name=company_name,
                        years=years,
                        report_type=report_type,
                        missing_years=(year,),
                    )

            retrieval = retriever.retrieve(
                query=query,
                route=plan.route,
                query_vector=query_vector,
                company_name=company_name,
                year=year,
                filing_id=filing_id,
                report_name=effective_report_name,
                top_k=top_k,
                candidate_k=candidate_k,
            )
            pack = build_hybrid_evidence_pack(
                query,
                structured_items=retrieval.structured_items,
                semantic_hits=retrieval.semantic_hits,
                max_semantic_items=top_k,
                max_total_chars=max_total_chars,
            )

        references = build_source_references(self.session, pack)
        year_text = (
            ",".join(str(year) for year in years) if years else "unfiltered"
        )
        report_scope = report_name or report_type or "unfiltered"
        metadata = _metadata(
            company=company_name,
            year=year_text,
            report_scope=report_scope,
            rails=",".join(rail.value for rail in plan.route.rails),
        )

        if pack.retrieval_status == "NO_MATCH":
            return AnswerResult(
                query=query,
                plan=plan,
                status="NO_MATCH",
                answer="제공된 공시에서 확인되지 않는다.",
                generator="deterministic",
                evidence_pack=pack,
                source_references=references,
                metadata=metadata,
            )

        facility_execution_answer = _render_facility_execution_semantic_answer(
            query,
            pack,
        )
        if facility_execution_answer is not None:
            return AnswerResult(
                query=query,
                plan=plan,
                status="ANSWERABLE",
                answer=facility_execution_answer,
                generator="deterministic",
                evidence_pack=pack,
                source_references=references,
                metadata=metadata,
            )

        model_result = self._generate(
            query,
            pack,
            max_completion_tokens=max_completion_tokens,
        )
        return AnswerResult(
            query=query,
            plan=plan,
            status=_generation_status("ANSWERABLE", model_result),
            answer=model_result.content,
            generator=HCX_MODEL,
            evidence_pack=pack,
            source_references=references,
            model_result=model_result,
            metadata=metadata,
        )
