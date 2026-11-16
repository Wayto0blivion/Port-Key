#!/bin/sh
# Regenerate the hash-locked requirement files from pyproject.toml.
#
# Runs inside a python:3.12-slim container; nothing is installed on the host.
# Usage (from the repo root): scripts/lock.sh            (or: make lock)
#   --upgrade   pick the newest allowed versions instead of keeping current pins
set -eu

if [ "${PORTKEY_IN_CONTAINER:-}" != "1" ]; then
    repo=$(cd "$(dirname "$0")/.." && pwd)
    exec docker run --rm \
        -e PORTKEY_IN_CONTAINER=1 \
        -e HOME=/tmp \
        --user "$(id -u):$(id -g)" \
        -v "$repo:/w" -w /w \
        python:3.12-slim sh scripts/lock.sh "$@"
fi

upgrade=""
if [ "${1:-}" = "--upgrade" ]; then
    upgrade="--upgrade"
fi

python -m venv /tmp/uv
/tmp/uv/bin/pip install --quiet --disable-pip-version-check "uv==0.5.11"
uv=/tmp/uv/bin/uv
common="--generate-hashes --python-version 3.12 --python-platform x86_64-manylinux_2_28 --no-header"

# shellcheck disable=SC2086
$uv pip compile pyproject.toml $common $upgrade -o requirements.lock
# shellcheck disable=SC2086
$uv pip compile pyproject.toml --extra dev $common $upgrade -o requirements-dev.lock
echo "Wrote requirements.lock and requirements-dev.lock"
