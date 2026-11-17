#!/bin/sh
# Smoke-test a built runtime image before anything is pushed (.github/workflows/release.yml):
# start it as compose.yaml does (read-only root, every capability dropped, no new privileges,
# the same limits), no port published, and wait for its own healthcheck. Also checks the
# version label and the version the app reports.
#
#   scripts/release-smoke.sh <image> <version>
#
# Needs only the docker CLI. On the dev box run it in a container that has the docker socket.
set -eu
image=${1:?usage: scripts/release-smoke.sh <image> <version>}
version=${2:?usage: scripts/release-smoke.sh <image> <version>}
timeout=${PORTKEY_SMOKE_TIMEOUT:-120}

fail() {
    echo "::error::$1"
    exit 1
}

label=$(docker image inspect -f '{{index .Config.Labels "org.opencontainers.image.version"}}' "$image")
[ "$label" = "$version" ] || fail "Image label version is '$label', expected '$version'."

cid=$(docker run -d --read-only --tmpfs /tmp --cap-drop ALL \
    --security-opt no-new-privileges:true --memory 1g --pids-limit 256 "$image")
trap 'docker rm -f -v "$cid" >/dev/null 2>&1 || true' EXIT

waited=0
while :; do
    state=$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$cid")
    case $state in
        "running healthy") break ;;
        "running unhealthy" | exited* | dead*)
            docker logs "$cid" || true
            fail "The container is '$state'." ;;
    esac
    if [ "$waited" -ge "$timeout" ]; then
        docker logs "$cid" || true
        fail "Not healthy after ${timeout}s (state: '$state')."
    fi
    sleep 2
    waited=$((waited + 2))
done
echo "Healthy after about ${waited}s."

reported=$(docker exec "$cid" python -c 'import portkey; print(portkey.__version__)')
if [ "$version" != dev ]; then
    [ "$reported" = "$version" ] || fail "The app reports version '$reported', expected '$version'."
fi
echo "Smoke test passed: $image, version $version, app reports $reported."
