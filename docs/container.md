# Isolated container and air-gapped runs

## Isolated container (Linux + Docker)

The agent and Ollama run together in one container on an internal Docker network with **no route out**. The
model files are mounted read-only from the host's Ollama folder (auto-detected, or set `MODELS_DIR`), so the
model must already be pulled on the host.

```bash
sudo ./scripts/container.sh up         # build, start, block container -> host, run the isolation checks
# open http://172.30.0.10:8080 on this machine
sudo ./scripts/container.sh down       # stop; resumes the host's Ollama if `up` paused it
```

| Command | What it does |
| --- | --- |
| `up` | Build and start; pauses the host's Ollama if it runs (one model in RAM); adds the host guard; runs the checks |
| `restart` | Restart the agent, e.g. after code changes (code is mounted read-only) |
| `proof` | Re-run the isolation checks |
| `agent "<task>"` | Run the CLI inside the container |
| `shell` | Open a shell inside the container |
| `down` | Stop, remove the host guard, resume the host's Ollama if `up` paused it |

Options: `MODEL=<tag>` (default `qwen3.5:4b`), `MODELS_DIR=<path>`. From another machine, tunnel over SSH:
`TARGET=172.30.0.10 ./scripts/open_ui_mac.sh` (`HOST` defaults to `mini-pc`).

### What `up` checks inside the container

```
routes inside the container:  172.30.0.0/24 dev eth0   (no default route)
[ok] ping 1.1.1.1: blocked            [ok] HTTPS https://ollama.com: blocked
[ok] DNS lookup ollama.com: blocked   [ok] host 172.30.0.1:22: blocked
[ok] host LAN gateway: blocked        [ok] local Ollama on 127.0.0.1:11434: reachable
```

### Hardening

- Runs as your user (not root); all Linux capabilities dropped; `no-new-privileges`.
- Read-only root filesystem; only `demo/work` and `audit/` are writable; memory and process limits.
- An nftables rule on the host drops connections *from* the container to the host, while replies to the host
  (the UI) still flow.
- Docker's embedded DNS does not resolve outside names on an internal network.

Limits: the container shares the host kernel (it is not a VM), and Docker itself runs as root. Docker Desktop on
macOS/Windows cannot reach the container's IP; use the no-Docker mode there.

## Fully air-gapped run (your own machine)

Browser, agent and model all run on one machine, so it can be physically disconnected.

1. **While online, once:** install Ollama and `ollama pull qwen3.5:4b`; create `.venv` with pytest; clone the repo.
   For the container mode, run `sudo ./scripts/container.sh up` once so the image is built (the build needs
   internet), then `down`.
2. **Disconnect:** unplug the network cable and turn off Wi-Fi/Bluetooth (or airplane mode).
3. **Run:** no Docker: `bin/airlock-ui --repo demo/work --reset-script scripts/reset_demo.sh` and open
   `http://localhost:8080`; Linux + Docker: `sudo ./scripts/container.sh up` and open `http://172.30.0.10:8080`.
4. **Check:** the badge shows **● OFFLINE**; `ping 1.1.1.1` and `curl https://google.com` fail in the Terminal tab;
   the fix flow still works.

On macOS/Windows use the no-Docker mode. The container on a disconnected machine is the strongest setup: no
network at all, and the agent sandboxed on top.
