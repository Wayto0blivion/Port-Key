# Package 8a: private image for the frontend shell tests (tests/frontend). Same Playwright base
# as tests/e2e/Dockerfile, with the locked dev dependencies installed into the system Python
# (the base image has no python3-venv). Built and run by tests/frontend/run.sh, no ports.
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble
COPY requirements-dev.lock /tmp/requirements-dev.lock
RUN pip install --no-cache-dir --break-system-packages --require-hashes --no-deps \
        -r /tmp/requirements-dev.lock
ENV PYTHONPATH=/src PYTHONDONTWRITEBYTECODE=1
WORKDIR /src
