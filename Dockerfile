# Multi-stage build for Python energy pipeline with Python scheduler
FROM python:3.11-slim AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install poetry

# Set working directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml poetry.lock* ./

# Configure poetry and install dependencies
RUN poetry config virtualenvs.create false \
    && poetry config virtualenvs.in-project false

# Install dependencies
RUN if [ -f poetry.lock ]; then \
        poetry install --only=main --no-root; \
    else \
        poetry install --only=main --no-root --no-cache; \
    fi

# Production stage
FROM python:3.11-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    && rm -rf /var/lib/apt/lists/*

# Create user for security
RUN groupadd -r maconso && useradd -r -g maconso maconso

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Set working directory
WORKDIR /app

# Copy source code
COPY src/ ./src/
COPY pyproject.toml ./

# Create log directory
RUN mkdir -p /var/log/maconso \
    && chown -R maconso:maconso /var/log/maconso

# Switch to non-root user
USER maconso

# Environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Expose port (if needed for health checks)
EXPOSE 8000

# Use Python scheduler instead of cron
CMD ["python", "src/scheduler.py"]