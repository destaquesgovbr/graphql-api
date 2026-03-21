FROM python:3.12-slim AS base

WORKDIR /app

COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY src/ ./src/

ENV PYTHONPATH=/app/src
EXPOSE 8000

CMD ["uvicorn", "graphql_api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
