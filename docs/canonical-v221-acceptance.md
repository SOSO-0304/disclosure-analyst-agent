# Canonical DART v2.2.1 overlay acceptance

This note records the acceptance decision for the selective DART v2.2.1 overlay.
The immutable base snapshot remains `data/processed/canonical-v22-smoke.jsonl`; the
replacement overlay is `data/processed/canonical-dart-221-overlay.jsonl` and is merged
by `filing_id` through `read_effective_canonical(...)`.

## Before / after profile

The selective overlay contained 77 filing packages and 119 DART documents.

| Metric | v2.2 base | v2.2.1 overlay |
| --- | ---: | ---: |
| DART documents compared | 119 | 119 |
| emitted tables | 117,678 | 165,213 |
| table delta |  | +47,535 |
| markup-recovery documents | 77 | 0 |
| partial -> success |  | 77 |
| success -> success |  | 42 |
| documents with table increase |  | 77 |
| documents with equal table count |  | 42 |
| documents with table decrease |  | 0 |

The recovered table delta of `+47,535` exactly matches the table gap previously
reported for the 77 structurally recovered DART documents. No DART document lost
an emitted table after the parser update.

## Independent audit

Audit input:

- `canonical-dart-221-overlay.jsonl`
- audit version `1.1.0`
- 77 packages / 119 documents / 165,213 canonical tables
- all 119 documents reported `success`
- all 119 DART documents reported parser version `2.2.1`

Source coverage reported 42 `MATCH_COUNT` documents and 77 `REVIEW` documents. The
77 reviews are expected conservative audit outcomes, not source/canonical table-count
mismatches. For every reviewed document:

- `source_table_count == canonical_table_count`
- `unmapped_canonical_tables == 0`
- `unrepresented_source_tables == 0`

Across those 77 reviewed documents, 153,431 source tables and 153,431 canonical tables
were paired by lexical XPath with no missing or extra table. The only document-scope
warnings were raw-source lexical stack warnings caused by malformed `TE` / `TH` tags:

- `Unbalanced lexical tag: TE`: 44 documents
- `Unbalanced lexical tag: TH`: 24 documents
- both TE and TH warnings: 9 documents

This is consistent with the design: the original XML stays byte-for-byte unchanged,
while the production parser creates an in-memory repair buffer for malformed `ENG`
attribute quotes. The independent auditor examines the original malformed source and
therefore remains conservative rather than importing the production repair logic.

All 127 target-text checks downgraded to `REVIEW` by those scope warnings had
`missing_occurrences == 0` and `extra_occurrences == 0`. The 18 sampled table checks
were marked `UNVERIFIED_PAIRING` because audit 1.1.0 intentionally refuses a table-cell
verdict whenever a document has a lexical scope warning; no cell mismatch was reported.

## Decision

The DART v2.2.1 overlay is accepted as the effective replacement for these 77 filing
packages for downstream source/fact/retrieval construction. Keep the v2.2 base snapshot
and the overlay separate; do not rewrite the 27 GB base JSONL.

Downstream readers should use:

```python
read_effective_canonical(
    "data/processed/canonical-v22-smoke.jsonl",
    "data/processed/canonical-dart-221-overlay.jsonl",
)
```

The 2.2.0 canonical schema version remains unchanged because this update repairs parser
behavior rather than changing the serialized data contract.
