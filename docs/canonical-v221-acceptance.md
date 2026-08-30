# Canonical v2.2.1 acceptance

This note records why the accepted downstream input is
`data/processed/canonical-v221-final.jsonl.gz`.

## Accepted final snapshot

| Metric | Result |
|---|---:|
| Filing packages | 4,204 |
| Semantic documents | 4,619 |
| Canonical tables | 1,580,832 |
| Success | 4,513 |
| Partial | 106 |
| Failed / unsupported | 0 |
| Compressed size | 1,432,113,080 bytes |

The final gzip snapshot is semantically equivalent to the compact plain JSONL generated
by the same run. A 100-package comparison also matched the legacy serialization after
excluding only the independently generated `created_at` timestamp.

The gzip output reduced the compact snapshot size by about 93% in the measured sample.
Compression changes storage only; the reader hashes logical decompressed JSONL lines so
plain and gzip representations of the same records produce the same corpus digest.

## Quality decision

The final audit reported 3,147 DART sources scanned, 3,068 table-count matches and 79
conservative reviews. Across the reviewed DART sources:

- source and Canonical table counts matched;
- no source table was unrepresented;
- no Canonical table was unmapped;
- reviewed target-text checks had no missing or extra occurrences;
- sampled comparable tables had no cell mismatch verdict.

All DART documents use parser `2.2.1` and are `success`. The remaining 106 `partial`
documents are Exchange parser diagnostics. They remain in the accepted snapshot and
must be loaded with their parse status and issues; downstream code must not silently
filter them out.

The audit is a diagnostic report, not a whole-corpus proof of perfect losslessness.
Parser work should reopen only when downstream evidence identifies a concrete source to
Canonical preservation loss.

## Historical base and overlay

The earlier acceptance process kept these artifacts separate:

- `canonical-v22-smoke.jsonl`: immutable v2.2 base;
- `canonical-dart-221-overlay.jsonl`: 77 selectively reparsed filing packages.

The overlay recovered 47,535 DART tables, exactly matching the previously observed gap,
without decreasing any affected document's table count. It remains useful for audit
reproducibility, and the reader keeps base-plus-overlay compatibility.

The production downstream input is now the single accepted gzip snapshot:

```python
from disclosure_agent.storage.jsonl import read_effective_canonical

packages = read_effective_canonical(
    "data/processed/canonical-v221-final.jsonl.gz"
)
```

Validate it before loading PostgreSQL:

```powershell
python scripts/validate_effective_canonical.py `
  --input data\processed\canonical-v221-final.jsonl.gz `
  --manifest data\processed\canonical-v221-final.manifest.json
```

The Canonical schema remains `2.2.0`; `2.2.1` is a parser behavior version, not a schema
change.
