#!/usr/bin/env bash
# Container start: Ollama on loopback, fresh demo repo, warm model, then the web UI.
set -euo pipefail
cd /app
model="${AIRLOCK_MODEL:-qwen3.5:4b}"
ollama serve >/tmp/ollama.log 2>&1 &
for _ in $(seq 1 60); do curl -sf http://127.0.0.1:11434/api/version >/dev/null && break; sleep 1; done
if ! ollama list | awk 'NR > 1 {print $1}' | grep -qx "$model"; then
  echo "model $model is not in the mounted models folder; on the host run: ollama pull $model" >&2
  exit 1
fi
scripts/reset_demo.sh
bin/airlock --warm-up --model "$model"
ip="$(ip -4 -o addr show eth0 | awk '{print $4}' | cut -d/ -f1)"
exec bin/airlock-ui --repo demo/work --model "$model" --bind 0.0.0.0 --allow-host "$ip:8080" \
  --reset-script scripts/reset_demo.sh
