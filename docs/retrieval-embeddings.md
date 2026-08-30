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

## 전체 적재 전 문맥 평가

전체 corpus를 임베딩하기 전에 회사·공시그룹·chunk 유형별 결정론적 층화 표본으로
입력 포맷을 비교합니다. 같은 seed와 `--sample-per-stratum`을 사용하면 v1과 v2가 정확히
같은 chunk 집합을 대상으로 합니다.

- v1: heading path + 원문
- v2: 회사명·종목코드·보고서명·공시 세부 유형·문서 제목·정정 여부·heading path·표 제목
  + 원문

각 입력 버전은 서로 다른 embedding run ID를 사용하므로 기존 v1 결과를 수정하거나
삭제하지 않습니다. 우선 API 호출 없는 dry-run으로 표본 수를 확인합니다.

```powershell
python scripts/load_retrieval_embeddings.py `
  --database-url $PerfDatabaseUrl `
  --input-version retrieval-embedding-v1 `
  --sample-per-stratum 5 `
  --dry-run

python scripts/load_retrieval_embeddings.py `
  --database-url $PerfDatabaseUrl `
  --input-version retrieval-embedding-v2 `
  --sample-per-stratum 5 `
  --dry-run
```

표본 계약이 확인된 뒤 두 버전을 적재합니다.

```powershell
python scripts/load_retrieval_embeddings.py `
  --database-url $PerfDatabaseUrl `
  --input-version retrieval-embedding-v1 `
  --sample-per-stratum 5 `
  --workers 8 `
  --requests-per-minute 480 `
  --report data\quality\embedding-eval-v1.json

python scripts/load_retrieval_embeddings.py `
  --database-url $PerfDatabaseUrl `
  --input-version retrieval-embedding-v2 `
  --sample-per-stratum 5 `
  --workers 8 `
  --requests-per-minute 480 `
  --report data\quality\embedding-eval-v2.json
```

리포트의 `scope.sample_completed=true`, `call failed=0`을 확인한 뒤 검색 평가를 수행합니다.
입력 버전을 승인하기 전에는 `--sample-per-stratum`을 제거한 전체 적재를 실행하지 않습니다.

## 안전한 실행 순서

Perf DB 외에는 loader가 실행되지 않습니다. `disclosure_perf`, port `55432`가 아니면 즉시
중단합니다.

```powershell
alembic upgrade head

python scripts/load_retrieval_embeddings.py `
  --database-url $PerfDatabaseUrl `
  --dry-run
```

API 키는 파일이나 명령행 인자로 넘기지 않습니다. 승인된 서비스 앱의 서비스 API 키를
현재 PowerShell 세션에만 주입합니다.

```powershell
$SecureKey = Read-Host "CLOVA Studio Service API Key" -AsSecureString
$KeyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureKey)

try {
  $env:CLOVASTUDIO_API_KEY = (
    [Runtime.InteropServices.Marshal]::PtrToStringBSTR($KeyPointer)
  )
}
finally {
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($KeyPointer)
}

Remove-Variable SecureKey, KeyPointer
```

먼저 100건만 호출하여 인증·응답 차원·DB 저장을 확인합니다.

```powershell
python scripts/load_retrieval_embeddings.py `
  --database-url $PerfDatabaseUrl `
  --limit 100 `
  --workers 4 `
  --requests-per-minute 480 `
  --report data\quality\embedding-smoke-100.json
```

테스트 API 키의 Embedding v2 한도는 60 QPM/40,000 TPM이고, 서비스 API 키는
540 QPM/960,000 TPM입니다. 전체 178,822건은 서비스 API 키로 실행해야 현실적인 시간 안에
끝납니다. Loader는 모든 worker가 공유하는 전역 limiter를 사용합니다. 첫 요청은 테스트
키에서도 안전한 54 QPM으로 시작하고, 응답 헤더에서 서비스 한도 540 QPM을 확인한 뒤
설정한 상한 480 QPM까지 자동으로 높입니다. 반대로 테스트 키가 감지되면 54 QPM을
유지합니다.

429가 반환되면 `Retry-After` 또는 `x-ratelimit-reset-requests`만큼 전체 worker를 함께
대기시켜 재시도 폭주를 막습니다. 리포트에는 이번 실행의 성공·실패 수, HTTP 상태별 개수,
재시도 수, 관측된 QPM과 대표 오류가 기록됩니다.

같은 명령에서 `--limit`만 제거하면 저장된 100건을 건너뛰고 나머지를 이어서 처리합니다.

```powershell
python scripts/load_retrieval_embeddings.py `
  --database-url $PerfDatabaseUrl `
  --workers 4 `
  --requests-per-minute 480 `
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
