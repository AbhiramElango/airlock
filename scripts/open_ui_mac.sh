#!/usr/bin/env bash
# On the Mac: tunnel to the mini-pc UI over SSH and open it as an app window (no address bar).
# Container UI: TARGET=172.30.0.10 ./scripts/open_ui_mac.sh
set -euo pipefail
host="${HOST:-mini-pc}"
port="${PORT:-8080}"
target="${TARGET:-127.0.0.1}"
if ! pgrep -f "$port:$target:$port $host" >/dev/null; then
  ssh -f -N -o ExitOnForwardFailure=yes -L "$port:$target:$port" "$host"
fi
open -na "Google Chrome" --args --app="http://localhost:$port" --window-size=1400,850
echo "UI opened. Close the tunnel later with: pkill -f '$port:$target:$port $host'"
