#!/bin/sh
# First step of the release workflow (.github/workflows/release.yml): refuse to release unless
#   - the tag is "v" + the version in pyproject.toml, and that version is X.Y.Z;
#   - portkey/__init__.py (what the app reports) has the same version;
#   - CHANGELOG.md has a non-empty section for it (the release notes).
# On success it writes version=X.Y.Z and minor=X.Y to $GITHUB_OUTPUT (stdout when unset).
#
#   scripts/release-check.sh v0.1.0
#
# Needs python3 (3.11 or newer, for tomllib), awk and grep.
set -eu
tag=${1:?usage: scripts/release-check.sh vX.Y.Z}
here=$(cd "$(dirname "$0")" && pwd)
cd "$here/.."

fail() {
    echo "::error::$1"
    exit 1
}

version=$(python3 -I -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
echo "$version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' \
    || fail "pyproject.toml version '$version' is not X.Y.Z."
[ "$tag" = "v$version" ] \
    || fail "Tag '$tag' does not match pyproject.toml: expected v$version."

app=$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' portkey/__init__.py)
[ "$app" = "$version" ] \
    || fail "portkey/__init__.py says '$app', pyproject.toml says $version. Bump both."

"$here/release-notes.sh" "$version" >/dev/null \
    || fail "CHANGELOG.md has no section '## $version' (or it is empty)."

minor=${version%.*}
echo "Releasing $version (image tags $version, $minor, latest)."
{
    echo "version=$version"
    echo "minor=$minor"
} >>"${GITHUB_OUTPUT:-/dev/stdout}"
