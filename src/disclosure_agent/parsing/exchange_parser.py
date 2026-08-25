"""Parser for exchange HTML-form disclosures."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from disclosure_agent.domain.models import (
    CanonicalDisclosure, Company, DisclosureField, DisclosureMetadata,
    DisclosureType, ExchangePayload, RevisionInfo, SourceFile,
)
from disclosure_agent.parsing.table_parser import logical_html_rows, parse_table
from disclosure_agent.parsing.text_normalizer import optional_int, optional_str, parse_date
from disclosure_agent.parsing.xml_loader import load_exchange_html


class ExchangeParser:
    def parse(self, file_paths: list[str | Path], *, manifest: dict[str, Any]) -> CanonicalDisclosure:
        path = Path(file_paths[0])
        root = load_exchange_html(path)
        title_nodes = root.xpath("//title")
        title = " ".join(title_nodes[0].itertext()).strip() if title_nodes else manifest["report_nm"]
        tables = [parse_table(t, f"{manifest['doc_id']}:table:{i}", i)
                  for i, t in enumerate(root.xpath("//table"), 1)]
        fields: list[DisclosureField] = []
        order = 0
        for table in root.xpath("//table"):
            for values in logical_html_rows(table):
                if len(values) < 2:
                    continue
                label_parts, value = values[:-1], values[-1]
                if " > ".join(label_parts) == value:
                    continue
                order += 1
                fields.append(DisclosureField(
                    label=label_parts[-1], path=label_parts,
                    value=value, raw_value=value, order=order,
                ))
        receipt_date = parse_date(manifest.get("rcept_dt"))
        if receipt_date is None:
            raise ValueError(f"Invalid receipt date: {manifest.get('rcept_dt')}")
        return CanonicalDisclosure(
            document_id=str(manifest["doc_id"]),
            document_type=DisclosureType.EXCHANGE,
            company=_company(manifest),
            metadata=_metadata(manifest, receipt_date),
            revision=RevisionInfo(is_correction=bool(manifest.get("is_correction", False))),
            source_files=[SourceFile(path=str(p), file_name=Path(p).name, order=i)
                          for i, p in enumerate(file_paths)],
            payload=ExchangePayload(title=title, fields=fields, tables=tables),
        )


def _company(m: dict[str, Any]) -> Company:
    return Company(corp_code=str(m["corp_code"]), corp_name=str(m["corp_name"]),
                   listed_name=optional_str(m.get("listed_name")), stock_code=optional_str(m.get("stock_code")),
                   industry=optional_str(m.get("industry")), sector=optional_str(m.get("sector")))


def _metadata(m: dict[str, Any], receipt_date) -> DisclosureMetadata:
    return DisclosureMetadata(report_name=str(m["report_nm"]), receipt_no=str(m["rcept_no"]),
        receipt_date=receipt_date, doc_group=str(m["doc_group"]), doc_subtype=optional_str(m.get("doc_subtype")),
        filer_name=optional_str(m.get("flr_nm")), base_year=optional_int(m.get("base_year")),
        base_month=optional_int(m.get("base_month")))
