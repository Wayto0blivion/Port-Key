# Owned by package 7: Playwright for Python with the locked dev dependencies.
# The image tag must match the playwright pin in pyproject.toml.
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble
COPY requirements-dev.lock /tmp/requirements-dev.lock
RUN python3 -m venv /venv \
 && /venv/bin/pip install --no-cache-dir --require-hashes --no-deps -r /tmp/requirements-dev.lock
ENV PATH=/venv/bin:$PATH PYTHONPATH=/src PYTHONDONTWRITEBYTECODE=1
WORKDIR /src
