# Retrieval embeddings

Retrieval chunk와 embedding 결과는 서로 다른 수명 주기로 관리합니다. Chunk를 다시 만들지
않고 모델·입력 포맷만 교체할 수 있도록 `embedding_runs`와 `retrieval_embeddings`를 별도
테이블로 둡니다.

## 승인 계약

| 항목 | 값 |
|---|---|
| Provider | NAVER Cloud CLOVA Studio |
| API | Embedding v2 native API |
| Model | `bge-m3` |
| Dimensions | 1,024 |
| Distance | cosine |
| Maximum input | 8,192 tokens |
| Input format | `retrieval-embedding-v1` |

공식 문서:

- https://api.ncloud-docs.com/docs/clovastudio-embeddingv2
- https://api.ncloud-docs.com/docs/clovastudio-openaicompatibility
- https://guide.ncloud-docs.com/docs/clovastudio-explorer03

Embedding v2 출력은 정규화되지 않지만 pgvector의 `vector_cosine_ops`가 cosine distance를
계산하므로 저장 전에 벡터를 임의 정규화하지 않습니다.

## 안전한 실행 순서

Perf DB 외에는 loader가 실행되지 않습니다. `disclosure_perf`, port `55432`가 아니면 즉시
중단합니다.

```powershell
alembic upgrade head

python scripts/load_retrieval_embeddings.py `
  --database-url $PerfDatabaseUrl `
  --dry-run
```

API 키는 파일이나 명령행 인자로 넘기지 않습니다.

```powershell
$env:CLOVASTUDIO_API_KEY = "발급받은_API_KEY"
```

먼저 100건만 호출하여 인증·응답 차원·DB 저장을 확인합니다.

```powershell
python scripts/load_retrieval_embeddings.py `
  --database-url $PerfDatabaseUrl `
  --limit 100 `
  --workers 4 `
  --report data\quality\embedding-smoke-100.json
```

테스트 API 키의 Embedding v2 한도는 60 QPM/40,000 TPM이고, 서비스 API 키는
540 QPM/960,000 TPM입니다. 전체 178,822건은 서비스 API 키로 실행해야 현실적인 시간 안에
끝납니다. Loader는 429와 응답의 `Retry-After`를 처리하지만, 낮은 한도를 우회하지 않습니다.

같은 명령에서 `--limit`만 제거하면 저장된 100건을 건너뛰고 나머지를 이어서 처리합니다.

```powershell
python scripts/load_retrieval_embeddings.py `
  --database-url $PerfDatabaseUrl `
  --workers 4 `
  --report data\quality\embedding-full.json
```

429 또는 서버 오류는 지수 backoff로 재시도합니다. 프로세스가 종료돼도 page 단위로 완료된
결과는 남습니다. 같은 전체 적재 명령을 다시 실행하면 content SHA가 일치하는 결과는 호출하지
않습니다.

전체 완료 후 검증합니다.

```powershell
python scripts/verify_retrieval_embeddings_db.py `
  --database-url $PerfDatabaseUrl
```

## 검색 smoke test

```powershell
python scripts/search_retrieval.py `
  "최근 공급계약의 계약상대와 계약금액을 알려줘" `
  --database-url $PerfDatabaseUrl `
  --top-k 10
```

결과에는 similarity뿐 아니라 filing/document/section/chunk/table ID와 원문 text가 함께
반환됩니다. 이 provenance가 이후 reranker, Context Builder, citation 응답의 기준입니다.

## 운영 원칙

- API key는 Git과 DB에 저장하지 않습니다.
- 100건 smoke가 성공하기 전 전체 호출을 시작하지 않습니다.
- `partial` embedding run은 검색 production 기본값으로 선택하지 않습니다.
- 전체 coverage와 content SHA가 검증된 run만 `is_active=true`가 됩니다.
- 모델이나 input format이 바뀌면 새 run ID가 생성되며 기존 벡터를 덮어쓰지 않습니다.
