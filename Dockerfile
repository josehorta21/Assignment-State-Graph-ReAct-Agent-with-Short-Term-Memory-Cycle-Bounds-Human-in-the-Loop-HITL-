FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AGENT_DB_PATH=/app/data/checkpoints.db \
    AGENT_PLANNER=deterministic

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY assets/ ./assets/

RUN pip install --no-cache-dir . && \
    mkdir -p /app/data && \
    useradd --system --uid 10001 --home-dir /app appuser && \
    chown -R appuser:appuser /app/data

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=4s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8000"]
