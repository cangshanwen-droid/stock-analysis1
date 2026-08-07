FROM python:3.12-slim

# Install curl for HEALTHCHECK (single RUN to keep layer slim)
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Layer 1: install dependencies (cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    rm -rf /root/.cache/pip

# Layer 2: copy application code
COPY . .

EXPOSE 8000

# Create and switch to non-root user
RUN useradd --create-home --uid 1001 --shell /bin/bash appuser && \
    chown -R appuser:appuser /app
USER 1001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -sf "http://127.0.0.1:${PORT:-8000}/health" || exit 1

CMD ["bash", "start.sh"]
