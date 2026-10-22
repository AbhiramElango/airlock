#!/usr/bin/env bash
# Recreate the disposable demo repo at demo/work as a clean git checkpoint.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
work="$here/demo/work"
mkdir -p "$work"
find "$work" -mindepth 1 -delete  # empty in place so shells inside it stay valid
cp -R "$here/demo/seed/." "$work/"
cd "$work"
git init -q
git add -A
git -c user.name=demo -c user.email=demo@localhost commit -qm "seed: demo repo with known bug"
echo "demo repo ready: $work"
