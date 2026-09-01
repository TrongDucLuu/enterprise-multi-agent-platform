# Multi-stage production build for IT Helpdesk Agent
# Stage 1: Builder
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.8.13

WORKDIR /code

COPY ./pyproject.toml ./README.md ./uv.lock* ./
COPY ./config ./config
COPY ./agent_core ./agent_core
COPY ./data ./data
COPY ./scripts ./scripts
COPY ./main.py ./test_local.py ./

RUN uv sync --frozen

# Stage 2: Runtime image (hardened, non-root)
FROM python:3.12-slim AS runner

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create dedicated non-root user and group
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /bin/sh -m appuser

WORKDIR /code

# Copy application and virtual environment with non-root ownership
COPY --from=builder --chown=appuser:appgroup /code /code

ENV PATH="/code/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/healthz || exit 1

CMD ["uvicorn", "agent_core.fast_api_app:app", "--host", "0.0.0.0", "--port", "8080"]
