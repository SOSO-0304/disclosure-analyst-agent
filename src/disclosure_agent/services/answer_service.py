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
_REPORT_SCOPE_YEARS = re.compile(
    r"(?P<years>(?:20\d{2}년(?:\s*(?:과|와|및|,|·|/)\s*)?)+)\s*"
    r"(?P<report>사업보고서|반기보고서|분기보고서)"
)
_QUANTIFIED_VALUE = re.compile(
    r"(?<![\d,])(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*"
    r"(?:조\s*원|억\s*원|만\s*원|천\s*원|원|%)"
)
_ATTRIBUTION_EVIDENCE_MARKERS = (
    "기여",
    "기여도",
    "증가분",
    "증가 요인",
    "로 인해",
    "때문",
    "덕분",
    "영향으로",
)


def _report_scoped_years(query: str, report_type: str) -> tuple[int, ...]:
    """Extract only years syntactically attached to the requested report type."""

    years: list[int] = []
    for match in _REPORT_SCOPE_YEARS.finditer(query):
        if match.group("report") != report_type:
            continue
        years.extend(
            int(year)
            for year in re.findall(r"20\d{2}", match.group("years"))
        )
    return tuple(dict.fromkeys(years))


def _asks_predictive_probability(query: str) -> bool:
    """Return whether the user asks for a numeric probability of a future outcome."""

    compact = "".join(query.split())
    probability_request = any(term in compact for term in ("확률", "가능성", "성공률"))
    predictive_markers = (
        "받을확률",
        "될확률",
        "성공할확률",
        "달성할확률",
        "낼확률",
        "오를확률",
        "내릴확률",
        "받을가능성",
        "될가능성",
        "성공할가능성",
        "달성할가능성",
        "낼가능성",
    )
    return probability_request and any(marker in compact for marker in predictive_markers)


def _render_predictive_probability_limit_answer(query: str, pack: EvidencePack) -> str | None:
    """Do not turn generic disclosure statistics into a subject-specific forecast."""

    if not _asks_predictive_probability(query):
        return None
    return (
        "제공된 공시만으로 질문 대상의 미래 결과 확률을 객관적인 퍼센트로 "
        "계산할 수 없습니다. 사업보고서에 일반적인 산업 통계나 개발 성공률이 "
        "기재되어 있더라도 이를 특정 기업·사업·제품의 미래 결과 확률로 그대로 "
        "적용할 수 없습니다."
    )


def _asks_future_market_price(query: str) -> bool:
    """Return whether the user asks to calculate or predict a future market price."""

    compact = "".join(query.split())
    market_price = any(term in compact for term in ("주가", "주식가격", "주식값"))
    future_or_prediction = bool(re.search(r"20\d{2}년", compact)) and any(
        marker in compact
        for marker in ("얼마가될", "예측", "전망", "계산", "될지", "오를", "내릴")
    )
    return market_price and future_or_prediction


def _render_future_market_price_limit_answer(query: str) -> str | None:
    """Reject deriving future stock prices from disclosure documents."""

    if not _asks_future_market_price(query):
        return None
    return (
        "제공된 공시만으로 미래 주가를 계산하거나 확정적으로 예측할 수 없습니다. "
        "사업보고서는 기업의 공시 정보를 제공하지만, 특정 미래 시점의 시장가격을 "
        "결정하는 값을 직접 제공하지 않습니다."
    )


def _asks_quantified_attribution(query: str) -> bool:
    """Return whether the query requests a causal contribution amount or ratio."""

    compact = "".join(query.split())
    if "기여" not in compact:
        return False
    return any(
        marker in compact
        for marker in ("금액", "얼마", "비율", "기여도", "정확히", "계산")
    )


def _has_explicit_quantified_attribution(pack: EvidencePack) -> bool:
    """Require causal wording and a numeric value in the same evidence segment."""

    for item in pack.items:
        for raw in re.split(r"(?<=[.!?])\s+|\n+", item.content_text):
            segment = " ".join(raw.split()).strip()
            if not segment or _QUANTIFIED_VALUE.search(segment) is None:
                continue
            if any(marker in segment for marker in _ATTRIBUTION_EVIDENCE_MARKERS):
                return True
    return False


