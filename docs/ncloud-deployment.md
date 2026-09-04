# Ncloud 단일 Linux 서버 배포

## 확정된 배포 경계

이 구성은 검증된 `retrieval-embedding-v2` 전체 178,822개와 공급계약 Query API v1을
Ubuntu 서버 한 대에서 실행하기 위한 첫 배포 계약입니다.

- 권장 서버: 4 vCPU, RAM 16 GB, CB1 100 GB
- 최소 데모 서버: 2 vCPU, RAM 8 GB, CB1 100 GB
- GPU는 사용하지 않음
- API process/replica는 정확히 1개
- PostgreSQL은 Docker private network에만 연결하며 host port를 열지 않음
- API도 최초 검증 동안 `127.0.0.1:8000`에만 연결
- 공개 전환은 별도 TLS reverse proxy 또는 Ncloud Load Balancer를 붙인 뒤 수행

API process를 둘 이상 실행하면 각 process의 query embedding limiter가 독립적으로
작동하여 CLOVA Studio 540 QPM을 초과할 수 있습니다. 분산 limiter를 구현하기 전까지
`--workers 1`과 replica 1을 유지합니다.

## 1. 서버 준비

Ubuntu의 기본 30 GB system disk에는 DB를 두지 않습니다. 100 GB CB1 block storage를
추가하고 Docker data root를 해당 disk에 배치한 뒤 진행합니다. ACG는 최초에는 다음만
허용합니다.

| Port | Source | 용도 |
|---:|---|---|
| 22 | 관리자 고정 IP | SSH |
| 80/443 | 아직 열지 않음 | TLS gateway 구성 후 개방 |
| 5432/55432 | 열지 않음 | PostgreSQL 외부 접근 금지 |
| 8000 | 열지 않음 | localhost 검증 전용 |

서버 반납 보호를 먼저 설정합니다.

## 2. secret 파일 생성

저장소를 받은 다음 예제만 복사합니다.

```bash
cp .env.deploy.example .env.deploy
chmod 600 .env.deploy
```

URL encoding 문제를 피하기 위해 DB password와 API token은 hexadecimal로 생성합니다.

```bash
openssl rand -hex 32
openssl rand -hex 32
```

첫 번째 값은 `DEPLOY_POSTGRES_PASSWORD`, 두 번째 값은 `DISCLOSURE_API_TOKEN`에 넣고,
승인된 CLOVA Studio 서비스 키를 `CLOVASTUDIO_API_KEY`에 넣습니다. 실제 값은 채팅,
Git, Docker image에 넣지 않습니다.

설정을 secret 값 없이 구조적으로 검사합니다.

```bash
docker compose \
  -p disclosure-deploy \
  -f compose.deploy.yaml \
  --env-file .env.deploy \
  config --quiet
```

## 3. 로컬 검증 DB export

Windows의 기존 `disclosure-perf-postgres`에서 읽기 전용 custom-format dump를 만듭니다.
기존 volume과 table은 변경하지 않습니다.

```powershell
docker exec disclosure-perf-postgres `
  pg_dump `
  -U disclosure_perf `
  -d disclosure_perf `
  --format=custom `
  --compress=6 `
  --no-owner `
  --no-privileges `
  --file=/tmp/disclosure_perf.dump

docker cp `
  disclosure-perf-postgres:/tmp/disclosure_perf.dump `
  .\disclosure_perf.dump
```

덤프 파일은 Git에 추가하지 않습니다. 서버의 100 GB data disk로 `scp` 전송합니다.

## 4. 새 PostgreSQL에 restore

먼저 DB만 시작합니다.

```bash
docker compose \
  -p disclosure-deploy \
  -f compose.deploy.yaml \
  --env-file .env.deploy \
  up -d postgres
```

새로 생성된 빈 `disclosure_deploy_pgdata` volume에만 restore합니다. 기존 데이터가 있는
volume에는 실행하지 않습니다.

```bash
docker cp disclosure_perf.dump disclosure-deploy-postgres:/tmp/disclosure_perf.dump

docker exec disclosure-deploy-postgres \
  pg_restore \
  -U disclosure_perf \
  -d disclosure_perf \
  --exit-on-error \
  --no-owner \
  --no-privileges \
  /tmp/disclosure_perf.dump
```

핵심 계약을 확인합니다.

```bash
docker exec disclosure-deploy-postgres \
  psql -U disclosure_perf -d disclosure_perf -Atc \
  "SELECT status, counts->>'embedded_chunks' FROM embedding_runs WHERE is_active;"
```

예상값은 `completed|178822`입니다.

## 5. API 시작과 localhost 검증

```bash
docker compose \
  -p disclosure-deploy \
  -f compose.deploy.yaml \
  --env-file .env.deploy \
  up -d --build api

docker compose \
  -p disclosure-deploy \
  -f compose.deploy.yaml \
  --env-file .env.deploy \
  ps

curl --fail http://127.0.0.1:8000/health/live
curl --fail http://127.0.0.1:8000/health/ready
```

Query endpoint에는 client-facing bearer token이 필요합니다.

```bash
set -a
. ./.env.deploy
set +a

curl --fail \
  -H "Authorization: Bearer ${DISCLOSURE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  --data '{"question":"삼성중공업의 단일판매 공급계약 상대방과 계약금액, 계약기간","company":"삼성중공업","top_k":5}' \
  http://127.0.0.1:8000/v1/query

unset DEPLOY_POSTGRES_PASSWORD CLOVASTUDIO_API_KEY DISCLOSURE_API_TOKEN
```

## 6. 공개 전 남은 gate

localhost smoke가 끝나도 port 8000을 바로 인터넷에 개방하지 않습니다. 다음 단계에서
TLS termination, domain, request rate limit, access log의 secret redaction, backup/restore
rehearsal을 확인한 뒤 80/443만 공개합니다.

## 안전한 중지

다음 명령은 container와 network만 중지하고 DB volume은 보존합니다.

```bash
docker compose \
  -p disclosure-deploy \
  -f compose.deploy.yaml \
  --env-file .env.deploy \
  down
```

`down -v`를 실행하거나 `disclosure_deploy_pgdata`를 삭제하지 않습니다.
