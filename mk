#!/bin/sh
# Run a Makefile target without make on the host: `./mk check P=portkey-wp3`.
# Uses host make if present; otherwise runs make in a small container (tools/make) that talks
# to the host's docker daemon. The repo is mounted at the same path, so compose bind mounts
# resolve to the same host directories.
set -eu
repo=$(cd "$(dirname "$0")" && pwd)
if command -v make >/dev/null 2>&1; then
    exec make -C "$repo" "$@"
fi
sock=/var/run/docker.sock
image=portkey-make:local
if ! docker image inspect "$image" >/dev/null 2>&1; then
    docker build -q -t "$image" "$repo/tools/make" >/dev/null
fi
tty=""
if [ -t 0 ] && [ -t 1 ]; then tty="-it"; fi
# shellcheck disable=SC2086
exec docker run --rm $tty \
    --user "$(id -u):$(id -g)" \
    --group-add "$(stat -c %g "$sock")" \
    -e HOME=/tmp \
    -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
    -v "$sock:$sock" \
    -v "$repo:$repo" -w "$repo" \
    "$image" make "$@"
