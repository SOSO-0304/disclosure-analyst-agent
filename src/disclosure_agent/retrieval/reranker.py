"""Lightweight lexical reranking for semantic disclosure hits."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from disclosure_agent.storage.retrieval_embedding_repository import SemanticSearchHit

_TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")
_SECTION_LINE = re.compile(r"(?m)^섹션:\s*(?P<title>.+)$")
_SUFFIXES = (
    "으로부터",
    "에서부터",
    "에게서",
    "으로",
    "에서",
    "에게",
    "부터",
    "까지",
    "처럼",
    "보다",
    "하고",
    "이며",
    "이고",
    "으로서",
    "으로써",
    "의",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "에",
    "와",
    "과",
    "도",
    "만",
    "년",
)
_STOPWORDS = frozenset(
    {
        "기준",
        "기업",
        "내용",
        "대한",
        "대해",
        "관련",
        "주요",
        "정리",
        "회사",
    }
)
_INVESTMENT_PLAN_ANCHORS = (
    "시설투자",
    "설비투자",
    "투자 계획",
    "투자계획",
    "투자 목적",
    "투자목적",
    "투자 예정",
    "투자할 계획",
    "투자를 지속",
    "투자도 진행",
    "첨단공정",
    "인프라 투자",
    "capa 확보",
    "신ㆍ증설",
    "신·증설",
    "증설ㆍ전환",
    "증설·전환",
    "투자 효율",
    "투자기간",
    "대상자산",
)
_BUSINESS_CHANGE_ANCHORS = (
    "사업 측면",
    "dx 부문",
    "ds 부문",
    "sdc",
    "harman",
    "영상디스플레이",
    "생활가전",
    "mobile experience",
    "mx(",
    "메모리 사업",
    "foundry",
    "system lsi",
    "반도체 사업",
    "galaxy",
    "갤럭시",
    "hbm",
    "neo qled",
    "영업이익",
    "부문 매출",
)
_INVESTMENT_EXCLUDED_SECTIONS = (
    "배당",
    "주주환원",
    "위험관리",
    "파생거래",
    "회사의 연혁",
    "주주에 관한",
)
_BUSINESS_CHANGE_EXCLUDED_SECTIONS = (
    "회사의 연혁",
    "배당",
    "주주에 관한",
    "임원 및 직원",
    "이사회",
    "감사제도",
    "위험관리",
    "파생거래",
    "계열회사",
)


@dataclass(frozen=True, slots=True)
class RerankedSemanticHit:
    """One semantic hit with lexical evidence and a blended score."""

    hit: SemanticSearchHit
    lexical_score: float
    final_score: float
    matched_terms: tuple[str, ...]


def _strip_suffix(token: str) -> str:
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 2:
            return token[: -len(suffix)]
    return token


def _section_title(lowered_text: str) -> str:
    match = _SECTION_LINE.search(lowered_text)
    return match.group("title").strip() if match is not None else ""


def _section_is_excluded(title: str, excluded_terms: tuple[str, ...]) -> bool:
    return any(term in title for term in excluded_terms)


def _matches_query_focus(query: str, lowered_text: str) -> bool:
    """Apply conservative content gates for narrow narrative disclosure questions."""

    compact = "".join(query.lower().split())
    section_title = _section_title(lowered_text)

    if "투자계획" in compact or "투자목적" in compact:
        if _section_is_excluded(section_title, _INVESTMENT_EXCLUDED_SECTIONS):
            return False
        return any(anchor in lowered_text for anchor in _INVESTMENT_PLAN_ANCHORS)

    business_change_query = "사업변화" in compact or (
        "핵심사업" in compact and "비교" in compact
    )
    if business_change_query:
        if _section_is_excluded(section_title, _BUSINESS_CHANGE_EXCLUDED_SECTIONS):
            return False
        return any(anchor in lowered_text for anchor in _BUSINESS_CHANGE_ANCHORS)

    return True


def query_terms(
    query: str,
    *,
    company_name: str | None = None,
    year: int | None = None,
) -> tuple[str, ...]:
    """Extract compact Korean-friendly lexical terms from a natural-language query."""

    company_token = _strip_suffix(company_name.lower()) if company_name else None
    year_token = str(year) if year is not None else None
    terms: list[str] = []
    seen: set[str] = set()

    for raw_token in _TOKEN_RE.findall(query.lower()):
        token = _strip_suffix(raw_token)
        if token in _STOPWORDS:
            continue
        if token == company_token or token == year_token:
            continue
        if len(token) < 2 and not token.isdigit():
            continue
        if token not in seen:
            seen.add(token)
            terms.append(token)

    return tuple(terms)


def rerank_semantic_hits(
    query: str,
    hits: tuple[SemanticSearchHit, ...],
    *,
    company_name: str | None = None,
    year: int | None = None,
    top_k: int = 5,
    semantic_weight: float = 0.75,
) -> tuple[RerankedSemanticHit, ...]:
    """Blend cosine similarity with IDF-weighted query-term coverage."""

    if top_k < 1:
        raise ValueError("top_k must be at least 1")
    if not 0.0 <= semantic_weight <= 1.0:
        raise ValueError("semantic_weight must be between 0 and 1")
    if not hits:
        return ()

    terms = query_terms(query, company_name=company_name, year=year)
    lowered_texts = tuple(hit.content_text.lower() for hit in hits)
    focused_pairs = tuple(
        (hit, text)
        for hit, text in zip(hits, lowered_texts, strict=True)
        if _matches_query_focus(query, text)
    )
    if not focused_pairs:
        return ()

    if not terms:
        return tuple(
            RerankedSemanticHit(
                hit=hit,
                lexical_score=0.0,
                final_score=hit.similarity,
                matched_terms=(),
            )
            for hit, _ in focused_pairs[:top_k]
        )

    focused_texts = tuple(text for _, text in focused_pairs)
    document_frequency = {
        term: sum(term in text for text in focused_texts)
        for term in terms
    }
    term_weights = {
        term: math.log((len(focused_pairs) + 1) / (document_frequency[term] + 1)) + 1.0
        for term in terms
    }
    total_weight = sum(term_weights.values())
    lexical_weight = 1.0 - semantic_weight
    reranked: list[RerankedSemanticHit] = []

    for hit, text in focused_pairs:
        matched_terms = tuple(term for term in terms if term in text)
        matched_weight = sum(term_weights[term] for term in matched_terms)
        lexical_score = matched_weight / total_weight if total_weight else 0.0
        final_score = semantic_weight * hit.similarity + lexical_weight * lexical_score
        reranked.append(
            RerankedSemanticHit(
                hit=hit,
                lexical_score=lexical_score,
                final_score=final_score,
                matched_terms=matched_terms,
            )
        )

    reranked.sort(
        key=lambda item: (item.final_score, item.hit.similarity),
        reverse=True,
    )
    return tuple(reranked[:top_k])
