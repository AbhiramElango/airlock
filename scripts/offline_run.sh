#!/usr/bin/env bash
# Fully offline run (as root, detached via systemd-run): take every network interface down, prove it,
# run the bill.py demo as the project owner (auto-approve), restore the network, save a transcript.
#   sudo systemd-run --unit airlock-offline --collect "$PWD/scripts/offline_run.sh"
#   --restore   only restore (used by the failsafe timer)
set -uo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
state=/run/airlock-offline
owner="$(stat -c %U "$here")"
failsafe_s="${FAILSAFE_S:-600}"
task="what is wrong with bill.py?"

snapshot() {  # static addresses and routes, so interfaces not managed by NetworkManager come back intact
  ip -o link show up | awk -F': ' '$2 != "lo" {sub(/@.*/, "", $2); print $2}' > "$state/ifaces"
  rfkill -rno ID,SOFT | awk '$2 == "unblocked" {print $1}' > "$state/rfkill"
  ip -o addr show | awk '$2 != "lo" && $0 !~ / dynamic / && $4 !~ /^fe80/ {print $2, $3, $4}' > "$state/addrs"
  { ip -4 route show table all; ip -6 route show table all; } \
    | grep -vE '^(local|broadcast|multicast|anycast) |fe80::/64|table local' | sed 's/ linkdown//' > "$state/routes"
}

restore() {
  [[ -f "$state/ifaces" ]] || return 0
  nmcli networking on 2>/dev/null
  while read -r i; do ip link set dev "$i" up 2>/dev/null; done < "$state/ifaces"
  while read -r dev fam addr; do ip "-${fam/inet6/6}" addr add "$addr" dev "$dev" 2>/dev/null; done < "$state/addrs"
  sleep 3
  while read -r route; do ip route add $route 2>/dev/null; done < "$state/routes"
  while read -r id; do rfkill unblock "$id" 2>/dev/null; done < "$state/rfkill"
  systemctl is-active --quiet tailscaled && systemctl restart tailscaled
  rm -f "$state/ifaces" "$state/rfkill" "$state/addrs" "$state/routes"
  systemctl stop airlock-offline-restore.timer 2>/dev/null
}

if [[ "${1:-}" == "--restore" ]]; then restore; exit 0; fi
[[ $EUID -eq 0 ]] || { echo "run with sudo via systemd-run (see header)" >&2; exit 1; }

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$here/runs" "$state"
raw="$state/raw.txt"
out="$here/runs/offline_transcript_$stamp.txt"
exec > "$raw" 2>&1
step() { printf '\n### %s\n' "$*"; }
as_owner() { runuser -u "$owner" -- env HOME="$(getent passwd "$owner" | cut -d: -f6)" PATH=/usr/bin:/bin "$@"; }

echo "# fully offline run | $(date -u +%FT%TZ) | host $(hostname) | task: $task"
step "1. Before: network is up"
ip -br addr | grep -v '^lo'
echo "default route: $(ip route show default | head -1)"
ping -c1 -W2 1.1.1.1 >/dev/null && echo "[online] ping 1.1.1.1 works"

step "2. Take every network interface down"
systemctl stop airlock-offline-restore.timer airlock-offline-restore.service 2>/dev/null
systemctl reset-failed airlock-offline-restore.service 2>/dev/null
systemd-run --quiet --unit airlock-offline-restore --on-active="${failsafe_s}s" "$here/scripts/offline_run.sh" --restore
echo "failsafe: network restored automatically in ${failsafe_s}s even if this run dies"
snapshot
rfkill block all
nmcli networking off 2>/dev/null
while read -r i; do ip link set dev "$i" down 2>/dev/null; done < "$state/ifaces"
sleep 2
echo "interfaces taken down: $(tr '\n' ' ' < "$state/ifaces")"
ip -br link

step "3. Proof: no network at all"
up="$(ip -o link show up | awk -F': ' '$2 != "lo"' | wc -l)"
[[ "$up" -eq 0 ]] && echo "[ok] no interface is up except loopback" || echo "[!!] $up interfaces still up"
[[ -z "$(ip route show default)" ]] && echo "[ok] no default route" || echo "[!!] default route present"
source "$here/scripts/offline_checks.sh"
isolated=0; prove_offline && isolated=1

if [[ $isolated -eq 1 ]]; then
  step "4. Demo (approval answered y automatically in this unattended run)"
  cd "$here/demo/work"
  as_owner "$here/scripts/reset_demo.sh"
  echo '$ python bill.py'; as_owner python3 bill.py
  echo "\$ airlock \"$task\""
  as_owner "$here/bin/airlock" --warm-up
  echo y | as_owner "$here/bin/airlock" --repo . --no-color "$task"
  echo '$ python bill.py'; as_owner python3 bill.py
  cd "$here"
else
  echo "network is NOT fully down; skipping the demo"
fi

step "5. Restore the network"
restore
for _ in $(seq 1 30); do ping -c1 -W1 1.1.1.1 >/dev/null 2>&1 && break; sleep 2; done
ping -c1 -W2 1.1.1.1 >/dev/null 2>&1 && echo "[ok] network restored: ping 1.1.1.1 works" \
  || echo "[!!] still offline; run: sudo $here/scripts/offline_run.sh --restore"
ip -br addr | grep -v '^lo'

PYTHONPATH="$here" "$here/.venv/bin/python" "$here/scripts/clean_transcript.py" < "$raw" > "$out"
chown "$owner": "$out"
rm -f "$raw"
