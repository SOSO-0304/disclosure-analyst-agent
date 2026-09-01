"""Deterministic company resolution, lexical probes, fusion and provenance.

Lexical matching is substring coverage, not BM25 or a Korean morphological parser.
No correction lineage is inferred from report titles or publication dates.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any


def _name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", "", value.replace("주식회사", "").replace("(주)", ""))


def resolve_company(
    companies: Sequence[Mapping[str, Any]],
    query: str,
    *,
    company: str | None = None,
    corp_code: str | None = None,
    auto: bool = True,
) -> dict[str, Any] | None:
    def aliases(row: Mapping[str, Any]) -> set[str]:
        return {_name(str(row.get(k) or "")) for k in ("corp_name", "listed_name")}

    if company is not None:
        wanted = _name(company)
        if not wanted:
            raise ValueError("--company must not be empty")
        matches = [
            r
            for r in companies
            if wanted in aliases(r)
            or wanted in {str(r["corp_code"]), str(r.get("stock_code") or "")}
        ]
    elif corp_code is not None:
        matches = [r for r in companies if str(r["corp_code"]) == corp_code]
    elif auto:
        normalized = unicodedata.normalize("NFKC", query).casefold()
        spans: list[tuple[int, int, Mapping[str, Any]]] = []
        for row in companies:
            for alias in aliases(row):
                if len(alias) < 2:
                    continue
                pattern = (
                    r"(?<![a-z0-9가-힣])"
                    + r"\s*".join(map(re.escape, alias))
                    + r"(?=$|[^a-z0-9가-힣]|(?:의|은|는|이|가|을|를|와|과|에서|에|도)"
                    + r"(?=$|[^a-z0-9가-힣]))"
                )
                spans.extend((m.start(), m.end(), row) for m in re.finditer(pattern, normalized))
        matches = [
            row
            for start, end, row in spans
            if not any(a <= start and end <= b and (a, b) != (start, end) for a, b, _ in spans)
        ]
        if not matches:
            return None
    else:
        return None
    unique = {str(r["corp_code"]): dict(r) for r in matches}
    if len(unique) != 1:
        if not unique:
            raise ValueError(
                "Company not found; use an exact company name, stock code or corp code"
            )
        names = ", ".join(str(r.get("listed_name") or r["corp_name"]) for r in unique.values())
        raise ValueError(
            f"Multiple companies matched ({names}); specify --company or --no-auto-company"
        )
    result = next(iter(unique.values()))
    if corp_code is not None and str(result["corp_code"]) != corp_code:
        raise ValueError("--company and --corp-code identify different companies")
    return result


def validate_dates(date_from: str | None, date_to: str | None) -> tuple[date | None, date | None]:
    def parse(value: str | None) -> date | None:
        if value is None:
            return None
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Dates must use YYYY-MM-DD")
        return date.fromisoformat(value)

    start, end = parse(date_from), parse(date_to)
    if start and end and start > end:
        raise ValueError("--date-from must not be later than --date-to")
    return start, end


def lexical_terms(query: str) -> list[str]:
    stop = {"관련", "내용", "공시", "최근", "최신", "알려줘", "알려주세요", "보여줘", "대한"}
    query = unicodedata.normalize("NFKC", query).casefold()
    terms = []
    for token in re.findall(r"[a-z0-9가-힣]+", query):
        for suffix in ("에서", "으로", "에는", "의", "과", "와", "을", "를", "은", "는"):
            if token.endswith(suffix) and len(token) - len(suffix) >= 2:
                token = token[: -len(suffix)]
                break
        token = {"계약상대방": "계약상대"}.get(token, token)
        if len(token) >= 2 and token not in stop and token not in terms:
            terms.append(token)
    return terms[:12]


def fuse_rankings(
    dense: Sequence[Mapping[str, Any]], lexical: Sequence[Mapping[str, Any]], *, k: int = 60
) -> list[dict[str, Any]]:
    if k <= 0:
        raise ValueError("RRF k must be positive")
    values: dict[str, dict[str, Any]] = {}
    for lane, rows in (("dense", dense), ("lexical", lexical)):
        seen = set()
        for rank, row in enumerate(rows, 1):
            key = str(row["chunk_id"])
            if key in seen:
                continue
            seen.add(key)
            item = values.setdefault(
                key,
                {
                    "chunk_id": key,
                    "rrf_score": 0.0,
                    "dense_rank": None,
                    "lexical_rank": None,
                    "similarity": None,
                    "lexical_score": None,
                },
            )
            item["rrf_score"] += 1.0 / (k + rank)
            item[f"{lane}_rank"] = rank
            metric = "similarity" if lane == "dense" else "lexical_score"
            item[metric] = row.get(metric)
    return sorted(values.values(), key=lambda r: (-r["rrf_score"], r["chunk_id"]))


def select_evidence(
    rows: Sequence[dict[str, Any]],
    *,
    top_k: int,
    max_per_filing: int = 1,
    company_cap: int | None = 2,
) -> list[dict[str, Any]]:
    if top_k < 1 or max_per_filing < 1 or (company_cap is not None and company_cap < 1):
        raise ValueError("Selection limits must be positive")
    selected: list[dict[str, Any]] = []
    seen_chunks: set[str] = set()
    seen_sources: set[tuple[str, str]] = set()
    filings: Counter[str] = Counter()
    companies: Counter[str] = Counter()
    # Diversity is a soft cap: backfill if insufficient companies have candidates.
    for enforce_company_cap in (True, False):
        for row in rows:
            chunk, filing, corp = str(row["chunk_id"]), str(row["filing_id"]), str(row["corp_code"])
            source = (filing, str(row.get("source_table_id") or row.get("content_sha256") or chunk))
            if chunk in seen_chunks or source in seen_sources or filings[filing] >= max_per_filing:
                continue
            if enforce_company_cap and company_cap and companies[corp] >= company_cap:
                continue
            selected.append(row)
            seen_chunks.add(chunk)
            seen_sources.add(source)
            filings[filing] += 1
            companies[corp] += 1
            if len(selected) == top_k:
                return selected
    return selected


def citation(row: Mapping[str, Any]) -> dict[str, Any]:
    receipt = str(row["receipt_number"])
    # The source manifest uses OpenDART's rcept_no, including exchange filings.
    url = (
        f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}"
        if re.fullmatch(r"\d{14}", receipt)
        else None
    )
    return {
        "receipt_number": receipt,
        "url": url,
        "url_status": "constructed_from_receipt" if url else "unavailable",
        "filing_id": row["filing_id"],
        "document_id": row["document_id"],
        "section_id": row.get("section_id"),
        "chunk_id": row["chunk_id"],
        "source_table_id": row.get("source_table_id"),
        "source_block_ids": row.get("source_block_ids") or [],
        "correction_status": "correction" if row["is_correction"] else "non_correction",
        "lineage_status": "not_resolved",
    }
