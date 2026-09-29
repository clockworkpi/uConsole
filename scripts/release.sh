#!/usr/bin/env bash
# Validate and tag a uConsole release. GitHub Actions builds and publishes the assets.
# Usage: scripts/release.sh [--dry-run] [major|minor|patch|X.Y.Z]

set -euo pipefail

fail() {
    printf 'release: %s\n' "$*" >&2
    exit 1
}

dry_run=false
if [[ ${1:-} == --dry-run ]]; then
    dry_run=true
    shift
fi
requested=${1:-patch}
(( $# <= 1 )) || fail 'usage: scripts/release.sh [--dry-run] [major|minor|patch|X.Y.Z]'

for command in git gh make python3; do
    command -v "$command" >/dev/null 2>&1 || fail "required command not found: $command"
done
gh auth status >/dev/null 2>&1 || fail 'GitHub CLI is not authenticated; run gh auth login'
[[ -z $(git status --porcelain) ]] || fail 'working tree is not clean'

branch=$(git branch --show-current)
[[ $branch == master ]] || fail "release from master, not $branch"
git fetch origin master --tags
[[ $(git rev-parse HEAD) == $(git rev-parse origin/master) ]] || \
    fail 'local master must exactly match origin/master'

current=$(git tag --list 'v[0-9]*.[0-9]*.[0-9]*' | python3 -c '
import re, sys
versions = []
for line in sys.stdin:
    tag = line.strip()
    if re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        versions.append((tuple(map(int, tag[1:].split("."))), tag[1:]))
print(max(versions)[1] if versions else "0.0.0")
')

if [[ $requested =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    next=$requested
elif [[ $requested =~ ^(major|minor|patch)$ ]]; then
    IFS=. read -r major minor patch <<< "$current"
    if [[ $current == 0.0.0 ]]; then
        next=1.0.0
    else
        case $requested in
            major) next="$((major + 1)).0.0" ;;
            minor) next="$major.$((minor + 1)).0" ;;
            patch) next="$major.$minor.$((patch + 1))" ;;
        esac
    fi
else
    fail "invalid version '$requested'; use major, minor, patch, or X.Y.Z"
fi

python3 - "$current" "$next" <<'PY' || fail "v$next must be newer than v$current"
import sys
old, new = (tuple(map(int, value.split('.'))) for value in sys.argv[1:])
raise SystemExit(0 if new > old else 1)
PY
git rev-parse "v$next" >/dev/null 2>&1 && fail "tag v$next already exists"

printf 'Preparing uConsole v%s from v%s\n' "$next" "$current"
release_python=$(scripts/platform.sh python)
printf 'Release GUI test interpreter: %s\n' "$release_python"
case $(uname -s) in
    Linux)
        command -v xvfb-run >/dev/null 2>&1 || fail 'xvfb-run is required for release GUI tests; run make deps'
        xvfb-run -a make check PYTHON="$release_python"
        ;;
    Darwin) make check PYTHON="$release_python" ;;
    *) fail 'Release qualification requires a Linux or macOS build host' ;;
esac
make emulator-build
make check-emulator
make package

if [[ $dry_run == true ]]; then
    printf 'Release validation passed; dry run did not create v%s.\n' "$next"
    exit 0
fi

git tag -a "v$next" -m "Release v$next"
if ! git push origin "v$next"; then
    git tag -d "v$next" >/dev/null
    fail "could not push v$next; removed the local tag"
fi

origin_url=$(git remote get-url origin)
repo=${origin_url#*github.com[:/]}
repo=${repo%.git}
printf 'Pushed v%s. GitHub Actions will publish the release and packages:\n' "$next"
printf 'https://github.com/%s/actions/workflows/release.yml\n' "$repo"
