"""Grounded HCX generation with bounded citation repair."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Protocol

from disclosure_agent.llm.hcx_client import HcxAnswerResult

_EVIDENCE_CITATION = re.compile(r"\[E(\d+)\]")
_MISSING_EVIDENCE_CITATION = "[EVIDENCE_CITATION_REQUIRED]"
_INTERNAL_CITATION = re.compile(
    r"\[(?:DETERMINISTIC[ _]ANALYSIS|DETERMINISTIC_RESULT|DERIVED_FROM|ANALYSIS_TYPE)\]",
    re.IGNORECASE,
)
_EMPTY_FUNDRAISING_CATEGORY = re.compile(
    r"^유형:\s*(?P<label>.+?)\s*\|\s*확인된 이벤트 0건\s*\|",
    re.MULTILINE,
)
_POSITIVE_FUNDRAISING_CATEGORY = re.compile(
    r"^유형:\s*(?P<label>.+?)\s*\|\s*[1-9]\d*건\s*\|",
    re.MULTILINE,
)
_REPORT_CONTEXT = re.compile(r"(?P<year>20\d{2})년\s*사업보고서")
_USER_QUESTION = re.compile(
    r"사용자 질문:\s*\n(?P<query>.*?)(?:\n\s*\n|$)",
    re.DOTALL,
)
_EVIDENCE_COMPANY = re.compile(
    r"(?m)^company=(?P<company>.+?)\s+report="
)
_MONEY_LITERAL = re.compile(
    r"(?<![\d,])(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*"
    r"(?:조\s*원|억\s*원|만\s*원|천\s*원|원)"
)
_TABLE_UNIT = re.compile(r"단위\s*[:：]\s*(조\s*원|억\s*원|만\s*원|천\s*원|원)")
_GROUPED_NUMBER = re.compile(r"(?<![\d,])\d{1,3}(?:,\d{3})+(?![\d,])")
_DOTTED_PERIOD = re.compile(
    r"(?P<start_year>20\d{2})\.(?P<start_month>\d{1,2})\s*[~～-]\s*"
    r"(?:(?P<end_year>20\d{2})\.)?(?P<end_month>\d{1,2})"
)
_KOREAN_PERIOD = re.compile(
    r"(?P<start_year>20\d{2})년\s*(?P<start_month>\d{1,2})월부터\s*"
    r"(?:(?P<end_year>20\d{2})년\s*)?(?P<end_month>\d{1,2})월까지"
)
_KOREAN_QUARTER = re.compile(r"(?P<year>20\d{2})년\s*(?P<quarter>[1-4])분기")
_COMPLETED_MARKERS = (
    "이루어졌",
    "완료했",
    "완료하였",
    "취득 완료",
    "실시했",
    "실시하였",
    "집행했",
    "집행하였",
    "투자했",
    "투자하였",
)
_FUTURE_MARKERS = (
    "계획",
    "예정",
    "진행할",
    "진행될",
    "추진할",
    "투자할",
    "집행할",
    "투자합니다",
    "집행합니다",
)
_ABSENCE_PHRASES = (
    "확인된 내역 없음",
    "확인된 이벤트가 없습니다",
    "확인된 이벤트 없음",
    "확인되지 않습니다",
)
_NEUTRAL_INVESTMENT_HEADING = "공시에서 확인되는 투자 관련 내용은 다음과 같습니다:"
_MEMORY_ONLY_SCOPE_MARKERS = (
    "메모리",
    "DRAM",
    "NAND",
    "HBM",
    "DDR",
    "GDDR",
    "LPDDR",
    "SOCAMM",
    "SSD",
)


_BUSINESS_UNIT_ALIASES = {
    "DX": (
        "dx 부문",
        "device experience",
        "mobile experience",
        "mx(",
        "영상디스플레이",
        "생활가전",
    ),
    "DS": (
        "ds 부문",
        "device solutions",
        "메모리 사업",
        "foundry",
        "system lsi",
        "시스템 반도체",
    ),
    "SDC": (
        "sdc",
        "삼성디스플레이",
        "display panel",
        "디스플레이 패널",
        "oled",
    ),
    "HARMAN": ("harman", "하만"),
}


class GroundedAnswerClient(Protocol):
    """Minimal client surface required for grounded generation."""

    def answer(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        max_completion_tokens: int = 1200,
    ) -> HcxAnswerResult: ...


def invalid_citation_tokens(content: str, *, evidence_count: int) -> tuple[str, ...]:
    """Return missing, internal, or out-of-range citation tokens."""

    invalid: list[str] = []
    for match in _INTERNAL_CITATION.finditer(content):
        token = match.group(0)
        if token not in invalid:
            invalid.append(token)

    evidence_matches = tuple(_EVIDENCE_CITATION.finditer(content))
    for match in evidence_matches:
        number = int(match.group(1))
        if number < 1 or number > evidence_count:
            token = match.group(0)
            if token not in invalid:
                invalid.append(token)

    if evidence_count > 0 and not evidence_matches:
        invalid.append(_MISSING_EVIDENCE_CITATION)
    return tuple(invalid)


def invalid_report_year_citations(
    content: str,
    *,
    evidence_report_years: dict[int, int],
) -> tuple[str, ...]:
    """Reject report-year misattribution without confusing content years with filing years."""

    invalid: list[str] = []
    distinct_report_years = set(evidence_report_years.values())
    segments = re.split(r"(?<=[.!?])\s+|\n+", content)
    for segment in segments:
        if not segment.strip():
            continue

        # An explicit "YYYY년 사업보고서" always identifies report scope.
        context_years = {
            int(match.group("year"))
            for match in _REPORT_CONTEXT.finditer(segment)
        }

        # In a genuine multi-report-year comparison, a bare "YYYY년에는" also
        # acts as attribution. In a single-report answer it may instead be a
        # future/past period discussed inside that report, so do not reject it.
        if not context_years and len(distinct_report_years) > 1:
            context_years = {
                int(match.group("year"))
                for match in re.finditer(r"(?P<year>20\d{2})년", segment)
            }

        if len(context_years) != 1:
            continue

        context_year = next(iter(context_years))
        for match in _EVIDENCE_CITATION.finditer(segment):
            number = int(match.group(1))
            evidence_year = evidence_report_years.get(number)
            if evidence_year is None or evidence_year == context_year:
                continue
            token = match.group(0)
            if token not in invalid:
                invalid.append(token)
    return tuple(invalid)


def _normalize_money_token(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _supported_money_literals(user_prompt: str) -> set[str]:
    supported = {
        _normalize_money_token(match.group(0))
        for match in _MONEY_LITERAL.finditer(user_prompt)
    }
    table_units = {
        _normalize_money_token(match.group(1))
        for match in _TABLE_UNIT.finditer(user_prompt)
    }
    grouped_numbers = {match.group(0) for match in _GROUPED_NUMBER.finditer(user_prompt)}
    for unit in table_units:
        for number in grouped_numbers:
            supported.add(f"{number}{unit}")
    return supported


def unsupported_money_literals(content: str, *, user_prompt: str) -> tuple[str, ...]:
    """Return answer money literals whose numeric value is absent from grounded input."""

    supported = _supported_money_literals(user_prompt)
    unsupported: list[str] = []
    for match in _MONEY_LITERAL.finditer(content):
        token = match.group(0)
        if _normalize_money_token(token) in supported:
            continue
        if token not in unsupported:
            unsupported.append(token)
    return tuple(unsupported)


def _period_tuple(match: re.Match[str]) -> tuple[int, int, int, int]:
    start_year = int(match.group("start_year"))
    start_month = int(match.group("start_month"))
    end_year_raw = match.group("end_year")
    end_year = int(end_year_raw) if end_year_raw else start_year
    end_month = int(match.group("end_month"))
    return start_year, start_month, end_year, end_month


def _quarter_period(match: re.Match[str]) -> tuple[int, int, int, int]:
    year = int(match.group("year"))
    quarter = int(match.group("quarter"))
    start_month = (quarter - 1) * 3 + 1
    return year, start_month, year, start_month + 2


def _evidence_blocks(user_prompt: str) -> tuple[str, ...]:
    blocks = re.split(r"(?=^\[E\d+\]\s)", user_prompt, flags=re.MULTILINE)
    return tuple(block for block in blocks if re.match(r"^\[E\d+\]\s", block))


def _question_text(user_prompt: str) -> str:
    match = _USER_QUESTION.search(user_prompt)
    return match.group("query").strip() if match is not None else ""


def _evidence_by_number(user_prompt: str) -> dict[int, str]:
    evidence: dict[int, str] = {}
    for block in _evidence_blocks(user_prompt):
        match = re.match(r"^\[E(?P<number>\d+)\]\s", block)
        if match is not None:
            evidence[int(match.group("number"))] = block
    return evidence


def _evidence_companies(user_prompt: str) -> tuple[str, ...]:
    """Return canonical company names represented in the Evidence Pack."""

    return tuple(
        dict.fromkeys(
            match.group("company").strip()
            for match in _EVIDENCE_COMPANY.finditer(user_prompt)
            if match.group("company").strip()
        )
    )


def missing_required_company_mentions(
    content: str,
    *,
    user_prompt: str,
) -> tuple[str, ...]:
    """Require every evidence-backed comparison company to appear in the answer."""

    companies = _evidence_companies(user_prompt)
    if len(companies) <= 1:
        return ()
    return tuple(company for company in companies if company not in content)


def _evidence_company_by_number(user_prompt: str) -> dict[int, str]:
    """Map each Evidence number to its canonical company name."""

    mapping: dict[int, str] = {}
    for number, block in _evidence_by_number(user_prompt).items():
        match = _EVIDENCE_COMPANY.search(block)
        if match is not None:
            company = match.group("company").strip()
            if company:
                mapping[number] = company
    return mapping


def missing_multi_company_comparison_synthesis(
    content: str,
    *,
    user_prompt: str,
) -> tuple[str, ...]:
    """Require explicit comparison language backed by evidence from every target company."""

    query = _question_text(user_prompt)
    compact = "".join(query.split())
    comparison_intent = any(
        marker in compact
        for marker in ("비교", "차이", "다른지", "어떻게다른", "공통점")
    )
    companies = _evidence_companies(user_prompt)
    if not comparison_intent or len(companies) <= 1:
        return ()

    comparison_markers = (
        "비교",
        "반면",
        "차이",
        "공통",
        "달리",
        "이에 비해",
        "한편",
        "각각",
    )
    if not any(marker in content for marker in comparison_markers):
        return ("[MULTI_COMPANY_COMPARISON_REQUIRED]",)

    evidence_company = _evidence_company_by_number(user_prompt)
    cited_companies = {
        evidence_company[int(match.group(1))]
        for match in _EVIDENCE_CITATION.finditer(content)
        if int(match.group(1)) in evidence_company
    }
    missing = tuple(company for company in companies if company not in cited_companies)
    if missing:
        return tuple(f"[UNCITED_COMPANY:{company}]" for company in missing)

    return ()


def _business_unit_heading(line: str) -> str | None:
    upper = line.upper()
    for unit in _BUSINESS_UNIT_ALIASES:
        if re.search(rf"\*\*{unit}(?:\s*부문)?\*\*", upper):
            return unit
    return None


def _evidence_section_context(block: str) -> str:
    match = re.search(r"(?m)^섹션:\s*(?P<title>.+)$", block)
    return match.group("title").strip().lower() if match is not None else ""


def _shared_business_alias(
    unit: str,
    *,
    claim: str,
    evidence_block: str,
) -> bool:
    lowered_claim = claim.lower()
    lowered_evidence = evidence_block.lower()
    return any(
        alias in lowered_claim and alias in lowered_evidence
        for alias in _BUSINESS_UNIT_ALIASES[unit]
    )


def _requires_business_unit_attribution_check(user_prompt: str) -> bool:
    query = _question_text(user_prompt)
    compact = "".join(query.split())
    upper = compact.upper()
    return (
        ("AI" in upper and "전략" in compact)
        or "사업부별" in compact
        or "부문별" in compact
        or "사업부" in compact
    )


def unsupported_business_unit_attributions(
    content: str,
    *,
    user_prompt: str,
) -> tuple[str, ...]:
    """Reject business-unit grouping not supported by evidence section/context."""

    if not _requires_business_unit_attribution_check(user_prompt):
        return ()

    evidence = _evidence_by_number(user_prompt)
    if not evidence:
        return ()

    evidence_context = {
        number: _evidence_section_context(block)
        for number, block in evidence.items()
    }
    lines = content.splitlines()
    invalid: list[str] = []
    active_unit: str | None = None
    active_heading: str | None = None
    supported_in_block = False

    def close_block() -> None:
        nonlocal active_unit, active_heading, supported_in_block
        if active_heading is not None and not supported_in_block:
            invalid.append(active_heading)
        active_unit = None
        active_heading = None
        supported_in_block = False

    for raw_line in lines:
        line = raw_line.strip()
        unit = _business_unit_heading(line)
        if unit is not None:
            close_block()
            active_unit = unit
            active_heading = line
            continue
        if active_unit is None or not line:
            continue
        if re.match(r"^\d+\.\s+\*\*", line):
            close_block()
            continue

        refs = [int(match.group(1)) for match in _EVIDENCE_CITATION.finditer(line)]
        if not refs:
            continue
        aliases = _BUSINESS_UNIT_ALIASES[active_unit]
        supported = any(
            any(alias in evidence_context.get(number, "") for alias in aliases)
            or _shared_business_alias(
                active_unit,
                claim=line,
                evidence_block=evidence.get(number, ""),
            )
            for number in refs
        )
        if supported:
            supported_in_block = True
        else:
            invalid.append(line)

    close_block()
    return tuple(dict.fromkeys(invalid))


def unsupported_narrow_business_scope_claims(
    content: str,
    *,
    user_prompt: str,
) -> tuple[str, ...]:
    """Prevent memory-only facts from leaking into a system-semiconductor query."""

    compact_query = "".join(_question_text(user_prompt).split())
    if "시스템반도체" not in compact_query or "메모리" in compact_query:
        return ()

    invalid: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if any(marker.upper() in upper for marker in _MEMORY_ONLY_SCOPE_MARKERS):
            invalid.append(stripped)
    return tuple(dict.fromkeys(invalid))


def _explicit_investment_purpose(block: str) -> bool:
    compact = "".join(block.lower().split())
    return any(
        marker in compact
        for marker in (
            "투자목적",
            "위한투자",
            "투자를위해",
            "투자하기위해",
            "목적으로투자",
        )
    )


def unsupported_unrequested_investment_amounts(
    content: str,
    *,
    user_prompt: str,
) -> tuple[str, ...]:
    """Reject completed investment amounts when a direction/purpose query did not ask for them."""

    compact_query = "".join(_question_text(user_prompt).split())
    asks_direction_or_purpose = (
        "투자" in compact_query
        and any(term in compact_query for term in ("방향", "목적"))
    )
    asks_amount = any(
        term in compact_query for term in ("금액", "규모", "얼마", "투자액")
    )
    if not asks_direction_or_purpose or asks_amount:
        return ()

    return tuple(
        line.strip()
        for line in content.splitlines()
        if line.strip() and _MONEY_LITERAL.search(line)
    )


def unsupported_investment_scope_structure(
    content: str,
    *,
    user_prompt: str,
) -> tuple[str, ...]:
    """Require non-purpose strategy facts to be separated from investment purpose."""

    compact_query = "".join(_question_text(user_prompt).split())
    if (
        "시스템반도체" not in compact_query
        or "투자" not in compact_query
        or "목적" not in compact_query
    ):
        return ()

    evidence = _evidence_by_number(user_prompt)
    lines = content.splitlines()
    combined_scope_lines = [
        line.strip()
        for line in lines
        if line.strip() and "투자 방향과 목적" in line
    ]
    if not combined_scope_lines:
        return ()

    invalid: list[str] = []
    nonpurpose_numbered = False
    for raw_line in lines:
        line = raw_line.strip()
        if not re.match(r"^\d+\.\s+", line):
            continue
        refs = [int(match.group(1)) for match in _EVIDENCE_CITATION.finditer(line)]
        if not refs:
            continue
        if any(_explicit_investment_purpose(evidence.get(number, "")) for number in refs):
            continue
        invalid.append(line)
        nonpurpose_numbered = True

    if nonpurpose_numbered:
        invalid.extend(combined_scope_lines)

    return tuple(dict.fromkeys(invalid))


def unsupported_investment_purpose_claims(
    content: str,
    *,
    user_prompt: str,
) -> tuple[str, ...]:
    """Reject strategy/context sentences relabeled as investment purpose."""

    compact_query = "".join(_question_text(user_prompt).split())
    if "투자" not in compact_query or "목적" not in compact_query:
        return ()

    evidence = _evidence_by_number(user_prompt)
    invalid: list[str] = []
    in_purpose_section = False
    interpretive_markers = (
        "투자 목적",
        "목표",
        "위한 전략",
        "전략적 움직임",
        "대응하기 위한",
        "위한 투자 방향",
        "투자 방향을 설정",
        "투자 방향과 목적을 통해",
        "것으로 보입니다",
        "시장 점유율",
    )

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "투자 목적" in line and line.endswith(":"):
            in_purpose_section = True
            continue
        if in_purpose_section and re.match(r"^\d+\.\s+\*\*", line):
            in_purpose_section = False

        refs = [int(match.group(1)) for match in _EVIDENCE_CITATION.finditer(line)]
        purpose_like = in_purpose_section or any(
            marker in line for marker in interpretive_markers
        )
        if not purpose_like:
            continue
        if not refs:
            invalid.append(line)
            continue
        if not any(
            _explicit_investment_purpose(evidence.get(number, ""))
            for number in refs
        ):
            invalid.append(line)

    return tuple(dict.fromkeys(invalid))


def _completed_context(
    user_prompt: str,
) -> tuple[set[str], set[tuple[int, int, int, int]]]:
    completed_money: set[str] = set()
    completed_periods: set[tuple[int, int, int, int]] = set()
    lowered = user_prompt.lower()
    window_radius = 240

    for marker in _COMPLETED_MARKERS:
        start = 0
        while True:
            index = lowered.find(marker.lower(), start)
            if index < 0:
                break
            window_start = max(0, index - window_radius)
            window_end = min(len(user_prompt), index + len(marker) + window_radius)
            window = user_prompt[window_start:window_end]
            completed_money.update(
                _normalize_money_token(match.group(0))
                for match in _MONEY_LITERAL.finditer(window)
            )
            completed_periods.update(
                _period_tuple(match) for match in _DOTTED_PERIOD.finditer(window)
            )
            start = index + len(marker)

    for block in _evidence_blocks(user_prompt):
        if not any(marker in block for marker in _COMPLETED_MARKERS):
            continue
        if "투자기간" not in block or "투자액" not in block:
            continue
        units = {
            _normalize_money_token(match.group(1)) for match in _TABLE_UNIT.finditer(block)
        }
        numbers = {match.group(0) for match in _GROUPED_NUMBER.finditer(block)}
        for unit in units:
            completed_money.update(f"{number}{unit}" for number in numbers)
        completed_periods.update(
            _period_tuple(match) for match in _DOTTED_PERIOD.finditer(block)
        )

    return completed_money, completed_periods


def _question_explicitly_excludes_completed_investment(user_prompt: str) -> bool:
    match = _USER_QUESTION.search(user_prompt)
    if match is None:
        return False
    compact = "".join(match.group("query").split())
    exclusion = any(term in compact for term in ("빼고", "제외하고", "제외해", "제외한"))
    completed_scope = any(term in compact for term in ("집행된", "집행한", "투자실적"))
    amount_scope = "금액" in compact or "실적" in compact
    return "투자" in compact and exclusion and completed_scope and amount_scope


def unsupported_explicit_exclusions(
    content: str,
    *,
    user_prompt: str,
) -> tuple[str, ...]:
    """Reject completed investment amounts that the user explicitly asked to exclude."""

    if not _question_explicitly_excludes_completed_investment(user_prompt):
        return ()
    completed_money, _ = _completed_context(user_prompt)
    if not completed_money:
        return ()

    invalid: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        normalized_line = _normalize_money_token(stripped)
        if any(token in normalized_line for token in completed_money):
            invalid.append(stripped)
    return tuple(dict.fromkeys(invalid))


def unsupported_temporal_claims(content: str, *, user_prompt: str) -> tuple[str, ...]:
    """Reject completed disclosure facts that HCX rewrites as future plans or schedules."""

    completed_money, completed_periods = _completed_context(user_prompt)
    if not completed_money and not completed_periods:
        return ()

    invalid: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or not any(marker in stripped for marker in _FUTURE_MARKERS):
            continue

        normalized_line = _normalize_money_token(stripped)
        money_conflict = any(token in normalized_line for token in completed_money)
        claimed_periods = {
            _period_tuple(match) for match in _KOREAN_PERIOD.finditer(stripped)
        }
        claimed_periods.update(
            _quarter_period(match) for match in _KOREAN_QUARTER.finditer(stripped)
        )
        period_conflict = bool(claimed_periods & completed_periods)
        if (money_conflict or period_conflict) and stripped not in invalid:
            invalid.append(stripped)
    return tuple(invalid)


def unsupported_investment_plan_structure(
    content: str,
    *,
    user_prompt: str,
) -> tuple[str, ...]:
    """Reject headings that label completed investment results as investment plans."""

    compact_prompt = "".join(user_prompt.split())
    if "투자계획" not in compact_prompt:
        return ()

    completed_money, _ = _completed_context(user_prompt)
    lines = content.splitlines()
    invalid: list[str] = []

    for index, line in enumerate(lines):
        heading = line.strip()
        if "투자 계획" not in heading or not heading.endswith(":"):
            continue
        if "향후" in heading or "지속" in heading:
            continue

        block_lines: list[str] = []
        started = False
        for following in lines[index + 1 :]:
            stripped = following.strip()
            if not stripped:
                if started:
                    break
                continue
            started = True
            block_lines.append(stripped)

        has_completed_marker = any(
            marker in block_line
            for block_line in block_lines
            for marker in _COMPLETED_MARKERS
        )
        has_completed_money = any(
            token in _normalize_money_token(block_line)
            for block_line in block_lines
            for token in completed_money
        )
        if (has_completed_marker or has_completed_money) and heading not in invalid:
            invalid.append(heading)

    return tuple(invalid)


def _strip_unsupported_fundraising_absence_citations(
    content: str,
    *,
    user_prompt: str,
) -> str:
    """Remove positive-event citations from deterministic zero-event category statements."""

    if "analysis_type: fundraising_by_instrument" not in user_prompt:
        return content

    empty_labels = tuple(
        match.group("label").strip()
        for match in _EMPTY_FUNDRAISING_CATEGORY.finditer(user_prompt)
    )
    positive_labels = tuple(
        match.group("label").strip()
        for match in _POSITIVE_FUNDRAISING_CATEGORY.finditer(user_prompt)
    )
    if not empty_labels:
        return content

    sanitized_lines: list[str] = []
    for line in content.splitlines():
        mentions_empty = any(label in line for label in empty_labels)
        mentions_positive = any(label in line for label in positive_labels)
        states_absence = any(phrase in line for phrase in _ABSENCE_PHRASES)
        if mentions_empty and not mentions_positive and states_absence:
            line = _EVIDENCE_CITATION.sub("", line)
            line = re.sub(r"\s+([.,])", r"\1", line)
            line = re.sub(r" {2,}", " ", line).rstrip()
        sanitized_lines.append(line)
    return "\n".join(sanitized_lines)


def _strip_lines_with_grounding_violations(
    content: str,
    *,
    user_prompt: str,
) -> str:
    """Drop unsupported claims and neutralize misleading investment headings."""

    unsupported_money = set(unsupported_money_literals(content, user_prompt=user_prompt))
    unsupported_temporal = set(unsupported_temporal_claims(content, user_prompt=user_prompt))
    unsupported_exclusions = set(
        unsupported_explicit_exclusions(content, user_prompt=user_prompt)
    )
    unsupported_structure = set(
        unsupported_investment_plan_structure(content, user_prompt=user_prompt)
    )
    unsupported_units = set(
        unsupported_business_unit_attributions(content, user_prompt=user_prompt)
    )
    unsupported_scope = set(
        unsupported_narrow_business_scope_claims(content, user_prompt=user_prompt)
    )
    unsupported_purpose = set(
        unsupported_investment_purpose_claims(content, user_prompt=user_prompt)
    )
    unsupported_purpose_structure = set(
        unsupported_investment_scope_structure(content, user_prompt=user_prompt)
    )
    unsupported_unrequested_amounts = set(
        unsupported_unrequested_investment_amounts(content, user_prompt=user_prompt)
    )
    if (
        not unsupported_money
        and not unsupported_temporal
        and not unsupported_exclusions
        and not unsupported_structure
        and not unsupported_units
        and not unsupported_scope
        and not unsupported_purpose
        and not unsupported_purpose_structure
        and not unsupported_unrequested_amounts
    ):
        return content

    retained: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if (
            stripped in unsupported_temporal
            or stripped in unsupported_exclusions
            or stripped in unsupported_units
            or stripped in unsupported_scope
            or stripped in unsupported_purpose
            or stripped in unsupported_purpose_structure
            or stripped in unsupported_unrequested_amounts
        ):
            continue
        if stripped in unsupported_structure:
            indent = line[: len(line) - len(line.lstrip())]
            retained.append(f"{indent}{_NEUTRAL_INVESTMENT_HEADING}")
            continue
        if any(token in line for token in unsupported_money):
            continue
        retained.append(line)

    while retained and retained[-1].strip().endswith(":"):
        retained.pop()
    return "\n".join(retained).strip()


def _sanitize_answer(
    answer: HcxAnswerResult,
    *,
    user_prompt: str,
) -> HcxAnswerResult:
    content = _strip_unsupported_fundraising_absence_citations(
        answer.content,
        user_prompt=user_prompt,
    )
    return replace(answer, content=content)


def _all_invalid_grounding_tokens(
    content: str,
    *,
    user_prompt: str,
    evidence_count: int,
    evidence_report_years: dict[int, int] | None,
) -> tuple[str, ...]:
    invalid = list(invalid_citation_tokens(content, evidence_count=evidence_count))
    invalid.extend(unsupported_money_literals(content, user_prompt=user_prompt))
    invalid.extend(unsupported_temporal_claims(content, user_prompt=user_prompt))
    invalid.extend(unsupported_explicit_exclusions(content, user_prompt=user_prompt))
    invalid.extend(unsupported_investment_plan_structure(content, user_prompt=user_prompt))
    invalid.extend(unsupported_business_unit_attributions(content, user_prompt=user_prompt))
    invalid.extend(unsupported_narrow_business_scope_claims(content, user_prompt=user_prompt))
    invalid.extend(unsupported_investment_purpose_claims(content, user_prompt=user_prompt))
    invalid.extend(
        unsupported_investment_scope_structure(content, user_prompt=user_prompt)
    )
    invalid.extend(
        unsupported_unrequested_investment_amounts(content, user_prompt=user_prompt)
    )
    invalid.extend(
        f"[MISSING_COMPANY:{company}]"
        for company in missing_required_company_mentions(
            content,
            user_prompt=user_prompt,
        )
    )
    invalid.extend(
        missing_multi_company_comparison_synthesis(
            content,
            user_prompt=user_prompt,
        )
    )
    if evidence_report_years:
        invalid.extend(
            invalid_report_year_citations(
                content,
                evidence_report_years=evidence_report_years,
            )
        )
    return tuple(dict.fromkeys(invalid))


def _conservative_candidate(
    answer: HcxAnswerResult,
    *,
    user_prompt: str,
    evidence_count: int,
    evidence_report_years: dict[int, int] | None,
) -> HcxAnswerResult | None:
    """Return a safe local degradation candidate without another model call."""

    content = _strip_lines_with_grounding_violations(
        answer.content,
        user_prompt=user_prompt,
    )
    if not content:
        return None
    candidate = replace(answer, content=content)
    remaining = _all_invalid_grounding_tokens(
        candidate.content,
        user_prompt=user_prompt,
        evidence_count=evidence_count,
        evidence_report_years=evidence_report_years,
    )
    return candidate if not remaining else None


def _content_size(content: str) -> int:
    return len(re.sub(r"\s+", "", content))


def _prefer_non_degraded_repair(
    repaired: HcxAnswerResult,
    conservative_original: HcxAnswerResult | None,
) -> HcxAnswerResult:
    """Avoid a grounded repair that discards most otherwise-safe answer content."""

    if conservative_original is None:
        return repaired
    original_size = _content_size(conservative_original.content)
    repaired_size = _content_size(repaired.content)
    if original_size and repaired_size * 2 < original_size:
        return conservative_original
    return repaired


def generate_grounded_answer(
    client: GroundedAnswerClient,
    *,
    system_prompt: str,
    user_prompt: str,
    evidence_count: int,
    max_completion_tokens: int = 1200,
    evidence_report_years: dict[int, int] | None = None,
) -> HcxAnswerResult:
    """Generate, repair once, then conservatively preserve the safest useful answer."""

    if evidence_count < 1:
        raise ValueError("evidence_count must be at least 1")

    answer = client.answer(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_completion_tokens=max_completion_tokens,
    )
    answer = _sanitize_answer(answer, user_prompt=user_prompt)
    invalid = _all_invalid_grounding_tokens(
        answer.content,
        user_prompt=user_prompt,
        evidence_count=evidence_count,
        evidence_report_years=evidence_report_years,
    )
    if not invalid:
        return answer

    conservative_original = _conservative_candidate(
        answer,
        user_prompt=user_prompt,
        evidence_count=evidence_count,
        evidence_report_years=evidence_report_years,
    )

    repair_lines = [
        user_prompt,
        "",
        "근거 정합성 재작성 요구사항:",
        f"- 사용할 수 있는 인용은 [E1]부터 [E{evidence_count}]까지뿐입니다.",
        "- Evidence를 사용한 사실 답변에는 최소 하나 이상의 유효한 [E번호] 인용을 붙이세요.",
        "- [DETERMINISTIC ANALYSIS] 같은 내부 섹션명은 인용으로 쓰지 마세요.",
        "- Evidence가 뒷받침하는 사실관계는 유지하되 잘못된 인용이나 숫자 표기만 고치세요.",
        "- 구체적 사실을 결론에서 다시 말하면 그 문장에도 해당 [E번호]를 다시 붙이세요.",
        "- 금액은 Evidence에 실제로 등장하는 숫자와 단위만 사용하세요. 표의 숫자를 옮길 때 "
        "자릿수나 쉼표를 바꾸지 말고, 근거에 없는 축약이나 임의 환산을 하지 마세요.",
        "- Evidence가 '이루어졌습니다', '완료했습니다'처럼 완료 사실로 밝힌 투자나 취득을 "
        "계획 또는 예정으로 미래화하지 마세요.",
        "- 이미 종료된 투자기간이나 분기를 '진행될 예정' 같은 미래 일정으로 바꾸지 마세요.",
        "- 투자 계획 질의에서 이미 집행된 금액·기간은 '확인된 투자 실적'처럼 별도 구분하고, "
        "'주요 투자 계획' 또는 '세부 투자 계획' 아래에 배치하지 마세요.",
        "- 사용자가 특정 정보나 범위를 빼거나 제외하라고 명시했으면 답변에 다시 포함하지 마세요.",
        "- 사용자가 시스템 반도체처럼 특정 사업 범위를 물었다면 인접한 메모리 전용 설명을 "
        "그 사업의 투자 방향이나 목적으로 옮기지 마세요. 질문이 메모리를 함께 요청하지 않았다면 "
        "메모리 전용 내용은 '관련 사업 전략' 등 다른 섹션으로 옮겨서도 답변에 포함하지 마세요. "
        "DRAM, NAND, HBM, DDR, GDDR, LPDDR, SOCAMM, SSD 등 메모리 제품·기술 설명도 제외하세요.",
        "- '투자 목적'으로 분류하는 문장은 Evidence가 목적 관계를 직접 표현할 때만 사용하세요. "
        "시장 전망이나 사업 전략을 투자 목적이라고 재명명하지 마세요.",
        "- 시스템 반도체의 투자 방향·목적 질의에서는 답변을 '직접 확인되는 투자 방향/목적'과 "
        "'관련 사업 전략'으로 구분하세요. 고부가 수주, 수익 구조 개선, 응용처 다변화처럼 "
        "직접적인 투자 목적 관계가 없는 사실을 '투자 방향과 목적' 목록에 넣지 마세요.",
        "- Evidence에 없는 '시장 점유율을 높이고자 한다', '~것으로 보인다' 같은 해석적 "
        "결론을 추가하지 마세요.",
    ]
    if _requires_business_unit_attribution_check(user_prompt):
        repair_lines.append(
            "- DX, DS, SDC 같은 사업부별로 내용을 묶을 때는 인용한 Evidence가 그 사업부를 "
            "명시적으로 식별하는 경우에만 해당 사업부에 귀속하세요. 그렇지 않으면 사업부 "
            "라벨을 붙이지 마세요."
        )
    if evidence_report_years:
        repair_lines.append(
            "- 연도별 사업보고서 비교에서는 각 연도 사실을 말하는 문장이나 행에 같은 연도의 "
            "사업보고서 Evidence만 인용하세요."
        )
    evidence_companies = _evidence_companies(user_prompt)
    if len(evidence_companies) > 1:
        repair_lines.append(
            "- 다중기업 질의에서는 Evidence에 포함된 모든 비교 대상 기업명을 최종 답변에 "
            "명시하고, 각 기업의 내용을 해당 기업 Evidence에 근거해 설명하세요. "
            f"비교 대상: {', '.join(evidence_companies)}."
        )
        compact_query = "".join(_question_text(user_prompt).split())
        if any(
            marker in compact_query
            for marker in ("비교", "차이", "다른지", "어떻게다른", "공통점")
        ):
            repair_lines.append(
                "- 비교를 요청한 질의에서는 기업별 요약만 나열하지 말고, '반면', '차이', "
                "'비교하면' 등으로 공통점 또는 차이점을 직접 설명하는 비교 문장을 최소 하나 "
                "포함하세요. 최종 답변 전체에서 각 비교 대상 기업의 사실에는 해당 기업 Evidence가 "
                "최소 하나 이상 인용되어야 합니다. 비교 문장에 모든 Evidence를 억지로 한꺼번에 "
                "붙일 필요는 없지만, 다른 기업의 Evidence로 사실을 뒷받침하지 마세요."
            )
    repair_prompt = "\n".join(repair_lines)
    repaired = client.answer(
        system_prompt=system_prompt,
        user_prompt=repair_prompt,
        max_completion_tokens=max_completion_tokens,
    )
    repaired = _sanitize_answer(repaired, user_prompt=user_prompt)
    remaining = _all_invalid_grounding_tokens(
        repaired.content,
        user_prompt=user_prompt,
        evidence_count=evidence_count,
        evidence_report_years=evidence_report_years,
    )
    if not remaining:
        return _prefer_non_degraded_repair(repaired, conservative_original)

    conservative_repaired = _conservative_candidate(
        repaired,
        user_prompt=user_prompt,
        evidence_count=evidence_count,
        evidence_report_years=evidence_report_years,
    )
    if conservative_repaired is not None:
        return _prefer_non_degraded_repair(conservative_repaired, conservative_original)
    if conservative_original is not None:
        return conservative_original

    return replace(
        repaired,
        content=(
            "검색된 공시는 있으나, 근거 정합성 검증을 통과한 서술형 답변을 "
            "안전하게 구성하지 못했습니다. 근거 공시를 직접 확인해 주세요."
        ),
        finish_reason="grounding_exhausted",
    )
