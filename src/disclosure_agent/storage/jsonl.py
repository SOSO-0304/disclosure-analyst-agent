"""Deterministic and compact Canonical JSONL serialization."""

from __future__ import annotations

import gzip
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal

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
CanonicalCompression = Literal["none", "gzip"]


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
        compression: CanonicalCompression = "none",
    ) -> None:
        if compression not in {"none", "gzip"}:
            raise ValueError(f"Unsupported canonical compression: {compression}")
        self.path = Path(path)
        self.profile = profile
        self.compression = compression
        self._raw_stream: BinaryIO | None = None
        self._stream: BinaryIO | None = None

    def __enter__(self) -> CanonicalJsonlWriter:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._raw_stream = self.path.open("wb")
        if self.compression == "gzip":
            self._stream = gzip.GzipFile(
                filename="",
                mode="wb",
                compresslevel=1,
                fileobj=self._raw_stream,
                mtime=0,
            )
        else:
            self._stream = self._raw_stream
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
        if self._raw_stream is not None:
            self._raw_stream.close()
            self._raw_stream = None


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
    compression: CanonicalCompression = "none",
) -> None:
    """Replace a JSONL file with validated packages."""

    with CanonicalJsonlWriter(path, profile=profile, compression=compression) as writer:
        for package in packages:
            writer.write(package)


def iter_canonical_lines(path: str | Path) -> Iterator[bytes]:
    """Yield logical JSONL lines while auto-detecting gzip by its magic bytes."""

    with Path(path).open("rb") as raw_stream:
        compressed = raw_stream.peek(2)[:2] == b"\x1f\x8b"
        stream = gzip.GzipFile(fileobj=raw_stream, mode="rb") if compressed else raw_stream
        try:
            yield from stream
        finally:
            if stream is not raw_stream:
                stream.close()


def read_canonical(path: str | Path) -> Iterator[FilingPackage]:
    """Validate every plain or gzip-compressed canonical JSONL record."""

    for line_number, line in enumerate(iter_canonical_lines(path), 1):
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
