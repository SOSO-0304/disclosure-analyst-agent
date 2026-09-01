# Filtered hybrid retrieval

## Frozen data contract

- Chunk run: `c700b5a3a6cfa055be147bc71dd478ca`
- Completed v2 embedding run: `1220f708cc241e9a00d0b40cbddea7da`
- Verified embeddings: 178,822; stored input tokens: 79,291,717.
- No re-chunking, re-embedding, DB migration, index creation or run deletion is required.
- Search uses read-only transactions and a 60-second SQL statement timeout.

## Configuration once

Keep the existing `.env.perf` at the repository root. Add the raw service API key there:

```dotenv
PERF_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:55432/disclosure_perf
CLOVASTUDIO_API_KEY=YOUR_SERVICE_API_KEY
```

Do not replace the working DB URL with the example above. Never commit this file or put keys in
`.env.perf.example`. Check `git ls-files -- .env.perf` is empty before storing secrets.
The file is plaintext, not an encrypted credential store.

The search, embedding loader, variant evaluator, manual review and embedding DB verifier now read
this file automatically. No PowerShell variables are needed. For another location, use
`--env-file PATH`. Explicit `--database-url` takes precedence; otherwise
`PERF_DATABASE_URL` from the process takes precedence over the file. Existing API key environment
variables also take precedence. The unrelated `DATABASE_URL` and `.env` are never fallback DB
sources for these perf commands. Only PostgreSQL localhost/loopback:55432/disclosure_perf is allowed.
Configuration is read without exporting secrets into global process environment.

## Commands

After pulling, install the explicitly declared dotenv dependency if needed:

```powershell
python -m pip install "python-dotenv>=1.0,<2"
python scripts/verify_retrieval_embeddings_db.py
```

Keyword-only search uses no API key/calls:

```powershell
python scripts/search_retrieval.py "계약상대방 계약금액 계약기간" --mode lexical --top-k 5
```

Hybrid search embeds only the query (one successful request, retries possible):

```powershell
python scripts/search_retrieval.py "계약상대방 계약금액 계약기간" --top-k 5

python scripts/search_retrieval.py "계약금액 계약기간" `
  --corp-code 00126478 `
  --date-from 2025-01-01 `
  --date-to 2025-12-31 `
  --document-group exchange `
  --top-k 5
```

Use `--company "정확한 회사명"` instead of a corp code; stock codes are also accepted.
Known company names in the query are resolved conservatively. Ambiguous multi-company queries
stop instead of selecting one company. Use `--no-auto-company` for deliberate cross-company search.
Unknown explicit company names/codes and conflicting flags are errors, not unfiltered fallback.

Dates are inclusive **filing receipt dates**, not contract start/end dates or accounting periods.
No relative-date inference or global "latest filing" guarantee is provided.
`--corrections only|exclude|all` filters the stored correction flag; default is all.
`--mode dense` retains dense-only search; `--exact` forces exact vector search.
`--json` keeps the previous results-list JSON shape and adds ranks and citations.

## Retrieval semantics

1. Both candidate lanes use the same completed run, current content hashes and company/date/type
   scope. Punctuation-only chunks are excluded from search without modifying stored data.
2. The dense lane uses HNSW-compatible ordering for unfiltered requests. Narrowed scopes use a
   materialized candidate set and exact vector ranking to avoid ANN post-filter underfill.
   Unfiltered ANN candidate underfill triggers exact fallback, never removal of filters.
   A full ANN candidate list does not prove exact recall.
3. The independent lexical lane scores substring term coverage over chunk content. It is a
   deterministic baseline, **not BM25 or Korean morphological analysis**. It scans eligible
   content and adds no disk-heavy index. Broad queries may be slower; timings are printed.
   Equal coverage scores get the same **midrank over the entire filtered eligible scope**,
   calculated before the candidate limit. For example, 1,000 equally highest-scoring chunks
   occupy positions 1..1,000, so every one gets rank 500.5, not arbitrary ranks 1..100 for
   the returned subset. Lower score groups start after all better-scoring matches.
   This keeps a large weakly differentiated group from receiving a false first-place boost.
   Candidate ties are selected by a deterministic chunk-ID hash, not chronological ID order.
   There is no implicit newest/oldest preference. Hashes are not relevance signals.
4. Reciprocal-rank fusion (RRF, k=60) combines the independent top-100 candidate lists, using
   the scoped midranks for the lexical contribution. `lexical_rank` may be fractional or
   larger than 100; it describes the full scoped score group, not returned-list position.
   Final equal RRF scores also use a deterministic hash tie-breaker. The chosen lexical
   subset still omits members of large tie groups; this does not guarantee improved recall
   or overlap. RRF/cosine scores are not answer confidence or probabilities.
5. Default selection permits one chunk per filing and one fragment per source table. A soft
   two-results-per-company limit promotes diversity for unfiltered queries, then backfills if
   other companies have insufficient candidates. This does not guarantee company balance.
   `--max-per-filing`, `--company-cap` and `--candidates` expose these limits.

## Per-stage timing and short comparison

The CLI prints `search breakdown` in seconds:

| Field | Measured work |
|---|---|
| `dense_initial` | Initial ANN or exact vector SQL, including row fetch and HNSW setting |
| `dense_fallback` | Additional exact SQL after ANN underfill; zero when not used |
| `lexical` | Keyword SQL, scoped tie-group ranks and candidate row fetch |
| `fusion` | Rank fusion and candidate diagnostics |
| `hydration` | Candidate content/provenance SQL and row fetch |
| `selection` | Deduplication, diversity selection and citation construction |
| `total` | Retrieval function wall time, including small unassigned overhead |

The existing `timing seconds` line still separates query API time, whole search phase and
whole CLI operation. Its `search` includes connection/transaction overhead and can exceed
the breakdown's `total`. Skipped stages are zero; these measurements are sequential,
not concurrent. Candidate counts now include `overlap`, and `lexical ties` reports whether
the candidate boundary cuts a larger equal-score group. Each result shows matched-term
count and full scoped tie count.

Run the same query twice to distinguish a cold run from a warm one. Do not infer a sustained
speedup from one measurement or compare different query texts as a controlled benchmark.
Both calls reuse the existing vectors and each embeds only its query:

```powershell
1..2 | ForEach-Object {
    python scripts/search_retrieval.py `
      "단일판매 공급계약의 계약상대방과 계약금액, 계약기간" `
      --top-k 5
}
```

No new migration, corpus embedding, or index is needed for this ranking change. It fixes
ID-order bias and adds diagnostics; production latency and relevance must still be checked
against actual results. `--json` continues to emit only the results list (now including tied
lexical ranks/counts); timing and candidate-level diagnostics are console-mode output.

## Citations and correction boundary

Each result includes the company, filing receipt date, report title, correction flag, raw chunk
content, embedding run, document/section/table/block/chunk IDs and a DART receipt-level viewer URL.
URLs are constructed from OpenDART receipt numbers, not verified by live fetching or asserted to
be deep links to a specific table. See the
[OpenDART viewer contract](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS002&apiId=2019007).

The source DB has `is_correction`, but no populated original-to-correction relation in its filing
table. Results therefore explicitly say `lineage_status=not_resolved`. Reports with the same title
are not merged, later dates do not automatically supersede earlier contracts, and non-correction
does not mean "latest effective version". Fact extraction, full table expansion and authoritative
correction lineage remain subsequent work; these results are evidence candidates, not generated
financial answers.

Implementation references:
[pgvector filtering](https://github.com/pgvector/pgvector#filtering),
[python-dotenv configuration](https://bbc2.github.io/python-dotenv/).
