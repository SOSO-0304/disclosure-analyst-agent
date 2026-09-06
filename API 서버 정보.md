# API 서버 정보

## End-point URL

### Base URL

```text
http://49.50.142.34
```

### Answer API

```text
GET http://49.50.142.34/answer
```

요청은 `GET` 방식이며, `question_id`와 `question`을 Query Parameter로 전달합니다.

```text
GET /answer?question_id={question_id}&question={question}
```

요청 예시:

```bash
curl -G "http://49.50.142.34/answer" \
  --data-urlencode "question_id=Q-001" \
  --data-urlencode "question=삼성전자의 2025년 연결기준 매출액은 얼마인가?"
```

---

## Request JSON Schema

실제 API 요청은 JSON Body가 아닌 Query Parameter 방식이며, 요청 데이터 구조는 다음과 같습니다.

```json
{
  "type": "object",
  "properties": {
    "question_id": {
      "type": "string",
      "minLength": 1,
      "description": "평가 질의 식별자"
    },
    "question": {
      "type": "string",
      "minLength": 1,
      "description": "기업 공시에 대한 자연어 질의"
    }
  },
  "required": [
    "question_id",
    "question"
  ],
  "additionalProperties": false
}
```

요청 데이터 예시:

```json
{
  "question_id": "Q-001",
  "question": "삼성전자의 2025년 연결기준 매출액은 얼마인가?"
}
```

---

## Response

응답 형식은 `application/json`이며, 아래 5개 필드는 모두 문자열(`string`)입니다.

| Field | Type | 설명 |
|---|---|---|
| `question_id` | string | 요청에서 전달받은 질의 식별자 |
| `question` | string | 요청에서 전달받은 자연어 질의 |
| `retrieved_context` | string | 답변 생성에 사용된 검색 근거 문맥 |
| `think_trace` | string | 질의 처리 방식, 검색 상태, 근거 수, 생성 방식 등을 나타내는 고수준 실행 정보 |
| `answer` | string | 공시 근거 기반 최종 답변 및 근거 공시 정보 |

`think_trace`는 내부 Chain-of-Thought가 아니라 질의 처리 방식과 검색·생성 상태를 나타내는 고수준 실행 정보입니다.

---

## Response JSON Schema

```json
{
  "type": "object",
  "properties": {
    "question_id": {
      "type": "string",
      "description": "요청에서 전달받은 평가 질의 식별자"
    },
    "question": {
      "type": "string",
      "description": "요청에서 전달받은 자연어 질의"
    },
    "retrieved_context": {
      "type": "string",
      "description": "답변 생성에 사용된 공시 검색 근거 문맥"
    },
    "think_trace": {
      "type": "string",
      "description": "질의 처리 방식, 검색 상태, 근거 수, 생성 방식 등을 나타내는 고수준 실행 정보"
    },
    "answer": {
      "type": "string",
      "description": "공시 근거 기반 최종 답변 및 근거 공시 정보"
    }
  },
  "required": [
    "question_id",
    "question",
    "retrieved_context",
    "think_trace",
    "answer"
  ],
  "additionalProperties": false
}
```

응답 예시:

```json
{
  "question_id": "Q-001",
  "question": "삼성전자의 2025년 연결기준 매출액은 얼마인가?",
  "retrieved_context": "[E1] 삼성전자 | 사업보고서 (2025.12)\n답변 생성에 사용된 공시 근거 내용",
  "think_trace": "mode=metric_structured; rails=metric; retrieval_status=MATCHES_FOUND; evidence_count=1; generator=deterministic; status=ANSWERABLE",
  "answer": "최종 답변\n\n근거 공시\n- 사업보고서 (2025.12) | 공시일: 2026-03-10"
}
```
