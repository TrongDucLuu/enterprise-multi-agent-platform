FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.8.13

WORKDIR /code

COPY ./pyproject.toml ./README.md ./uv.lock* ./
COPY ./config ./config
COPY ./it_helpdesk_agent ./it_helpdesk_agent
COPY ./data ./data
COPY ./scripts ./scripts
COPY ./main.py ./test_local.py ./

RUN uv sync --frozen

EXPOSE 8080

CMD ["uv", "run", "uvicorn", "it_helpdesk_agent.fast_api_app:app", "--host", "0.0.0.0", "--port", "8080"]
