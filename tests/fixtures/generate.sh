#!/bin/sh
# Regenerate the TEST-ONLY fixtures in the dev container (never on the host).
#   tests/fixtures/generate.sh            create missing keys, rewrite derived files
#   tests/fixtures/generate.sh --force    replace every key pair too
# Rebuild the sshd image afterwards (./mk integration does).
set -eu
repo=$(cd "$(dirname "$0")/../.." && pwd)
HOST_UID=$(id -u)
HOST_GID=$(id -g)
export HOST_UID HOST_GID
exec docker compose -f "$repo/compose.dev.yaml" -p "${P:-portkey-fixtures}" \
    run --rm --no-deps test python tests/fixtures/generate.py "$@"
