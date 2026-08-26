"""Resolve manifest rows into validated, content-aware source inventories."""

from __future__ import annotations

import hashlib
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from disclosure_agent.domain.models import CorpusManifestEntry, SourceFile, SourceRole
from disclosure_agent.parsing.content_detector import (
    choose_parser_profile,
    detect_content_format,
)

SUPPORTED_SOURCE_SUFFIXES = {".xml", ".html", ".htm", ".pdf"}


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    """Runtime path paired with its portable canonical source metadata."""

    path: Path
    source: SourceFile


def _normalize_path(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\\", "/"))


def _receipt_from_file_name(path: Path) -> str | None:
    receipt = path.name.split("_", 1)[0].split(".", 1)[0]
    return receipt if len(receipt) == 14 and receipt.isdigit() else None


def _source_priority(path: Path) -> tuple[int, str]:
    stem = path.stem.lower()
    suffix = path.suffix.lower()
    if stem.endswith("_00760"):
        rank = 1
    elif stem.endswith("_00761"):
        rank = 2
    elif suffix in {".xml", ".pdf"}:
        rank = 0
    elif suffix in {".html", ".htm"} and "viewer" in stem:
        rank = 3
    else:
        rank = 4
    return rank, _normalize_path(str(path))


def _source_role(path: Path, receipt_number: str) -> SourceRole:
    stem = path.stem.lower()
    if stem.endswith("_00760"):
        return SourceRole.SEPARATE_AUDIT_REPORT
    if stem.endswith("_00761"):
        return SourceRole.CONSOLIDATED_AUDIT_REPORT
    if "viewer" in stem:
        return SourceRole.COMPANION_VIEWER
    if stem == receipt_number or path.suffix.lower() == ".pdf":
        return SourceRole.PRIMARY_REPORT
    return SourceRole.ATTACHMENT


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class InventoryBuilder:
    """Build one receipt-level inventory while validating source coverage."""

    def __init__(self, corpus_root: str | Path, *, compute_hashes: bool = True) -> None:
        self.corpus_root = Path(corpus_root)
        self.compute_hashes = compute_hashes
        self._source_index: dict[str, list[Path]] | None = None

    def build_source_index(self) -> dict[str, list[Path]]:
        """Scan supported files once and index them by DART receipt number."""

        index: dict[str, list[Path]] = defaultdict(list)
        raw_root = self.corpus_root / "raw"
        if raw_root.exists():
            for path in raw_root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in SUPPORTED_SOURCE_SUFFIXES:
                    continue
                receipt = _receipt_from_file_name(path)
                if receipt is not None:
                    index[receipt].append(path)
        for paths in index.values():
            paths.sort(key=_source_priority)
        self._source_index = dict(index)
        return self._source_index

    @property
    def source_index(self) -> dict[str, list[Path]]:
        if self._source_index is None:
            return self.build_source_index()
        return self._source_index

    def resolve_paths(self, entry: CorpusManifestEntry) -> list[Path]:
        """Resolve manifest paths, then safely fall back to the receipt index."""

        candidates = [
            self.corpus_root / entry.normalized_file_path,
            self.corpus_root / "raw" / entry.normalized_file_path,
        ]
        for marker in ("corpus/reports/", "reports/", "raw/"):
            if marker in entry.normalized_file_path:
                relative = entry.normalized_file_path.split(marker, 1)[1]
                candidates.append(self.corpus_root / "raw" / relative)

        for candidate in candidates:
            if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_SOURCE_SUFFIXES:
                return [candidate]
            if candidate.is_dir():
                files = sorted(
                    (
                        path
                        for path in candidate.iterdir()
                        if path.is_file() and path.suffix.lower() in SUPPORTED_SOURCE_SUFFIXES
                    ),
                    key=_source_priority,
                )
                if files:
                    return files

        return list(self.source_index.get(entry.rcept_no, []))

    def build(
        self,
        manifest: CorpusManifestEntry | dict[str, Any],
    ) -> tuple[CorpusManifestEntry, list[ResolvedSource]]:
        """Validate a manifest row and construct its ordered source records."""

        entry = (
            manifest
            if isinstance(manifest, CorpusManifestEntry)
            else CorpusManifestEntry.model_validate(manifest)
        )
        paths = self.resolve_paths(entry)
        if not paths:
            raise FileNotFoundError(entry.normalized_file_path)
        if len(paths) != entry.n_files:
            raise ValueError(
                f"source count mismatch for {entry.doc_id}: "
                f"manifest={entry.n_files}, resolved={len(paths)}"
            )

        roles = [_source_role(path, entry.rcept_no) for path in paths]
        primary_index = next(
            (index for index, role in enumerate(roles) if role is SourceRole.PRIMARY_REPORT),
            0,
        )
        source_ids = [f"{entry.doc_id}:source:{index + 1}" for index in range(len(paths))]
        primary_source_id = source_ids[primary_index]

        resolved: list[ResolvedSource] = []
        for index, (path, role, source_id) in enumerate(zip(paths, roles, source_ids, strict=True)):
            relative_path_raw = str(path.relative_to(self.corpus_root)).replace("\\", "/")
            relative_path_normalized = _normalize_path(relative_path_raw)
            content_format = detect_content_format(path)
            companion_to = primary_source_id if role is SourceRole.COMPANION_VIEWER else None
            source = SourceFile(
                source_file_id=source_id,
                archive_path_raw=relative_path_raw,
                archive_path_normalized=relative_path_normalized,
                file_name=unicodedata.normalize("NFC", path.name),
                source_role=role,
                declared_extension=path.suffix,
                detected_content_format=content_format,
                parser_profile=choose_parser_profile(content_format, role),
                sha256=_sha256(path) if self.compute_hashes else None,
                size_bytes=path.stat().st_size,
                is_primary=index == primary_index,
                companion_to_source_file_id=companion_to,
            )
            resolved.append(ResolvedSource(path=path, source=source))
        return entry, resolved


def validate_manifest_rows(rows: Iterable[dict[str, Any]]) -> list[CorpusManifestEntry]:
    """Validate a manifest before any expensive source parsing begins."""

    entries = [CorpusManifestEntry.model_validate(row) for row in rows]
    document_ids = [entry.doc_id for entry in entries]
    receipt_numbers = [entry.rcept_no for entry in entries]
    if len(document_ids) != len(set(document_ids)):
        raise ValueError("manifest contains duplicate doc_id values")
    if len(receipt_numbers) != len(set(receipt_numbers)):
        raise ValueError("manifest contains duplicate rcept_no values")
    return entries
