# Playwright for Python (Chromium) with the locked dev dependencies, in a venv. One image for
# both browser suites: the e2e specs (compose service e2e) and the frontend shell tests
# (compose service frontend, tests/frontend/run.sh). Owned by package 7.
# The image tag must match the playwright pin in pyproject.toml, so the browsers baked into the
# image (/ms-playwright) are the ones the locked playwright package expects. The base image has
# no python3-venv, hence the apt step (checked with a no-cache build on 2026-11-16).
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble
RUN apt-get update \
 && apt-get install --no-install-recommends -y python3-venv \
 && rm -rf /var/lib/apt/lists/*
COPY requirements-dev.lock /tmp/requirements-dev.lock
RUN python3 -m venv /venv \
 && /venv/bin/pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements-dev.lock
ENV PATH=/venv/bin:$PATH \
    PYTHONPATH=/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /src
