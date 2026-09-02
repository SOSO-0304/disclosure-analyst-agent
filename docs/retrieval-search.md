# Filtered retrieval and plan diagnostics

Dense-only search is now the default. Hybrid is explicitly opt-in while its quality and
latency are being evaluated. User measurements for the previous full-scope-midrank version
were 31.72/32.84 seconds overall, including 23.817/21.081 seconds in lexical SQL. All five
selected results came from the dense lane. This was not an accepted performance result.

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

Default dense search embeds only the query (one successful request, retries possible),
and does not run lexical SQL:

```powershell
python scripts/search_retrieval.py "계약상대방 계약금액 계약기간" --top-k 5

python scripts/search_retrieval.py "계약금액 계약기간" `
  --corp-code 00126478 `
  --date-from 2025-01-01 `
  --date-to 2025-12-31 `
  --document-group exchange `
  --top-k 5
```

Enable hybrid explicitly with `--mode hybrid`. This still scans eligible text; it is not
an indexed BM25 implementation and is not promised to be faster than dense-only search.

Use `--company "정확한 회사명"` instead of a corp code; stock codes are also accepted.
Known company names in the query are resolved conservatively. Ambiguous multi-company queries
stop instead of selecting one company. Use `--no-auto-company` for deliberate cross-company search.
Unknown explicit company names/codes and conflicting flags are errors, not unfiltered fallback.

Dates are inclusive **filing receipt dates**, not contract start/end dates or accounting periods.
No relative-date inference or global "latest filing" guarantee is provided.
`--corrections only|exclude|all` filters the stored correction flag; default is all.
`--mode dense` is the default; `--exact` forces exact vector search.
`--json` keeps the previous results-list JSON shape and adds ranks and citations.

## Retrieval semantics

1. Both candidate lanes use the same completed run, current content hashes and company/date/type
   scope. Punctuation-only chunks are excluded from search without modifying stored data.
2. Unfiltered dense search first selects a bounded pool from **only the embedding table**,
   ordered by cosine distance, in a materialized CTE. Run IDs are checked before its LIMIT.
   Only that pool is then joined to chunks/filings and validated against current hashes,
   meaningful content and the same scope guards. At the default 100-candidate limit, pool
   sizes are 200, 800, 3200; expansion stops as soon as 100 eligible candidates are found.
   Each larger pool replaces the earlier ranking. After bounded underfill, exact search is
   used without relaxing guards. Other candidate limits use at most three pools capped at
   3200 rows. Narrowed company/date/type/correction scopes and `--exact` retain the existing
   materialized eligible-set exact ranking, without the vector-pool attempts.
   The vector-first path sets `hnsw.ef_search=200` and `hnsw.iterative_scan=strict_order`
   **locally for the transaction** (requires pgvector >= 0.8; observed perf version: 0.8.6).
   This enables HNSW-compatible ordering and iterative scanning; it does not force the
   planner to choose HNSW or prove exact recall. See
   [pgvector iterative scans](https://github.com/pgvector/pgvector#iterative-index-scans).
3. The independent lexical lane scores substring term coverage over chunk content. It is a
   deterministic baseline, **not BM25 or Korean morphological analysis**. It scans eligible
   content and adds no disk-heavy index. Broad queries may be slower; timings are printed.
   Equal coverage scores get the same **midrank within the returned candidate window**.
   The full-scope materialization/count aggregation from the previous version was removed.
   Only the at-most-100 returned candidates (or the explicit `--candidates` limit) are
   materialized for tie ranking. For the observed 547-way top tie, 100 returned tied rows
   now share rank 50.5, not rank 274 based on all 547 documents. Both lanes therefore use
   the bounded candidate rank range. This does not force lexical results into the final set.
   Candidate ties are selected by a deterministic chunk-ID hash, not chronological ID order.
   There is no implicit newest/oldest preference. Hashes are not relevance signals.
4. Reciprocal-rank fusion (RRF, k=60) combines the independent top-100 candidate lists, using
   candidate-window midranks for the lexical contribution. `lexical_rank` may be fractional
   but is bounded by the number of returned lexical candidates.
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
| `dense_initial` | Initial vector-pool or exact SQL, including row fetch and local settings |
| `dense_expansion` | Additional bounded vector-pool attempts; zero when not needed |
| `dense_fallback` | Exact SQL after all vector pools underfill; zero when not used |
| `lexical` | Keyword SQL, candidate-window tie ranks and row fetch; zero in default dense mode |
| `fusion` | Rank fusion and candidate diagnostics |
| `hydration` | Candidate content/provenance SQL and row fetch |
| `selection` | Deduplication, diversity selection and citation construction |
| `total` | Retrieval function wall time, including small unassigned overhead |

The existing `timing seconds` line still separates query API time, whole search phase and
whole CLI operation. Its `search` includes connection/transaction overhead and can exceed
the breakdown's `total`. Skipped stages are zero; these measurements are sequential,
not concurrent. Candidate counts include `overlap`. `lexical ties` reports candidate-window
ties and whether the candidate limit was reached; **full-scope tie counts and actual cutoff
truncation are unknown**, not silently assumed zero. Per-result `candidate_ties` and JSON
`lexical_tie_count` now count returned candidates, not the whole corpus; the printed policy
is `candidate_midrank`. Reaching the candidate limit does not prove a tie was truncated.

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

No new migration, corpus embedding, or index is needed for this query change. Normal output
includes `dense candidates`: attempted pool sizes and eligible counts. `dense_strategy` is
`vector_first`, `vector_first_expanded`, `exact_fallback`, `exact_filtered` or `exact`.
It describes the requested path, **not observed index use**. Ordinary vector-first execution
reports `index_usage=not_observed`; it does not run an extra EXPLAIN. Exact-only/lexical-only
paths use `not_applicable`. Production latency and relevance must still be checked
against actual results. `--json` continues to emit only the results list (now including tied
lexical ranks/counts); timing and candidate-level diagnostics are console-mode output.

## Read-only EXPLAIN diagnostic (one run, not another corpus evaluation)

Plan-only mode asks PostgreSQL for plans without executing the candidate SELECTs. It still
reads configuration/run/company metadata and embeds the question once for a real dense plan.
Lexical-only explain needs no embedding API call:

```powershell
python scripts/search_retrieval.py "계약금액 계약기간" --mode lexical --explain
```

For actual row/buffer counts and server execution time, explicitly opt in to `--analyze`:

```powershell
python scripts/search_retrieval.py `
  "단일판매 공급계약의 계약상대방과 계약금액, 계약기간" `
  --mode dense `
  --explain `
  --analyze `
  --explain-report data\quality\retrieval-explain-v2.json
```

