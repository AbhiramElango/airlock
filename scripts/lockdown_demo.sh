#!/usr/bin/env bash
# Live demo over SSH: block all network traffic except loopback and this SSH client,
# prove it, open an interactive offline shell, then restore on exit. Run as your user (not root).
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
table="airlock_lockdown"
restore_unit="airlock-lockdown-restore"
failsafe_s="${FAILSAFE_S:-900}"
model="${MODEL:-qwen3.5:4b}"

if [[ "${1:-}" == "--inner" ]]; then
  source "$here/scripts/offline_checks.sh"
  printf '\n\033[1m### 2. Proof: internet is unreachable\033[0m\n'
  prove_offline || { echo "  network is NOT isolated; not starting the demo shell"; exit 1; }
  printf '  loading the model into memory... '
  "$here/bin/airlock" --warm-up --model "$model" && echo "ready" || echo "failed (first answer will be slower)"
  rc="$(mktemp)"
  cat > "$rc" <<RC
source "$here/scripts/offline_checks.sh"
bind 'set enable-bracketed-paste off' 2>/dev/null
export PATH="$here/.venv/bin:\$PATH" PYTHONDONTWRITEBYTECODE=1
alias agent="$here/bin/airlock --repo ."
alias prove-offline=prove_offline
ui() {
  if pgrep -f "[a]irgap_agent[.]web" >/dev/null; then echo "  UI already running on 127.0.0.1:8080"; return; fi
  ( setsid "$here/bin/airlock-ui" --repo "$here/demo/work" --port 8080 \
      --reset-script "$here/scripts/reset_demo.sh" >/tmp/airlock-ui.log 2>&1 & )
  sleep 1; echo "  UI on 127.0.0.1:8080. On the Mac run: ./scripts/open_ui_mac.sh"
}
trap 'pkill -f "[a]irgap_agent[.]web" 2>/dev/null' EXIT
PS1='\[\033[1;31m\][OFFLINE]\[\033[0m\] \W\$ '
cd "$here/demo/work"
printf '\n\033[1m### 3. Offline demo shell\033[0m (type exit when done; failsafe ${failsafe_s}s)\n'
echo "  python bill.py    run the program"
echo "  agent             ask the local model (e.g. what is wrong with bill.py?)"
echo "  prove-offline     re-run the network checks"
echo "  ui                start the web UI (open it from the Mac with scripts/open_ui_mac.sh)"
RC
  bash --rcfile "$rc" -i || true
  rm -f "$rc"
  exit 0
fi

[[ $EUID -ne 0 ]] || { echo "run as your normal user; sudo is used only for the firewall" >&2; exit 1; }
[[ -x "$here/.venv/bin/python" ]] || { echo "missing .venv; see README setup" >&2; exit 1; }
client_ip="$(awk '{print $1}' <<< "${SSH_CONNECTION:-}")"
ssh_port="$(awk '{print $4}' <<< "${SSH_CONNECTION:-}")"

cd "$here"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
out="$here/runs/lockdown_transcript_$stamp.txt"
mkdir -p "$here/runs"
work="$(mktemp -d)"
log="$work/log.txt"
say() { printf '%b\n' "$*" | tee -a "$log"; }

restore() {
  sudo nft delete table inet "$table" 2>/dev/null || true
  sudo systemctl stop "$restore_unit.timer" 2>/dev/null || true
  [[ -n "${mon_pid:-}" ]] && kill "$mon_pid" 2>/dev/null || true
}
trap restore EXIT

echo "sudo is needed for the firewall rules:"
sudo -v
"$here/scripts/reset_demo.sh" >/dev/null

say "# lockdown demo | $(date -u +%FT%TZ) | host $(hostname)"
say "\n\033[1m### 1. Lock down the network\033[0m"
say "  before: default route: $(ip route show default | head -1)"
in_rules="iif lo accept;"
out_rules="oif lo accept;"
if [[ -n "$client_ip" ]]; then
  fam="ip"; [[ "$client_ip" == *:* ]] && fam="ip6"
  in_rules+=" $fam saddr $client_ip tcp dport $ssh_port accept;"
  out_rules+=" $fam daddr $client_ip tcp sport $ssh_port accept;"
  say "  allowing only SSH with $client_ip on port $ssh_port"
fi
sudo systemctl stop "$restore_unit.timer" "$restore_unit.service" 2>/dev/null || true
sudo systemctl reset-failed "$restore_unit.service" 2>/dev/null || true
sudo systemd-run --quiet --unit "$restore_unit" --on-active="${failsafe_s}s" nft delete table inet "$table"
sudo nft -f - <<EOF
table inet $table {
  chain input   { type filter hook input priority -10; policy drop; $in_rules counter; }
  chain output  { type filter hook output priority -10; policy drop; $out_rules counter; }
  chain forward { type filter hook forward priority -10; policy drop; counter; }
}
EOF
say "  firewall active: everything dropped except loopback${client_ip:+ and this SSH session}"
say "  failsafe: rules auto-removed in ${failsafe_s}s even if this script dies"

conns="$work/conns.txt"
( while true; do sudo ss -tunpH state established 2>/dev/null >> "$conns"; sleep 0.5; done ) &
mon_pid=$!

script -q -e -c "$0 --inner" "$work/inner.txt" || true

kill "$mon_pid" 2>/dev/null || true; mon_pid=""
say "\n\033[1m### 4. Network sockets of python and ollama during the demo\033[0m"
grep -E '"(python[0-9.]*|ollama[^"]*)"' "$conns" | awk '
  function norm(a) { if (a !~ /:(11434|22)$/) sub(/:[0-9]+$/, ":*", a); return a }
  { p = $6; gsub(/,(pid|fd)=[0-9]+/, "", p); print "  " norm($4) " -> " norm($5) "  " p }' \
  | sort -u | tee -a "$log" || true
say "  outbound packets dropped (all processes): $(sudo nft list chain inet $table output | grep -oE 'packets [0-9]+' | head -1)"

restore; trap - EXIT
say "\n\033[1m### 5. Network restored\033[0m"
ping -c1 -W2 1.1.1.1 >/dev/null 2>&1 && say "  [ok] ping 1.1.1.1 works again" || say "  [!!] still offline; check 'sudo nft list ruleset'"

{ sed -n '/### 1/,$p' "$log" | sed '/### 4/,$d'
  tr -d '\r' < "$work/inner.txt" | grep -v '^Script '
  sed -n '/### 4/,$p' "$log"; } > "$work/full.txt"
( head -1 "$log"; cat "$work/full.txt" ) | PYTHONPATH="$here" "$here/.venv/bin/python" "$here/scripts/clean_transcript.py" > "$out"
rm -rf "$work"
echo -e "\ntranscript -> $out"
