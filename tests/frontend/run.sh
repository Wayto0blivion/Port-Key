#!/bin/sh
# Frontend shell tests and screenshots (package 8a). Everything runs in a container built from
# tests/frontend/Dockerfile; the container has no network and publishes no ports.
#
#   tests/frontend/run.sh test [pytest args]   run tests/frontend
#   tests/frontend/run.sh shots OUT_DIR [names] write screenshots to OUT_DIR (absolute path)
set -eu
repo=$(cd "$(dirname "$0")/../.." && pwd)
image=portkey-ui8a:local
if ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -q -t "$image" -f "$repo/tests/frontend/Dockerfile" "$repo" >/dev/null
fi
cmd=${1:-test}
[ $# -gt 0 ] && shift
extra=""
if [ "$cmd" = "shots" ]; then
    out=$1
    shift
    mkdir -p "$out"
    extra="-v $out:/out"
    set -- python -m tests.frontend.screenshots /out "$@"
else
    set -- pytest tests/frontend -p no:cacheprovider "$@"
fi
# shellcheck disable=SC2086
exec docker run --rm --network none --user "$(id -u):$(id -g)" -e HOME=/tmp --tmpfs /tmp:exec \
    --shm-size 1g -v "$repo:/src" $extra -w /src "$image" "$@"
