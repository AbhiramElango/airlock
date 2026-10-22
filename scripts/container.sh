#!/usr/bin/env bash
# Network-isolated container demo (run with sudo on the mini-pc).
#   up      build + start the container (host Ollama paused if running), block container -> host, prove isolation
#   restart restart the agent (e.g. after code changes; code is mounted read-only)
#   proof   re-run the isolation checks inside the container
#   agent   run the CLI agent inside the container, e.g. agent "what is wrong with bill.py?"
#   shell   open a shell inside the container
#   down    stop the container, remove the host guard, resume host Ollama if `up` paused it
# Prerequisite: the model is pulled on the host (ollama pull qwen3.5:4b).
# Optional: MODEL=<tag> (default qwen3.5:4b), MODELS_DIR=<ollama models folder> (auto-detected).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
owner="$(stat -c %U "$here")"
export UID_GID="$(stat -c %u:%g "$here")"
compose=(docker compose -f "$here/container/compose.yaml")
name=airlock
ip=172.30.0.10
paused=/run/airlock.paused-ollama
[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }

models_dir() {  # where `ollama pull` stored the models: system service, then the project owner's home
  local home; home="$(getent passwd "$owner" | cut -d: -f6)"
  for d in "${MODELS_DIR:-}" /usr/share/ollama/.ollama/models "$home/.ollama/models"; do
    [[ -n "$d" && -d "$d/manifests" ]] && { echo "$d"; return; }
  done
  echo "no Ollama models folder found; install Ollama and run: ollama pull ${MODEL:-qwen3.5:4b}" >&2
  echo "(or set MODELS_DIR=/path/to/.ollama/models)" >&2
  exit 1
}

guard_on() {  # the container may answer the host (SSH tunnel) but never open connections to it
  guard_off
  nft -f - <<'EOF'
table inet airlock_guard {
  chain input {
    type filter hook input priority -10; policy accept;
    iifname "br-airlock" ct state established,related accept
    iifname "br-airlock" counter drop
  }
}
EOF
}
guard_off() { nft delete table inet airlock_guard 2>/dev/null || true; }

proof() {
  local gw; gw="$(ip route show default | awk '{print $3; exit}')"
  docker exec -e GW="$gw" "$name" bash -c '
    source scripts/offline_checks.sh
    echo "routes inside the container:"; ip route | sed "s/^/  /"
    prove_offline; ok=$?
    check_blocked "host 172.30.0.1:22" timeout 3 bash -c "</dev/tcp/172.30.0.1/22" || ok=1
    [[ -n "$GW" ]] && { check_blocked "host LAN gateway $GW" ping -c1 -W2 "$GW" || ok=1; }
    exit $ok'
}

wait_ready() {
  echo "waiting for the model to load and the UI to start..."
  for _ in $(seq 1 90); do curl -sf -H "Host: localhost:8080" "http://$ip:8080/api/status" >/dev/null && return 0; sleep 2; done
  echo "UI did not start; see: sudo docker logs $name" >&2; exit 1
}

case "${1:-}" in
  up)
    runuser -u "$owner" -- mkdir -p "$here/demo/work" "$here/audit"
    export MODELS_DIR="$(models_dir)"
    echo "models: $MODELS_DIR"
    if systemctl is-active --quiet ollama 2>/dev/null; then  # one copy of the model in RAM
      systemctl stop ollama && touch "$paused"
    fi
    "${compose[@]}" up -d --build
    guard_on
    wait_ready
    proof || echo "[!!] isolation check failed: do not demo; see the lines above"
    echo "UI ready: open http://$ip:8080 on this machine, or from another one: TARGET=$ip ./scripts/open_ui_mac.sh"
    ;;
  restart) docker restart "$name" >/dev/null; wait_ready; echo "restarted" ;;
  proof) proof ;;
  agent) shift; docker exec -it "$name" bin/airlock --repo demo/work "$@" ;;
  shell) docker exec -it "$name" bash ;;
  down)
    export MODELS_DIR="${MODELS_DIR:-/nonexistent}"
    "${compose[@]}" down
    guard_off
    if [[ -f "$paused" ]]; then systemctl start ollama && rm -f "$paused"; fi
    ;;
  *) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