This embeds only the question once and runs **one initial dense SELECT**, with a 60-second
statement timeout. It does not also run normal retrieval, pool expansion, exact fallback,
lexical search or hydration. The existing company/date/type and
correction filters are shared with regular search; `--exact` uses the same exact dense SQL.
Do not rerun it in a loop. Explicit `--mode hybrid` additionally explains the lexical SELECT,
once, with its own 60-second timeout. It is not needed to verify this dense-query change.
Ordinary search can execute up to three pool queries plus one exact fallback, so its worst
case is not bounded by the diagnostic's single-query timeout.

Safety and interpretation:

- Transactions are read-only. No INSERT/UPDATE/DELETE, migration, index creation, standalone
  `ANALYZE` statistics update or persistent settings change is performed. SELECTs can still
  consume temporary disk, warm caches and add load. `EXPLAIN ANALYZE` really executes queries.
- Per-stage savepoints preserve the other plan if one query times out. Failed stages produce
  `status=partial`, SQLSTATE and exit code 1, not a success claim; raw error text is omitted.
- The report includes scan/index names, estimated vs actual rows, filter removals, root buffer
  counters, disk-sort information, JIT summary, PostgreSQL/pgvector versions and selected
  memory settings. Parent buffer counters already include children; do not sum them again.
- Schema `retrieval-explain-v2` adds `initial_vector_pool_limit`, local iterative scan settings,
  and index catalog access methods/validity/readiness. In `dense_initial.summary`,
  `vector_index_check.status=hnsw_used` requires a valid, ready HNSW index in the catalog and
  an index-plan node with actual execution loops. `hnsw_not_used` means no such execution
  was observed, even if the strategy is `vector_first`. Plan-only reports use `hnsw_planned`
  or `hnsw_not_planned`, never `hnsw_used`. Unavailable catalog data is `unknown_catalog`.
- Node timing is disabled to reduce profiling overhead. `execution_ms` is server execution
  time, not normal client fetch/network/whole-search time; profiling can still change timing.
- Raw SQL, bound query text/vector, connection URLs, credentials, Filter/Index Cond/Order By,
  Output and unrecognized plan fields are not stored. Only allowlisted plan fields survive.
- Report output is opt-in, UTF-8, and refuses to overwrite an existing file. Choose a new
  filename if it exists. Without `--explain-report`, sanitized JSON is printed to the console.
  `--json` is reserved for ordinary result lists and cannot be combined with `--explain`.

The diagnostic inspects the current implementation, not a replay of the previous slower SQL.
Its purpose is to decide from evidence whether scans, index selection, row-estimate errors,
cache reads, sorts or temporary I/O need further work. Do not infer the cause merely from
the presence of an index or a printed strategy label. See
[PostgreSQL EXPLAIN](https://www.postgresql.org/docs/current/sql-explain.html) and
[using EXPLAIN](https://www.postgresql.org/docs/current/using-explain.html).

The supplied `retrieval-explain-v1.json` showed the previous unfiltered dense query using
sequential scans/hash joins before distance sorting, with **no index use**, despite its
`ann` label. Dense server execution was about 6.855s; the hash join had eight batches and
temporary I/O (not a disk-spilling sort). This motivated the vector-only candidate boundary.
The new query's actual plan and latency are still unverified on the user's database. Shared
buffer read counters alone do not prove physical disk reads. No memory settings, statistics,
embedding data or index definitions are changed by this fix.

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
