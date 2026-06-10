FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    DASHBOARD_PORT=8765 \
    DASHBOARD_HOST=0.0.0.0 \
    STATUS_INTERVAL=30 \
    SURGE_INTERVAL=5 \
    CONFIG_PATH=/config/config.json

WORKDIR /app

# docker CLI is optional but useful when mounting /var/run/docker.sock for container health checks.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl docker.io \
    && rm -rf /var/lib/apt/lists/*

COPY public ./public
COPY scripts ./scripts
COPY config.example.json ./config.example.json
COPY docker/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh scripts/*.sh scripts/*.py

VOLUME ["/config", "/app/data", "/media"]
EXPOSE 8765

ENTRYPOINT ["/entrypoint.sh"]
