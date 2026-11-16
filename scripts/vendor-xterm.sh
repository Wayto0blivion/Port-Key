#!/bin/sh
# Fetch xterm.js and its addons from the npm registry into portkey/static/vendor/xterm/,
# verifying each tarball against the sha256 sums recorded below.
#
# Runs inside an alpine container; nothing is installed on the host.
# Usage (from the repo root): scripts/vendor-xterm.sh      (or: make vendor-xterm)
#
# To move to a new version: change the version, run with PORTKEY_PRINT_SUMS=1, check the printed
# sums against the npm registry's published integrity, and record them here.
set -eu

XTERM_VERSION=5.5.0
FIT_VERSION=0.10.0
SEARCH_VERSION=0.15.0

XTERM_SHA256=bd954fa721872170188cc5d7e83e88db3c83c9a18a4e8d24c2783d26491f59d2
FIT_SHA256=917ac44972453d5eed52edc1e50260c76398ce48cf2290c2e60671102bba0b33
SEARCH_SHA256=6fe6b2c0aa5cc51dc8888352ee276a790a64cfe25246a5af72a02afc373e172f

if [ "${PORTKEY_IN_CONTAINER:-}" != "1" ]; then
    repo=$(cd "$(dirname "$0")/.." && pwd)
    exec docker run --rm \
        -e PORTKEY_IN_CONTAINER=1 \
        -e PORTKEY_PRINT_SUMS="${PORTKEY_PRINT_SUMS:-}" \
        --user "$(id -u):$(id -g)" \
        -v "$repo:/w" -w /w \
        alpine:3.20 sh scripts/vendor-xterm.sh
fi

registry=https://registry.npmjs.org
dest=portkey/static/vendor/xterm
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

fetch() { # name version expected-sha256 -> extracts into $work/<short>
    name=$1 version=$2 expected=$3
    short=${name#@xterm/}
    tgz="$work/$short.tgz"
    wget -q -O "$tgz" "$registry/$name/-/$short-$version.tgz"
    actual=$(sha256sum "$tgz" | cut -d' ' -f1)
    if [ -n "${PORTKEY_PRINT_SUMS:-}" ]; then
        echo "$name@$version $actual"
    elif [ "$actual" != "$expected" ]; then
        echo "sha256 mismatch for $name@$version: got $actual, expected $expected" >&2
        exit 1
    fi
    mkdir -p "$work/$short"
    tar -xzf "$tgz" -C "$work/$short"
}

fetch @xterm/xterm "$XTERM_VERSION" "$XTERM_SHA256"
fetch @xterm/addon-fit "$FIT_VERSION" "$FIT_SHA256"
fetch @xterm/addon-search "$SEARCH_VERSION" "$SEARCH_SHA256"

if [ -n "${PORTKEY_PRINT_SUMS:-}" ]; then
    exit 0
fi

rm -rf "$dest"
mkdir -p "$dest"
cp "$work/xterm/package/lib/xterm.js" "$dest/xterm.js"
cp "$work/xterm/package/css/xterm.css" "$dest/xterm.css"
cp "$work/xterm/package/typings/xterm.d.ts" "$dest/xterm.d.ts"
cp "$work/xterm/package/LICENSE" "$dest/LICENSE.xterm"
cp "$work/addon-fit/package/lib/addon-fit.js" "$dest/addon-fit.js"
cp "$work/addon-fit/package/typings/addon-fit.d.ts" "$dest/addon-fit.d.ts"
cp "$work/addon-search/package/lib/addon-search.js" "$dest/addon-search.js"
cp "$work/addon-search/package/typings/addon-search.d.ts" "$dest/addon-search.d.ts"
# Drop source-map references: the maps are not vendored.
for f in "$dest"/*.js; do
    sed -i '/^\/\/# sourceMappingURL=/d' "$f"
done
cat > "$dest/VERSIONS" <<EOF
@xterm/xterm $XTERM_VERSION sha256:$XTERM_SHA256
@xterm/addon-fit $FIT_VERSION sha256:$FIT_SHA256
@xterm/addon-search $SEARCH_VERSION sha256:$SEARCH_SHA256
EOF
(cd "$dest" && sha256sum -- *.js *.css *.d.ts LICENSE.xterm > SHA256SUMS)
echo "Vendored xterm $XTERM_VERSION, addon-fit $FIT_VERSION, addon-search $SEARCH_VERSION into $dest"
