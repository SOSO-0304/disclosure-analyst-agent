"""PDF text fallback for periodic disclosures without source XML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from disclosure_agent.domain.models import (
    BlockType,
    CanonicalBlock,
    CanonicalDocument,
    CanonicalSection,
    IssueSeverity,
    ParseIssue,
    ParseStatus,
    ParseSummary,
    SourceFile,
    SourceLocator,
)
from disclosure_agent.parsing.text_normalizer import normalize_text


class PdfParser:
    """Extract page text while explicitly reporting empty or unsupported PDFs."""

    parser_version = "2.0.0"

    def parse(
        self,
        path: str | Path,
        source: SourceFile,
        *,
        filing_id: str,
        title: str,
        companion_source_ids: list[str] | None = None,
        companion_metadata: dict[str, Any] | None = None,
    ) -> CanonicalDocument:
        from pypdf import PdfReader

        reader = PdfReader(path)
        section_id = f"{filing_id}:{source.source_file_id}:section:1"
        section = CanonicalSection(
            section_id=section_id,
            order=0,
            level=1,
            title_raw=title,
            title_normalized=normalize_text(title),
            source_locator=SourceLocator(source_file_id=source.source_file_id, page_number=1),
        )
        blocks: list[CanonicalBlock] = []
        issues: list[ParseIssue] = []
        for page_number, page in enumerate(reader.pages, 1):
            raw_text = page.extract_text() or ""
            normalized = normalize_text(raw_text)
            if normalized is None:
                issues.append(
                    ParseIssue(
                        issue_code="empty_pdf_page",
                        severity=IssueSeverity.WARNING,
                        message=f"No extractable text on PDF page {page_number}.",
                        source_file_id=source.source_file_id,
                        source_locator=SourceLocator(
                            source_file_id=source.source_file_id,
                            page_number=page_number,
                        ),
                    )
                )
                continue
            blocks.append(
                CanonicalBlock(
                    block_id=f"{filing_id}:{source.source_file_id}:block:{len(blocks) + 1}",
                    section_id=section_id,
                    order=len(blocks),
                    block_type=BlockType.PARAGRAPH,
                    text_raw=raw_text,
                    text_normalized=normalized,
                    source_locator=SourceLocator(
                        source_file_id=source.source_file_id,
                        page_number=page_number,
                    ),
                )
            )

        if not blocks:
            issues.append(
                ParseIssue(
                    issue_code="pdf_text_unavailable",
                    severity=IssueSeverity.ERROR,
                    message="The PDF contains no extractable text; OCR is required.",
                    source_file_id=source.source_file_id,
                )
            )
        errors = sum(
            issue.occurrence_count for issue in issues if issue.severity is IssueSeverity.ERROR
        )
        warnings = sum(
            issue.occurrence_count for issue in issues if issue.severity is IssueSeverity.WARNING
        )
        if not blocks:
            status = ParseStatus.FAILED
        elif issues:
            status = ParseStatus.PARTIAL
        else:
            status = ParseStatus.SUCCESS

        source_ids = [source.source_file_id, *(companion_source_ids or [])]
        return CanonicalDocument(
            document_id=f"{filing_id}:{source.source_role.value}",
            filing_id=filing_id,
            document_role=source.source_role,
            title_raw=title,
            title_normalized=normalize_text(title),
            primary_source_file_id=source.source_file_id,
            source_file_ids=source_ids,
            sections=[section],
            blocks=blocks,
            parse_summary=ParseSummary(
                status=status,
                parser_name=type(self).__name__,
                parser_version=self.parser_version,
                emitted_section_count=1,
                emitted_block_count=len(blocks),
                warning_count=warnings,
                error_count=errors,
            ),
            parse_issues=issues,
            attributes_raw=companion_metadata or {},
        )
