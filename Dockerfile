FROM python:3.12-slim

# Unbuffered so container logs appear as they happen, not when a buffer fills.
ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# One install layer: the package is installed editable, so it needs its source.
COPY pyproject.toml README.md ./
COPY infrareplay ./infrareplay
COPY examples ./examples
COPY migrations ./migrations
COPY docker ./docker
COPY alembic.ini ./

RUN pip install -e ".[test]" \
 && chmod +x docker/entrypoint.sh \
 && mkdir -p /data/blobs

# Everything lands in /data so one volume keeps recordings between runs.
# Compose overrides these with Postgres; on their own they need nothing running.
ENV INFRAREPLAY_DATABASE_URL="sqlite+aiosqlite:////data/infrareplay.db" \
    INFRAREPLAY_BLOB_ROOT="/data/blobs" \
    DEMO_SHOP_DATABASE_URL="sqlite+aiosqlite:////data/demo_shop.db" \
    INFRAREPLAY_API_URL="http://127.0.0.1:8000" \
    INFRAREPLAY_DASHBOARD_PORT="8080" \
    INFRAREPLAY_ROLE="all"

VOLUME ["/data"]

# 8000 API · 8080 dashboard · 3000 demo shop · 8081 capture proxy
EXPOSE 8000 8080 3000 8081

# NiceGUI takes ~20s to come up; the grace period covers that.
HEALTHCHECK --interval=10s --timeout=5s --start-period=45s --retries=5 \
  CMD ["python", "/app/docker/healthcheck.py"]

ENTRYPOINT ["/app/docker/entrypoint.sh"]
