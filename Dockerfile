FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PYTHONPATH=/app/src

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --upgrade pip \
    && python -m pip install .

COPY alembic.ini ./
COPY alembic ./alembic
COPY config ./config

EXPOSE 8000

CMD ["uvicorn", "disclosure_agent.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
