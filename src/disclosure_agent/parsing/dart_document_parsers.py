"""Canonical document parsers built on the common DART XML parser."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from disclosure_agent.domain.models import (
    CanonicalDisclosure, Company, DisclosureMetadata, DisclosureType,
    HoldingPayload, MajorPayload, PeriodicPayload, RevisionInfo, SourceFile,
)
from disclosure_agent.parsing.dart_parser import DartParser
from disclosure_agent.parsing.text_normalizer import optional_int, optional_str, parse_date


class _BaseDartDocumentParser:
    document_type: DisclosureType

    def __init__(self) -> None:
        self.dart = DartParser()

    def _common(self, file_paths: list[str | Path], manifest: dict[str, Any]) -> dict[str, Any]:
        receipt_date = parse_date(manifest.get("rcept_dt"))
        if receipt_date is None:
            raise ValueError(f"Invalid receipt date: {manifest.get('rcept_dt')}")
        return dict(
            document_id=str(manifest["doc_id"]),
            document_type=self.document_type,
            company=Company(
                corp_code=str(manifest["corp_code"]), corp_name=str(manifest["corp_name"]),
                listed_name=optional_str(manifest.get("listed_name")), stock_code=optional_str(manifest.get("stock_code")),
                industry=optional_str(manifest.get("industry")), sector=optional_str(manifest.get("sector")),
            ),
            metadata=DisclosureMetadata(
                report_name=str(manifest["report_nm"]), receipt_no=str(manifest["rcept_no"]),
                receipt_date=receipt_date, doc_group=str(manifest["doc_group"]),
                doc_subtype=optional_str(manifest.get("doc_subtype")), filer_name=optional_str(manifest.get("flr_nm")),
                base_year=optional_int(manifest.get("base_year")), base_month=optional_int(manifest.get("base_month")),
            ),
            revision=RevisionInfo(is_correction=bool(manifest.get("is_correction", False))),
            source_files=[SourceFile(path=str(p), file_name=Path(p).name, order=i)
                          for i, p in enumerate(file_paths)],
        )


class PeriodicParser(_BaseDartDocumentParser):
    document_type = DisclosureType.PERIODIC

    def parse(self, file_paths: list[str | Path], *, manifest: dict[str, Any]) -> CanonicalDisclosure:
        sections, _ = self.dart.parse_files(file_paths)
        return CanonicalDisclosure(**self._common(file_paths, manifest), payload=PeriodicPayload(
            fiscal_year=optional_int(manifest.get("base_year")), sections=sections))


class MajorParser(_BaseDartDocumentParser):
    document_type = DisclosureType.MAJOR

    def parse(self, file_paths: list[str | Path], *, manifest: dict[str, Any]) -> CanonicalDisclosure:
        sections, fields = self.dart.parse_files(file_paths)
        return CanonicalDisclosure(**self._common(file_paths, manifest), payload=MajorPayload(sections=sections, fields=fields))


class HoldingParser(_BaseDartDocumentParser):
    document_type = DisclosureType.HOLDING

    def parse(self, file_paths: list[str | Path], *, manifest: dict[str, Any]) -> CanonicalDisclosure:
        sections, fields = self.dart.parse_files(file_paths)
        return CanonicalDisclosure(**self._common(file_paths, manifest), payload=HoldingPayload(sections=sections, fields=fields))
