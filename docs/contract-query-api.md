# 공급계약 Query API v1

## 지원 범위

이 API는 검증된 단일판매·공급계약 공시의 표에서 다음 필드만 반환합니다.

- 계약상대방
- 계약금액(공시에 명시된 원화)
- 계약 시작일
- 계약 종료일

각 필드는 원문 표의 행·열, XPath, table/block ID와 DART 공시 URL로 역추적할 수
있습니다. 구조화 필드는 규칙 기반으로 추출하며 답변 생성을 위한 별도 LLM 호출은 하지
않습니다.

다음 기능은 v1에서 명시적으로 거절합니다.

- 여러 계약의 합계·평균·누적
- 정정·해지를 연결한 최신 유효 계약 또는 잔여 금액
- 환율 변환
- 매출·영업이익·가동률 등 재무 분석
- 계약 시작일·종료일을 검색 조건으로 사용

## 실행

`.env.perf`는 저장소 루트에 두며 Git에 포함하지 않습니다.

```text
PERF_DATABASE_URL=postgresql+psycopg://...@localhost:55432/disclosure_perf
CLOVASTUDIO_API_KEY=...
```

```powershell
python -m uvicorn disclosure_agent.api.main:app `
  --host 127.0.0.1 `
  --port 8000
```

`127.0.0.1` 바인딩은 로컬 개발 계약입니다. 인증·TLS·접근제어를 구성하기 전에는
`0.0.0.0`으로 공개하지 않습니다.

배포 환경에서는 `DISCLOSURE_API_TOKEN`을 설정하고 요청에
`Authorization: Bearer <token>`을 보냅니다. 로컬 환경에서 변수가 비어 있으면 기존
개발 호출은 유지되지만, `compose.deploy.yaml`은 빈 token으로 시작되지 않습니다.

## Health endpoints

`GET /health/live`는 프로세스만 확인하며 설정이나 외부 서비스에 접근하지 않습니다.

`GET /health/ready`는 다음을 확인합니다.

- perf DB URL과 CLOVA Studio 키가 구성됨
- active chunk/embedding run이 completed 상태임
- `bge-m3`, 1024차원, cosine 계약임
- input version이 승인된 `retrieval-embedding-v2`임
- 읽기 전용 DB transaction이 동작함

Provider 과금을 피하기 위해 readiness는 CLOVA Studio에 실제 요청을 보내지 않으며
`provider_checked=false`를 반환합니다.

## Query

`POST /v1/query`

```json
{
  "question": "삼성중공업의 2025년 공급계약 상대방과 계약금액을 알려줘",
  "company": "삼성중공업",
  "date_from": "2025-01-01",
  "date_to": "2025-12-31",
  "corrections": "all",
  "top_k": 5
}
```

공개 입력은 다음으로 제한됩니다.

| 필드 | 계약 |
|---|---|
| `question` | 공백 제외 1~10,000자 |
| `company` | 선택, 정확한 회사명·종목코드·고유번호 |
| `corp_code` | 선택, 숫자 8자리 |
| `date_from`, `date_to` | 선택, 공시 접수일 기준 ISO 날짜 |
| `corrections` | `all`, `only`, `exclude` |
| `top_k` | 1~10, 기본 5 |

알 수 없는 필드는 거절합니다. 특히 사용자가 `embedding_run_id`, 검색 모드, 후보 수,
document subtype을 요청별로 변경할 수 없습니다.

정상 응답에는 다음이 포함됩니다.

- `status`: `completed`, `partial`, `no_results`, `unsupported`,
  `clarification_required`
- `answer`: 구조화된 필드로 만든 근거 포함 한국어 답변
- `interpreted_query`, `applied_filters`
- `findings`, 중복 제거된 `citations`
- `limitations`
- active chunk/embedding run ID
- provider 호출 수와 query token 수
- 단계별 latency와 `database_writes=0`

`no_results`는 해당 검색 범위에서 찾지 못했다는 뜻이며 회사에 계약이 존재하지 않는다는
뜻이 아닙니다.

## 오류와 정보 노출 방지

| HTTP | 코드 예시 | 의미 |
|---:|---|---|
| 401 | `invalid_access_token` | 배포 API 접근 token 누락 또는 불일치 |
| 400 | `query_needs_clarification` | 회사명이나 명시 범위를 수정해야 함 |
| 422 | `invalid_request` | JSON schema 또는 입력 범위 오류 |
| 502 | `embedding_error` | provider 응답/전송 오류 |
| 503 | `service_not_ready` | 키, DB 또는 승인 run 상태 오류 |
| 504 | `embedding_timeout` | 질문 embedding timeout |

오류 응답에는 DB URL, 비밀번호, API 키, provider 본문 또는 내부 예외를 포함하지 않습니다.
모든 응답에는 서버가 생성한 `X-Request-ID`가 들어갑니다.
