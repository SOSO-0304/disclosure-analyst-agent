"""Deterministic checks for batch answer regression evaluation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from disclosure_agent.services.answer_service import AnswerResult

_EVIDENCE_CITATION = re.compile(r"\[E(?P<number>\d+)\]")
_REPORT_YEAR = re.compile(r"(?P<year>20\d{2})")


@dataclass(frozen=True, slots=True)
class RegressionCase:
    """One regression query and its machine-checkable expectations."""

    case_id: str
    tier: str
    category: str
    query: str
    expected_mode: str | None = None
    expected_statuses: tuple[str, ...] = ()
    expected_rails: tuple[str, ...] = ()
    min_evidence: int = 0
    must_include: tuple[str, ...] = ()
    must_include_any: tuple[tuple[str, ...], ...] = ()
    must_not_include: tuple[str, ...] = ()
    evidence_report_contains: str | None = None
    min_evidence_per_year: tuple[tuple[int, int], ...] = ()
    require_citations: bool = False
    manual_review: bool = False
    note: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RegressionCase:
        """Parse one JSON object into a validated regression case."""

        case_id = str(payload.get("id", "")).strip()
        query = str(payload.get("query", "")).strip()
        if not case_id:
            raise ValueError("regression case requires non-empty id")
        if not query:
            raise ValueError(f"regression case {case_id} requires non-empty query")

        statuses = payload.get("expected_statuses")
        if statuses is None and payload.get("expected_status") is not None:
            statuses = [payload["expected_status"]]

        year_counts = payload.get("min_evidence_per_year", {})
        if not isinstance(year_counts, dict):
            raise ValueError(f"{case_id}: min_evidence_per_year must be an object")

        include_any = payload.get("must_include_any", [])
        if not all(isinstance(group, list) and group for group in include_any):
            raise ValueError(f"{case_id}: must_include_any groups must be non-empty lists")

        return cls(
            case_id=case_id,
            tier=str(payload.get("tier", "challenge")),
            category=str(payload.get("category", "uncategorized")),
            query=query,
            expected_mode=_optional_string(payload.get("expected_mode")),
            expected_statuses=tuple(str(value) for value in (statuses or [])),
            expected_rails=tuple(str(value) for value in payload.get("expected_rails", [])),
            min_evidence=int(payload.get("min_evidence", 0)),
            must_include=tuple(str(value) for value in payload.get("must_include", [])),
            must_include_any=tuple(
                tuple(str(value) for value in group) for group in include_any
            ),
            must_not_include=tuple(
                str(value) for value in payload.get("must_not_include", [])
            ),
            evidence_report_contains=_optional_string(
                payload.get("evidence_report_contains")
            ),
            min_evidence_per_year=tuple(
                sorted((int(year), int(count)) for year, count in year_counts.items())
            ),
            require_citations=bool(payload.get("require_citations", False)),
            manual_review=bool(payload.get("manual_review", False)),
            note=str(payload.get("note", "")),
        )


@dataclass(frozen=True, slots=True)
class RegressionCheck:
    """One machine-checkable assertion against an AnswerResult."""

    name: str
    passed: bool
    detail: str
    failure_type: str


@dataclass(frozen=True, slots=True)
class RegressionEvaluation:
    """Evaluation outcome for one regression case."""

    case: RegressionCase
    checks: tuple[RegressionCheck, ...]
    mode: str | None
    status: str | None
    generator: str | None
    evidence_count: int
    answer: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    duration_ms: int | None = None
    error: str | None = None

    @property
    def hard_passed(self) -> bool:
        return self.error is None and all(check.passed for check in self.checks)

    @property
    def verdict(self) -> str:
        if self.error is not None:
            return "ERROR"
        if not self.hard_passed:
            return "FAIL"
        if self.case.manual_review:
            return "MANUAL_REVIEW"
        return "PASS"

    @property
    def failure_types(self) -> tuple[str, ...]:
        if self.error is not None:
            return ("execution",)
        return tuple(
            dict.fromkeys(check.failure_type for check in self.checks if not check.passed)
        )

    def to_record(self) -> dict[str, Any]:
        """Serialize the evaluation to a flat JSON/CSV friendly record."""

        failed_checks = [check.detail for check in self.checks if not check.passed]
        return {
            "case_id": self.case.case_id,
            "tier": self.case.tier,
            "category": self.case.category,
            "query": self.case.query,
            "verdict": self.verdict,
            "hard_passed": self.hard_passed,
            "manual_review": self.case.manual_review,
            "mode": self.mode,
            "status": self.status,
            "generator": self.generator,
            "evidence_count": self.evidence_count,
            "failure_types": ",".join(self.failure_types),
            "failure_reason": " | ".join(failed_checks) if failed_checks else self.error or "",
            "answer": self.answer,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "duration_ms": self.duration_ms,
            "note": self.case.note,
        }


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def load_regression_cases(path: str | Path) -> tuple[RegressionCase, ...]:
    """Load newline-delimited JSON regression cases."""

    cases: list[RegressionCase] = []
    seen: set[str] = set()
    source = Path(path)
    for line_number, raw_line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"{source}:{line_number}: each JSONL row must be an object")
        case = RegressionCase.from_dict(payload)
        if case.case_id in seen:
            raise ValueError(f"{source}:{line_number}: duplicate case id {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    return tuple(cases)


def evaluate_answer(
    case: RegressionCase,
    result: AnswerResult,
    *,
    duration_ms: int | None = None,
) -> RegressionEvaluation:
    """Apply deterministic expectations to one AnswerResult."""

    checks: list[RegressionCheck] = []
    mode = result.plan.mode.value
    rails = tuple(rail.value for rail in result.plan.route.rails)
    evidence_count = len(result.evidence_pack.items)

    if case.expected_mode is not None:
        checks.append(
            _check(
                "mode",
                mode == case.expected_mode,
                f"expected mode={case.expected_mode}, actual={mode}",
                "routing",
            )
        )

    if case.expected_statuses:
        checks.append(
            _check(
                "status",
                result.status in case.expected_statuses,
                f"expected status in {case.expected_statuses}, actual={result.status}",
                "status",
            )
        )

    if case.expected_rails:
        checks.append(
            _check(
                "rails",
                rails == case.expected_rails,
                f"expected rails={case.expected_rails}, actual={rails}",
                "routing",
            )
        )

    checks.append(
        _check(
            "min_evidence",
            evidence_count >= case.min_evidence,
            f"expected evidence>={case.min_evidence}, actual={evidence_count}",
            "retrieval",
        )
    )

    for token in case.must_include:
        checks.append(
            _check(
                f"must_include:{token}",
                token in result.answer,
                f"answer missing required text: {token}",
                "answer_correctness",
            )
        )

    for index, group in enumerate(case.must_include_any, 1):
        checks.append(
            _check(
                f"must_include_any:{index}",
                any(token in result.answer for token in group),
                f"answer missing every alternative in {group}",
                "answer_correctness",
            )
        )

    for token in case.must_not_include:
        checks.append(
            _check(
                f"must_not_include:{token}",
                token not in result.answer,
                f"answer contains forbidden text: {token}",
                "grounding",
            )
        )

    if case.evidence_report_contains is not None:
        invalid_reports = tuple(
            item.report_name
            for item in result.evidence_pack.items
            if case.evidence_report_contains not in item.report_name
        )
        checks.append(
            _check(
                "evidence_report_scope",
                not invalid_reports,
                (
                    f"all evidence reports must contain {case.evidence_report_contains!r}; "
                    f"invalid={invalid_reports}"
                ),
                "retrieval",
            )
        )

    if case.min_evidence_per_year:
        counts = _evidence_year_counts(result)
        for year, minimum in case.min_evidence_per_year:
            actual = counts.get(year, 0)
            checks.append(
                _check(
                    f"evidence_year:{year}",
                    actual >= minimum,
                    f"expected {year} evidence>={minimum}, actual={actual}",
                    "retrieval",
                )
            )

    if case.require_citations:
        citations = tuple(
            int(match.group("number")) for match in _EVIDENCE_CITATION.finditer(result.answer)
        )
        valid = bool(citations) and all(1 <= number <= evidence_count for number in citations)
        checks.append(
            _check(
                "citations",
                valid,
                f"citations={citations}, evidence_count={evidence_count}",
                "grounding",
            )
        )

    model_result = result.model_result
    return RegressionEvaluation(
        case=case,
        checks=tuple(checks),
        mode=mode,
        status=result.status,
        generator=result.generator,
        evidence_count=evidence_count,
        answer=result.answer,
        prompt_tokens=model_result.prompt_tokens if model_result else None,
        completion_tokens=model_result.completion_tokens if model_result else None,
        total_tokens=model_result.total_tokens if model_result else None,
        duration_ms=duration_ms,
    )


def evaluation_error(
    case: RegressionCase,
    error: Exception,
    *,
    duration_ms: int | None = None,
) -> RegressionEvaluation:
    """Create an evaluation record without aborting the remaining batch."""

    return RegressionEvaluation(
        case=case,
        checks=(),
        mode=None,
        status=None,
        generator=None,
        evidence_count=0,
        answer="",
        duration_ms=duration_ms,
        error=f"{type(error).__name__}: {error}",
    )


def _evidence_year_counts(result: AnswerResult) -> dict[int, int]:
    counts: dict[int, int] = {}
    for item in result.evidence_pack.items:
        match = _REPORT_YEAR.search(item.report_name)
        if match is None:
            continue
        year = int(match.group("year"))
        counts[year] = counts.get(year, 0) + 1
    return counts


def _check(name: str, passed: bool, detail: str, failure_type: str) -> RegressionCheck:
    return RegressionCheck(
        name=name,
        passed=passed,
        detail=detail,
        failure_type=failure_type,
    )
