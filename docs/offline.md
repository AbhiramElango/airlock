# Offline tests

Besides the [isolated container](container.md), two host-level tests show the agent working without a network.

## Host firewall lockdown

`./scripts/lockdown_demo.sh` (over SSH): nftables drops everything except loopback and the current SSH client,
with a 15-minute failsafe that removes the rules. It proves ping, DNS, HTTPS and the LAN gateway are unreachable,
then opens an `[OFFLINE]` shell:

```
python bill.py     # run the program
agent              # ask the local model, e.g. "what is wrong with bill.py?"
ui                 # start the web UI (open it from another machine with scripts/open_ui_mac.sh)
prove-offline      # re-run the network checks
exit               # show python/ollama sockets, restore network, save runs/lockdown_transcript_<time>.txt
```

Passed on 2026-10-20 with the CLI and the web UI. Recorded transcript:
[`lockdown_transcript_20261020T103615Z.txt`](lockdown_transcript_20261020T103615Z.txt) (an earlier version of
the agent and demo).

## All interfaces down

```bash
sudo systemd-run --unit airlock-offline --collect "$PWD/scripts/offline_run.sh"
```

Snapshots the network, blocks rfkill, turns NetworkManager off and takes every interface down (failsafe restore
after 10 minutes), proves there is no interface, default route, ping, DNS or HTTPS, runs the demo unattended
(approval auto-`y`), restores the network and saves `runs/offline_transcript_<time>.txt`. Passed on 2026-10-20.

## Wording

- **Network-isolated:** the machine stays connected, traffic is blocked (firewall, container).
- **Fully offline:** every interface is down.
- **Air-gapped:** the machine is physically disconnected, see the
  [air-gapped checklist](container.md#fully-air-gapped-run-your-own-machine).
