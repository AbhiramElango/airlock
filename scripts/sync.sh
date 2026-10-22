#!/usr/bin/env bash
# Copy the project to the mini-pc (run while online).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
rsync -a --delete --exclude .git --exclude demo/work --exclude __pycache__ --exclude .venv --exclude audit --exclude runs \
  "$here/" "${HOST:-mini-pc}:${DEST:-airlock}/"
echo "synced to ${HOST:-mini-pc}:${DEST:-airlock}"
