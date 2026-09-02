"""Conservative, deterministic planning for the four-field contract endpoint.

This is a bounded rule-based interface, not a general natural-language parser.
It consumes only the user's request; no benchmark IDs, receipts or gold data.
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from collections.abc import Mapping
from datetime import date
from typing import Any

PLANNER_VERSION = "contract-query-v1"
CONTRACT_SUBTYPE = "단일판매공급계약체결"
DATE_TOKEN = re.compile(
    r"(?<![0-9])(?:"
    r"(?P<ky>[12][0-9]{3})\s*년(?:\s*(?P<km>[0-9]{1,2})\s*월"
    r"(?:\s*(?P<kd>[0-9]{1,2})\s*일)?)?"
    r"|(?P<iy>[12][0-9]{3})[-/.](?P<im>[0-9]{1,2})[-/.](?P<id>[0-9]{1,2})(?![0-9]))"
)


def _date_bounds(match: re.Match) -> tuple[date, date]:
    year = int(match["ky"] or match["iy"])
    month = match["km"] or match["im"]
    day = match["kd"] or match["id"]
    if month is None:
        return date(year, 1, 1), date(year, 12, 31)
    month = int(month)
    if day is not None:
        value = date(year, month, int(day))
        return value, value
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def quantity_probes(query: str) -> list[tuple[str, str]]:
    """Only explicit equipment/ship quantities; never treat dates/amounts as quantities."""
    found = []
    normalized = unicodedata.normalize("NFKC", query)
    for match in re.finditer(
        r"(?<![0-9.,])([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,9})\s*(대|척)"
        r"(?=$|[^a-zA-Z0-9가-힣]|(?:의|은|는|을|를|짜리)(?=$|[^a-zA-Z0-9가-힣]))",
        normalized,
    ):
        item = (str(int(match[1].replace(",", ""))), match[2])
        if item not in found:
            found.append(item)
    return found[:3]


def quantity_pattern(probe: tuple[str, str]) -> str:
    number, unit = probe
    if not re.fullmatch(r"[0-9]{1,9}", number) or unit not in {"대", "척"}:
        raise ValueError("Invalid quantity probe")
    # SQL removes whitespace/thousands separators first; do not match 3500 in 13500.
    return rf"(^|[^0-9.]){number}{unit}"


def plan_contract_query(query: str, filters: Mapping[str, Any]) -> dict[str, Any]:
    plan = {
        "version": PLANNER_VERSION,
        "status": "ready",
        "reason_code": None,
        "reason": None,
        "filters": dict(filters),
        "notes": [],
        "quantity_probes": [],
    }

    def stop(status, code, reason):
        plan.update(status=status, reason_code=code, reason=reason)
        return plan

    if not query.strip() or len(query) > 10000:
        return stop("clarification_required", "invalid_query", "질문은 1~10,000자로 입력해 주세요.")
    normalized = unicodedata.normalize("NFKC", query).casefold()
    compact = re.sub(r"\s+", "", normalized)
    # Mixed requests are refused as a whole: never silently return a partial answer.
    unsupported = (
        (
            r"합산|합계|총합|누적|평균|모두더|전부더|(?:모든|전체|전부).*(?:금액|수주액|규모)",
            "aggregation",
            "전체 계약의 합산·평균은 지원하지 않습니다. 특정 공시의 계약금액을 물어봐 주세요.",
        ),
        (
            r"최신|유효계약|잔여|잔고|정정.*반영|(?:해지|취소).*(?:제외|반영)|현재.*(?:금액|계약)",
            "contract_lifecycle",
            "정정·해지 연결을 적용한 최신 유효 계약이나 잔여 금액은 아직 지원하지 않습니다.",
        ),
        (
            r"환율|환산|(?:달러|usd|엔화|유로|원화)(?:로|으로)",
            "currency_conversion",
            "환율 환산은 지원하지 않습니다. 공시에 명시된 원화 계약금액만 제공합니다.",
        ),
        (
            r"매출|영업이익|매출총이익|순이익|순익|이익률|마진|가동률|배당",
            "financial_analysis",
            "매출·이익 등은 계약금액과 다릅니다. 이 경로는 계약의 네 가지 원문 필드만 지원합니다.",
        ),
    )
    for pattern, code, reason in unsupported:
        if re.search(pattern, compact) or (
            code == "financial_analysis" and re.search(r"(?<![a-z0-9가-힣])주가", normalized)
        ):
            return stop("unsupported", code, reason)
    if not re.search(r"계약|대선|수주|납품|발주|금액|기간|상대방|시제", compact):
        return stop(
            "unsupported",
            "outside_contract_fields",
            "계약상대방·계약금액·계약 시작일·종료일을 물어봐 주세요.",
        )
    if re.search(r"올해|작년|내년|이번달|지난달|오늘|어제|최근", compact):
        return stop(
            "clarification_required",
            "relative_date",
            "공시 접수일 범위를 연월일로 지정해 주세요. 상대 날짜를 임의로 해석하지 않습니다.",
        )
    scoped = plan["filters"]
    scoped.setdefault("date_from", None)
    scoped.setdefault("date_to", None)
    for name, value in (
        ("document_group", "exchange"),
        ("chunk_type", "table"),
        ("document_subtype", CONTRACT_SUBTYPE),
    ):
        if scoped.get(name) not in {None, value}:
            return stop(
                "clarification_required",
                "incompatible_scope",
                "계약 필드 검색은 공급계약 공시의 표만 지원합니다. 문서 종류 조건을 확인해 주세요.",
            )
        scoped[name] = value
    for key in ("date_from", "date_to"):
        value = scoped.get(key)
        if isinstance(value, str):
            try:
                scoped[key] = date.fromisoformat(value)
            except ValueError:
                return stop("clarification_required", "invalid_date", "날짜 옵션을 확인해 주세요.")
        elif value is not None and not isinstance(value, date):
            return stop("clarification_required", "invalid_date", "날짜 옵션을 확인해 주세요.")
    if (
        scoped.get("date_from")
        and scoped.get("date_to")
        and scoped["date_from"] > scoped["date_to"]
    ):
        return stop("clarification_required", "reversed_dates", "접수일 시작이 종료보다 늦습니다.")
    corrections = scoped.get("corrections", "all")
    if corrections not in {"all", "only", "exclude"}:
        return stop(
            "clarification_required", "invalid_corrections", "정정공시 조건을 확인해 주세요."
        )
    requested_correction = None
    if re.search(r"정정공시(?:를|는)?(?:제외|빼고)", compact):
        requested_correction = "exclude"
    elif "정정공시" in compact:
        requested_correction = "only"
    if requested_correction:
        if corrections not in {"all", requested_correction}:
            return stop(
                "clarification_required",
                "correction_conflict",
                "질문과 정정공시 옵션이 서로 충돌합니다.",
            )
        scoped["corrections"] = requested_correction
    dates = list(DATE_TOKEN.finditer(normalized))
    residual = DATE_TOKEN.sub("", normalized)
    if re.search(r"[0-9]{2,4}\s*년|[0-9]{4}[-/.][0-9]{1,2}|[0-9]{1,2}\s*월|[1-4]\s*분기", residual):
        return stop(
            "clarification_required",
            "unsupported_date_format",
            "공시 접수일을 YYYY-MM-DD 또는 연·월·일 형식으로 지정해 주세요.",
        )
    if dates:
        # Dates linked to contract performance must not become filing receipt dates.
        for token in dates:
            before = re.sub(r"\s+", "", normalized[max(0, token.start() - 15) : token.start()])
            after = re.sub(r"\s+", "", normalized[token.end() : token.end() + 20])
            if re.search(
                r"(?:계약)?(?:시작일|종료일|착수일|체결일|납기|만기|계약기간)(?:이|은|는|:)?$",
                before,
            ) or re.match(
                r"(?:에|부터|까지)?(?:계약(?:이|을)?)?(?:시작|종료|끝나|끝난|착수|만료|체결|납품|납기)",
                after,
            ):
                return stop(
                    "clarification_required",
                    "contract_date_not_receipt",
                    "계약 시작·종료일 조건 검색은 아직 지원하지 않습니다. "
                    "공시 접수일 조건과 구분해 주세요.",
                )
        try:
            bounds = [_date_bounds(token) for token in dates]
        except (ValueError, calendar.IllegalMonthError):
            return stop(
                "clarification_required",
                "invalid_date",
                "존재하는 연월일로 공시 접수일을 지정해 주세요.",
            )
        if len(dates) > 2:
            return stop(
                "clarification_required",
                "multiple_dates",
                "공시 접수일 범위를 하나로 지정해 주세요.",
            )
        if len(dates) == 1 and re.match(
            r"\s*(?:부터|이후|이전|까지|이래)", normalized[dates[0].end() :]
        ):
            return stop(
                "clarification_required",
                "open_date_range",
                "공시 접수일 범위의 시작과 종료를 모두 지정해 주세요.",
            )
        if len(dates) == 2:
            connector = re.sub(r"\s+", "", normalized[dates[0].end() : dates[1].start()])
            if connector not in {"~", "～", "부터", "에서", "-"}:
                return stop(
                    "clarification_required",
                    "multiple_dates",
                    "서로 다른 날짜가 있습니다. 공시 접수일 범위를 명시해 주세요.",
                )
        start, end = bounds[0][0], bounds[-1][1]
        if start > end:
            return stop(
                "clarification_required", "reversed_dates", "공시 접수일 시작이 종료보다 늦습니다."
            )
        explicit_start, explicit_end = scoped.get("date_from"), scoped.get("date_to")
        # Explicit UI scopes and text scopes are intersected, never widened or overwritten.
        start = max(start, explicit_start) if explicit_start else start
        end = min(end, explicit_end) if explicit_end else end
        if start > end:
            return stop(
                "clarification_required",
                "date_conflict",
                "질문의 날짜와 명시한 접수일 범위가 겹치지 않습니다.",
            )
        scoped.update(date_from=start, date_to=end)
        plan["notes"].append(
            "질문의 연월일을 공시 접수일로 해석했습니다. 계약 시작·종료일과 다릅니다."
        )
    plan["quantity_probes"] = quantity_probes(query)
    return plan


def stopped_contract_answer(plan: Mapping[str, Any], run: Mapping[str, Any]) -> dict[str, Any]:
    if plan["status"] == "ready":
        raise ValueError("Ready plans require retrieval")
    return {
        "schema_version": "retrieval-contract-fields-v2",
        "status": plan["status"],
        "reason_code": plan["reason_code"],
        "reason": plan["reason"],
        "query_plan": dict(plan),
        "embedding_run_id": run["embedding_run_id"],
        "chunk_run_id": run["chunk_run_id"],
        "findings": [],
        "limitations": [],
        "database_writes": 0,
        "extraction_provider_calls": 0,
        "query_tokens": 0,
        "query_provider_calls": 0,
    }
