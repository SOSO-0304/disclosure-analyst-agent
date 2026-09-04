# Evaluation API

## Endpoint

`GET /answer`

Query parameters:

- `question_id`: evaluator-provided question identifier
- `question`: natural-language disclosure question

Example:

```bash
curl -G "http://localhost:8000/answer" \
  --data-urlencode "question_id=Q-001" \
  --data-urlencode "question=삼성전자의 2025년 연결기준 매출액은 얼마인가?"
```

Response:

```json
{
  "question_id": "Q-001",
  "question": "평가 질의 원문",
  "retrieved_context": "[E1] ...",
  "think_trace": "mode=metric_structured; rails=...; retrieval_status=...; evidence_count=...; generator=...",
  "answer": "최종 답변\n\n근거 공시\n- 사업보고서 (2025.12) | 공시일: ..."
}
```

`think_trace` contains an auditable execution summary (routing, retrieval status, evidence
count, generator, and fallback state). It does not expose hidden model chain-of-thought.

## Health

`GET /health`

A healthy deployment returns:

```json
{
  "status": "ok",
  "database": "ok",
  "hcx_configured": true
}
```

FastAPI OpenAPI documentation is available at `/docs`.


## Evidence labels

Internal evidence labels such as `[E1]` remain in `retrieved_context` for traceability and
grounding validation. They are removed from the public `answer` field. The public answer ends
with deduplicated disclosure names and receipt dates instead.
