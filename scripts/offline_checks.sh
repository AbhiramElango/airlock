# Sourced helpers: prove_offline returns 0 only if the internet and LAN are unreachable.
check_blocked() {  # $1 label, rest = command expected to FAIL
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then echo "  [!!] $label: reachable (NOT isolated)"; return 1; fi
  echo "  [ok] $label: blocked"
}

prove_offline() {
  local ok=0 gw
  check_blocked "ping 1.1.1.1"             ping -c1 -W2 1.1.1.1 || ok=1
  check_blocked "DNS lookup ollama.com"    timeout 5 getent hosts ollama.com || ok=1
  check_blocked "HTTPS https://ollama.com" curl -sS -m5 https://ollama.com || ok=1
  check_blocked "HTTPS https://1.1.1.1"    curl -sS -m5 https://1.1.1.1 || ok=1
  gw="$(ip route show default | awk '{print $3; exit}')"
  if [[ -n "$gw" ]]; then check_blocked "LAN gateway $gw" ping -c1 -W2 "$gw" || ok=1; fi
  if curl -sS -m5 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
    echo "  [ok] local Ollama on 127.0.0.1:11434: reachable"
  else
    echo "  [!!] local Ollama on 127.0.0.1:11434: NOT reachable"; ok=1
  fi
  return $ok
}
