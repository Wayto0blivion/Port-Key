# GNU make plus the docker CLI and compose plugin, for dev boxes without make on the host.
# Used by ./mk; the host's docker socket is mounted in, so containers start as siblings.
FROM docker:27.5.1-cli
RUN apk add --no-cache make
