# Sales Bot Engine v2 — Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cache layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Create directories
RUN mkdir -p /app/data/memory /app/data/logs /app/sessions

# Default: run v2 multi-persona mode
ENV BOT_MODE=v2
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["python3", "-m", "src.main"]
