# Multi-stage build optimized for CI/CD
FROM python:3.11-slim as builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install poetry
RUN pip install poetry==1.7.1

# Set working directory
WORKDIR /app

# Copy dependency files
COPY pyproject.toml poetry.lock* ./

# Configure poetry
RUN poetry config virtualenvs.create false \
    && poetry config virtualenvs.in-project false

# Install dependencies (handle missing lock file gracefully)
RUN if [ -f poetry.lock ]; then \
        poetry install --only=main --no-root; \
    else \
        poetry install --only=main --no-root --no-cache; \
    fi

# Production stage
FROM python:3.11-slim

# Add labels for better image metadata
LABEL org.opencontainers.image.title="Maconso Energy Pipeline" \
      org.opencontainers.image.description="Daily energy data pipeline with cron scheduling" \
      org.opencontainers.image.vendor="thidev" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/bthidev/maconso-api"

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    cron \
    curl \
    tini \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Create non-root user for security
RUN groupadd -r maconso && useradd -r -g maconso maconso

# Copy Python dependencies from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Set working directory
WORKDIR /app

# Copy source code
COPY src/ ./src/
COPY pyproject.toml ./

# Create log directory with proper permissions
RUN mkdir -p /var/log/maconso \
    && chown -R maconso:maconso /var/log/maconso

# Create cron job file
RUN echo "# Energy data pipeline cron job" > /etc/cron.d/maconso-pipeline \
    && echo "0 2 * * * maconso cd /app && python -m src.pipeline >> /var/log/maconso/pipeline.log 2>&1" >> /etc/cron.d/maconso-pipeline \
    && echo "" >> /etc/cron.d/maconso-pipeline

# Set proper permissions for cron file
RUN chmod 0644 /etc/cron.d/maconso-pipeline \
    && crontab /etc/cron.d/maconso-pipeline

# Create entrypoint script with proper error handling
RUN echo '#!/bin/bash' > /entrypoint.sh \
    && echo 'set -euo pipefail' >> /entrypoint.sh \
    && echo '' >> /entrypoint.sh \
    && echo '# Function to handle shutdown gracefully' >> /entrypoint.sh \
    && echo 'cleanup() {' >> /entrypoint.sh \
    && echo '    echo "Shutting down gracefully..."' >> /entrypoint.sh \
    && echo '    pkill -TERM cron || true' >> /entrypoint.sh \
    && echo '    exit 0' >> /entrypoint.sh \
    && echo '}' >> /entrypoint.sh \
    && echo '' >> /entrypoint.sh \
    && echo '# Set up signal handlers' >> /entrypoint.sh \
    && echo 'trap cleanup SIGTERM SIGINT' >> /entrypoint.sh \
    && echo '' >> /entrypoint.sh \
    && echo '# Start cron daemon' >> /entrypoint.sh \
    && echo 'echo "Starting cron daemon..."' >> /entrypoint.sh \
    && echo 'cron' >> /entrypoint.sh \
    && echo '' >> /entrypoint.sh \
    && echo '# Run pipeline once on startup if requested' >> /entrypoint.sh \
    && echo 'if [[ "${RUN_ON_STARTUP:-false}" == "true" ]]; then' >> /entrypoint.sh \
    && echo '    echo "Running pipeline on startup..."' >> /entrypoint.sh \
    && echo '    cd /app && python -m src.pipeline || echo "Startup run failed, continuing..."' >> /entrypoint.sh \
    && echo 'fi' >> /entrypoint.sh \
    && echo '' >> /entrypoint.sh \
    && echo '# Show configuration' >> /entrypoint.sh \
    && echo 'echo "===== Maconso Energy Pipeline ====="' >> /entrypoint.sh \
    && echo 'echo "Scheduled to run daily at 2 AM UTC"' >> /entrypoint.sh \
    && echo 'echo "Logs: /var/log/maconso/pipeline.log"' >> /entrypoint.sh \
    && echo 'echo "Timezone: $(cat /etc/timezone 2>/dev/null || echo '\''UTC'\'')"' >> /entrypoint.sh \
    && echo 'echo "Container is running... Press Ctrl+C to stop"' >> /entrypoint.sh \
    && echo 'echo "===================================="' >> /entrypoint.sh \
    && echo '' >> /entrypoint.sh \
    && echo '# Create initial log file if it doesn'\''t exist' >> /entrypoint.sh \
    && echo 'touch /var/log/maconso/pipeline.log' >> /entrypoint.sh \
    && echo 'chown maconso:maconso /var/log/maconso/pipeline.log' >> /entrypoint.sh \
    && echo '' >> /entrypoint.sh \
    && echo '# Follow logs in background' >> /entrypoint.sh \
    && echo 'tail -f /var/log/maconso/pipeline.log &' >> /entrypoint.sh \
    && echo '' >> /entrypoint.sh \
    && echo '# Keep container alive and wait for signals' >> /entrypoint.sh \
    && echo 'while true; do' >> /entrypoint.sh \
    && echo '    sleep 30' >> /entrypoint.sh \
    && echo 'done' >> /entrypoint.sh

RUN chmod +x /entrypoint.sh

# Switch to non-root user
USER maconso

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import sys, os; sys.exit(0 if os.path.exists('/var/log/maconso/pipeline.log') else 1)"

# Expose log directory as volume
VOLUME ["/var/log/maconso"]

# Use tini as init system for proper signal handling
ENTRYPOINT ["tini", "--", "/entrypoint.sh"]