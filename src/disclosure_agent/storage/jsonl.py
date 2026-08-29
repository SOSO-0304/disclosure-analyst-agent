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
    """Compatibility iterator backed by the official EffectiveCanonicalReader."""

    from disclosure_agent.storage.effective_canonical import EffectiveCanonicalReader

    yield from EffectiveCanonicalReader(base_path, overlay_path)
