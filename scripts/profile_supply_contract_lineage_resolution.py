"""Profile deterministic in-corpus Supply Contract correction lineage resolution."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from disclosure_agent.extractors.exchange_fields import ExchangeFieldReader
from disclosure_agent.extractors.supply_contract import extract_supply_contract
from disclosure_agent.extractors.supply_contract_lineage import (
    MATCH_FIELDS,
    RELATED_FILING_DATE_PATH,
    LineageResolutionStatus,
    resolve_supply_contract_lineage,
)
from disclosure_agent.storage.jsonl import read_canonical

SUBSET = Path("data/processed/subsets/supply-contract-v22.jsonl")


def _normalise(value: object | None) -> str | None:
    if value is None:
        return None
    text = "".join(str(value).split()).lower()
    return text or None


def _related_date_raw(package, reader: ExchangeFieldReader) -> str | None:
    for field in reader.read_package(package):
        if field.path_key == RELATED_FILING_DATE_PATH:
            return field.value
    return None


def main() -> None:
    if not SUBSET.is_file():
        raise SystemExit(f"Subset not found: {SUBSET}")

    packages = list(read_canonical(SUBSET))
    by_filing_id = {package.filing_id: package for package in packages}
    reader = ExchangeFieldReader()
    events = {
        package.filing_id: extract_supply_contract(package, reader=reader).event
        for package in packages
    }
    lineage = resolve_supply_contract_lineage(packages, reader=reader)
    counts = Counter(link.status for link in lineage.links)

    direct = counts[LineageResolutionStatus.RESOLVED]
    fingerprint = counts[LineageResolutionStatus.RESOLVED_BY_FINGERPRINT]
    resolved = direct + fingerprint
    total = len(lineage.links)
    unique_roots = len(set(lineage.root_by_filing_id.values()))

    print("=== supply contract lineage resolution ===")
    print(f"packages                         {len(packages)}")
    print(f"corrections                      {total}")
    print(f"resolved direct predecessors     {direct}/{total}")
    print(f"resolved by fingerprint          {fingerprint}/{total}")
    print(f"resolved total                   {resolved}/{total}")
    print(f"unique in-corpus roots           {unique_roots}")
    print()
    print("status                         count   coverage")
    print("-----------------------------  ------  --------")
    for status in LineageResolutionStatus:
        count = counts[status]
        coverage = count / total * 100 if total else 0.0
        print(f"{status.value:<29}  {count:>6}  {coverage:>7.2f}%")

    fingerprint_links = [
        link
        for link in lineage.links
        if link.status is LineageResolutionStatus.RESOLVED_BY_FINGERPRINT
    ]
    if fingerprint_links:
        print()
        print("=== fingerprint resolution examples ===")
        for link in fingerprint_links[:20]:
            print(
                f"receipt={link.correction_receipt_number} "
                f"related_date={link.related_filing_date} "
                f"candidates={len(link.candidate_filing_ids)} "
                f"score={link.fingerprint_score}/{link.fingerprint_compared} "
                f"predecessor={link.predecessor_receipt_number}"
            )

    ambiguous = [
        link
        for link in lineage.links
        if link.status is LineageResolutionStatus.AMBIGUOUS
    ]
    if ambiguous:
        print()
        print("=== ambiguous details ===")
        for link in ambiguous:
            correction = by_filing_id[link.correction_filing_id]
            correction_event = events[correction.filing_id]
            print(
                f"receipt={link.correction_receipt_number} "
                f"related_date={link.related_filing_date}"
            )
            for candidate_id in link.candidate_filing_ids:
                candidate = by_filing_id[candidate_id]
                candidate_event = events[candidate_id]
                matches = []
                differences = []
                for field_name in MATCH_FIELDS:
                    current = _normalise(getattr(correction_event, field_name, None))
                    previous = _normalise(getattr(candidate_event, field_name, None))
                    if current is None or previous is None:
                        continue
                    target = matches if current == previous else differences
                    target.append(field_name)
                print(
                    "  "
                    f"candidate={candidate.filing.receipt_number} "
                    f"matches={','.join(matches) or '-'} "
                    f"differs={','.join(differences) or '-'}"
                )

    missing_related_date = [
        link
        for link in lineage.links
        if link.status is LineageResolutionStatus.MISSING_RELATED_DATE
    ]
    if missing_related_date:
        print()
        print("=== missing related date raw values ===")
        for link in missing_related_date:
            package = by_filing_id[link.correction_filing_id]
            raw = _related_date_raw(package, reader)
            print(f"{link.correction_receipt_number}  {raw!r}")

    unresolved = [
        link
        for link in lineage.links
        if link.status
        not in {
            LineageResolutionStatus.RESOLVED,
            LineageResolutionStatus.RESOLVED_BY_FINGERPRINT,
        }
    ]
    if unresolved:
        print()
        print("=== unresolved examples ===")
        for link in unresolved[:30]:
            related = link.related_filing_date.isoformat() if link.related_filing_date else "-"
            print(
                f"{link.status.value:<23} "
                f"receipt={link.correction_receipt_number} "
                f"related_date={related} "
                f"candidates={len(link.candidate_filing_ids)}"
            )


if __name__ == "__main__":
    main()
