"""JSONL storage adapter."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator

import orjson

from disclosure_agent.domain.models import CanonicalDisclosure


def append_canonical(path: str | Path, document: CanonicalDisclosure) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("ab") as fp:
        fp.write(orjson.dumps(document.model_dump(mode="json")))
        fp.write(b"\n")


def write_canonical(path: str | Path, documents: Iterable[CanonicalDisclosure]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as fp:
        for document in documents:
            fp.write(orjson.dumps(document.model_dump(mode="json")))
            fp.write(b"\n")


def read_canonical(path: str | Path) -> Iterator[CanonicalDisclosure]:
    with Path(path).open("rb") as fp:
        for line in fp:
            if line.strip():
                yield CanonicalDisclosure.model_validate(orjson.loads(line))
