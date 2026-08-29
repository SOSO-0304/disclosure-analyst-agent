"""Deterministic and compact Canonical JSONL serialization."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import orjson
from pydantic import TypeAdapter

from disclosure_agent.domain.models import FilingPackage


@dataclass(frozen=True, slots=True)
class CanonicalSerializationProfile:
    """Controls the on-disk representation without changing canonical semantics."""

    exclude_none: bool = False
    exclude_defaults: bool = False
    sort_keys: bool = True


LEGACY_CANONICAL_PROFILE = CanonicalSerializationProfile()
COMPACT_CANONICAL_PROFILE = CanonicalSerializationProfile(
    exclude_none=True,
    sort_keys=False,
)
_FILING_PACKAGE_ADAPTER = TypeAdapter(FilingPackage)


def serialize_canonical(
    package: FilingPackage,
    *,
    profile: CanonicalSerializationProfile = LEGACY_CANONICAL_PROFILE,
) -> bytes:
    """Serialize one package according to an explicit storage profile.

    The legacy profile remains byte-compatible with the accepted v2.2 JSONL.
    The compact profile avoids the intermediate ``model_dump`` dictionary,
    omits only ``None`` values, and relies on Pydantic's deterministic model
    field order instead of recursively sorting every JSON object.
    """

    if profile.sort_keys:
        return orjson.dumps(
            package.model_dump(
                mode="json",
                exclude_none=profile.exclude_none,
                exclude_defaults=profile.exclude_defaults,
            ),
            option=orjson.OPT_SORT_KEYS,
        )
    return _FILING_PACKAGE_ADAPTER.dump_json(
        package,
        exclude_none=profile.exclude_none,
        exclude_defaults=profile.exclude_defaults,
    )


class CanonicalJsonlWriter:
    """Keep one buffered output stream open for an entire canonical batch."""

    def __init__(
        self,
        path: str | Path,
        *,
        profile: CanonicalSerializationProfile = LEGACY_CANONICAL_PROFILE,
    ) -> None:
        self.path = Path(path)
        self.profile = profile
        self._stream: BinaryIO | None = None

    def __enter__(self) -> CanonicalJsonlWriter:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream = self.path.open("wb")
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def serialize(self, package: FilingPackage) -> bytes:
        """Serialize separately so callers can profile encoding and writing."""

        return serialize_canonical(package, profile=self.profile)

    def write_serialized(self, payload: bytes) -> int:
        """Write one already serialized record and return its on-disk byte count."""

        if self._stream is None:
            raise RuntimeError("CanonicalJsonlWriter must be used as a context manager")
        self._stream.write(payload)
        self._stream.write(b"\n")
        return len(payload) + 1

    def write(self, package: FilingPackage) -> int:
        """Serialize and write one package."""

        return self.write_serialized(self.serialize(package))

    def close(self) -> None:
        """Flush and close the writer if it is open."""

        if self._stream is not None:
            self._stream.close()
            self._stream = None


def append_canonical(path: str | Path, package: FilingPackage) -> None:
    """Append one validated package using the legacy representation.

    This compatibility helper intentionally opens the path per call. Batch jobs
    should use :class:`CanonicalJsonlWriter` instead.
    """

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("ab") as stream:
        stream.write(serialize_canonical(package))
        stream.write(b"\n")


def write_canonical(
    path: str | Path,
    packages: Iterable[FilingPackage],
    *,
    profile: CanonicalSerializationProfile = LEGACY_CANONICAL_PROFILE,
) -> None:
    """Replace a JSONL file with validated packages."""

    with CanonicalJsonlWriter(path, profile=profile) as writer:
        for package in packages:
            writer.write(package)


def read_canonical(path: str | Path) -> Iterator[FilingPackage]:
    """Validate every legacy or compact JSONL record while reading it."""

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
