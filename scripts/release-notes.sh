#!/bin/sh
# Print one version's section of CHANGELOG.md, without its "## " heading and without the blank
# lines around it: the body of that version's GitHub release (.github/workflows/release.yml).
#
#   scripts/release-notes.sh 0.1.0
#
# The heading may be "## 0.1.0", "## [0.1.0]" or either followed by " - <date>". Exits 1 when the
# section is missing or empty. Plain POSIX sh and awk (the runner's mawk, busybox and gawk).
set -eu
version=${1:?usage: scripts/release-notes.sh X.Y.Z}
cd "$(dirname "$0")/.."
awk -v v="$version" '
    /^## / {
        if (found) exit
        h = $2
        sub(/^\[/, "", h)
        sub(/\]$/, "", h)
        if (h == v) found = 1
        next
    }
    !found { next }
    /^[ \t\r]*$/ { if (started) blank++; next }
    {
        for (; blank > 0; blank--) print ""
        started = 1
        print
    }
    END { if (!started) exit 1 }
' CHANGELOG.md
