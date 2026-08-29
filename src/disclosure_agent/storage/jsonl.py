"""Deterministic Canonical JSONL serialization."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path

import orjson

from disclosure_agent.domain.models import FilingPackage


def append_canonical(path: str | Path, package: FilingPackage) -> None:
    """Append one validated filing package as one JSONL record."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("ab") as stream:
        stream.write(orjson.dumps(package.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS))
        stream.write(b"\n")


def write_canonical(path: str | Path, packages: Iterable[FilingPackage]) -> None:
    """Replace a JSONL file with validated packages."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as stream:
        for package in packages:
            stream.write(
                orjson.dumps(
                    package.model_dump(mode="json"),
                    option=orjson.OPT_SORT_KEYS,
                )
            )
            stream.write(b"\n")


def read_canonical(path: str | Path) -> Iterator[FilingPackage]:
    """Validate every JSONL record while reading it."""

    with Path(path).open("rb") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                yield FilingPackage.model_validate(orjson.loads(line))
            except Exception as exc:
                raise ValueError(f"Invalid canonical JSONL record at line {line_number}") from exc


def read_effective_canonical(
    base_path: str | Path,
    overlay_path: str | Path,
) -> Iterator[FilingPackage]:
    """Stream base packages while replacing matching filing IDs from a validated overlay.

    ``filing_id`` is the merge key.  Receipt number and company identity are checked
    before replacement so an overlay cannot silently replace a different filing.
    Overlay-only filing IDs are rejected after the base stream is exhausted.
    """

    overlay_by_id: dict[str, FilingPackage] = {}
    for package in read_canonical(overlay_path):
        if package.filing_id in overlay_by_id:
            raise ValueError(f"Duplicate overlay filing_id: {package.filing_id}")
        overlay_by_id[package.filing_id] = package

    seen_overlay_ids: set[str] = set()
    for base in read_canonical(base_path):
        replacement = overlay_by_id.get(base.filing_id)
        if replacement is None:
            yield base
            continue
        if replacement.filing.receipt_number != base.filing.receipt_number:
            raise ValueError(
                f"Overlay receipt mismatch for {base.filing_id}: "
                f"base={base.filing.receipt_number}, "
                f"overlay={replacement.filing.receipt_number}"
            )
        if replacement.company.corp_code != base.company.corp_code:
            raise ValueError(
                f"Overlay company mismatch for {base.filing_id}: "
                f"base={base.company.corp_code}, overlay={replacement.company.corp_code}"
            )
        seen_overlay_ids.add(base.filing_id)
        yield replacement

    unknown = sorted(set(overlay_by_id) - seen_overlay_ids)
    if unknown:
        raise ValueError(f"Overlay filing IDs are absent from base snapshot: {unknown[:10]}")
