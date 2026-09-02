"""Unified execution service for structured and hybrid disclosure answers."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from disclosure_agent.llm.clova_embedding_client import ClovaEmbeddingClient
from disclosure_agent.llm.grounded_generation import generate_grounded_answer
from disclosure_agent.llm.hcx_client import HCX_MODEL, HcxAnswerResult, HcxClient
from disclosure_agent.llm.prompts import GROUNDING_SYSTEM_PROMPT, build_grounded_answer_prompt
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
    EvidencePack,
    build_hybrid_evidence_pack,
)
from disclosure_agent.retrieval.fundraising_evidence import build_fundraising_evidence_pack
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
                max_completion_tokens=max_completion_tokens,
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
                system_prompt=GROUNDING_SYSTEM_PROMPT,
                user_prompt=prompt,
                evidence_count=len(pack.items),
                max_completion_tokens=max_completion_tokens,
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
        max_completion_tokens: int,
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

        model_result = self._generate(
            query,
            pack,
            max_completion_tokens=max_completion_tokens,
        )
        return AnswerResult(
            query=query,
            plan=plan,
            status=analysis.status,
            answer=model_result.content,
            generator=HCX_MODEL,
            evidence_pack=pack,
            source_references=references,
            model_result=model_result,
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

        model_result = self._generate(
            query,
            pack,
            max_completion_tokens=max_completion_tokens,
        )
        return AnswerResult(
            query=query,
            plan=plan,
            status=analysis.status,
            answer=model_result.content,
            generator=HCX_MODEL,
            evidence_pack=pack,
            source_references=references,
            model_result=model_result,
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
    def _hybrid_year(
        query: str,
        *,
        fallback_year: int | None,
        report_name: str | None,
    ) -> int | None:
        if fallback_year is not None:
            return fallback_year
        years = extract_query_years(query)
        if len(years) == 1:
            return years[0]
        if len(years) > 1:
            return None
        if report_name:
            report_years = extract_query_years(report_name)
            if len(report_years) == 1:
                return report_years[0]
        return None

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

        year = self._hybrid_year(
            query,
            fallback_year=fallback_year,
            report_name=report_name,
        )
        if self.api_key is None:
            raise RuntimeError("HyperCLOVA X API key is required for semantic retrieval")

        query_vector = None
        if plan.route.uses_semantic:
            with ClovaEmbeddingClient(self.api_key) as client:
                query_vector = client.embed(query).vector

        retrieval = HybridRetriever(self.session).retrieve(
            query=query,
            route=plan.route,
            query_vector=query_vector,
            company_name=company_name,
            year=year,
            filing_id=filing_id,
            report_name=report_name,
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
        metadata = _metadata(
            company=company_name,
            year=year or "unfiltered",
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

        model_result = self._generate(
            query,
            pack,
            max_completion_tokens=max_completion_tokens,
        )
        return AnswerResult(
            query=query,
            plan=plan,
            status="ANSWERABLE",
            answer=model_result.content,
            generator=HCX_MODEL,
            evidence_pack=pack,
            source_references=references,
            model_result=model_result,
            metadata=metadata,
        )
