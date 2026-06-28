FROM python:3.11-slim

# System deps needed by some transitive packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install poetry
RUN pip install --no-cache-dir poetry==1.8.3

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml poetry.lock ./

# Install deps into system Python (no virtualenv needed in a container)
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi

# Copy application code
COPY . .

# Make all packages importable from /app
ENV PYTHONPATH=/app

EXPOSE 8080

CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8080"]
