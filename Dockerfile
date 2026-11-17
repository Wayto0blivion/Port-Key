# syntax=docker/dockerfile:1
#
# Port-Key images.
#   runtime  the app as it runs on the Windows PC (default target). Released as
#            ghcr.io/wayto0blivion/port-key by .github/workflows/release.yml, which compose.yaml
#            pulls; compose.build.yaml and compose.dev.yaml build it locally as portkey:local.
#   dev      runtime dependencies plus test, lint and type-check tools (compose.dev.yaml);
#            the source is bind-mounted at /src, so code changes need no rebuild.

ARG PYTHON_IMAGE=python:3.12-slim

FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN groupadd --gid 1000 portkey \
 && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin portkey

FROM base AS deps
COPY requirements.lock /tmp/requirements.lock
RUN python -m venv /venv \
 && /venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements.lock

FROM base AS dev-deps
COPY requirements-dev.lock /tmp/requirements-dev.lock
RUN python -m venv /venv \
 && /venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements-dev.lock

# ---------------------------------------------------------------------------
FROM base AS dev
# Shells the remote-script unit tests run under (package 6): dash, bash and busybox sh. dash and
# bash are already in the slim image (dash is /bin/sh); naming them keeps them if that changes.
# tests/unit/skeleton/test_dev_image.py checks all three.
RUN apt-get update \
 && apt-get install --no-install-recommends -y bash busybox dash \
 && rm -rf /var/lib/apt/lists/*
COPY --from=dev-deps /venv /venv
ENV PATH=/venv/bin:$PATH \
    PYTHONPATH=/src \
    MYPY_CACHE_DIR=/tmp/mypy-cache \
    RUFF_CACHE_DIR=/tmp/ruff-cache \
    HOME=/tmp
WORKDIR /src
USER 1000:1000
CMD ["pytest"]

# ---------------------------------------------------------------------------
FROM base AS runtime
RUN mkdir -p /data /ssh /keys-generated \
 && chown 1000:1000 /data /keys-generated \
 && chmod 0700 /data
COPY --from=deps /venv /venv
COPY portkey /app/portkey
RUN /venv/bin/python -m compileall -q /app/portkey
ENV PATH=/venv/bin:$PATH \
    PYTHONPATH=/app \
    PORTKEY_DATA_DIR=/data \
    PORTKEY_SSH_DIR=/ssh \
    PORTKEY_KEYS_DIR=/keys-generated \
    PORTKEY_LISTEN_PORT=8000
WORKDIR /app
USER 1000:1000
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-m", "portkey.healthcheck"]
# Standard OCI labels. The release workflow passes VERSION (pyproject.toml's version) and
# REVISION (the tagged commit); a local build keeps the defaults, so it never claims to be a
# release. Declared last so a new commit does not invalidate the layers above.
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.source="https://github.com/Wayto0blivion/Port-Key" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.title="Port-Key" \
      org.opencontainers.image.description="Browser-based SSH connection manager for one person" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"
CMD ["python", "-m", "portkey"]
