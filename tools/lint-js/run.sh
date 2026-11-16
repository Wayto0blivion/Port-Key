#!/bin/sh
# Runs inside the lint-js container with the repo mounted read-only at /src.
set -eu
cd /src
echo "eslint"
eslint --config /src/eslint.config.js --cache-location /tmp/eslintcache portkey/static/js
echo "tsc --checkJs"
tsc --project /src/jsconfig.json
echo "lint-js ok"
