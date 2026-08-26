"""Receipt-level orchestration from resolved sources to a canonical package."""

from __future__ import annotations

from disclosure_agent.domain.models import (
    CanonicalDocument,
    ContentFormat,
    CorpusManifestEntry,
    CorrectionType,
    FilingPackage,
    IssueSeverity,
    ParseIssue,
    ParseStatus,
    ParseSummary,
    SourceRole,
    company_from_manifest,
    correction_from_manifest,
    filing_from_manifest,
)
from disclosure_agent.inventory.builder import ResolvedSource
from disclosure_agent.parsing.dart_parser import DartParser
from disclosure_agent.parsing.exchange_parser import ExchangeParser
from disclosure_agent.parsing.pdf_parser import PdfParser
from disclosure_agent.parsing.periodic_html_parser import parse_viewer_metadata


class DocumentParser:
    """Parse every semantic source independently and preserve package relations."""

    def __init__(self) -> None:
        self.dart_parser = DartParser()
        self.exchange_parser = ExchangeParser()
        self.pdf_parser = PdfParser()

    def parse(
        self,
        sources: list[ResolvedSource],
        *,
        manifest: CorpusManifestEntry,
    ) -> FilingPackage:
        if not sources:
            raise ValueError(f"No source files for {manifest.doc_id}")

        companions = {
            source.source.companion_to_source_file_id: source
            for source in sources
            if source.source.source_role is SourceRole.COMPANION_VIEWER
        }
        documents: list[CanonicalDocument] = []
        package_issues: list[ParseIssue] = []

        for resolved in sources:
            source = resolved.source
            if source.source_role is SourceRole.COMPANION_VIEWER:
                continue
            try:
                if source.detected_content_format is ContentFormat.DART_XML:
                    document = self.dart_parser.parse(
                        resolved.path,
                        source,
                        filing_id=manifest.doc_id,
                        title=self._title(manifest, source.source_role),
                    )
                elif source.detected_content_format is ContentFormat.HTML:
                    document = self.exchange_parser.parse(
                        resolved.path,
                        source,
                        filing_id=manifest.doc_id,
                        title=self._title(manifest, source.source_role),
                    )
                elif source.detected_content_format is ContentFormat.PDF:
                    companion = companions.get(source.source_file_id)
                    metadata = None
                    companion_ids: list[str] = []
                    if companion is not None:
                        companion_ids.append(companion.source.source_file_id)
                        metadata = parse_viewer_metadata(companion.path)
                    document = self.pdf_parser.parse(
                        resolved.path,
                        source,
                        filing_id=manifest.doc_id,
                        title=self._title(manifest, source.source_role),
                        companion_source_ids=companion_ids,
                        companion_metadata=metadata,
                    )
                else:
                    document = self._unsupported_document(resolved, manifest)
            # One malformed source must not abort the remaining filing package.
            except Exception as exc:  # noqa: BLE001
                document = self._failed_document(resolved, manifest, exc)
            documents.append(document)

        if not documents:
            first_source = sources[0]
            documents.append(
                self._failed_document(
                    first_source,
                    manifest,
                    ValueError("No semantic primary source was resolved."),
                )
            )

        correction = correction_from_manifest(manifest)
        if correction.correction_type is not CorrectionType.NONE:
            package_issues.append(
                ParseIssue(
                    issue_code="correction_lineage_unresolved",
                    severity=IssueSeverity.WARNING,
                    message=(
                        "The manifest marks this filing as a correction; original filing "
                        "lineage must be resolved before retrieval."
                    ),
                )
            )

        for source in sources:
            suffix = source.source.declared_extension
            detected = source.source.detected_content_format.value
            if suffix == "xml" and detected == "html":
                package_issues.append(
                    ParseIssue(
                        issue_code="extension_content_mismatch",
                        severity=IssueSeverity.INFO,
                        message="An .xml source was correctly routed as HTML after inspection.",
                        source_file_id=source.source.source_file_id,
                    )
                )

        return FilingPackage(
            filing_id=manifest.doc_id,
            company=company_from_manifest(manifest),
            filing=filing_from_manifest(manifest),
            correction=correction,
            source_files=[resolved.source for resolved in sources],
            documents=documents,
            package_issues=package_issues,
        )

    @staticmethod
    def _title(manifest: CorpusManifestEntry, role: SourceRole) -> str:
        suffixes = {
            SourceRole.SEPARATE_AUDIT_REPORT: "별도감사보고서",
            SourceRole.CONSOLIDATED_AUDIT_REPORT: "연결감사보고서",
        }
        suffix = suffixes.get(role)
        return f"{manifest.report_nm} - {suffix}" if suffix else manifest.report_nm

    @classmethod
    def _unsupported_document(
        cls,
        resolved: ResolvedSource,
        manifest: CorpusManifestEntry,
    ) -> CanonicalDocument:
        issue = ParseIssue(
            issue_code="unsupported_source_format",
            severity=IssueSeverity.ERROR,
            message="No parser is available for the detected source content.",
            source_file_id=resolved.source.source_file_id,
        )
        return CanonicalDocument(
            document_id=f"{manifest.doc_id}:{resolved.source.source_role.value}",
            filing_id=manifest.doc_id,
            document_role=resolved.source.source_role,
            title_raw=cls._title(manifest, resolved.source.source_role),
            primary_source_file_id=resolved.source.source_file_id,
            source_file_ids=[resolved.source.source_file_id],
            parse_summary=ParseSummary(
                status=ParseStatus.UNSUPPORTED,
                parser_name="DocumentParser",
                error_count=1,
            ),
            parse_issues=[issue],
        )

    @classmethod
    def _failed_document(
        cls,
        resolved: ResolvedSource,
        manifest: CorpusManifestEntry,
        error: Exception,
    ) -> CanonicalDocument:
        issue = ParseIssue(
            issue_code="parser_exception",
            severity=IssueSeverity.ERROR,
            message=str(error) or type(error).__name__,
            source_file_id=resolved.source.source_file_id,
            details={"error_type": type(error).__name__},
        )
        return CanonicalDocument(
            document_id=f"{manifest.doc_id}:{resolved.source.source_role.value}",
            filing_id=manifest.doc_id,
            document_role=resolved.source.source_role,
            title_raw=cls._title(manifest, resolved.source.source_role),
            primary_source_file_id=resolved.source.source_file_id,
            source_file_ids=[resolved.source.source_file_id],
            parse_summary=ParseSummary(
                status=ParseStatus.FAILED,
                parser_name="DocumentParser",
                error_count=1,
            ),
            parse_issues=[issue],
        )
