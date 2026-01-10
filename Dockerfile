FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    nmap \
    openssh-client \
    iputils-ping \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 devops
RUN mkdir -p /app/output /app/data /app/logs && chown -R devops:devops /app

# Copy project files
COPY --chown=devops:devops pyproject.toml ./
COPY --chown=devops:devops src ./src
COPY --chown=devops:devops configs ./configs

# Install Python dependencies
RUN pip install --no-cache-dir -e .

# Switch to non-root user
USER devops

# Expose API port
EXPOSE 8200

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8200/health || exit 1

# Default command runs the API
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8200"]