def _render_quantified_attribution_limit_answer(
    query: str,
    pack: EvidencePack,
) -> str | None:
    """Reject deriving contribution values from overall performance figures."""

    if not _asks_quantified_attribution(query):
        return None
    if _has_explicit_quantified_attribution(pack):
        return None
    return (
        "제공된 공시에서 요청한 요인이 실적에 기여한 금액 또는 비율을 직접 분리해 "
        "확인할 수 없습니다. 전체 매출액이나 관련 사건의 발생 사실만으로 해당 요인의 "
        "기여 금액을 계산할 수 없습니다."
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
    """Interleave structured evidence groups while removing duplicate evidence."""

    items: list[EvidenceItem] = []
    seen: set[str] = set()
    index = 0
    while True:
        added = False
        for group in groups:
            if index >= len(group):
                continue
            item = group[index]
            added = True
            if item.evidence_id in seen:
                continue
            seen.add(item.evidence_id)
            items.append(item)
        if not added:
            break
        index += 1
    return tuple(items)


def _evidence_report_years(pack: EvidencePack) -> dict[int, int]:
    years: dict[int, int] = {}
    for item in pack.items:
        report_years = extract_query_years(item.report_name)
        if len(report_years) == 1:
            years[item.rank] = report_years[0]
    return years


_COMPARISON_STRATEGY_MARKERS = (
    "HBM",
    "DDR5",
    "DRAM",
    "NAND",
    "AI",
    "서버",
    "고부가",
    "메모리",
    "반도체",
    "Foundry",
    "System LSI",
    "CAPA",
    "확대",
    "강화",
    "대응",
    "집중",
    "선도",
)
_FALLBACK_SEGMENT_MAX_CHARS = 320


def _bounded_fallback_parts(raw: str) -> tuple[str, ...]:
    """Split noisy disclosure text into short extractive fallback candidates."""

    normalized = " ".join(raw.split()).strip()
    if not normalized:
        return ()

    primary = re.split(r"(?<=[.!?])\s*|[□■▪▶]+", normalized)
    parts: list[str] = []
    for piece in primary:
        piece = piece.strip(" -·")
        if not piece:
            continue
        secondary = re.split(r"\s+-\s+", piece)
        for candidate in secondary:
            candidate = candidate.strip(" -·")
            if not candidate:
                continue
            while len(candidate) > _FALLBACK_SEGMENT_MAX_CHARS:
                cut = candidate.rfind(" ", 0, _FALLBACK_SEGMENT_MAX_CHARS + 1)
                if cut < _FALLBACK_SEGMENT_MAX_CHARS // 2:
                    cut = _FALLBACK_SEGMENT_MAX_CHARS
                head = candidate[:cut].strip()
                if head:
                    parts.append(head)
                candidate = candidate[cut:].strip()
            if candidate:
                parts.append(candidate)
    return tuple(parts)


def _substantive_evidence_segments(item: EvidenceItem) -> tuple[str, ...]:
    segments: list[str] = []
    for raw in re.split(r"\n+", item.content_text):
        for text in _bounded_fallback_parts(raw):
            if any(
                text.startswith(prefix)
                for prefix in ("회사:", "공시:", "문서:", "섹션:", "출처:")
            ):
                continue
            if len(text) < 8:
                continue
            segments.append(text)
    return tuple(segments)


def _comparison_segment_score(text: str, query: str) -> tuple[int, int]:
    marker_score = sum(
        1 for marker in _COMPARISON_STRATEGY_MARKERS if marker.lower() in text.lower()
    )
    query_terms = tuple(
        term
        for term in re.findall(r"[A-Za-z0-9가-힣]+", query)
        if len(term) >= 2
        and term not in {"삼성전자", "사업보고서", "기준", "어떻게", "달라졌는지", "비교해줘"}
    )
    query_score = sum(1 for term in query_terms if term in text)
    return marker_score + query_score, -len(text)


def _year_comparison_snippets(
    query: str,
    *,
    year: int,
    pack: EvidencePack,
    limit: int = 1,
) -> tuple[tuple[str, int], ...]:
    candidates: list[tuple[tuple[int, int], int, str]] = []
    for item in pack.items:
        report_years = extract_query_years(item.report_name)
        if report_years != (year,):
            continue
        for segment in _substantive_evidence_segments(item):
            candidates.append(
                (_comparison_segment_score(segment, query), item.rank, segment)
            )

    candidates.sort(key=lambda row: row[0], reverse=True)
    selected: list[tuple[str, int]] = []
    seen: set[str] = set()
    for _, rank, segment in candidates:
        if segment in seen:
            continue
        seen.add(segment)
        selected.append((segment, rank))
        if len(selected) >= limit:
            break
    return tuple(selected)


_GENERIC_COMPARISON_MARKERS = (
    "전략",
    "성장",
    "AI",
    "투자",
    "제품",
    "서비스",
    "시장",
    "사업",
    "기술",
    "고객",
    "확대",
    "강화",
    "계획",
    "추진",
    "대응",
)


def _scope_comparison_segment_score(
    text: str,
    query: str,
    *,
    company_names: tuple[str, ...],
) -> tuple[int, int]:
    marker_score = sum(
        1
        for marker in _GENERIC_COMPARISON_MARKERS
        if marker.lower() in text.lower()
    )
    stopwords = {
        "사업보고서",
        "기준",
        "비교",
        "비교해서",
        "설명해줘",
        "주요",
        "핵심",
        "어떻게",
        "달랐는지",
        "차이",
        "각",
        "기업",
    }
    stopwords.update(company_names)
    query_terms = tuple(
        term
        for term in re.findall(r"[A-Za-z0-9가-힣]+", query)
        if len(term) >= 2 and term not in stopwords
    )
    query_score = sum(1 for term in query_terms if term.lower() in text.lower())
    return marker_score + query_score, -len(text)


def _company_year_comparison_snippet(
    query: str,
    *,
    company_name: str,
    year: int | None,
    pack: EvidencePack,
) -> tuple[str, int] | None:
    companies = tuple(dict.fromkeys(item.company_name for item in pack.items))
    candidates: list[tuple[tuple[int, int], int, str]] = []
    for item in pack.items:
        if item.company_name != company_name:
            continue
        if year is not None and extract_query_years(item.report_name) != (year,):
            continue
        for segment in _substantive_evidence_segments(item):
            candidates.append(
                (
                    _scope_comparison_segment_score(
                        segment,
                        query,
                        company_names=companies,
                    ),
                    item.rank,
                    segment,
                )
            )

    if not candidates:
        return None
    candidates.sort(key=lambda row: row[0], reverse=True)
    _, rank, segment = candidates[0]
    return segment, rank


def _render_multi_scope_comparison_fallback(
    query: str,
    pack: EvidencePack,
) -> str | None:
    """Render company-aware extractive fallback for multi-company report comparisons."""

    compact = "".join(query.split())
    if "사업보고서" not in query or not any(
        marker in compact for marker in ("비교", "차이", "달랐", "다른")
    ):
        return None

    company_names = tuple(dict.fromkeys(item.company_name for item in pack.items))
    if len(company_names) <= 1:
        return None

    years = extract_query_years(query)
    scoped_years: tuple[int | None, ...] = years if years else (None,)
    lines: list[str] = []
    used_refs: list[str] = []

    for company_name in company_names:
        lines.append(f"{company_name}:")
        for year in scoped_years:
            selected = _company_year_comparison_snippet(
                query,
                company_name=company_name,
                year=year,
                pack=pack,
            )
            if selected is None:
                return None
            text, rank = selected
            ref = f"[E{rank}]"
            used_refs.append(ref)
            prefix = f"- {year}년: " if year is not None else "- "
            lines.append(f"{prefix}{text} {ref}")

    citations = "".join(dict.fromkeys(used_refs))
    lines.append(
        "비교하면, 각 기업은 위 사업보고서 근거에서 확인되는 서로 다른 사업 전략과 "
        f"강조점을 보이고 있습니다 {citations}."
    )
    return "\n".join(lines)


def _render_multi_year_comparison_fallback(
    query: str,
    pack: EvidencePack,
) -> str | None:
    """Render a grounded extractive fallback for flaky multi-year report comparisons."""

    years = extract_query_years(query)
    compact = "".join(query.split())
    comparison_query = len(years) > 1 and any(
        marker in compact for marker in ("비교", "달라졌", "변화", "차이")
    )
    if not comparison_query or "사업보고서" not in query:
        return None

    year_blocks: list[str] = []
    for year in years:
        snippets = _year_comparison_snippets(query, year=year, pack=pack)
        if not snippets:
            return None
        details = " ".join(
            f"{text} [E{rank}]" for text, rank in snippets
        )
        year_blocks.append(f"{year}년 공시 핵심: {details}")

    if len(year_blocks) < 2:
        return None
    return "\n".join(year_blocks)


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
                top_k=top_k,
                candidate_k=candidate_k,
            )
        if plan.mode is AnswerExecutionMode.SUPPLY_CONTRACT_STRUCTURED:
            return self._answer_supply_contract(
                query,
                plan,
                fallback_company=fallback_company,
                fallback_year=fallback_year,
                max_total_chars=max_total_chars,
                max_completion_tokens=max_completion_tokens,
                top_k=top_k,
                candidate_k=candidate_k,
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
        system_prompt = _grounding_prompt_for_query(query)
        companies = tuple(dict.fromkeys(item.company_name for item in pack.items))
        if len(companies) > 1:
            system_prompt = "\n".join(
                (
                    system_prompt,
                    "",
                    "다중기업 비교 규칙:",
                    f"- 비교 대상 기업은 {', '.join(companies)}입니다.",
                    "- 각 기업에 대한 사실은 반드시 같은 기업의 Evidence에 근거해 서술하세요.",
                    "- 한 기업의 제품, 전략, 수치, 계획을 다른 기업의 사실로 재귀속하지 마세요.",
                    "- 비교 결론은 양쪽 기업의 근거가 모두 확보된 항목에 대해서만 제시하세요.",
                )
            )
        with HcxClient(self.api_key) as client:
            return generate_grounded_answer(
                client,
                system_prompt=system_prompt,
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
        top_k: int,
        candidate_k: int,
    ) -> AnswerResult:
        target = resolve_fundraising_query_target(
            self.session,
            query=query,
            fallback_company=fallback_company,
            fallback_year=fallback_year,
        )
        if target.status != "RESOLVED" or target.company_name is None or target.year is None:
            if target.reason in {"multiple_companies", "multiple_years"}:
                hybrid_plan = AnswerQueryPlan(
                    mode=AnswerExecutionMode.HYBRID_GROUNDED,
                    route=plan.route,
                    reason="multi-target fundraising comparison requires grounded synthesis",
                )
                return self._answer_hybrid(
                    query,
                    hybrid_plan,
                    fallback_company=fallback_company,
                    fallback_year=fallback_year,
                    filing_id=None,
                    report_name=None,
                    top_k=top_k,
                    candidate_k=candidate_k,
                    max_total_chars=max_total_chars,
                    max_completion_tokens=max_completion_tokens,
                )
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
        max_completion_tokens: int,
        top_k: int,
        candidate_k: int,
    ) -> AnswerResult:
        target = resolve_supply_contract_query_target(
            self.session,
            query=query,
            fallback_company=fallback_company,
            fallback_year=fallback_year,
        )
        if target.status != "RESOLVED" or target.company_name is None or target.year is None:
            if target.reason in {"multiple_companies", "multiple_years"}:
                hybrid_plan = AnswerQueryPlan(
                    mode=AnswerExecutionMode.HYBRID_GROUNDED,
                    route=plan.route,
                    reason="multi-target supply-contract comparison requires grounded synthesis",
                )
                return self._answer_hybrid(
                    query,
                    hybrid_plan,
                    fallback_company=fallback_company,
                    fallback_year=fallback_year,
                    filing_id=None,
                    report_name=None,
                    top_k=top_k,
                    candidate_k=candidate_k,
                    max_total_chars=max_total_chars,
                    max_completion_tokens=max_completion_tokens,
                )
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

    def _resolve_hybrid_companies(
        self,
        query: str,
        fallback_company: str | None,
    ) -> tuple[tuple[str, ...], str | None]:
        mentions = match_query_companies(self.session, query)
        if mentions:
            return tuple(company.listed_name for company in mentions), None
        if fallback_company:
            company = resolve_company(self.session, fallback_company)
            if company is not None:
                return (company.listed_name,), None
        return (), "company_unresolved"

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
        report_type = AnswerService._infer_report_type(query)
        if report_type is not None:
            scoped_years = _report_scoped_years(query, report_type)
            if scoped_years:
                return scoped_years

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
        requested_scopes: tuple[tuple[str, int], ...],
        report_type: str,
        missing_scopes: tuple[tuple[str, int], ...],
    ) -> AnswerResult:
        missing_text = ", ".join(
            f"{company_name} {year}년 {report_type}"
            for company_name, year in missing_scopes
        )
        companies = tuple(dict.fromkeys(company for company, _ in requested_scopes))
        years = tuple(dict.fromkeys(year for _, year in requested_scopes))
        return AnswerResult(
            query=query,
            plan=plan,
            status="PARTIAL",
            answer=(
                f"제공된 공시에서 {missing_text}를 확인하지 못해 "
                "요청한 범위 전체를 비교하거나 정리할 수 없습니다."
            ),
            generator="deterministic",
            evidence_pack=_empty_pack(query),
            source_references=(),
            metadata=_metadata(
                company=",".join(companies),
                year=",".join(str(year) for year in years) or "unfiltered",
                report_scope=report_type,
                missing_scopes=missing_text,
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
        company_names, company_error = self._resolve_hybrid_companies(
            query,
            fallback_company,
        )
        if not company_names:
            return self._unresolved(
                query,
                plan,
                "질의에서 분석 대상 기업을 확정할 수 없습니다.",
                reason=company_error or "company_unresolved",
            )

        if filing_id is not None and len(company_names) > 1:
            return self._unresolved(
                query,
                plan,
                "하나의 공시 식별자로 여러 기업을 동시에 비교할 수 없습니다.",
                reason="filing_id_with_multiple_companies",
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
        scoped_years: tuple[int | None, ...] = years if years else (None,)
        requested_scopes = tuple(
            (company_name, scoped_year)
            for company_name in company_names
            for scoped_year in scoped_years
            if scoped_year is not None
        )

        retrievals = []
        missing_scopes: list[tuple[str, int]] = []
        for company_name in company_names:
            for scoped_year in scoped_years:
                effective_report_name = report_name
                if (
                    effective_report_name is None
                    and report_type is not None
                    and scoped_year is not None
                ):
                    effective_report_name = self._resolve_report_name(
                        company_name=company_name,
                        year=scoped_year,
                        report_type=report_type,
                    )
                    if effective_report_name is None:
                        missing_scopes.append((company_name, scoped_year))
                        continue

                retrievals.append(
                    retriever.retrieve(
                        query=query,
                        route=plan.route,
                        query_vector=query_vector,
                        company_name=company_name,
                        year=scoped_year,
                        filing_id=filing_id,
                        report_name=effective_report_name,
                        report_type=report_type if effective_report_name is None else None,
                        top_k=top_k,
                        candidate_k=candidate_k,
                    )
                )

        if missing_scopes and report_type is not None:
            return self._missing_report_scope_result(
                query=query,
                plan=plan,
                requested_scopes=requested_scopes,
                report_type=report_type,
                missing_scopes=tuple(missing_scopes),
            )

        structured_items = _dedupe_evidence_items(
            tuple(retrieval.structured_items for retrieval in retrievals)
        )
        semantic_limit = max(top_k, len(retrievals))
        semantic_hits = _round_robin(
            tuple(retrieval.semantic_hits for retrieval in retrievals),
            limit=semantic_limit,
        ) if retrievals else ()

        chars_per_item = min(
            3200,
            max(1, max_total_chars // max(1, semantic_limit)),
        )
        pack = build_hybrid_evidence_pack(
            query,
            structured_items=structured_items,
            semantic_hits=semantic_hits,
            max_semantic_items=semantic_limit,
            max_chars_per_item=chars_per_item,
            max_total_chars=max_total_chars,
        )

        references = build_source_references(self.session, pack)
        year_text = ",".join(str(year) for year in years) if years else "unfiltered"
        report_scope = report_name or report_type or "unfiltered"
        metadata = _metadata(
            company=",".join(company_names),
            company_count=len(company_names),
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

        future_market_price_answer = _render_future_market_price_limit_answer(query)
        if future_market_price_answer is not None:
            return AnswerResult(
                query=query,
                plan=plan,
                status="PARTIAL",
                answer=future_market_price_answer,
                generator="deterministic",
                evidence_pack=pack,
                source_references=references,
                metadata=metadata + _metadata(limitation="future_market_price"),
            )

        predictive_probability_answer = _render_predictive_probability_limit_answer(
            query,
            pack,
        )
        if predictive_probability_answer is not None:
            return AnswerResult(
                query=query,
                plan=plan,
                status="PARTIAL",
                answer=predictive_probability_answer,
                generator="deterministic",
                evidence_pack=pack,
                source_references=references,
                metadata=metadata + _metadata(limitation="predictive_probability"),
            )

        quantified_attribution_answer = _render_quantified_attribution_limit_answer(
            query,
            pack,
        )
        if quantified_attribution_answer is not None:
            return AnswerResult(
                query=query,
                plan=plan,
                status="PARTIAL",
                answer=quantified_attribution_answer,
                generator="deterministic",
                evidence_pack=pack,
                source_references=references,
                metadata=metadata + _metadata(limitation="quantified_attribution"),
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
        if model_result.finish_reason == "grounding_exhausted":
            comparison_fallback = _render_multi_scope_comparison_fallback(query, pack)
            fallback_kind = "multi_scope_comparison"
            if comparison_fallback is None:
                comparison_fallback = _render_multi_year_comparison_fallback(query, pack)
                fallback_kind = "multi_year_comparison"
            if comparison_fallback is not None:
                return AnswerResult(
                    query=query,
                    plan=plan,
                    status="ANSWERABLE",
                    answer=comparison_fallback,
                    generator="deterministic_fallback",
                    evidence_pack=pack,
                    source_references=references,
                    model_result=model_result,
                    metadata=metadata + _metadata(fallback=fallback_kind),
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
