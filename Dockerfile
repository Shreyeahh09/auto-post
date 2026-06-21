FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy source code first (pyproject.toml needs src/ present for the editable install)
COPY pyproject.toml .
COPY src/ src/
COPY dashboard/ dashboard/
RUN pip install --no-cache-dir -e .

# Create data directories for SQLite + the media upload queue
RUN mkdir -p /app/data/uploads

# Expose port (Railway/Render will set PORT env var)
EXPOSE 8000

# Health check — unauthenticated liveness endpoint, doesn't depend on DB/admin state
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8000}/health')" || exit 1

# Run the API server
CMD ["python", "-m", "openinstaflow.api"]
