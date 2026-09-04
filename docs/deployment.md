# Naver Cloud deployment

This project is deployed as two Docker Compose services on one evaluation VM:

```text
Internet
   |
Public IP
   |
Ncloud ACG
   |
disclosure-agent : 8000
   |
PostgreSQL + pgvector : 5432 (container network)
```

For submission, expose the API through a public endpoint. A domain + HTTPS reverse proxy may be
added later; first verify the service by public IP.

## 1. Prepare the local database dump

The regression dataset is not committed to Git. Export the already validated local PostgreSQL DB:

```bash
docker exec disclosure-postgres \
  pg_dump -U disclosure -d disclosure -Fc -f /tmp/disclosure.dump

docker cp disclosure-postgres:/tmp/disclosure.dump ./data/disclosure.dump
```

Keep the dump outside Git. Transfer it to the server with `scp` or another private storage method.

## 2. Create Ncloud infrastructure

Recommended minimal layout:

1. VPC
2. Public Subnet
3. Server with Public IP
4. ACG inbound rules
   - TCP 22: restrict to your IP if possible
   - TCP 80: public API when using `APP_PORT=80`
   - TCP 443: enable when HTTPS is configured

Do not expose PostgreSQL 5432 in ACG.

## 3. Install Docker on the server

Example for Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
```

Log out and back in after adding the Docker group.

## 4. Clone and configure

```bash
git clone <PRIVATE_REPOSITORY_URL>
cd disclosure-analyst-agent
git checkout model/data-pipeline

cp .env.example .env
```

Edit `.env`:

```dotenv
CLOVA_STUDIO_API_KEY=<service-api-key>

POSTGRES_DB=disclosure
POSTGRES_USER=disclosure
POSTGRES_PASSWORD=<strong-password>
POSTGRES_PORT=5432

DATABASE_URL=postgresql+psycopg://disclosure:<strong-password>@db:5432/disclosure
APP_PORT=80
```

Use a URL-safe DB password or percent-encode special characters in `DATABASE_URL`.

## 5. Start PostgreSQL and restore the validated DB

```bash
docker compose up -d db

docker cp ./data/disclosure.dump disclosure-postgres:/tmp/disclosure.dump

docker exec disclosure-postgres \
  pg_restore -U disclosure -d disclosure --clean --if-exists /tmp/disclosure.dump
```

If restoring into a brand-new empty DB causes ownership warnings, add `--no-owner`.

## 6. Start the API

```bash
docker compose up -d --build disclosure-agent
docker compose ps
docker compose logs --tail=200 disclosure-agent
```

Local VM smoke test:

```bash
curl http://127.0.0.1/health

curl -G "http://127.0.0.1/answer" \
  --data-urlencode "question_id=SMOKE-001" \
  --data-urlencode "question=카카오의 2025년 연결기준 매출액은 얼마인가?"
```

External smoke test from your laptop:

```bash
curl http://<PUBLIC_IP>/health
```

Then test representative structured and hybrid queries.

## 7. HTTPS

The evaluation material shows an HTTPS endpoint example. Once the HTTP smoke test succeeds,
place a reverse proxy on ports 80/443 and connect a domain/certificate. Ncloud Global DNS and
Certificate Manager can be used if you have a domain available.

Do not postpone the working API smoke test while configuring HTTPS.

## 8. Final freeze

Before submission:

1. run `pytest -q tests/unit`;
2. run the 30-case extended regression against the deployed-equivalent database;
3. verify `/health` and several `/answer` calls from an external network;
4. record the final endpoint and API schema;
5. stop changing code/server state after the official submission cutoff.
