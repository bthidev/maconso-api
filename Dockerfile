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

# Install dependencies
RUN poetry install --only=main --no-root

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
COPY <<EOF /etc/cron.d/maconso-pipeline
# Energy data pipeline cron job
0 2 * * * maconso cd /app && python -m src.pipeline >> /var/log/maconso/pipeline.log 2>&1

EOF

# Set proper permissions for cron file
RUN chmod 0644 /etc/cron.d/maconso-pipeline \
    && crontab /etc/cron.d/maconso-pipeline

# Create entrypoint script with proper error handling
COPY <<'EOF' /entrypoint.sh
#!/bin/bash
set -euo pipefail

# Function to handle shutdown gracefully
cleanup() {
    echo "Shutting down gracefully..."
    pkill -TERM cron || true
    exit 0
}

# Set up signal handlers
trap cleanup SIGTERM SIGINT

# Start cron daemon
echo "Starting cron daemon..."
cron

# Run pipeline once on startup if requested
if [[ "${RUN_ON_STARTUP:-false}" == "true" ]]; then
    echo "Running pipeline on startup..."
    cd /app && python -m src.pipeline || echo "Startup run failed, continuing..."
fi

# Show configuration
echo "===== Maconso Energy Pipeline ====="
echo "Scheduled to run daily at 2 AM UTC"
echo "Logs: /var/log/maconso/pipeline.log"
echo "Timezone: $(cat /etc/timezone 2>/dev/null || echo 'UTC')"
echo "Container is running... Press Ctrl+C to stop"
echo "===================================="

# Create initial log file if it doesn't exist
touch /var/log/maconso/pipeline.log
chown maconso:maconso /var/log/maconso/pipeline.log

# Follow logs in background
tail -f /var/log/maconso/pipeline.log &

# Keep container alive and wait for signals
while true; do
    sleep 30
done
EOF

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